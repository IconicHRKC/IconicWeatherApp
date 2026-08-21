"""
storm_data.py
Fetches severe weather reports from NOAA's Storm Prediction Center (SPC)
for a rolling window of days, filters them to a radius around Kansas
City, and normalizes them into the shape the dashboard expects.

Data sources (all free / public, no API key required):
  - SPC Local Storm Reports (hail, wind, tornado):
      Today:       https://www.spc.noaa.gov/climo/reports/today_hail.csv
                    https://www.spc.noaa.gov/climo/reports/today_wind.csv
                    https://www.spc.noaa.gov/climo/reports/today_torn.csv
      Past days:   https://www.spc.noaa.gov/climo/reports/YYMMDD_rpts_hail.csv
                    https://www.spc.noaa.gov/climo/reports/YYMMDD_rpts_wind.csv
                    https://www.spc.noaa.gov/climo/reports/YYMMDD_rpts_torn.csv
    NOTE: SPC's "today" is a convective day that starts around 06Z
    (roughly 1am CDT), matching how meteorologists group storm days.
    Past days' files don't change once the day is over, so we cache
    them in memory and only ever re-fetch "today".
  - Reverse geocoding (lat/lon -> ZIP code) via OpenStreetMap Nominatim:
      https://nominatim.openstreetmap.org/reverse
    Nominatim's usage policy allows light, non-bulk use with a proper
    User-Agent and max ~1 request/second - fine for this volume of
    storm reports.
"""

import csv
import io
import math
import time
from datetime import datetime, timedelta, timezone
import requests

# ---- Coverage area -----------------------------------------------------

KC_LAT = 39.0997
KC_LON = -94.5786
RADIUS_MILES = 30
ALLOWED_STATES = {"KS", "MO"}
LOOKBACK_DAYS = 15  # how many days of history to include, in addition to today

USER_AGENT = "IconicStormWatch/1.0 (hello@iconichrkc.com)"

SPC_BASE = "https://www.spc.noaa.gov/climo/reports"
NOMINATIM = "https://nominatim.openstreetmap.org/reverse"

# Cache of historical (already-finalized) day CSVs, keyed by "YYMMDD".
# Today's data is never cached here since it changes throughout the day.
_day_cache = {}


def haversine_miles(lat1, lon1, lat2, lon2):
    R = 3958.8  # earth radius in miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _spc_latlon(raw_lat, raw_lon):
    """SPC encodes lat/lon as integers without a decimal point, e.g.
    3910 -> 39.10, -9458 -> -94.58. Longitude is given as a positive
    number meaning "west" so we negate it."""
    lat = float(raw_lat) / 100.0
    lon = -float(raw_lon) / 100.0
    return lat, lon


def _fetch_csv(url):
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    return list(csv.DictReader(io.StringIO(resp.text)))


def _reverse_geocode_zip(lat, lon, cache):
    key = (round(lat, 3), round(lon, 3))
    if key in cache:
        return cache[key]
    try:
        resp = requests.get(
            NOMINATIM,
            params={"lat": lat, "lon": lon, "format": "json", "zoom": 15, "addressdetails": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        zip_code = data.get("address", {}).get("postcode")
    except Exception:
        zip_code = None
    cache[key] = zip_code
    time.sleep(1)  # respect Nominatim's ~1 req/sec usage policy
    return zip_code


def _parse_hail_row(r):
    try:
        lat, lon = _spc_latlon(r["Lat"], r["Lon"])
    except (KeyError, ValueError):
        return None
    size_hundredths = r.get("Size", "").strip()
    try:
        size_in = float(size_hundredths) / 100.0
        impact = f"Hail size: {size_in:.2f} in"
    except ValueError:
        impact = "Hail reported (size unconfirmed)"
    return {
        "raw_type": "Hail", "time": r.get("Time", "").strip(),
        "location": r.get("Location", "").strip(), "county": r.get("County", "").strip(),
        "state": r.get("State", "").strip(), "lat": lat, "lon": lon,
        "impact": impact, "comments": r.get("Comments", "").strip(),
    }


def _parse_wind_row(r):
    try:
        lat, lon = _spc_latlon(r["Lat"], r["Lon"])
    except (KeyError, ValueError):
        return None
    speed = r.get("Speed", "").strip()
    impact = f"Wind gust: {speed} mph" if speed and speed.upper() != "UNK" \
        else "Damaging wind reported (speed unconfirmed)"
    return {
        "raw_type": "Wind", "time": r.get("Time", "").strip(),
        "location": r.get("Location", "").strip(), "county": r.get("County", "").strip(),
        "state": r.get("State", "").strip(), "lat": lat, "lon": lon,
        "impact": impact, "comments": r.get("Comments", "").strip(),
    }


def _parse_tornado_row(r):
    try:
        lat, lon = _spc_latlon(r["Lat"], r["Lon"])
    except (KeyError, ValueError):
        return None
    return {
        "raw_type": "Tornado", "time": r.get("Time", "").strip(),
        "location": r.get("Location", "").strip(), "county": r.get("County", "").strip(),
        "state": r.get("State", "").strip(), "lat": lat, "lon": lon,
        "impact": "Tornado reported", "comments": r.get("Comments", "").strip(),
    }


_PARSERS = {"hail": _parse_hail_row, "wind": _parse_wind_row, "torn": _parse_tornado_row}


def _fetch_day(day: datetime, offset: int):
    """Fetch hail/wind/tornado reports for one convective day. Returns a
    list of normalized (but not yet filtered/geocoded) report dicts, each
    tagged with the calendar date it belongs to.

    offset 0 = today (always re-fetched, uses today_*.csv - updates live)
    offset 1 = yesterday (uses yesterday_*.csv - SPC updates this fast;
               the dated archive file for a day this recent can lag
               behind while final quality control finishes)
    offset 2+ = older, already-finalized days (uses the dated archive,
               cached in memory since it won't change anymore)
    """
    yymmdd = day.strftime("%y%m%d")
    date_str = day.strftime("%Y-%m-%d")
    is_today = offset == 0
    cache_key = yymmdd if offset != 1 else f"y-{yymmdd}"  # keep yesterday's slot distinct

    if offset >= 2 and cache_key in _day_cache:
        return _day_cache[cache_key]

    day_reports = []
    for kind, parser in _PARSERS.items():
        if offset == 0:
            url = f"{SPC_BASE}/today_{kind}.csv"
        elif offset == 1:
            url = f"{SPC_BASE}/yesterday_{kind}.csv"
        else:
            url = f"{SPC_BASE}/{yymmdd}_rpts_{kind}.csv"
        try:
            rows = _fetch_csv(url)
        except Exception as exc:
            print(f"[storm_data] fetch failed for {url}: {exc}", flush=True)
            continue
        print(f"[storm_data] {url} -> {len(rows)} rows", flush=True)
        for r in rows:
            parsed = parser(r)
            if parsed:
                parsed["date"] = date_str
                day_reports.append(parsed)

    if offset >= 2:
        _day_cache[cache_key] = day_reports

    return day_reports


def fetch_and_filter_reports(zip_cache=None, lookback_days: int = LOOKBACK_DAYS):
    """Returns a list of normalized report dicts within RADIUS_MILES of
    Kansas City and inside ALLOWED_STATES, covering today plus the past
    `lookback_days` days. The window always slides forward with "today" -
    no fixed dates, so it's always "now back N days" on every call."""
    if zip_cache is None:
        zip_cache = {}

    now = datetime.now(timezone.utc)
    raw_reports = []
    valid_keys = set()
    for offset in range(0, lookback_days + 1):
        day = now - timedelta(days=offset)
        yymmdd = day.strftime("%y%m%d")
        valid_keys.add(yymmdd if offset != 1 else f"y-{yymmdd}")
        raw_reports.extend(_fetch_day(day, offset))

    # Drop cached days that have aged out of the rolling window, so memory
    # doesn't grow unbounded on a service that stays up for months.
    for stale_key in [k for k in _day_cache if k not in valid_keys]:
        del _day_cache[stale_key]

    state_matches = 0
    results = []
    for r in raw_reports:
        if r["state"] not in ALLOWED_STATES:
            continue
        state_matches += 1
        dist = haversine_miles(KC_LAT, KC_LON, r["lat"], r["lon"])
        if dist > RADIUS_MILES:
            continue

        zip_code = _reverse_geocode_zip(r["lat"], r["lon"], zip_cache)
        t = r["time"]
        time_fmt = f"{t[:2]}:{t[2:]}" if len(t) == 4 else t

        report_id = f"{r['raw_type']}-{r['date']}-{r['time']}-{r['lat']}-{r['lon']}"
        results.append({
            "id": report_id,
            "type": r["raw_type"],
            "impact": r["impact"],
            "city": r["location"] or r["county"],
            "county": r["county"],
            "state": r["state"],
            "zip_codes": [zip_code] if zip_code else [],
            "date": r["date"],
            "time": time_fmt,
            "source": "NOAA Storm Prediction Center (Local Storm Report)",
            "distance_mi": round(dist, 1),
            "comments": r["comments"],
        })

    print(f"[storm_data] fetched {len(raw_reports)} raw reports across "
          f"{lookback_days + 1} days | {state_matches} in KS/MO | "
          f"{len(results)} within {RADIUS_MILES}mi of KC", flush=True)

    results.sort(key=lambda x: (x["date"], x["time"]), reverse=True)
    return results
