# Crisis Intelligence Engine — status.md
(Persistent project memory. Replace/merge with any earlier history you wish to keep.)

Project: Offline predictive RAG decision-support system, Ward 177 Velachery, Chennai.
Rule: nothing is "complete" unless implemented AND tested. Paper/presentation are NOT evidence.

## Current phase
Phase 1 — Data audit + source-preserving canonicalization.
Status: CODE DELIVERED, NOT YET EXECUTED ON REAL PROJECT DATA

Status: CODE DELIVERED, NOT YET EXECUTED BY ANYONE. All checkboxes below stay
unticked until the user runs the commands and records the results in
"Test results" / "Verified findings".

## Pipeline chain (target)
REAL DATA → RISK INFERENCE → PERSISTENCE TRIGGER → JURISDICTION → POLICY RETRIEVAL
→ LOCAL LLM → EVIDENCE VERIFICATION → DASHBOARD

| Stage | Status |
|---|---|
| Raw acquisition (RTFF ARG, reservoir; rainfall CSV; KML; stormwater PDF; policy PDFs) | Acquired (per user report; ARG 568/568 tiles, reservoir 710/710 tiles) |
| Data audit + canonicalization | Code written; NOT run; NOT tested |
| Everything else (ML, spatial, RAG, Ollama, API, dashboard, PostGIS) | Not started |

## Files created in Phase 1 (delivered in chat; user copies manually)
- scripts/pipeline_common.py        (JSON double-decode, gap stats, duplicate classification, helpers)
- scripts/rtff_arg_processor.py     (ARG long format + quality audit)
- scripts/rtff_reservoir_processor.py (reservoir records + quality audit)
- scripts/build_canonical_dataset.py (canonical_rainfall / canonical_reservoir)
- scripts/data_audit.py             (CLI: discovery, stations, provenance, orchestration)
- tests/test_pipeline.py
- tests/fixtures/arg_ok/STN1/tile_2022-04-01_2022-04-08.json
- tests/fixtures/arg_ok/STN1/tile_2022-04-04_2022-04-11.json
- tests/fixtures/arg_bad/STN2/tile_bad.json
- tests/fixtures/arg_bad/STN2/tile_corrupt.json
- tests/fixtures/reservoir/RES1/tile_2022-04-08_2022-04-15.json (double-encoded)
- requirements.txt: add pandas>=2.0, pytest>=7.0
Existing scripts/rtff_scraper.py and rtff_velachery_collector.py: UNCHANGED.

## Outputs the pipeline WILL create when run (none verified yet)
Datasets/processed/: rtff_arg_hourly_source.csv, rtff_arg_day_records.csv,
  rtff_reservoir_daily.csv, canonical_rainfall.csv, canonical_reservoir.csv
Datasets/metadata/: arg_data_quality.csv, arg_issues.csv,
  reservoir_data_quality.csv, reservoir_issues.csv, stations.csv,
  data_sources.csv, rainfall_csv_schema.csv, canonical_build_report.csv

## Commands
python -m unittest discover -s tests -v     (or: pytest -v)
python scripts/data_audit.py [--summary|--arg|--reservoir|--canonical|--stations|--provenance]

## Test results
- [ ] Unit tests run: NOT YET RUN  (result: ________ )
- Tests cover: ARG parsing; double-encoded reservoir JSON; null preservation;
  24 hour fields + order; duplicate/conflict detection; missing-date/gap
  detection; malformed + unreadable handling; no unit fabrication; provenance.
- [ ] Full pipeline run on real data: NOT YET RUN  (result: ________ )

## Raw data coverage (USER-REPORTED; to be confirmed by arg_data_quality.csv)
- ARG: 4 stations x 142 tiles = 568 OK, 0 failed. Common window 2022-04-01..2025-05-10.
  Reported missing in window: Taramani 140 (max gap 89), W178 76 (max gap 63),
  NIOT Pallikaranai 96 (max gap 29). Anna University broader archive
  2018-01-02..2025-05-10, 2148 observed days, 538 missing, max gap 178.
- Reservoir: 5 tanks x 142 tiles = 710 OK, 0 failed (RD001 Red Hills, Cho001
  Cholavaram, Po001 Poondi, TK-001 Thervoy Kandigal, TNCH-07-T0726 Chembarambakkam).
- Other raw: Chennai Daily Rainfall 1991–2023 CSV (schema not yet inspected),
  2 KML collections (not processed), GCC stormwater PDF (not processed).
- Policy PDFs present: NDMA Flood 2008, NDMA Urban Flooding 2010, TN SDMP 2018-2030,
  CCUDMA GO Gazette 221, "Perspective Plan 2018-2030.pdf" (IDENTITY UNVERIFIED).
- NOT present: National Disaster Management Plan 2019; Disaster Management Act PDF.
  Do not create placeholders or depend on them.

## Known data-quality issues
- Substantial missing ARG days (see above); missingness is reported, never filled.
- Reservoir JSON is double-encoded; rtff_scraper.py `summary` fails on it
  (TypeError: string indices must be integers). Downloads are fine. Bypassed by
  pipeline_common.decode_json_text. Scraper not modified.
- aws/ has no primary AWS dataset.
- Overlapping tiles may repeat dates: pipeline reports repeated/conflicting dates.

## Unresolved questions (DO NOT assume)
1. ARG hour-label semantics: order h09_30..h08_30; whether label = interval
   start/end/instant; whether h00_30..h08_30 belong to date_val or date_val+1.
   => No final timestamp built. timestamp_status = source_date_and_hour_label_preserved.
2. ARG units (rainfall_value, dailyrainfall) unverified; no "mm" label used.
3. Relation of dailyrainfall to hourly sum unverified
   (info column daily_minus_hourly_sum only).
4. Reservoir units for storage / inflow_total / outflow_total unverified;
   source names preserved.
5. Station metadata: _meta/ and station_manifest.csv schemas were NOT seen when
   the code was written. ALIASES in data_audit.py are guesses; verify against the
   "[STATIONS] metadata keys seen" output and raw_meta_fields_json.
6. Tile filename pattern unknown; tile start/end falls back to record date range
   (source_tile_range_origin column says which was used).
7. Rainfall CSV schema unknown (inspected at runtime into rainfall_csv_schema.csv).
8. acquisition_date left null everywhere (acquisition_log.csv not parsed).

## Canonicalization policy (implemented in code, untested on real data)
- No interpolation, zero-fill, forward-fill, averaging, unit conversion.
- Exact duplicate records: first kept, later copies dropped from canonical only
  (still in source-preserving CSV). Conflicting versions: ALL kept, flagged
  record_status = conflicting_source_versions.

## Next recommended task
1. Create files, install requirements, run tests, run `python scripts/data_audit.py`.
2. Paste back: test output, discovery table, arg_data_quality.csv,
   reservoir_data_quality.csv, [STATIONS] output, head of rainfall_csv_schema.csv,
   2-3 raw tile filenames, and the _meta/ file listing + station_manifest.csv head.
3. Then update this file with REAL results; fix any failures.
4. Next task after that: Chennai Daily Rainfall CSV audit, then ARG hour-semantics
   investigation (compare to rainfall CSV / RTFF documentation), then KML audit.
   Knowledge-base identity audit is separate.