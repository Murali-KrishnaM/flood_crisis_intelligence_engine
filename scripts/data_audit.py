"""Data audit CLI.  Run from the project root:

    python scripts/data_audit.py              # everything
    python scripts/data_audit.py --summary    # discovery + existing reports
    python scripts/data_audit.py --arg
    python scripts/data_audit.py --reservoir
    python scripts/data_audit.py --rainfall [--rainfall-file PATH]
    python scripts/data_audit.py --canonical
    python scripts/data_audit.py --stations
    python scripts/data_audit.py --provenance
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

from build_canonical_dataset import run_canonical
from pipeline_common import load_json_file, project_root, rel_posix, write_csv
from rainfall_csv_processor import run_rainfall
from rtff_arg_processor import run_arg
from rtff_reservoir_processor import run_reservoir


# ------------------------------------------------------------ discovery ----
def _files(base: Path, pattern_suffixes):
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*")
                  if p.is_file() and p.suffix.lower() in pattern_suffixes)


def discover(root: Path):
    raw = root / "Datasets" / "raw"
    rtff = raw / "rtff"
    arg_dirs = sorted(p for p in (rtff / "arg").glob("*") if p.is_dir()) if (rtff / "arg").is_dir() else []
    res_dirs = sorted(p for p in (rtff / "reservoir").glob("*") if p.is_dir()) if (rtff / "reservoir").is_dir() else []
    groups = {
        "rainfall_csv": _files(raw / "rainfall", {".csv"}),
        "rtff_arg_json_tiles": _files(rtff / "arg", {".json"}),
        "rtff_reservoir_json_tiles": _files(rtff / "reservoir", {".json"}),
        "rtff_meta_files": _files(rtff / "_meta", {".json", ".csv", ".txt"}),
        "kml_flood_all": _files(raw / "flood", {".kml"}),
        "kml_chennai_2015": _files(raw / "flood" / "chennai_2015", {".kml"}),
        "kml_chennai_flooding": _files(raw / "flood" / "chennai_flooding", {".kml"}),
        "gis_stormwater_pdf": _files(raw / "gis", {".pdf"}),
        "policy_pdf_knowledge_base": _files(root / "knowledge_base", {".pdf"}),
    }
    table = pd.DataFrame([
        dict(source_group=k, file_count=len(v),
             total_bytes=sum(f.stat().st_size for f in v),
             total_mb=round(sum(f.stat().st_size for f in v) / 1e6, 2))
        for k, v in groups.items()])
    counts = dict(rainfall_csv_files=len(groups["rainfall_csv"]),
                  arg_station_dirs=len(arg_dirs),
                  arg_json_tiles=len(groups["rtff_arg_json_tiles"]),
                  reservoir_dirs=len(res_dirs),
                  reservoir_json_tiles=len(groups["rtff_reservoir_json_tiles"]),
                  kml_files=len(groups["kml_flood_all"]),
                  policy_pdfs=len(groups["policy_pdf_knowledge_base"]))
    return table, counts, groups, arg_dirs, res_dirs


# ------------------------------------------------------------- stations ----
def _norm(k):
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


ALIASES = {
    "station_code": {"stationcode", "stncode", "code"},
    "rtff_internal_id": {"argid", "tankid", "rtffid", "internalid", "id"},
    "station_name": {"stationname", "name", "argname", "tankname", "location"},
    "station_type": {"stationtype", "type", "category"},
    "district": {"district", "districtname"},
    "agency": {"agency", "department", "dept", "organisation", "organization"},
    "latitude": {"latitude", "lat"},
    "longitude": {"longitude", "lon", "lng", "long"},
    "established_date": {"established", "estddate", "establisheddate",
                         "installeddate", "commissioned"},
}


def _extract_rows(obj):
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            return list(obj)
        rows = []
        for x in obj:
            rows += _extract_rows(x)
        return rows
    if isinstance(obj, dict):
        rows = [obj] if any(not isinstance(v, (list, dict)) for v in obj.values()) else []
        for v in obj.values():
            if isinstance(v, (list, dict)):
                rows += _extract_rows(v)
        return rows
    return []


def _load_meta_rows(root: Path):
    rtff = root / "Datasets" / "raw" / "rtff"
    sources = [rtff / "station_manifest.csv"] + _files(rtff / "_meta", {".csv", ".json"})
    items = []
    for f in sources:
        if not f.exists():
            continue
        try:
            if f.suffix.lower() == ".csv":
                rows = pd.read_csv(f, dtype=str).to_dict("records")
            else:
                obj, _ = load_json_file(f)
                rows = _extract_rows(obj)
        except Exception as exc:  # noqa: BLE001
            print(f"[STATIONS] could not read {f.name}: {exc}")
            continue
        items += [(rel_posix(f, root), r) for r in rows]
    return items


def build_stations(root: Path, arg_dirs, res_dirs, verbose=True):
    meta_items = _load_meta_rows(root)
    out, matched_n = [], 0
    for category, dirs in (("arg", arg_dirs), ("reservoir", res_dirs)):
        for d in dirs:
            key = d.name.strip().lower()
            merged, raw_all, srcs = {}, [], []
            for src, row in meta_items:
                vals = {str(v).strip().lower() for v in row.values()
                        if v is not None and not isinstance(v, (list, dict))}
                if key in vals:
                    srcs.append(src)
                    raw_all.append(row)
                    for field, names in ALIASES.items():
                        if field in merged:
                            continue
                        for k, v in row.items():
                            if _norm(k) in names and v not in (None, "", "nan"):
                                merged[field] = v
                                break
            matched_n += bool(raw_all)
            rec = dict(station_id=d.name, source_category=category, **{
                f: merged.get(f) for f in ALIASES})
            rec["latitude"] = pd.to_numeric(rec["latitude"], errors="coerce")
            rec["longitude"] = pd.to_numeric(rec["longitude"], errors="coerce")
            rec["metadata_sources"] = "|".join(dict.fromkeys(srcs)) or None
            rec["raw_meta_fields_json"] = json.dumps(raw_all, default=str) if raw_all else None
            out.append(rec)
    df = pd.DataFrame(out)
    write_csv(df, root / "Datasets" / "metadata" / "stations.csv")
    if verbose:
        keys = sorted({k for _, r in meta_items for k in r})
        print(f"[STATIONS] directories={len(df)} matched_to_metadata={matched_n} "
              f"meta_rows_loaded={len(meta_items)}")
        print(f"[STATIONS] metadata keys seen: {keys[:40]}")
        print("[STATIONS] VERIFY alias mapping against these keys; "
              "raw_meta_fields_json keeps everything found.")
    return df


# ----------------------------------------------------------- provenance ----
def build_provenance(root: Path, groups, verbose=True):
    meta = root / "Datasets" / "metadata"
    rows = []

    def cov(path, id_col, start_col, end_col):
        p = meta / path
        if p.exists():
            q = pd.read_csv(p, dtype=str)
            return {r[id_col]: (r[start_col], r[end_col]) for _, r in q.iterrows()}
        return {}

    arg_cov = cov("arg_data_quality.csv", "station_id", "first_source_date", "last_source_date")
    res_cov = cov("reservoir_data_quality.csv", "reservoir_id", "first_date", "last_date")
    raw = root / "Datasets" / "raw"
    for base, kind, cmap, status in (
        (raw / "rtff" / "arg", "rtff_arg", arg_cov, "processed_to_canonical"),
        (raw / "rtff" / "reservoir", "rtff_reservoir", res_cov, "processed_to_canonical"),
    ):
        if base.is_dir():
            for d in sorted(p for p in base.iterdir() if p.is_dir()):
                s, e = cmap.get(d.name, (None, None))
                rows.append(dict(
                    source_name=f"{kind}:{d.name}", source_type=kind,
                    original_location=None, local_path=rel_posix(d, root),
                    acquisition_date=None, coverage_start=s, coverage_end=e,
                    processing_status=status if s else "not_yet_processed",
                    notes="coverage = first/last parsed source date; acquisition "
                          "date not derived (see rtff/acquisition_log.csv)"))

    # rainfall CSV: coverage from the rainfall audit if it has been run
    rq = meta / "rainfall_csv_quality.csv"
    r_start = r_end = None
    r_status = "not_yet_audited"
    if rq.exists():
        q = pd.read_csv(rq, dtype=str, keep_default_na=False)
        m = dict(zip(q["metric"], q["value"]))
        r_start, r_end = m.get("first_date") or None, m.get("last_date") or None
        r_status = "audited_not_canonicalized"
    for f in groups.get("rainfall_csv", []):
        rows.append(dict(
            source_name=f.stem, source_type="rainfall_csv",
            original_location=None, local_path=rel_posix(f, root),
            acquisition_date=None, coverage_start=r_start, coverage_end=r_end,
            processing_status=r_status,
            notes="independent of RTFF; not merged; rainfall unit UNVERIFIED; "
                  "duplicates flagged not removed; coverage = first/last parsed "
                  "date (file name suggests 1991 start - not assumed)"))

    simple = [
        ("kml_flood_all", "flood_kml", "not_processed_future_task", "KML not converted yet"),
        ("gis_stormwater_pdf", "gis_pdf", "not_processed_future_task", "PDF not processed yet"),
        ("policy_pdf_knowledge_base", "policy_pdf", "not_processed_future_task",
         "document identities NOT yet verified; RAG ingestion is a later task"),
    ]
    for g, kind, status, note in simple:
        for f in groups.get(g, []):
            rows.append(dict(source_name=f.stem, source_type=kind,
                             original_location=None, local_path=rel_posix(f, root),
                             acquisition_date=None, coverage_start=None,
                             coverage_end=None, processing_status=status, notes=note))
    df = pd.DataFrame(rows)
    write_csv(df, meta / "data_sources.csv")
    if verbose:
        print(f"[PROVENANCE] {len(df)} source rows -> Datasets/metadata/data_sources.csv")
    return df


# ------------------------------------------------------------------ CLI ----
def print_summary(root: Path):
    table, counts, *_ = discover(root)
    print("=== RAW DATA DISCOVERY ===")
    print(table.to_string(index=False))
    print("\n" + json.dumps(counts, indent=2))
    meta = root / "Datasets" / "metadata"
    for name in ("arg_data_quality.csv", "reservoir_data_quality.csv",
                 "rainfall_csv_quality.csv", "canonical_build_report.csv"):
        p = meta / name
        print(f"\n=== {name} ===")
        if not p.exists():
            print("(not generated yet)")
        elif name == "rainfall_csv_quality.csv":
            print(pd.read_csv(p, dtype=str, keep_default_na=False)
                  [["section", "metric", "value"]].to_string(index=False))
        else:
            print(pd.read_csv(p).to_string(index=False))


def _safe_rainfall(root, csv_path=None):
    try:
        return run_rainfall(root, csv_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[RAINFALL] skipped: {exc}")
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Crisis Intelligence Engine data audit")
    for flag in ("arg", "reservoir", "rainfall", "canonical", "stations",
                 "provenance", "summary"):
        ap.add_argument(f"--{flag}", action="store_true")
    ap.add_argument("--rainfall-file", default=None,
                    help="rainfall CSV path (relative to project root or absolute)")
    ap.add_argument("--root", default=None, help="override project root (testing)")
    a = ap.parse_args(argv)
    root = Path(a.root) if a.root else project_root()
    (root / "Datasets" / "processed").mkdir(parents=True, exist_ok=True)
    (root / "Datasets" / "metadata").mkdir(parents=True, exist_ok=True)

    if a.summary:
        print_summary(root)
        return 0
    run_all = not any([a.arg, a.reservoir, a.rainfall, a.canonical, a.stations,
                       a.provenance])
    table, counts, groups, arg_dirs, res_dirs = discover(root)
    if run_all:
        print("=== RAW DATA DISCOVERY ===")
        print(table.to_string(index=False))
        print(json.dumps(counts, indent=2))
    if run_all or a.arg:
        run_arg(root)
    if run_all or a.reservoir:
        run_reservoir(root)
    if run_all or a.rainfall:
        _safe_rainfall(root, a.rainfall_file)
    if run_all or a.canonical:
        run_canonical(root)
    if run_all or a.stations:
        build_stations(root, arg_dirs, res_dirs)
    if run_all or a.provenance:
        build_provenance(root, groups)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())