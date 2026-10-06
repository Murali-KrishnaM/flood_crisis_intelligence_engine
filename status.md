# Crisis Intelligence Engine — Project Status

## Project

Crisis Intelligence Engine:
An Offline Predictive RAG Decision-Support System
for Ward 177, Velachery, Chennai

## Purpose

Build a genuinely functional near-realistic prototype that connects:

Real/traceable Chennai data
→ dynamic flood-risk inference
→ persistence trigger
→ jurisdiction context
→ official-document retrieval
→ local LLM generation
→ evidence verification
→ operator dashboard

The submitted paper and presentations describe the intended system.
They are NOT evidence that those components have already been implemented.

---

# Status Legend

- 🟢 Implemented and tested
- 🟡 Implemented but incomplete/unverified
- 🔴 Not implemented
- ⚠️ Blocked
- 🔵 Planned

---

# Current Repository State

The implementation repository is currently empty apart from:

- Datasets/raw/
- knowledge_base/
- status.md

No production code has been implemented yet.

---

# Architecture Status

| Component | Status | Notes |
|---|---|---|
| Data ingestion | 🔴 | |
| Data validation | 🔴 | |
| Data normalization | 🔴 | |
| Dynamic risk model | 🔴 | |
| XGBoost | 🔴 | |
| LSTM | 🔴 | |
| Spatial susceptibility | 🔴 | |
| Risk fusion | 🔴 | |
| Persistence trigger | 🔴 | |
| Jurisdiction resolver | 🔴 | |
| Policy corpus | 🟡 | Initial official PDFs downloaded |
| Embedding pipeline | 🔴 | |
| ChromaDB | 🔴 | |
| RAG retrieval | 🔴 | |
| Local LLM | 🔴 | |
| Claim verification | 🔴 | |
| FastAPI | 🔴 | |
| React dashboard | 🔴 | |
| Historical replay | 🔴 | |
| Three-node LAN deployment | 🔵 | |
| Automated tests | 🔴 | |
| Audit logging | 🔴 | |

---

# Data Collection

## RTFF

A separate scraper has already been developed and tested on one
RTFF ARG station (Anna University) outside the current repository.

The scraper has demonstrated:

- authenticated/session-aware historical access
- fixed 8-day historical request behaviour
- JSON data retrieval
- handling of empty tiles

Known RTFF data-quality concerns:

- missing dates exist
- individual hourly values may be null
- PDF export is unreliable
- hour-label semantics require verification
- rainfall unit semantics must be verified before final normalization
- some stations may have different historical coverage

The scraper must therefore preserve raw source values and must not
silently interpolate or fabricate missing observations.

---

# RTFF Target Stations

## Rainfall

- ARG0055 — IIT Madras, Guindy
- ARG0069 — Jerusalem Engineering College, Pallikaranai
- ARG0052 — Government High School, MGR Nagar
- ARG0075 — Tamil Nadu Bio-Diversity Board, Medavakkam

## Water Level

- AWLR0053 — Velachery Tank
- AWLR0052 — Narayanapuram Tank
- AWLR0050 — Velachery-Tambaram Main Road / Veerangal Odai

## Weather

- AWS0005 — KCG College of Technology, Karapakkam

These stations were selected because they provide geographically
relevant observations around the Velachery study area.

---

# Downloaded Datasets

## Rainfall

- Chennai Daily Rainfall 1991–2023 CSV
  Status: 🟡 Downloaded; schema inspection pending

## Flood Data

- Chennai Floods 2015 KML collection
  Status: 🟡 Downloaded; GIS inspection pending

- Chennai Flooding Data KML collection
  Status: 🟡 Downloaded; GIS inspection pending

## GIS

- GCC Stormwater Drain KML collection
  Status: 🟡 Downloaded; GIS inspection pending

---

# Policy Knowledge Base

## National

- NDMA Urban Flooding Guidelines 2010
  Status: 🟢 Downloaded

- NDMA Flood Management Guidelines 2008
  Status: 🟢 Downloaded

- National Disaster Management Plan 2019
  Status: 🔴 Pending usable official copy

- Current Disaster Management Act
  Status: 🔴 Pending usable official copy

## Tamil Nadu

- Tamil Nadu State Disaster Management Perspective Plan 2018–2030
  Status: 🟢 Downloaded

## Chennai

- GCC City Disaster Management Perspective Plan 2025
  Status: 🟢 Downloaded

- CCUDMA 2025 notification / G.O.Ms. No.236
  Status: 🟢 Downloaded

---

# Immediate Objective

Obtain sufficient reliable, geographically relevant RTFF data around
Velachery to determine whether RTFF can serve as a primary local
observation source.

Do NOT scrape the entire Chennai station network unless later
evidence shows it is necessary.

---

# Immediate Task

1. Test targeted RTFF ARG stations.
2. Validate coverage and data quality.
3. Store each station separately.
4. Produce station-level manifests and acquisition logs.
5. Test selected AWLR stations if ARG collection is successful.
6. Test AWS0005 after ARG/AWLR.
7. Do not merge datasets yet.

---

# Data Rules

- Original source data must remain unchanged.
- Do not silently fill missing values.
- Do not invent timestamps.
- Do not invent station metadata.
- Do not claim a unit/semantic interpretation without evidence.
- Do not merge datasets before their schemas and temporal semantics
  are understood.
- Maintain provenance for every downloaded source.

---

# Tomorrow Review MVP

Target a functioning vertical slice:

Data
→ Risk
→ Trigger
→ Policy Retrieval
→ Local LLM
→ Verification
→ Dashboard

The first implementation may use a simpler validated predictive
baseline before LSTM is introduced.

---

# Known Limitations

- No trained model currently exists.
- No validated flood-risk label currently exists.
- No RAG implementation currently exists.
- No local LLM integration currently exists.
- No dashboard currently exists.
- No three-node deployment currently exists.
- No quantitative project performance claims are currently valid.

---

# Current Truth

The project is at the beginning of implementation.

Any component marked 🔴 must not be described as already implemented
in reports, presentations, demonstrations, or documentation.

---

## RTFF station mapping diagnostic

- Mapping diagnostic executed against the live RTFF page.
- No historical bulk download was performed.
- See collector.log for the live selector inventory and target matches.
