"""
Acoustic noise loggers.

A noise logger is a small sensor fixed in a valve or hydrant chamber. Between 2 and 4 am, when the streets are
quiet, it listens to the pipe and sends one noise level per night (in dB). Water escaping from a pipe makes a
steady hiss that travels along the pipe, so a logger near a leak gets louder and stays loud night after night.
Traffic or a night pump can make it loud for one night, but that does not last.

This file places the loggers on the network. The layout is the same every start-up. Settings are in
config/acoustic.json.
"""
import json
import math
from pathlib import Path

import numpy as np

ACOUSTIC_DEFAULT = {
    "km_of_pipe_per_logger": 4.0,   # one logger for every 4 km of pipe in a zone
    "hearing_range_m": 300,          # a logger hears a leak up to about this far away
}

MAIN_ROADS = {"trunk", "primary", "secondary"}
_KX = 111320 * math.cos(math.radians(-20.1))   # metres per degree of longitude around Mauritius
_KY = 110540                                    # metres per degree of latitude


def xy(lat, lon):
    """Rough metres east and north. Good enough for distances inside one zone."""
    return lon * _KX, lat * _KY


def load_settings(config_dir):
    try:
        raw = json.loads((Path(config_dir) / "acoustic.json").read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    out = dict(ACOUSTIC_DEFAULT)
    for k, v in raw.items():
        if k in ACOUSTIC_DEFAULT and isinstance(v, (int, float)) and v > 0:
            out[k] = v
    return out


def place_loggers(geo, zones, settings):
    """
    Spread loggers evenly over each zone: start at the junction nearest the middle, then keep adding the
    junction farthest from all loggers so far. Junctions that already have a pressure sensor are skipped.
    Returns {logger_id: {id, zone, junction, lat, lon}}.
    """
    taken = {s for z in zones.values() for s in z["sensors"]}
    by_zone, pipe_m = {}, {}
    for f in geo["nodes"]["features"]:
        p = f["properties"]
        if p["id"].startswith("J_") and p.get("zone") in zones and p["id"] not in taken:
            lon, lat = f["geometry"]["coordinates"]
            by_zone.setdefault(p["zone"], []).append((p["id"], lat, lon))
    for f in geo["pipes"]["features"]:
        p = f["properties"]
        pipe_m[p.get("zone")] = pipe_m.get(p.get("zone"), 0) + (p.get("length_m") or 0)

    roads = {}
    for f in geo["pipes"]["features"]:
        p = f["properties"]
        if p.get("zone") in zones:
            lon, lat = np.array(f["geometry"]["coordinates"]).reshape(-1, 2).mean(axis=0)
            roads.setdefault(p["zone"], []).append((*xy(lat, lon), p.get("road_type") or ""))

    loggers = {}
    for z in sorted(zones):
        nodes = sorted(by_zone.get(z, []))
        if not nodes:
            continue
        n = max(1, math.ceil(pipe_m.get(z, 0) / 1000 / settings["km_of_pipe_per_logger"]))
        pts = np.array([xy(lat, lon) for _, lat, lon in nodes])
        mid = pts.mean(axis=0)
        i = int(np.argmin(((pts - mid) ** 2).sum(axis=1)))
        chosen, d = [i], ((pts - pts[i]) ** 2).sum(axis=1)
        for _ in range(min(n, len(nodes)) - 1):
            i = int(np.argmax(d))
            chosen.append(i)
            d = np.minimum(d, ((pts - pts[i]) ** 2).sum(axis=1))
        # number them from north-west to south-east so neighbours get close numbers
        chosen.sort(key=lambda k: (-round(nodes[k][1], 3), nodes[k][2]))
        for k, idx in enumerate(chosen, 1):
            jid, lat, lon = nodes[idx]
            lid = f"AL-{z}-{k:02d}"
            x, y = xy(lat, lon)
            road = min(roads.get(z, [(x, y, "")]), key=lambda r: (r[0] - x) ** 2 + (r[1] - y) ** 2)[2]
            loggers[lid] = {"id": lid, "zone": z, "junction": jid, "lat": round(lat, 6), "lon": round(lon, 6),
                            "main_road": road.split("_")[0] in MAIN_ROADS}
    return loggers


def pipe_coverage(geo, loggers, settings):
    """Share of pipe length (per zone and overall) within hearing range of at least one logger."""
    rng = settings["hearing_range_m"]
    pts = {}
    for lg in loggers.values():
        pts.setdefault(lg["zone"], []).append(xy(lg["lat"], lg["lon"]))
    pts = {z: np.array(v) for z, v in pts.items()}
    tot, cov = {}, {}
    for f in geo["pipes"]["features"]:
        p = f["properties"]
        z = p.get("zone")
        if z not in pts:
            continue
        lon, lat = np.array(f["geometry"]["coordinates"]).reshape(-1, 2).mean(axis=0)
        m = np.array(xy(lat, lon))
        length = p.get("length_m") or 0
        tot[z] = tot.get(z, 0) + length
        if np.sqrt(((pts[z] - m) ** 2).sum(axis=1)).min() <= rng:
            cov[z] = cov.get(z, 0) + length
    per_zone = {z: round(100 * cov.get(z, 0) / tot[z]) for z in sorted(tot) if tot[z]}
    overall = round(100 * sum(cov.values()) / max(1, sum(tot.values())))
    return {"zones": per_zone, "overall": overall}


# ---------------------------------------------------------------- night noise readings
# Simulated, like the flow and pressure readings. What a logger records each night:
#   background  a quiet street at 3 am: about 15 to 23 dB, a few dB more next to a main road
#   wobble      +-1.5 dB from night to night
#   one-offs    about 1 night in 50 a logger picks up something else (a night pump, roadworks, a tank filling):
#               6 to 14 dB louder for that one night only
#   leaks       a leak hisses at about 32 dB + 6 dB for every tenfold more water at the pipe. The sound fades
#               along the pipe so that a medium leak at the hearing range is as loud as the background.
#               A slow leak gets louder over its first day, like its flow.
ONE_OFF_CHANCE = 0.02


def leak_source_db(flow_lps):
    return 32.0 + 6.0 * math.log10(max(flow_lps, 0.05))


def fade_db_per_m(settings):
    """A medium leak (about 2 L/s) at the hearing range is as loud as an average background (19 dB)."""
    return (leak_source_db(2.0) - 19.0) / settings["hearing_range_m"]


def _add_db(a, b):
    """Two sounds together: add their power, not their dB."""
    return 10 * math.log10(10 ** (a / 10) + 10 ** (b / 10))


def night_levels(loggers, leaks, nights, settings, seed_of):
    """
    One level per logger per night. nights: list of 3 am times. leaks: the simulated leaks, each with
    zone, latlon, flow_lps, kind, start, end. Returns {logger_id: [{"night": t, "db": level}, ...]} oldest first,
    plus {logger_id: {night: cause}} for testing only ("leak:<id>" or "one-off"); the app never shows the causes.
    """
    fade = fade_db_per_m(settings)
    by_zone = {}
    for lk in leaks:
        if lk.get("latlon"):
            by_zone.setdefault(lk["zone"], []).append((lk, xy(*lk["latlon"])))
    out, causes = {}, {}
    for lid, lg in loggers.items():
        base = 15.0 + (seed_of(lid) % 800) / 100.0 + (3.0 if lg.get("main_road") else 0.0)
        x, y = xy(lg["lat"], lg["lon"])
        near = []
        for lk, (lx, ly) in by_zone.get(lg["zone"], []):
            d = math.hypot(lx - x, ly - y)
            if d <= 3 * settings["hearing_range_m"]:
                near.append((lk, d))
        rows, why = [], {}
        for t in nights:
            r = np.random.default_rng(seed_of(f"{lid}:{t}"))
            db = base + float(r.normal(0, 1.5))
            if r.random() < ONE_OFF_CHANCE:
                db += float(r.uniform(6, 14))
                why[t] = "one-off"
            for lk, d in near:
                end = lk.get("end") or 10 ** 12
                if not (lk["start"] <= t < end):
                    continue
                q = lk["flow_lps"]
                if lk.get("kind") == "slow":
                    q *= min(1.0, max(0.25, (t - lk["start"]) / 86400))
                level = leak_source_db(q) - fade * d
                db = _add_db(db, level)
                if level > base - 3:
                    why[t] = f"leak:{lk['id']}"
            rows.append({"night": int(t), "db": round(db, 1)})
        out[lid], causes[lid] = rows, why
    return out, causes
