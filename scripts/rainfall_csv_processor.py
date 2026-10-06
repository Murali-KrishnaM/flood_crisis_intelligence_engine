"""Audit of the independent Chennai daily-rainfall CSV (NOT merged with RTFF).

Rules
  * raw Date string is preserved exactly (kept as `date_raw`)
  * dates parsed with explicit day-first semantics (DD-MM-YYYY)
  * duplicates are classified and flagged, NEVER removed; no row is dropped
  * rainfall unit is UNVERIFIED: no unit is implied in any column or note
  * no interpolation, no gap filling, no geographic claims
Outputs
  Datasets/processed/rainfall_csv_source_rows.csv   (one row per source row)
  Datasets/metadata/rainfall_csv_quality.csv        (section,metric,value,note)
  Datasets/metadata/rainfall_csv_station_coverage.csv
  Datasets/metadata/rainfall_csv_yearly.csv
  Datasets/metadata/rainfall_csv_station_year.csv
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from pipeline_common import (COMMON_WINDOW, classify_duplicates, gap_stats,
                             project_root, rel_posix, to_float, write_csv)

RAW_COLUMNS = ["District", "Station", "Rainfall", "Date"]
DDMMYYYY = re.compile(r"^\d{2}-\d{2}-\d{4}$")

# Values the USER reported from an independent pandas audit. They are ONLY used
# to emit pass/fail "reproduction_check" rows; results are always recomputed.
USER_REPORTED = {
    "total_rows": 21416,
    "parsed_rows": 21416,
    "date_parse_failures": 0,
    "unique_stations_raw": 62,
    "true_duplicate_station_date_rows": 1447,
    "first_date": "1993-01-08",
    "last_date": "2023-12-12",
}

# Free-text search tokens (lower-case substrings of the STATION NAME). They are
# NOT geographic facts: a match only means "name contains this text". Edit freely.
STUDY_AREA_NAME_HINTS = (
    "velachery", "taramani", "pallikaranai", "perungudi", "adyar",
    "sholinganallur", "anna univ", "zone 13", "zone 14", "zone 15",
    "meenambakkam", "alandur", "alandhur",
)
HINT_BASIS = "station_name_text_match_only; no coordinates; geography unverified"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_rainfall_csv(path: Path):
    """Read every field as a raw string (no NA conversion). Returns (df, encoding)."""
    last = None
    for enc in ("utf-8-sig", "latin-1"):
        try:
            df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding=enc)
            return df, enc
        except UnicodeDecodeError as exc:
            last = exc
    raise last


def name_hint_matches(name, hints):
    n = str(name).casefold()
    found = [h for h in hints if h in n]
    return "|".join(found) if found else None


def _norm_name(name):
    return re.sub(r"\s+", " ", str(name).strip().casefold())


def audit_rainfall(df_raw, source_rel, provenance=None, hints=STUDY_AREA_NAME_HINTS,
                   window=COMMON_WINDOW, reference=USER_REPORTED):
    missing = [c for c in RAW_COLUMNS if c not in df_raw.columns]
    if missing:
        raise ValueError(f"rainfall CSV missing expected columns {missing}; "
                         f"found {list(df_raw.columns)}")
    df = df_raw.reset_index(drop=True)
    stripped = df["Date"].str.strip()
    parsed = pd.to_datetime(stripped, errors="coerce", dayfirst=True)

    conv = [to_float(v) for v in df["Rainfall"]]
    vals = pd.Series([c[0] for c in conv], dtype="float64")
    stats = [c[1] for c in conv]

    rows = pd.DataFrame({
        "source_file": source_rel,
        "source_data_row": df.index + 1,       # 1-based data row, header excluded
        "district": df["District"],
        "station": df["Station"],
        "rainfall_raw": df["Rainfall"],        # exact source text
        "rainfall_value": vals,                # numeric, NaN if null/non-numeric
        "rainfall_status": stats,
        "date_raw": df["Date"],                # exact source text
        "parsed_date": parsed.dt.strftime("%Y-%m-%d"),
    })
    rows["date_status"] = parsed.notna().map({True: "parsed", False: "unparseable"})
    rows["date_format_conformant_ddmmyyyy"] = stripped.str.match(DDMMYYYY).fillna(False)
    rows["record_signature"] = [
        f"{d}|" + (repr(float(v)) if s == "ok" else f"RAW:{str(r).strip()}")
        for d, v, s, r in zip(rows["district"], rows["rainfall_value"],
                              rows["rainfall_status"], rows["rainfall_raw"])]

    rows = classify_duplicates(rows, ["station", "parsed_date"], "record_signature")
    rows["exact_duplicate_later_copy"] = ~rows["keep"]   # flag only; nothing dropped
    rows = rows.drop(columns=["keep"])

    valid = rows[rows["parsed_date"].notna()].copy()
    total, n_parsed = len(rows), len(valid)
    n_fail = total - n_parsed
    all_dates = [date.fromisoformat(d) for d in valid["parsed_date"]]
    overall = gap_stats(all_dates)

    # ---- duplicate structure (parsed-date rows only) -----------------------
    true_dup = int(valid.duplicated(["station", "parsed_date"]).sum())
    keys = (valid.groupby(["station", "parsed_date"])
            .agg(n=("record_signature", "size"), u=("record_signature", "nunique"),
                 rmax=("rainfall_value", "max"), rmin=("rainfall_value", "min"))
            .reset_index())
    dup = keys[keys["n"] > 1]
    exact, conf = dup[dup["u"] == 1], dup[dup["u"] > 1]
    spread = (conf["rmax"] - conf["rmin"]).dropna()
    full_row_dups = int(df_raw.duplicated(RAW_COLUMNS).sum())

    # ---- station coverage ---------------------------------------------------
    ov = None
    if window and overall["first"] and overall["last"]:
        s, e = max(window[0], overall["first"]), min(window[1], overall["last"])
        if s <= e:
            ov = (s, e)
    srows = []
    for st, r in rows.groupby("station", sort=True):
        v = r[r["parsed_date"].notna()]
        ds = [date.fromisoformat(d) for d in v["parsed_date"]]
        gs = gap_stats(ds)
        ks = keys[keys["station"] == st]
        rec = dict(
            station=st, first_date=gs["first"], last_date=gs["last"],
            row_count=len(r), unique_dates=gs["observed_days"],
            span_days=gs["span_days"], missing_calendar_days=gs["missing_days"],
            largest_missing_gap_days=gs["largest_gap"],
            duplicate_station_date_count=int((ks["n"] - 1).sum()),  # extra rows
            duplicate_station_date_keys=int((ks["n"] > 1).sum()),
            conflicting_station_date_keys=int((ks["u"] > 1).sum()),
            null_rainfall_rows=int((r["rainfall_status"] == "null").sum()),
            non_numeric_rainfall_rows=int((r["rainfall_status"] == "non_numeric").sum()),
            unparseable_date_rows=int((r["date_status"] == "unparseable").sum()),
            rainfall_min=r["rainfall_value"].min(), rainfall_max=r["rainfall_value"].max(),
            station_name_normalized=_norm_name(st),
            study_area_name_hint_match=name_hint_matches(st, hints),
            hint_basis=HINT_BASIS,
            overlap_window_start=None, overlap_window_end=None,
            overlap_window_observed_days=None, overlap_window_missing_days=None)
        if ov:
            w = gap_stats(ds, *ov)
            rec.update(overlap_window_start=ov[0].isoformat(),
                       overlap_window_end=ov[1].isoformat(),
                       overlap_window_observed_days=w["observed_days"],
                       overlap_window_missing_days=w["missing_days"])
        srows.append(rec)
    stations = pd.DataFrame(srows)

    # ---- yearly -------------------------------------------------------------
    if keys.empty:
        yearly = pd.DataFrame(columns=["year"])
        station_year = pd.DataFrame(columns=["station", "year", "row_count", "unique_dates"])
    else:
        valid["year"] = valid["parsed_date"].str[:4]
        station_year = (valid.groupby(["station", "year"])
                        .agg(row_count=("parsed_date", "size"),
                             unique_dates=("parsed_date", "nunique")).reset_index())
        keys["year"] = keys["parsed_date"].str[:4]
        y = (keys.groupby("year")
             .agg(row_count=("n", "sum"), unique_station_dates=("n", "size"),
                  station_count=("station", "nunique")).reset_index())
        y["duplicate_extra_rows"] = y["row_count"] - y["unique_station_dates"]
        y["conflicting_keys"] = y["year"].map(
            keys[keys["u"] > 1].groupby("year").size()).fillna(0).astype(int)
        sy = (station_year.groupby("year")["unique_dates"]
              .agg(unique_dates_per_station_min="min",
                   unique_dates_per_station_median="median",
                   unique_dates_per_station_max="max").reset_index())
        yearly = y.merge(sy, on="year")

    # ---- quality (long format) ---------------------------------------------
    q = []

    def add(section, metric, value, note=""):
        q.append(dict(section=section, metric=metric,
                      value="" if value is None else str(value), note=note))

    prov = provenance or {}
    add("provenance", "source_file", source_rel)
    for k in ("source_size_bytes", "source_sha256", "encoding_used"):
        add("provenance", k, prov.get(k))
    add("provenance", "audit_utc", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    add("provenance", "columns_found", json.dumps(list(map(str, df_raw.columns))))
    add("provenance", "unexpected_columns",
        json.dumps([c for c in df_raw.columns if c not in RAW_COLUMNS]))
    add("provenance", "raw_file_modified", False,
        "audit only reads the raw file; compare source_sha256 over time")

    add("dates", "total_rows", total)
    add("dates", "parsed_rows", n_parsed, "pd.to_datetime(errors='coerce', dayfirst=True)")
    add("dates", "unparseable_rows", n_fail)
    add("dates", "date_parse_failures", n_fail)
    add("dates", "rows_matching_strict_ddmmyyyy_pattern",
        int(rows["date_format_conformant_ddmmyyyy"].sum()),
        "independent regex cross-check; ^\\d{2}-\\d{2}-\\d{4}$")
    add("dates", "first_date", overall["first"], "from parsed rows only")
    add("dates", "last_date", overall["last"], "from parsed rows only")

    add("stations", "unique_stations_raw", rows["station"].nunique(), "exact raw strings")
    add("stations", "unique_stations_normalized",
        rows["station"].map(_norm_name).nunique(),
        "strip + casefold + collapse spaces; fewer than raw => name variants exist")
    add("stations", "district_values", json.dumps(sorted(rows["district"].unique())))

    add("rainfall", "null_rainfall_rows", int((rows["rainfall_status"] == "null").sum()),
        "kept as null; never zero")
    add("rainfall", "non_numeric_rainfall_rows",
        int((rows["rainfall_status"] == "non_numeric").sum()))
    add("rainfall", "source_value_min", rows["rainfall_value"].min(), "UNIT UNVERIFIED")
    add("rainfall", "source_value_max", rows["rainfall_value"].max(), "UNIT UNVERIFIED")

    note_dup = "parsed-date rows only; key = raw Station string + parsed date"
    add("duplicates", "duplicate_full_rows", full_row_dups,
        "all 4 raw columns identical to an earlier row")
    add("duplicates", "duplicate_station_date_keys", len(dup),
        "distinct Station+Date keys that occur more than once")
    add("duplicates", "rows_in_duplicated_keys", int(dup["n"].sum()), note_dup)
    add("duplicates", "true_duplicate_station_date_rows", true_dup,
        "extra rows beyond the first per key (pandas duplicated, keep='first')")
    add("duplicates", "exact_duplicate_keys", len(exact),
        "all versions have identical District+Rainfall")
    add("duplicates", "exact_duplicate_extra_rows", int((exact["n"] - 1).sum()))
    add("duplicates", "conflicting_keys", len(conf),
        "versions differ in rainfall (or district); ALL versions retained")
    add("duplicates", "rows_in_conflicting_keys", int(conf["n"].sum()))
    add("duplicates", "conflicting_extra_rows", int((conf["n"] - 1).sum()))
    add("duplicates", "conflict_max_abs_rainfall_difference",
        spread.max() if len(spread) else None, "UNIT UNVERIFIED")
    add("duplicates", "conflict_median_abs_rainfall_difference",
        spread.median() if len(spread) else None, "UNIT UNVERIFIED")
    add("duplicates", "rows_dropped_from_processed_file", 0, "nothing is dropped")

    computed = dict(total_rows=total, parsed_rows=n_parsed, date_parse_failures=n_fail,
                    unique_stations_raw=rows["station"].nunique(),
                    true_duplicate_station_date_rows=true_dup,
                    first_date=overall["first"], last_date=overall["last"])
    for k, ref in (reference or {}).items():
        add("reproduction_check", f"{k}_reproduces_user_reported",
            str(computed.get(k)) == str(ref), f"computed={computed.get(k)} reference={ref}")

    return dict(rows=rows, quality=pd.DataFrame(q), stations=stations,
                yearly=yearly, station_year=station_year)


def run_rainfall(root=None, csv_path=None, verbose=True):
    root = Path(root) if root else project_root()
    if csv_path:
        path = Path(csv_path)
        path = path if path.is_absolute() else root / path
    else:
        raw_dir = root / "Datasets" / "raw" / "rainfall"
        files = sorted(raw_dir.rglob("*.csv")) if raw_dir.is_dir() else []
        if not files:
            raise FileNotFoundError(f"no rainfall CSV under {raw_dir}")
        if len(files) > 1:
            raise ValueError("multiple rainfall CSVs found; choose one with "
                             f"--rainfall-file: {[rel_posix(f, root) for f in files]}")
        path = files[0]
    df, enc = read_rainfall_csv(path)
    prov = dict(source_size_bytes=path.stat().st_size,
                source_sha256=sha256_file(path), encoding_used=enc)
    res = audit_rainfall(df, rel_posix(path, root), prov)
    proc, meta = root / "Datasets" / "processed", root / "Datasets" / "metadata"
    write_csv(res["rows"], proc / "rainfall_csv_source_rows.csv")
    write_csv(res["quality"], meta / "rainfall_csv_quality.csv")
    write_csv(res["stations"], meta / "rainfall_csv_station_coverage.csv")
    write_csv(res["yearly"], meta / "rainfall_csv_yearly.csv")
    write_csv(res["station_year"], meta / "rainfall_csv_station_year.csv")
    if verbose:
        q = res["quality"]
        print(f"[RAINFALL] {rel_posix(path, root)} (encoding {enc})")
        show = q[q["section"].isin(["dates", "stations", "duplicates", "reproduction_check"])]
        print(show[["section", "metric", "value"]].to_string(index=False))
        bad = q[(q["section"] == "reproduction_check") & (q["value"] != "True")]
        if len(bad):
            print("\n[RAINFALL] !! NOT reproduced (investigate, do not patch silently):")
            print(bad[["metric", "note"]].to_string(index=False))
        st = res["stations"]
        hit = st[st["study_area_name_hint_match"].notna()]
        print(f"\n[RAINFALL] stations={len(st)}; name-hint matches={len(hit)} "
              "(text match only, geography UNVERIFIED)")
        if len(hit):
            cols = ["station", "first_date", "last_date", "unique_dates",
                    "missing_calendar_days", "overlap_window_observed_days",
                    "study_area_name_hint_match"]
            print(hit[cols].to_string(index=False))
    return res


if __name__ == "__main__":
    run_rainfall()