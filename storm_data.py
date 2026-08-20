"""
storm_data.py
Fetches severe weather reports from NOAA's Storm Prediction Center (SPC)
and the National Weather Service (NWS), filters them to a radius around
Kansas City, and normalizes them into the shape the dashboard expects.

Data sources (all free / public, no API key required):
  - SPC Local Storm Reports (hail, wind, tornado):
      https://www.spc.noaa.gov/climo/reports/today.csv  (raw combined)
      We instead pull the three typed CSVs, which are easier to parse:
      https://www.spc.noaa.gov/climo/reports/today_hail.csv
      https://www.spc.noaa.gov/climo/reports/today_wind.csv
      https://www.spc.noaa.gov/climo/reports/today_torn.csv
    NOTE: SPC's "today" is a convective day that starts around 06Z
    (roughly 1am CDT), matching how meteorologists group storm days.
  - NWS active alerts (severe thunderstorm / tornado / winter storm
    warnings currently in effect), used as a supplementary signal:
      https://api.weather.gov/alerts/active?point={lat},{lon}
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
from datetime import datetime, timezone
import requests

# ---- Coverage area -----------------------------------------------------

KC_LAT = 39.0997
KC_LON = -94.5786
RADIUS_MILES = 30
ALLOWED_STATES = {"KS", "MO"}

USER_AGENT = "IconicStormWatch/1.0 (hello@iconichrkc.com)"

SPC_BASE = "https://www.spc.noaa.gov/climo/reports"
NWS_ALERTS = "https://api.weather.gov/alerts/active"
NOMINATIM = "https://nominatim.openstreetmap.org/reverse"


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


def _today_convective_date():
    # SPC "today" reports cover roughly 06Z to 06Z; using UTC date is a
    # reasonable approximation for which file to pull.
    return datetime.now(timezone.utc).strftime("%y%m%d")


def _fetch_hail():
    rows = _fetch_csv(f"{SPC_BASE}/today_hail.csv")
    out = []
    for r in rows:
        try:
            lat, lon = _spc_latlon(r["Lat"], r["Lon"])
        except (KeyError, ValueError):
            continue
        size_hundredths = r.get("Size", "").strip()
        try:
            size_in = float(size_hundredths) / 100.0
            impact = f"Hail size: {size_in:.2f} in"
        except ValueError:
            impact = "Hail reported (size unconfirmed)"
        out.append({
            "raw_type": "Hail",
            "time": r.get("Time", "").strip(),
            "location": r.get("Location", "").strip(),
            "county": r.get("County", "").strip(),
            "state": r.get("State", "").strip(),
            "lat": lat, "lon": lon,
            "impact": impact,
            "comments": r.get("Comments", "").strip(),
        })
    return out


def _fetch_wind():
    rows = _fetch_csv(f"{SPC_BASE}/today_wind.csv")
    out = []
    for r in rows:
        try:
            lat, lon = _spc_latlon(r["Lat"], r["Lon"])
        except (KeyError, ValueError):
            continue
        speed = r.get("Speed", "").strip()
        if speed and speed.upper() != "UNK":
            impact = f"Wind gust: {speed} mph"
        else:
            impact = "Damaging wind reported (speed unconfirmed)"
        out.append({
            "raw_type": "Wind",
            "time": r.get("Time", "").strip(),
            "location": r.get("Location", "").strip(),
            "county": r.get("County", "").strip(),
            "state": r.get("State", "").strip(),
            "lat": lat, "lon": lon,
            "impact": impact,
            "comments": r.get("Comments", "").strip(),
        })
    return out


def _fetch_tornado():
    rows = _fetch_csv(f"{SPC_BASE}/today_torn.csv")
    out = []
    for r in rows:
        try:
            lat, lon = _spc_latlon(r["Lat"], r["Lon"])
        except (KeyError, ValueError):
            continue
        out.append({
            "raw_type": "Tornado",
            "time": r.get("Time", "").strip(),
            "location": r.get("Location", "").strip(),
            "county": r.get("County", "").strip(),
            "state": r.get("State", "").strip(),
            "lat": lat, "lon": lon,
            "impact": "Tornado reported",
            "comments": r.get("Comments", "").strip(),
        })
    return out


def fetch_and_filter_reports(zip_cache=None):
    """Returns a list of normalized report dicts within RADIUS_MILES of
    Kansas City and inside ALLOWED_STATES."""
    if zip_cache is None:
        zip_cache = {}

    raw_reports = []
    for fetch_fn in (_fetch_hail, _fetch_wind, _fetch_tornado):
        try:
            raw_reports.extend(fetch_fn())
        except Exception as exc:
            print(f"[storm_data] fetch failed for {fetch_fn.__name__}: {exc}")

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results = []
    for r in raw_reports:
        if r["state"] not in ALLOWED_STATES:
            continue
        dist = haversine_miles(KC_LAT, KC_LON, r["lat"], r["lon"])
        if dist > RADIUS_MILES:
            continue

        zip_code = _reverse_geocode_zip(r["lat"], r["lon"], zip_cache)
        t = r["time"]
        time_fmt = f"{t[:2]}:{t[2:]}" if len(t) == 4 else t

        report_id = f"{r['raw_type']}-{r['time']}-{r['lat']}-{r['lon']}"
        results.append({
            "id": report_id,
            "type": r["raw_type"],
            "impact": r["impact"],
            "city": r["location"] or r["county"],
            "county": r["county"],
            "state": r["state"],
            "zip_codes": [zip_code] if zip_code else [],
            "date": today_str,
            "time": time_fmt,
            "source": "NOAA Storm Prediction Center (Local Storm Report)",
            "distance_mi": round(dist, 1),
            "comments": r["comments"],
        })

    results.sort(key=lambda x: x["time"], reverse=True)
    return results
