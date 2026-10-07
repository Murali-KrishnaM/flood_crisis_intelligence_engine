# Crisis Intelligence Engine (CIE)

A research prototype for **Ward 177, Velachery, Chennai** that combines rainfall data processing, a machine-learning risk score, a persistence trigger, and a local web dashboard.

> **Important:** The current system is a research prototype. The risk score represents **next-day high local rainfall stress**, not flood occurrence, not a flood probability, and not an official warning.

---

## 1. What is completed?

### ✅ Data processing
- Rainfall source data is parsed and audited.
- Missing values are preserved instead of being silently changed to zero.
- Duplicate/conflicting records are checked.
- Dates and source provenance are validated.
- Reservoir source records are also parsed and checked.

### ✅ ML risk engine
- Daily station rainfall data is converted into a modelling panel.
- Features include recent rainfall behaviour such as:
  - current/recent rainfall
  - calendar-day lag values
  - rolling rainfall statistics
  - rainy-day counts
  - missing-data indicators
  - station coverage information
- The target is a **future high-rainfall-stress condition**.
- Models include a dummy baseline, logistic regression, and **XGBoost**.
- XGBoost is the selected main model.
- Model predictions are stored in:
  `Datasets/processed/model_predictions.csv`

### ✅ Persistence trigger
The trigger checks whether the model score stays above the configured threshold for consecutive observed days.

Current demo configuration:
- High threshold: `0.10`
- Critical threshold: `0.18`
- Trigger threshold: `0.15`
- Persistence: `3` consecutive observed days

Outputs:
- `Datasets/processed/risk_trigger_history.csv`
- `Datasets/metadata/trigger_config.json`

### ✅ Dashboard + Flask backend
The current visible prototype provides:
- Current research risk score
- Risk history chart
- Rainfall history chart
- Station observations
- Reservoir observations
- Persistence-trigger state
- Historical replay
- Policy corpus/status section
- System health
- Model evidence/summary

Run it locally with Flask and open:
`http://127.0.0.1:5000`

---

## 2. What is NOT finished yet?

### 🚧 RAG / policy intelligence
The dashboard has a **Policy Intelligence** section, but the full Retrieval-Augmented Generation pipeline is not yet implemented.

Planned flow:
`Government documents → text extraction → chunks → embeddings → vector search → relevant policy context → answer generation`

### 🚧 Production deployment
The current Flask server is a development/demo server. It is not a production deployment.

### 🚧 Real-time/live operation
The current dashboard primarily demonstrates the processed historical dataset and replay workflow. It is not yet connected to a live rainfall feed or official emergency-warning system.

---

# 3. Commands for the demonstration

Run these from the project root while the virtual environment is active.

## A. Run the main risk-model tests

```powershell
python -m pytest tests/test_risk_model.py -v
```

### What this proves
The ML/data-processing logic passes its dedicated tests.

### What it says
> "These tests verify that our feature engineering, missing-data handling, chronological split, target generation, leakage checks, and inference schema behave as intended."

Expected ending:

```text
17 passed
```

---

## B. Run the complete existing test suite

```powershell
python -m unittest discover -s tests -v
```

### What this proves
The earlier ingestion, rainfall-audit, reservoir, and pipeline tests are passing.

Expected ending:

```text
Ran 33 tests
OK
```

### What it says
> "This is our broader regression test suite covering the data-ingestion and audit modules."

---

## C. Run the dashboard tests

```powershell
python -m pytest tests/test_dashboard.py -v
```

### What this proves
The Flask page, API responses, static files, reservoir filtering, and dashboard safety checks work.

Expected ending:

```text
5 passed
```

### What to say
> "These tests make sure the dashboard can load its backend data and that the frontend is connected to the expected Flask APIs."

---

## D. Start the dashboard

```powershell
python app.py
```

Expected:

```text
Running on http://127.0.0.1:5000
```

Open:

```text
http://127.0.0.1:5000
```

### What to demonstrate

1. **Research Risk Index**
   - Shows the XGBoost model's current research score.
   - Explain that it represents next-day high local rainfall stress.

2. **Persistence Trigger**
   - Shows whether the score has remained above the configured trigger threshold for 3 consecutive observed days.
   - Click **"Jump to first recorded trigger"** to demonstrate a historical event.

3. **Risk History**
   - Shows how the model score changes over time.

4. **Station Observations**
   - Shows rainfall values from the processed station data.

5. **Reservoir State**
   - Shows recorded reservoir observations without inventing physical units or official thresholds.

6. **Historical Replay**
   - Use Play / Next / slider to move through historical dates.
   - This is useful for demonstrating how the system would have behaved over time.

7. **System Health / Model Evidence**
   - Shows the state and basic evidence behind the dashboard.

---

# 4. Simple architecture to explain

```text
Source Data
    ↓
Data Processing & Audit
    ↓
Daily Rainfall Panel
    ↓
Feature Engineering
    ↓
XGBoost Risk Model
    ↓
Risk Score
    ↓
Persistence Trigger
    ↓
Flask Backend
    ↓
HTML / CSS / JavaScript Dashboard
```

The planned extension is:

```text
Government Policy Documents
    ↓
RAG Pipeline
    ↓
Policy-aware explanations / recommendations
    ↓
Dashboard
```

---

# 5. One-minute explanation for a non-technical reviewer

> "The system takes historical rainfall observations from multiple stations, cleans and validates them, and converts them into daily features. An XGBoost model then produces a research risk score for next-day high local rainfall stress. Instead of reacting to one high score, a persistence rule checks whether the condition continues for three observed days. Flask provides the backend and the dashboard presents the result in a simple visual interface. The current prototype demonstrates the data, ML, trigger, and dashboard layers. The next major module is the RAG-based policy intelligence layer, which will connect government documents to the system so that the risk signal can be accompanied by relevant policy information."

---

## 6. Be honest about the current limitations

Do **not** describe the current score as:
- "probability of flood"
- "flood prediction"
- "official warning"
- "government alert"

Use:

**"research risk score for next-day high local rainfall stress."**

This distinction matters because the model currently learns a rainfall-stress target, not actual flood occurrence.

---

## 7. Main files

```text
app.py
    Flask backend

templates/index.html
    Dashboard page

static/css/dashboard.css
    Dashboard styling

static/js/dashboard.js
    Dashboard behaviour and API calls

scripts/
    Data processing, model training, replay and trigger scripts

Datasets/processed/
    Cleaned/processed datasets and model outputs

Datasets/metadata/
    Model and trigger configuration/metadata

tests/
    Automated tests
```

---

## 8. Current project status

| Module | Status |
|---|---|
| Data ingestion & validation | ✅ Done |
| Rainfall processing/audit | ✅ Done |
| Reservoir processing | ✅ Done |
| Feature engineering | ✅ Done |
| ML risk model | ✅ Done |
| Risk prediction output | ✅ Done |
| Persistence trigger | ✅ Done |
| Flask backend | ✅ Done |
| Dashboard | ✅ Demo-ready |
| RAG policy intelligence | 🚧 Next |
| Live data integration | 🚧 Future |
| Production deployment | 🚧 Future |

**Current goal:** demonstrate the working research pipeline and dashboard, then complete the RAG/policy intelligence layer.
