"""Build canonical datasets from the source-preserving processed files.

Rules: no imputation, no timestamp guessing. Only EXACT duplicate records are
removed (first copy kept). Conflicting versions are all retained and flagged.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from pipeline_common import classify_duplicates, project_root, write_csv

ARG_STATUS = "source_date_and_hour_label_preserved"
RES_STATUS = "source_date_and_time_label_preserved"


def build_canonical_rainfall(root: Path | None = None):
    root = Path(root) if root else project_root()
    proc, meta = root / "Datasets" / "processed", root / "Datasets" / "metadata"
    hp, rp = proc / "rtff_arg_hourly_source.csv", proc / "rtff_arg_day_records.csv"
    if not (hp.exists() and rp.exists()):
        raise FileNotFoundError("Run `python scripts/data_audit.py --arg` first.")
    s = str
    hourly = pd.read_csv(hp, dtype={"station_id": s, "source_date": s,
                                    "source_hour_label": s, "source_file": s,
                                    "source_tile_start": s, "source_tile_end": s})
    recs = pd.read_csv(rp, dtype={"station_id": s, "source_file": s, "source_date": s,
                                  "parsed_date": s, "source_record_signature": s})
    recs = recs.sort_values(["station_id", "source_file", "source_record_index"])
    recs = classify_duplicates(recs, ["station_id", "source_date"],
                               "source_record_signature")
    status = recs[["station_id", "source_file", "source_record_index",
                   "record_status", "keep", "parsed_date"]]
    merged = hourly.merge(status, on=["station_id", "source_file",
                                      "source_record_index"], how="inner")
    merged = merged[merged["keep"]]
    out = pd.DataFrame({
        "station_id": merged["station_id"],
        "observation_date": merged["parsed_date"],      # from source date only
        "observation_time": merged["source_hour_label"],  # LABEL, not a parsed time
        "timestamp_status": ARG_STATUS,
        "source_date": merged["source_date"],
        "source_hour_label": merged["source_hour_label"],
        "source_hour_index": merged["source_hour_index"],
        "rainfall_value": merged["rainfall_value"],
        "source_daily_rainfall": merged["source_daily_rainfall"],
        "record_status": merged["record_status"],
        "source_file": merged["source_file"],
        "source_record_index": merged["source_record_index"],
        "source_tile_start": merged["source_tile_start"],
        "source_tile_end": merged["source_tile_end"],
    }).sort_values(["station_id", "observation_date", "source_hour_index",
                    "source_file", "source_record_index"], na_position="last")
    write_csv(out, proc / "canonical_rainfall.csv")
    report = dict(dataset="canonical_rainfall", input_day_records=len(recs),
                  output_day_records=int(recs["keep"].sum()),
                  exact_duplicates_dropped=int((~recs["keep"]).sum()),
                  conflicting_records_retained=int((recs["record_status"] ==
                                                    "conflicting_source_versions").sum()),
                  input_rows=len(hourly), output_rows=len(out))
    return out, report


def build_canonical_reservoir(root: Path | None = None):
    root = Path(root) if root else project_root()
    proc = root / "Datasets" / "processed"
    p = proc / "rtff_reservoir_daily.csv"
    if not p.exists():
        raise FileNotFoundError("Run `python scripts/data_audit.py --reservoir` first.")
    s = str
    df = pd.read_csv(p, dtype={"reservoir_id": s, "date": s, "time": s,
                               "source_file": s, "source_record_signature": s,
                               "source_tile_start": s, "source_tile_end": s})
    df = df.sort_values(["reservoir_id", "source_file", "source_record_index"])
    df = classify_duplicates(df, ["reservoir_id", "date", "time"],
                             "source_record_signature")
    kept = df[df["keep"]]
    out = pd.DataFrame({
        "reservoir_id": kept["reservoir_id"],
        "observation_date": kept["date"],
        "observation_time": kept["time"],        # source label, e.g. "6:00 AM"
        "timestamp_status": RES_STATUS,
        "waterlevel": kept["waterlevel"], "storage": kept["storage"],
        "inflow_total": kept["inflow_total"], "outflow_total": kept["outflow_total"],
        "record_status": kept["record_status"],
        "source_file": kept["source_file"],
        "source_record_index": kept["source_record_index"],
        "source_tile_start": kept["source_tile_start"],
        "source_tile_end": kept["source_tile_end"],
    }).sort_values(["reservoir_id", "observation_date", "observation_time"],
                   na_position="last")
    write_csv(out, proc / "canonical_reservoir.csv")
    report = dict(dataset="canonical_reservoir", input_day_records=len(df),
                  output_day_records=len(out),
                  exact_duplicates_dropped=int((~df["keep"]).sum()),
                  conflicting_records_retained=int((df["record_status"] ==
                                                    "conflicting_source_versions").sum()),
                  input_rows=len(df), output_rows=len(out))
    return out, report


def run_canonical(root: Path | None = None, verbose=True):
    root = Path(root) if root else project_root()
    reports = []
    for fn in (build_canonical_rainfall, build_canonical_reservoir):
        try:
            _, rep = fn(root)
            reports.append(rep)
        except FileNotFoundError as exc:
            print(f"[CANONICAL] skipped: {exc}")
    rep_df = pd.DataFrame(reports)
    if not rep_df.empty:
        write_csv(rep_df, root / "Datasets" / "metadata" / "canonical_build_report.csv")
        if verbose:
            print("[CANONICAL]")
            print(rep_df.to_string(index=False))
    return rep_df


if __name__ == "__main__":
    run_canonical()