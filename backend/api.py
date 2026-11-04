"""
Backend API for the water loss dashboard (FastAPI).

It reads what the scripts produced (models, GeoJSON, EPANET files) and serves it
to the Angular dashboard. It does not rebuild or retrain anything.

Run (from the port_louis_epanet folder):
  pip install fastapi uvicorn
  uvicorn api:app --reload                          # uses output_north
  set NETWORK_DIR=output && uvicorn api:app --reload   # Port Louis (Windows)

Then open http://localhost:8000/ for the dashboard, or /docs to try every endpoint.

Endpoints:
  GET  /health                 is it running, which network
  GET  /zones                  zone outlines + stats (island view)
  GET  /pipes?zone=Z3          pipes (all, or one zone)
  GET  /nodes?zone=Z3          junctions, reservoirs, sensors
  GET  /sensors                sensors per zone + inlet meters
  POST /predict                sensor readings -> leak? where? how big?
  POST /simulate-leak          make a leak on a pipe with EPANET, run the model
  GET  /repairs                open leak alerts, biggest water loss first
  POST /repairs/{id}/resolve   mark an alert as repaired
"""

import json
import os
import time
import uuid
from pathlib import Path
from typing import Literal, Optional

import numpy as np
import wntr
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pyproj import Transformer

from build_port_louis_network import UTM_CRS
from generate_leak_dataset import zone_jobs
from train_models import load_models, predict

NET = Path(os.environ.get("NETWORK_DIR", "output_north"))
LEAK_SIZES = {"small": 0.15, "medium": 0.4, "large": 0.9}   # emitter coeff, L/s per sqrt(m)
to_latlon = Transformer.from_crs(UTM_CRS, "EPSG:4326", always_xy=True)

app = FastAPI(title="Water Loss Detection API",
              description="Illustrative network on real OpenStreetMap streets. Not real CWA data.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------- load once at startup
def read_json(name):
    path = NET / name
    return json.loads(path.read_text()) if path.exists() else None


print(f"Loading network from {NET} ...")
GEO = {k: read_json(f"{k}.geojson") for k in ("pipes", "nodes", "zones")}
SENSORS = read_json("sensors.json")
INFO = read_json("leak_dataset_info.json") or {"settings": {"hours": [3, 8]}}
HOURS = INFO["settings"]["hours"]
MODELS = load_models(NET / "models")
ZONES = {z: {"inp": inp, "sensors": s, "inlet": inlet} for z, inp, s, inlet in zone_jobs(NET)}
PIPE_ZONE = {f["properties"]["id"]: f["properties"].get("zone", next(iter(ZONES)))
             for f in GEO["pipes"]["features"]}
ALERTS = {}   # in-memory alert list (resets when the server restarts)


def run_zone(wn):
    """Run EPANET up to the last recorded hour and return the results."""
    wn.options.time.duration = max(HOURS) * 3600
    return wntr.sim.EpanetSimulator(wn).run_sim(file_prefix=str(NET / f"_api_{uuid.uuid4().hex[:6]}"))


def cleanup():
    for f in NET.glob("_api_*"):
        try:
            f.unlink()
        except OSError:
            pass


BASELINE = {}
for z, zinfo in ZONES.items():
    BASELINE[z] = run_zone(wntr.network.WaterNetworkModel(zinfo["inp"]))
cleanup()
print(f"Ready: {len(ZONES)} zone(s), {len(MODELS)} model set(s), hours {HOURS}")


def features_from_results(zone, res):
    """Same feature names as leak_dataset.csv: pressure drops and inflow change."""
    z, base = ZONES[zone], BASELINE[zone]
    out = {}
    for h in HOURS:
        t = h * 3600
        tag = f"h{h:02d}"
        q0 = abs(base.link["flowrate"].loc[t, z["inlet"]])
        q1 = abs(res.link["flowrate"].loc[t, z["inlet"]])
        out[f"inflow_change_lps_{tag}"] = round(float(q1 - q0) * 1000, 4)
        for s in z["sensors"]:
            dp = base.node["pressure"].loc[t, s] - res.node["pressure"].loc[t, s]
            out[f"dp_mm_{tag}_{s}"] = round(float(dp) * 1000, 2)
    return out


def add_latlon(result):
    """Models work in UTM metres; the map needs lat/lon."""
    if "best_guess_xy" in result:
        lon, lat = to_latlon.transform(*result["best_guess_xy"])
        result["best_guess_latlon"] = [round(lat, 6), round(lon, 6)]
    for a in result.get("top_areas", []):
        lon, lat = to_latlon.transform(*a["centre_xy"])
        a["centre_latlon"] = [round(lat, 6), round(lon, 6)]
    return result


def filter_zone(fc, zone):
    if zone is None:
        return fc
    return {**fc, "features": [f for f in fc["features"] if f["properties"].get("zone") == zone]}


# ---------------------------------------------------------------- endpoints
@app.get("/health")
def health():
    return {"status": "ok", "network": str(NET), "zones": list(ZONES),
            "models": sorted(MODELS), "hours": HOURS}


@app.get("/zones")
def zones():
    if GEO["zones"] is None:   # Port Louis network has no zone file
        return {"type": "FeatureCollection", "features": []}
    return GEO["zones"]


@app.get("/pipes")
def pipes(zone: Optional[str] = None):
    return filter_zone(GEO["pipes"], zone)


@app.get("/nodes")
def nodes(zone: Optional[str] = None):
    return filter_zone(GEO["nodes"], zone)


@app.get("/sensors")
def sensors():
    return {z: {"pressure_sensors": v["sensors"], "inlet_meter": v["inlet"]}
            for z, v in ZONES.items()}


class PredictRequest(BaseModel):
    zone: str = Field(examples=["Z3"])
    readings: dict = Field(description="feature name -> value, same names as leak_dataset.csv")


@app.post("/predict")
def predict_endpoint(req: PredictRequest):
    if req.zone not in MODELS:
        raise HTTPException(404, f"No model for zone {req.zone}")
    return add_latlon(predict(MODELS, req.zone, req.readings))


class LeakRequest(BaseModel):
    pipe_id: str = Field(examples=["P_1234"])
    size: Literal["small", "medium", "large"] = "medium"
    position: float = Field(0.5, ge=0.05, le=0.95, description="where along the pipe (0 to 1)")


@app.post("/simulate-leak")
def simulate_leak(req: LeakRequest):
    """Put a leak on a pipe, run EPANET, then ask the model where it is.
    Returns the real answer next to the model's answer, for the demo."""
    t0 = time.time()
    zone = PIPE_ZONE.get(req.pipe_id)
    if zone is None:
        raise HTTPException(404, f"Unknown pipe {req.pipe_id}")
    if zone not in ZONES:
        raise HTTPException(400, f"{req.pipe_id} is a closed boundary valve, pick a pipe inside a zone")

    wn = wntr.network.WaterNetworkModel(ZONES[zone]["inp"])
    wn = wntr.morph.split_pipe(wn, req.pipe_id, f"{req.pipe_id}_LEAK_B", "LEAK_NODE",
                               split_at_point=req.position)
    wn.get_node("LEAK_NODE").emitter_coefficient = LEAK_SIZES[req.size] / 1000   # to SI
    res = run_zone(wn)
    cleanup()

    readings = features_from_results(zone, res)
    t_first = min(HOURS) * 3600
    # LEAK_NODE has no normal water use, so all its outflow is the leak
    leak_flow = float(res.node["demand"].loc[t_first, "LEAK_NODE"]) * 1000
    x, y = wn.get_node("LEAK_NODE").coordinates
    lon, lat = to_latlon.transform(x, y)

    result = add_latlon(predict(MODELS, zone, readings))
    truth = {"pipe_id": req.pipe_id, "zone": zone, "size": req.size,
             "leak_flow_lps": round(leak_flow, 2),
             "water_lost_m3_per_day": round(leak_flow * 86.4, 1),
             "xy": [round(x, 1), round(y, 1)], "latlon": [round(lat, 6), round(lon, 6)]}
    check = {}
    if result.get("leak"):
        check["distance_error_m"] = round(float(np.hypot(result["best_guess_xy"][0] - x,
                                                         result["best_guess_xy"][1] - y)))
        check["pipe_in_top3_areas"] = any(req.pipe_id in a["pipes_to_check"]
                                          for a in result["top_areas"])
        alert_id = uuid.uuid4().hex[:8]
        ALERTS[alert_id] = {"id": alert_id, "zone": zone, "status": "open",
                            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "leak_probability": result["leak_probability"],
                            "water_lost_m3_per_day": result["water_lost_m3_per_day"],
                            "best_guess_latlon": result["best_guess_latlon"],
                            "top_areas": [{k: a[k] for k in ("area", "probability", "centre_latlon",
                                                             "radius_m", "pipes_to_check")}
                                          for a in result["top_areas"]],
                            "simulated_truth": truth}
        check["alert_id"] = alert_id
    return {"truth": truth, "prediction": result, "check": check, "readings": readings,
            "seconds": round(time.time() - t0, 2)}


@app.get("/repairs")
def repairs(status: Literal["open", "resolved", "all"] = "open"):
    items = [a for a in ALERTS.values() if status == "all" or a["status"] == status]
    return sorted(items, key=lambda a: -a["water_lost_m3_per_day"])


@app.post("/repairs/{alert_id}/resolve")
def resolve(alert_id: str):
    if alert_id not in ALERTS:
        raise HTTPException(404, "Unknown alert")
    ALERTS[alert_id]["status"] = "resolved"
    ALERTS[alert_id]["resolved"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return ALERTS[alert_id]


# ---------------------------------------------------------------- dashboard page
from fastapi.responses import FileResponse  # noqa: E402

DASHBOARD = Path(__file__).with_name("dashboard.html")


@app.get("/", include_in_schema=False)
def dashboard():
    """Open http://127.0.0.1:8000/ for the test dashboard."""
    return FileResponse(DASHBOARD)