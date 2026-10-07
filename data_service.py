from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


ACTIVE_STATIONS = [
    "Anna_University",
    "CHN_TARAMANI",
    "CHN13Z178WG",
    "ARG_NIOT_Pallikaranai",
]
ACTIVE_RESERVOIRS = ["RD001", "Cho001", "Po001", "TK-001", "TNCH-07-T0726"]
RESERVOIR_NAMES = {
    "RD001": "Red Hills",
    "Cho001": "Cholavaram",
    "Po001": "Poondi",
    "TK-001": "Thervoy Kandigal",
    "TNCH-07-T0726": "Chembarambakkam",
}


class DataService:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.processed = self.root / "Datasets" / "processed"
        self.metadata = self.root / "Datasets" / "metadata"
        self.raw = self.root / "Datasets" / "raw"
        self._cache: dict[str, Any] = {}

    def _read_csv(self, name: str, required: list[str] | None = None) -> pd.DataFrame:
        key = f"csv:{name}"
        if key in self._cache:
            return self._cache[key].copy()
        path = self.processed / name
        if not path.exists():
            return pd.DataFrame(columns=required or [])
        try:
            df = pd.read_csv(path)
        except Exception:
            return pd.DataFrame(columns=required or [])
        if required:
            for col in required:
                if col not in df.columns:
                    df[col] = pd.NA
        self._cache[key] = df.copy()
        return df

    @staticmethod
    def _date_col(df: pd.DataFrame) -> str | None:
        for c in ("parsed_date", "date", "observation_date", "source_date"):
            if c in df.columns:
                return c
        return None

    @staticmethod
    def _rain_col(df: pd.DataFrame) -> str | None:
        for c in ("source_daily_rainfall_raw", "daily_rainfall", "rainfall_value", "Rainfall"):
            if c in df.columns:
                return c
        return None

    def _arg_days(self) -> pd.DataFrame:
        df = self._read_csv("rtff_arg_day_records.csv")
        if df.empty:
            return pd.DataFrame(columns=["station_id", "date", "rainfall_value"])
        station_col = "station_id" if "station_id" in df.columns else None
        date_col = self._date_col(df)
        rain_col = self._rain_col(df)
        if not all([station_col, date_col, rain_col]):
            return pd.DataFrame(columns=["station_id", "date", "rainfall_value"])
        out = df[[station_col, date_col, rain_col]].copy()
        out.columns = ["station_id", "date", "rainfall_value"]
        out = out[out["station_id"].astype(str).isin(ACTIVE_STATIONS)]
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
        out["rainfall_value"] = pd.to_numeric(out["rainfall_value"], errors="coerce")
        out = out.dropna(subset=["date"]).sort_values(["date", "station_id"])
        return out

    def station_rows_for_date(self, date: str | None = None) -> list[dict[str, Any]]:
        df = self._arg_days()
        if df.empty:
            return []
        if date:
            target = pd.to_datetime(date, errors="coerce")
            if pd.isna(target):
                return []
            use = df[df["date"] <= target]
        else:
            use = df
        latest_date = use["date"].max() if not use.empty else None
        if latest_date is None:
            return []
        latest = use[use["date"] == latest_date]
        rows: list[dict[str, Any]] = []
        for station in ACTIVE_STATIONS:
            r = latest[latest["station_id"] == station]
            if r.empty:
                rows.append({"station_id": station, "station_name": self._station_name(station), "date": None, "rainfall_value": None, "available": False})
            else:
                val = r.iloc[-1]["rainfall_value"]
                rows.append({
                    "station_id": station,
                    "station_name": self._station_name(station),
                    "date": latest_date.strftime("%Y-%m-%d"),
                    "rainfall_value": None if pd.isna(val) else float(val),
                    "available": not pd.isna(val),
                })
        return rows

    def _station_name(self, station_id: str) -> str:
        mapping = {
            "Anna_University": "Anna University",
            "CHN_TARAMANI": "Taramani",
            "CHN13Z178WG": "Velachery W178",
            "ARG_NIOT_Pallikaranai": "NIOT Pallikaranai",
        }
        return mapping.get(station_id, station_id)

    def rainfall_series(self, days: int = 30) -> dict[str, Any]:
        df = self._arg_days()
        if df.empty:
            return {"dates": [], "values": [], "station_count": 0, "source": "RTFF-derived processed data"}
        panel = df.pivot_table(index="date", columns="station_id", values="rainfall_value", aggfunc="last")
        panel = panel.reindex(columns=ACTIVE_STATIONS)
        panel["available_count"] = panel.notna().sum(axis=1)
        panel["aggregate"] = panel[ACTIVE_STATIONS].mean(axis=1, skipna=True)
        panel = panel.tail(days)
        return {
            "dates": [d.strftime("%Y-%m-%d") for d in panel.index],
            "values": [None if pd.isna(v) else float(v) for v in panel["aggregate"]],
            "station_count": len(ACTIVE_STATIONS),
            "source": "RTFF-derived processed data",
        }

    def _reservoir_df(self) -> pd.DataFrame:
        df = self._read_csv("canonical_reservoir.csv")
        if df.empty:
            return pd.DataFrame(columns=["reservoir_id", "date", "time", "waterlevel", "storage", "inflow_total", "outflow_total"])
        rid = "reservoir_id" if "reservoir_id" in df.columns else "tankid" if "tankid" in df.columns else None
        date_col = self._date_col(df)
        if not rid or not date_col:
            return pd.DataFrame()
        out = df.copy()
        out = out[~out[rid].astype(str).isin(["_meta", "meta"])]
        out = out[out[rid].astype(str).isin(ACTIVE_RESERVOIRS)]
        out["reservoir_id"] = out[rid].astype(str)
        out["date"] = pd.to_datetime(out[date_col], errors="coerce")
        if "time" not in out.columns:
            out["time"] = pd.NA
        for c in ("waterlevel", "storage", "inflow_total", "outflow_total"):
            if c not in out.columns:
                out[c] = pd.NA
            out[c] = pd.to_numeric(out[c], errors="coerce")
        return out.dropna(subset=["date"]).sort_values(["date", "reservoir_id"])

    def reservoirs(self, date: str | None = None) -> list[dict[str, Any]]:
        df = self._reservoir_df()
        if df.empty:
            return []
        if date:
            target = pd.to_datetime(date, errors="coerce")
            if pd.isna(target):
                return []
            df = df[df["date"] <= target]
        rows = []
        for rid in ACTIVE_RESERVOIRS:
            r = df[df["reservoir_id"] == rid]
            if r.empty:
                continue
            latest = r.iloc[-1]
            rows.append({
                "reservoir_id": rid,
                "name": RESERVOIR_NAMES.get(rid, rid),
                "date": latest["date"].strftime("%Y-%m-%d"),
                "time": None if pd.isna(latest["time"]) else str(latest["time"]),
                "waterlevel": self._num(latest["waterlevel"]),
                "storage": self._num(latest["storage"]),
                "inflow_total": self._num(latest["inflow_total"]),
                "outflow_total": self._num(latest["outflow_total"]),
            })
        return rows

    @staticmethod
    def _num(v: Any) -> float | None:
        return None if pd.isna(v) else float(v)

    def summary(self) -> dict[str, Any]:
        arg = self._arg_days()
        res = self._reservoir_df()
        return {
            "study_area": "Ward 177 / Velachery, Chennai",
            "rainfall_stations": [self._station_name(x) for x in ACTIVE_STATIONS],
            "station_count": len(ACTIVE_STATIONS),
            "rainfall_rows": int(len(arg)),
            "rainfall_first_date": None if arg.empty else arg["date"].min().strftime("%Y-%m-%d"),
            "rainfall_last_date": None if arg.empty else arg["date"].max().strftime("%Y-%m-%d"),
            "reservoir_count": int(res["reservoir_id"].nunique()) if not res.empty else 0,
            "reservoir_rows": int(len(res)),
        }

    def system_status(self) -> dict[str, Any]:
        model = (self.processed / "model_predictions.csv").exists()
        trigger = (self.processed / "risk_trigger_history.csv").exists()
        return {
            "stages": [
                {"name": "Data ingestion", "state": "ONLINE" if model else "DEGRADED"},
                {"name": "Risk model", "state": "ONLINE" if model else "PENDING"},
                {"name": "Persistence trigger", "state": "ONLINE" if trigger else "PENDING"},
                {"name": "Policy corpus", "state": "READY" if list((self.root / "knowledge_base").rglob("*.pdf")) else "PENDING"},
                {"name": "RAG retrieval", "state": "PENDING"},
                {"name": "Local LLM", "state": "PENDING"},
                {"name": "Evidence verification", "state": "PENDING"},
            ]
        }


__all__ = ["DataService", "ACTIVE_STATIONS", "ACTIVE_RESERVOIRS"]
