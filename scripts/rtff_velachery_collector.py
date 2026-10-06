#!/usr/bin/env python3
"""
Targeted RTFF/CRTFF collector for the Velachery / Ward-177 study area.

This version intentionally follows the working transport pattern from the
existing Claude scraper: a real Chromium page is used only as the HTTP
transport so the page session, cookies and anti-forgery token are valid.
The script then calls the discovered JSON endpoints directly. It does NOT
navigate the dashboard's historical-data UI.

IMPORTANT:
- Raw JSON is preserved exactly as received.
- No rainfall unit conversion is performed.
- No interpolation is performed.
- No null values are changed to zero.
- No merged ML dataset is created in this phase.
- Station mappings are discovered from the RTFF API and geoserver metadata.

Project layout expected:

flood_crisis_intelligence_engine/
├── Datasets/
│   └── raw/
│       └── rtff/
├── knowledge_base/
├── scripts/
│   └── rtff_velachery_collector.py
└── status.md

Setup:
    pip install playwright
    playwright install chromium

Recommended first run:
    python scripts/rtff_velachery_collector.py --headed discover

Then scrape only stations that the discovery step has actually resolved.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode

BASE = "https://chennaifloodmonitor.tn.gov.in"
RAW = Path("Datasets/raw/rtff")
ARG_DIR = RAW / "arg"
META_DIR = RAW / "_meta"

# Reference point used only for ranking nearby RTFF stations. It is a
# hydrologically relevant Velachery-area point, not a personal location.
REFERENCE_LAT = 12.987032
REFERENCE_LON = 80.218064

# Districts containing Chennai-area candidates. We query all of them because
# the current RTFF inventory can place Medavakkam-area stations in Chengalpattu.
DISCOVERY_DISTRICTS = ["Chennai", "Chengalpattu", "Kancheepuram", "Tiruvallur"]

# Recommended local stations for the first historical-data test. These are
# intentionally the nearby stations with older establishment dates visible in
# the current RTFF geospatial inventory, rather than only the four ARG codes
# used in the earlier Phase-I paperwork. The discover command verifies them
# against the live station list before scraping.
RECOMMENDED_STATIONS = [
    "CHN13Z178WG",           # Z 13 Velachery (W 178)
    "CHN_TARAMANI",          # Taramani
    "Anna_University",       # Anna University
    "ARG_NIOT_Pallikaranai", # NIOT Pallikaranai
]

# Original Phase-I codes, retained as optional checks. Some are recent
# installations in the current inventory and may not have long history.
PHASE1_CODES = [
    "ARG0055",  # Guindy (IIT)
    "ARG0069",  # Pallikaranai
    "ARG0052",  # MGR Nagar
    "ARG0075",  # Medavakkam
]

PREFERRED_STATIONS = RECOMMENDED_STATIONS + PHASE1_CODES

# Direct endpoint proven by the existing scraper.
ARG_HISTORY_PATH = "/Master/GetARGHistoricaldatafordashboard"
ARG_STATIONS_PATH = "/Master/GetARGStationData"
ARG_GEOSERVER_PATH = "/Master/GetGeoserverDataLoadARG"

JS_FETCH = """async (u, t) => { try {
    const r = await fetch(u, {headers: {
        'X-Requested-With': 'XMLHttpRequest',
        'Content-Type': 'application/json; charset=utf-8',
        'RequestVerificationToken': t
    }});
    return {status: r.status, text: await r.text()};
  } catch (e) { return {status: 0, text: String(e)}; } }"""


@dataclass
class Station:
    argid: str
    station_id: str
    station_name: str
    district: str
    agency: str
    latitude: float | None
    longitude: float | None
    establishment_date: str | None
    distance_km: float | None = None


class BrowserClient:
    """Use Chromium only as a same-origin/session-aware transport."""

    def __init__(self, headed: bool = False) -> None:
        from playwright.sync_api import sync_playwright

        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=not headed)
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.token = ""
        self.refresh()

    def refresh(self) -> None:
        for attempt in range(1, 4):
            try:
                self.page.goto(
                    BASE + "/HomePage/Dashboard",
                    wait_until="domcontentloaded",
                    timeout=90_000,
                )
                break
            except Exception as exc:  # pragma: no cover - browser-dependent
                print(
                    f"[browser] dashboard load failed ({type(exc).__name__}) "
                    f"attempt {attempt}/3"
                )
                if attempt == 3:
                    raise
                time.sleep(10 * attempt)

        self.page.wait_for_timeout(4000)
        token = self.page.evaluate(
            "() => (typeof token !== 'undefined' ? token : '')"
        )
        if not token:
            html = self.page.content()
            match = re.search(r"token\s*=\s*'([^']{20,})'", html)
            token = match.group(1) if match else ""
        self.token = token
        if self.token:
            print(f"[browser] anti-forgery token found (len={len(self.token)})")
        else:
            print("[browser] WARNING: anti-forgery token not found")

    def get(self, url: str) -> tuple[int, str]:
        result = self.page.evaluate(
            f"([u, t]) => ({JS_FETCH})(u, t)",
            [url, self.token],
        )
        return int(result["status"]), str(result["text"])

    def close(self) -> None:
        self.browser.close()
        self.pw.stop()


CLIENT: BrowserClient | None = None


def now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def iso_stamp() -> int:
    return int(time.time() * 1000)


def sleep_between(delay: float) -> None:
    time.sleep(delay + random.random() * 0.5)


def build(path: str, params: dict[str, Any] | None = None) -> str:
    url = path if path.startswith("http") else BASE + path
    if params:
        return url + (("&" if "?" in url else "?") + urlencode(params))
    return url


def parse_payload(text: str) -> Any:
    value: Any = json.loads(text)
    # Some RTFF endpoints double-encode JSON as a JSON string.
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass
    return value


def api_get_raw(path: str, params: dict[str, Any] | None = None, tries: int = 4) -> str | None:
    """Return response text exactly as received from the API."""
    assert CLIENT is not None
    url = build(path, params)
    for attempt in range(1, tries + 1):
        try:
            status, text = CLIENT.get(url)
            if status == 200:
                return text
            print(f"  HTTP {status} on attempt {attempt}/{tries}")
            if status in (401, 403) and attempt < tries:
                CLIENT.refresh()
        except Exception as exc:  # pragma: no cover - browser/network dependent
            print(f"  request error {type(exc).__name__}: {exc}")
        time.sleep(2 ** (attempt - 1))
    return None


def api_get(path: str, params: dict[str, Any] | None = None, tries: int = 4) -> Any:
    raw = api_get_raw(path, params, tries=tries)
    return parse_payload(raw) if raw is not None else None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def fetch_geoserver() -> list[dict[str, Any]]:
    payload = api_get(ARG_GEOSERVER_PATH, {"cqfilter": "&CQL_FILTER="})
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected ARG geoserver payload")
    features = payload.get("features")
    if not isinstance(features, list):
        raise RuntimeError("ARG geoserver payload has no feature list")
    return [x.get("properties", {}) for x in features if isinstance(x, dict)]


def fetch_station_rows(district: str) -> list[dict[str, Any]]:
    payload = api_get(
        ARG_STATIONS_PATH,
        {
            "district": district,
            "agency": "ALL",
            "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )
    if not isinstance(payload, list):
        print(f"[discover] {district}: unexpected station payload")
        return []
    return [x for x in payload if isinstance(x, dict)]


def discover_catalog(save: bool = True) -> list[Station]:
    """Merge current ARG station-list IDs with geoserver coordinates/metadata."""
    geos = fetch_geoserver()
    geos_by_station_id: dict[str, dict[str, Any]] = {}
    for row in geos:
        sid = row.get("stationid")
        if sid:
            geos_by_station_id[str(sid)] = row

    merged: dict[str, Station] = {}

    for district in DISCOVERY_DISTRICTS:
        rows = fetch_station_rows(district)
        print(f"[discover] {district}: {len(rows)} station-list rows")
        for row in rows:
            argid = row.get("argid")
            station_id = row.get("stationid")
            if not argid or not station_id:
                continue
            meta = geos_by_station_id.get(str(station_id), {})
            lat = meta.get("latitude")
            lon = meta.get("longitude")
            try:
                lat = float(lat) if lat is not None else None
                lon = float(lon) if lon is not None else None
            except (TypeError, ValueError):
                lat, lon = None, None
            distance = None
            if lat is not None and lon is not None:
                distance = haversine_km(REFERENCE_LAT, REFERENCE_LON, lat, lon)
            merged[str(argid)] = Station(
                argid=str(argid),
                station_id=str(station_id),
                station_name=str(row.get("stationname") or meta.get("stationname") or ""),
                district=str(row.get("district") or meta.get("district") or district),
                agency=str(row.get("agency") or meta.get("agency") or ""),
                latitude=lat,
                longitude=lon,
                establishment_date=(
                    str(meta.get("date_of_establishment"))
                    if meta.get("date_of_establishment") is not None
                    else None
                ),
                distance_km=distance,
            )

    stations = list(merged.values())
    stations.sort(key=lambda s: (s.distance_km is None, s.distance_km or 1e9, s.argid))

    if save:
        META_DIR.mkdir(parents=True, exist_ok=True)
        out = META_DIR / f"arg_station_catalog_{now_tag()}.csv"
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow([
                "argid", "station_id", "station_name", "district", "agency",
                "latitude", "longitude", "establishment_date", "distance_from_reference_km",
            ])
            for s in stations:
                w.writerow([
                    s.argid, s.station_id, s.station_name, s.district, s.agency,
                    s.latitude, s.longitude, s.establishment_date,
                    None if s.distance_km is None else round(s.distance_km, 3),
                ])
        print(f"[discover] catalog saved: {out}")

    return stations


def print_targets(stations: list[Station]) -> None:
    by_argid = {s.argid: s for s in stations}

    def show_group(title: str, ids: list[str]) -> None:
        print(f"\n=== {title} ===")
        for argid in ids:
            s = by_argid.get(argid)
            if s is None:
                print(f"[UNRESOLVED] {argid}")
                continue
            distance = "n/a" if s.distance_km is None else f"{s.distance_km:.2f} km"
            print(
                f"[FOUND] {s.argid:24s} | {s.station_name:34s} | "
                f"{s.district:12s} | {s.agency:16s} | "
                f"{distance:>9s} | established={s.establishment_date} | "
                f"{s.station_id}"
            )

    show_group("RECOMMENDED LOCAL HISTORICAL CANDIDATES", RECOMMENDED_STATIONS)
    show_group("ORIGINAL PHASE-I ARG CODES", PHASE1_CODES)

    print("\n=== NEAREST ARG STATIONS (up to 25) ===")
    for s in stations[:25]:
        if s.distance_km is None:
            continue
        print(
            f"{s.distance_km:5.2f} km | {s.argid:24s} | {s.station_name:34s} | "
            f"{s.agency:16s} | established={s.establishment_date}"
        )


def tiles(start: date, end: date, tile_days: int = 8) -> Iterable[tuple[date, date]]:
    cur = start
    while cur <= end:
        ed = min(cur + timedelta(days=tile_days - 1), end)
        yield cur, ed
        cur = ed + timedelta(days=1)


def history_request(argid: str, sd: date, ed: date) -> tuple[str, dict[str, Any]]:
    return ARG_HISTORY_PATH, {
        "argobjectid": argid,
        "sdate": sd.isoformat(),
        "edate": ed.isoformat(),
        "_": iso_stamp(),
    }


def download_tile(argid: str, sd: date, ed: date) -> tuple[Path | None, int]:
    folder = ARG_DIR / argid / "tiles"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sd}_{ed}.json"
    if path.exists():
        try:
            payload = parse_payload(path.read_text(encoding="utf-8"))
            return path, len(payload) if isinstance(payload, list) else -1
        except Exception:
            pass

    path_api, params = history_request(argid, sd, ed)
    raw_text = api_get_raw(path_api, params)
    if raw_text is None:
        return None, -1

    # Preserve the response text exactly as returned by the API. No parsing,
    # reformatting, unit conversion, null coercion, or interpolation occurs.
    path.write_text(raw_text, encoding="utf-8")
    try:
        payload = parse_payload(raw_text)
        rows = len(payload) if isinstance(payload, list) else -1
    except Exception:
        rows = -1
    return path, rows


def scrape(argids: list[str], start: date, end: date, delay: float) -> None:
    ARG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RAW / "acquisition_log.csv"
    new_log = not log_path.exists()

    with log_path.open("a", newline="", encoding="utf-8") as logfh:
        log = csv.writer(logfh)
        if new_log:
            log.writerow([
                "timestamp", "argid", "sdate", "edate", "rows",
                "status", "path",
            ])

        total_tiles = sum(1 for _ in tiles(start, end))
        print(f"Scraping {len(argids)} stations x {total_tiles} eight-day tiles")

        for idx, argid in enumerate(argids, 1):
            station_dir = ARG_DIR / argid
            (station_dir / "tiles").mkdir(parents=True, exist_ok=True)
            new_count = 0
            failed = 0
            print(f"\n[{idx}/{len(argids)}] {argid}")

            for tile_i, (sd, ed) in enumerate(tiles(start, end), 1):
                path = station_dir / "tiles" / f"{sd}_{ed}.json"
                existed = path.exists()
                try:
                    result, rows = download_tile(argid, sd, ed)
                    status = "existing" if existed else ("ok" if result else "failed")
                    if status == "failed":
                        failed += 1
                    else:
                        new_count += 0 if existed else 1
                    log.writerow([
                        datetime.now().isoformat(timespec="seconds"),
                        argid, sd.isoformat(), ed.isoformat(), rows,
                        status, str(result) if result else "",
                    ])
                    logfh.flush()
                    print(f"  {tile_i:4d}/{total_tiles} {sd}..{ed}: {status:8s} rows={rows}")
                except Exception as exc:
                    failed += 1
                    log.writerow([
                        datetime.now().isoformat(timespec="seconds"),
                        argid, sd.isoformat(), ed.isoformat(), -1,
                        f"error:{type(exc).__name__}", "",
                    ])
                    logfh.flush()
                    print(f"  {tile_i:4d}/{total_tiles} {sd}..{ed}: ERROR {exc}")

                if tile_i < total_tiles:
                    sleep_between(delay)

            print(f"  completed: new={new_count}, failed={failed}")


def parse_cli_date(value: str) -> date:
    return date.fromisoformat(value)


def load_station_ids(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Targeted Velachery RTFF ARG collector")
    parser.add_argument("--headed", action="store_true", help="Show Chromium window")
    sub = parser.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="Discover station mappings and nearby candidates")
    d.add_argument("--no-save", action="store_true")

    s = sub.add_parser("scrape", help="Download raw eight-day ARG history tiles")
    s.add_argument("--stations", required=True, help="Comma-separated internal argids")
    s.add_argument("--start", default="2018-01-01")
    s.add_argument("--end", default=date.today().isoformat())
    s.add_argument("--delay", type=float, default=1.5)

    both = sub.add_parser("discover-and-scrape", help="Discover then scrape selected targets")
    both.add_argument("--stations", default=",".join(RECOMMENDED_STATIONS))
    both.add_argument("--start", default="2018-01-01")
    both.add_argument("--end", default=date.today().isoformat())
    both.add_argument("--delay", type=float, default=1.5)

    rec = sub.add_parser("recommended", help="Discover, validate, then scrape recommended local historical candidates")
    rec.add_argument("--start", default="2018-01-01")
    rec.add_argument("--end", default=date.today().isoformat())
    rec.add_argument("--delay", type=float, default=1.5)

    args = parser.parse_args()

    global CLIENT
    CLIENT = BrowserClient(headed=args.headed)
    try:
        if args.cmd == "discover":
            stations = discover_catalog(save=not args.no_save)
            print_targets(stations)
            return 0

        if args.cmd == "scrape":
            ids = load_station_ids(args.stations)
            scrape(ids, parse_cli_date(args.start), parse_cli_date(args.end), args.delay)
            return 0

        if args.cmd == "recommended":
            stations = discover_catalog(save=True)
            by_argid = {s.argid for s in stations}
            resolved = [x for x in RECOMMENDED_STATIONS if x in by_argid]
            unresolved = [x for x in RECOMMENDED_STATIONS if x not in by_argid]
            print_targets(stations)
            if unresolved:
                print("\n[WARN] Unresolved recommended stations:", ", ".join(unresolved))
            if not resolved:
                print("No recommended station IDs were resolved. Nothing scraped.")
                return 2
            scrape(resolved, parse_cli_date(args.start), parse_cli_date(args.end), args.delay)
            return 0

        if args.cmd == "discover-and-scrape":
            stations = discover_catalog(save=True)
            by_argid = {s.argid for s in stations}
            requested = load_station_ids(args.stations)
            resolved = [x for x in requested if x in by_argid]
            unresolved = [x for x in requested if x not in by_argid]
            print_targets(stations)
            if unresolved:
                print("\n[WARN] Unresolved targets:", ", ".join(unresolved))
            if not resolved:
                print("No requested station IDs were resolved. Nothing scraped.")
                return 2
            scrape(resolved, parse_cli_date(args.start), parse_cli_date(args.end), args.delay)
            return 0

        return 2
    finally:
        CLIENT.close()


if __name__ == "__main__":
    raise SystemExit(main())
