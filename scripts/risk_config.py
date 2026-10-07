"""Central configuration for Phase 2 (predictive risk inference).

All numeric thresholds here are INTERNAL RESEARCH SETTINGS.
They are NOT government-defined and NOT official flood-warning levels.

RAINFALL UNITS ARE UNVERIFIED. Every rainfall quantity and rainfall threshold in
this project is expressed in the source rainfall measurement units; the physical
unit has not been independently verified.
"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "Datasets" / "processed"
METADATA_DIR = PROJECT_ROOT / "Datasets" / "metadata"
MODELS_DIR = PROJECT_ROOT / "models"
PLOTS_DIR = PROJECT_ROOT / "artifacts" / "plots"
REPLAY_DIR = PROJECT_ROOT / "artifacts" / "replay"

# Primary model source: RTFF-derived daily station records (not the historical CSV).
DEFAULT_RAINFALL_SOURCE = PROCESSED_DIR / "rtff_arg_day_records.csv"

PROJECT_STATIONS = [
    "Anna_University",
    "CHN_TARAMANI",
    "CHN13Z178WG",
    "ARG_NIOT_Pallikaranai",
]
WINDOW_START = "2022-04-01"   # common RTFF window
WINDOW_END = "2025-05-10"

# Wording used wherever a rainfall threshold or quantity is reported.
UNIT_NOTE = ("source rainfall measurement units; "
             "physical unit not yet independently verified")

# Feature rules
# A "rainy day" = local mean daily rainfall >= this value, in source rainfall units.
# The numeric value is an internal research setting; its physical meaning depends on
# the still-unverified source unit.
RAINY_DAY_THRESHOLD = 2.5
MIN_OBS_FRACTION = 0.6        # rolling feature needs >= ceil(window * 0.6) observed days

# Target
TARGET_NAME = "high_rainfall_stress"
HORIZON_DAYS = 1              # features through day t -> target on day t+1
STRESS_PERCENTILE = 0.90      # percentile of local_rainfall_mean, TRAIN PERIOD ONLY

# Chronological split (by target date)
TRAIN_FRAC = 0.70
VAL_FRAC = 0.15              # test = remainder (0.15)

# Internal research severity mapping: level -> minimum risk_score. "LOW" is implicit below the lowest.
SEVERITY_THRESHOLDS = {"MODERATE": 0.30, "HIGH": 0.60}
TRIGGER_CANDIDATE_THRESHOLD = 0.60   # replay emits trigger_candidate = risk_score >= this

RANDOM_SEED = 42

DISCLAIMER = (
    "Internal research risk score for the high_rainfall_stress proxy. "
    "Not an official flood warning or prediction."
)