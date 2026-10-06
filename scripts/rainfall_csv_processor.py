"""Audit of the independent Chennai daily-rainfall CSV (NOT merged with RTFF).

Rules
  * raw Date string is preserved exactly (kept as `date_raw`)
  * dates parsed with explicit day-first semantics (DD-MM-YYYY)
  * duplicates are classified and flagged, NEVER removed; no row is dropped
  * conflicting Station+Date versions are ALL retained; no value is chosen
  * rainfall unit is UNVERIFIED: no unit is implied in any column or note
  * no interpolation, no gap filling, no geographic claims
Outputs
  Datasets/processed/rainfall_csv_source_rows.csv   (one row per source row)
  Datasets/metadata/rainfall_csv_quality.csv        (section,metric,value,note)
  Datasets/metadata/rainfall_csv_duplicate_report.csv
  Datasets/metadata/rainfall_csv_conflicts.csv      (one row per conflicting key)
  Datasets/metadata/rainfall_csv_station_coverage.csv
  Datasets/metadata/rainfall_csv_yearly.csv
  Datasets/metadata/rainfall_csv_station_year.csv

Type note: in-memory tables hold datetime.date objects for first_date/last_date
etc.; CSV files contain their ISO strings (YYYY-MM-DD). parsed_date in the
source-rows table is an ISO string.
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
NUMERIC_TOLERANCE = 0.05   # figures reported to 1 decimal place

# Independent-style reference values (user's pandas audit of the raw CSV).
USER_REPORTED = {
    "total_rows": 21416,
    "parsed_rows": 21416,
    "date_parse_failures": 0,
    "unique_stations_raw": 62,
    "true_duplicate_station_date_rows": 1447,
}

# REGRESSION PINS: values observed when the pipeline was run on the real file
# (reported by the user). They are produced BY this pipeline, so matching them
# only shows the result is stable; it is NOT independent verification.
# The earlier ad-hoc dates 1993-01-08 / 2023-12-12 were retracted and removed.
PIPELINE_OBSERVED_PINS = {
    "first_date": "1993-03-03",
    "last_date": "2023-12-26",
    "duplicate_full_rows": 1361,
    "duplicate_station_date_keys": 1397,
    "exact_duplicate_keys": 1311,
    "exact_duplicate_extra_rows": 1358,
    "conflicting_keys": 86,
    "rows_in_conflicting_keys": 175,
    "conflicting_extra_rows": 89,
    "conflict_max_abs_rainfall_difference": 202.1,
    "conflict_median_abs_rainfall_difference": 5.5,
}

# Free-text search tokens (lower-case substrings of the STATION NAME). They are
# NOT geographic facts: a match only means "name contains this text". Edit freely.
STUDY_AREA_NAME_HINTS = (
    "velachery", "taramani", "pallikaranai", "perungudi", "adyar",
    "sholinganallur", "anna univ", "zone 13", "zone 14", "zone 15",
    "meenambakkam", "alandur", "alandhur",
)
HINT_BASIS = "station_name_text_match_only; no coordinates; geography unverified"

CONFLICT_COLUMNS = [
    "station", "parsed_date", "version_count", "distinct_versions",
    "rainfall_raw_versions", "source_data_rows", "rainfall_min",
    "rainfall_max", "abs_rainfall_difference", "resolution",
]
DUP_REPORT_COLUMNS = ["category", "key_count", "rows_in_keys", "extra_rows",
                      "definition"]


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


def _matches(computed, ref):
    """Numeric reference -> tolerance compare; otherwise exact string compare."""
    if computed is None:
        return False
    if isinstance(ref, (int, float)) and not isinstance(ref, bool):
        try:
            return abs(float(computed) - float(ref)) <= NUMERIC_TOLERANCE
        except (TypeError, ValueError):
            return False
    return str(computed) == str(ref)


def audit_rainfall(df_raw, source_rel, provenance=None, hints=STUDY_AREA_NAME_HINTS,
                   window=COMMON_WINDOW, reference=USER_REPORTED,
                   pins=PIPELINE_OBSERVED_PINS):
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
    # byte-level full-row duplicate flag (all 4 raw columns identical to an earlier row)
    rows["full_row_duplicate_later_copy"] = df.duplicated(RAW_COLUMNS).to_numpy()

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

    fd = rows["full_row_duplicate_later_copy"]
    full_row_dups = int(fd.sum())
    sizes = df.groupby(RAW_COLUMNS).size()
    full_groups = sizes[sizes > 1]
    status = rows["record_status"]
    fd_conf = int((fd & (status == "conflicting_source_versions")).sum())
    fd_exact = int((fd & (status == "exact_duplicate_first_kept")).sum())
    fd_other = full_row_dups - fd_conf - fd_exact
    exact_extra = int((exact["n"] - 1).sum())
    conf_extra = int((conf["n"] - 1).sum())

    # ---- conflict detail: every version of every conflicting key ------------
    if len(conf):
        cv = valid.merge(conf[["station", "parsed_date"]], on=["station", "parsed_date"])
        cv = cv.sort_values(["station", "parsed_date", "source_data_row"])
        conflicts = (cv.groupby(["station", "parsed_date"], sort=True)
                     .agg(version_count=("source_data_row", "size"),
                          distinct_versions=("record_signature", "nunique"),
                          rainfall_raw_versions=("rainfall_raw", lambda s: "|".join(s)),
                          source_data_rows=("source_data_row",
                                            lambda s: "|".join(map(str, s))),
                          rainfall_min=("rainfall_value", "min"),
                          rainfall_max=("rainfall_value", "max"))
                     .reset_index())
        conflicts["abs_rainfall_difference"] = (conflicts["rainfall_max"]
                                                - conflicts["rainfall_min"])
        conflicts["resolution"] = "UNRESOLVED_all_versions_retained"
        conflicts = conflicts.sort_values("abs_rainfall_difference", ascending=False,
                                          na_position="last", kind="stable")
        conflicts = conflicts[CONFLICT_COLUMNS].reset_index(drop=True)
    else:
        conflicts = pd.DataFrame(columns=CONFLICT_COLUMNS)

    # ---- explicit duplicate report -----------------------------------------
    dup_report = pd.DataFrame([
        ("exact_duplicate_station_date", len(exact), int(exact["n"].sum()), exact_extra,
         "Station+parsed Date key repeated; every version has identical District+Rainfall"),
        ("conflicting_station_date", len(conf), int(conf["n"].sum()), conf_extra,
         "Station+parsed Date key repeated; versions DIFFER; all retained, none chosen"),
        ("all_duplicated_station_date", len(dup), int(dup["n"].sum()), true_dup,
         "exact + conflicting; extra_rows = pandas duplicated(keep='first') on parsed rows"),
        ("exact_duplicate_full_rows", int(len(full_groups)), int(full_groups.sum()),
         full_row_dups,
         "all 4 raw columns byte-identical to an earlier row (all rows, incl. unparsed dates)"),
        ("rows_involved_in_conflicting_keys", len(conf), int(conf["n"].sum()), conf_extra,
         "every row belonging to a conflicting key; see rainfall_csv_conflicts.csv"),
        ("full_row_duplicates_inside_exact_keys", None, None, fd_exact,
         "later full-row copies whose Station+Date key is an exact duplicate key"),
        ("full_row_duplicates_inside_conflicting_keys", None, None, fd_conf,
         "later full-row copies inside a conflicting key (e.g. 7.0, 7.0, 8.0)"),
        ("full_row_duplicates_other_status", None, None, fd_other,
         "later full-row copies in neither group (e.g. unparseable date)"),
        ("exact_duplicate_extra_rows_not_byte_identical", None, None,
         exact_extra - fd_exact,
         "exact-duplicate extra rows that differ in raw text only (e.g. '2.0' vs '2.00')"),
    ], columns=DUP_REPORT_COLUMNS)

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
    add("duplicates", "exact_duplicate_extra_rows", exact_extra)
    add("duplicates", "conflicting_keys", len(conf),
        "versions differ in rainfall (or district); ALL versions retained")
    add("duplicates", "rows_in_conflicting_keys", int(conf["n"].sum()))
    add("duplicates", "conflicting_extra_rows", conf_extra)
    add("duplicates", "full_row_duplicates_inside_exact_keys", fd_exact)
    add("duplicates", "full_row_duplicates_inside_conflicting_keys", fd_conf,
        "explains any gap between duplicate_full_rows and exact_duplicate_extra_rows")
    add("duplicates", "full_row_duplicates_other_status", fd_other)
    add("duplicates", "conflict_max_abs_rainfall_difference",
        spread.max() if len(spread) else None, "UNIT UNVERIFIED")
    add("duplicates", "conflict_median_abs_rainfall_difference",
        spread.median() if len(spread) else None, "UNIT UNVERIFIED")
    add("duplicates", "rows_dropped_from_processed_file", 0, "nothing is dropped")

    # computed internal-consistency checks (not hardcoded references)
    def chk(name, ok, note):
        add("consistency_check", name, bool(ok), note)

    chk("exact_plus_conflicting_extra_rows_equals_true_duplicate_rows",
        exact_extra + conf_extra == true_dup, "computed identity")
    chk("exact_plus_conflicting_keys_equals_duplicate_keys",
        len(exact) + len(conf) == len(dup), "computed identity")
    chk("exact_plus_conflicting_rows_equals_rows_in_duplicated_keys",
        int(exact["n"].sum()) + int(conf["n"].sum()) == int(dup["n"].sum()),
        "computed identity")
    chk("parsed_plus_unparseable_equals_total", n_parsed + n_fail == total,
        "computed identity")
    chk("no_rows_dropped", len(rows) == len(df_raw), "processed rows == raw rows")
    if len(stations) and overall["first"] is not None:
        ok = (overall["first"] == stations["first_date"].dropna().min()
              and overall["last"] == stations["last_date"].dropna().max())
    else:
        ok = overall["first"] is None and len(stations) == 0
    chk("overall_first_last_equal_min_max_of_station_dates", ok,
        "cross-check of overall dates against station coverage table")

    computed = dict(
        total_rows=total, parsed_rows=n_parsed, date_parse_failures=n_fail,
        unique_stations_raw=rows["station"].nunique(),
        true_duplicate_station_date_rows=true_dup,
        first_date=overall["first"], last_date=overall["last"],
        duplicate_full_rows=full_row_dups, duplicate_station_date_keys=len(dup),
        exact_duplicate_keys=len(exact), exact_duplicate_extra_rows=exact_extra,
        conflicting_keys=len(conf), rows_in_conflicting_keys=int(conf["n"].sum()),
        conflicting_extra_rows=conf_extra,
        conflict_max_abs_rainfall_difference=spread.max() if len(spread) else None,
        conflict_median_abs_rainfall_difference=spread.median() if len(spread) else None)
    for k, ref in (reference or {}).items():
        add("reproduction_check", f"{k}_reproduces_user_reported",
            _matches(computed.get(k), ref),
            f"computed={computed.get(k)} reference={ref}")
    for k, ref in (pins or {}).items():
        add("regression_pin", f"{k}_matches_pipeline_observed_pin",
            _matches(computed.get(k), ref),
            f"computed={computed.get(k)} pin={ref}; pin came from a real pipeline "
            "run, so this is NOT independent verification")

    return dict(rows=rows, quality=pd.DataFrame(q), stations=stations,
                yearly=yearly, station_year=station_year,
                duplicate_report=dup_report, conflicts=conflicts)


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
    write_csv(res["duplicate_report"], meta / "rainfall_csv_duplicate_report.csv")
    write_csv(res["conflicts"], meta / "rainfall_csv_conflicts.csv")
    write_csv(res["stations"], meta / "rainfall_csv_station_coverage.csv")
    write_csv(res["yearly"], meta / "rainfall_csv_yearly.csv")
    write_csv(res["station_year"], meta / "rainfall_csv_station_year.csv")
    if verbose:
        q = res["quality"]
        print(f"[RAINFALL] {rel_posix(path, root)} (encoding {enc})")
        show = q[q["section"].isin(["dates", "stations", "duplicates",
                                    "reproduction_check", "regression_pin",
                                    "consistency_check"])]
        print(show[["section", "metric", "value"]].to_string(index=False))
        bad = q[q["section"].isin(["reproduction_check", "regression_pin",
                                   "consistency_check"]) & (q["value"] != "True")]
        if len(bad):
            print("\n[RAINFALL] !! check NOT satisfied (investigate, do not patch silently):")
            print(bad[["section", "metric", "note"]].to_string(index=False))
        print("\n[RAINFALL] duplicate report (all raw records retained):")
        print(res["duplicate_report"][["category", "key_count", "rows_in_keys",
                                       "extra_rows"]].to_string(index=False))
        c = res["conflicts"]
        print(f"\n[RAINFALL] conflicting keys listed in rainfall_csv_conflicts.csv: {len(c)}"
              " (resolution UNRESOLVED; no value chosen)")
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