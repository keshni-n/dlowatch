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
            loggers[lid] = {"id": lid, "zone": z, "junction": jid, "lat": round(lat, 6), "lon": round(lon, 6)}
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
