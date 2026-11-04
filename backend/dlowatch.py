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


def point_in_zone(lat, lon):
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
    for ar in areas:
        p_area = ar["probability"] if ar.get("probability") is not None else 1.0
        for pid in ar["pipes"]:
            m = PIPE_MID.get(pid)
            if not m:
                continue
            d = km(g[0], g[1], m["lat"], m["lon"]) * 1000
            w = p_area * math.exp(-0.5 * (d / spread) ** 2)
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
                            + " for this alert. Scored by the rules only, search area from the pressure sensors.")
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
    if any(c.startswith("PUB") for c in codes):
        return f"{len(a.get('reports', []))} public reports close together"
    return "Small change, keep watching"


def job_summary(job_id):
    j = JOBS.get(job_id) if job_id else None
    if not j:
        return None
    return {"id": j["id"], "crew": CREWS[j["crew_id"]]["name"], "status": j["status"], "outcome": j.get("outcome")}


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


class ReportIn(BaseModel):
    lat: float = Field(ge=-20.6, le=-19.9)
    lon: float = Field(ge=57.2, le=57.9)
    kind: Literal["water_on_road", "broken_pipe", "no_water", "low_pressure"]
    note: Optional[str] = Field(None, max_length=300)
    device: str = Field(min_length=6, max_length=64, description="random id the phone keeps, not personal")
    photo: Optional[str] = Field(None, description="data URL of a photo, optional", max_length=3_000_000)


def add_report(body: ReportIn, at=None):
    with LOCK:
        CODE_COUNTER["n"] += 1
        code = f"R-{CODE_COUNTER['n']}"
        zone = point_in_zone(body.lat, body.lon)
        r = {"code": code, "created": at or int(time.time()), "lat": round(body.lat, 4), "lon": round(body.lon, 4),
             "kind": body.kind, "note": body.note, "device": hashlib.sha256(body.device.encode()).hexdigest()[:12],
             "photo": body.photo, "zone": zone, "alert": None, "cluster": None}
        REPORTS[code] = r
        link_report(r)
        return r


def link_report(r):
    """Attach a report to an open alert nearby, or group it with other reports."""
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
    near = [x for x in REPORTS.values() if x["code"] != r["code"] and x["alert"] is None
            and km(r["lat"], r["lon"], x["lat"], x["lon"]) <= 0.15 and r["created"] - x["created"] <= 86400]
    group = near + [r]
    if len({x["device"] for x in group}) >= 3:
        aid = uuid.uuid4().hex[:8]
        lat = sum(x["lat"] for x in group) / len(group)
        lon = sum(x["lon"] for x in group) / len(group)
        ALERTS[aid] = {"id": aid, "zone": r["zone"] or "Z1", "kind": "public", "status": "open",
                       "started": min(x["created"] for x in group), "created": r["created"],
                       "model": {}, "model_status": "ok", "ruleset": RULESET,
                       "search": {"source": "reports", "best_guess": [lat, lon], "areas": [], "flow_lps": None, "m3_day": None},
                       "dp_mm": {}, "latlon": [lat, lon],
                       "streets": [], "reports": [x["code"] for x in group], "job": None, "events": []}
        for x in group:
            x["alert"] = aid


PUBLIC_DEFAULT = {"max_reports_per_phone_per_hour": 5, "max_reports_per_phone_per_day": 15, "same_photo_block_hours": 24}
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
    if h and any(r.get("photo_hash") == h and now - r["created"] < PUBLIC["same_photo_block_hours"] * 3600
                 for r in REPORTS.values()):
        raise HTTPException(409, "This photo was already sent with another report. Please take a new photo, or send without one.")
    note, cleaned = clean_note(body.note)
    r = add_report(body.model_copy(update={"note": note}))
    r["photo_hash"] = h
    return {"code": r["code"], "status": public_status(r), "note_cleaned": cleaned}


def public_status(r):
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


