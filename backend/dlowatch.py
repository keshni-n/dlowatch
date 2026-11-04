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


