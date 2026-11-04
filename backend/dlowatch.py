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


