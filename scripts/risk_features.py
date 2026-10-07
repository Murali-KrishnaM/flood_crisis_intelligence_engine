"""Feature engineering, target construction and chronological split.

Design rules
- Everything is computed on a FULL CALENDAR DAY INDEX so shift(k) means k days.
- Missing rainfall stays NaN. Rolling features need >= ceil(window * MIN_OBS_FRACTION)
  observed days, and sum only OBSERVED days (no zero-fill, no interpolation).
- Features at day t use observations <= t only.
- Target = stress quantity on day t+horizon >= threshold fitted on the TRAIN period only.
- All rainfall quantities and thresholds are in SOURCE UNITS (physical unit unverified).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from risk_config import (HORIZON_DAYS, MIN_OBS_FRACTION, RAINY_DAY_THRESHOLD, STRESS_PERCENTILE,
                         TARGET_NAME, TRAIN_FRAC, UNIT_NOTE, VAL_FRAC)

FEATURE_COLUMNS = [
    "rainfall_today",
    "rainfall_lag_1", "rainfall_lag_2", "rainfall_lag_3", "rainfall_lag_7",
    "rolling_rainfall_3d", "rolling_rainfall_7d", "rolling_rainfall_14d",
    "rainy_days_last_7d", "rainy_days_last_14d",
    "rainfall_change_1d", "rainfall_change_3d",
    "local_rainfall_max", "local_rainfall_min", "station_count_available",
    "rainfall_missing_today", "observed_fraction_7d",
    "month_sin", "month_cos", "is_ne_monsoon", "is_sw_monsoon",
]


def build_daily_panel(long_df: pd.DataFrame, start=None, end=None) -> pd.DataFrame:
    """Long [date, station, rainfall_value] -> daily panel on a full calendar index.

    local_rainfall_mean/max/min are computed over stations observed that day (NaN if none).
    """
    need = {"date", "station", "rainfall_value"}
    if need - set(long_df.columns):
        raise ValueError(f"long_df must have columns {sorted(need)}")
    if long_df.empty:
        raise ValueError("long_df is empty")
    df = long_df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    if df.duplicated(["date", "station"]).any():
        raise ValueError("Duplicate station-date rows; resolve before building the panel.")
    wide = df.pivot(index="date", columns="station", values="rainfall_value")
    lo = pd.Timestamp(start) if start is not None else wide.index.min()
    hi = pd.Timestamp(end) if end is not None else wide.index.max()
    wide = wide.reindex(pd.date_range(lo, hi, freq="D"))
    wide.index.name = "date"
    panel = pd.DataFrame(index=wide.index)
    panel["local_rainfall_mean"] = wide.mean(axis=1, skipna=True)
    panel["local_rainfall_max"] = wide.max(axis=1, skipna=True)
    panel["local_rainfall_min"] = wide.min(axis=1, skipna=True)
    panel["station_count_available"] = wide.notna().sum(axis=1).astype(int)
    return panel


def _rolling_sum(s: pd.Series, window: int, frac: float) -> pd.Series:
    required = max(1, math.ceil(window * frac))
    return s.rolling(window, min_periods=required).sum()


def build_features(panel: pd.DataFrame, rainy_day_threshold: float = RAINY_DAY_THRESHOLD,
                   min_obs_fraction: float = MIN_OBS_FRACTION) -> pd.DataFrame:
    """Features for each calendar day t, using observations <= t only."""
    r = panel["local_rainfall_mean"]
    f = pd.DataFrame(index=panel.index)
    f["rainfall_today"] = r
    for k in (1, 2, 3, 7):
        f[f"rainfall_lag_{k}"] = r.shift(k)
    for w in (3, 7, 14):
        f[f"rolling_rainfall_{w}d"] = _rolling_sum(r, w, min_obs_fraction)
    rainy = (r >= rainy_day_threshold).astype(float).where(r.notna())
    for w in (7, 14):
        f[f"rainy_days_last_{w}d"] = _rolling_sum(rainy, w, min_obs_fraction)
    f["rainfall_change_1d"] = r - r.shift(1)
    f["rainfall_change_3d"] = r - r.shift(3)
    f["local_rainfall_max"] = panel["local_rainfall_max"]
    f["local_rainfall_min"] = panel["local_rainfall_min"]
    f["station_count_available"] = panel["station_count_available"]
    f["rainfall_missing_today"] = r.isna().astype(int)
    f["observed_fraction_7d"] = r.notna().astype(float).rolling(7, min_periods=1).sum() / 7.0
    month = np.asarray(f.index.month)
    f["month_sin"] = np.sin(2 * np.pi * month / 12.0)
    f["month_cos"] = np.cos(2 * np.pi * month / 12.0)
    f["is_ne_monsoon"] = np.isin(month, [10, 11, 12]).astype(int)   # Chennai's main rain season
    f["is_sw_monsoon"] = np.isin(month, [6, 7, 8, 9]).astype(int)
    f.index.name = "date"
    return f[FEATURE_COLUMNS]


def chronological_split(dates, train_frac: float = TRAIN_FRAC,
                        val_frac: float = VAL_FRAC) -> pd.Series:
    """Label sorted unique dates 'train' / 'val' / 'test' in chronological order."""
    idx = pd.DatetimeIndex(dates)
    if not idx.is_monotonic_increasing or idx.has_duplicates:
        raise ValueError("dates must be strictly increasing (chronological, unique)")
    if train_frac <= 0 or val_frac <= 0 or train_frac + val_frac >= 1:
        raise ValueError("need train_frac>0, val_frac>0, train_frac+val_frac<1")
    n = len(idx)
    if n < 10:
        raise ValueError("too few rows to split")
    n_tr, n_va = int(n * train_frac), int(n * val_frac)
    labels = np.array(["test"] * n, dtype=object)
    labels[:n_tr] = "train"
    labels[n_tr:n_tr + n_va] = "val"
    return pd.Series(labels, index=idx, name="split")


def _split_summary(ds: pd.DataFrame) -> dict:
    out = {}
    for sp in ("train", "val", "test"):
        sub = ds[ds["split"] == sp]
        y = sub[TARGET_NAME]
        out[sp] = {
            "n_rows": int(len(sub)),
            "first_feature_date": sub.index.min().date().isoformat() if len(sub) else None,
            "last_feature_date": sub.index.max().date().isoformat() if len(sub) else None,
            "first_target_date": sub["target_date"].min().date().isoformat() if len(sub) else None,
            "last_target_date": sub["target_date"].max().date().isoformat() if len(sub) else None,
            "n_positive": int(y.sum()) if len(sub) else 0,
            "positive_rate": float(y.mean()) if len(sub) else None,
        }
    return out


def prepare_dataset(panel: pd.DataFrame, horizon: int = HORIZON_DAYS,
                    percentile: float = STRESS_PERCENTILE,
                    train_frac: float = TRAIN_FRAC, val_frac: float = VAL_FRAC,
                    rainy_day_threshold: float = RAINY_DAY_THRESHOLD,
                    min_obs_fraction: float = MIN_OBS_FRACTION):
    """Build features + target + split. Returns (dataset, info).

    Eligible row: rainfall observed on day t AND stress quantity observed on day t+horizon.
    Split is chronological over eligible rows (by target date, same order as feature date).
    Threshold = `percentile` of local_rainfall_mean over observed days <= last TRAIN target date,
    expressed in source rainfall units (physical unit not independently verified).
    """
    r = panel["local_rainfall_mean"]
    ds = build_features(panel, rainy_day_threshold, min_obs_fraction).copy()
    ds["target_date"] = ds.index + pd.Timedelta(days=horizon)
    ds["stress_quantity_next"] = r.shift(-horizon)
    ds["eligible"] = ds["stress_quantity_next"].notna() & ds["rainfall_today"].notna()

    el = ds[ds["eligible"]]
    labels = chronological_split(el.index, train_frac, val_frac)
    ds["split"] = ""
    ds.loc[el.index, "split"] = labels.values

    train_end_target = ds.loc[ds["split"] == "train", "target_date"].max()
    train_period = r[r.index <= train_end_target].dropna()
    threshold = float(train_period.quantile(percentile))
    if threshold <= 0:
        raise ValueError("Stress threshold <= 0; the target would be degenerate. "
                         "Check the data or raise the percentile.")
    ds[TARGET_NAME] = np.where(ds["eligible"], (ds["stress_quantity_next"] >= threshold).astype(float),
                               np.nan)
    info = {
        "stress_quantity": "local_rainfall_mean (mean over stations observed that day, "
                           "in source rainfall units)",
        "percentile": percentile,
        "stress_threshold_source_units": threshold,
        "threshold_unit_note": UNIT_NOTE,
        "threshold_fitted_on_days_up_to": train_end_target.date().isoformat(),
        "n_threshold_days": int(len(train_period)),
        "horizon_days": horizon,
        "splits": _split_summary(ds),
        "n_calendar_days": int(len(ds)), "n_eligible_rows": int(ds["eligible"].sum()),
    }
    return ds, info