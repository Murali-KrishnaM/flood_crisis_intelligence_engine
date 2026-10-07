from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from data_service import ACTIVE_STATIONS


class RiskEngine:
    def __init__(self, data_service):
        self.ds = data_service
        self.processed = data_service.processed
        self.metadata = data_service.metadata
        self._pred = None
        self._trigger = None
        self._cfg = None

    def _load_predictions(self) -> pd.DataFrame:
        if self._pred is None:
            path = self.processed / "model_predictions.csv"
            if path.exists():
                try:
                    df = pd.read_csv(path)
                except Exception:
                    df = pd.DataFrame()
            else:
                df = pd.DataFrame()
            if not df.empty and "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"], errors="coerce")
                df = df.dropna(subset=["date"]).sort_values("date")
            self._pred = df
        return self._pred.copy()

    def _load_trigger(self) -> pd.DataFrame:
        if self._trigger is None:
            path = self.processed / "risk_trigger_history.csv"
            if path.exists():
                try:
                    df = pd.read_csv(path)
                except Exception:
                    df = pd.DataFrame()
            else:
                df = pd.DataFrame()
            if not df.empty and "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"], errors="coerce")
                df = df.dropna(subset=["date"]).sort_values("date")
            self._trigger = df
        return self._trigger.copy()

    def _config(self) -> dict[str, Any]:
        if self._cfg is None:
            path = self.metadata / "trigger_config.json"
            try:
                self._cfg = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self._cfg = {"high_threshold": 0.10, "critical_threshold": 0.18, "trigger_threshold": 0.15, "persistence_days": 3}
        return self._cfg

    def _severity(self, score: float | None) -> str:
        if score is None:
            return "NO DATA"
        c = self._config()
        high = float(c.get("high_threshold", 0.10))
        critical = float(c.get("critical_threshold", 0.18))
        if score >= critical:
            return "CRITICAL"
        if score >= high:
            return "HIGH"
        return "LOW"

    def _row(self, row: pd.Series) -> dict[str, Any]:
        score = row.get("p_xgboost")
        score = None if pd.isna(score) else max(0.0, min(1.0, float(score)))
        return {
            "date": row["date"].strftime("%Y-%m-%d"),
            "risk_score": score,
            "severity": self._severity(score),
        }

    def history(self, days: int = 90) -> dict[str, Any]:
        trig = self._load_trigger()
        pred = self._load_predictions()
        if not trig.empty and "risk_score" in trig.columns:
            df = trig.copy()
            df["risk_score"] = pd.to_numeric(df["risk_score"], errors="coerce")
            df = df.tail(days)
        elif not pred.empty and "p_xgboost" in pred.columns:
            df = pred[["date", "p_xgboost"]].rename(columns={"p_xgboost": "risk_score"}).tail(days)
        else:
            return {"dates": [], "scores": [], "severities": [], "trigger_active": []}
        return {
            "dates": [d.strftime("%Y-%m-%d") for d in df["date"]],
            "scores": [None if pd.isna(v) else float(v) for v in df["risk_score"]],
            "severities": [self._severity(None if pd.isna(v) else float(v)) for v in df["risk_score"]],
            "trigger_active": [bool(v) if not pd.isna(v) else False for v in df.get("trigger_active", pd.Series(False, index=df.index))],
        }

    def current_risk(self) -> dict[str, Any]:
        trig = self._load_trigger()
        pred = self._load_predictions()
        if not trig.empty and "risk_score" in trig.columns:
            row = trig.iloc[-1]
            score = None if pd.isna(row["risk_score"]) else float(row["risk_score"])
            active = bool(row.get("trigger_active", False))
            count = int(row.get("consecutive_high_count", 0) or 0)
            reason = str(row.get("trigger_reason", ""))
            date = row["date"].strftime("%Y-%m-%d")
            source = "risk_trigger_history.csv"
        elif not pred.empty and "p_xgboost" in pred.columns:
            row = pred.iloc[-1]
            score = None if pd.isna(row["p_xgboost"]) else float(row["p_xgboost"])
            active = False
            count = 0
            reason = "Trigger history unavailable"
            date = row["date"].strftime("%Y-%m-%d")
            source = "model_predictions.csv"
        else:
            return {"risk_score": None, "severity": "NO DATA", "date": None, "trigger_active": False, "consecutive_high_count": 0, "trigger_reason": "No prediction data", "source": "unavailable", "model_mode": "unavailable"}
        return {"risk_score": score, "severity": self._severity(score), "date": date, "trigger_active": active, "consecutive_high_count": count, "trigger_reason": reason, "source": source, "model_mode": "XGBoost prediction"}

    def replay(self, date: str | None = None) -> dict[str, Any]:
        trig = self._load_trigger()
        if trig.empty:
            dates: list[str] = []
            step = {"date": date, "risk_score": None, "severity": "NO DATA", "trigger_active": False, "consecutive_high_count": 0, "stations": self.ds.station_rows_for_date(date), "reservoirs": self.ds.reservoirs(date=date)}
            return {"dates": dates, "trigger_active_days": 0, **step} if date else {"dates": dates, "trigger_active_days": 0}
        dates = [d.strftime("%Y-%m-%d") for d in trig["date"]]
        active = int(trig.get("trigger_active", pd.Series(False, index=trig.index)).fillna(False).astype(bool).sum())
        if date is None:
            return {"dates": dates, "trigger_active_days": active}
        target = pd.to_datetime(date, errors="coerce")
        if pd.isna(target):
            return {"dates": dates, "trigger_active_days": active, "date": date, "error": "invalid date"}
        exact = trig[trig["date"] == target]
        if exact.empty:
            prior = trig[trig["date"] <= target]
            if prior.empty:
                row = None
            else:
                row = prior.iloc[-1]
        else:
            row = exact.iloc[-1]
        if row is None:
            return {"dates": dates, "trigger_active_days": active, "date": date, "risk_score": None, "severity": "NO DATA", "trigger_active": False, "consecutive_high_count": 0, "stations": self.ds.station_rows_for_date(date), "reservoirs": self.ds.reservoirs(date=date)}
        score = None if pd.isna(row.get("risk_score")) else float(row["risk_score"])
        return {
            "dates": dates,
            "trigger_active_days": active,
            "date": row["date"].strftime("%Y-%m-%d"),
            "risk_score": score,
            "severity": self._severity(score),
            "trigger_active": bool(row.get("trigger_active", False)),
            "consecutive_high_count": int(row.get("consecutive_high_count", 0) or 0),
            "trigger_reason": str(row.get("trigger_reason", "")),
            "stations": self.ds.station_rows_for_date(row["date"].strftime("%Y-%m-%d")),
            "reservoirs": self.ds.reservoirs(date=row["date"].strftime("%Y-%m-%d")),
        }
