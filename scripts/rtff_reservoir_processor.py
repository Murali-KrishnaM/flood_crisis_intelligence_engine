"""RTFF reservoir processing. Units of storage / inflow_total / outflow_total
are UNVERIFIED, so source field names are preserved and no unit is implied."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from pipeline_common import (COMMON_WINDOW, dates_from_filename, gap_stats,
                             load_json_file, parse_iso_date, project_root,
                             rel_posix, signature, to_float, write_csv)

RES_NUMERIC_FIELDS = ["waterlevel", "storage", "inflow_total", "outflow_total"]
RES_EXPECTED_FIELDS = set(RES_NUMERIC_FIELDS) | {"date", "time", "tankid"}

RES_COLUMNS = [
    "reservoir_id", "date", "time", "waterlevel", "storage", "inflow_total",
    "outflow_total", "source_file", "source_tile_start", "source_tile_end",
    "source_record_index", "source_tankid", "date_source",
    "source_tile_range_origin", "non_numeric_fields", "unexpected_fields",
    "suspicious_reasons", "source_record_signature",
]
ISSUE_COLUMNS = ["reservoir_id", "source_file", "source_record_index",
                 "issue_type", "detail"]


def process_reservoir_object(obj, reservoir_id, source_file, filename_range=None):
    rows, issues = [], []

    def issue(idx, typ, detail):
        issues.append(dict(reservoir_id=reservoir_id, source_file=source_file,
                           source_record_index=idx, issue_type=typ, detail=detail))

    if isinstance(obj, dict) and "date" in obj:
        obj = [obj]
    if not isinstance(obj, list):
        issue(None, "malformed_tile", f"top-level type {type(obj).__name__}")
        return rows, issues

    for idx, rec in enumerate(obj):
        if not isinstance(rec, dict):
            issue(idx, "malformed_record", f"element type {type(rec).__name__}")
            continue
        reasons = []
        d_raw = rec.get("date")
        d = parse_iso_date(d_raw) if isinstance(d_raw, str) else None
        if d is None:
            reasons.append("missing_date" if d_raw is None else "unparsable_date")
        tankid = rec.get("tankid")
        if tankid is not None and tankid != reservoir_id:
            reasons.append("tankid_differs_from_directory")
        vals, nonnum = {}, []
        for f in RES_NUMERIC_FIELDS:
            v, st = to_float(rec.get(f))
            vals[f] = v
            if st == "non_numeric":
                nonnum.append(f)
                issue(idx, "non_numeric_value", f"{f}={rec.get(f)!r}")
            if v is not None and v < 0:
                reasons.append(f"negative_{f}")
        unexpected = sorted(set(rec) - RES_EXPECTED_FIELDS)
        if unexpected:
            reasons.append("unexpected_fields")
        rows.append(dict(
            reservoir_id=reservoir_id, date=d.isoformat() if d else None,
            time=rec.get("time"), **vals, source_file=source_file,
            source_record_index=idx, source_tankid=tankid, date_source=d_raw,
            non_numeric_fields=";".join(nonnum),
            unexpected_fields=";".join(unexpected),
            suspicious_reasons=";".join(reasons),
            source_record_signature=signature(
                [rec.get("date"), rec.get("time")]
                + [rec.get(f) for f in RES_NUMERIC_FIELDS])))

    if filename_range:
        start, end, origin = filename_range[0], filename_range[1], "filename"
    else:
        ds = [r["date"] for r in rows if r["date"]]
        start, end = (min(ds), max(ds)) if ds else (None, None)
        origin = "records" if ds else "unavailable"
    for r in rows:
        r.update(source_tile_start=start, source_tile_end=end,
                 source_tile_range_origin=origin)
    return rows, issues


def process_reservoir_directory(res_root: Path, root: Path):
    """Returns (df, issues_df, tile_counts). Handles double-encoded JSON."""
    rows_all, iss_all, tile_counts = [], [], {}
    res_root = Path(res_root)
    if res_root.is_dir():
        for rdir in sorted(p for p in res_root.iterdir() if p.is_dir()):
            files = sorted(rdir.rglob("*.json"))
            tile_counts[rdir.name] = len(files)
            for f in files:
                src = rel_posix(f, root)
                try:
                    obj, _ = load_json_file(f)
                except (OSError, ValueError) as exc:
                    iss_all.append(dict(reservoir_id=rdir.name, source_file=src,
                                        source_record_index=None,
                                        issue_type="unreadable_file",
                                        detail=str(exc)[:200]))
                    continue
                r, i = process_reservoir_object(obj, rdir.name, src,
                                                dates_from_filename(f.name))
                rows_all += r
                iss_all += i
    return (pd.DataFrame(rows_all, columns=RES_COLUMNS),
            pd.DataFrame(iss_all, columns=ISSUE_COLUMNS), tile_counts)


def _num(v):
    return None if pd.isna(v) else float(v)


def compute_reservoir_quality(df, issues, tile_counts, window=COMMON_WINDOW):
    out = []
    for rid in sorted(set(tile_counts) | set(df["reservoir_id"].unique())):
        r = df[df["reservoir_id"] == rid]
        iss = issues[issues["reservoir_id"] == rid]
        keyed = r.dropna(subset=["date"])
        dates = [date.fromisoformat(d) for d in keyed["date"]]
        full = gap_stats(dates)
        win = gap_stats(dates, *window) if window else None
        dcount = keyed["date"].value_counts()
        kg = keyed.groupby(["date", "time"], dropna=False)["source_record_signature"]
        itypes = iss["issue_type"].value_counts()
        nn = r["non_numeric_fields"].fillna("")
        row = dict(
            reservoir_id=rid, tile_count=int(tile_counts.get(rid, 0)),
            unreadable_files=int(itypes.get("unreadable_file", 0)),
            record_count=len(r), first_date=full["first"], last_date=full["last"],
            unique_dates=int(len(dcount)),
            duplicate_dates=int((dcount > 1).sum()),
            duplicate_extra_rows=int((dcount - 1).clip(lower=0).sum()),
            duplicate_date_time_keys=int((kg.size() > 1).sum()) if len(keyed) else 0,
            conflicting_date_time_keys=int((kg.nunique() > 1).sum()) if len(keyed) else 0,
            span_days=full["span_days"], missing_calendar_dates=full["missing_days"],
            largest_gap_days=full["largest_gap"],
            rows_unparsable_date=int(r["date"].isna().sum()),
            distinct_time_labels="|".join(sorted(map(str, r["time"].dropna().unique()))),
            tankid_mismatch_rows=int(r["suspicious_reasons"].fillna("")
                                     .str.contains("tankid_differs").sum()),
            malformed_records=int(itypes.get("malformed_record", 0)
                                  + itypes.get("malformed_tile", 0)),
            rows_with_unexpected_fields=int((r["unexpected_fields"].fillna("") != "").sum()),
            suspicious_rows=int((r["suspicious_reasons"].fillna("") != "").sum()),
        )
        total_null = total_nn = 0
        for f in RES_NUMERIC_FIELDS:
            n_nonnum = int(nn.str.contains(f, regex=False).sum())
            n_null = int(r[f].isna().sum()) - n_nonnum
            total_null += n_null
            total_nn += n_nonnum
            row[f"null_{f}"] = n_null
            row[f"non_numeric_{f}"] = n_nonnum
            row[f"{f}_min"] = _num(r[f].min()) if len(r) else None
            row[f"{f}_max"] = _num(r[f].max()) if len(r) else None
        row["null_values_total"] = total_null
        row["non_numeric_values_total"] = total_nn
        if win is not None:
            row.update(window_start=window[0].isoformat(),
                       window_end=window[1].isoformat(),
                       window_observed_days=win["observed_days"],
                       window_missing_days=win["missing_days"],
                       window_largest_gap_days=win["largest_gap"])
        out.append(row)
    return pd.DataFrame(out)


def run_reservoir(root: Path | None = None, verbose=True):
    root = Path(root) if root else project_root()
    raw = root / "Datasets" / "raw" / "rtff" / "reservoir"
    df, issues, tiles = process_reservoir_directory(raw, root)
    proc, meta = root / "Datasets" / "processed", root / "Datasets" / "metadata"
    write_csv(df, proc / "rtff_reservoir_daily.csv")
    write_csv(issues, meta / "reservoir_issues.csv")
    quality = compute_reservoir_quality(df, issues, tiles)
    write_csv(quality, meta / "reservoir_data_quality.csv")
    if verbose:
        print(f"[RESERVOIR] reservoirs={len(tiles)} tiles={sum(tiles.values())} "
              f"records={len(df)} issues={len(issues)}")
        if not quality.empty:
            cols = ["reservoir_id", "tile_count", "record_count", "first_date",
                    "last_date", "unique_dates", "duplicate_dates",
                    "missing_calendar_dates", "largest_gap_days",
                    "distinct_time_labels", "null_values_total"]
            print(quality[[c for c in cols if c in quality]].to_string(index=False))
    return quality


if __name__ == "__main__":
    run_reservoir()