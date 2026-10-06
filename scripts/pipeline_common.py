"""Shared helpers for the data audit / canonicalization pipeline.

Design rules:
  * never impute, interpolate, or zero-fill
  * never invent units
  * every derived row keeps a pointer back to its source file
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

# Common acquisition window reported by the RTFF scraper (used ONLY to compute
# additional "window_*" coverage columns; it never filters or alters data).
COMMON_WINDOW = (date(2022, 4, 1), date(2025, 5, 10))


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def rel_posix(path: Path, root: Path) -> str:
    """Project-root-relative POSIX path (falls back to the given path)."""
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


# ----------------------------------------------------------------- JSON ----
def decode_json_text(text: str, max_layers: int = 3):
    """Decode JSON text; if the result is itself a JSON-encoded string,
    decode again (RTFF reservoir files are double-encoded).
    Returns (object, extra_decoding_layers)."""
    obj = json.loads(text)
    layers = 0
    while isinstance(obj, str) and layers < max_layers:
        s = obj.strip()
        if not s or s[0] not in '[{"':
            break
        obj = json.loads(s)
        layers += 1
    return obj, layers


def load_json_file(path: Path):
    text = Path(path).read_text(encoding="utf-8-sig")
    return decode_json_text(text)


# ---------------------------------------------------------------- dates ----
def parse_ddmmyyyy(s):
    try:
        return datetime.strptime(str(s).strip(), "%d-%m-%Y").date()
    except (ValueError, TypeError):
        return None


def parse_iso_date(s):
    try:
        return datetime.strptime(str(s).strip()[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _mk(y, m, d):
    try:
        return date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return None


_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_DMY = re.compile(r"(?<!\d)(\d{2})-(\d{2})-(\d{4})(?!\d)")
_COMPACT = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")


def dates_from_filename(name: str):
    """Return (start_iso, end_iso) if >=2 dates are parseable from the file
    name, else None. Purely a provenance hint; the caller records the origin."""
    for rx, order in ((_ISO, "ymd"), (_DMY, "dmy"), (_COMPACT, "ymd")):
        found = []
        for m in rx.finditer(name):
            a, b, c = m.groups()
            iso = _mk(a, b, c) if order == "ymd" else _mk(c, b, a)
            if iso:
                found.append(iso)
        if len(found) >= 2:
            return min(found), max(found)
    return None


def gap_stats(dates, start=None, end=None):
    """Missing-calendar-day statistics. Missing days are REPORTED, never filled.
    If start/end are given, only that window is evaluated."""
    ds = sorted(set(dates))
    if start is None:
        if not ds:
            return dict(first=None, last=None, span_days=0, observed_days=0,
                        missing_days=0, largest_gap=0)
        start = ds[0]
    if end is None:
        end = ds[-1] if ds else start
    present = {d for d in ds if start <= d <= end}
    span = max((end - start).days + 1, 0)
    missing = largest = run = 0
    for i in range(span):
        if (start + timedelta(days=i)) in present:
            run = 0
        else:
            missing += 1
            run += 1
            largest = max(largest, run)
    obs = sorted(present)
    return dict(first=obs[0] if obs else None, last=obs[-1] if obs else None,
                span_days=span, observed_days=len(present),
                missing_days=missing, largest_gap=largest)


# -------------------------------------------------------------- numerics ----
def to_float(v):
    """Returns (value|None, status) with status in
    {'ok','null','non_numeric'}. Null is NEVER converted to zero."""
    if v is None:
        return None, "null"
    if isinstance(v, bool):
        return None, "non_numeric"
    if isinstance(v, (int, float)):
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None, "non_numeric"
        return f, "ok"
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return None, "null"
        try:
            f = float(s)
        except ValueError:
            return None, "non_numeric"
        if math.isnan(f) or math.isinf(f):
            return None, "non_numeric"
        return f, "ok"
    return None, "non_numeric"


def signature(obj) -> str:
    raw = json.dumps(obj, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


# ------------------------------------------------------------ duplicates ----
def classify_duplicates(df: pd.DataFrame, key_cols, sig_col) -> pd.DataFrame:
    """Adds `record_status` and `keep`.

    unique                       key appears once
    exact_duplicate_first_kept   key repeated, identical content; later copies
                                 get keep=False (still present in source CSV)
    conflicting_source_versions  key repeated with DIFFERENT content; ALL
                                 versions kept, nothing is chosen for you
    missing_key_kept             key has a null part; cannot be de-duplicated
    """
    df = df.reset_index(drop=True).copy()
    df["record_status"] = "unique"
    df["keep"] = True
    if df.empty:
        return df
    valid = df[list(key_cols)].notna().all(axis=1)
    df.loc[~valid, "record_status"] = "missing_key_kept"
    sub = df[valid]
    if sub.empty:
        return df
    g = sub.groupby(list(key_cols))[sig_col]
    size = g.transform("size")
    nuniq = g.transform("nunique")
    conf_idx = sub.index[(size > 1) & (nuniq > 1)]
    exact_idx = sub.index[(size > 1) & (nuniq == 1)]
    df.loc[conf_idx, "record_status"] = "conflicting_source_versions"
    df.loc[exact_idx, "record_status"] = "exact_duplicate_first_kept"
    later = df.loc[exact_idx].duplicated(subset=list(key_cols), keep="first")
    df.loc[later[later].index, "keep"] = False
    return df


def write_csv(df: pd.DataFrame, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path