# Crisis Intelligence Engine — status.md
(Persistent project memory across Claude accounts. Replaces the earlier version.)

Project: Offline predictive RAG decision-support system, Ward 177 Velachery, Chennai.
Rule: nothing is "complete" unless implemented AND tested. The paper/presentation
are NOT evidence that a component exists.

## Current phase
Phase 1 — Data audit + source-preserving canonicalization.
Sub-step just delivered: rainfall CSV audit (replaces schema-only inspection).
STATUS: CODE DELIVERED. The assistant cannot execute code. No test run or real-data
pipeline run has been reported back yet, so NOTHING below is marked complete.

## Pipeline chain (target)
REAL DATA → RISK INFERENCE → PERSISTENCE TRIGGER → JURISDICTION → POLICY RETRIEVAL
→ LOCAL LLM → EVIDENCE VERIFICATION → DASHBOARD

| Stage | Status |
|---|---|
| Raw acquisition (RTFF ARG + reservoir, rainfall CSV, KML, stormwater PDF, policy PDFs) | Acquired (user-reported) |
| Data audit + canonicalization (RTFF ARG, reservoir) | Code written; NOT yet run/tested by user |
| Rainfall CSV audit | Code written; NOT yet run/tested. Source findings (below) were verified by the USER with an ad-hoc pandas inspection |
| Everything else (ML, spatial, RAG, Ollama, API, dashboard, PostGIS) | Not started |

## Files (delivered in chat; user copies manually)
Phase 1 original:
- scripts/pipeline_common.py, rtff_arg_processor.py, rtff_reservoir_processor.py,
  build_canonical_dataset.py
- tests/test_pipeline.py + fixtures: tests/fixtures/arg_ok/STN1/(2 tiles),
  arg_bad/STN2/(2 files), reservoir/RES1/(1 double-encoded tile)
Rainfall CSV audit step:
- scripts/rainfall_csv_processor.py   NEW
- scripts/data_audit.py               REPLACED (schema-only rainfall inspection removed;
                                      adds --rainfall, --rainfall-file; provenance now
                                      reads rainfall coverage)
- tests/test_rainfall_csv.py          NEW
- tests/fixtures/rainfall/sample.csv, tests/fixtures/rainfall_bad_date/bad.csv   NEW
- requirements.txt: pandas>=2.0, pytest>=7.0 (unchanged)
Untouched: scripts/rtff_scraper.py, scripts/rtff_velachery_collector.py, all of Datasets/raw/.
Obsolete output: Datasets/metadata/rainfall_csv_schema.csv (no longer produced; may be deleted).

## Commands
python -m unittest discover -s tests -v        (or: pytest -v)
python scripts/data_audit.py [--summary|--arg|--reservoir|--rainfall|--canonical|--stations|--provenance]
python scripts/data_audit.py --rainfall --rainfall-file "<path>"   (only if >1 CSV)

## Outputs the pipeline WILL create when run (none verified yet)
Datasets/processed/: rtff_arg_hourly_source.csv, rtff_arg_day_records.csv,
  rtff_reservoir_daily.csv, canonical_rainfall.csv, canonical_reservoir.csv,
  rainfall_csv_source_rows.csv
Datasets/metadata/: arg_data_quality.csv, arg_issues.csv, reservoir_data_quality.csv,
  reservoir_issues.csv, stations.csv, data_sources.csv, canonical_build_report.csv,
  rainfall_csv_quality.csv, rainfall_csv_station_coverage.csv,
  rainfall_csv_yearly.csv, rainfall_csv_station_year.csv

## Test results
- [ ] Unit tests (Phase 1 + rainfall): NOT YET RUN (result: ________ )
- Rainfall tests cover: DD-MM-YYYY day-first parsing; zero parse failures on valid data;
  unparseable dates reported and rows kept; Station+Date duplicate detection; exact vs
  conflicting duplicates (incl. mixed groups); null rainfall preserved; raw date string
  preserved; raw file hash unchanged; no rows dropped; station coverage; yearly counts;
  name hints are text-match only; reference values not fabricated; no unit fabrication.
- [ ] Full pipeline run on real data: NOT YET RUN (result: ________ )

## Rainfall CSV: USER-VERIFIED findings (ad-hoc pandas audit; pipeline must reproduce)
File: Datasets/raw/rainfall/Chennai Daily Rainfall 1991–2023.csv
- 21,416 rows x 4 columns: District, Station, Rainfall, Date. District: 1 value (Chennai).
- 62 unique stations (e.g. Chennai nungambakkam, Chennai port trust, Anna university,
  Chennai AP, DGP Office, Ambathur, Sholinganallur, MGR Nagar, Alandur, Zone 13 Adyar
  Eco Park, Zone 14 Perungudi, Zone 15 Sholinganallur, Zone 13 Adyar, Zone 12
  Meenambakkam, Zone 12 Alandhur, Perungudi, Adyar, ...).
- Date format is DD-MM-YYYY. pd.to_datetime(errors="coerce", dayfirst=True):
  21,416 parsed / 0 failed. NO date-format problem exists in the source.
- Parsed range 1993-01-08 .. 2023-12-12. NOTE: file name says 1991; no 1991-1992 dates
  were found in the parsed data. Do not assume earlier data exist.
- True duplicate Station+parsed-Date rows: 1,447 (extra rows beyond the first).
- Rainfall descriptive stats (UNIT UNVERIFIED): mean 18.279057, std 28.925106,
  min 0.01, 25% 2.0, 50% 7.4, 75% 22.5, max 348.3.
- CORRECTION OF EARLIER NUMBERS: the earlier "12,949 invalid dates / 13,488 duplicates"
  came from an incorrect ad-hoc audit parser, NOT from the source CSV. The source CSV has
  0 date failures. Never describe it as having 12,949 invalid dates.
- Not yet known (will come from the pipeline): exact-duplicate vs conflicting-duplicate
  split of the 1,447; per-station coverage and gaps; yearly coverage; which stations have
  data overlapping the RTFF window.

## Rainfall audit policy (implemented in code, untested on real data)
- Raw Date string preserved as date_raw; parsed_date derived with dayfirst=True; plus a
  strict ^\d{2}-\d{2}-\d{4}$ pattern cross-check.
- Duplicates are classified, never removed. Key = raw Station string + parsed date.
  exact = identical District+Rainfall in all versions; conflicting = differing versions,
  ALL retained. Unparseable-date rows would be kept with status "missing_key_kept".
- Pipeline emits reproduction_check rows comparing computed values with the user-reported
  ones (21416 rows, 0 failures, 62 stations, 1447 duplicates, date range); mismatches are
  findings, not silently patched.
- Station "study-area hints" are station-NAME text matches only (hint_basis column);
  no geographic claim is made. Stations are NOT merged with RTFF.

## RTFF raw data coverage (USER-REPORTED; to be confirmed by arg_data_quality.csv)
- ARG: 4 stations x 142 tiles = 568 OK, 0 failed. Common window 2022-04-01..2025-05-10.
  Reported missing in window: Taramani 140 (max gap 89), W178 76 (max gap 63),
  NIOT Pallikaranai 96 (max gap 29). Anna University broader archive 2018-01-02..
  2025-05-10, 2148 observed days, 538 missing, max gap 178.
- Reservoir: 5 tanks x 142 tiles = 710 OK, 0 failed (RD001 Red Hills, Cho001 Cholavaram,
  Po001 Poondi, TK-001 Thervoy Kandigal, TNCH-07-T0726 Chembarambakkam).
- Other raw: 2 KML collections (not processed), GCC stormwater PDF (not processed).
- Policy PDFs present: NDMA Flood 2008, NDMA Urban Flooding 2010, TN SDMP 2018-2030,
  CCUDMA GO Gazette 221, "Perspective Plan 2018-2030.pdf" (IDENTITY UNVERIFIED).
- NOT present: National Disaster Management Plan 2019; Disaster Management Act PDF.
  No placeholders; no code may depend on them.

## Known data-quality issues
- Substantial missing ARG days; missingness reported, never filled.
- Reservoir JSON is double-encoded; rtff_scraper.py `summary` fails on it
  (TypeError: string indices must be integers). Downloads fine; bypassed by
  pipeline_common.decode_json_text. Scraper not modified.
- aws/ has no primary AWS dataset.
- Overlapping ARG tiles may repeat dates: reported as repeated/conflicting.
- Rainfall CSV: 1,447 duplicate Station+Date rows (split exact/conflicting pending run);
  station coverage uneven/unknown until station coverage table is produced.

## Unresolved questions (DO NOT assume)
1. ARG hour-label semantics (order h09_30..h08_30; interval start/end/instant; whether
   h00_30..h08_30 belong to date_val or date_val+1). No final timestamp built.
   timestamp_status = source_date_and_hour_label_preserved.
2. ARG units (rainfall_value, dailyrainfall) unverified; no "mm" label used.
3. Relation of dailyrainfall to hourly sum unverified (info column only).
4. Reservoir units for storage / inflow_total / outflow_total unverified.
5. Rainfall CSV unit unverified (no "mm" label used).
6. Station metadata: _meta/ and station_manifest.csv schemas were NOT seen when code was
   written; ALIASES in data_audit.py are guesses. Verify against "[STATIONS] metadata keys
   seen" output and raw_meta_fields_json.
7. RTFF tile filename pattern unknown; tile start/end may fall back to record date range
   (source_tile_range_origin says which).
8. acquisition_date null everywhere (acquisition_log.csv not parsed).
9. Which of the 62 rainfall stations are geographically relevant to Ward 177: UNDECIDED;
   needs station coordinates/metadata (none exist yet). Next phase decides.
10. Whether station-name variants (case/whitespace) exist among the 62: pipeline reports
    unique_stations_raw vs unique_stations_normalized.

## Canonicalization policy (RTFF; implemented, untested on real data)
- No interpolation, zero-fill, forward-fill, averaging, unit conversion.
- Exact duplicate records: first kept in canonical only (still in source-preserving CSV).
  Conflicting versions: all kept, flagged conflicting_source_versions.
- Rainfall CSV has NO canonical file yet (audit + source-rows file only).

## Next recommended task
1. Create/replace the files above, run the tests, then run `python scripts/data_audit.py`.
2. Paste back: unittest output; discovery table; arg_data_quality.csv;
   reservoir_data_quality.csv; the [STATIONS] output; the [RAINFALL] block (especially any
   "NOT reproduced" lines); rainfall_csv_station_coverage.csv for the hint-matched
   stations; 2-3 RTFF tile filenames; _meta/ listing + station_manifest.csv head.
3. Update this file with REAL results; fix failures.
4. Then: decide the rainfall-station selection approach for Ward 177 (needs coordinates
   or an explicit, documented selection rule), ARG hour-semantics investigation, KML audit.
   Knowledge-base identity audit is a separate task.