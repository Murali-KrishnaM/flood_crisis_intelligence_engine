#!/usr/bin/env python
"""Train the Phase 2 research-proxy model (high_rainfall_stress, next day).

Run from the project root:
    python scripts/train_risk_model.py --inspect     # check column detection first
    python scripts/train_risk_model.py

Rainfall units are UNVERIFIED: every rainfall threshold is in source rainfall units.

This script ALWAYS prints something: a start banner, progress lines, and either a final
summary or an error with a traceback. It never exits silently. Exit codes:
    0 success | 1 error / cannot train | 2 source file missing
"""
from __future__ import annotations

import sys

# Printed BEFORE the heavy imports: if you see nothing at all, the file being run is not this one.
print(f"[train_risk_model] script started (python {sys.version.split()[0]}, file={__file__})",
      flush=True)

import argparse  # noqa: E402
import datetime as dt  # noqa: E402
import json  # noqa: E402
import traceback  # noqa: E402
from pathlib import Path  # noqa: E402

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import sklearn  # noqa: E402
import xgboost  # noqa: E402
from sklearn.dummy import DummyClassifier  # noqa: E402
from sklearn.impute import SimpleImputer  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import (average_precision_score, brier_score_loss, confusion_matrix,  # noqa: E402
                             f1_score, precision_score, recall_score, roc_auc_score)
from sklearn.pipeline import make_pipeline  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402
from xgboost import XGBClassifier  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import risk_config as cfg  # noqa: E402
from risk_data import inspect_source, json_default, load_daily_station_rainfall  # noqa: E402
from risk_features import FEATURE_COLUMNS, build_daily_panel, prepare_dataset  # noqa: E402
from risk_plots import plot_confusion, plot_prob_vs_target, plot_rainfall, plot_risk  # noqa: E402

PROXY_NOTE = "Metrics are for the high_rainfall_stress PROXY target, NOT flood-prediction accuracy."


def log(msg: str) -> None:
    print(f"[train_risk_model] {msg}", flush=True)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", default=None, help="daily rainfall CSV (default rtff_arg_day_records.csv)")
    p.add_argument("--date-col"); p.add_argument("--station-col"); p.add_argument("--rain-col")
    p.add_argument("--allow-sum-hourly", action="store_true",
                   help="UNVERIFIED: sum 24 hXX_30 columns if no daily column exists")
    p.add_argument("--percentile", type=float, default=cfg.STRESS_PERCENTILE)
    p.add_argument("--inspect", action="store_true", help="print detected columns and exit")
    return p.parse_args(argv)


def rel(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(cfg.PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def choose_threshold(y, p) -> float:
    """Decision threshold maximising F1 on the VALIDATION split (fallback 0.5)."""
    y = np.asarray(y).astype(int)
    if y.sum() == 0:
        return 0.5
    best_t, best_f = 0.5, 0.0
    for t in np.linspace(0.05, 0.95, 19):
        f = f1_score(y, (p >= t).astype(int), zero_division=0)
        if f > best_f:
            best_t, best_f = float(t), f
    return best_t


def compute_metrics(y, p, threshold) -> dict:
    y = np.asarray(y).astype(int)
    p = np.asarray(p, dtype=float)
    pred = (p >= threshold).astype(int)
    cm = confusion_matrix(y, pred, labels=[0, 1])
    return {
        "n": int(len(y)), "n_positive": int(y.sum()),
        "positive_rate": float(y.mean()) if len(y) else None,
        "roc_auc": float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None,
        "pr_auc": float(average_precision_score(y, p)) if y.sum() > 0 else None,
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "brier": float(brier_score_loss(y, p)),
        "decision_threshold": float(threshold),
        "confusion_matrix": {"layout": "[[TN, FP], [FN, TP]]", "matrix": cm.tolist()},
    }


def train_xgboost(Xtr, ytr, Xva, yva):
    params = dict(n_estimators=400, max_depth=3, learning_rate=0.05, subsample=0.8,
                  colsample_bytree=0.8, min_child_weight=2, reg_lambda=1.0,
                  eval_metric="aucpr", tree_method="hist", n_jobs=1,
                  random_state=cfg.RANDOM_SEED)
    use_es = 0 < int(yva.sum()) < len(yva)
    if use_es:
        params["early_stopping_rounds"] = 40
    clf = XGBClassifier(**params)
    clf.fit(Xtr, ytr, eval_set=[(Xva, yva)] if use_es else None, verbose=False)
    best_it = int(clf.best_iteration) if use_es else None
    return clf, params, best_it


def write_model_card(path: Path, meta: dict, ev: dict):
    t, sp = meta["target_definition"], meta["split_info"]
    h = t["horizon_days"]
    L = ["# Model Card — high_rainfall_stress (research proxy)", "",
         "> **This model does NOT issue official warnings.** Its output is an internal research "
         "risk score for a rainfall-based proxy target. It is not a flood prediction, not a "
         "government warning, and not a guarantee of flood occurrence.", "",
         "> **Rainfall units are unverified.** All rainfall values and rainfall thresholds in this "
         "card are in the source rainfall measurement units; the physical unit has not been "
         "independently verified.", "",
         "## Objective",
         "Estimate the probability that next-day local rainfall is 'high rainfall stress' "
         "(defined below), as a building block for a decision-support prototype.", "",
         "## Study area", "Ward 177, Velachery, Chennai (nearby RTFF rain gauges).", "",
         "## Data sources",
         f"- `{meta['source_file']}` (RTFF-derived daily station records; primary model source).",
         "- Historical Chennai rainfall CSV (1993–2023) NOT used: 86 conflicting station-day keys unresolved.",
         "- Reservoir data NOT used: schema/units unverified.", "",
         "## Observation period & stations",
         f"- Window: {meta['observation_window']['start']} to {meta['observation_window']['end']}",
         f"- Stations found: {', '.join(meta['stations_found'])}",
         f"- Stations missing from source: {', '.join(meta['stations_missing']) or 'none'}",
         f"- Observed days per station: {meta['observed_days_per_station']}", "",
         "## Target definition",
         f"- Quantity: {t['stress_quantity']}",
         f"- `high_rainfall_stress(t+{h}) = 1` if the quantity on day t+{h} is >= "
         f"**{t['stress_threshold_source_units']:.4f}**.",
         f"- That threshold is in the {t['threshold_unit_note']}.",
         f"- It is the {t['percentile']:.0%} percentile of observed days "
         f"up to {t['threshold_fitted_on_days_up_to']} (TRAIN period only).",
         "- Features through day t only; the target lies strictly in the future.",
         "- Rows are used only if rainfall is observed on day t and the target-day quantity is observed.", "",
         "## Features",
         ", ".join(f"`{c}`" for c in meta["feature_columns"]), "",
         f"Rules: no zero-fill, no interpolation; rolling features need >= {int(cfg.MIN_OBS_FRACTION*100)}% "
         f"of the window observed and sum observed days only; a 'rainy day' is a day with local mean "
         f"rainfall >= {meta['rainy_day_threshold']} (source rainfall units; internal research setting); "
         "explicit missingness features (`rainfall_missing_today`, `observed_fraction_7d`, "
         "`station_count_available`). Hourly RTFF labels are not used.", "",
         "## Chronological split (by target date)",
         "| split | rows | feature dates | target dates | positives |", "|---|---|---|---|---|"]
    for k in ("train", "val", "test"):
        s = sp[k]
        L.append(f"| {k} | {s['n_rows']} | {s['first_feature_date']} → {s['last_feature_date']} | "
                 f"{s['first_target_date']} → {s['last_target_date']} | {s['n_positive']} |")
    L += ["", f"## Metrics (TEST split). {PROXY_NOTE}",
          "| model | ROC-AUC | PR-AUC | Precision | Recall | F1 | Brier | threshold |",
          "|---|---|---|---|---|---|---|---|"]
    fmt = lambda v: "n/a" if v is None else f"{v:.3f}"
    for name, m in ev["models"].items():
        x = m["splits"]["test"]
        L.append(f"| {name} | {fmt(x['roc_auc'])} | {fmt(x['pr_auc'])} | {fmt(x['precision'])} | "
                 f"{fmt(x['recall'])} | {fmt(x['f1'])} | {fmt(x['brier'])} | {x['decision_threshold']:.2f} |")
    L += ["", "Confusion matrix layout `[[TN, FP], [FN, TP]]` per model is in "
          "`Datasets/metadata/model_evaluation.json`. Decision thresholds were chosen on the "
          "validation split (max F1). Train/validation metrics are also in the JSON.", ""]
    if ev["warnings"]:
        L += ["### Warnings"] + [f"- {w}" for w in ev["warnings"]] + [""]
    L += ["## Risk score & severity (internal research thresholds, not government-defined)",
          "- `risk_score` = predicted probability of next-day high_rainfall_stress.",
          f"- Severity mapping (configurable): {meta['severity_thresholds']} (LOW below the lowest).",
          f"- `trigger_candidate` threshold: {meta['trigger_candidate_threshold']} "
          "(candidate only; persistence trigger not implemented).", "",
          "## Limitations & known unresolved data semantics",
          "- The target is a rainfall percentile proxy, not an observed flood label.",
          "- The physical unit of the RTFF rainfall values is not independently verified; the stress "
          "threshold and the rainy-day threshold are in source rainfall units, and the rainy-day "
          "threshold value is an internal research setting whose physical meaning depends on that unit.",
          "- Small sample (about 3 years); few positive days in val/test, so metrics are high-variance.",
          "- Local mean mixes different station sets on different days (`station_count_available` provided).",
          "- RTFF hourly slot labels (h09_30 … h08_30) are not verified as chronological timestamps; unused.",
          "- Reservoir units/semantics unverified; no reservoir features.",
          "- Historical CSV duplicates/conflicts unresolved; not used for training.",
          "- Missing days are left missing: rolling sums over partially observed windows under-count rainfall.",
          "- No spatial/drainage/tide information is modelled.", "",
          "## Reproducibility",
          f"- Seed {meta['random_seed']}; xgboost {meta['library_versions']['xgboost']}; "
          f"scikit-learn {meta['library_versions']['scikit_learn']}; created {meta['created_utc']}.", ""]
    path.write_text("\n".join(L), encoding="utf-8")


def run(argv=None) -> int:
    a = parse_args(argv)
    source = Path(a.source) if a.source else cfg.DEFAULT_RAINFALL_SOURCE
    log(f"project root: {cfg.PROJECT_ROOT}")
    log(f"source file : {source}")
    if not source.exists():
        print(f"[train_risk_model] ERROR: source file not found: {source}\n"
              f"  Run from the project root, or pass --source <csv>.", file=sys.stderr, flush=True)
        return 2

    if a.inspect:
        log("INSPECT mode: reading source schema ...")
        report = inspect_source(source, a.date_col, a.station_col, a.rain_col)
        print(json.dumps(report, indent=2, default=json_default), flush=True)
        undetected = [k for k in ("date_col", "station_col", "rain_col")
                      if str(report.get(k, "")).startswith("NOT DETECTED")]
        if undetected:
            print(f"[train_risk_model] INSPECT: could not auto-detect {undetected}. "
                  f"Pass --date-col / --station-col / --rain-col explicitly.", file=sys.stderr, flush=True)
            return 1
        log("INSPECT OK: all three columns detected. Check them above, then run without --inspect.")
        return 0

    for d in (cfg.PROCESSED_DIR, cfg.METADATA_DIR, cfg.MODELS_DIR, cfg.PLOTS_DIR):
        d.mkdir(parents=True, exist_ok=True)

    log("[1/10] loading daily station rainfall ...")
    long_df, load_info = load_daily_station_rainfall(
        source, a.date_col, a.station_col, a.rain_col, allow_sum_hourly=a.allow_sum_hourly)
    log(f"       rows read={load_info['rows_read']} matched={load_info['rows_matched_to_project_stations']} "
        f"stations={load_info['stations_found']} missing={load_info['stations_missing']} "
        f"rain_col={load_info['rain_col']}")
    log("[2/10] building daily panel, features, target, chronological split ...")
    panel = build_daily_panel(long_df, cfg.WINDOW_START, cfg.WINDOW_END)
    ds, info = prepare_dataset(panel, percentile=a.percentile)
    el = ds[ds["eligible"]]
    log(f"       calendar days={info['n_calendar_days']} eligible rows={info['n_eligible_rows']}")
    parts = {k: el[el["split"] == k] for k in ("train", "val", "test")}
    X = {k: v[FEATURE_COLUMNS].astype(float) for k, v in parts.items()}
    y = {k: v[cfg.TARGET_NAME].astype(int) for k, v in parts.items()}
    if y["train"].nunique() < 2:
        print("[train_risk_model] ERROR: training split contains a single class; cannot train.",
              file=sys.stderr, flush=True)
        return 1

    log("[3/10] training baselines (dummy prior, logistic regression) ...")
    dummy = DummyClassifier(strategy="prior").fit(X["train"], y["train"])
    logreg = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                           LogisticRegression(max_iter=2000, random_state=cfg.RANDOM_SEED))
    logreg.fit(X["train"], y["train"])   # median imputation lives ONLY inside this baseline

    log("[4/10] training XGBoost (NaN handled natively, early stopping on validation) ...")
    clf, xgb_params, best_it = train_xgboost(X["train"], y["train"], X["val"], y["val"])
    model_path = cfg.MODELS_DIR / "xgboost_model.json"
    clf.save_model(str(model_path))
    xgb = XGBClassifier()
    xgb.load_model(str(model_path))      # evaluate the artifact exactly as inference will load it
    reload_diff = float(np.max(np.abs(clf.predict_proba(X["test"])[:, 1]
                                      - xgb.predict_proba(X["test"])[:, 1]))) if len(X["test"]) else 0.0
    joblib.dump(logreg, cfg.MODELS_DIR / "baseline_logreg.joblib")

    log("[5/10] evaluating ...")
    models = {"dummy_prior": dummy, "logistic_regression": logreg, "xgboost": xgb}
    evaluation = {"metric_scope": PROXY_NOTE, "target": cfg.TARGET_NAME, "models": {}, "warnings": []}
    for name, m in models.items():
        thr = choose_threshold(y["val"], m.predict_proba(X["val"])[:, 1])
        evaluation["models"][name] = {
            "decision_threshold": thr, "decision_threshold_selected_on": "validation (max F1; 0.5 fallback)",
            "splits": {k: compute_metrics(y[k], m.predict_proba(X[k])[:, 1], thr) for k in X}}
    for k in ("val", "test"):
        npos = int(y[k].sum())
        if npos < 20:
            evaluation["warnings"].append(
                f"{k} split has only {npos} positive days; metrics are high-variance.")
    if reload_diff > 1e-6:
        evaluation["warnings"].append(f"Saved-vs-loaded XGBoost prediction diff {reload_diff:.2e}.")
    evaluation["xgboost"] = {"best_iteration": best_it, "reload_max_abs_diff": reload_diff}
    (cfg.METADATA_DIR / "model_evaluation.json").write_text(
        json.dumps(evaluation, indent=2, default=json_default), encoding="utf-8")

    log("[6/10] saving model_features.csv and model_predictions.csv ...")
    ds.reset_index().to_csv(cfg.PROCESSED_DIR / "model_features.csv", index=False)
    preds = pd.DataFrame({"date": el.index, "target_date": el["target_date"].values,
                          "split": el["split"].values,
                          "y_true": el[cfg.TARGET_NAME].astype(int).values,
                          "rainfall_today": el["rainfall_today"].values,
                          "stress_quantity_next": el["stress_quantity_next"].values})
    Xall = el[FEATURE_COLUMNS].astype(float)
    for name, col in (("dummy_prior", "p_dummy"), ("logistic_regression", "p_logreg"), ("xgboost", "p_xgboost")):
        preds[col] = models[name].predict_proba(Xall)[:, 1]
    preds.to_csv(cfg.PROCESSED_DIR / "model_predictions.csv", index=False)

    log("[7/10] saving model artifacts (feature_columns.json, model_metadata.json) ...")
    (cfg.MODELS_DIR / "feature_columns.json").write_text(json.dumps(FEATURE_COLUMNS, indent=2), encoding="utf-8")
    imp = dict(sorted(zip(FEATURE_COLUMNS, map(float, xgb.feature_importances_)), key=lambda kv: -kv[1]))
    meta = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "purpose": "Internal research risk score for high_rainfall_stress. Not an official flood warning.",
        "source_file": rel(source),
        "data_loader_settings": {
            "date_col": a.date_col,
            "station_col": a.station_col,
            "rain_col": a.rain_col,
            "allow_sum_hourly": a.allow_sum_hourly,
        },
        "observation_window": {"start": cfg.WINDOW_START, "end": cfg.WINDOW_END},
        "stations_found": load_info["stations_found"], "stations_missing": load_info["stations_missing"],
        "observed_days_per_station": load_info["observed_days_per_station"],
        "used_hourly_sum_UNVERIFIED": load_info["used_hourly_sum"],
        "rainfall_unit_note": cfg.UNIT_NOTE,
        "target_definition": info, "split_info": info["splits"],
        "feature_columns": FEATURE_COLUMNS,
        "rainy_day_threshold": cfg.RAINY_DAY_THRESHOLD,
        "rainy_day_threshold_note": "source rainfall units; internal research setting; "
                                    "physical unit not yet independently verified",
        "min_obs_fraction": cfg.MIN_OBS_FRACTION,
        "severity_thresholds": cfg.SEVERITY_THRESHOLDS,
        "trigger_candidate_threshold": cfg.TRIGGER_CANDIDATE_THRESHOLD,
        "thresholds_note": "Internal research thresholds; not government-defined.",
        "xgboost_params": xgb_params, "xgboost_best_iteration": best_it,
        "xgboost_decision_threshold": evaluation["models"]["xgboost"]["decision_threshold"],
        "feature_importance_gain_order": imp,
        "reservoir_features": "not used (units/schema unverified)",
        "random_seed": cfg.RANDOM_SEED,
        "library_versions": {"xgboost": xgboost.__version__, "scikit_learn": sklearn.__version__,
                             "pandas": pd.__version__, "numpy": np.__version__},
    }
    (cfg.MODELS_DIR / "model_metadata.json").write_text(
        json.dumps(meta, indent=2, default=json_default), encoding="utf-8")

    log("[8/10] generating plots ...")
    plot_rainfall(panel, info["stress_threshold_source_units"], cfg.PLOTS_DIR / "01_rainfall_over_time.png")
    plot_risk(preds, cfg.SEVERITY_THRESHOLDS, cfg.TRIGGER_CANDIDATE_THRESHOLD,
              cfg.PLOTS_DIR / "02_risk_score_over_time.png")
    plot_prob_vs_target(preds, cfg.PLOTS_DIR / "03_probability_vs_target.png")
    cm = evaluation["models"]["xgboost"]["splits"]["test"]["confusion_matrix"]["matrix"]
    plot_confusion(cm, cfg.PLOTS_DIR / "04_confusion_matrix_test.png", "XGBoost, test split (proxy target)")

    log("[9/10] writing model card ...")
    write_model_card(cfg.METADATA_DIR / "model_card.md", meta, evaluation)

    log("[10/10] done. Summary:")
    xt = evaluation["models"]["xgboost"]["splits"]["test"]
    print("=" * 70)
    print("PHASE 2 TRAINING SUMMARY (proxy target: high_rainfall_stress)")
    print(f"source: {rel(source)}   stations: {load_info['stations_found']}")
    print(f"stress threshold: {info['stress_threshold_source_units']:.4f} "
          f"({cfg.UNIT_NOTE}); {info['percentile']:.0%} percentile, train period only")
    for k, s in info["splits"].items():
        print(f"  {k:5s} n={s['n_rows']:4d} features {s['first_feature_date']}..{s['last_feature_date']} "
              f"targets {s['first_target_date']}..{s['last_target_date']} positives={s['n_positive']}")
    for name, m in evaluation["models"].items():
        x = m["splits"]["test"]
        print(f"  TEST {name:20s} ROC-AUC={x['roc_auc']} PR-AUC={x['pr_auc']} "
              f"P={x['precision']:.3f} R={x['recall']:.3f} F1={x['f1']:.3f} Brier={x['brier']:.3f}")
    print(f"  XGBoost test confusion [[TN,FP],[FN,TP]]: {xt['confusion_matrix']['matrix']}")
    for w in evaluation["warnings"]:
        print("  WARNING:", w)
    print(PROXY_NOTE)
    print("Artifacts: models/, Datasets/processed/model_*.csv, Datasets/metadata/model_*, artifacts/plots/",
          flush=True)
    return 0


def main(argv=None) -> int:
    """Run and never hide exceptions: print the traceback, return a non-zero code."""
    try:
        code = run(argv)
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - deliberate: report everything
        print(f"[train_risk_model] FAILED: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        traceback.print_exc()
        code = 1
    print(f"[train_risk_model] finished with exit code {code}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())