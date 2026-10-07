#!/usr/bin/env python
"""Historical replay: one risk prediction per day (feeds the later dashboard).

    python scripts/replay_risk.py --start 2024-10-01 --end 2024-12-31
    python scripts/replay_risk.py --start 2024-10-01 --end 2024-12-31 --with-trigger

Always emits trigger_candidate = risk_score >= threshold (single-day check only).
With --with-trigger it also adds the Phase 3 persistence-trigger columns
(trigger_severity, consecutive_high_count, trigger_active, trigger_reason),
computed by risk_trigger.py over the FULL feature history and then sliced to
the requested window, so a streak that began before --start is counted.
The persistence trigger is an internal research proxy, not a flood prediction.
Rainfall units are unverified; the observed-stress flag uses the stored threshold in source units.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import risk_config as cfg  # noqa: E402
from risk_data import load_daily_station_rainfall  # noqa: E402
from risk_features import build_daily_panel, build_features  # noqa: E402
from risk_inference import load_model_bundle, predict_risk_frame  # noqa: E402
from risk_trigger import (  # noqa: E402
    DEFAULT_CONFIG_OUT, TRIGGER_REPLAY_COLUMNS, load_trigger_config, trigger_columns_for_window,
)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", required=True, help="YYYY-MM-DD (feature date)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (feature date)")
    p.add_argument("--model-dir", default=str(cfg.MODELS_DIR))
    p.add_argument("--source", default=None)
    p.add_argument("--output", default=None)
    p.add_argument("--trigger-threshold", type=float, default=None)
    p.add_argument("--severity-thresholds", default=None,
                   help='JSON, e.g. \'{"MODERATE":0.3,"HIGH":0.6}\'')
    p.add_argument("--with-trigger", action="store_true",
                   help="add Phase 3 persistence-trigger columns (internal research proxy)")
    p.add_argument("--trigger-config", default=None,
                   help="trigger_config.json; default Datasets/metadata/trigger_config.json if it "
                        "exists, else built-in defaults")
    return p.parse_args(argv)


def _split_for(date, split_info):
    for sp in ("train", "val", "test"):
        s = split_info.get(sp, {})
        if s.get("first_target_date") and s["first_target_date"] <= date.date().isoformat() <= s["last_target_date"]:
            return sp
    return "outside_labelled_splits"


def main(argv=None):
    a = parse_args(argv)
    start, end = pd.Timestamp(a.start), pd.Timestamp(a.end)
    if start > end:
        print("ERROR: --start is after --end", file=sys.stderr)
        return 1

    trigger_cfg = None
    if a.with_trigger:
        cfg_path = Path(a.trigger_config) if a.trigger_config else (
            DEFAULT_CONFIG_OUT if DEFAULT_CONFIG_OUT.exists() else None)
        trigger_cfg = load_trigger_config(cfg_path)
        print(f"trigger config: {cfg_path if cfg_path else 'built-in defaults'}")

    bundle = load_model_bundle(a.model_dir)
    meta = bundle["metadata"]
    ls = meta["data_loader_settings"]
    src = Path(a.source) if a.source else cfg.PROJECT_ROOT / meta["source_file"]
    long_df, _ = load_daily_station_rainfall(
        src, ls["date_col"], ls["station_col"],
        None if ls["rain_col"] == "SUM_OF_24_HOURLY_SLOTS" else ls["rain_col"],
        allow_sum_hourly=ls["allow_sum_hourly"])
    panel = build_daily_panel(long_df, meta["observation_window"]["start"], meta["observation_window"]["end"])
    feats = build_features(panel, meta["rainy_day_threshold"], meta["min_obs_fraction"])
    if start < feats.index.min() or end > feats.index.max():
        print(f"ERROR: requested range outside available data "
              f"{feats.index.min().date()}..{feats.index.max().date()}", file=sys.stderr)
        return 1

    th = json.loads(a.severity_thresholds) if a.severity_thresholds else meta["severity_thresholds"]
    trig = a.trigger_threshold if a.trigger_threshold is not None else meta["trigger_candidate_threshold"]
    sub = feats.loc[start:end]
    res = predict_risk_frame(sub, bundle, th)

    h = meta["target_definition"]["horizon_days"]
    stress_threshold = meta["target_definition"]["stress_threshold_source_units"]
    q = panel["local_rainfall_mean"].shift(-h).reindex(sub.index)
    out = pd.DataFrame({
        "date": sub.index.date,
        "prediction_for_date": (sub.index + pd.Timedelta(days=h)).date,
        "risk_score": res["risk_score"].round(4).values,
        "severity": res["severity"].values,
        "trigger_candidate": (res["risk_score"] >= trig).values,
        "station_count_available": sub["station_count_available"].values,
        "split": [_split_for(d + pd.Timedelta(days=h), meta["split_info"]) for d in sub.index],
        "observed_high_rainfall_stress": np.where(q.notna(), (q >= stress_threshold).astype(float), np.nan),
    })

    if a.with_trigger:
        # Score the whole history so streaks that began before --start are counted.
        res_full = predict_risk_frame(feats, bundle, th)
        tcols = trigger_columns_for_window(feats.index, res_full["risk_score"].to_numpy(), sub.index, trigger_cfg)
        for c in TRIGGER_REPLAY_COLUMNS:
            out[c] = tcols[c].to_numpy()

    cfg.REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    path = Path(a.output) if a.output else cfg.REPLAY_DIR / f"risk_replay_{a.start}_{a.end}.csv"
    out.to_csv(path, index=False)
    if a.with_trigger:
        show = ["date", "risk_score", "severity", "trigger_severity", "consecutive_high_count", "trigger_active"]
        print(out[show].head(10).to_string(index=False))
    else:
        print(out.head(10).to_string(index=False))
    print(f"... {len(out)} rows -> {path}")
    print(f"severity counts: {out['severity'].value_counts().to_dict()}  "
          f"trigger_candidates: {int(out['trigger_candidate'].sum())}")
    if a.with_trigger:
        print(f"persistence trigger_active days: {int(out['trigger_active'].sum())}  "
              f"longest streak in window: {int(out['consecutive_high_count'].max())}")
    print("Note: rows with split in train/val are IN-SAMPLE. Internal research score, not an official warning.")
    return 0


if __name__ == "__main__":
    sys.exit(main())