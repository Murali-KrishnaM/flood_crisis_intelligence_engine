# Crisis Intelligence Engine — status.md
(Persistent project memory across Claude accounts. Replaces the earlier version.)

Project: Offline predictive RAG decision-support system, Ward 177 Velachery, Chennai.
Rule: nothing is "complete" unless implemented AND tested. The paper/presentation are NOT
evidence that a component exists. The assistant cannot execute code: every result below is
labelled USER-REPORTED (user ran it) or DELIVERED-UNVERIFIED (code written, not yet run).

## Current phase
Phase 1 — Data audit + source-preserving canonicalization.
Sub-step: rainfall CSV audit — real-data run done by the user; fix round 2 delivered.

## Pipeline chain (target)
REAL DATA → RISK INFERENCE → PERSISTENCE TRIGGER → JURISDICTION → POLICY RETRIEVAL
→ LOCAL LLM → EVIDENCE VERIFICATION → DASHBOARD

| Stage | Status |
|---|---|
| Raw acquisition (RTFF ARG + reservoir, rainfall CSV, KML, stormwater PDF, policy PDFs) | Acquired (user-reported) |
| RTFF ARG/reservoir audit + canonicalization | Code written; unit tests passed (user-reported); real-data run results NOT yet reported |
| Rainfall CSV audit | Run on REAL data by user (results below). Fix round 2 (below) DELIVERED-UNVERIFIED |
| Everything else (ML, spatial, RAG, Ollama, API, dashboard, PostGIS) | Not started |

## Test results
- USER-REPORTED (before fix round 2): 27 tests, 26 passed, 1 failed.
  Failure: test_station_coverage — test compared ISO strings with datetime.date objects.
  Cause: TEST expectation type mismatch only. Production code was correct and unchanged.
- Fix round 2 (DELIVERED-UNVERIFIED): test fixed to compare date objects; 7 tests added
  (date-object handling, stale reference removal, reference-match helper, duplicate-report
  categories, conflict-detail, consistency checks) plus extended run test.
  Expected total ≈ 34 tests (27 + 7). ACTUAL RESULT: NOT YET REPORTED  ( ________ )
- [ ] Full real-data pipeline run after fix round 2: NOT YET REPORTED ( ________ )

## Fix round 2 — what changed (DELIVERED-UNVERIFIED)
- tests/test_rainfall_csv.py: date-object comparison fix + new tests.
- scripts/rainfall_csv_processor.py:
  * Stale reference dates 1993-01-08 / 2023-12-12 REMOVED. They were retracted.
  * References split: USER_REPORTED (rows, parse results, station count, 1447; suffix
    _reproduces_user_reported) vs PIPELINE_OBSERVED_PINS (first/last date and duplicate
    counts from the real pipeline run; suffix _matches_pipeline_observed_pin). Pins are
    regression guards, NOT independent verification.
  * Numeric comparison tolerance 0.05 (reported figures are 1-decimal).
  * New computed consistency_check rows (e.g. exact+conflicting extra rows == 1447 identity).
  * New output Datasets/metadata/rainfall_csv_duplicate_report.csv
    (exact Station+Date dups; conflicting Station+Date; exact full-row dups; rows involved in
    conflicting keys; full-row dups split by key status; non-byte-identical exact dups).
  * New output Datasets/metadata/rainfall_csv_conflicts.csv: one row per conflicting key,
    all raw versions + source row numbers, resolution = UNRESOLVED_all_versions_retained.
  * New column full_row_duplicate_later_copy in rainfall_csv_source_rows.csv (flag only).
- scripts/data_audit.py: UNCHANGED. All other files unchanged.

## Rainfall CSV: REAL findings (USER-REPORTED from running the audit)
File: Datasets/raw/rainfall/Chennai Daily Rainfall 1991–2023.csv
- 21,416 rows x 4 columns (District, Station, Rainfall, Date); 1 district (Chennai);
  62 stations.
- Date format DD-MM-YYYY (dayfirst=True): 21,416 parsed / 0 unparseable.
- Verified date range (pipeline): 1993-03-03 .. 2023-12-26.
  The earlier values 1993-01-08 / 2023-12-12 were stale/incorrect and must NOT be used.
  (Cause of the earlier disagreement not determined. Suggested independent cross-check:
  python -c "import pandas as pd,glob; f=glob.glob('Datasets/raw/rainfall/*.csv')[0]; d=pd.to_datetime(pd.read_csv(f,dtype=str)['Date'],dayfirst=True); print(d.min().date(), d.max().date())")
- File name says 1991 but no 1991-1992 data were found; do not assume they exist.
- Earlier "12,949 invalid dates" / "13,488 duplicates" were an audit-parser artefact, NOT a
  property of the source CSV.
- Duplicates (all rows retained; nothing dropped):
  | metric | value |
  |---|---|
  | duplicate_full_rows (all 4 raw columns identical to earlier row) | 1,361 |
  | duplicate_station_date_keys | 1,397 |
  | true_duplicate_station_date_rows (extra rows) | 1,447 |
  | exact_duplicate_keys | 1,311 |
  | exact_duplicate_extra_rows | 1,358 |
  | conflicting_keys | 86 |
  | rows_in_conflicting_keys | 175 |
  | conflicting_extra_rows | 89 |
  | max abs rainfall difference among conflicts (unit unverified) | 202.1 |
  | median abs rainfall difference among conflicts | 5.5 |
  Arithmetic cross-check: 1311 + 86 = 1397 keys; 1358 + 89 = 1447 extra rows.
  OPEN: duplicate_full_rows (1361) > exact_duplicate_extra_rows (1358). Hypothesis: the 3
  extra are full-row duplicates inside conflicting keys. The metric
  full_row_duplicates_inside_conflicting_keys (new) will confirm or refute. Not yet run.
- Rainfall stats (UNIT UNVERIFIED): mean 18.279057, std 28.925106, min 0.01, 25% 2.0,
  50% 7.4, 75% 22.5, max 348.3.
- Station name-hint matches (TEXT ONLY, not geography): Anna University, MGR Nagar,
  Sholinganallur, Alandur, Adyar, Perungudi, Zone 13 Adyar, Zone 13 Adyar Eco Park,
  Zone 14 Perungudi, Zone 15 Sholinganallur, Zone 12 Meenambakkam, etc.
  No claim is made that any station lies in Ward 177; no coordinates/GIS metadata exist.

## Rainfall audit policy (implemented)
- Raw Date string kept as date_raw; parsed_date from dayfirst=True; strict regex cross-check.
- Duplicate key = raw Station string + parsed date. exact = identical District+Rainfall in
  all versions; conflicting = versions differ. ALL versions retained; no value chosen;
  no automatic resolution. Resolving conflicts is a future, explicit decision.
- In-memory tables use datetime.date for first/last dates; CSVs contain ISO strings.
- Stations are NOT merged with RTFF. Rainfall CSV has NO canonical file yet.

## Files
Phase 1 original: scripts/pipeline_common.py, rtff_arg_processor.py,
  rtff_reservoir_processor.py, build_canonical_dataset.py, data_audit.py;
  tests/test_pipeline.py + fixtures (arg_ok, arg_bad, reservoir).
Rainfall step: scripts/rainfall_csv_processor.py (MODIFIED round 2),
  tests/test_rainfall_csv.py (MODIFIED round 2),
  tests/fixtures/rainfall/sample.csv, tests/fixtures/rainfall_bad_date/bad.csv.
requirements.txt: pandas>=2.0, pytest>=7.0.
Untouched: scripts/rtff_scraper.py, scripts/rtff_velachery_collector.py, Datasets/raw/*.
Obsolete: Datasets/metadata/rainfall_csv_schema.csv (no longer produced; may be deleted).

## Commands
python -m unittest discover -s tests -v        (or: pytest -v)
python scripts/data_audit.py --rainfall
python scripts/data_audit.py [--summary|--arg|--reservoir|--canonical|--stations|--provenance]

## Outputs (processed / metadata)
Datasets/processed/: rtff_arg_hourly_source.csv, rtff_arg_day_records.csv,
  rtff_reservoir_daily.csv, canonical_rainfall.csv, canonical_reservoir.csv,
  rainfall_csv_source_rows.csv
Datasets/metadata/: arg_data_quality.csv, arg_issues.csv, reservoir_data_quality.csv,
  reservoir_issues.csv, stations.csv, data_sources.csv, canonical_build_report.csv,
  rainfall_csv_quality.csv, rainfall_csv_duplicate_report.csv, rainfall_csv_conflicts.csv,
  rainfall_csv_station_coverage.csv, rainfall_csv_yearly.csv, rainfall_csv_station_year.csv
(Rainfall outputs were produced in the user's earlier real run, except the two files new in
round 2. RTFF outputs: real-run status not reported.)

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
- Rainfall CSV: 1,447 duplicate Station+Date extra rows, of which 89 sit in 86 conflicting
  keys (largest disagreement 202.1, unit unverified). Unresolved by design.

## Unresolved questions (DO NOT assume)
1. ARG hour-label semantics (order h09_30..h08_30; interval start/end/instant; whether
   h00_30..h08_30 belong to date_val or date_val+1). No final timestamp built.
2. ARG units (rainfall_value, dailyrainfall) unverified; no "mm" label used.
3. Relation of dailyrainfall to hourly sum unverified (info column only).
4. Reservoir units for storage / inflow_total / outflow_total unverified.
5. Rainfall CSV unit unverified.
6. Station metadata: _meta/ and station_manifest.csv schemas not seen when code written;
   ALIASES in data_audit.py are guesses. Verify against "[STATIONS] metadata keys seen".
7. RTFF tile filename pattern unknown; tile ranges may fall back to record dates.
8. acquisition_date null everywhere (acquisition_log.csv not parsed).
9. Which rainfall-CSV stations are geographically relevant to Ward 177: UNDECIDED;
   needs coordinates/metadata or an explicit documented rule.
10. Whether station-name variants (case/whitespace) exist among the 62: see
    unique_stations_raw vs unique_stations_normalized in rainfall_csv_quality.csv.
11. How (or whether) the 86 conflicting Station+Date keys will be resolved: undecided.
12. Why the earlier ad-hoc audit reported 1993-01-08 / 2023-12-12: undetermined.
13. Reason for duplicate_full_rows (1361) > exact_duplicate_extra_rows (1358): hypothesis
    only, pending the new metric.

## Next recommended task
1. Replace the three files above; run the full test suite and
   `python scripts/data_audit.py --rainfall`.
2. Paste back: unittest summary; the [RAINFALL] block (any "check NOT satisfied" lines,
   the duplicate report table, full_row_duplicates_inside_conflicting_keys);
   head of rainfall_csv_conflicts.csv; the result of the pandas min/max cross-check.
3. Update this file with REAL results.
4. Then run `python scripts/data_audit.py` (full) and paste the ARG/reservoir quality tables,
   [STATIONS] output, 2-3 RTFF tile filenames and the _meta/ listing.
5. After that: rainfall-station selection rule for Ward 177, ARG hour-semantics
   investigation, KML audit. Knowledge-base identity audit is a separate task.


   ## Phase 2 — Predictive Risk Inference

**Status: code delivered, NOT YET RUN by the user. Nothing below is marked tested until run.**

- Model implementation: written (scripts/risk_config.py, risk_data.py, risk_features.py,
  risk_inference.py, risk_plots.py, train_risk_model.py, replay_risk.py; tests/test_risk_model.py).
  Execution status: [ ] not run
- Tests: 11 offline tests in tests/test_risk_model.py. Result: [ ] not run  (fill in: N passed / N failed)
- Real training: [ ] not run
- Dataset used: Datasets/processed/rtff_arg_day_records.csv (RTFF; 4 stations; window 2022-04-01..2025-05-10).
  Column detection verified with `--inspect`: [ ] not done. Detected columns: [fill in]
- Exact training period: [fill in from console / model_metadata.json split_info]
- Target definition: high_rainfall_stress(t+1) = 1 if local_rainfall_mean on day t+1 >= 90th percentile
  of observed local_rainfall_mean over days up to the last TRAIN target date. Internal research proxy,
  NOT flood occurrence, NOT an official warning.
- Split: chronological 70/15/15 over eligible rows (by target date). Dates: [fill in]
- Metrics (TEST, proxy target): [fill in from Datasets/metadata/model_evaluation.json]
- Artifacts: models/xgboost_model.json, models/feature_columns.json, models/model_metadata.json,
  models/baseline_logreg.joblib, Datasets/processed/model_features.csv,
  Datasets/processed/model_predictions.csv, Datasets/metadata/model_evaluation.json,
  Datasets/metadata/model_card.md, artifacts/plots/*.png  (exist: [ ] unverified)
- Replay: scripts/replay_risk.py written; run status: [ ] not run
- Known limitations: proxy target only; ~3 years of data; few positives in val/test (high variance);
  local mean mixes varying station sets; hourly RTFF labels unverified and unused; reservoir features
  excluded (units/schema unverified); historical CSV not used (86 unresolved conflicting keys);
  rolling sums under-count over partially observed windows; severity and trigger thresholds are
  internal research settings.
- Not implemented (by design): persistent trigger, RAG, ChromaDB, Ollama, FastAPI, React, KML fusion, LSTM.
- Next task: review real metrics/plots, then Phase 3 persistence trigger on the replay output.


## Phase 3 — Persistence Trigger

**Status:** implemented; NOT yet verified in the project environment. Items below are
marked tested only after the user runs the commands on the real repository.

### Implementation
- [x] `scripts/risk_trigger.py` written (evaluate_trigger, process_prediction_history,
      load_prediction_history, replay_with_trigger, write_trigger_config, CLI)
- [x] `tests/test_risk_trigger.py` written (17 tests, tiny synthetic fixtures, no network / RTFF access)
- [ ] `scripts/replay_risk.py` `--with-trigger` flag integrated (needs the real file; the trigger is
      already replayable via `python scripts/risk_trigger.py --replay`)
- [x] `tests/test_risk_model.py`: unit-claim file-count assertion updated 7 -> 8
      (the new `risk_trigger.py` matches the `risk_*.py` glob)

### Tests
- [ ] `python -m pytest tests/test_risk_trigger.py -v` — pending user run
- [ ] `python -m pytest tests/test_risk_model.py -v` — pending user re-run (count change)
- [ ] `python -m unittest discover -s tests -v` — pending user run

### Configured thresholds (internal, in `Datasets/metadata/trigger_config.json`)
- HIGH: risk_score >= 0.70
- CRITICAL: risk_score >= 0.85
- Trigger threshold: risk_score >= 0.70
- Score source: `p_xgboost` from `Datasets/processed/model_predictions.csv`

### Persistence rule
- `trigger_active = true` once risk_score >= 0.70 on 3 consecutive observed daily rows
  (HIGH and CRITICAL both count).
- Count resets to 0 on a day below 0.70, a day with no score, or a calendar gap > 1 day.
- Missing dates are never filled in or turned into observations.
- Input must be strictly increasing by date; unsorted or duplicate dates are rejected.
- Scores outside [0, 1] are rejected.

### Generated output (created when the user runs `python scripts/risk_trigger.py`)
- `Datasets/processed/risk_trigger_history.csv`
  (date, target_date, risk_score, severity, consecutive_high_count, trigger_active, trigger_reason)
- `Datasets/metadata/trigger_config.json`
- Not yet generated.

### Limitations
- Internal research trigger on a model-output proxy (`high_rainfall_stress` at t+1). It is not
  flood occurrence, not a flood prediction, and has no official status.
- Upstream model metrics are unchanged (ROC-AUC 0.7224, PR-AUC 0.2455, precision 0.100,
  recall 0.364, F1 0.157, Brier 0.059; only 11 positive test days, so high variance).
- `model_predictions.csv` contains train/val/test rows; trigger history over train rows reflects
  in-sample scores and should not be read as out-of-sample behaviour.
- The 0.70 / 0.85 thresholds and 3-day persistence are defaults, not tuned or validated values.
- 0.70 is high relative to how the model scores: precision at its operating point is low, so the
  trigger may rarely or never activate on real data. Check this before reading anything into it.
- No reservoir data and no KML data are used.

### Next task
Run the Phase 3 commands, record the real results and generated-output summary here, then
start the RAG retrieval layer (separate task).