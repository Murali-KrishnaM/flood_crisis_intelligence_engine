#!/usr/bin/env python
"""Phase 3 persistence trigger (INTERNAL RESEARCH PROXY ONLY).

Turns the daily XGBoost probability (``p_xgboost``) into a persistence-based
trigger state. A day counts toward the streak when its risk score is at or
above the trigger threshold. The trigger becomes active once the streak
reaches the configured number of consecutive daily observations.

This module only reads model output. It does not change the model, the
target definition, or any metric. The trigger is not a flood prediction and
has no official status of any kind.

Persistence rules (all explicit, nothing silent):
  * The streak counts consecutive observed days at or above the threshold.
  * A day below the threshold resets the streak to 0.
  * A day with no risk score (NaN) is not an observation: it resets the streak.
  * A calendar gap larger than ``max_gap_days`` between two rows resets the
    streak. Missing dates are never filled in or invented as rows.
  * Input must be strictly increasing by date. Unsorted or duplicated dates
    are rejected, not repaired.

Usage:
    python scripts/risk_trigger.py                          # write history + config
    python scripts/risk_trigger.py --replay --start 2024-10-01 --end 2024-12-31
"""
from __future__ import annotations

import argparse
import json
import math
import numbers
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREDICTIONS = PROJECT_ROOT / "Datasets" / "processed" / "model_predictions.csv"
DEFAULT_HISTORY_OUT = PROJECT_ROOT / "Datasets" / "processed" / "risk_trigger_history.csv"
DEFAULT_CONFIG_OUT = PROJECT_ROOT / "Datasets" / "metadata" / "trigger_config.json"

SCORE_COLUMN = "p_xgboost"

OUTPUT_COLUMNS = [
    "date",
    "target_date",
    "risk_score",
    "severity",
    "consecutive_high_count",
    "trigger_active",
    "trigger_reason",
]
REPLAY_COLUMNS = [
    "date",
    "risk_score",
    "severity",
    "consecutive_high_count",
    "trigger_active",
    "trigger_reason",
]

TRIGGER_REPLAY_COLUMNS = [
    "trigger_severity",
    "consecutive_high_count",
    "trigger_active",
    "trigger_reason",
]

SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_HIGH = "HIGH"
SEVERITY_BELOW = "BELOW_HIGH"
SEVERITY_NO_DATA = "NO_DATA"
SEVERITY_LABELS = (SEVERITY_BELOW, SEVERITY_HIGH, SEVERITY_CRITICAL, SEVERITY_NO_DATA)

NOTICE = (
    "Internal research trigger on a model-output proxy "
    "(high_rainfall_stress at t+1). It does not predict floods and has no "
    "official status."
)


def _is_number(x: Any) -> bool:
    return isinstance(x, numbers.Real) and not isinstance(x, bool)


@dataclass(frozen=True)
class TriggerConfig:
    """Internal, configurable thresholds. Scores are model probabilities in [0, 1]."""

    high_threshold: float = 0.70
    critical_threshold: float = 0.85
    trigger_threshold: float = 0.70
    persistence_days: int = 3
    max_gap_days: int = 1

    def __post_init__(self) -> None:
        for name in ("high_threshold", "critical_threshold", "trigger_threshold"):
            v = getattr(self, name)
            if not _is_number(v) or math.isnan(float(v)) or not 0.0 <= float(v) <= 1.0:
                raise ValueError(f"{name} must be a number in [0, 1], got {v!r}")
        if self.high_threshold > self.critical_threshold:
            raise ValueError("high_threshold must not exceed critical_threshold")
        for name in ("persistence_days", "max_gap_days"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                raise ValueError(f"{name} must be an integer >= 1, got {v!r}")


def _clean_score(value: Any) -> float | None:
    """Return a float in [0, 1], or None when there is no score. Reject anything else."""
    if value is None or value is pd.NA:
        return None
    if isinstance(value, bool):
        raise ValueError(f"risk score must be numeric, got {value!r}")
    try:
        score = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"risk score must be numeric, got {value!r}") from None
    if math.isnan(score):
        return None
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"risk score {score} is outside [0, 1]")
    return score


def severity_from_score(risk_score: Any, config: TriggerConfig | None = None) -> str:
    """CRITICAL / HIGH / BELOW_HIGH / NO_DATA. Internal labels only."""
    config = config or TriggerConfig()
    score = _clean_score(risk_score)
    if score is None:
        return SEVERITY_NO_DATA
    if score >= config.critical_threshold:
        return SEVERITY_CRITICAL
    if score >= config.high_threshold:
        return SEVERITY_HIGH
    return SEVERITY_BELOW


def evaluate_trigger(
    risk_score: Any,
    previous_count: int = 0,
    config: TriggerConfig | None = None,
    gap_before: bool = False,
) -> dict[str, Any]:
    """Evaluate one observation given the streak length before it.

    ``gap_before=True`` means calendar days are missing between the previous
    observation and this one, so the earlier streak cannot continue.
    """
    config = config or TriggerConfig()
    if isinstance(previous_count, bool) or not isinstance(previous_count, int) or previous_count < 0:
        raise ValueError(f"previous_count must be an integer >= 0, got {previous_count!r}")

    score = _clean_score(risk_score)
    severity = severity_from_score(score, config)

    gap_reset = bool(gap_before) and previous_count > 0
    if gap_before:
        previous_count = 0

    if score is None:
        count = 0
        reason = "no_risk_score_streak_reset"
    elif score < config.trigger_threshold:
        count = 0
        reason = "below_trigger_threshold_streak_reset"
    else:
        count = previous_count + 1
        if count >= config.persistence_days:
            reason = f"persistence_met_{count}_consecutive_days"
        else:
            reason = f"above_trigger_threshold_{count}_of_{config.persistence_days}"
    if gap_reset:
        reason = "date_gap_streak_reset;" + reason

    return {
        "risk_score": float("nan") if score is None else score,
        "severity": severity,
        "consecutive_high_count": int(count),
        "trigger_active": bool(count >= config.persistence_days),
        "trigger_reason": reason,
    }


def _empty_history() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.Series(dtype="datetime64[ns]"),
            "target_date": pd.Series(dtype="datetime64[ns]"),
            "risk_score": pd.Series(dtype="float64"),
            "severity": pd.Series(dtype="object"),
            "consecutive_high_count": pd.Series(dtype="int64"),
            "trigger_active": pd.Series(dtype="bool"),
            "trigger_reason": pd.Series(dtype="object"),
        }
    )[OUTPUT_COLUMNS]


def process_prediction_history(
    history: pd.DataFrame,
    config: TriggerConfig | None = None,
    score_column: str = SCORE_COLUMN,
) -> pd.DataFrame:
    """Run the persistence trigger over a chronological prediction history.

    Requires columns ``date`` and ``score_column``; ``target_date`` is used if
    present, otherwise it is date + 1 day. Raises ValueError for unsorted or
    duplicated dates, unparseable dates, or scores outside [0, 1]. Returns one
    output row per input row (no rows are added or dropped).
    """
    config = config or TriggerConfig()
    if not isinstance(history, pd.DataFrame):
        raise TypeError("history must be a pandas DataFrame")
    missing = [c for c in ("date", score_column) if c not in history.columns]
    if missing:
        raise ValueError(f"history is missing required columns {missing}; found {list(history.columns)}")
    if history.empty:
        return _empty_history()

    dates = pd.to_datetime(history["date"], errors="coerce")
    if dates.isna().any():
        raise ValueError(f"{int(dates.isna().sum())} unparseable values in 'date'")
    dates = dates.dt.normalize().reset_index(drop=True)
    if dates.duplicated().any():
        raise ValueError("duplicate dates in prediction history")
    if not dates.is_monotonic_increasing:
        raise ValueError("prediction history is not in chronological order (sort by date first)")

    if "target_date" in history.columns:
        targets = pd.to_datetime(history["target_date"], errors="coerce").dt.normalize().reset_index(drop=True)
    else:
        targets = dates + pd.Timedelta(days=1)

    scores = history[score_column].tolist()
    rows: list[dict[str, Any]] = []
    prev_count = 0
    prev_date = None
    for d, td, s in zip(dates, targets, scores):
        gap = prev_date is not None and (d - prev_date).days > config.max_gap_days
        res = evaluate_trigger(s, prev_count, config, gap_before=gap)
        rows.append({"date": d, "target_date": td, **res})
        prev_count = res["consecutive_high_count"]
        prev_date = d

    out = pd.DataFrame(rows)[OUTPUT_COLUMNS]
    out["risk_score"] = out["risk_score"].astype("float64")
    out["consecutive_high_count"] = out["consecutive_high_count"].astype("int64")
    out["trigger_active"] = out["trigger_active"].astype(bool)
    return out


def load_prediction_history(path: Path | str, score_column: str = SCORE_COLUMN) -> pd.DataFrame:
    """Read model_predictions.csv and sort chronologically by date."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Prediction file does not exist: {path}")
    df = pd.read_csv(path)
    missing = [c for c in ("date", score_column) if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name} is missing required columns {missing}; found {list(df.columns)}")
    try:
        df["date"] = pd.to_datetime(df["date"], format="ISO8601")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"unparseable values in 'date' of {path.name}: {exc}") from exc
    return df.sort_values("date", kind="stable").reset_index(drop=True)


def replay_with_trigger(
    predictions_path: Path | str = DEFAULT_PREDICTIONS,
    start: str | None = None,
    end: str | None = None,
    config: TriggerConfig | None = None,
    score_column: str = SCORE_COLUMN,
) -> pd.DataFrame:
    """Trigger state for an inclusive date window.

    The trigger runs over the FULL history first and is sliced afterwards, so a
    streak that began before ``start`` is counted correctly.
    """
    full = process_prediction_history(load_prediction_history(predictions_path, score_column), config, score_column)
    start_ts = pd.Timestamp(start).normalize() if start else None
    end_ts = pd.Timestamp(end).normalize() if end else None
    if start_ts is not None and end_ts is not None and start_ts > end_ts:
        raise ValueError(f"start {start_ts.date()} is after end {end_ts.date()}")
    mask = pd.Series(True, index=full.index)
    if start_ts is not None:
        mask &= full["date"] >= start_ts
    if end_ts is not None:
        mask &= full["date"] <= end_ts
    return full.loc[mask, REPLAY_COLUMNS].reset_index(drop=True)


def load_trigger_config(path: Path | str | None = None) -> TriggerConfig:
    """Load thresholds from trigger_config.json; ``None`` gives the built-in defaults."""
    if path is None:
        return TriggerConfig()
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Trigger config does not exist: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    allowed = {f.name for f in fields(TriggerConfig)}
    return TriggerConfig(**{k: v for k, v in data.items() if k in allowed})


def trigger_columns_for_window(
    full_dates: Any,
    full_scores: Any,
    window_dates: Any,
    config: TriggerConfig | None = None,
) -> pd.DataFrame:
    """Trigger columns for ``window_dates``, computed over the FULL score history.

    Used by replay_risk.py. ``full_dates``/``full_scores`` are the whole daily
    history (NaN = no score that day); every window date must exist in it.
    Returns a frame indexed by ``window_dates`` with TRIGGER_REPLAY_COLUMNS.
    """
    full = pd.DataFrame({
        "date": pd.to_datetime(list(full_dates)),
        "risk_score": pd.Series(list(full_scores), dtype="float64"),
    })
    processed = process_prediction_history(full, config, score_column="risk_score").set_index("date")
    window = pd.DatetimeIndex(pd.to_datetime(list(window_dates))).normalize()
    missing = window.difference(processed.index)
    if len(missing):
        raise ValueError(f"{len(missing)} window dates are not in the score history, e.g. {missing[0].date()}")
    sel = processed.loc[window].rename(columns={"severity": "trigger_severity"})
    return sel[TRIGGER_REPLAY_COLUMNS]


def write_history(history: pd.DataFrame, path: Path | str = DEFAULT_HISTORY_OUT) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    history.to_csv(path, index=False, date_format="%Y-%m-%d")
    return path


def write_trigger_config(
    config: TriggerConfig,
    path: Path | str = DEFAULT_CONFIG_OUT,
    score_column: str = SCORE_COLUMN,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **asdict(config),
        "score_column": score_column,
        "severity_labels": list(SEVERITY_LABELS),
        "persistence_rule": (
            "trigger_active when risk_score >= trigger_threshold on persistence_days "
            "consecutive observed daily rows; a day below threshold, a day with no score, "
            "or a calendar gap larger than max_gap_days resets the count"
        ),
        "notice": NOTICE,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _format_table(df: pd.DataFrame) -> str:
    shown = df.copy()
    shown["date"] = shown["date"].dt.strftime("%Y-%m-%d")
    shown["risk_score"] = shown["risk_score"].map(lambda v: "NaN" if pd.isna(v) else f"{v:.4f}")
    return shown.to_string(index=False)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Phase 3 persistence trigger (internal research proxy).")
    p.add_argument("--predictions", default=str(DEFAULT_PREDICTIONS))
    p.add_argument("--history-out", default=str(DEFAULT_HISTORY_OUT))
    p.add_argument("--config-out", default=str(DEFAULT_CONFIG_OUT))
    p.add_argument("--score-column", default=SCORE_COLUMN)
    p.add_argument("--high-threshold", type=float, default=0.70)
    p.add_argument("--critical-threshold", type=float, default=0.85)
    p.add_argument("--trigger-threshold", type=float, default=0.70)
    p.add_argument("--persistence-days", type=int, default=3)
    p.add_argument("--max-gap-days", type=int, default=1)
    p.add_argument("--replay", action="store_true", help="print a date window; write no files")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    config = TriggerConfig(
        high_threshold=args.high_threshold,
        critical_threshold=args.critical_threshold,
        trigger_threshold=args.trigger_threshold,
        persistence_days=args.persistence_days,
        max_gap_days=args.max_gap_days,
    )

    if args.replay:
        window = replay_with_trigger(args.predictions, args.start, args.end, config, args.score_column)
        print(_format_table(window))
        print(f"\n{len(window)} rows | trigger_active days: {int(window['trigger_active'].sum())}")
        print(NOTICE)
        return 0

    history = process_prediction_history(
        load_prediction_history(args.predictions, args.score_column), config, args.score_column
    )
    hist_path = write_history(history, args.history_out)
    cfg_path = write_trigger_config(config, args.config_out, args.score_column)

    active = history[history["trigger_active"]]
    print(f"rows processed:        {len(history)}")
    print(f"date range:            {history['date'].min().date()} .. {history['date'].max().date()}")
    print(f"days >= trigger:       {int((history['consecutive_high_count'] > 0).sum())}")
    print(f"trigger_active days:   {len(active)}")
    print(f"longest streak:        {int(history['consecutive_high_count'].max())}")
    if len(active):
        print(f"first / last active:   {active['date'].min().date()} / {active['date'].max().date()}")
    print(f"history written to:    {hist_path}")
    print(f"config written to:     {cfg_path}")
    print(NOTICE)
    return 0


if __name__ == "__main__":
    sys.exit(main())