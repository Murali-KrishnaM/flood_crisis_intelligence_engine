"""Offline unit tests for the Phase 3 persistence trigger (tiny synthetic fixtures only).

The trigger is an internal research proxy on model output. These tests make no
claim that it predicts floods.
"""
import json
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from risk_trigger import (  # noqa: E402
    OUTPUT_COLUMNS, SEVERITY_LABELS, TRIGGER_REPLAY_COLUMNS, TriggerConfig, evaluate_trigger,
    load_prediction_history, load_trigger_config, main, process_prediction_history,
    replay_with_trigger, trigger_columns_for_window, write_trigger_config,
)

START = "2024-10-01"


def hist(scores, start=START):
    """Consecutive daily rows. scores may contain None/NaN (no score that day)."""
    dates = pd.date_range(start, periods=len(scores), freq="D")
    return pd.DataFrame({"date": dates, "p_xgboost": scores})


def run(scores, config=None):
    return process_prediction_history(hist(scores), config)


# 1
def test_one_high_day_does_not_trigger():
    out = run([0.10, 0.75, 0.10])
    assert out["trigger_active"].tolist() == [False, False, False]
    assert out["consecutive_high_count"].tolist() == [0, 1, 0]


# 2
def test_two_high_days_do_not_trigger():
    out = run([0.10, 0.75, 0.80])
    assert out["trigger_active"].tolist() == [False, False, False]
    assert out["consecutive_high_count"].tolist() == [0, 1, 2]


# 3
def test_three_consecutive_high_days_trigger():
    out = run([0.75, 0.80, 0.72, 0.71])
    assert out["consecutive_high_count"].tolist() == [1, 2, 3, 4]
    assert out["trigger_active"].tolist() == [False, False, True, True]
    assert out.loc[2, "trigger_reason"].startswith("persistence_met_3")


# 4
def test_low_day_resets_persistence():
    out = run([0.80, 0.80, 0.20, 0.80, 0.80, 0.80])
    assert out["consecutive_high_count"].tolist() == [1, 2, 0, 1, 2, 3]
    assert out["trigger_active"].tolist() == [False, False, False, False, False, True]
    assert out.loc[2, "trigger_reason"] == "below_trigger_threshold_streak_reset"


# 5
def test_critical_days_count_toward_persistence():
    out = run([0.90, 0.75, 0.95])
    assert out["severity"].tolist() == ["CRITICAL", "HIGH", "CRITICAL"]
    assert out["consecutive_high_count"].tolist() == [1, 2, 3]
    assert out["trigger_active"].tolist() == [False, False, True]


# 6
def test_missing_dates_do_not_become_observations():
    h = pd.DataFrame({
        "date": pd.to_datetime(["2024-10-01", "2024-10-02", "2024-10-04", "2024-10-05", "2024-10-06"]),
        "p_xgboost": [0.8, 0.8, 0.8, 0.8, 0.8]})          # 2024-10-03 is absent
    out = process_prediction_history(h)
    assert len(out) == 5                                      # no invented row for the gap
    assert pd.Timestamp("2024-10-03") not in set(out["date"])
    assert out["consecutive_high_count"].tolist() == [1, 2, 1, 2, 3]   # gap reset the streak
    assert out["trigger_active"].tolist() == [False, False, False, False, True]
    assert out.loc[2, "trigger_reason"].startswith("date_gap_streak_reset")


def test_nan_score_is_not_an_observation_and_resets():
    out = run([0.8, 0.8, float("nan"), 0.8])
    assert out["severity"].tolist()[2] == "NO_DATA"
    assert math.isnan(out.loc[2, "risk_score"])
    assert out["consecutive_high_count"].tolist() == [1, 2, 0, 1]
    assert out["trigger_active"].tolist() == [False, False, False, False]


# 7
def test_chronological_order_is_enforced():
    h = hist([0.8, 0.8, 0.8])
    with pytest.raises(ValueError, match="chronological"):
        process_prediction_history(h.iloc[::-1].reset_index(drop=True))
    dup = pd.concat([h, h.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        process_prediction_history(dup)


def test_loader_sorts_chronologically(tmp_path):
    h = hist([0.8, 0.1, 0.9]).iloc[[2, 0, 1]]
    p = tmp_path / "pred.csv"
    h.to_csv(p, index=False)
    loaded = load_prediction_history(p)
    assert loaded["date"].is_monotonic_increasing
    assert loaded["p_xgboost"].tolist() == [0.8, 0.1, 0.9]
    out = process_prediction_history(loaded)
    assert out["date"].tolist() == sorted(out["date"].tolist())


# 8
def test_score_outside_unit_interval_is_rejected():
    for bad in (1.01, -0.01, float("inf")):
        with pytest.raises(ValueError):
            run([0.5, bad])
        with pytest.raises(ValueError):
            evaluate_trigger(bad)
    with pytest.raises(ValueError):
        evaluate_trigger("high")


# 9
def test_thresholds_and_persistence_are_configurable():
    cfg = TriggerConfig(high_threshold=0.5, critical_threshold=0.6, trigger_threshold=0.5, persistence_days=2)
    out = run([0.55, 0.65], cfg)
    assert out["severity"].tolist() == ["HIGH", "CRITICAL"]
    assert out["trigger_active"].tolist() == [False, True]          # 2 days, not 3
    assert run([0.55, 0.65])["trigger_active"].tolist() == [False, False]   # defaults stay 0.70 / 3
    assert evaluate_trigger(0.70)["consecutive_high_count"] == 1             # boundary is inclusive
    assert evaluate_trigger(0.85)["severity"] == "CRITICAL"
    with pytest.raises(ValueError):
        TriggerConfig(high_threshold=0.9, critical_threshold=0.8)
    with pytest.raises(ValueError):
        TriggerConfig(trigger_threshold=1.5)
    with pytest.raises(ValueError):
        TriggerConfig(persistence_days=0)


# 10
def test_output_schema_is_stable():
    out = run([0.1, 0.8, 0.9, 0.95])
    assert list(out.columns) == OUTPUT_COLUMNS == [
        "date", "target_date", "risk_score", "severity",
        "consecutive_high_count", "trigger_active", "trigger_reason"]
    assert out["trigger_active"].dtype == bool
    assert out["consecutive_high_count"].dtype.kind == "i"
    assert out["risk_score"].dtype.kind == "f"
    assert set(out["severity"]) <= set(SEVERITY_LABELS)
    assert (out["target_date"] == out["date"] + pd.Timedelta(days=1)).all()   # derived when absent
    empty = process_prediction_history(hist([]))
    assert list(empty.columns) == OUTPUT_COLUMNS and len(empty) == 0


def test_target_date_column_is_passed_through():
    h = hist([0.8, 0.8])
    h["target_date"] = h["date"] + pd.Timedelta(days=1)
    assert process_prediction_history(h)["target_date"].tolist() == h["target_date"].tolist()


def test_reasons_use_no_official_language():
    out = run([0.1, 0.8, 0.8, 0.8, float("nan")])
    text = " ".join(out["trigger_reason"]).lower()
    for word in ("warning", "alert", "evacuat", "emergency", "flood"):
        assert word not in text


# ---------- config / CLI / replay ----------
def test_config_file_records_actual_values(tmp_path):
    cfg = TriggerConfig(high_threshold=0.6, critical_threshold=0.8, trigger_threshold=0.6, persistence_days=4)
    path = write_trigger_config(cfg, tmp_path / "meta" / "trigger_config.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["high_threshold"] == 0.6 and data["critical_threshold"] == 0.8
    assert data["trigger_threshold"] == 0.6 and data["persistence_days"] == 4
    assert data["score_column"] == "p_xgboost" and "notice" in data


def _write_predictions(tmp_path):
    h = hist([0.1, 0.8, 0.8, 0.9, 0.2, 0.8])
    h["target_date"] = h["date"] + pd.Timedelta(days=1)
    h["split"] = "test"
    p = tmp_path / "model_predictions.csv"
    h.to_csv(p, index=False)
    return p


def test_cli_writes_history_and_config(tmp_path):
    pred = _write_predictions(tmp_path)
    hist_out, cfg_out = tmp_path / "out" / "history.csv", tmp_path / "meta" / "cfg.json"
    assert main(["--predictions", str(pred), "--history-out", str(hist_out), "--config-out", str(cfg_out)]) == 0
    written = pd.read_csv(hist_out)
    assert list(written.columns) == OUTPUT_COLUMNS
    assert written["trigger_active"].tolist() == [False, False, False, True, False, False]
    assert json.loads(cfg_out.read_text(encoding="utf-8"))["persistence_days"] == 3


def test_replay_uses_full_history_for_streak_before_window(tmp_path):
    pred = _write_predictions(tmp_path)
    win = replay_with_trigger(pred, start="2024-10-04", end="2024-10-04")
    assert len(win) == 1
    assert int(win.loc[0, "consecutive_high_count"]) == 3 and bool(win.loc[0, "trigger_active"])
    with pytest.raises(ValueError):
        replay_with_trigger(pred, start="2024-10-05", end="2024-10-01")


# ---------- helpers used by replay_risk.py --with-trigger ----------
def test_window_trigger_columns_count_streak_started_before_window():
    dates = pd.date_range(START, periods=6)
    cols = trigger_columns_for_window(dates, [0.8, 0.8, 0.8, 0.8, 0.2, 0.8], dates[2:5])
    assert list(cols.columns) == TRIGGER_REPLAY_COLUMNS
    assert (cols.index == dates[2:5]).all()
    assert cols["consecutive_high_count"].tolist() == [3, 4, 0]
    assert cols["trigger_active"].tolist() == [True, True, False]


def test_window_trigger_columns_nan_resets_and_missing_window_date_rejected():
    dates = pd.date_range(START, periods=5)
    cols = trigger_columns_for_window(dates, [0.8, 0.8, float("nan"), 0.8, 0.8], dates)
    assert cols["trigger_severity"].tolist()[2] == "NO_DATA"
    assert cols["trigger_active"].tolist() == [False] * 5
    with pytest.raises(ValueError):
        trigger_columns_for_window(dates, [0.8] * 5, pd.date_range("2025-01-01", periods=2))


def test_load_trigger_config_roundtrip_and_defaults(tmp_path):
    cfg = TriggerConfig(high_threshold=0.6, critical_threshold=0.8, trigger_threshold=0.6, persistence_days=2)
    path = write_trigger_config(cfg, tmp_path / "c.json")
    assert load_trigger_config(path) == cfg
    assert load_trigger_config(None) == TriggerConfig()
    with pytest.raises(FileNotFoundError):
        load_trigger_config(tmp_path / "nope.json")