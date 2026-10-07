#!/usr/bin/env python
"""Phase 2 rainfall data loader.

Loads the already-processed RTFF daily station records and prepares a
simple long-format dataframe for the risk model.

Important project rule: RTFF rainfall physical units are not yet verified,
so internal fields use the neutral name ``rainfall_value``.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

# The four stations deliberately selected for the current Ward 177 prototype.
# This is a project-level eligibility list, not a claim that these are the
# only stations available in RTFF.
PROJECT_STATIONS = (
    "Anna_University",
    "CHN_TARAMANI",
    "CHN13Z178WG",
    "ARG_NIOT_Pallikaranai",
)

META_STATION_IDS = {
    "_meta",
    "meta",
    "metadata",
}

DATE_CANDIDATES = (
    "parsed_date",
    "source_date",
    "date",
    "Date",
    "observation_date",
)

STATION_CANDIDATES = (
    "station_id",
    "station",
    "argid",
    "source_argid",
    "Station",
)

RAIN_CANDIDATES = (
    "source_daily_rainfall_raw",
    "daily_rainfall",
    "dailyrainfall",
    "rainfall_value",
    "rainfall",
    "Rainfall",
)


def json_default(obj: Any) -> Any:
    """JSON serializer for common numpy/pandas/path/date values."""
    if isinstance(obj, (pd.Timestamp, dt.datetime, dt.date)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if pd.isna(obj):
        return None
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _pick(columns: Iterable[str], candidates: Iterable[str], explicit: str | None, label: str) -> str:
    cols = list(columns)
    if explicit:
        if explicit not in cols:
            raise KeyError(f"Requested {label} column '{explicit}' not found. Columns found: {cols}")
        return explicit
    lowered = {str(c).strip().lower(): c for c in cols}
    for candidate in candidates:
        if candidate in cols:
            return candidate
        hit = lowered.get(candidate.lower())
        if hit is not None:
            return hit
    raise KeyError(
        f'Could not auto-detect the {label} column. Columns found: {cols}. '
        f'Pass --{label.replace("_", "-")}-col <name>.'
    )


def _read_csv(source: Path) -> pd.DataFrame:
    """Read CSV while preserving source strings as much as practical."""
    if not source.exists():
        raise FileNotFoundError(f"Source rainfall file does not exist: {source}")
    return pd.read_csv(source, low_memory=False)


def _is_metadata_station(series: pd.Series) -> pd.Series:
    normalized = series.astype("string").str.strip().str.lower()
    return normalized.isin(META_STATION_IDS)


def _parse_dates(series: pd.Series) -> pd.Series:
    """Parse dates: ISO (YYYY-MM-DD...) strictly first, then DD-MM-YYYY day-first.

    A blanket dayfirst=True can swap day/month on ISO strings
    (e.g. 2024-01-02 -> 2 Feb), so ISO values must never go through it.
    """
    text = series.astype("string").str.strip()
    parsed = pd.to_datetime(text, format="ISO8601", errors="coerce")
    leftover = parsed.isna() & text.notna()
    if leftover.any():
        parsed.loc[leftover] = pd.to_datetime(text[leftover], errors="coerce", dayfirst=True)
    return parsed


def _expected_station_info(stations_found: list[str]) -> tuple[list[str], list[str]]:
    found = [str(x) for x in stations_found]
    found_set = set(found)
    missing = [s for s in PROJECT_STATIONS if s not in found_set]
    return found, missing


def _conflict_table(df: pd.DataFrame) -> pd.DataFrame:
    """Return station/date groups containing >1 distinct rainfall value."""
    if df.empty:
        return pd.DataFrame(columns=["station", "date", "rainfall_value"])
    work = df.dropna(subset=["date", "station"]).copy()
    work["rainfall_value"] = pd.to_numeric(work["rainfall_value"], errors="coerce")
    grouped = work.groupby(["station", "date"], dropna=False)["rainfall_value"].nunique(dropna=True)
    bad_keys = grouped[grouped > 1].index
    if len(bad_keys) == 0:
        return pd.DataFrame(columns=["station", "date", "rainfall_value"])
    idx = pd.MultiIndex.from_frame(work[["station", "date"]])
    return work.loc[idx.isin(bad_keys), ["station", "date", "rainfall_value"]].sort_values(
        ["station", "date"]
    )


def inspect_source(
    source: Path,
    date_col: str | None = None,
    station_col: str | None = None,
    rain_col: str | None = None,
) -> dict[str, Any]:
    """Inspect the source schema without silently filtering bad data."""
    source = Path(source)
    df = _read_csv(source)
    dcol = _pick(df.columns, DATE_CANDIDATES, date_col, "date")
    scol = _pick(df.columns, STATION_CANDIDATES, station_col, "station")
    rcol = _pick(df.columns, RAIN_CANDIDATES, rain_col, "rain")

    meta_mask = _is_metadata_station(df[scol])
    obs = df.loc[~meta_mask].copy()
    dates = _parse_dates(obs[dcol])

    station_values = sorted(str(x) for x in obs[scol].dropna().unique())
    found, missing = _expected_station_info(station_values)

    return {
        "path": str(source.resolve()),
        "n_rows": int(len(df)),
        "n_columns": int(len(df.columns)),
        "columns": [str(c) for c in df.columns],
        "date_col": dcol,
        "station_col": scol,
        "rain_col": rcol,
        "metadata_rows_excluded": int(meta_mask.sum()),
        "n_observation_rows": int(len(obs)),
        "n_distinct_station_values": int(obs[scol].nunique(dropna=True)),
        "station_values": station_values,
        "project_stations_found": found,
        "project_stations_missing": missing,
        "date_unparseable_count": int(dates.isna().sum()),
        "date_range": [
            dates.min().date().isoformat() if dates.notna().any() else None,
            dates.max().date().isoformat() if dates.notna().any() else None,
        ],
        "rainfall_unit_note": "source rainfall measurement units; physical unit not yet independently verified",
    }


def _parse_observations(
    df: pd.DataFrame,
    dcol: str,
    scol: str,
    rcol: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Filter metadata, parse observations, normalize column names, validate conflicts."""
    original_rows = len(df)

    # Never let _meta records contaminate model input. They are metadata
    # emitted by the Phase-1 audit/export step, not rainfall observations.
    meta_mask = _is_metadata_station(df[scol])
    metadata_rows = int(meta_mask.sum())
    obs = df.loc[~meta_mask].copy()

    # Blank station identifiers are also not valid observations.
    blank_station_mask = obs[scol].isna() | obs[scol].astype("string").str.strip().eq("")
    blank_station_rows = int(blank_station_mask.sum())
    obs = obs.loc[~blank_station_mask].copy()

    # ISO dates are parsed strictly; only non-ISO strings (the verified
    # DD-MM-YYYY source format) are parsed day-first.
    dates = _parse_dates(obs[dcol])
    bad_dates = dates.isna()
    bad_date_count = int(bad_dates.sum())
    if bad_date_count:
        examples = obs.loc[bad_dates, dcol].astype("string").drop_duplicates().head(10).tolist()
        raise ValueError(
            f"{bad_date_count} unparseable observation dates in column '{dcol}' after metadata exclusion. "
            f"Examples: {examples}"
        )
    obs["date"] = dates.dt.normalize()

    # Numeric conversion is deliberate. Invalid numeric strings become NaN,
    # but no rainfall observation is silently converted into zero.
    rain = pd.to_numeric(obs[rcol], errors="coerce")
    negative_mask = rain.notna() & (rain < 0)
    negative_count = int(negative_mask.sum())
    if negative_count:
        rain.loc[negative_mask] = np.nan
    obs["rainfall_value"] = rain.astype(float)
    obs["station"] = obs[scol].astype("string").str.strip()

    # Detect ambiguous same-station/day observations BEFORE de-duplicating.
    conflicts = _conflict_table(obs[["station", "date", "rainfall_value"]])
    conflict_keys = int(conflicts.drop_duplicates(["station", "date"]).shape[0])
    if conflict_keys:
        sample = conflicts.head(20).to_dict(orient="records")
        raise ValueError(
            f"Found {conflict_keys} conflicting station/date keys in model source. "
            f"The risk model refuses to choose a value silently. Sample: {sample}"
        )

    # Exact duplicates are harmless source duplication. They can be removed
    # from model input while the untouched raw/canonical-source files remain
    # unchanged.
    before_dedupe = len(obs)
    obs = obs.drop_duplicates(subset=["station", "date", "rainfall_value"], keep="first")
    exact_duplicates_removed = int(before_dedupe - len(obs))

    # Limit model input to the four project-selected stations. This prevents
    # future metadata/new-station additions from silently changing the model.
    eligible_mask = obs["station"].isin(PROJECT_STATIONS)
    non_project_station_rows = int((~eligible_mask).sum())
    obs = obs.loc[eligible_mask].copy()

    obs = obs.sort_values(["date", "station"]).reset_index(drop=True)

    station_values = sorted(str(x) for x in obs["station"].unique())
    found, missing = _expected_station_info(station_values)
    observed_days_per_station = {
        s: int(obs.loc[obs["station"] == s, "date"].nunique()) for s in found
    }

    info = {
        "input_rows": int(original_rows),
        # Interface keys required by train_risk_model.py. Both reuse values
        # already computed above; nothing is recalculated.
        "rows_read": int(original_rows),
        "rows_matched_to_project_stations": int(len(obs)),
        "metadata_rows_excluded": metadata_rows,
        "blank_station_rows_excluded": blank_station_rows,
        "bad_date_count": bad_date_count,
        "negative_values_set_to_nan": negative_count,
        "exact_duplicate_rows_removed_for_model": exact_duplicates_removed,
        "conflicting_station_date_keys": conflict_keys,
        "non_project_station_rows_excluded": non_project_station_rows,
        "output_rows": int(len(obs)),
        "stations_found": found,
        "stations_missing": missing,
        "observed_days_per_station": observed_days_per_station,
        "used_hourly_sum": False,
        "date_col": dcol,
        "station_col": scol,
        "rain_col": rcol,
        "rainfall_unit_note": "source rainfall measurement units; physical unit not yet independently verified",
    }
    return obs[["date", "station", "rainfall_value"]], info


def load_daily_station_rainfall(
    source: Path,
    date_col: str | None = None,
    station_col: str | None = None,
    rain_col: str | None = None,
    allow_sum_hourly: bool = False,
    start: str | dt.date | pd.Timestamp | None = None,
    end: str | dt.date | pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load daily station rainfall into [date, station, rainfall_value].

    ``start`` and ``end`` are optional inclusive calendar-date filters kept
    for compatibility with the Phase-2 tests and useful for targeted
    experiments. Filtering happens *after* source validation so malformed or
    conflicting records are not silently hidden by a date window.
    """
    del allow_sum_hourly  # kept for API compatibility; hourly semantics are unverified.

    source = Path(source)
    df = _read_csv(source)
    dcol = _pick(df.columns, DATE_CANDIDATES, date_col, "date")
    scol = _pick(df.columns, STATION_CANDIDATES, station_col, "station")
    rcol = _pick(df.columns, RAIN_CANDIDATES, rain_col, "rain")

    long_df, info = _parse_observations(df, dcol, scol, rcol)

    # Optional inclusive date filtering. Keep the raw source untouched; only
    # the in-memory model view is filtered.
    if start is not None or end is not None:
        start_ts = pd.Timestamp(start).normalize() if start is not None else None
        end_ts = pd.Timestamp(end).normalize() if end is not None else None
        if start_ts is not None and end_ts is not None and start_ts > end_ts:
            raise ValueError(f"start date {start_ts.date()} is after end date {end_ts.date()}")
        mask = pd.Series(True, index=long_df.index)
        if start_ts is not None:
            mask &= long_df["date"] >= start_ts
        if end_ts is not None:
            mask &= long_df["date"] <= end_ts
        long_df = long_df.loc[mask].copy()
        info["filter_start"] = start_ts.date().isoformat() if start_ts is not None else None
        info["filter_end"] = end_ts.date().isoformat() if end_ts is not None else None
        info["filtered_output_rows"] = int(len(long_df))
        info["observed_days_per_station_after_filter"] = {
            s: int(long_df.loc[long_df["station"] == s, "date"].nunique())
            for s in sorted(str(x) for x in long_df["station"].unique())
        }

    info["source_file"] = str(source)
    return long_df, info