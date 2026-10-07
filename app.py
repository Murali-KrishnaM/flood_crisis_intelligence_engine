from __future__ import annotations

from pathlib import Path
from flask import Flask, jsonify, render_template, request

from data_service import DataService
from policy_service import PolicyService
from risk_engine import RiskEngine

ROOT = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))

service = DataService(ROOT)
risk = RiskEngine(service)
policy = PolicyService(ROOT)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/summary")
def summary():
    return jsonify(service.summary())


@app.get("/api/current-risk")
def current_risk():
    return jsonify(risk.current_risk())


@app.get("/api/rainfall")
def rainfall():
    days = request.args.get("days", default=30, type=int)
    days = max(1, min(days, 366))
    return jsonify(service.rainfall_series(days))


@app.get("/api/risk-history")
def risk_history():
    days = request.args.get("days", default=90, type=int)
    days = max(1, min(days, 2000))
    return jsonify(risk.history(days))


@app.get("/api/reservoirs")
def reservoirs():
    date = request.args.get("date")
    return jsonify(service.reservoirs(date=date))


@app.get("/api/replay")
def replay():
    date = request.args.get("date")
    return jsonify(risk.replay(date=date))


@app.get("/api/policy-status")
def policy_status():
    return jsonify(policy.status())


@app.get("/api/system-status")
def system_status():
    return jsonify(service.system_status())


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
