from __future__ import annotations

from pathlib import Path

import pytest

from app import app


@pytest.fixture()
def client():
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


def test_index(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"Crisis Intelligence Engine" in r.data


def test_api_json(client):
    for path in [
        "/api/summary",
        "/api/current-risk",
        "/api/rainfall",
        "/api/risk-history",
        "/api/reservoirs",
        "/api/replay",
        "/api/policy-status",
        "/api/system-status",
    ]:
        r = client.get(path)
        assert r.status_code == 200
        assert r.is_json


def test_static_assets(client):
    assert client.get("/static/css/dashboard.css").status_code == 200
    assert client.get("/static/js/dashboard.js").status_code == 200


def test_replay_reservoirs_are_filtered(client):
    payload = client.get("/api/replay?date=2024-10-10").get_json()
    ids = {x["reservoir_id"] for x in payload.get("reservoirs", [])}
    assert "_meta" not in ids
    assert ids.issubset({"RD001", "Cho001", "Po001", "TK-001", "TNCH-07-T0726"})


def test_dashboard_source_has_no_unverified_unit_claims():
    root = Path(__file__).resolve().parents[1]
    files = [
        root / "app.py",
        root / "data_service.py",
        root / "risk_engine.py",
        root / "policy_service.py",
        root / "templates" / "index.html",
        root / "static" / "css" / "dashboard.css",
        root / "static" / "js" / "dashboard.js",
    ]
    bad = ("millimet", "mm/day", "mm/hour")
    for path in files:
        text = path.read_text(encoding="utf-8").lower()
        assert not any(token in text for token in bad), path
