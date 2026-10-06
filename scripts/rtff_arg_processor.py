"""RTFF ARG (rain-gauge) processing: source-preserving long format + audit.

Hour labels are kept EXACTLY as in the source. `source_hour_index` is only the
position of the label in the documented field order (h09_30 ... h08_30); it is
NOT a verified chronological position. rainfall_value has no unit.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from pipeline_common import (COMMON_WINDOW, dates_from_filename, gap_stats,
                             load_json_file, parse_ddmmyyyy, project_root,
                             rel_posix, signature, to_float, write_csv)
from datetime import date

ARG_HOUR_FIELDS = (
    [f"h{h:02d}_30" for h in range(9, 24)] + [f"h{h:02d}_30" for h in range(0, 9)]
)
ARG_EXPECTED_FIELDS = set(ARG_HOUR_FIELDS) | {"argid", "date_val", "dailyrainfall"}

HOURLY_COLUMNS = [
    "station_id", "source_date", "source_hour_label", "rainfall_value",
    "source_daily_rainfall", "source_file", "source_tile_start",
    "source_tile_end", "source_hour_index", "source_record_index",
]
RECORD_COLUMNS = [
    "station_id", "source_file", "source_record_index", "source_argid",
    "source_date", "parsed_date", "source_daily_rainfall_raw", "daily_status",
    "hour_fields_present", "hour_null", "hour_nonnull", "hour_non_numeric",
    "hour_negative", "missing_hour_fields", "unexpected_fields",
    "daily_minus_hourly_sum", "suspicious_reasons", "source_record_signature",
    "source_tile_start", "source_tile_end", "source_tile_range_origin",
]
ISSUE_COLUMNS = ["station_id", "source_file", "source_record_index",
                 "issue_type", "detail"]


def process_arg_object(obj, station_id, source_file, filename_range=None):
    """Process one decoded tile. Returns (records, hourly_rows, issues)."""
    records, hourly, issues = [], [], []

    def issue(idx, typ, detail):
        issues.append(dict(station_id=station_id, source_file=source_file,
                           source_record_index=idx, issue_type=typ,
                           detail=detail))

    if isinstance(obj, dict) and "date_val" in obj:
        obj = [obj]
    if not isinstance(obj, list):
        issue(None, "malformed_tile", f"top-level type {type(obj).__name__}")
        return records, hourly, issues

    for idx, rec in enumerate(obj):
        if not isinstance(rec, dict):
            issue(idx, "malformed_record", f"element type {type(rec).__name__}")
            continue
        reasons = []
        src_date = rec.get("date_val")
        parsed = parse_ddmmyyyy(src_date) if isinstance(src_date, str) else None
        if src_date is None:
            reasons.append("missing_date_val")
        elif parsed is None:
            reasons.append("unparsable_date")
        argid = rec.get("argid")
        if argid is not None and argid != station_id:
            reasons.append("argid_differs_from_directory")

        if "dailyrainfall" in rec:
            daily, dstat = to_float(rec["dailyrainfall"])
        else:
            daily, dstat = None, "missing_field"
            reasons.append("daily_missing_field")
        if dstat == "non_numeric":
            reasons.append("daily_non_numeric")
        if daily is not None and daily < 0:
            reasons.append("daily_negative")

        present = nulls = nonnum = neg = ok_n = 0
        hsum = 0.0
        missing = []
        for pos, h in enumerate(ARG_HOUR_FIELDS):
            if h not in rec:
                missing.append(h)
                continue
            present += 1
            val, st = to_float(rec[h])
            if st == "null":
                nulls += 1
            elif st == "non_numeric":
                nonnum += 1
                issue(idx, "non_numeric_hourly", f"{h}={rec[h]!r}")
            else:
                ok_n += 1
                hsum += val
                if val < 0:
                    neg += 1
            hourly.append(dict(
                station_id=station_id, source_date=src_date,
                source_hour_label=h, rainfall_value=val,
                source_daily_rainfall=daily, source_file=source_file,
                source_hour_index=pos, source_record_index=idx))
        if missing:
            reasons.append("missing_hour_fields")
        if neg:
            reasons.append("hourly_negative")
        if nonnum:
            reasons.append("hourly_non_numeric")
        if present and nulls == present:
            reasons.append("all_hourly_null")
        unexpected = sorted(set(rec) - ARG_EXPECTED_FIELDS)
        if unexpected:
            reasons.append("unexpected_fields")

        records.append(dict(
            station_id=station_id, source_file=source_file,
            source_record_index=idx, source_argid=argid, source_date=src_date,
            parsed_date=parsed.isoformat() if parsed else None,
            source_daily_rainfall_raw=repr(rec.get("dailyrainfall")),
            daily_status=dstat, hour_fields_present=present, hour_null=nulls,
            hour_nonnull=present - nulls, hour_non_numeric=nonnum,
            hour_negative=neg, missing_hour_fields=";".join(missing),
            unexpected_fields=";".join(unexpected),
            daily_minus_hourly_sum=(daily - hsum) if (daily is not None and ok_n) else None,
            suspicious_reasons=";".join(reasons),
            source_record_signature=signature(
                [rec.get("date_val"), rec.get("dailyrainfall"),
                 [rec.get(h) for h in ARG_HOUR_FIELDS]]),
        ))

    # tile range provenance
    if filename_range:
        start, end, origin = filename_range[0], filename_range[1], "filename"
    else:
        ds = [r["parsed_date"] for r in records if r["parsed_date"]]
        start, end = (min(ds), max(ds)) if ds else (None, None)
        origin = "records" if ds else "unavailable"
    for r in records:
        r.update(source_tile_start=start, source_tile_end=end,
                 source_tile_range_origin=origin)
    for h in hourly:
        h.update(source_tile_start=start, source_tile_end=end)
    return records, hourly, issues


def process_arg_directory(arg_root: Path, root: Path):
    """Returns (hourly_df, records_df, issues_df, tile_counts)."""
    hourly_all, rec_all, iss_all, tile_counts = [], [], [], {}
    arg_root = Path(arg_root)
    if arg_root.is_dir():
        for sdir in sorted(p for p in arg_root.iterdir() if p.is_dir()):
            files = sorted(sdir.rglob("*.json"))
            tile_counts[sdir.name] = len(files)
            for f in files:
                src = rel_posix(f, root)
                try:
                    obj, _layers = load_json_file(f)
                except (OSError, ValueError) as exc:
                    iss_all.append(dict(station_id=sdir.name, source_file=src,
                                        source_record_index=None,
                                        issue_type="unreadable_file",
                                        detail=str(exc)[:200]))
                    continue
                r, h, i = process_arg_object(obj, sdir.name, src,
                                             dates_from_filename(f.name))
                rec_all += r
                hourly_all += h
                iss_all += i
    return (pd.DataFrame(hourly_all, columns=HOURLY_COLUMNS),
            pd.DataFrame(rec_all, columns=RECORD_COLUMNS),
            pd.DataFrame(iss_all, columns=ISSUE_COLUMNS),
            tile_counts)


def compute_arg_quality(records, issues, tile_counts, window=COMMON_WINDOW):
    rows = []
    stations = sorted(set(tile_counts) | set(records["station_id"].unique()))
    for sid in stations:
        r = records[records["station_id"] == sid]
        iss = issues[issues["station_id"] == sid]
        valid = r.dropna(subset=["parsed_date"])
        dates = [date.fromisoformat(d) for d in valid["parsed_date"]]
        full = gap_stats(dates)
        win = gap_stats(dates, *window) if window else None
        cnt = valid["parsed_date"].value_counts()
        files_per_date = valid.groupby("parsed_date")["source_file"].nunique()
        sigs_per_date = valid.groupby("parsed_date")["source_record_signature"].nunique()
        unexpected = sorted({n for s in r["unexpected_fields"].dropna()
                             for n in str(s).split(";") if n})
        diff = r["daily_minus_hourly_sum"].dropna()
        itypes = iss["issue_type"].value_counts()
        row = dict(
            station_id=sid,
            tile_count=int(tile_counts.get(sid, 0)),
            unreadable_files=int(itypes.get("unreadable_file", 0)),
            raw_day_rows=len(r),
            unique_source_dates=int(len(cnt)),
            duplicate_source_dates=int((cnt > 1).sum()),
            duplicate_extra_rows=int((cnt - 1).clip(lower=0).sum()),
            repeated_dates_across_tiles=int((files_per_date > 1).sum()),
            conflicting_dates=int((sigs_per_date > 1).sum()),
            first_source_date=full["first"], last_source_date=full["last"],
            span_days=full["span_days"],
            missing_calendar_days=full["missing_days"],
            largest_missing_gap_days=full["largest_gap"],
            hourly_field_count=int(r["hour_fields_present"].sum()),
            null_hourly_values=int(r["hour_null"].sum()),
            non_null_hourly_values=int(r["hour_nonnull"].sum()),
            non_numeric_hourly_values=int(r["hour_non_numeric"].sum()),
            source_daily_totals_count=int((r["daily_status"] == "ok").sum()),
            suspicious_rows=int((r["suspicious_reasons"].fillna("") != "").sum()),
            malformed_records=int(itypes.get("malformed_record", 0)
                                  + itypes.get("malformed_tile", 0)),
            rows_missing_hour_fields=int((r["missing_hour_fields"].fillna("") != "").sum()),
            rows_with_unexpected_fields=int((r["unexpected_fields"].fillna("") != "").sum()),
            unexpected_field_names="|".join(unexpected),
            rows_argid_differs_from_dir=int(r["suspicious_reasons"].fillna("")
                                            .str.contains("argid_differs").sum()),
            info_rows_daily_differs_from_hourly_sum=int((diff.abs() > 0.01).sum()),
        )
        if win is not None:
            row.update(window_start=window[0].isoformat(),
                       window_end=window[1].isoformat(),
                       window_observed_days=win["observed_days"],
                       window_missing_days=win["missing_days"],
                       window_largest_gap_days=win["largest_gap"])
        rows.append(row)
    return pd.DataFrame(rows)


def run_arg(root: Path | None = None, verbose=True):
    root = Path(root) if root else project_root()
    raw = root / "Datasets" / "raw" / "rtff" / "arg"
    hourly, records, issues, tiles = process_arg_directory(raw, root)
    proc, meta = root / "Datasets" / "processed", root / "Datasets" / "metadata"
    write_csv(hourly, proc / "rtff_arg_hourly_source.csv")
    write_csv(records, proc / "rtff_arg_day_records.csv")
    write_csv(issues, meta / "arg_issues.csv")
    quality = compute_arg_quality(records, issues, tiles)
    write_csv(quality, meta / "arg_data_quality.csv")
    if verbose:
        print(f"[ARG] stations={len(tiles)} tiles={sum(tiles.values())} "
              f"day_rows={len(records)} hourly_rows={len(hourly)} "
              f"issues={len(issues)}")
        if not quality.empty:
            cols = ["station_id", "tile_count", "raw_day_rows", "unique_source_dates",
                    "first_source_date", "last_source_date", "missing_calendar_days",
                    "largest_missing_gap_days", "window_observed_days",
                    "window_missing_days", "window_largest_gap_days"]
            print(quality[[c for c in cols if c in quality]].to_string(index=False))
    return quality


if __name__ == "__main__":
    run_arg()