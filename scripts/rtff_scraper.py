"""
RTFF (Chennai Flood Monitor) raw-data scraper.  All raw responses are stored untouched.

KEY FACT (verified from 109 real requests): the history endpoints only look at the 8 calendar days
sdate .. sdate+7 and ignore edate beyond that. So history is downloaded in fixed 8-day tiles.
An empty reply therefore means "no data in those 8 days" - NOT "no data in the whole range".

Setup:  pip install playwright && playwright install chromium
        (put --headed BEFORE the command if headless ever gets 403)

Commands
  coverage     ARG: samples the first 8 days of every month for one station (quick look)
  scrape       ARG hourly rainfall for a district, fixed 8-day tiles, resumable
  verify       re-download tiles singly and compare with disk (empty tiles + random sample)
  summary      offline report of what is on disk; --argid also cross-checks the coverage csv exactly
  reservoir    reservoir history for the 5 Chennai lakes, fixed 8-day tiles, resumable
  aws-probe    AWS: which stations report recently, does the history call return data?

Layout
  Datasets/raw/rtff/arg/<argid>/<sdate>_<edate>.json         (+ _meta/, _manifest.csv)
  Datasets/raw/rtff/reservoir/<tankid>/<sdate>_<edate>.json  (+ _meta/, _manifest.csv)
  Datasets/raw/rtff/aws/_probe/
"""
import argparse, csv, json, random, sys, time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

BASE = "https://chennaifloodmonitor.tn.gov.in"
RAW = Path("Datasets/raw/rtff")
ARG, RES, AWS = RAW / "arg", RAW / "reservoir", RAW / "aws"
TILE = 8   # days the server actually returns per request
TANKS = {"RD001": "Red Hills", "Cho001": "Cholavaram", "Po001": "Poondi",
         "TK-001": "Thervoy Kandigal", "TNCH-07-T0726": "Chembarambakkam"}


# ---------------------------------------------------------------- browser transport
class R:
    def __init__(self, status, text):
        self.status_code, self.content = status, text.encode("utf-8")


JS_FETCH = """async (u, t) => { try {
    const r = await fetch(u, {headers: {'X-Requested-With': 'XMLHttpRequest',
        'Content-Type': 'application/json; charset=utf-8', 'RequestVerificationToken': t}});
    return {status: r.status, text: await r.text()};
  } catch (e) { return {status: 0, text: String(e)}; } }"""


class BrowserClient:
    """Runs fetch() inside a real Chromium page, sending the page's anti-forgery token."""
    def __init__(self, headed=False):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=not headed)
        self.page = self.browser.new_page()
        self.token = ""
        self.refresh()

    def refresh(self):
        import re
        for attempt in range(1, 4):               # the site is sometimes slow: retry instead of crashing
            try:
                self.page.goto(BASE + "/HomePage/Dashboard", wait_until="domcontentloaded", timeout=90000)
                break
            except Exception as e:
                print(f"[browser] dashboard did not load ({type(e).__name__}), attempt {attempt}/3")
                if attempt == 3: raise
                time.sleep(20 * attempt)
        self.page.wait_for_timeout(4000)
        tok = self.page.evaluate("() => (typeof token !== 'undefined' ? token : '')")
        if not tok:
            m = re.search(r"token\s*=\s*'([^']{20,})'", self.page.content())
            tok = m.group(1) if m else ""
        self.token = tok
        print(f"[browser] anti-forgery token {'found (len %d)' % len(tok) if tok else 'NOT FOUND'}")

    def get(self, url):
        return self.page.evaluate(f"([u, t]) => ({JS_FETCH})(u, t)", [url, self.token])

    def get_many(self, urls):
        """several requests in parallel from inside the page"""
        return self.page.evaluate(
            f"async ([us, t]) => await Promise.all(us.map(u => ({JS_FETCH})(u, t)))", [urls, self.token])

    def close(self):
        self.browser.close(); self.pw.stop()


client = None


def parse(content: bytes):
    d = json.loads(content)
    return json.loads(d) if isinstance(d, str) else d  # some endpoints double-encode


def build(path, params=None):
    url = path if path.startswith("http") else BASE + path
    return url + (("&" if "?" in url else "?") + urlencode(params) if params else "")


def get(path, params=None, tries=4):
    url = build(path, params)
    for i in range(tries):
        try:
            res = client.get(url)
            if res["status"] == 200:
                return R(200, res["text"])
            print(f"  HTTP {res['status']} (try {i+1})")
            if res["status"] in (401, 403) and i < tries - 1:
                client.refresh()
        except Exception as e:
            print(f"  error {e} (try {i+1})")
        time.sleep(2 ** i)
    return None


def get_batch(reqs):
    """reqs: list of (path, params). Parallel first attempt; any failure is retried one by one."""
    res = client.get_many([build(p, q) for p, q in reqs])
    out = []
    for (p, q), x in zip(reqs, res):
        if x["status"] == 200:
            out.append(R(200, x["text"]))
        else:
            print(f"  batch item failed (HTTP {x['status']}) - retrying singly")
            out.append(get(p, q))
    return out


def stamp(): return int(time.time() * 1000)
def pause(a=None): time.sleep((a.delay if a else 1.0) + random.random() * 0.5)


def manifest(folder, header):
    folder.mkdir(parents=True, exist_ok=True)
    mf = folder / "_manifest.csv"; new = not mf.exists()
    fh = open(mf, "a", newline=""); w = csv.writer(fh)
    if new: w.writerow(header)
    return fh, w


def row_date(r):  return datetime.strptime(r["date_val"], "%d-%m-%Y").date()   # ARG rows
def res_day(r):   return date.fromisoformat(r["date"][:10])                    # reservoir rows


# ---------------------------------------------------------------- fixed 8-day tiling (shared)
def tiles(start, end):
    cur = start
    while cur <= end:
        ed = min(cur + timedelta(days=TILE - 1), end)
        yield cur, ed
        cur = ed + timedelta(days=1)


def run_tiles(folder, key, req_fn, start, end, a, w, fh):
    """Download every 8-day tile for one station/tank. Existing files are skipped (resume).
    Raw bytes are written exactly as received. Returns (total, new, failed)."""
    todo = [(sd, ed) for sd, ed in tiles(start, end) if not (folder / key / f"{sd}_{ed}.json").exists()]
    total = sum(1 for _ in tiles(start, end)); new = failed = streak = 0
    for i in range(0, len(todo), a.concurrency):
        batch = todo[i:i + a.concurrency]
        for (sd, ed), r in zip(batch, get_batch([req_fn(key, sd, ed) for sd, ed in batch])):
            if r is None:
                failed += 1; streak += 1
                if streak >= 5: sys.exit("5 failed requests in a row - stopping. Re-run later to resume.")
                continue
            streak = 0
            out = folder / key / f"{sd}_{ed}.json"
            out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(r.content)   # untouched
            w.writerow([key, sd, ed, len(parse(r.content)), len(r.content), datetime.now().isoformat()])
            new += 1
        fh.flush()
        if (i // a.concurrency) % 25 == 24: print(f"    {key}: {min(i + a.concurrency, len(todo))}/{len(todo)} tiles")
        pause(a)
    return total, new, failed


# ---------------------------------------------------------------- ARG
def hist_req(argid, sd, ed):
    return ("/Master/GetARGHistoricaldatafordashboard",
            {"argobjectid": argid, "sdate": sd.isoformat(), "edate": ed.isoformat(), "_": stamp()})


def hist(argid, sd, ed): return get(*hist_req(argid, sd, ed))


def coverage(a):
    """Quick look: for each month, how many days exist among the FIRST 8 DAYS of the month."""
    import calendar
    meta = ARG / "_meta"; meta.mkdir(parents=True, exist_ok=True)
    y, m = map(int, a.from_month.split("-")); today = date.today()
    with open(meta / f"coverage_{a.argid}.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["month", "rows_in_first_page", "first_date_val", "unique_days"])
        while (y, m) <= (today.year, today.month):
            sd = date(y, m, 1); ed = date(y, m, calendar.monthrange(y, m)[1])
            r = hist(a.argid, sd, ed); rows = parse(r.content) if r else []
            ud = len({x["date_val"] for x in rows})
            print(f"{y}-{m:02d} rows_in_first_8_days={len(rows)} unique_days={ud} first={rows[0]['date_val'] if rows else ''}")
            w.writerow([f"{y}-{m:02d}", len(rows), rows[0]["date_val"] if rows else "", ud]); fh.flush()
            pause(); m += 1
            if m == 13: y, m = y + 1, 1


def scrape(a):
    meta = ARG / "_meta"; meta.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    r = get("/Master/GetGeoserverDataLoadARG?cqfilter=%26CQL_FILTER%3D")
    if r: (meta / f"arg_layer_{ts}.json").write_bytes(r.content)
    r = get("/Master/GetARGStationData", {"district": a.district, "agency": "ALL",
                                           "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    if not r: sys.exit("could not fetch station list")
    (meta / f"stations_{a.district}_{ts}.json").write_bytes(r.content)
    sts = [(x["argid"], x["stationname"]) for x in parse(r.content)]
    if a.only: sts = [s for s in sts if s[0] in a.only.split(",")]
    if a.max_stations: sts = sts[: a.max_stations]
    start, end = date.fromisoformat(a.start), (date.fromisoformat(a.end) if a.end else date.today())
    per = sum(1 for _ in tiles(start, end))
    print(f"{len(sts)} stations x {per} tiles = up to {len(sts) * per} requests (existing files are skipped)")
    fh, w = manifest(ARG, ["argid", "sdate", "edate", "rows", "bytes", "fetched_at"])
    for k, (argid, name) in enumerate(sts, 1):
        tot, new, bad = run_tiles(ARG, argid, hist_req, start, end, a, w, fh)
        print(f"[{k}/{len(sts)}] {argid} ({name}): {tot} tiles, {new} new, {bad} failed")


def verify(a):
    """Re-download tiles ONE AT A TIME and compare with what is stored: all empty tiles (is an empty reply
    real, or a server hiccup?) plus a random sample of non-empty ones (--sample N, or --all).
    Originals are never touched; a differing re-download is saved beside it as <tile>.recheckN.json."""
    folder = ARG / a.argid
    files = sorted(p for p in folder.glob("*.json") if ".recheck" not in p.name)
    empties = [p for p in files if not parse(p.read_bytes())]
    full = [p for p in files if p not in empties]
    pick = empties + (full if a.all else random.sample(full, min(a.sample, len(full))))
    print(f"{len(files)} tiles on disk: {len(empties)} empty, {len(full)} with data -> re-checking {len(pick)}")
    same = diff = failed = 0
    for p in pick:
        sd, ed = p.stem.split("_")
        r = hist(a.argid, date.fromisoformat(sd), date.fromisoformat(ed))
        if r is None:
            failed += 1; print(f"  could not re-fetch {p.name}"); continue
        old, new = parse(p.read_bytes()), parse(r.content)
        if old == new:
            same += 1
        else:
            diff += 1
            n = len(list(folder.glob(f"{p.stem}.recheck*.json"))) + 1
            p.with_name(f"{p.stem}.recheck{n}.json").write_bytes(r.content)
            od = {x["date_val"]: x for x in old}; nd = {x["date_val"]: x for x in new}
            changed = sum(1 for k in od if k in nd and od[k] != nd[k])
            print(f"  DIFF {p.name}: stored {len(old)} day-rows, re-fetched {len(new)}; "
                  f"only-in-new={len(set(nd) - set(od))} only-in-old={len(set(od) - set(nd))} values-changed={changed}")
        pause(a)
    print(f"\nverify: {same} identical, {diff} different, {failed} failed")


def summary(a):
    """Offline report of what is on disk. Counts unique days AND raw rows, reports duplicate rows inside
    single responses, and (with --argid) cross-checks the coverage csv exactly."""
    stations = [ARG / a.argid] if a.argid else [p for p in sorted(ARG.iterdir()) if p.is_dir() and not p.name.startswith("_")]
    info = {}
    for st in stations:
        days, dups, raw8 = set(), [], Counter()
        for f in sorted(st.glob("*.json")):
            try: rows = parse(f.read_bytes())
            except Exception as e: print("  unreadable", f, e); continue
            ds = [row_date(r) for r in rows]; days.update(ds)
            if ".recheck" in f.name: continue            # re-downloads: only used for the day set
            seen = {}
            for r, d in zip(rows, ds):
                seen.setdefault(d, []).append(r)
                if d.day <= TILE: raw8[f"{d.year}-{d.month:02d}"] += 1
            for d, v in seen.items():
                if len(v) > 1: dups.append((f.name, d, len(v), any(x != v[0] for x in v[1:])))
        info[st.name] = (days, dups, raw8)
        if not days: print(f"{st.name:30s} no data"); continue
        d = sorted(days); span = (d[-1] - d[0]).days + 1
        print(f"{st.name:30s} days={len(d):5d} first={d[0]} last={d[-1]} span={span} missing_inside_span={span - len(d)}"
              f" | duplicate dates in a response: {len(dups)} ({sum(1 for x in dups if x[3])} with conflicting values)")
        if a.argid:
            print("  largest gaps (missing days, after, before):")
            for g in sorted(((d[i + 1] - d[i]).days - 1, d[i], d[i + 1]) for i in range(len(d) - 1) if (d[i + 1] - d[i]).days > 1)[-5:][::-1]:
                print(f"    {g[0]:4d}  {g[1]} -> {g[2]}")
            for x in dups[:5]: print(f"  duplicate: {x[0]} date={x[1]} copies={x[2]} conflicting={x[3]}")
    cov = ARG / "_meta" / f"coverage_{a.argid}.csv" if a.argid else None
    if cov and cov.exists() and info.get(a.argid, (set(),))[0]:
        days, dups, raw8 = info[a.argid]; first = {}
        for d in sorted(days):
            if d.day <= TILE: first.setdefault(f"{d.year}-{d.month:02d}", d)
        bad = []; tot = 0
        for r in csv.DictReader(open(cov)):
            tot += 1; exp = int(r["rows_in_first_page"]); got = raw8.get(r["month"], 0)
            ok = got == exp and (exp == 0 or first[r["month"]].strftime("%d-%m-%Y") == r["first_date_val"])
            if not ok: bad.append((r["month"], exp, got, sum(1 for d in days if f"{d.year}-{d.month:02d}" == r["month"] and d.day <= TILE)))
        print(f"\ncoverage cross-check (raw rows in days 1-8 of each month must match exactly): {tot - len(bad)}/{tot} months consistent")
        for b in bad: print("  MISMATCH month=%s coverage_rows=%d downloaded_rows=%d (unique days=%d)" % b)


# ---------------------------------------------------------------- reservoir
def res_req(tank, sd, ed):
    return ("/Master/GetHistoricalDataForReservoir",
            {"reservoirid": tank, "startdate": sd.isoformat(), "enddate": ed.isoformat(), "_": stamp()})


def reservoir(a):
    meta = RES / "_meta"; meta.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for name, path in [("tank_master", "/Master/GetGeoserverDatatank_master_new"),
                       ("tank_latest", "/Master/GetTankDataByDate"), ("storage_levels", "/Master/GetStorageLevels")]:
        r = get(path, {"_": stamp()} if "Geoserver" not in path else None)
        if r: (meta / f"{name}_{ts}.json").write_bytes(r.content)
    start, end = date.fromisoformat(a.start), (date.fromisoformat(a.end) if a.end else date.today())
    print(f"5 tanks x {sum(1 for _ in tiles(start, end))} tiles")
    fh, w = manifest(RES, ["tankid", "sdate", "edate", "rows", "bytes", "fetched_at"])
    for tank, name in TANKS.items():
        tot, new, bad = run_tiles(RES, tank, res_req, start, end, a, w, fh)
        print(f"{tank} ({name}): {tot} tiles, {new} new, {bad} failed")


# ---------------------------------------------------------------- AWS
def aws_hist(awsid, typ, sd, ed):
    return get("/Master/GetHistoricaldatafordashboard",
               {"awsobjectid": awsid, "sdate": sd.isoformat(), "edate": ed.isoformat(), "type": typ, "_": stamp()})


def aws_probe(a):
    """Which AWS stations report recently, and does history return data for the 8 days ending at the last
    record? (The earlier probe asked for a 10-year window; the server only reads the first 8 days of it,
    which is why every station came back empty.)"""
    out = AWS / "_probe"; out.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S"); rows = []
    for dist in ("ALL", "Chennai"):
        r = get("/Master/GetRainfallDatafordashboard", {"district": dist, "datetime": now})
        rows = parse(r.content) if r else []
        if rows:
            (out / f"latest_rainfall_{dist}.json").write_bytes(r.content); break
    print(f"{len(rows)} AWS stations in the latest-rainfall feed:\n")
    live = []
    for x in rows:
        print(f"{x.get('aws_id', ''):30s} {str(x.get('stationname', '')):28s} last={x.get('date_time')}")
        if x.get("date_time"): live.append((x["aws_id"], date.fromisoformat(x["date_time"][:10])))
    print("\nRainfall history for the 8 days ending at each station's last record:")
    hit = None
    for aid, last in live:
        sd = last - timedelta(days=TILE - 1)
        r = aws_hist(aid, "Rainfall", sd, last); rr = parse(r.content) if r else []
        if r: (out / f"{aid}_Rainfall_{sd}.json").write_bytes(r.content)
        print(f"  {aid:30s} {sd}..{last} rows={len(rr):3d} {list(rr[0].keys())[:8] if rr else ''}")
        if rr and hit is None: hit = (aid, sd, last, rr[0])
        pause()
    if hit: print("\nfirst row of first hit:", json.dumps(hit[3])[:400])
    t = hit or ((live[0][0], live[0][1] - timedelta(days=TILE - 1), live[0][1], None) if live else None)
    if t:
        aid, sd, last, _ = t
        print(f"\nvariable names for {aid} ({sd}..{last}) - guessing:")
        for typ in ["Rainfall", "rainfall", "Temperature", "AirTemperature", "Humidity", "RelativeHumidity",
                    "WindSpeed", "Wind Speed", "WindDirection"]:
            r = aws_hist(aid, typ, sd, last); rr = parse(r.content) if r else []
            if r: (out / f"{aid}_{typ.replace(' ', '')}_{sd}.json").write_bytes(r.content)
            print(f"  type={typ:18s} rows={len(rr)} {list(rr[0].keys())[:8] if rr else ''}")
            pause()


# ---------------------------------------------------------------- main
if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--headed", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("aws-probe").set_defaults(fn=aws_probe)
    sm = sub.add_parser("summary"); sm.set_defaults(fn=summary); sm.add_argument("--argid", default=None)
    vf = sub.add_parser("verify"); vf.set_defaults(fn=verify); vf.add_argument("--argid", required=True)
    vf.add_argument("--sample", type=int, default=40); vf.add_argument("--all", action="store_true")
    vf.add_argument("--delay", type=float, default=1.5)
    cv = sub.add_parser("coverage"); cv.set_defaults(fn=coverage)
    cv.add_argument("--argid", default="Anna_University"); cv.add_argument("--from-month", default="2015-01")
    for name, fn, dstart in (("scrape", scrape, "2018-01-01"), ("reservoir", reservoir, "2015-01-01")):
        sp = sub.add_parser(name); sp.set_defaults(fn=fn)
        sp.add_argument("--start", default=dstart); sp.add_argument("--end", default=None)
        sp.add_argument("--concurrency", type=int, default=3, help="parallel requests per batch")
        sp.add_argument("--delay", type=float, default=1.5, help="seconds between batches")
        if name == "scrape":
            sp.add_argument("--district", default="Chennai")
            sp.add_argument("--only", default=None, help="comma-separated argids")
            sp.add_argument("--max-stations", type=int, default=None)
    a = p.parse_args()
    if a.cmd == "summary":
        a.fn(a)
    else:
        client = BrowserClient(headed=a.headed)
        try:
            a.fn(a)
        finally:
            client.close()