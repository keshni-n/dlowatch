"""
DloWatch additions to the water loss API.

Adds everything the Angular app needs on top of api.py, without changing api.py:
  - logins with roles (operator, admin, crew) and public pages with no login
  - meter telemetry with realistic noise, and the cleaning pipeline (DQ-01..DQ-06)
  - a rules engine that turns signals into an explainable score (every point traced)
  - leak alerts, replay timelines, public reports (with clustering), crews and jobs

Everything is kept in memory and rebuilt at start-up from a demo scenario, so the
demo always starts in the same state. Not real CWA data.

Run with:  uvicorn server:app --reload
"""

import base64
import hashlib
import io
import hmac
import json
from pathlib import Path
import math
import os
import random
import re
import threading
import time
import uuid
from collections import Counter
from typing import Literal, Optional

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import numpy as np
import wntr
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

import acoustic
import api  # the existing backend: network, models, EPANET leak simulation
import scoring
import store

log = logging.getLogger("dlowatch")

router = APIRouter()
LOCK = threading.RLock()

STEP_S = 300                      # one reading every 5 minutes
PER_DAY = 86400 // STEP_S         # 288 readings a day
HISTORY_DAYS = 14
NOW = int(time.time()) // STEP_S * STEP_S   # demo clock, fixed at start-up

# 24h demand pattern (mean about 1), low at night, peaks morning and evening
PATTERN = np.array([0.42, 0.38, 0.36, 0.35, 0.38, 0.58, 0.95, 1.38, 1.45, 1.22, 1.06, 1.02,
                    1.06, 1.0, 0.96, 0.97, 1.08, 1.3, 1.46, 1.32, 1.12, 0.92, 0.7, 0.54])
PATTERN = PATTERN / PATTERN.mean()


SETTINGS_DIR = Path(os.environ.get("DLOWATCH_CONFIG_DIR") or Path(__file__).with_name("config"))
PRIORITY_DEFAULT = {
    "weights": {"water": 0.7, "households": 0.15, "ease": 0.15},
    "household_use_litres_per_day": 600,
    "suspects": {"top": 5, "location_spread_m": 300, "stretch_size_m": 300, "max_pipes_per_stretch": 25},
    "ease": {"big_pipe_mm": 300, "medium_pipe_mm": 200,
             "busy_roads": ["trunk", "trunk_link", "primary", "primary_link"], "wide_search_km": 2.0},
}


def load_settings(name, default):
    """Read config/<name>. Missing or broken file: warn and use the defaults. Missing keys fall back one by one."""
    path = SETTINGS_DIR / name
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.warning("Settings file %s not found, using defaults", path)
        return default
    except Exception as e:
        log.warning("Settings file %s could not be read (%s), using defaults", path, e)
        return default
    out = {}
    for k, v in default.items():
        out[k] = {**v, **data.get(k, {})} if isinstance(v, dict) and isinstance(data.get(k), dict) else data.get(k, v)
    return out



# ============================================================== auth
# Key that signs login tokens. DLOWATCH_SECRET if set; otherwise a random key made on the first start and kept
# in the database (see start()), so nobody can sign tokens with a key that is visible in the code.
SECRET = os.environ.get("DLOWATCH_SECRET", "").encode() or os.urandom(32).hex().encode()
TOKEN_HOURS = 12

# Demo accounts and crews come from config/people.json. In a real system accounts live in a database
# with hashed passwords. If the file is missing or broken, these defaults are used.
PEOPLE_DEFAULT = {
    "staff": {
        "operator@dlowatch.mu": {"password": "demo123", "role": "operator", "name": "Night operator"},
        "admin@dlowatch.mu": {"password": "admin123", "role": "admin", "name": "Admin"},
        "manager@dlowatch.mu": {"password": "manager123", "role": "manager", "name": "Network manager"},
    },
    "crew_logins": {
        "CW-0108": {"pin": "1111", "crew_id": "C1", "name": "Crew 1"},
        "CW-0217": {"pin": "1234", "crew_id": "C2", "name": "Crew 2"},
        "CW-0350": {"pin": "3333", "crew_id": "C3", "name": "Maintenance"},
    },
    "crews": {
        "C1": {"id": "C1", "name": "Crew 1", "people": "2 people · van", "base": "Port Louis depot"},
        "C2": {"id": "C2", "name": "Crew 2", "people": "2 people · van", "base": "Pamplemousses depot"},
        "C3": {"id": "C3", "name": "Maintenance", "people": "Meter and sensor team", "base": "Head office"},
    },
}


def load_people():
    """Each section in the file replaces the default section; a missing or empty section keeps the default."""
    path = SETTINGS_DIR / "people.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.warning("Settings file %s not found, using the demo accounts", path)
        raw = {}
    except Exception as e:
        log.warning("Settings file %s could not be read (%s), using the demo accounts", path, e)
        raw = {}
    return {k: raw[k] if isinstance(raw.get(k), dict) and raw[k] else v for k, v in PEOPLE_DEFAULT.items()}


PEOPLE = load_people()
STAFF = PEOPLE["staff"]
CREW_USERS = PEOPLE["crew_logins"]


def make_token(payload):
    payload = {**payload, "exp": int(time.time()) + TOKEN_HOURS * 3600}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def read_token(token):
    try:
        body, sig = token.split(".")
        good = hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, good):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return payload if payload["exp"] > time.time() else None
    except Exception:
        return None


def current_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Please sign in")
    user = read_token(authorization[7:])
    if user is None:
        raise HTTPException(401, "Your session has ended, please sign in again")
    return user


def require(*roles):
    def check(user=Depends(current_user)):
        if user["role"] not in roles:
            raise HTTPException(403, "Your account cannot open this page")
        return user
    return check


STAFF_ONLY = require("operator", "admin", "manager")   # can look at everything
OPS_ONLY = require("operator", "admin")      # can act: send crews, close alerts
ADMIN_ONLY = require("admin")                 # system test page (test leaks, AI assist switch)
CREW_ONLY = require("crew")


class StaffLogin(BaseModel):
    email: str
    password: str


class CrewLogin(BaseModel):
    staff_id: str
    pin: str


@router.post("/auth/staff")
def login_staff(body: StaffLogin):
    login = body.email.strip().lower()
    if DB["store"]:
        u = DB["store"].check_user(login, "staff", body.password)
    else:
        u = STAFF.get(login)
        u = u if u and hmac.compare_digest(u["password"], body.password) else None
    if not u:
        raise HTTPException(401, "Wrong email or password")
    user = {"sub": body.email.strip().lower(), "role": u["role"], "name": u["name"]}
    return {"token": make_token(user), "user": user}


@router.post("/auth/crew")
def login_crew(body: CrewLogin):
    sid = body.staff_id.strip().upper()
    if DB["store"]:
        u = DB["store"].check_user(sid, "crew", body.pin)
    else:
        u = CREW_USERS.get(sid)
        u = u if u and hmac.compare_digest(u["pin"], body.pin) else None
    if not u:
        raise HTTPException(401, "Wrong staff ID or PIN")
    user = {"sub": sid, "role": "crew", "name": u["name"], "crew_id": u["crew_id"]}
    return {"token": make_token(user), "user": user}


@router.get("/auth/me")
def me(user=Depends(current_user)):
    return user


# ============================================================== zones and map
def zone_centroid(feature):
    ring = feature["geometry"]["coordinates"][0]
    return (sum(p[1] for p in ring) / len(ring), sum(p[0] for p in ring) / len(ring))


PLACES = [("Port Louis", -20.162, 57.501), ("Roche Bois", -20.141, 57.521),
          ("Baie du Tombeau", -20.118, 57.512), ("Terre Rouge", -20.125, 57.528),
          ("Pamplemousses", -20.104, 57.571), ("Triolet", -20.057, 57.548),
          ("Grand Baie", -20.013, 57.581), ("Goodlands", -20.036, 57.645),
          ("Riviere du Rempart", -20.102, 57.684), ("Poudre d'Or", -20.058, 57.684),
          ("Cap Malheureux", -19.985, 57.614), ("Plaine des Papayes", -20.071, 57.581),
          ("Long Mountain", -20.137, 57.556), ("Sainte-Croix", -20.153, 57.523),
          ("Calebasses", -20.124, 57.597), ("Mapou", -20.071, 57.613)]


def km(a_lat, a_lon, b_lat, b_lon):
    dlat, dlon = math.radians(b_lat - a_lat), math.radians(b_lon - a_lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a_lat)) * math.cos(math.radians(b_lat)) * math.sin(dlon / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def name_zones():
    names, used = {}, set()
    for f in api.GEO["zones"]["features"]:
        lat, lon = zone_centroid(f)
        for name, plat, plon in sorted(PLACES, key=lambda p: km(lat, lon, p[1], p[2])):
            if name not in used:
                used.add(name)
                names[f["properties"]["zone"]] = name
                break
    return names


ZONE_NAMES = name_zones()
ZONE_PROPS = {f["properties"]["zone"]: f["properties"] for f in api.GEO["zones"]["features"]}
NODE = {f["properties"]["id"]: f for f in api.GEO["nodes"]["features"]}
PIPE = {f["properties"]["id"]: f["properties"] for f in api.GEO["pipes"]["features"]}


def mu_time(t):
    """Clock time in Mauritius (UTC+4), whatever the server's own time zone is."""
    return time.strftime("%H:%M", time.gmtime(t + 4 * 3600))


def zone_label(z):
    return f"{ZONE_NAMES.get(z, z)} ({z})"


_ZONE_PTS = {}


def nearest_zone(lat, lon):
    """The zone whose pipe junctions are closest to this spot, and how far the closest one is (m)."""
    if not _ZONE_PTS:
        for f in api.GEO["nodes"]["features"]:
            p = f["properties"]
            if p["id"].startswith("J_") and p.get("zone") in api.ZONES:
                _ZONE_PTS.setdefault(p["zone"], []).append(f["geometry"]["coordinates"])
        for z in list(_ZONE_PTS):
            _ZONE_PTS[z] = np.array(_ZONE_PTS[z])
    kx = 111320 * math.cos(math.radians(lat))
    best = (None, float("inf"))
    for z, pts in _ZONE_PTS.items():
        d = float(np.sqrt((((pts[:, 0] - lon) * kx) ** 2 + ((pts[:, 1] - lat) * 110540) ** 2).min()))
        if d < best[1]:
            best = (z, d)
    return best


def point_in_zone(lat, lon):
    """
    The zone a spot belongs to. Inside a zone outline, that zone. Just outside every outline (the outlines
    hug the pipes, so homes at a zone's edge or in the gap between two zones fall out), the zone with a pipe
    junction within zone_snap_m. Farther than that: None, outside the service area.
    """
    z = _inside_outline(lat, lon)
    if z:
        return z
    near, dist = nearest_zone(lat, lon)
    snap = (globals().get("PUBLIC") or {}).get("zone_snap_m", 400)
    return near if dist <= snap else None


def _inside_outline(lat, lon):
    for f in api.GEO["zones"]["features"]:
        ring = f["geometry"]["coordinates"][0]
        inside, j = False, len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i][0], ring[i][1]
            xj, yj = ring[j][0], ring[j][1]
            if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-12) + xi:
                inside = not inside
            j = i
        if inside:
            return f["properties"]["zone"]
    return None


def streets_of(pipe_ids, n=3):
    c = Counter(PIPE[p].get("street") for p in pipe_ids if p in PIPE and PIPE[p].get("street"))
    return [s for s, _ in c.most_common(n)]


# ============================================================== leaks known to the demo
# Each leak changes the meter readings from its start until it is fixed.
LEAKS = {}      # leak_id -> {zone, pipe_id, flow_lps, start, end, dp_mm: {sensor: mm}}
VERSION = {"n": 0}


def _by_demand(t, night, morning):
    """A leak's effect between its 3am (night) and 8am (morning) values, following the demand pattern."""
    p3, p8 = PATTERN[3], PATTERN[8]
    w = np.clip((demand_pattern_at(t) - p3) / (p8 - p3), 0.0, 1.3)
    return night + (morning - night) * w


def leak_flow_at(zone, t):
    """Extra inflow (L/s) caused by leaks in this zone at times t (numpy array)."""
    extra = np.zeros_like(t, dtype=float)
    for lk in LEAKS.values():
        if lk["zone"] != zone:
            continue
        end = lk["end"] or 10 ** 12
        on = (t >= lk["start"]) & (t < end)
        q = _by_demand(t, lk["flow_lps"], lk.get("flow_lps_h8", lk["flow_lps"]))
        if lk["kind"] == "slow":       # a slow leak grows over its first day
            q = q * np.clip((t - lk["start"]) / 86400, 0.25, 1.0)
        extra += np.where(on, q, 0)
    return extra


def leak_dp_at(zone, sensor, t):
    drop = np.zeros_like(t, dtype=float)
    for lk in LEAKS.values():
        if lk["zone"] != zone:
            continue
        end = lk["end"] or 10 ** 12
        on = (t >= lk["start"]) & (t < end)
        night = lk["dp_mm"].get(sensor, 0.0) / 1000.0
        morning = (lk.get("dp_mm_h8") or {}).get(sensor, lk["dp_mm"].get(sensor, 0.0)) / 1000.0
        d = _by_demand(t, night, morning)
        if lk["kind"] == "slow":
            d = d * np.clip((t - lk["start"]) / 86400, 0.25, 1.0)
        drop += np.where(on, d, 0)
    return drop


# ============================================================== meters and noise
def meters():
    out = []
    for z in api.ZONES:
        out.append({"id": api.ZONES[z]["inlet"], "zone": z, "type": "flow", "unit": "L/s"})
        for s in api.ZONES[z]["sensors"]:
            out.append({"id": s, "zone": z, "type": "pressure", "unit": "m"})
    return out


METERS = {m["id"]: m for m in meters()}

# Acoustic noise loggers: a third kind of sensor, next to the flow and pressure meters (see acoustic.py)
ACOUSTIC = acoustic.load_settings(SETTINGS_DIR)
LOGGERS = acoustic.place_loggers(api.GEO, api.ZONES, ACOUSTIC)
LOGGER_COVERAGE = acoustic.pipe_coverage(api.GEO, LOGGERS, ACOUSTIC)
log.info("%d acoustic loggers, %d%% of pipe length within hearing range", len(LOGGERS), LOGGER_COVERAGE["overall"])


def pick_fault_meters():
    """Give a handful of meters a realistic problem, the same every start-up."""
    zones = list(api.ZONES)
    s = {z: api.ZONES[z]["sensors"] for z in zones}
    faults = {}
    for z in zones:
        faults[s[z][0]] = "spikes"
    faults[api.ZONES[zones[0]]["inlet"]] = "spikes"
    faults[s[zones[1]][2]] = "drift"
    faults[s[zones[5 % len(zones)]][1]] = "stuck"
    faults[s[zones[7 % len(zones)]][3]] = "offline"
    faults[api.ZONES[zones[3 % len(zones)]]["inlet"]] = "reset"
    faults[s[zones[4 % len(zones)]][5]] = "duplicates"
    faults[s[zones[2 % len(zones)]][4]] = "gaps"
    return faults


FAULTS = pick_fault_meters()


def seed_of(text):
    return int(hashlib.md5(text.encode()).hexdigest()[:8], 16)


def day_number(t):
    """Day number in Mauritius time (UTC+4), for anything that changes day by day."""
    return (np.asarray(t) + 4 * 3600) // 86400


def demand_pattern_at(t):
    hours = ((t + 4 * 3600) // 3600) % 24
    frac = (((t + 4 * 3600) % 3600) / 3600)
    return PATTERN[hours] * (1 - frac) + PATTERN[(hours + 1) % 24] * frac


def true_series(meter_id, t):
    """What the meter should read with no noise (still includes leaks). Same answer for the same time, always."""
    m = METERS[meter_id]
    pat = demand_pattern_at(t)
    days = day_number(t)
    scale = {int(d): 1 + np.random.default_rng(seed_of(f"{m['zone']}day{int(d)}")).normal(0, 0.02) for d in np.unique(days)}
    day_scale = np.array([scale[int(d)] for d in days])
    if m["type"] == "flow":
        avg = ZONE_PROPS[m["zone"]]["avg_demand_lps"]
        return avg * pat * day_scale + leak_flow_at(m["zone"], t)
    base = NODE[meter_id]["properties"].get("pressure_8am_m", 30.0)
    # pressure is higher at night when little water is used
    return base + 6.0 * (PATTERN.max() - pat) / PATTERN.max() - leak_dp_at(m["zone"], meter_id, t)


FAULT_EPOCH = {"t": NOW}     # when the meter faults began; kept in the database so they stay put


def raw_series(meter_id, days):
    """Meter readings as they arrive: noise, the odd lost reading, and the meter's own fault.
    Noise is fixed per meter and per day, so past readings never change as time moves on."""
    m = METERS[meter_id]
    start = NOW - days * 86400
    t = np.arange(start, NOW, STEP_S)
    v = true_series(meter_id, t)
    n = len(t)
    noise = np.empty(n)
    keep = np.ones(n, bool)
    spikes = np.zeros(n, bool)
    gaps = np.zeros(n, bool)
    dups = []
    fault = FAULTS.get(meter_id)
    dn = day_number(t)
    for d in np.unique(dn):
        idx = np.nonzero(dn == d)[0]
        rng = np.random.default_rng(seed_of(f"{meter_id}-{int(d)}"))
        sd = 0.004 * abs(float(np.mean(v[idx]))) if m["type"] == "flow" else 0.005   # pressure loggers: about 5 mm
        noise[idx] = rng.normal(0, sd, len(idx))
        keep[idx[rng.random(len(idx)) < 0.004]] = False                            # the odd lost reading
        if fault == "spikes":
            spikes[idx[rng.choice(len(idx), min(2, len(idx)), replace=False)]] = True
        if fault == "gaps" and len(idx) > 20:
            g = int(rng.integers(0, len(idx) - 6))
            gaps[idx[g:g + int(rng.integers(3, 6))]] = True
        if fault == "duplicates" and len(idx) > 4:
            dups.extend(idx[rng.choice(len(idx) - 2, 2, replace=False)].tolist())
    v = v + noise
    ep = FAULT_EPOCH["t"]
    if fault == "spikes":
        mult = np.random.default_rng(seed_of(meter_id + "x")).choice([2.6, 3.2, 0.15], n)
        v[spikes] = v[spikes] * mult[spikes]
    if fault == "gaps":
        keep[gaps] = False
    if fault == "drift":                                   # reads about 1% higher each day (began 10 days before the first start), up to 14%
        v = v * (1 + np.clip((t - (ep - 10 * 86400)) / 86400 * 0.01, 0, 0.14))
    if fault == "stuck":                                   # stuck at one value since 6 h before the faults began
        t0 = ep - 6 * 3600
        on = t >= t0
        if on.any():
            v[on] = round(float(true_series(meter_id, np.array([t0]))[0]), 2)
    if fault == "offline":                                 # silent since 3 h 20 before the faults began
        keep[t >= ep - 200 * 60] = False
    if fault == "reset":                                   # one counter reset, then an impossible jump
        k = np.searchsorted(t, ep - 130 * STEP_S)
        if 0 <= k < n - 2:
            v[k] = -abs(v[k]) * 3
            v[k + 2] = v[k + 2] * 40
    v = np.round(v, 3)
    t, v = t[keep], v[keep]
    if fault == "duplicates" and dups:
        dup = np.array(sorted(i for i in dups if i < len(t)))
        t = np.insert(t, dup, t[dup])
        v = np.insert(v, dup, v[dup])
        swap = len(t) - 30
        if swap > 0:
            t[swap], t[swap + 1] = t[swap + 1], t[swap]
    return t, v


# ============================================================== cleaning (DQ-01..06)
DQ_RULES = [
    {"code": "DQ-01", "name": "Spikes", "looks": "One reading jumps far up or down, then goes back",
     "fix": "replaced with the median of nearby readings"},
    {"code": "DQ-02", "name": "Missing readings", "looks": "Gaps when the signal or battery drops",
     "fix": "gaps up to 30 min filled in a straight line, longer gaps left empty"},
    {"code": "DQ-03", "name": "Stuck meter", "looks": "Exactly the same number for 3 hours or more",
     "fix": "readings ignored and the meter marked faulty"},
    {"code": "DQ-04", "name": "Impossible values", "looks": "Below zero, or far more than the pipe can carry",
     "fix": "reading dropped"},
    {"code": "DQ-05", "name": "Drift", "looks": "Slowly reads higher or lower than it should",
     "fix": "compared with its expected level, trust lowered"},
    {"code": "DQ-06", "name": "Time problems", "looks": "Duplicate or out of order timestamps",
     "fix": "duplicates removed, sorted by time"},
]


def clean(meter_id, t, v):
    m = METERS[meter_id]
    fixes = Counter()
    events = []
    # DQ-06: order and duplicates
    order = np.argsort(t, kind="stable")
    if np.any(order != np.arange(len(t))):
        fixes["DQ-06"] += int(np.sum(order != np.arange(len(t))) // 2) or 1
    t, v = t[order], v[order]
    _, first = np.unique(t, return_index=True)
    dups = len(t) - len(first)
    if dups:
        fixes["DQ-06"] += dups
    t, v = t[first], v[first].astype(float)

    # DQ-04: impossible values
    if m["type"] == "flow":
        limit = ZONE_PROPS[m["zone"]]["avg_demand_lps"] * 4
    else:
        limit = 150.0
    bad = (v < 0) | (v > limit)
    for i in np.where(bad)[0]:
        events.append({"code": "DQ-04", "t": int(t[i]), "raw": float(v[i]), "action": "dropped"})
    fixes["DQ-04"] += int(bad.sum())
    v = np.where(bad, np.nan, v)

    # DQ-01: spikes, against a rolling median of the 7 readings around each point
    # (skipped when the meter sent nothing at all: it is then marked offline below)
    if len(v) and not np.all(np.isnan(v)):
        pad = 3
        padded = np.pad(v, pad, mode="edge")
        windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * pad + 1)
        with np.errstate(all="ignore"):
            med = np.nanmedian(windows, axis=1)
            dev = np.abs(v - med)
            mad = np.nanmedian(np.abs(windows - med[:, None]), axis=1)
            scale = np.maximum(mad * 6, np.nanmedian(np.abs(v)) * 0.25)
        spike = (dev > scale) & ~np.isnan(v)
        for i in np.where(spike)[0]:
            events.append({"code": "DQ-01", "t": int(t[i]), "raw": float(v[i]), "action": f"replaced with {med[i]:.2f}"})
        fixes["DQ-01"] += int(spike.sum())
        v = np.where(spike, med, v)

    # DQ-03: stuck (same value for 36 readings = 3 hours)
    status = "healthy"
    run_start, stuck_run = 0, None
    for i in range(1, len(v) + 1):
        if i == len(v) or v[i] != v[run_start]:
            if i - run_start >= 36 and not np.isnan(v[run_start]):
                stuck_run = (run_start, i)
            run_start = i
    if stuck_run:
        a, b = stuck_run
        v[a:b] = np.nan
        fixes["DQ-03"] += b - a
        events.append({"code": "DQ-03", "t": int(t[a]), "raw": None,
                       "action": f"same value for {(b - a) * 5 // 60} h, readings ignored"})
        status = "stuck"

    # DQ-02: put on a regular 5 minute grid, fill short gaps
    grid = np.arange(t.min() if len(t) else NOW, NOW, STEP_S) if len(t) else np.arange(NOW - 86400, NOW, STEP_S)
    grid = np.arange(NOW - (NOW - grid[0]) // STEP_S * STEP_S, NOW, STEP_S)
    vals = np.full(len(grid), np.nan)
    pos = ((t - grid[0]) // STEP_S).astype(int)
    ok = (pos >= 0) & (pos < len(grid))
    vals[pos[ok]] = v[ok]
    filled = np.zeros(len(grid), bool)
    isnan = np.isnan(vals)
    i = 0
    while i < len(vals):
        if isnan[i]:
            j = i
            while j < len(vals) and isnan[j]:
                j += 1
            gap = j - i
            if 0 < i and j < len(vals) and gap <= 6 and not (stuck_run and status == "stuck"):
                vals[i:j] = np.linspace(vals[i - 1], vals[j], gap + 2)[1:-1]
                filled[i:j] = True
                fixes["DQ-02"] += gap
                events.append({"code": "DQ-02", "t": int(grid[i]), "raw": None,
                               "action": f"{gap * 5} min gap filled"})
            elif gap > 6 and j == len(vals):
                status = "offline" if status == "healthy" else status
            i = j
        else:
            i += 1

    # DQ-05: drift against what the meter should read over the last day
    drift_pct = 0.0
    if m["type"] == "pressure":
        recent = slice(-PER_DAY, None)
        expected = true_series(meter_id, grid[recent])
        obs = vals[recent]
        okk = ~np.isnan(obs)
        if okk.sum() > 50:
            drift_pct = float(np.median(obs[okk] / expected[okk]) - 1) * 100
            if abs(drift_pct) >= 6:
                fixes["DQ-05"] += 1
                events.append({"code": "DQ-05", "t": int(grid[-1]), "raw": None,
                               "action": f"reads {drift_pct:+.0f}% against expected"})
                if status == "healthy":
                    status = "drifting"

    trust = 100.0
    trust -= min(20, fixes["DQ-01"] * 0.4)
    trust -= min(10, fixes["DQ-02"] * 0.1)
    trust -= min(10, fixes["DQ-04"] * 2)
    trust -= min(10, fixes["DQ-06"] * 1)
    if status == "drifting":
        trust = min(trust, 74)
    if status == "stuck":
        trust = 20
    if status == "offline":
        trust = 0
    if status == "healthy" and trust < 95:
        status = "noisy"
    return {"t": grid, "v": vals, "filled": filled, "fixes": dict(fixes),
            "events": sorted(events, key=lambda e: e["t"])[-40:], "status": status, "trust": round(max(trust, 0)), "drift_pct": round(drift_pct, 1)}


CACHE = {"version": -1, "flow": {}, "pressure": {}}


def refresh():
    """Rebuild the cleaned series when leaks change. Cheap enough for 72 meters."""
    with LOCK:
        if CACHE["version"] == (VERSION["n"], NOW):
            return CACHE
        flow, pressure = {}, {}
        for mid, m in METERS.items():
            days = HISTORY_DAYS if m["type"] == "flow" else 8
            t, v = raw_series(mid, days)
            c = clean(mid, t, v)
            # meter health (trust, fixes, status) is judged on the last 3 days, whatever length of history is kept
            recent = t >= NOW - 3 * 86400
            h = clean(mid, t[recent], v[recent])
            c.update({k: h[k] for k in ("fixes", "events", "status", "trust", "drift_pct")})
            c["raw_t"], c["raw_v"] = t, v
            (flow if m["type"] == "flow" else pressure)[mid] = c
        CACHE.update(version=(VERSION["n"], NOW), flow=flow, pressure=pressure)
        return CACHE


LOUD_DB = 4.0     # a night counts as loud when it is this many dB above the logger's usual level
STEADY_DB = 3.0   # loud nights in a run must be this close to each other (leak noise is steady)
LOGGER_CACHE = {"version": None, "levels": {}, "causes": {}}


def logger_nights(days=HISTORY_DAYS):
    """3 am of each of the last few nights whose 2 to 4 am recording is finished, oldest first."""
    last = next_3am(NOW) - 86400
    if last + 3600 > NOW:
        last -= 86400
    return [last - 86400 * i for i in range(days - 1, -1, -1)]


def logger_levels():
    """Noise level per logger per night. Rebuilt when leaks change or a new night is recorded."""
    with LOCK:
        nights = logger_nights()
        key = (VERSION["n"], nights[-1])
        if LOGGER_CACHE["version"] != key:
            levels, causes = acoustic.night_levels(LOGGERS, list(LEAKS.values()), nights, ACOUSTIC, seed_of)
            LOGGER_CACHE.update(version=key, levels=levels, causes=causes)
        return LOGGER_CACHE["levels"]


def logger_state(lid):
    """Last night against the usual level (median of the nights before the last 3)."""
    rows = logger_levels()[lid]
    usual = float(np.median([r["db"] for r in rows[:-3]]))
    last = rows[-1]["db"]
    loud = [r["db"] - usual >= LOUD_DB for r in rows[-3:]]
    streak = 0
    for i in range(len(loud) - 1, -1, -1):
        if not loud[i]:
            break
        # leak noise is steady: a loud night only extends the run if it is close to the next one
        if streak and abs(rows[-3 + i]["db"] - rows[-3 + i + 1]["db"]) > STEADY_DB:
            break
        streak += 1
    return {"last_db": last, "usual_db": round(usual, 1), "change_db": round(last - usual, 1),
            "loud_nights": streak, "status": "noisy" if streak >= 2 else "loud once" if streak == 1 else "quiet"}


def zone_loggers(zone):
    """Every logger in a zone with its state, loudest change first."""
    out = [{**lg, **logger_state(lid)} for lid, lg in LOGGERS.items() if lg["zone"] == zone]
    return sorted(out, key=lambda x: -x["change_db"])


def heard_by_loggers(a):
    """Had the loggers recorded this leak yet? They need it running through the last 2 nights."""
    return a["kind"] != "burst" and a.get("started", NOW) <= logger_nights()[-2] - 3600


def noise_area(zone, rs, base=None):
    """
    Where the noisy loggers point: the loudest noisy logger, plus noisy loggers within group_radius_m of it,
    centred on them weighted by how much louder they are. None when no logger in the zone is noisy.
    With base (the meters' or model's search), only loggers inside or near those areas are used, so a noisy
    logger elsewhere in the zone (another leak, or street noise) does not pull the search away.
    """
    if not (rs.get("points") or {}).get("acoustic"):
        return None
    cfg = rs.get("acoustic_search") or {}
    noisy = [x for x in zone_loggers(zone) if x["status"] == "noisy"]
    if base and base.get("areas"):
        margin = cfg.get("match_margin_m", 300)
        noisy = [x for x in noisy if any(km(ar["centre_latlon"][0], ar["centre_latlon"][1], x["lat"], x["lon"]) * 1000
                                         <= ar["radius_m"] + margin for ar in base["areas"] if ar.get("area") != "N1")]
    if not noisy:
        return None
    top = noisy[0]
    group = [x for x in noisy if km(top["lat"], top["lon"], x["lat"], x["lon"]) * 1000 <= cfg.get("group_radius_m", 600)]
    w = [max(0.1, x["change_db"]) for x in group]
    lat = sum(x["lat"] * wi for x, wi in zip(group, w)) / sum(w)
    lon = sum(x["lon"] * wi for x, wi in zip(group, w)) / sum(w)
    radius = ACOUSTIC["hearing_range_m"]
    return {"area": "N1", "probability": None, "centre_latlon": [round(lat, 6), round(lon, 6)], "radius_m": radius,
            "pipes": pipes_near(zone, lat, lon, radius), "from_loggers": [x["id"] for x in group]}


def apply_loggers(a):
    """
    Put the noisy loggers' area first in the alert's search and move the best guess onto it.
    The search from the meters or the model is kept in search_meters, so this can be redone every scan.
    """
    if a["kind"] == "public" or a["status"] != "open":
        return
    rs = scoring.get(a.get("ruleset", RULESET))
    base = a.get("search_meters") or a.get("search") or {}
    # 2 steady loud nights already show the loggers heard it, so only fresh bursts are left out here;
    # the start time is an estimate and is only used for the quiet-logger ranking
    na = noise_area(a["zone"], rs, base) if a["kind"] != "burst" else None
    if not na:
        if a.get("search_meters"):
            a["search"] = a.pop("search_meters")
        return
    a["search_meters"] = base
    a["search"] = {**base, "best_guess": na["centre_latlon"], "areas": [na] + [x for x in base.get("areas", []) if x.get("area") != "N1"],
                   "loggers": na["from_loggers"]}


def quiet_logger_points(zone, rs):
    """Quiet loggers in a zone, in metres, for ranking pipes next to them lower."""
    pts = [_xy(x["lat"], x["lon"]) for x in zone_loggers(zone) if x["status"] == "quiet"]
    return np.array(pts) if pts else None


def meter_trust(mid):
    c = refresh()
    d = c["flow"].get(mid) or c["pressure"].get(mid)
    return d["trust"] if d else 0


# ============================================================== night flow and water balance
def night_flows(zone):
    """Average cleaned inflow from 02:00 to 04:00 each night, oldest first."""
    c = refresh()["flow"][api.ZONES[zone]["inlet"]]
    t, v = c["t"], c["v"]
    local_h = ((t + 4 * 3600) // 3600) % 24
    day = (t + 4 * 3600) // 86400
    out = []
    for d in np.unique(day):
        sel = (day == d) & (local_h >= 2) & (local_h < 4)
        if sel.sum() >= 12 and not np.all(np.isnan(v[sel])):
            out.append({"night": int(d * 86400 - 4 * 3600 + 3 * 3600), "lps": round(float(np.nanmean(v[sel])), 2)})
    return out


def usual_night(nights, i, rs):
    """The usual night flow for night i. Same weekday over the last few weeks when the ruleset asks for it and
    there is enough history; otherwise the median of the oldest 7 nights in the window (wl-1.2 behaviour)."""
    b = rs.get("baseline") or {}
    if b.get("mode") == "same_weekday":
        idx = [i - 7 * w for w in range(1, b.get("weeks", 4) + 1)]
        if idx[-1] >= 0:
            return float(np.median([nights[j]["lps"] for j in idx])), True
    return (float(np.median([n["lps"] for n in nights[:7]])) if len(nights) >= 7 else nights[0]["lps"]), False


def zone_flow_stats(zone, rs=None):
    rs = rs or scoring.get(scoring.ACTIVE)
    nights = night_flows(zone)
    last_i = len(nights) - 1
    base, weekday_ok = usual_night(nights, last_i, rs)
    last = nights[-1]["lps"]
    pct = (last - base) / base * 100 if base else 0
    high_pct = min(t for t, _ in rs["points"]["night_pct"])
    running = 0
    for i in range(last_i, -1, -1):
        b_i = usual_night(nights, i, rs)[0]
        if b_i and (nights[i]["lps"] - b_i) / b_i * 100 >= high_pct:
            running += 1
        else:
            break
    c = refresh()["flow"][api.ZONES[zone]["inlet"]]
    t, v = c["t"][-PER_DAY:], c["v"][-PER_DAY:]
    m = METERS[api.ZONES[zone]["inlet"]]
    pat_h = ((t + 4 * 3600) // 3600) % 24
    expected = ZONE_PROPS[zone]["avg_demand_lps"] * PATTERN[pat_h]
    okk = ~np.isnan(v)
    water_in = float(np.nansum(v[okk])) * STEP_S / 1000
    exp_m3 = float(np.sum(expected[okk])) * STEP_S / 1000
    gap_pct = (water_in - exp_m3) / exp_m3 * 100 if exp_m3 else 0
    # flow in the last hour against the same hour on the previous 7 days
    vv = c["v"]
    now_mean = float(np.nanmean(vv[-12:]))
    prev = [np.nanmean(vv[-12 - k * PER_DAY: len(vv) - k * PER_DAY]) for k in range(1, 8) if len(vv) > 12 + k * PER_DAY]
    usual_now = float(np.nanmedian(prev)) if prev else now_mean
    now_pct = (now_mean - usual_now) / usual_now * 100 if usual_now else 0
    return {"nights": nights, "baseline_lps": round(base, 2), "last_night_lps": last,
            "now_lps": round(now_mean, 2), "usual_now_lps": round(usual_now, 2), "now_pct": round(now_pct, 1),
            "night_pct": round(pct, 1), "nights_running": running,
            "water_in_m3": round(water_in), "expected_m3": round(exp_m3),
            "balance_gap_pct": round(gap_pct, 1), "inlet_trust": meter_trust(m["id"]),
            "weekday_baseline": weekday_ok}


# ============================================================== alerts and the rules engine
ALERTS = {}      # alert_id -> alert dict (our own store, richer than api.ALERTS)
REPORTS = {}     # code -> public report
JOBS = {}        # job_id -> job
LABELS = []      # crew results, used to test and retrain the model
CREWS = PEOPLE["crews"]


def crew_name(crew_id):
    """Name of a crew. Old jobs can point to a crew that was later removed from people.json."""
    return CREWS.get(crew_id, {}).get("name", crew_id)
RULESET = scoring.ACTIVE           # newest ruleset, used for new alerts


# ---------------------------------------------------------------- the anomaly model, with a fallback
# The model only helps. If it is switched off, times out or fails, alerts still come from the meters.
MODEL = {"enabled": os.environ.get("DLOWATCH_MODEL", "on").lower() != "off",
         "timeout_s": float(os.environ.get("DLOWATCH_MODEL_TIMEOUT", "5")),
         "last_error": None, "last_ok": None}
_POOL = ThreadPoolExecutor(max_workers=2)


def model_status():
    if not MODEL["enabled"]:
        return "off"
    return "unavailable" if MODEL["last_error"] and (MODEL["last_ok"] or 0) < MODEL["last_error"][0] else "online"


def ask_model(zone, readings):
    """(prediction, status). status is ok, off or unavailable. Never raises."""
    if not MODEL["enabled"]:
        return None, "off"
    if zone not in api.MODELS:
        MODEL["last_error"] = (time.time(), f"no model for zone {zone}")
        return None, "unavailable"
    try:
        fut = _POOL.submit(lambda: api.add_latlon(api.predict(api.MODELS, zone, readings)))
        pred = fut.result(timeout=MODEL["timeout_s"])
        MODEL["last_ok"] = time.time()
        return pred, "ok"
    except FuturesTimeout:
        MODEL["last_error"] = (time.time(), f"no answer within {MODEL['timeout_s']} s")
    except Exception as e:      # a broken model file must not stop the alerts
        MODEL["last_error"] = (time.time(), f"{type(e).__name__}: {e}")
    log.warning("Model unavailable for %s: %s", zone, MODEL["last_error"][1])
    return None, "unavailable"


def simulate(pipe_id, size, demand_scale=1.0, sensor_noise_mm=0.0, rng=None, coeff=None):
    """Run an EPANET leak on one pipe and return what the meters would see (no model).
    demand_scale and sensor_noise_mm add the same kind of noise the training data had."""
    zone = api.PIPE_ZONE.get(pipe_id)
    if zone not in api.ZONES:
        raise HTTPException(400, "Pick a pipe inside a zone")
    wn = wntr.network.WaterNetworkModel(api.ZONES[zone]["inp"])
    wn = wntr.morph.split_pipe(wn, pipe_id, f"{pipe_id}_LEAK_B", "LEAK_NODE", split_at_point=0.5)
    wn.get_node("LEAK_NODE").emitter_coefficient = (coeff if coeff is not None else api.LEAK_SIZES[size]) / 1000
    wn.options.hydraulic.demand_multiplier = demand_scale
    res = api.run_zone(wn)
    api.cleanup()
    readings = add_noise(api.features_from_results(zone, res), sensor_noise_mm, rng)
    flow = float(res.node["demand"].loc[min(api.HOURS) * 3600, "LEAK_NODE"]) * 1000
    x, y = wn.get_node("LEAK_NODE").coordinates
    lon, lat = api.to_latlon.transform(x, y)
    truth = {"pipe_id": pipe_id, "zone": zone, "size": size, "leak_flow_lps": round(flow, 2),
             "water_lost_m3_per_day": round(flow * 86.4, 1), "latlon": [round(lat, 6), round(lon, 6)]}
    return zone, readings, truth


def add_noise(readings, sensor_noise_mm, rng):
    if not sensor_noise_mm:
        return readings
    rng = rng or random.Random()
    return {k: (round(v + rng.gauss(0, sensor_noise_mm), 2) if k.startswith("dp_mm_") else v) for k, v in readings.items()}


def simulate_normal(zone, demand_scale=1.0, sensor_noise_mm=0.0, rng=None):
    """A normal day with no leak (water use a bit higher or lower than usual), as the meters would see it."""
    wn = wntr.network.WaterNetworkModel(api.ZONES[zone]["inp"])
    wn.options.hydraulic.demand_multiplier = demand_scale
    res = api.run_zone(wn)
    api.cleanup()
    return add_noise(api.features_from_results(zone, res), sensor_noise_mm, rng)


def scan_zone(zone, readings, reports=()):
    """
    What the rules say about a zone right now, without knowing of any leak.
    Used by the evaluation to count false alarms on normal days.
    """
    dp = {k.split("_h03_")[1]: v for k, v in readings.items() if "_h03_" in k}
    pred, status = ask_model(zone, readings)
    search = search_from_model(pred) if pred and pred.get("leak") else search_from_meters(zone, dp, readings)
    a = {"id": "scan", "zone": zone, "kind": "slow", "status": "open", "model": pred or {}, "model_status": status,
         "search": search, "dp_mm": dp, "reports": list(reports), "ruleset": RULESET, "started": NOW, "created": NOW,
         "streets": []}
    return a, score_alert(a)


def pipes_near(zone, lat, lon, radius_m):
    out = []
    for f in api.GEO["pipes"]["features"]:
        p = f["properties"]
        if p.get("zone") != zone or p.get("status", "open") != "open":
            continue
        if any(km(lat, lon, c[1], c[0]) * 1000 <= radius_m for c in f["geometry"]["coordinates"]):
            out.append(p["id"])
    return out


def search_from_model(pred):
    areas = [{"area": a["area"], "probability": a["probability"], "centre_latlon": a["centre_latlon"],
              "radius_m": a["radius_m"], "pipes": a["pipes_to_check"]} for a in pred.get("top_areas", [])]
    return {"source": "model", "best_guess": pred.get("best_guess_latlon"), "areas": areas,
            "flow_lps": pred.get("leak_flow_lps"), "m3_day": pred.get("water_lost_m3_per_day")}


def search_from_meters(zone, dp, readings):
    """
    No model: search around the trusted sensor with the biggest pressure drop, size from the inlet meter.
    Tested on 24 simulated leaks: typically about 700 m off, against about 300 m for the model,
    so the radius (from the ruleset) is wider.
    """
    rs = scoring.get(RULESET)
    fb = rs.get("fallback_search", {})
    flow = max(0.0, readings.get(f"inflow_change_lps_h{min(api.HOURS):02d}", 0.0))
    size = {"flow_lps": round(flow, 2) or None, "m3_day": round(flow * 86.4, 1) or None}
    trusted = [(mm, s) for s, mm in dp.items() if mm > 0 and meter_trust(s) >= rs["trust"]["min"]]
    if not trusted:
        return {"source": "meters", "best_guess": None, "areas": [], **size}
    mm, sensor = max(trusted)
    lon, lat = NODE[sensor]["geometry"]["coordinates"]
    radius = fb.get("radius_m", 1200)
    area = {"area": "S1", "probability": None, "centre_latlon": [round(lat, 6), round(lon, 6)], "radius_m": radius,
            "pipes": pipes_near(zone, lat, lon, radius), "from_sensors": [sensor]}
    return {"source": "meters", "best_guess": area["centre_latlon"], "areas": [area], **size}


# ---------------------------------------------------------------- settings files, with fallbacks
PRIORITY = load_settings("priority.json", PRIORITY_DEFAULT)

# pipe midpoints and junction demand, worked out once from the network files
PIPE_MID = {}
for _f in api.GEO["pipes"]["features"]:
    _p, _c = _f["properties"], _f["geometry"]["coordinates"]
    _m = _c[len(_c) // 2]
    PIPE_MID[_p["id"]] = {"lat": _m[1], "lon": _m[0], "length_m": _p.get("length_m") or 0, "street": _p.get("street"),
                          "d": _p.get("diameter_mm") or 0, "road": _p.get("road_type"), "zone": _p.get("zone")}
JUNCTIONS = {}
for _f in api.GEO["nodes"]["features"]:
    _p = _f["properties"]
    if _p["id"].startswith("J_") and _p.get("base_demand_lps"):
        JUNCTIONS.setdefault(_p.get("zone"), []).append((_f["geometry"]["coordinates"][1], _f["geometry"]["coordinates"][0], _p["base_demand_lps"]))


# ---------------------------------------------------------------- pipe suspects and repair priority
def _xy(lat, lon):
    """Rough metres east/north, good enough for grouping pipes into blocks across a small island."""
    return lon * 111320 * math.cos(math.radians(-20.1)), lat * 110540


NAMED_BY_ZONE = {}
for _pid, _m in PIPE_MID.items():
    if _m["street"]:
        NAMED_BY_ZONE.setdefault(_m["zone"], []).append((_m["lat"], _m["lon"], _m["street"]))


def block_name(zone, items, lat, lon):
    streets = Counter(m["street"] for _, _, m in items if m["street"])
    if streets:
        main = streets.most_common(1)[0][0]
        return main if len(streets) == 1 else f"{main} and nearby streets"
    near = min(NAMED_BY_ZONE.get(zone, []), key=lambda n: (n[0] - lat) ** 2 + (n[1] - lon) ** 2, default=None)
    return f"Side streets near {near[2]}" if near else "Unnamed side streets"


def suspects(a, keep_all=False):
    """
    Rank blocks of pipes to check. Each pipe in the search areas gets
    (area likelihood) x (closeness to the best guess). Pipes are grouped into square blocks
    (size from config), named after their main street, and the shares add up to 100%.
    """
    srch = a.get("search") or {}
    areas, g = srch.get("areas") or [], srch.get("best_guess")
    if not areas or not g:
        return []
    cfg = PRIORITY["suspects"]
    spread = max(50.0, float(cfg["location_spread_m"]))
    size = max(50.0, float(cfg.get("stretch_size_m", 300)))
    weight = {}
    rs = scoring.get(a.get("ruleset", RULESET))
    qcfg = rs.get("acoustic_search") if (rs.get("points") or {}).get("acoustic") else None
    quiet = quiet_logger_points(a["zone"], rs) if qcfg and heard_by_loggers(a) else None
    for ar in areas:
        p_area = ar["probability"] if ar.get("probability") is not None else 1.0
        for pid in ar["pipes"]:
            m = PIPE_MID.get(pid)
            if not m:
                continue
            d = km(g[0], g[1], m["lat"], m["lon"]) * 1000
            w = p_area * math.exp(-0.5 * (d / spread) ** 2)
            if quiet is not None:
                # a leak right next to a quiet logger would have been heard
                mx, my = _xy(m["lat"], m["lon"])
                if (((quiet - (mx, my)) ** 2).sum(axis=1)).min() <= qcfg.get("quiet_radius_m", 150) ** 2:
                    w *= qcfg.get("quiet_weight", 0.3)
            if w > weight.get(pid, 0):
                weight[pid] = w
    total = sum(weight.values())
    if total <= 0:
        return []
    blocks = {}
    for pid, w in weight.items():
        m = PIPE_MID[pid]
        x, y = _xy(m["lat"], m["lon"])
        blocks.setdefault((int(x // size), int(y // size)), []).append((w / total, pid, m))
    out = []
    for items in blocks.values():
        items.sort(key=lambda x: -x[0])
        share = sum(x[0] for x in items)
        lat = sum(x[0] * x[2]["lat"] for x in items) / share
        lon = sum(x[0] * x[2]["lon"] for x in items) / share
        out.append({"street": block_name(a["zone"], items, lat, lon), "probability": round(share, 3),
                    "pipes": [x[1] for x in items] if keep_all else [x[1] for x in items][: cfg["max_pipes_per_stretch"]],
                    "pipe_count": len(items), "length_m": round(sum(x[2]["length_m"] for x in items)),
                    "centre_latlon": [round(lat, 6), round(lon, 6)],
                    "distance_from_guess_m": round(km(g[0], g[1], lat, lon) * 1000),
                    "max_diameter_mm": max(x[2]["d"] for x in items),
                    "roads": sorted({x[2]["road"] for x in items if x[2]["road"]})})
    out.sort(key=lambda x: -x["probability"])
    top = out[: cfg["top"]]
    # several blocks on the same street: say which part, seen from the best guess
    names = Counter(sp["street"] for sp in top)
    for sp in top:
        if names[sp["street"]] > 1:
            d = sp["distance_from_guess_m"]
            where = compass(g, sp["centre_latlon"])
            sp["street"] = f"{sp['street']} ({where})" if where == "middle" else f"{sp['street']} ({where}, {int(round(d, -1))} m)"
    for i, sp in enumerate(top):
        sp["rank"] = i + 1
    return top


def compass(a, b):
    dy = (b[0] - a[0]) * 110540
    dx = (b[1] - a[1]) * 111320 * math.cos(math.radians(a[0]))
    if math.hypot(dx, dy) < 60:
        return "middle"
    ang = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    return ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"][int((ang + 22.5) // 45) % 8]


def households_near(zone, stretches):
    """Households supplied in the suspect blocks: junction demand inside them / household water use."""
    if not stretches:
        return None
    size = float(PRIORITY["suspects"].get("stretch_size_m", 300))
    cells = set()
    for s in stretches:
        x, y = _xy(*s["centre_latlon"])
        cells.add((int(x // size), int(y // size)))
    lps = 0.0
    for lat, lon, q in JUNCTIONS.get(zone, []):
        x, y = _xy(lat, lon)
        if (int(x // size), int(y // size)) in cells:
            lps += q
    per_day = PRIORITY["household_use_litres_per_day"] or 600
    return int(round(lps * 86400 / per_day, -1))


def repair_ease(stretches):
    """0 (hard) to 1 (easy), with the reasons, from pipe size, busy roads and how much pipe must be walked."""
    if not stretches:
        return {"value": None, "label": "unknown", "reasons": ["No search area yet"]}
    e = PRIORITY["ease"]
    d = max(s["max_diameter_mm"] for s in stretches[:3])
    roads = {r for s in stretches[:3] for r in s["roads"]}
    walk_km = sum(s["length_m"] for s in stretches[:3]) / 1000
    pipe_hard = 1.0 if d >= e["big_pipe_mm"] else 0.5 if d >= e["medium_pipe_mm"] else 0.0
    road_hard = 1.0 if roads & set(e["busy_roads"]) else 0.0
    search_hard = min(1.0, walk_km / max(0.1, e["wide_search_km"]))
    value = round(1 - (pipe_hard + road_hard + search_hard) / 3, 2)
    reasons = [f"Pipes up to {d} mm" + (" (big main)" if pipe_hard == 1 else ""),
               "Busy road, traffic control needed" if road_hard else "Quiet roads",
               f"{walk_km:.1f} km of pipe to walk in the top 3 stretches"]
    label = "easy" if value >= 0.67 else "medium" if value >= 0.34 else "hard"
    return {"value": value, "label": label, "reasons": reasons}


def repair_items():
    """Open alerts ranked by what fixing them is worth: water saved first, then households and ease."""
    rows = []
    for a in ALERTS.values():
        if a["status"] != "open":
            continue
        view = public_alert_view(a)
        sp = suspects(a)
        rows.append({**view, "suspects": sp[:3], "households_est": households_near(a["zone"], sp[:3]),
                     "ease": repair_ease(sp)})
    w = PRIORITY["weights"]
    max_m3 = max([r["water_lost_m3_per_day"] or 0 for r in rows] + [0])
    max_hh = max([r["households_est"] or 0 for r in rows] + [0])
    for r in rows:
        parts = {"water": (r["water_lost_m3_per_day"] or 0) / max_m3 if max_m3 else 0,
                 "households": (r["households_est"] or 0) / max_hh if max_hh else 0,
                 "ease": r["ease"]["value"] if r["ease"]["value"] is not None else 0}
        r["priority"] = round(100 * sum(w[k] * parts[k] for k in parts) / (sum(w.values()) or 1))
        r["priority_parts"] = {k: round(v, 2) for k, v in parts.items()}
        r["litres_per_hour"] = round((r["water_lost_m3_per_day"] or 0) * 1000 / 24) or None
    rows.sort(key=lambda r: (-r["priority"], -(r["water_lost_m3_per_day"] or 0)))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


# ---------------------------------------------------------------- detectors: data -> signals
def collect_signals(a, rs):
    """Every signal the meters, the model and the public give for this alert, plus AI findings that were rejected."""
    zone = a["zone"]
    fs = zone_flow_stats(zone, rs)
    P = rs["points"]
    full, low = rs["trust"]["full"], rs["trust"]["min"]
    inlet_w = 1.0 if fs["inlet_trust"] >= full else 0.5 if fs["inlet_trust"] >= low else 0.0
    sig, rejected = [], []

    def add(code, finding, evidence, source, pts, weight=1.0):
        if pts > 0 and weight > 0:
            sig.append({"code": code, "finding": finding, "evidence": evidence, "source": source,
                        "points": pts, "weight": weight})

    if a["kind"] != "public":
        p = fs["night_pct"]
        nf_pts = scoring.tier(p, P["night_pct"])
        need = (rs.get("persistence") or {}).get("min_nights", 0)
        if nf_pts and fs["nights_running"] < need:
            rejected.append({"code": "NF-01", "finding": f"Night flow {p:.0f}% above the usual level",
                             "reason": f"High for {fs['nights_running']} night only so far. It counts once it stays high {need} nights in a row"})
        else:
            add("NF-01", f"Night flow {p:.0f}% above the usual level",
                f"Inlet {api.ZONES[zone]['inlet']}, 02:00 to 04:00, {fs['last_night_lps']} vs usual {fs['baseline_lps']} L/s",
                "Rules", nf_pts, inlet_w)
        if a["kind"] == "burst":
            add("NF-03", f"Flow jumped {fs['now_pct']:.0f}% in the last hour",
                f"{fs['now_lps']} L/s now vs usual {fs['usual_now_lps']} L/s at this time", "Rules",
                scoring.tier(fs["now_pct"], P["now_pct"]), inlet_w)
        nr = P["nights_running"]
        if fs["nights_running"] >= nr["min_nights"]:
            add("NF-02", f"Still high {fs['nights_running']} nights in a row",
                "Night flow has not gone back to its usual level", "Rules", nr["points"], inlet_w)
        g = fs["balance_gap_pct"]
        add("WB-01", "More water going in than expected use",
            f"Last 24 h: {fs['water_in_m3']} m³ in vs {fs['expected_m3']} m³ expected (+{g:.1f}%)",
            "Rules", scoring.tier(g, P["balance_pct"]), inlet_w)
        drops = []
        for s, mm in a.get("dp_mm", {}).items():
            tr = meter_trust(s)
            if tr >= low:
                drops.append((mm * (1 if tr >= full else 0.5), s, tr))
        drops.sort(reverse=True)
        if drops:
            mm, s, tr = drops[0]
            add("PR-01", "Pressure dropped at nearby sensors",
                f"{s}: {mm:.0f} mm lower than normal (sensor trust {tr}%)", "Rules", scoring.tier(mm, P["pressure_mm"]))
        if a["kind"] == "burst":
            add("PR-02", "Sudden pressure drop with a flow jump",
                f"Started {mu_time(a['started'])}, typical of a burst main", "Rules", P["burst"])

        # AI findings, from a fixed list only, and checked against the meters when the ruleset says so
        pred = a.get("model") or {}
        meter_codes = {x["code"] for x in sig}
        if pred.get("leak_probability") is not None:
            pts = scoring.tier(pred["leak_probability"], P["ai_probability"])
            if pts:
                if rs.get("ground_ai") and not meter_codes & {"NF-01", "NF-03", "WB-01", "PR-01"}:
                    rejected.append({"code": "AI-01", "finding": "Model says this is a leak",
                                     "reason": "No meter signal agrees, so it does not count"})
                else:
                    add("AI-01", "Model says this is a leak, not normal use",
                        f"Leak probability {pred['leak_probability']:.0%} from zone {zone} detector", "AI model", pts)
        top = (pred.get("top_areas") or [None])[0]
        ar = P["ai_area"]
        if top and top.get("probability", 0) >= ar["min_probability"]:
            ok, why = area_grounded(a, top, rs)
            if rs.get("ground_ai") and not ok:
                rejected.append({"code": "AI-02", "finding": f"Model points to area {top['area']}", "reason": why})
            else:
                add("AI-02", "Model points to one search area",
                    f"Area {top['area']}: {top['probability']:.0%} likely, {top['radius_m']:.0f} m radius" + (f". {why}" if ok and rs.get("ground_ai") else ""),
                    "AI model", ar["points"])

    ac = P.get("acoustic")
    if ac and a["kind"] != "public":
        ls = zone_loggers(zone)
        noisy = [x for x in ls if x["status"] == "noisy"]
        once = [x for x in ls if x["status"] == "loud once"]
        if noisy:
            top = noisy[0]
            add("AC-01", "Leak noise at a logger",
                f"{top['id']}: {top['change_db']:.0f} dB louder than usual for {top['loud_nights']} nights in a row "
                f"({top['last_db']} vs usual {top['usual_db']} dB)", "Rules", scoring.tier(top["change_db"], ac["noise_db"]))
            mo = ac.get("more_loggers") or {}
            na = noise_area(zone, rs)
            n_group = len(na["from_loggers"]) if na else 1
            if mo and n_group >= mo.get("min_loggers", 2):
                add("AC-02", f"{n_group} loggers close together hear it",
                    ", ".join(na["from_loggers"][:4]), "Rules", mo.get("points", 0))
        elif once:
            x = once[0]
            rejected.append({"code": "AC-01", "finding": f"Logger {x['id']} was {x['change_db']:.0f} dB louder last night",
                             "reason": f"Loud for 1 night only, which is often traffic or a night pump. It counts after {ac.get('min_nights', 2)} nights in a row"})

    pub = P["public"]
    linked = [REPORTS[c] for c in a.get("reports", []) if c in REPORTS]
    phones = len({r["device"] for r in linked})
    if phones >= pub["min_phones"]:
        add("PUB-01", f"{phones} people reported water nearby", f"{len(linked)} reports from {phones} different phones",
            "Public", pub["points_without_meters"] if a["kind"] == "public" else pub["points"])
    elif phones:
        add("PUB-00", "Someone reported water nearby", f"{len(linked)} report", "Public", pub["one_report"])
    return sig, rejected, fs


def area_grounded(a, top, rs):
    """Does anything in the data back up the model's search area?"""
    g = rs.get("grounding", {})
    reach = top["radius_m"] + g.get("area_margin_m", 300)
    clat, clon = top["centre_latlon"]
    for s, mm in a.get("dp_mm", {}).items():
        if mm >= g.get("area_drop_mm", 10) and meter_trust(s) >= rs["trust"]["min"]:
            lon, lat = NODE[s]["geometry"]["coordinates"]
            if km(clat, clon, lat, lon) * 1000 <= reach:
                return True, f"Backed by sensor {s} ({mm:.0f} mm drop inside the area)"
    for c in a.get("reports", []):
        r = REPORTS.get(c)
        if r and km(clat, clon, r["lat"], r["lon"]) * 1000 <= reach:
            return True, f"Backed by public report {c} inside the area"
    return False, "No trusted sensor or report inside this area saw anything, so it does not count"


def score_alert(a):
    """Rules decide the score with the ruleset the alert was created under. Every point is traced."""
    rs = scoring.get(a.get("ruleset", RULESET))
    sig, rejected, fs = collect_signals(a, rs)
    out = scoring.score(sig, rs)
    if fs["inlet_trust"] < rs["trust"]["full"] and a["kind"] != "public":
        out["notes"].append(f"Inlet meter trust is {fs['inlet_trust']}%, so its signals count for half.")
    if a.get("model_status") in ("off", "unavailable") and a["kind"] != "public":
        out["notes"].append("AI assist was " + ("switched off" if a["model_status"] == "off" else "not available")
                            + " for this alert. Scored by the rules only, search area from the "
                            + ("noise loggers." if (a.get("search") or {}).get("loggers") else "pressure sensors."))
    return {**out, "rejected": rejected, "ruleset": rs["version"], "flow": fs}


def public_alert_view(a):
    s = score_alert(a)
    srch = a.get("search") or {}
    top = (srch.get("areas") or [None])[0]
    return {
        "id": a["id"], "zone": a["zone"], "zone_name": ZONE_NAMES.get(a["zone"], a["zone"]),
        "kind": a["kind"], "status": a["status"], "created": a["created"], "started": a["started"],
        "resolved": a.get("resolved"), "score": s["score"], "level": s["level"],
        "headline": headline(a, s), "street": a.get("streets", [None])[0] if a.get("streets") else None,
        "water_lost_m3_per_day": srch.get("m3_day"),
        "best_guess_latlon": srch.get("best_guess") or a.get("latlon"),
        "job": job_summary(a.get("job")), "report_count": len(a.get("reports", [])),
        "top_area": {"area": top["area"], "probability": top["probability"]} if top else None,
        "model_status": a.get("model_status", "ok"), "search_source": srch.get("source"), "ruleset": s["ruleset"],
    }


def headline(a, s):
    codes = [x["code"] for x in s["trace"]]
    if "PR-02" in codes:
        return "Pressure drop and flow jump: likely burst"
    if "NF-01" in codes and s["flow"]["nights_running"] >= 2:
        return f"Night flow up {s['flow']['nights_running']} nights in a row"
    if "NF-01" in codes:
        return "Night flow above usual"
    if a.get("opened_by") and len(a.get("reports", [])) < 3:
        n = len(a.get("reports", []))
        return f"Opened from {'a public report' if n == 1 else f'{n} public reports'}"
    if any(c.startswith("PUB") for c in codes):
        return f"{len(a.get('reports', []))} public reports close together"
    return "Small change, keep watching"


def job_summary(job_id):
    j = JOBS.get(job_id) if job_id else None
    if not j:
        return None
    return {"id": j["id"], "crew_id": j["crew_id"], "crew": crew_name(j["crew_id"]),
            "status": j["status"], "outcome": j.get("outcome")}


def create_leak_alert(pipe_id, size, started, kind, fixed_at=None, demand_scale=1.0, sensor_noise_mm=0.0, rng=None, coeff=None):
    """
    Run a real EPANET leak, then let the RULES decide whether it becomes an alert.
    The model is asked for extra findings and a search area; if it is off or fails,
    the search area comes from the pressure sensors instead.
    Returns (alert_id or None, model_status).
    """
    zone, readings, truth = simulate(pipe_id, size, demand_scale, sensor_noise_mm, rng, coeff)
    leak_id = uuid.uuid4().hex[:8]
    dp = {k.split("_h03_")[1]: v for k, v in readings.items() if "_h03_" in k}
    LEAKS[leak_id] = {"id": leak_id, "zone": zone, "pipe_id": pipe_id, "flow_lps": truth["leak_flow_lps"],
                      "start": started, "end": fixed_at, "kind": kind, "dp_mm": dp, "latlon": truth["latlon"],
                      "dp_mm_h8": {k.split("_h08_")[1]: v for k, v in readings.items() if "_h08_" in k}}
    VERSION["n"] += 1

    pred, status = ask_model(zone, readings)
    search = search_from_model(pred) if pred and pred.get("leak") else search_from_meters(zone, dp, readings)
    check = {}
    if search.get("best_guess"):
        g = search["best_guess"]
        check["distance_error_m"] = round(km(g[0], g[1], *truth["latlon"]) * 1000)
        check["pipe_in_search"] = any(pipe_id in ar["pipes"] for ar in search["areas"])

    aid = uuid.uuid4().hex[:8]
    created = started + 15 * 60 if kind == "burst" else next_3am(started)
    top_pipes = search["areas"][0]["pipes"] if search["areas"] else []
    a = {"id": aid, "zone": zone, "kind": kind, "status": "open", "leak_id": leak_id,
         "started": started, "created": min(created, NOW), "model": pred or {}, "model_status": status,
         "search": search, "dp_mm": dp, "truth": truth, "check": check, "streets": streets_of(top_pipes),
         "reports": [], "job": None, "events": [], "ruleset": RULESET}
    ALERTS[aid] = a
    # the rules decide: below Watch there is no alert (the leak still affects the meters)
    if score_alert(a)["score"] < scoring.get(RULESET)["bands"]["watch"]:
        del ALERTS[aid]
        return None, status
    if fixed_at:
        a["status"] = "resolved"
        a["resolved"] = fixed_at
    return aid, status


def next_3am(t):
    local = t + 4 * 3600
    day = local // 86400 * 86400
    at = day + 3 * 3600
    if at <= local:
        at += 86400
    return at - 4 * 3600


# ============================================================== public reports
REPORT_KINDS = {"water_on_road": ("Dlo lor semin", "Water on the road"),
                "broken_pipe": ("Tiyo kase", "Broken pipe"),
                "no_water": ("Pa ena dlo", "No water at home"),
                "low_pressure": ("Presion feb", "Low pressure")}
CODE_COUNTER = {"n": 4820}


class PhotoCheckIn(BaseModel):
    """What the phone's photo check found. A hint for staff only: never trusted, never changes the score."""
    ok: bool
    issues: list[Literal["dark", "blank", "blurry", "not_relevant"]] = Field(default_factory=list, max_length=4)
    label: Optional[str] = Field(None, max_length=30)
    confidence: Optional[float] = Field(None, ge=0, le=1)
    model: Optional[str] = Field(None, max_length=40)
    brightness: Optional[float] = None
    sharpness: Optional[float] = None
    kept_anyway: bool = False


PHOTO_ISSUES = {"dark": "too dark", "blank": "nearly blank", "blurry": "blurry", "not_relevant": "may not show water or a pipe"}


def photo_hint(r):
    """One short line for staff and crews about the phone's photo check."""
    c = r.get("photo_check")
    if not r.get("photo") or not c:
        return None
    if c.get("ok"):
        found = f", looks like {c['label']} ({c['confidence']:.0%})" if c.get("label") and c.get("confidence") is not None else ""
        return "Photo check: clear" + found
    issues = ", ".join(PHOTO_ISSUES.get(i, i) for i in c.get("issues", []))
    return f"Photo check: {issues}" + (" (sent anyway)" if c.get("kept_anyway") else "")


class ReportIn(BaseModel):
    lat: float = Field(ge=-20.6, le=-19.9)
    lon: float = Field(ge=57.2, le=57.9)
    kind: Literal["water_on_road", "broken_pipe", "no_water", "low_pressure"]
    note: Optional[str] = Field(None, max_length=300)
    device: str = Field(min_length=6, max_length=64, description="random id the phone keeps, not personal")
    photo: Optional[str] = Field(None, description="data URL of a photo, optional", max_length=3_000_000)
    photo_check: Optional[PhotoCheckIn] = None


def add_report(body: ReportIn, at=None):
    with LOCK:
        CODE_COUNTER["n"] += 1
        code = f"R-{CODE_COUNTER['n']}"
        zone = point_in_zone(body.lat, body.lon)
        r = {"code": code, "created": at or int(time.time()), "lat": round(body.lat, 4), "lon": round(body.lon, 4),
             "kind": body.kind, "note": body.note, "device": hashlib.sha256(body.device.encode()).hexdigest()[:12],
             "photo": body.photo, "zone": zone, "alert": None, "cluster": None,
             "photo_check": body.photo_check.model_dump() if body.photo and body.photo_check else None}
        REPORTS[code] = r
        link_report(r)
        return r


def link_report(r):
    """Attach a report to an open alert nearby, or group it with other reports.
    Reports outside our network are never grouped: an operator forwards them to the right regional team."""
    if not r["zone"]:
        return
    best = None
    for a in ALERTS.values():
        if a["status"] != "open" or a["zone"] != r["zone"]:
            continue
        pts = []
        srch = a.get("search") or {}
        if srch.get("best_guess"):
            pts.append((srch["best_guess"], 1.5))
        for ar in srch.get("areas", []):
            pts.append((ar["centre_latlon"], ar["radius_m"] / 1000 + 0.3))
        if a.get("latlon"):
            pts.append((a["latlon"], 0.3))
        for (lat, lon), reach in pts:
            d = km(r["lat"], r["lon"], lat, lon)
            if d <= reach and (best is None or d < best[0]):
                best = (d, a["id"])
    if best:
        r["alert"] = best[1]
        ALERTS[best[1]]["reports"].append(r["code"])
        return
    # no alert: look for other reports within 150 m in the last 24 h
    near = [x for x in REPORTS.values() if x["code"] != r["code"] and x["alert"] is None and x["zone"] == r["zone"]
            and km(r["lat"], r["lon"], x["lat"], x["lon"]) <= 0.15 and r["created"] - x["created"] <= 86400]
    group = near + [r]
    if len({x["device"] for x in group}) >= 3:
        aid = uuid.uuid4().hex[:8]
        lat = sum(x["lat"] for x in group) / len(group)
        lon = sum(x["lon"] for x in group) / len(group)
        ALERTS[aid] = {"id": aid, "zone": r["zone"], "kind": "public", "status": "open",
                       "started": min(x["created"] for x in group), "created": r["created"],
                       "model": {}, "model_status": "ok", "ruleset": RULESET,
                       "search": {"source": "reports", "best_guess": [lat, lon], "areas": [], "flow_lps": None, "m3_day": None},
                       "dp_mm": {}, "latlon": [lat, lon],
                       "streets": [], "reports": [x["code"] for x in group], "job": None, "events": []}
        for x in group:
            x["alert"] = aid


PUBLIC_DEFAULT = {"max_reports_per_phone_per_hour": 5, "max_reports_per_phone_per_day": 15, "same_photo_block_hours": 24,
                  "same_photo_max_diff": 4, "zone_snap_m": 400}
PUBLIC = load_settings("public.json", PUBLIC_DEFAULT)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE_RE = re.compile(r"(\+?\d[\d\s-]{6,}\d)")


def clean_note(note):
    """Remove emails and phone numbers from a public note. Returns (note, removed_anything)."""
    if not note:
        return note, False
    out = EMAIL_RE.sub("[removed]", note)
    out = PHONE_RE.sub(lambda m: "[removed]" if sum(c.isdigit() for c in m.group(0)) >= 7 else m.group(0), out)
    return out.strip(), out != note


def photo_hash(photo):
    return hashlib.sha256(photo.encode()).hexdigest()[:16] if photo else None


def photo_print(photo):
    """
    'Looks the same' fingerprint (difference hash, 64 bits): shrink to 9 x 8 grey and compare each pixel
    with its right-hand neighbour. Re-saved, resized or slightly brightened copies of a photo give the same
    print; a new photo of the same leak, even from almost the same spot, differs in 10 or more bits.
    """
    if not photo:
        return None
    try:
        from PIL import Image
        raw = base64.b64decode(photo.split(",", 1)[-1])
        a = np.asarray(Image.open(io.BytesIO(raw)).convert("L").resize((9, 8), Image.LANCZOS), dtype=float)
        return int("".join("1" if b else "0" for b in (a[:, 1:] > a[:, :-1]).ravel()), 2)
    except Exception:
        return None     # an unreadable photo is still accepted, the exact check above still applies


def same_photo(a, b, max_diff):
    return a is not None and b is not None and bin(a ^ b).count("1") <= max_diff


@router.post("/public/reports")
def public_report(body: ReportIn):
    now = int(time.time())
    dev = hashlib.sha256(body.device.encode()).hexdigest()[:12]
    mine = [r for r in REPORTS.values() if r["device"] == dev]
    if len([r for r in mine if now - r["created"] < 3600]) >= PUBLIC["max_reports_per_phone_per_hour"]:
        raise HTTPException(429, "You have sent several reports in the last hour. Thank you, we have them. "
                                 "If water is flooding a road, call the hotline.")
    if len([r for r in mine if now - r["created"] < 86400]) >= PUBLIC["max_reports_per_phone_per_day"]:
        raise HTTPException(429, "Daily limit of reports from this phone reached. If it is urgent, call the hotline.")
    h = photo_hash(body.photo)
    fp = photo_print(body.photo)
    recent = [r for r in REPORTS.values() if now - r["created"] < PUBLIC["same_photo_block_hours"] * 3600]
    for r in recent:
        if r.get("photo") and "photo_print" not in r:
            r["photo_print"] = photo_print(r["photo"])     # reports saved before prints existed
    if h and any(r.get("photo_hash") == h or same_photo(fp, r.get("photo_print"), PUBLIC["same_photo_max_diff"])
                 for r in recent):
        raise HTTPException(409, "This photo was already sent with another report. Please take a new photo, or send without one.")
    note, cleaned = clean_note(body.note)
    r = add_report(body.model_copy(update={"note": note}))
    r["photo_hash"], r["photo_print"] = h, fp
    return {"code": r["code"], "status": public_status(r), "note_cleaned": cleaned}


def public_status(r):
    if not r["zone"]:
        steps = [
            {"key": "received", "kreol": "Rapor resevwar", "fr": "Rapport recu", "en": "Received", "done": True,
             "at": r["created"]},
            {"key": "forwarded", "kreol": "Avoye ar lekip ou rezion", "fr": "Transmis a l'equipe de votre region",
             "en": "Sent to the team for your area", "done": bool(r.get("forwarded")), "at": r.get("forwarded")},
        ]
        return {"code": r["code"], "created": r["created"], "kind": r["kind"], "area": "Outside the network",
                "others_nearby": 0, "steps": steps}
    a = ALERTS.get(r["alert"]) if r["alert"] else None
    job = JOBS.get(a["job"]) if a and a.get("job") else None
    fixed = bool(a and a["status"] == "resolved")
    steps = [
        {"key": "received", "kreol": "Rapor resevwar", "fr": "Rapport recu", "en": "Received", "done": True,
         "at": r["created"]},
        {"key": "linked", "kreol": "Lie avek enn lalert", "fr": "Lie a une alerte", "en": "Linked to a leak alert",
         "done": a is not None, "at": a["created"] if a else None},
        {"key": "crew", "kreol": "Lekip pe vini", "fr": "Equipe en route", "en": "Crew on the way",
         "done": job is not None, "at": job["created"] if job else None},
        {"key": "fixed", "kreol": "Fwit repare", "fr": "Fuite reparee", "en": "Leak fixed",
         "done": fixed, "at": a.get("resolved") if fixed else None},
    ]
    others = 0
    if a:
        others = len({REPORTS[c]["device"] for c in a["reports"] if c in REPORTS and c != r["code"]})
    return {"code": r["code"], "created": r["created"], "kind": r["kind"],
            "area": ZONE_NAMES.get(r["zone"], "Outside the network") if r["zone"] else "Outside the network",
            "others_nearby": others, "steps": steps}


@router.get("/public/reports/{code}")
def public_report_status(code: str):
    r = REPORTS.get(code.strip().upper())
    if not r:
        raise HTTPException(404, "No report with this code")
    return public_status(r)


@router.get("/public/info")
def public_info():
    # contact details come from the environment, so nothing invented ends up on the public page
    return {"kinds": [{"key": k, "kreol": v[0], "en": v[1]} for k, v in REPORT_KINDS.items()],
            "area": "North Mauritius",
            "hotline": os.environ.get("DLOWATCH_HOTLINE") or None,
            "utility": os.environ.get("DLOWATCH_UTILITY_NAME") or None}


# ---------------------------------------------------------------- public area notices
# What the public may see: a simple status per area. Never scores, meter data or crew details.
PUBLIC_FIXED_HOURS = 48          # how long "fixed recently" stays on the map


def public_notice(a, now):
    """Turn one alert into a public notice, or None if the public should not see it."""
    if a["status"] == "resolved":
        job = JOBS.get(a.get("job")) if a.get("job") else None
        if job and job.get("outcome") == "leak_found" and now - (a.get("resolved") or 0) <= PUBLIC_FIXED_HOURS * 3600:
            return "fixed", a.get("resolved")
        return None
    job = JOBS.get(a.get("job")) if a.get("job") else None
    if job and job["status"] in ("sent", "on_the_way", "on_site"):
        return "crew", job["created"]
    level = score_alert(a)["level"]
    if level in ("high", "critical") or (a["kind"] == "public" and len(a.get("reports", [])) >= 3):
        return "checking", a["created"]
    return None


STATUS_RANK = {"crew": 3, "checking": 2, "fixed": 1, "normal": 0}


def notice_place(a):
    ll = (a.get("search") or {}).get("best_guess") or a.get("latlon")
    return [round(ll[0], 4), round(ll[1], 4)] if ll else None


def public_notices(now=None):
    now = now or int(time.time())
    out = []
    for a in ALERTS.values():
        n = public_notice(a, now)
        if not n:
            continue
        status, since = n
        out.append({"id": a["id"], "zone": a["zone"], "area": ZONE_NAMES.get(a["zone"], a["zone"]),
                    "status": status, "since": since, "street": (a.get("streets") or [None])[0],
                    "latlon": notice_place(a), "burst": a["kind"] == "burst"})
    return sorted(out, key=lambda n: (-STATUS_RANK[n["status"]], -(n["since"] or 0)))


@router.get("/public/areas")
def public_areas():
    notices = public_notices()
    by_zone = {}
    for n in notices:
        cur = by_zone.get(n["zone"])
        if cur is None or STATUS_RANK[n["status"]] > STATUS_RANK[cur]:
            by_zone[n["zone"]] = n["status"]
    feats = []
    for f in api.GEO["zones"]["features"]:
        z = f["properties"]["zone"]
        ring = [[round(x, 4), round(y, 4)] for x, y in f["geometry"]["coordinates"][0]]
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                      "properties": {"zone": z, "name": ZONE_NAMES.get(z, z), "status": by_zone.get(z, "normal")}})
    return {"areas": {"type": "FeatureCollection", "features": feats}, "notices": notices, "updated": int(time.time())}


@router.get("/public/nearby")
def public_nearby(lat: float, lon: float, radius_m: int = 600):
    """Is there already a known problem close to this spot? Used before sending a report."""
    zone = point_in_zone(lat, lon)
    best = None
    for n in public_notices():
        if not n["latlon"] or n["status"] == "fixed":
            continue
        d = km(lat, lon, n["latlon"][0], n["latlon"][1]) * 1000
        if d <= radius_m and (best is None or d < best["distance_m"]):
            best = {**n, "distance_m": round(d)}
    return {"zone": zone, "area": ZONE_NAMES.get(zone) if zone else None, "inside_network": zone is not None,
            "known": best}


@router.get("/public/stats")
def public_stats():
    now = int(time.time())
    week = [r for r in REPORTS.values() if now - r["created"] <= 7 * 86400]
    fixed = [a for a in ALERTS.values() if a["status"] == "resolved" and a.get("job")
             and JOBS.get(a["job"], {}).get("outcome") == "leak_found" and now - (a.get("resolved") or 0) <= 30 * 86400]
    saved = sum(((a.get("search") or {}).get("m3_day") or 0) for a in fixed)
    hours = [(a["resolved"] - a["created"]) / 3600 for a in fixed if a.get("resolved") and a.get("created")]
    return {"reports_7d": len(week), "leaks_fixed_30d": len(fixed),
            "water_saved_m3_per_day": round(saved), "avg_fix_hours": round(sum(hours) / len(hours), 1) if hours else None,
            "open_notices": len([n for n in public_notices() if n["status"] != "fixed"])}


@router.get("/public/reports-batch")
def public_reports_batch(codes: str):
    """Status of several reports at once (the phone keeps the codes it sent). Unknown codes are skipped."""
    out = []
    for c in codes.split(",")[:30]:
        r = REPORTS.get(c.strip().upper())
        if r:
            out.append(public_status(r))
    return out


# ============================================================== staff endpoints
@router.get("/overview", dependencies=[Depends(STAFF_ONLY)])
def overview():
    refresh()
    zones = []
    tot_in = tot_exp = 0
    for z in api.ZONES:
        fs = zone_flow_stats(z)
        tot_in += fs["water_in_m3"]
        tot_exp += fs["expected_m3"]
        open_alerts = [public_alert_view(a) for a in ALERTS.values() if a["zone"] == z and a["status"] == "open"]
        top = max(open_alerts, key=lambda x: x["score"], default=None)
        zones.append({"zone": z, "name": ZONE_NAMES[z], "score": top["score"] if top else 0,
                      "level": top["level"] if top else "low", "night_pct": fs["night_pct"],
                      "avg_demand_lps": ZONE_PROPS[z]["avg_demand_lps"], "pipe_km": ZONE_PROPS[z]["pipe_km"]})
    alerts = sorted([public_alert_view(a) for a in ALERTS.values() if a["status"] == "open"],
                    key=lambda x: -x["score"])
    levels = Counter(a["level"] for a in alerts)
    ms = meter_list()
    st = Counter(m["status"] for m in ms)
    # network night flow: sum of zones, last 7 nights
    per_night = {}
    for z in api.ZONES:
        for n in zone_flow_stats(z)["nights"]:
            per_night.setdefault(n["night"], 0)
            per_night[n["night"]] += n["lps"]
    nights = sorted(per_night.items())[-7:]
    base = float(np.median([v for _, v in sorted(per_night.items())[:7]]))
    return {
        "now": NOW, "water_in_m3": tot_in, "expected_m3": tot_exp,
        "extra_m3": max(0, tot_in - tot_exp), "extra_pct": round((tot_in - tot_exp) / tot_exp * 100, 1),
        "alerts": alerts, "levels": dict(levels),
        "meters": {"total": len(ms), "healthy": st["healthy"], "noisy": st["noisy"] + st["drifting"],
                   "offline": st["offline"] + st["stuck"]},
        "loggers": {"total": len(LOGGERS), "noisy": sum(logger_state(l)["status"] == "noisy" for l in LOGGERS),
                    "coverage_pct": LOGGER_COVERAGE["overall"]},
        "zones": zones, "night_flow": [{"night": t, "lps": round(v, 1)} for t, v in nights],
        "night_baseline_lps": round(base, 1), "ruleset": RULESET,
    }


@router.get("/alerts", dependencies=[Depends(STAFF_ONLY)])
def alerts(status: Literal["open", "resolved", "all"] = "open"):
    items = [public_alert_view(a) for a in ALERTS.values() if status == "all" or a["status"] == status]
    return sorted(items, key=lambda x: (x["status"] != "open", -x["score"]))


def series(c, hours, every=1):
    n = hours * 3600 // STEP_S
    t, v, f = c["t"][-n:], c["v"][-n:], c["filled"][-n:]
    rt, rv = c["raw_t"], c["raw_v"]
    sel = rt >= t[0]
    return {"t": t[::every].tolist(), "v": [None if np.isnan(x) else round(float(x), 3) for x in v[::every]],
            "filled": f[::every].tolist(),
            "raw_t": rt[sel].tolist()[::every], "raw_v": [round(float(x), 3) for x in rv[sel]][::every]}


def if_not_fixed(m3_day):
    """Water (and money, if a price is set) lost if this leak is left for a week, a month or a year."""
    if not m3_day:
        return None
    cost = report_settings().get("water_cost_rs_per_m3")
    out = {"m3_per_day": round(m3_day), "week_m3": round(m3_day * 7), "month_m3": round(m3_day * 30),
           "year_m3": round(m3_day * 365), "olympic_pools_per_year": round(m3_day * 365 / 2500, 1)}
    if isinstance(cost, (int, float)) and cost > 0:
        out["money"] = {"currency": report_settings().get("currency", "Rs"), "week": round(m3_day * 7 * cost),
                        "month": round(m3_day * 30 * cost), "year": round(m3_day * 365 * cost)}
    return out


@router.get("/alerts/{aid}", dependencies=[Depends(STAFF_ONLY)])
def alert_detail(aid: str):
    a = ALERTS.get(aid)
    if not a:
        raise HTTPException(404, "Unknown alert")
    s = score_alert(a)
    c = refresh()
    zone = a["zone"]
    sensors = sorted(a.get("dp_mm", {}).items(), key=lambda kv: -kv[1])[:2]
    used = [{"id": api.ZONES[zone]["inlet"], "type": "flow", "trust": meter_trust(api.ZONES[zone]["inlet"])}]
    used += [{"id": sid, "type": "pressure", "trust": meter_trust(sid), "drop_mm": round(mm, 1)} for sid, mm in sensors]
    fixes = Counter()
    for u in used:
        d = c["flow"].get(u["id"]) or c["pressure"].get(u["id"])
        fixes.update(d["fixes"])
    pressure = []
    for sid, _ in sensors:
        pressure.append({"id": sid, **series(c["pressure"][sid], 72, every=3)})
    areas = []
    for ar in (a.get("search") or {}).get("areas", []):
        areas.append({**ar, "streets": streets_of(ar["pipes"])})
    sensor_pos = []
    for sid in api.ZONES[zone]["sensors"]:
        lon, lat = NODE[sid]["geometry"]["coordinates"]
        sensor_pos.append({"id": sid, "latlon": [lat, lon], "trust": meter_trust(sid),
                           "drop_mm": round(a.get("dp_mm", {}).get(sid, 0), 1)})
    linked = [report_view(REPORTS[x]) for x in a.get("reports", []) if x in REPORTS]
    # loggers in the zone: all of them for the map, the ones near the best guess or loud for the list
    g = (a.get("search") or {}).get("best_guess")
    logs = []
    if a["kind"] != "public":
        for x in zone_loggers(zone):
            dist = round(km(g[0], g[1], x["lat"], x["lon"]) * 1000) if g else None
            logs.append({k: x[k] for k in ("id", "status", "change_db", "loud_nights", "last_db", "usual_db")}
                        | {"latlon": [x["lat"], x["lon"]], "distance_m": dist})
        logs.sort(key=lambda x: (x["status"] != "noisy", x["distance_m"] if x["distance_m"] is not None else 10 ** 9))
    return {
        **public_alert_view(a), "trace": s["trace"], "notes": s["notes"], "ruleset": s["ruleset"],
        "bands": scoring.get(a.get("ruleset", RULESET))["bands"], "rejected": s["rejected"],
        "ruleset_info": {"version": s["ruleset"], "active": RULESET,
                         "description": scoring.get(a.get("ruleset", RULESET)).get("description"),
                         "ai_cap": scoring.get(a.get("ruleset", RULESET))["caps"].get("ai_total")},
        "flow": {k: v for k, v in s["flow"].items()}, "data_quality": {"meters": used, "fixes": dict(fixes)},
        "pressure": pressure, "areas": areas, "sensors": sensor_pos, "reports": linked, "loggers": logs,
        "hearing_range_m": ACOUSTIC["hearing_range_m"],
        "model": {"leak_probability": (a.get("model") or {}).get("leak_probability"),
                  "leak_flow_lps": (a.get("search") or {}).get("flow_lps"),
                  "water_lost_m3_per_day": (a.get("search") or {}).get("m3_day"),
                  "best_guess_latlon": (a.get("search") or {}).get("best_guess")},
        "if_not_fixed": if_not_fixed((a.get("search") or {}).get("m3_day")) if a["status"] == "open" else None,
        "suspects": (sp := suspects(a)), "households_est": households_near(zone, sp[:3]), "ease": repair_ease(sp),
        "inlet": api.ZONES[zone]["inlet"],
    }


def usual(c, days):
    """Median of the same time of day over the previous days (NaN where there is no history)."""
    v = c["v"]
    stack = []
    for k in range(1, days + 1):
        shifted = np.full(len(v), np.nan)
        shifted[k * PER_DAY:] = v[:len(v) - k * PER_DAY]
        stack.append(shifted)
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return np.nanmedian(np.vstack(stack), axis=0)


def suspect_check(a, sp=None):
    """Demo only: where the simulated leak pipe sits in the suspect list (1 = first block)."""
    pid = (a.get("truth") or {}).get("pipe_id")
    if not pid:
        return {}
    for s in suspects(a, keep_all=True):
        if pid in s["pipes"] or f"{pid}_LEAK_B" in s["pipes"]:
            return {"true_pipe_suspect_rank": s["rank"]}
    return {"true_pipe_suspect_rank": None}


@router.get("/alerts/{aid}/replay", dependencies=[Depends(STAFF_ONLY)])
def alert_replay(aid: str):
    a = ALERTS.get(aid)
    if not a:
        raise HTTPException(404, "Unknown alert")
    c = refresh()
    zone = a["zone"]
    start = a["started"]
    if a["kind"] == "burst":
        t0, t1 = start - 3 * 3600, min(NOW, (a.get("resolved") or NOW) + 2 * 3600)
    else:
        t0, t1 = start - 24 * 3600, NOW
    t1 = max(t1, t0 + 6 * 3600)
    fc = c["flow"][api.ZONES[zone]["inlet"]]
    sel = (fc["t"] >= t0) & (fc["t"] <= t1)
    every = max(1, int(sel.sum() // 240))
    extra = fc["v"] - usual(fc, 7)
    flow = {"t": fc["t"][sel][::every].tolist(),
            "v": [None if np.isnan(x) else round(float(x), 2) for x in extra[sel][::every]]}
    top_sensor = max(a.get("dp_mm", {"": 0}).items(), key=lambda kv: kv[1])[0] if a.get("dp_mm") else None
    pres = None
    if top_sensor and top_sensor in c["pressure"]:
        pc = c["pressure"][top_sensor]
        ps = (pc["t"] >= t0) & (pc["t"] <= t1)
        drop = usual(pc, 2) - pc["v"]
        pres = {"id": top_sensor, "t": pc["t"][ps][::every].tolist(),
                "v": [None if np.isnan(x) else round(float(x), 3) for x in drop[ps][::every]]}
    s = score_alert(a)
    ev = []
    if a["kind"] != "public":
        ev.append({"t": start, "tag": "Sensor", "title": "Leak starts" if a["kind"] == "slow" else "Pressure drops fast while flow jumps",
                   "detail": (f"Inlet {api.ZONES[zone]['inlet']} starts reading above normal."
                              if a["kind"] == "slow" else
                              f"Flow jumps by about {(a.get('search') or {}).get('flow_lps') or '?'} L/s. This shape is typical of a burst main.")})
        codes = ", ".join(x["code"] for x in s["trace"] if x["source"] in ("Rules", "Combo"))
        ev.append({"t": a["created"], "tag": "Rules", "title": "Rules raise an alert",
                   "detail": f"Signals {codes}. Score {s['score']}/100, {s['level']}."})
        srch = a.get("search") or {}
        top = (srch.get("areas") or [None])[0]
        st = ", ".join(a.get("streets", [])[:2]) or "the zone"
        if top and top.get("from_loggers"):
            ev.append({"t": a["created"] + 60, "tag": "Loggers", "title": "Noise loggers point to the spot",
                       "detail": f"{', '.join(top['from_loggers'][:3])} loud and steady for 2 nights or more. "
                                 f"Search area {top['radius_m']:.0f} m around them, near {st}."})
        elif top and srch.get("source") == "model":
            ev.append({"t": a["created"] + 60, "tag": "AI model", "title": "Model suggests where to look",
                       "detail": f"Area {top['area']} ({top['probability']:.0%}), around {st}. About {srch.get('flow_lps')} L/s lost."})
        elif top:
            ev.append({"t": a["created"] + 60, "tag": "Rules", "title": "Search area from the pressure sensors",
                       "detail": f"Model {'switched off' if a.get('model_status') == 'off' else 'not available'}. "
                                 f"Area around sensors {', '.join(top.get('from_sensors', []))}, near {st}. "
                                 f"About {srch.get('flow_lps')} L/s extra at the inlet meter."})
    for code in a.get("reports", []):
        r = REPORTS.get(code)
        if r:
            ev.append({"t": r["created"], "tag": "Public", "title": f"Public report {r['code']}",
                       "detail": f"{REPORT_KINDS[r['kind']][1]}, sent from the public page."})
    j = JOBS.get(a.get("job")) if a.get("job") else None
    if j:
        ev.append({"t": j["created"], "tag": "Crew", "title": f"{crew_name(j['crew_id'])} sent",
                   "detail": "Job sent to the crew phone."})
        if j.get("finished"):
            ev.append({"t": j["finished"], "tag": "Crew", "title": "Crew logs the result",
                       "detail": f"{OUTCOMES[j['outcome']]}" + (f": {j.get('cause')}" if j.get("cause") else "") + "."})
    ev.sort(key=lambda e: e["t"])
    for i, e in enumerate(ev):
        e["n"] = i + 1
    return {**public_alert_view(a), "events": ev, "flow": flow, "pressure": pres, "window": [t0, t1]}


class DispatchIn(BaseModel):
    crew_id: str
    note: Optional[str] = None


@router.post("/alerts/{aid}/dispatch")
def dispatch(aid: str, body: DispatchIn, user=Depends(OPS_ONLY)):
    with LOCK:
        a = ALERTS.get(aid)
        if not a or a["status"] != "open":
            raise HTTPException(404, "No open alert with this id")
        if body.crew_id not in CREWS:
            raise HTTPException(400, "Unknown crew. Crews are set in config/people.json.")
        old = JOBS.get(a.get("job")) if a.get("job") else None
        if old and old["status"] != "done":
            old["status"] = "cancelled"
        jid = f"J-{1040 + len(JOBS) + 1}"
        JOBS[jid] = {"id": jid, "alert": aid, "crew_id": body.crew_id, "status": "sent",
                     "created": int(time.time()), "note": body.note, "by": user["name"]}
        a["job"] = jid
        return JOBS[jid]


@router.post("/alerts/{aid}/dismiss")
def dismiss(aid: str, user=Depends(OPS_ONLY)):
    a = ALERTS.get(aid)
    if not a:
        raise HTTPException(404, "Unknown alert")
    a["status"] = "resolved"
    a["resolved"] = int(time.time())
    LABELS.append({"id": f"L-{len(LABELS) + 1}", "alert": aid, "zone": a["zone"], "label": "not_a_leak",
                   "by": user["name"], "at": a["resolved"]})
    return public_alert_view(a)


def meter_list():
    c = refresh()
    out = []
    for mid, m in METERS.items():
        d = c["flow"].get(mid) or c["pressure"].get(mid)
        issues = ", ".join(f"{n} {code}" for code, n in sorted(d["fixes"].items()))
        out.append({"id": mid, "zone": m["zone"], "zone_name": ZONE_NAMES[m["zone"]], "type": m["type"],
                    "status": d["status"], "trust": d["trust"], "fixes": d["fixes"], "issues": issues,
                    "used": "yes" if d["trust"] >= 80 else "half" if d["trust"] >= 50 else "no",
                    "problem": describe_problem(d)})
    return out


def describe_problem(d):
    f = d["fixes"]
    if d["status"] == "offline":
        return "No readings for over 30 min (offline)"
    if d["status"] == "stuck":
        return "Same value for hours (stuck)"
    if d["status"] == "drifting":
        return f"Reads {d['drift_pct']:+.0f}% against what it should"
    parts = []
    if f.get("DQ-01"):
        parts.append(f"{f['DQ-01']} spikes")
    if f.get("DQ-02"):
        parts.append(f"{f['DQ-02'] * 5} min of gaps filled")
    if f.get("DQ-04"):
        parts.append(f"{f['DQ-04']} impossible values")
    if f.get("DQ-06"):
        parts.append(f"{f['DQ-06']} time problems")
    return ", ".join(parts) or "No problems"


@router.get("/meters", dependencies=[Depends(STAFF_ONLY)])
def meters_endpoint():
    ms = meter_list()
    st = Counter(m["status"] for m in ms)
    fixed = sum(sum(m["fixes"].values()) for m in ms)
    return {"meters": sorted(ms, key=lambda m: (m["trust"], m["id"])), "rules": DQ_RULES,
            "summary": {"total": len(ms), "healthy": st["healthy"], "noisy": st["noisy"] + st["drifting"],
                        "offline": st["offline"] + st["stuck"], "fixed": fixed}}


@router.get("/loggers", dependencies=[Depends(STAFF_ONLY)])
def loggers_endpoint(zone: Optional[str] = None):
    """Acoustic noise loggers: where they are and how much of the pipe network they can hear."""
    items = [{**lg, **logger_state(lid), "zone_name": ZONE_NAMES[lg["zone"]]}
             for lid, lg in LOGGERS.items() if not zone or lg["zone"] == zone]
    st = Counter(i["status"] for i in items)
    return {"loggers": items, "hearing_range_m": ACOUSTIC["hearing_range_m"], "coverage": LOGGER_COVERAGE,
            "summary": {"total": len(items), "noisy": st["noisy"], "loud_once": st["loud once"], "quiet": st["quiet"]}}


@router.get("/loggers/{lid}", dependencies=[Depends(STAFF_ONLY)])
def logger_detail(lid: str):
    """One logger: where it is and its noise level for each of the last 14 nights."""
    if lid not in LOGGERS:
        raise HTTPException(404, "Unknown logger")
    return {**LOGGERS[lid], **logger_state(lid), "nights": logger_levels()[lid],
            "zone_name": ZONE_NAMES[LOGGERS[lid]["zone"]]}


@router.get("/meters/{mid}", dependencies=[Depends(STAFF_ONLY)])
def meter_detail(mid: str, hours: int = 24):
    if mid not in METERS:
        raise HTTPException(404, "Unknown meter")
    c = refresh()
    d = c["flow"].get(mid) or c["pressure"].get(mid)
    m = METERS[mid]
    lon, lat = (NODE[mid]["geometry"]["coordinates"] if mid in NODE else (None, None))
    hours = max(6, min(hours, 72))
    return {**m, "zone_name": ZONE_NAMES[m["zone"]], "status": d["status"], "trust": d["trust"],
            "fixes": d["fixes"], "events": d["events"], "problem": describe_problem(d),
            "expected_per_day": PER_DAY, "latlon": [lat, lon] if lat else None,
            "series": series(d, hours, every=1)}


def report_view(r):
    return {"code": r["code"], "created": r["created"], "lat": r["lat"], "lon": r["lon"], "kind": r["kind"],
            "kind_kreol": REPORT_KINDS[r["kind"]][0], "kind_en": REPORT_KINDS[r["kind"]][1],
            "zone": r["zone"], "zone_name": ZONE_NAMES.get(r["zone"], "Outside network") if r["zone"] else "Outside network",
            "note": r["note"], "has_photo": bool(r["photo"]), "alert": r["alert"],
            "forwarded": r.get("forwarded"), "forwarded_by": r.get("forwarded_by"),
            "photo_hint": photo_hint(r), "photo_ok": (r.get("photo_check") or {}).get("ok") if r.get("photo_check") else None,
            "status": report_state(r)}


def report_state(r):
    if not r["zone"]:
        return "forwarded" if r.get("forwarded") else "outside"
    a = ALERTS.get(r["alert"]) if r["alert"] else None
    if not a:
        return "new"
    if a.get("opened_by") and a["status"] == "open":
        return "linked"
    if a["status"] == "resolved":
        return "fixed"
    return "linked" if a["kind"] != "public" else "cluster"


@router.get("/reports", dependencies=[Depends(STAFF_ONLY)])
def reports_list():
    items = sorted((report_view(r) for r in REPORTS.values()), key=lambda r: -r["created"])
    clusters = []
    for a in ALERTS.values():
        if not a.get("reports") or a["status"] != "open" or (a.get("opened_by") and len(a["reports"]) < 3):
            continue
        rs = [REPORTS[c] for c in a["reports"] if c in REPORTS]
        clusters.append({"alert": a["id"], "kind": a["kind"], "zone": a["zone"], "zone_name": ZONE_NAMES.get(a["zone"]),
                         "count": len(rs), "devices": len({r["device"] for r in rs}),
                         "street": (a.get("streets") or [None])[0],
                         "minutes": round((max(r["created"] for r in rs) - min(r["created"] for r in rs)) / 60),
                         "score": score_alert(a)["score"]})
    return {"reports": items, "clusters": clusters}


@router.get("/reports/{code}/photo", dependencies=[Depends(STAFF_ONLY)])
def report_photo(code: str):
    r = REPORTS.get(code)
    if not r or not r["photo"]:
        raise HTTPException(404, "No photo")
    return {"photo": r["photo"]}


@router.post("/reports/{code}/alert")
def report_to_alert(code: str, user=Depends(OPS_ONLY)):
    """Operator opens an alert from a single report (a clear photo of a broken pipe, say), so a crew can be sent.
    Other reports on their own within 150 m in the last 24 h join it. Public reports alone still score Watch at most."""
    with LOCK:
        r = REPORTS.get(code)
        if not r:
            raise HTTPException(404, "No report with this code")
        if not r["zone"]:
            raise HTTPException(400, "This report is outside our network. Forward it to the team for that area.")
        if r["alert"] and ALERTS.get(r["alert"], {}).get("status") == "open":
            return {"alert": r["alert"]}
        group = [x for x in REPORTS.values() if x["zone"] == r["zone"] and (x["code"] == code or (
                 (x["alert"] is None or ALERTS.get(x["alert"], {}).get("status") != "open")
                 and km(r["lat"], r["lon"], x["lat"], x["lon"]) <= 0.15 and abs(r["created"] - x["created"]) <= 86400))]
        aid = uuid.uuid4().hex[:8]
        lat = sum(x["lat"] for x in group) / len(group)
        lon = sum(x["lon"] for x in group) / len(group)
        ALERTS[aid] = {"id": aid, "zone": r["zone"], "kind": "public", "status": "open",
                       "started": min(x["created"] for x in group), "created": int(time.time()),
                       "model": {}, "model_status": "ok", "ruleset": RULESET,
                       "search": {"source": "reports", "best_guess": [lat, lon], "areas": [], "flow_lps": None, "m3_day": None},
                       "dp_mm": {}, "latlon": [lat, lon], "streets": streets_of(pipes_near(r["zone"], lat, lon, 150)),
                       "reports": [x["code"] for x in group], "job": None, "events": [], "opened_by": user["name"]}
        for x in group:
            x["alert"] = aid
        log.warning("Alert %s opened from report %s by %s", aid, code, user["name"])
        return {"alert": aid}


@router.post("/reports/{code}/forward")
def forward_report(code: str, user=Depends(OPS_ONLY)):
    """Report outside our network: mark it as passed on to the regional team. The reporter sees this on their status."""
    with LOCK:
        r = REPORTS.get(code)
        if not r:
            raise HTTPException(404, "No report with this code")
        if r["zone"]:
            raise HTTPException(400, "This report is inside our network. Open an alert from it instead.")
        if not r.get("forwarded"):
            r["forwarded"], r["forwarded_by"] = int(time.time()), user["name"]
        return report_view(r)


@router.get("/crews", dependencies=[Depends(STAFF_ONLY)])
def crews():
    out = []
    for c in CREWS.values():
        active = [j for j in JOBS.values() if j["crew_id"] == c["id"] and j["status"] in ("sent", "on_the_way", "on_site")]
        out.append({**c, "state": "busy" if active else "available", "open_jobs": len(active),
                    "detail": job_line(active[0]) if active else c["base"]})
    jobs = sorted(JOBS.values(), key=lambda j: -j["created"])
    return {"crews": out, "jobs": [job_view(j) for j in jobs], "labels": LABELS[-20:][::-1]}


def job_line(j):
    a = ALERTS[j["alert"]]
    return f"Job {j['id']}, {zone_label(a['zone'])}"


OUTCOMES = {"leak_found": "Leak found and fixed", "no_leak": "No leak found",
            "meter_fault": "Meter fault", "planned_works": "Planned works"}


def job_view(j):
    a = ALERTS[j["alert"]]
    s = score_alert(a)
    srch = a.get("search") or {}
    top = (srch.get("areas") or [None])[0]
    lat_lon = srch.get("best_guess") or a.get("latlon")
    return {
        "id": j["id"], "alert": a["id"], "zone": a["zone"], "zone_name": ZONE_NAMES.get(a["zone"]),
        "crew_id": j["crew_id"], "crew": crew_name(j["crew_id"]), "status": j["status"],
        "created": j["created"], "finished": j.get("finished"), "outcome": j.get("outcome"),
        "outcome_text": OUTCOMES.get(j.get("outcome")), "cause": j.get("cause"), "notes": j.get("notes"),
        "score": s["score"], "level": s["level"], "why": headline(a, s),
        "reasons": [x["finding"] for x in s["trace"] if x["source"] in ("Rules", "Public")][:3],
        "streets": a.get("streets", []), "latlon": lat_lon,
        "search_radius_m": round(top["radius_m"]) if top else None,
        "suspects": suspects(a),
        "pipes": top["pipes"] if top else [],
        "reports": job_reports(a),
        "loggers": job_loggers(a),
    }


def job_loggers(a):
    """Noise loggers that hear this alert's leak (the ones the search area was built from), loudest first."""
    out = []
    for lid in (a.get("search") or {}).get("loggers", []):
        if lid in LOGGERS:
            st = logger_state(lid)
            out.append({"id": lid, "latlon": [LOGGERS[lid]["lat"], LOGGERS[lid]["lon"]],
                        "junction": LOGGERS[lid]["junction"], "change_db": st["change_db"], "loud_nights": st["loud_nights"]})
    return sorted(out, key=lambda x: -x["change_db"])


def job_reports(a):
    """Public reports linked to the job's alert, newest first. Photos are fetched one by one (they are big)."""
    out = []
    for c in a.get("reports", []):
        r = REPORTS.get(c)
        if r:
            out.append({"code": r["code"], "created": r["created"], "kind_en": REPORT_KINDS[r["kind"]][1],
                        "kind_kreol": REPORT_KINDS[r["kind"]][0], "note": r["note"], "latlon": [r["lat"], r["lon"]],
                        "has_photo": bool(r["photo"]), "photo_hint": photo_hint(r)})
    return sorted(out, key=lambda x: -x["created"])


# ============================================================== crew endpoints
@router.get("/crew/jobs")
def crew_jobs(user=Depends(CREW_ONLY)):
    mine = [job_view(j) for j in JOBS.values() if j["crew_id"] == user["crew_id"] and j["status"] != "cancelled"]
    return sorted(mine, key=lambda j: (j["status"] == "done", -j["score"]))


def own_job(jid, user):
    j = JOBS.get(jid)
    if not j or j["crew_id"] != user["crew_id"]:
        raise HTTPException(404, "This job is not for your crew")
    return j


@router.get("/crew/jobs/{jid}")
def crew_job(jid: str, user=Depends(CREW_ONLY)):
    return job_view(own_job(jid, user))


@router.get("/crew/jobs/{jid}/pipes")
def crew_job_pipes(jid: str, user=Depends(CREW_ONLY)):
    j = own_job(jid, user)
    v = job_view(j)
    ids = set(v["pipes"])
    return {"type": "FeatureCollection",
            "features": [f for f in api.GEO["pipes"]["features"] if f["properties"]["id"] in ids]}


@router.get("/crew/jobs/{jid}/reports/{code}/photo")
def crew_report_photo(jid: str, code: str, user=Depends(CREW_ONLY)):
    """Photo from a public report, only for a report linked to this crew's own job."""
    j = own_job(jid, user)
    a = ALERTS.get(j["alert"]) or {}
    r = REPORTS.get(code)
    if not r or code not in a.get("reports", []) or not r["photo"]:
        raise HTTPException(404, "No photo")
    return {"photo": r["photo"]}


class StatusIn(BaseModel):
    status: Literal["on_the_way", "on_site"]


@router.post("/crew/jobs/{jid}/status")
def crew_status(jid: str, body: StatusIn, user=Depends(CREW_ONLY)):
    j = own_job(jid, user)
    if j["status"] == "done":
        raise HTTPException(400, "This job is already closed")
    j["status"] = body.status
    return job_view(j)


class ResultIn(BaseModel):
    outcome: Literal["leak_found", "no_leak", "meter_fault", "planned_works"]
    cause: Optional[str] = Field(None, max_length=60)
    found_at: Optional[str] = None
    fixed_at: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=500)
    photo_before: Optional[str] = Field(None, max_length=3_000_000)
    photo_after: Optional[str] = Field(None, max_length=3_000_000)


@router.post("/crew/jobs/{jid}/result")
def crew_result(jid: str, body: ResultIn, user=Depends(CREW_ONLY)):
    with LOCK:
        j = own_job(jid, user)
        if j["status"] == "done":
            raise HTTPException(400, "This job is already closed")
        now = int(time.time())
        j.update(status="done", finished=now, outcome=body.outcome, cause=body.cause, notes=body.notes,
                 found_at=body.found_at, fixed_at=body.fixed_at,
                 photos=sum(1 for p in (body.photo_before, body.photo_after) if p))
        a = ALERTS[j["alert"]]
        a["status"] = "resolved"
        a["resolved"] = now
        fixed = None
        if body.outcome == "leak_found":
            if a.get("leak_id") in LEAKS:
                fixed = LEAKS[a["leak_id"]]
                fixed["end"] = now
                VERSION["n"] += 1
            else:
                fixed = end_leak_near(a, now)
        if a["id"] in api.ALERTS:
            api.ALERTS[a["id"]]["status"] = "resolved"
        LABELS.append({"id": f"L-{len(LABELS) + 1}", "alert": a["id"], "zone": a["zone"], "label": body.outcome,
                       "cause": body.cause, "by": crew_name(j["crew_id"]), "at": now,
                       "pipe": fixed["pipe_id"] if fixed else None})
        return job_view(j)


# ============================================================== map data
@router.get("/map/zones", dependencies=[Depends(STAFF_ONLY)])
def map_zones():
    feats = []
    ov = {z["zone"]: z for z in overview()["zones"]}
    for f in api.GEO["zones"]["features"]:
        z = f["properties"]["zone"]
        feats.append({"type": "Feature", "geometry": f["geometry"],
                      "properties": {"zone": z, "name": ZONE_NAMES[z], "level": ov[z]["level"],
                                     "score": ov[z]["score"], "pipe_km": f["properties"]["pipe_km"]}})
    return {"type": "FeatureCollection", "features": feats}


PIPES_BY_ZONE = {}


@router.get("/map/pipes", dependencies=[Depends(STAFF_ONLY)])
def map_pipes(zone: str):
    if zone not in api.ZONES:
        raise HTTPException(404, "Unknown zone")
    if zone not in PIPES_BY_ZONE:
        feats = []
        for f in api.GEO["pipes"]["features"]:
            p = f["properties"]
            if p.get("zone") == zone and p.get("status", "open") == "open":
                coords = [[round(x, 5), round(y, 5)] for x, y in f["geometry"]["coordinates"]]
                feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
                              "properties": {"id": p["id"], "street": p.get("street"), "d": p.get("diameter_mm")}})
        PIPES_BY_ZONE[zone] = {"type": "FeatureCollection", "features": feats}
    return PIPES_BY_ZONE[zone]


class DemoLeakIn(BaseModel):
    pipe_id: str
    size: Literal["small", "medium", "large"] = "large"
    nights_ago: int = Field(0, ge=0, le=5)   # 0: started 45 minutes ago. 2: running since before 3 am two nights ago


@router.post("/demo/leak", dependencies=[Depends(ADMIN_ONLY)])
def demo_leak(body: DemoLeakIn):
    """System test: put a leak on this pipe in the network (started 45 minutes ago), then run the detector on its zone.
    The detector is not told about the leak; it only sees the meters."""
    zone = api.PIPE_ZONE.get(body.pipe_id)
    if zone not in api.ZONES:
        raise HTTPException(400, "Pick a pipe inside a zone")
    started = NOW - 45 * 60 if body.nights_ago == 0 else logger_nights()[-body.nights_ago] - 3600
    world_add_leak(body.pipe_id, body.size, started)
    refresh()
    aid = detect_zone(zone)
    flush()
    if aid is None:
        open_a = next((a for a in ALERTS.values() if a["zone"] == zone and a["status"] == "open" and a["kind"] != "public"), None)
        msg = ("The zone already has an open alert, so no second alert was raised." if open_a else
               "Test leak added. The meters do not show it clearly yet: a slow leak usually shows after 2 nights. "
               "The detector checks every few minutes.")
        return {"alert": None, "model_status": None, "message": msg}
    return {"alert": aid, "model_status": ALERTS[aid]["model_status"], "detail": public_alert_view(ALERTS[aid])}


# ---------------------------------------------------------------- system status (rules + model)
@router.get("/system/status", dependencies=[Depends(STAFF_ONLY)])
def system_status():
    err = MODEL["last_error"]
    return {"model": {"status": model_status(), "enabled": MODEL["enabled"], "timeout_s": MODEL["timeout_s"],
                      "zones_with_model": sorted(api.MODELS), "last_error": err[1] if err else None},
            "ruleset": {"active": RULESET,
                        "available": [{"version": v, "description": r.get("description"), "published": r.get("published")}
                                      for v, r in sorted(scoring.RULESETS.items(), key=lambda kv: scoring._version_key(kv[0]))]}}


@router.get("/repairs/priority", dependencies=[Depends(STAFF_ONLY)])
def repairs_priority():
    rows = repair_items()
    return {"items": rows, "weights": PRIORITY["weights"],
            "household_use_litres_per_day": PRIORITY["household_use_litres_per_day"],
            "totals": {"leaks": len(rows), "m3_per_day": round(sum(r["water_lost_m3_per_day"] or 0 for r in rows)),
                       "households": sum(r["households_est"] or 0 for r in rows)}}


REPORT_DEFAULT = {"water_cost_rs_per_m3": None, "currency": "Rs", "default_days": 7}


def report_settings():
    return load_settings("report.json", REPORT_DEFAULT)


@router.get("/report", dependencies=[Depends(STAFF_ONLY)])
def water_report(days: Optional[int] = None):
    """Water lost, water saved, money and worst zones over the last few days (default 7)."""
    cfg = report_settings()
    days = max(1, min(days or cfg["default_days"] or 7, HISTORY_DAYS - 1))
    start = NOW - days * 86400
    c = refresh()
    zones, daily = [], {}
    for z in api.ZONES:
        fc = c["flow"][api.ZONES[z]["inlet"]]
        t, v = fc["t"], fc["v"]
        sel = (t >= start) & (t < NOW)
        hours = ((t[sel] + 4 * 3600) // 3600) % 24
        expected = ZONE_PROPS[z]["avg_demand_lps"] * PATTERN[hours]
        ok = ~np.isnan(v[sel])
        water_in = float(np.sum(v[sel][ok])) * STEP_S / 1000
        exp_m3 = float(np.sum(expected[ok])) * STEP_S / 1000
        coverage = float(ok.mean()) if len(ok) else 0.0
        day = ((t[sel] + 4 * 3600) // 86400)[ok]
        extra = (v[sel][ok] - expected[ok]) * STEP_S / 1000
        for d_, x in zip(day, extra):
            daily[int(d_)] = daily.get(int(d_), 0.0) + float(x)
        alerts_z = [a for a in ALERTS.values() if a["zone"] == z and a["created"] >= start]
        zones.append({"zone": z, "name": ZONE_NAMES[z], "water_in_m3": round(water_in), "expected_m3": round(exp_m3),
                      "lost_m3": round(max(0.0, water_in - exp_m3)), "lost_pct": round((water_in - exp_m3) / exp_m3 * 100, 1) if exp_m3 else 0,
                      "data_coverage_pct": round(coverage * 100, 1), "alerts": len(alerts_z),
                      "open_leaks": len([a for a in ALERTS.values() if a["zone"] == z and a["status"] == "open" and a["kind"] != "public"])})
    zones.sort(key=lambda r: -r["lost_m3"])
    fixed = []
    for a in ALERTS.values():
        j = JOBS.get(a.get("job")) if a.get("job") else None
        if a["status"] == "resolved" and j and j.get("outcome") == "leak_found" and (a.get("resolved") or 0) >= start - 30 * 86400:
            per_day = (a.get("search") or {}).get("m3_day") or 0
            since = max(a["resolved"], start)
            fixed.append({"alert": a["id"], "zone": a["zone"], "zone_name": ZONE_NAMES[a["zone"]], "fixed": a["resolved"],
                          "m3_per_day": per_day, "saved_m3_in_period": round(per_day * max(0, NOW - since) / 86400),
                          "cause": j.get("cause"), "crew": crew_name(j["crew_id"])})
    open_rows = repair_items()
    total_in = sum(z["water_in_m3"] for z in zones)
    total_exp = sum(z["expected_m3"] for z in zones)
    lost = sum(z["lost_m3"] for z in zones)
    saved = sum(f["saved_m3_in_period"] for f in fixed)
    still = round(sum(r["water_lost_m3_per_day"] or 0 for r in open_rows))
    cost = cfg.get("water_cost_rs_per_m3")
    money = None
    if isinstance(cost, (int, float)) and cost > 0:
        money = {"cost_per_m3": cost, "currency": cfg.get("currency", "Rs"), "lost": round(lost * cost),
                 "saved": round(saved * cost), "still_losing_per_day": round(still * cost)}
    return {
        "days": days, "start": start, "end": NOW,
        "totals": {"water_in_m3": total_in, "expected_m3": total_exp, "lost_m3": lost,
                   "lost_pct": round((total_in - total_exp) / total_exp * 100, 1) if total_exp else 0,
                   "saved_m3": saved, "saved_per_day_m3": round(sum(f["m3_per_day"] for f in fixed)),
                   "still_losing_m3_per_day": still, "open_leaks": len([r for r in open_rows if r["kind"] != "public"]),
                   "alerts_raised": len([a for a in ALERTS.values() if a["created"] >= start]),
                   "reports": len([r for r in REPORTS.values() if r["created"] >= start]),
                   "repairs_done": len(fixed)},
        "money": money, "money_note": None if money else "The cost of water per m³ has not been set yet.",
        "zones": zones, "fixed": fixed,
        "daily": [{"day": d_ * 86400 - 4 * 3600, "lost_m3": round(max(0.0, x))} for d_, x in sorted(daily.items())],
        "note": "Expected use comes from each zone's normal demand pattern. Lost = water put in minus expected use.",
    }


EVAL_FILE = Path(__file__).with_name("evaluation") / "results.json"


def battledim_results():
    """Test on the public BattLeDIM L-Town benchmark (battledim/evaluate_ltown.py), if it has been run."""
    folder = EVAL_FILE.parent
    runs = {}
    for f in sorted(folder.glob("battledim_2019_wl-*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            runs[d["summary"]["ruleset"]] = d
        except Exception:
            continue
    if not runs:
        return None
    newest = max(runs, key=scoring._version_key)
    locator = None
    try:
        lo = json.loads((folder / "battledim_locator.json").read_text(encoding="utf-8"))
        locator = {k: lo[k] for k in ("trained_on", "training_leaks", "areas", "avg_area_radius_m", "simulated_holdout",
                                      "validation_2018", "test_2019")}
    except Exception:
        pass
    return {"locator": locator, "network": "BattLeDIM L-Town (782 junctions, 905 pipes, 33 pressure sensors)",
            "tuned_on": "2018", "tested_on": "2019",
            "rulesets": {v: r["summary"] for v, r in runs.items()},
            "newest": newest, "leaks": runs[newest]["leaks"], "method": runs[newest].get("method")}


@router.get("/evaluation", dependencies=[Depends(STAFF_ONLY)])
def evaluation_results():
    """Latest results of evaluate.py, if it has been run."""
    try:
        return {"available": True, **json.loads(EVAL_FILE.read_text(encoding="utf-8")), "battledim": battledim_results()}
    except FileNotFoundError:
        return {"available": False, "how": "Run: python evaluate.py (in the backend folder)"}
    except Exception as e:
        return {"available": False, "how": f"Results file could not be read: {e}"}


@router.post("/system/reset")
def reset_network(user=Depends(ADMIN_ONLY)):
    """Admin only: start fresh before a demo. Deletes every alert, public report, crew job, crew result and
    leak in the network. Accounts, settings and the meters stay. New leaks then start on their own again."""
    global NOW
    with LOCK:
        ALERTS.clear(); REPORTS.clear(); JOBS.clear(); LABELS.clear(); LEAKS.clear()
        NOW = int(time.time()) // STEP_S * STEP_S
        LIVE_STATE["last_scan"] = None
        VERSION["n"] += 1
    flush()
    log.warning("Network reset by %s", user["name"])
    return {"alerts": 0, "reports": 0, "jobs": 0}


class ModelSwitch(BaseModel):
    enabled: bool


@router.post("/system/model")
def switch_model(body: ModelSwitch, user=Depends(ADMIN_ONLY)):
    """For the demo: switch the anomaly model off to show that alerts still come from the rules."""
    MODEL["enabled"] = body.enabled
    log.warning("Anomaly model switched %s by %s", "on" if body.enabled else "off", user["name"])
    return system_status()


@router.get("/model/metrics", dependencies=[Depends(STAFF_ONLY)])
def model_metrics():
    path = api.NET / "models" / "metrics.json"
    return json.loads(path.read_text()) if path.exists() else []


# ============================================================== helpers
def pick_pipe(zone, rng, min_d=0):
    options = [p for p, z in api.PIPE_ZONE.items()
               if z == zone and not p.startswith("P_IN") and PIPE.get(p, {}).get("status", "open") == "open"
               and (PIPE.get(p, {}).get("diameter_mm") or 0) >= min_d and (PIPE.get(p, {}).get("length_m") or 0) > 30]
    return rng.choice(sorted(options))


# ============================================================== live network, detector and database
# The meters read a simulated network (there are no live CWA meters to connect yet). Leaks start in that
# network on their own, at random, like they do in a real one. Nothing tells the detector about them: it
# only sees the meter readings, scores them with the rules and raises an alert when the score is high enough.
LIVE_DEFAULT = {
    "leaks_per_day": 4,             # new leaks in the whole network, on average
    "size_mix": {"small": 0.35, "medium": 0.35, "large": 0.30},   # large ones are bursts
    "max_active_leaks": 6,          # unfixed leaks the network can have at once
    "scan_minutes": 5,              # how often the detector checks every zone
    "realert_cooldown_hours": 6,    # after an alert is closed with no leak found, wait before alerting that zone again
    "burst_flow_pct": 5,            # flow in the last 30 minutes this much above usual...
    "burst_drop_mm": 60,            # ...and a pressure drop this big = burst
    "needs_one_of": ["NF-01", "NF-03", "PR-01", "AC-01"],   # a direct leak sign; the 24 h water balance alone is not enough
}
LIVE = load_settings("network.json", LIVE_DEFAULT)
DB = {"store": None, "last_flush": 0}
LIVE_STATE = {"running": False, "last_scan": None, "scans": 0}


def world_add_leak(pipe_id, size, started):
    """Put a leak in the simulated network. EPANET works out what each meter will see."""
    zone, readings, truth = simulate(pipe_id, size)
    lid = uuid.uuid4().hex[:8]
    with LOCK:
        LEAKS[lid] = {"id": lid, "zone": zone, "pipe_id": pipe_id, "size": size,
                      "kind": "burst" if size == "large" else "slow", "start": int(started), "end": None,
                      "flow_lps": truth["leak_flow_lps"],
                      "flow_lps_h8": round(truth["leak_flow_lps"] * max(1.0, readings.get("inflow_change_lps_h08", 0) /
                                                                      max(readings.get("inflow_change_lps_h03", 0), 1e-6)), 3),
                      "dp_mm": {k.split("_h03_")[1]: v for k, v in readings.items() if "_h03_" in k},
                      "dp_mm_h8": {k.split("_h08_")[1]: v for k, v in readings.items() if "_h08_" in k},
                      "latlon": truth["latlon"]}
        VERSION["n"] += 1
    return lid


def world_spawn(t_from, t_to, rng):
    """Random new leaks between two times (a Poisson process), up to the active limit."""
    active = [lk for lk in LEAKS.values() if not lk["end"]]
    expected = LIVE["leaks_per_day"] * (t_to - t_from) / 86400
    n = int(np.random.default_rng(rng.randrange(10 ** 9)).poisson(expected))
    sizes, weights = zip(*LIVE["size_mix"].items())
    for _ in range(min(n, max(0, LIVE["max_active_leaks"] - len(active)))):
        zone = rng.choice(list(api.ZONES))
        size = rng.choices(sizes, weights)[0]
        pipe = pick_pipe(zone, rng, 150 if size == "large" else 0)
        world_add_leak(pipe, size, rng.randint(t_from, t_to - 60))


def _window_mean(c, t0, t1):
    sel = (c["t"] >= t0) & (c["t"] < t1)
    vals = c["v"][sel]
    return float(np.nanmedian(vals)) if sel.any() and not np.all(np.isnan(vals)) else float("nan")   # median: one bad reading cannot swing it


def _vs_usual(c, t0, t1, days):
    """Mean in a time window, and the usual value (median of the same window on previous days)."""
    now = _window_mean(c, t0, t1)
    prev = [_window_mean(c, t0 - k * 86400, t1 - k * 86400) for k in range(1, days + 1)]
    prev = [x for x in prev if not math.isnan(x)]
    return now, (float(np.median(prev)) if prev else float("nan"))


def last_reading_time(h):
    """The latest h:00 (Mauritius time) whose one-hour window (h-30 min to h+30 min) is complete."""
    t = (NOW + 4 * 3600) // 86400 * 86400 - 4 * 3600 + h * 3600
    return t - 86400 if t > NOW - 30 * 60 else t


def readings_from_meters(zone):
    """The same features the model was trained on, worked out from the cleaned meter readings:
    pressure drop at each sensor and extra inflow at 3am and 8am, against the same time on previous days."""
    c = refresh()
    inlet = c["flow"][api.ZONES[zone]["inlet"]]
    out = {}
    for h in api.HOURS:
        t = last_reading_time(h)
        q, q0 = _vs_usual(inlet, t - 15 * 60, t + 15 * 60, 7)
        out[f"inflow_change_lps_h{h:02d}"] = round(q - q0, 4) if not math.isnan(q - q0) else 0.0
        for sid in api.ZONES[zone]["sensors"]:
            p, p0 = _vs_usual(c["pressure"][sid], t - 30 * 60, t + 30 * 60, 6)
            out[f"dp_mm_h{h:02d}_{sid}"] = round((p0 - p) * 1000, 2) if not math.isnan(p0 - p) else 0.0
    return out


def sensor_wobble_mm(sid, t, nights=7):
    """How much this sensor's reading at time t normally moves from one night to the next (robust spread, mm)."""
    c = refresh()["pressure"][sid]
    vals = [_window_mean(c, t - k * 86400 - 30 * 60, t - k * 86400 + 30 * 60) for k in range(1, nights + 1)]
    vals = np.array([x for x in vals if not math.isnan(x)])
    if len(vals) < 4:
        return None
    return float(1.4826 * np.median(np.abs(vals - np.median(vals))) * 1000)


def wobble_filter(dp, rs, t):
    """Ruleset pressure_check: a drop only counts if it is bigger than k times the sensor's usual wobble.
    Sensors with too little history keep their drop as it is."""
    k = (rs.get("pressure_check") or {}).get("spread_k")
    if not k:
        return dp
    out = {}
    for sid, mm in dp.items():
        w = sensor_wobble_mm(sid, t)
        out[sid] = mm if w is None or mm > k * max(w, 1.0) else 0.0
    return out


def burst_check(zone):
    """Last 30 minutes against the same 30 minutes on previous days: a flow jump and a pressure drop together."""
    c = refresh()
    t0, t1 = NOW - 30 * 60, NOW
    q, q0 = _vs_usual(c["flow"][api.ZONES[zone]["inlet"]], t0, t1, 7)
    dp = {}
    for sid in api.ZONES[zone]["sensors"]:
        p, p0 = _vs_usual(c["pressure"][sid], t0, t1, 6)
        if not math.isnan(p0 - p) and meter_trust(sid) >= scoring.get(RULESET)["trust"]["min"]:
            dp[sid] = round((p0 - p) * 1000, 1)
    jump = (q - q0) if not math.isnan(q - q0) else 0.0
    pct = jump / q0 * 100 if q0 else 0.0
    qb, qb0 = _vs_usual(c["flow"][api.ZONES[zone]["inlet"]], NOW - 150 * 60, NOW - 120 * 60, 7)
    before = (qb - qb0) / qb0 * 100 if qb0 and not math.isnan(qb - qb0) else 0.0
    drops = sorted(dp.values(), reverse=True) + [0, 0]
    # a burst drops pressure at more than one sensor; one sensor alone is more likely a meter problem
    # sudden: the flow rose this much since two hours ago (a busy day raises both, so it cancels out)
    is_burst = (pct - before >= LIVE["burst_flow_pct"]
                and drops[0] >= LIVE["burst_drop_mm"] and drops[1] >= LIVE["burst_drop_mm"] / 3)
    return is_burst, jump, pct, dp


def _started_estimate(zone, kind):
    if kind == "burst":
        c = refresh()["flow"][api.ZONES[zone]["inlet"]]
        for back in range(1, 13):                        # walk back 5 minutes at a time, up to an hour
            q, q0 = _vs_usual(c, NOW - back * STEP_S, NOW - (back - 1) * STEP_S, 7)
            if math.isnan(q) or q0 <= 0 or (q - q0) / q0 * 100 < LIVE["burst_flow_pct"] / 2:
                return NOW - (back - 1) * STEP_S
        return NOW - 3600
    nights = zone_flow_stats(zone)["nights"]
    run = zone_flow_stats(zone)["nights_running"]
    return nights[-max(run, 1)]["night"] - 6 * 3600 if nights else NOW - 86400


def detect_zone(zone):
    """Look at one zone's meters and raise an alert if the rules say so. Returns the new alert id or None."""
    is_burst, jump, pct, dp_now = burst_check(zone)
    with LOCK:
        open_kinds = {a["kind"] for a in ALERTS.values() if a["zone"] == zone and a["status"] == "open" and a["kind"] != "public"}
        # one open alert per zone, except that a burst still gets its own alert next to an open slow leak
        if open_kinds and not (is_burst and "burst" not in open_kinds):
            return None
        cool = LIVE["realert_cooldown_hours"] * 3600
        closed = [a for a in ALERTS.values() if a["zone"] == zone and a["status"] == "resolved" and a["kind"] != "public"]
        if any((a.get("resolved") or 0) > NOW - cool and (JOBS.get(a.get("job")) or {}).get("outcome") != "leak_found"
               for a in closed):
            return None
        last_closed = max((a.get("resolved") or 0 for a in closed), default=0)
    if not is_burst:
        # night signals come from last night: if an alert in this zone was closed after that, wait for the next night
        if last_closed >= last_reading_time(min(api.HOURS)) - 6 * 3600:
            return None
    if is_burst:
        readings = {}
        for h in api.HOURS:
            readings[f"inflow_change_lps_h{h:02d}"] = round(jump, 4)
            for sid in api.ZONES[zone]["sensors"]:
                readings[f"dp_mm_h{h:02d}_{sid}"] = dp_now.get(sid, 0.0)
        dp = dp_now
    else:
        readings = readings_from_meters(zone)
        dp = {k.split("_h03_")[1]: v for k, v in readings.items() if "_h03_" in k}
        # the model sees every drop; the rules only count drops bigger than the sensor's normal night-to-night wobble
        dp = wobble_filter(dp, scoring.get(RULESET), last_reading_time(min(api.HOURS)))
    pred, status = ask_model(zone, readings)
    search = search_from_model(pred) if pred and pred.get("leak") else search_from_meters(zone, dp, readings)
    if is_burst and jump > 0:
        search.update(flow_lps=round(jump, 2), m3_day=round(jump * 86.4, 1))
    kind = "burst" if is_burst else "slow"
    top_pipes = search["areas"][0]["pipes"] if search.get("areas") else []
    a = {"id": uuid.uuid4().hex[:8], "zone": zone, "kind": kind, "status": "open", "started": _started_estimate(zone, kind),
         "created": NOW, "model": pred or {}, "model_status": status, "search": search, "dp_mm": dp,
         "streets": streets_of(top_pipes), "reports": [], "job": None, "events": [], "ruleset": RULESET}
    apply_loggers(a)
    a["streets"] = streets_of(a["search"]["areas"][0]["pipes"]) if a["search"].get("areas") else a["streets"]
    with LOCK:
        ALERTS[a["id"]] = a
        sc = score_alert(a)
        codes = {r["code"] for r in sc["trace"] if r.get("points")}
        if sc["score"] < scoring.get(RULESET)["bands"]["watch"] or not codes & set(LIVE["needs_one_of"]):
            del ALERTS[a["id"]]
            return None
        # public reports already waiting nearby join this alert
        for r in REPORTS.values():
            if r["zone"] == zone and r["alert"] is None and r["created"] > NOW - 86400:
                link_report(r)
        log.info("Alert %s raised in %s (%s)", a["id"], zone, kind)
        return a["id"]


def scan_all():
    with LOCK:
        for a in ALERTS.values():
            apply_loggers(a)
    out = [aid for z in api.ZONES if (aid := detect_zone(z))]
    LIVE_STATE.update(last_scan=NOW, scans=LIVE_STATE["scans"] + 1)
    return out


def end_leak_near(a, at):
    """A crew fixed the leak behind this alert: stop the nearest active leak in that zone."""
    active = [lk for lk in LEAKS.values() if lk["zone"] == a["zone"] and not lk["end"]]
    if not active:
        return None
    g = (a.get("search") or {}).get("best_guess") or a.get("latlon")
    lk = min(active, key=lambda x: km(g[0], g[1], *x["latlon"]) if g else 0)
    lk["end"] = int(at)
    VERSION["n"] += 1
    return lk


def flush():
    st = DB["store"]
    if not st:
        return
    with LOCK:
        st.flush("alerts", ALERTS)
        st.flush("reports", REPORTS)
        st.flush("jobs", JOBS)
        st.flush("crew_results", {l["id"]: l for l in LABELS})
        st.flush("network_leaks", LEAKS)
        st.set_meta("report_counter", CODE_COUNTER["n"])
        st.set_meta("last_tick", NOW)
    DB["last_flush"] = int(time.time())


def tick(rng):
    """Move the clock to the latest 5 minutes, let the network spring leaks, scan, save."""
    global NOW
    new_now = int(time.time()) // STEP_S * STEP_S
    if new_now <= NOW:
        return False
    world_spawn(NOW, new_now, rng)
    with LOCK:
        NOW = new_now
        VERSION["n"] += 1
    refresh()
    if NOW - (LIVE_STATE["last_scan"] or 0) >= LIVE["scan_minutes"] * 60 - 1:
        scan_all()
    flush()
    return True


def _loop():
    rng = random.Random()
    while True:
        try:
            tick(rng)
        except Exception:
            log.exception("Live tick failed")
        time.sleep(20)


def start(db_path=None, run_loop=True):
    """Open the database, load everything, seed accounts on the very first start, and run the network live."""
    global NOW
    st = store.Store(db_path)
    DB["store"] = st
    global SECRET
    if not os.environ.get("DLOWATCH_SECRET"):
        key = st.get_meta("token_key")
        if not key:
            key = os.urandom(32).hex()
            st.set_meta("token_key", key)
        SECRET = key.encode()
    with LOCK:
        ALERTS.update(st.load("alerts"))
        REPORTS.update(st.load("reports"))
        JOBS.update(st.load("jobs"))
        LABELS.extend(sorted(st.load("crew_results").values(), key=lambda l: l.get("at") or 0))
        LEAKS.update(st.load("network_leaks"))
        CODE_COUNTER["n"] = st.get_meta("report_counter", CODE_COUNTER["n"])
        first = st.get_meta("first_start")
        if first is None:
            first = NOW
            st.set_meta("first_start", first)
        FAULT_EPOCH["t"] = first
        if st.users_count() == 0:
            for login, u in STAFF.items():
                st.add_user(login, "staff", u["role"], u["name"], u["password"])
            for login, u in CREW_USERS.items():
                st.add_user(login, "crew", "crew", u["name"], u["pin"], u["crew_id"])
            log.info("Accounts created from config/people.json")
        VERSION["n"] += 1
    # if the server was off, the network kept running: spring the leaks of that time (up to 2 days)
    last = st.get_meta("last_tick")
    if last and NOW - last > STEP_S:
        world_spawn(max(last, NOW - 2 * 86400), NOW, random.Random())
    refresh()
    scan_all()
    flush()
    print(f"DloWatch: database {st.path.name}, {len(ALERTS)} alerts, {len(REPORTS)} reports, "
          f"{len([l for l in LEAKS.values() if not l['end']])} unfixed leaks in the network")
    if run_loop and not LIVE_STATE["running"]:
        LIVE_STATE["running"] = True
        threading.Thread(target=_loop, daemon=True, name="dlowatch-live").start()
