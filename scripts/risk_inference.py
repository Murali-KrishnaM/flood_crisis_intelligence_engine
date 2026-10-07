"""Reusable inference: severity mapping and predict_risk()."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from risk_config import DISCLAIMER, MODELS_DIR, SEVERITY_THRESHOLDS, TARGET_NAME

RESERVED = {"LOW", "NO_DATA"}


def _validate_thresholds(thresholds) -> list:
    items = sorted(dict(thresholds).items(), key=lambda kv: kv[1])
    seen = set()
    for name, v in items:
        if name in RESERVED:
            raise ValueError(f"'{name}' is reserved and cannot be a threshold level")
        if not (0 < v <= 1):
            raise ValueError(f"threshold for {name} must be in (0, 1], got {v}")
        if v in seen:
            raise ValueError("threshold values must be distinct")
        seen.add(v)
    return items


def _is_nan(x) -> bool:
    return x is None or (isinstance(x, (float, np.floating)) and math.isnan(x))


def severity_from_score(score, thresholds=None) -> str:
    """Map a risk score to an INTERNAL research severity label (not an official level)."""
    items = _validate_thresholds(SEVERITY_THRESHOLDS if thresholds is None else thresholds)
    if _is_nan(score):
        return "NO_DATA"
    if not (0 <= score <= 1):
        raise ValueError(f"risk score must be in [0, 1], got {score}")
    level = "LOW"
    for name, v in items:
        if score >= v:
            level = name
    return level


def load_model_bundle(model_dir=MODELS_DIR) -> dict:
    from xgboost import XGBClassifier
    d = Path(model_dir)
    paths = {k: d / f for k, f in (("model", "xgboost_model.json"),
                                   ("cols", "feature_columns.json"),
                                   ("meta", "model_metadata.json"))}
    missing = [str(p) for p in paths.values() if not p.exists()]
    if missing:
        raise FileNotFoundError(f"Missing model artifacts {missing}. "
                                f"Run: python scripts/train_risk_model.py")
    model = XGBClassifier()
    model.load_model(str(paths["model"]))
    return {"model": model,
            "feature_columns": json.loads(paths["cols"].read_text(encoding="utf-8")),
            "metadata": json.loads(paths["meta"].read_text(encoding="utf-8"))}


def predict_risk_frame(df: pd.DataFrame, bundle: dict, severity_thresholds=None) -> pd.DataFrame:
    """Vectorised scoring. Rows with no observation today get risk_score NaN / NO_DATA."""
    cols = bundle["feature_columns"]
    absent = [c for c in cols if c not in df.columns]
    if absent:
        raise ValueError(f"Missing feature columns: {absent}")
    th = severity_thresholds or bundle["metadata"].get("severity_thresholds") or SEVERITY_THRESHOLDS
    X = df[cols].astype(float)
    usable = df["rainfall_today"].notna() & (df["station_count_available"] > 0)
    scores = pd.Series(np.nan, index=df.index, dtype=float)
    if usable.any():
        scores[usable] = bundle["model"].predict_proba(X[usable])[:, 1]
    sev = scores.map(lambda s: severity_from_score(s, th))
    return pd.DataFrame({"risk_score": scores, "severity": sev})


def predict_risk(features, bundle=None, model_dir=None, severity_thresholds=None) -> dict:
    """Score ONE feature row (dict / Series / 1-row DataFrame) containing every feature column.

    Missing features must be passed explicitly as NaN (never silently defaulted).
    """
    bundle = bundle or load_model_bundle(model_dir or MODELS_DIR)
    cols = bundle["feature_columns"]
    if isinstance(features, pd.DataFrame):
        if len(features) != 1:
            raise ValueError("pass exactly one row")
        row = features.iloc[0].to_dict()
    elif isinstance(features, pd.Series):
        row = features.to_dict()
    else:
        row = dict(features)
    absent = [c for c in cols if c not in row]
    if absent:
        raise ValueError(f"Missing feature(s): {absent}. Pass NaN explicitly if unobserved.")
    df = pd.DataFrame([{c: row[c] for c in cols}]).astype(float)
    res = predict_risk_frame(df, bundle, severity_thresholds).iloc[0]
    score = None if pd.isna(res["risk_score"]) else round(float(res["risk_score"]), 4)
    return {
        "risk_score": score,
        "severity": res["severity"],
        "target": TARGET_NAME,
        "prediction_horizon": "next_day",
        "note": DISCLAIMER,
    }