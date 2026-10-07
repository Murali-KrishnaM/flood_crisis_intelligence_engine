"""Offline unit tests for Phase 2 (tiny synthetic fixtures; no RTFF access needed).

Column convention: the long-format rainfall column is `rainfall_value` (SOURCE UNITS;
physical unit not independently verified). Never `rainfall_mm`.
"""
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from risk_data import inspect_source, load_daily_station_rainfall  # noqa: E402
from risk_features import (FEATURE_COLUMNS, build_daily_panel, build_features,  # noqa: E402
                           chronological_split, prepare_dataset)
from risk_inference import load_model_bundle, predict_risk, severity_from_score  # noqa: E402

START = "2024-01-01"


def panel_from(vals, start=START):
    """vals: list of floats or None (None = station did not observe that day)."""
    dates = pd.date_range(start, periods=len(vals), freq="D")
    rows = [{"date": d, "station": "Anna_University", "rainfall_value": v}
            for d, v in zip(dates, vals) if v is not None]
    return build_daily_panel(pd.DataFrame(rows), start=dates[0], end=dates[-1])


def synthetic_panel(n=200, seed=0):
    rng = np.random.default_rng(seed)
    return panel_from([float(x) for x in rng.gamma(0.5, 6.0, size=n)])


# ---------- missing data / panel ----------
def test_missing_not_zero_filled_and_station_count():
    long = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-01-03"]),
        "station": ["A", "B", "A"], "rainfall_value": [2.0, 4.0, 6.0]})
    p = build_daily_panel(long, "2024-01-01", "2024-01-03")
    assert p.loc["2024-01-01", "local_rainfall_mean"] == 3.0
    assert p.loc["2024-01-01", "local_rainfall_max"] == 4.0
    assert p.loc["2024-01-01", "local_rainfall_min"] == 2.0
    assert p.loc["2024-01-01", "station_count_available"] == 2
    assert math.isnan(p.loc["2024-01-02", "local_rainfall_mean"])     # NOT 0
    assert p.loc["2024-01-02", "station_count_available"] == 0
    assert p.loc["2024-01-03", "local_rainfall_mean"] == 6.0


def test_panel_rejects_legacy_unit_column_name():
    legacy = pd.DataFrame({"date": pd.to_datetime(["2024-01-01"]), "station": ["A"],
                           "rainfall_mm": [1.0]})
    with pytest.raises(ValueError):
        build_daily_panel(legacy)


def test_missing_data_handling_in_dataset():
    vals = [float(x) for x in np.random.default_rng(1).gamma(0.5, 6.0, 120)]
    vals[50] = None
    p = panel_from(vals)
    ds, _ = prepare_dataset(p)
    d49, d50 = pd.Timestamp(START) + pd.Timedelta(days=49), pd.Timestamp(START) + pd.Timedelta(days=50)
    assert not ds.loc[d49, "eligible"] and math.isnan(ds.loc[d49, "high_rainfall_stress"])  # next missing
    assert not ds.loc[d50, "eligible"]                                                    # today missing
    assert ds.loc[d50, "rainfall_missing_today"] == 1
    assert math.isnan(ds.loc[d50, "rainfall_today"])


# ---------- lag / rolling ----------
def test_lag_features_use_calendar_days():
    f = build_features(panel_from([0, 1, 2, None, 4, 5, 6, 7, 8, 9]))
    d4 = pd.Timestamp(START) + pd.Timedelta(days=4)
    assert math.isnan(f.loc[d4, "rainfall_lag_1"])        # day 3 missing, not shifted past
    assert f.loc[d4, "rainfall_lag_2"] == 2
    assert f.loc[d4, "rainfall_lag_3"] == 1
    d7 = pd.Timestamp(START) + pd.Timedelta(days=7)
    assert f.loc[d7, "rainfall_lag_7"] == 0
    assert f.loc[d7, "rainfall_change_1d"] == 1


def test_rolling_features_and_missing_rule():
    f = build_features(panel_from([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]))
    d = lambda i: pd.Timestamp(START) + pd.Timedelta(days=i)
    assert f.loc[d(2), "rolling_rainfall_3d"] == 6
    assert f.loc[d(6), "rolling_rainfall_7d"] == 28
    assert f.loc[d(13), "rolling_rainfall_14d"] == 105
    g = build_features(panel_from([1, None, None, 4, 1, None, 3]))
    assert math.isnan(g.loc[d(3), "rolling_rainfall_3d"])         # 1 of 3 observed < 2 required
    assert g.loc[d(6), "rolling_rainfall_3d"] == 4.0              # observed 1 + 3, NOT zero-filled
    assert g.loc[d(6), "observed_fraction_7d"] == pytest.approx(4 / 7)


def test_rainy_days_counts():
    f = build_features(panel_from([0, 3, 0, 5, 0, 2.5, 1]))
    assert f.loc[pd.Timestamp(START) + pd.Timedelta(days=6), "rainy_days_last_7d"] == 3


# ---------- leakage ----------
def test_no_future_leakage_in_features():
    p = synthetic_panel(60)
    full = build_features(p)
    cut = build_features(p.iloc[:30])
    pd.testing.assert_frame_equal(full.iloc[:30], cut, check_freq=False)
    p2 = p.copy()
    p2.iloc[30:, p2.columns.get_loc("local_rainfall_mean")] = 9999.0
    pd.testing.assert_frame_equal(full.iloc[:30], build_features(p2).iloc[:30], check_freq=False)


# ---------- split ----------
def test_chronological_split():
    idx = pd.date_range(START, periods=100, freq="D")
    s = chronological_split(idx, 0.70, 0.15)
    assert (s == "train").sum() == 70 and (s == "val").sum() == 15 and (s == "test").sum() == 15
    assert s.index[s == "train"].max() < s.index[s == "val"].min()
    assert s.index[s == "val"].max() < s.index[s == "test"].min()
    with pytest.raises(ValueError):
        chronological_split(idx[::-1])
    with pytest.raises(ValueError):
        chronological_split(idx, 0.9, 0.2)


# ---------- target ----------
def test_target_generation_future_only_and_train_only_threshold():
    vals = [float(x) for x in np.random.default_rng(0).gamma(0.5, 6.0, 200)]
    p = panel_from(vals)
    ds, info = prepare_dataset(p, percentile=0.9)
    thr = info["stress_threshold_source_units"]
    el = ds[ds["eligible"]]
    for t in (10, 60, 150):
        d = pd.Timestamp(START) + pd.Timedelta(days=t)
        assert ds.loc[d, "high_rainfall_stress"] == float(vals[t + 1] >= thr)   # day t+1, not t
    assert not ds.iloc[-1]["eligible"] and math.isnan(ds.iloc[-1]["high_rainfall_stress"])
    cutoff = pd.Timestamp(info["threshold_fitted_on_days_up_to"])
    expected = p.loc[:cutoff, "local_rainfall_mean"].dropna().quantile(0.9)
    assert thr == pytest.approx(expected)
    assert cutoff < el[el.split == "val"]["target_date"].min()
    # changing TEST-period rainfall must not change the threshold
    p2 = p.copy()
    p2.iloc[-20:, p2.columns.get_loc("local_rainfall_mean")] = 1000.0
    assert prepare_dataset(p2, percentile=0.9)[1]["stress_threshold_source_units"] == pytest.approx(thr)


# ---------- unit neutrality ----------
def test_threshold_naming_is_unit_neutral():
    _, info = prepare_dataset(synthetic_panel(200))
    assert "stress_threshold_source_units" in info
    assert "stress_threshold_mm" not in info
    assert "not yet independently verified" in info["threshold_unit_note"]


def test_phase2_code_has_no_hardcoded_unit_claims():
    files = sorted(SCRIPTS_DIR.glob("risk_*.py")) + [SCRIPTS_DIR / "train_risk_model.py",
                                                     SCRIPTS_DIR / "replay_risk.py"]
    assert len(files) == 8, [f.name for f in files]      # 6 risk_*.py (incl. risk_trigger, Phase 3) + train_risk_model + replay_risk
    assert all(f.exists() for f in files)
    unit_re = re.compile(r"(?<![A-Za-z0-9_])mm(?![A-Za-z0-9_])|(?i:millimet)")
    name_re = re.compile(r"stress_threshold_mm|rainy_day_mm|RAINY_DAY_MM|threshold_mm|thr_mm")
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert not unit_re.search(text), f"unit claim found in {f.name}"
        assert not name_re.search(text), f"unit-bearing identifier found in {f.name}"


def test_internal_rainfall_column_is_rainfall_value_everywhere():
    """Outside the file-column-NAME detection list, 'rainfall_mm' must not appear in Phase 2 code."""
    files = sorted(SCRIPTS_DIR.glob("risk_*.py")) + [SCRIPTS_DIR / "train_risk_model.py",
                                                     SCRIPTS_DIR / "replay_risk.py"]
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            if "rainfall_mm" in line:
                # allowed only inside the auto-detect candidate list of risk_data.py
                assert f.name == "risk_data.py" and ('"rainfall_mm"' in line or '"daily_rainfall_mm"' in line), \
                    f"unexpected rainfall_mm in {f.name}: {line.strip()}"


# ---------- severity ----------
def test_risk_severity_mapping():
    th = {"MODERATE": 0.3, "HIGH": 0.6}
    assert severity_from_score(0.0, th) == "LOW"
    assert severity_from_score(0.299, th) == "LOW"
    assert severity_from_score(0.3, th) == "MODERATE"
    assert severity_from_score(0.83, th) == "HIGH"
    assert severity_from_score(float("nan"), th) == "NO_DATA"
    assert severity_from_score(0.5, {"A": 0.2, "B": 0.4, "C": 0.9}) == "B"   # configurable
    with pytest.raises(ValueError):
        severity_from_score(1.5, th)
    with pytest.raises(ValueError):
        severity_from_score(0.5, {"LOW": 0.1})
    with pytest.raises(ValueError):
        severity_from_score(0.5, {"X": 0.0})


# ---------- inference ----------
@pytest.fixture()
def tiny_model_dir(tmp_path):
    from xgboost import XGBClassifier
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(200, len(FEATURE_COLUMNS))), columns=FEATURE_COLUMNS)
    y = (X.iloc[:, 0] > 0).astype(int)
    clf = XGBClassifier(n_estimators=10, max_depth=2, random_state=0, n_jobs=1).fit(X, y)
    clf.save_model(str(tmp_path / "xgboost_model.json"))
    (tmp_path / "feature_columns.json").write_text(json.dumps(FEATURE_COLUMNS))
    (tmp_path / "model_metadata.json").write_text(json.dumps(
        {"severity_thresholds": {"MODERATE": 0.3, "HIGH": 0.6}}))
    return tmp_path


def test_inference_schema(tiny_model_dir):
    bundle = load_model_bundle(tiny_model_dir)
    row = {c: 0.0 for c in FEATURE_COLUMNS}
    row.update(rainfall_today=1.5, station_count_available=2)
    out = predict_risk(row, bundle=bundle)
    assert {"risk_score", "severity", "target", "prediction_horizon"} <= set(out)
    assert 0.0 <= out["risk_score"] <= 1.0
    assert out["severity"] in {"LOW", "MODERATE", "HIGH"}
    assert out["target"] == "high_rainfall_stress" and out["prediction_horizon"] == "next_day"
    # custom thresholds are respected
    assert predict_risk(row, bundle=bundle, severity_thresholds={"HIGH": 0.0001})["severity"] == "HIGH"
    # missing feature is an error, not a silent default
    bad = dict(row); bad.pop("rainfall_lag_1")
    with pytest.raises(ValueError):
        predict_risk(bad, bundle=bundle)
    # no observation today -> NO_DATA, no score
    nodata = dict(row); nodata.update(rainfall_today=float("nan"), station_count_available=0)
    res = predict_risk(nodata, bundle=bundle)
    assert res["risk_score"] is None and res["severity"] == "NO_DATA"


# ---------- loader / inspect ----------
def test_loader_preserves_nan_and_rejects_conflicts(tmp_path):
    csv = tmp_path / "day.csv"
    pd.DataFrame({
        "date": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-01"],
        "station_id": ["Anna_University", "Anna_University", "Anna_University", "Other_Place"],
        "daily_rainfall": [1.0, None, -5.0, 9.0]}).to_csv(csv, index=False)
    long, info = load_daily_station_rainfall(csv, start="2024-01-01", end="2024-01-31")
    assert set(long["station"]) == {"Anna_University"}                       # unrelated station dropped
    assert list(long.columns) == ["date", "station", "rainfall_value"]
    assert long["rainfall_value"].isna().sum() == 2                         # NaN kept + negative -> NaN
    assert info["negative_values_set_to_nan"] == 1
    conflict = tmp_path / "dup.csv"
    pd.DataFrame({"date": ["2024-01-01"] * 2, "station_id": ["Anna_University"] * 2,
                  "daily_rainfall": [1.0, 2.0]}).to_csv(conflict, index=False)
    with pytest.raises(ValueError):
        load_daily_station_rainfall(conflict, start="2024-01-01", end="2024-01-31")


def test_loader_info_reports_rows_read_and_matched(tmp_path):
    """train_risk_model.py reads rows_read and rows_matched_to_project_stations from load_info."""
    csv = tmp_path / "day.csv"
    pd.DataFrame({
        "date": ["2024-01-01", "2024-01-02", "2024-01-01", "2024-01-01", "2024-01-01"],
        "station_id": ["Anna_University", "Anna_University", "CHN_TARAMANI", "Other_Place", "_meta"],
        "daily_rainfall": [1.0, 2.0, 3.0, 9.0, 0.0]}).to_csv(csv, index=False)
    long, info = load_daily_station_rainfall(csv)
    assert info["rows_read"] == 5                              # every CSV row, incl. _meta and non-project
    assert info["rows_matched_to_project_stations"] == 3       # 2 Anna + 1 TARAMANI; _meta/Other_Place excluded
    assert info["rows_matched_to_project_stations"] == len(long)
    assert info["rows_read"] == info["input_rows"]             # existing keys preserved
    assert info["metadata_rows_excluded"] == 1 and info["non_project_station_rows_excluded"] == 1


def test_inspect_reports_schema_and_fails_loudly(tmp_path):
    csv = tmp_path / "day.csv"
    pd.DataFrame({"date": ["2024-01-01", "2024-01-02"], "station_id": ["Anna_University"] * 2,
                  "daily_rainfall": [1.0, 2.0]}).to_csv(csv, index=False)
    rep = inspect_source(csv)
    assert rep["date_col"] == "date" and rep["station_col"] == "station_id"
    assert rep["rain_col"] == "daily_rainfall" and rep["n_rows"] == 2
    with pytest.raises(FileNotFoundError):
        inspect_source(tmp_path / "missing.csv")
    empty = tmp_path / "empty.csv"
    empty.write_text("")
    with pytest.raises(Exception):          # pandas EmptyDataError or our ValueError; never silent
        inspect_source(empty)