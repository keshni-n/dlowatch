"""
Build an illustrative EPANET water network for central Port Louis.

Real streets come from OpenStreetMap (osmnx). Every street junction becomes a
pipe junction and every street becomes a pipe, because water mains usually run
under roads. Pipe sizes, elevations (unless you give a DEM), demand and the
reservoir are ASSUMED. This is NOT real CWA pipe data.

Outputs (in --out folder):
  port_louis_demo.inp      EPANET file (open in EPANET desktop or load with WNTR)
  pipes.geojson            pipes in lat/lon for Leaflet
  nodes.geojson            junctions in lat/lon (elevation, demand, sensor flag)
  sensors.json             chosen pressure sensors + inlet flow meter
  baseline_pressure.csv    hourly pressure at each sensor, no leak
  leak_demo.json           one simulated leak: flow lost + pressure drop per sensor
  network_preview.png      quick picture coloured by pressure (for checking / deck)

Usage:
  python build_port_louis_network.py
  python build_port_louis_network.py --radius 900 --sensors 12
  python build_port_louis_network.py --dem srtm_mauritius.tif   # real elevations
  python build_port_louis_network.py --leak-pipe P_42 --leak-coeff 0.0005
"""

import argparse
import json
import math
import random
from pathlib import Path

import networkx as nx
import pandas as pd
import wntr
from pyproj import Transformer

# ---------------------------------------------------------------- settings
PORT_LOUIS_CENTRE = (-20.1619, 57.5012)   # lat, lon (around Place d'Armes)
UTM_CRS = "EPSG:32740"                    # UTM zone 40S, metres, covers Mauritius

# Assumed starting pipe diameter (m) by OSM road type. The sizing loop upsizes
# pipes that lose too much pressure.
DIAMETER_BY_ROAD = {
    "motorway": 0.30, "trunk": 0.30, "primary": 0.30,
    "secondary": 0.25, "tertiary": 0.20,
    "residential": 0.15, "unclassified": 0.15,
    "living_street": 0.10, "service": 0.10,
}
STANDARD_DIAMETERS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]
DEFAULT_DIAMETER = 0.15
HAZEN_WILLIAMS_C = 110            # older cast iron / mixed pipes
MIN_PRESSURE_M = 20               # target minimum pressure at peak hour
MAX_HEADLOSS_M_PER_KM = 10        # upsizing threshold

# 24 hourly demand multipliers: low at night, peaks morning and evening
DIURNAL = [0.45, 0.40, 0.38, 0.38, 0.45, 0.70, 1.20, 1.55, 1.45, 1.20, 1.05, 1.05,
           1.10, 1.05, 0.95, 0.95, 1.05, 1.30, 1.50, 1.40, 1.15, 0.90, 0.70, 0.55]


# ---------------------------------------------------------------- helpers
def first(value, default=None):
    """OSM tags can be a list after simplification; take the first item."""
    if isinstance(value, list):
        return value[0] if value else default
    return value if value is not None else default


def pick_diameter(highway):
    return DIAMETER_BY_ROAD.get(first(highway, ""), DEFAULT_DIAMETER)


def next_size_up(d):
    for size in STANDARD_DIAMETERS:
        if size > d + 1e-6:
            return size
    return d


# ---------------------------------------------------------------- 1. streets
def download_streets(lat, lon, radius_m):
    import osmnx as ox
    print(f"Downloading streets within {radius_m} m of {lat}, {lon} ...")
    return ox.graph_from_point((lat, lon), dist=radius_m, network_type="drive",
                               simplify=True)


def clean_street_graph(G):
    """Undirected, one edge per street segment, no loops, one connected piece."""
    U = nx.Graph()
    for n, data in G.nodes(data=True):
        U.add_node(n, lon=data["x"], lat=data["y"], elevation=data.get("elevation"))
    for u, v, data in G.edges(data=True):
        if u == v:
            continue
        length = float(data.get("length", 0) or 0)
        # keep the shortest of parallel / two-way duplicates
        if U.has_edge(u, v) and U[u][v]["length"] <= length:
            continue
        geom = data.get("geometry")
        coords = list(geom.coords) if geom is not None else None
        U.add_edge(u, v, length=length, highway=first(data.get("highway"), ""),
                   name=first(data.get("name"), ""), coords=coords)
    biggest = max(nx.connected_components(U), key=len)
    U = U.subgraph(biggest).copy()
    print(f"Street graph: {U.number_of_nodes()} junctions, {U.number_of_edges()} streets")
    return U


def add_elevations(U, dem_path=None):
    """Use a DEM raster if given, otherwise an assumed slope rising inland."""
    if dem_path:
        import rasterio
        with rasterio.open(dem_path) as src:
            to_dem = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
            pts = [to_dem.transform(d["lon"], d["lat"]) for _, d in U.nodes(data=True)]
            values = [v[0] for v in src.sample(pts)]
        for (n, _), z in zip(U.nodes(data=True), values):
            U.nodes[n]["elevation"] = max(float(z), 0.5)
        print(f"Elevations from DEM: {dem_path}")
        return "dem"

    # Assumed: Port Louis harbour is to the west/north-west, ground rises to the
    # south-east towards the hills. About 3 m per 100 m inland, 2 m at the coast.
    lons = [d["lon"] for _, d in U.nodes(data=True)]
    lats = [d["lat"] for _, d in U.nodes(data=True)]
    lon0, lat0 = min(lons), max(lats)
    for n, d in U.nodes(data=True):
        east_m = (d["lon"] - lon0) * 104_500        # metres per degree lon at -20°
        south_m = (lat0 - d["lat"]) * 110_700
        inland = 0.7 * east_m + 0.3 * south_m
        U.nodes[n]["elevation"] = round(2 + 0.03 * inland, 2)
    print("Elevations ASSUMED (slope rising inland). Use --dem for real values.")
    return "assumed"


# ---------------------------------------------------------------- 2. network
def build_model(U, total_demand_lps):
    to_utm = Transformer.from_crs("EPSG:4326", UTM_CRS, always_xy=True)
    wn = wntr.network.WaterNetworkModel()
    wn.options.hydraulic.headloss = "H-W"
    wn.options.time.duration = 24 * 3600
    wn.options.time.hydraulic_timestep = 3600
    wn.options.time.report_timestep = 3600
    wn.options.time.pattern_timestep = 3600
    wn.add_pattern("DIURNAL", DIURNAL)

    node_id = {n: f"J_{i + 1}" for i, n in enumerate(U.nodes)}

    # Demand: each junction serves half of every street touching it,
    # so demand follows street length (a stand-in for housing density).
    served_len = {n: 0.5 * sum(U[n][m]["length"] for m in U[n]) for n in U.nodes}
    total_len = sum(served_len.values()) or 1.0
    total_m3s = total_demand_lps / 1000

    for n, d in U.nodes(data=True):
        x, y = to_utm.transform(d["lon"], d["lat"])
        wn.add_junction(node_id[n],
                        base_demand=total_m3s * served_len[n] / total_len,
                        demand_pattern="DIURNAL",
                        elevation=d["elevation"],
                        coordinates=(round(x, 2), round(y, 2)))

    pipe_meta = {}
    for i, (u, v, d) in enumerate(U.edges(data=True)):
        pid = f"P_{i + 1}"
        length = max(d["length"], 1.0)
        wn.add_pipe(pid, node_id[u], node_id[v], length=round(length, 2),
                    diameter=pick_diameter(d["highway"]),
                    roughness=HAZEN_WILLIAMS_C)
        latlon = d["coords"] or [(U.nodes[u]["lon"], U.nodes[u]["lat"]),
                                 (U.nodes[v]["lon"], U.nodes[v]["lat"])]
        # orient geometry from start node to end node
        if d["coords"] and _dist(latlon[0], (U.nodes[u]["lon"], U.nodes[u]["lat"])) > \
                _dist(latlon[-1], (U.nodes[u]["lon"], U.nodes[u]["lat"])):
            latlon = latlon[::-1]
        if len(latlon) > 2:   # bends shown in the EPANET desktop app
            wn.get_link(pid).vertices = [tuple(round(c, 2) for c in to_utm.transform(*p))
                                         for p in latlon[1:-1]]
        pipe_meta[pid] = {"street": d["name"], "road_type": d["highway"],
                          "lonlat": latlon}

    # Reservoir: next to the highest junction (assumed supply from the hills)
    top = max(U.nodes, key=lambda n: U.nodes[n]["elevation"])
    tx, ty = wn.get_node(node_id[top]).coordinates
    top_elev = U.nodes[top]["elevation"]
    wn.add_reservoir("R_1", base_head=top_elev + 45, coordinates=(tx + 150, ty - 150))
    wn.add_pipe("P_INLET", "R_1", node_id[top], length=200, diameter=0.50,
                roughness=HAZEN_WILLIAMS_C)
    rlon, rlat = Transformer.from_crs(UTM_CRS, "EPSG:4326", always_xy=True) \
        .transform(tx + 150, ty - 150)
    pipe_meta["P_INLET"] = {"street": "Supply main (assumed)", "road_type": "inlet",
                            "lonlat": [(rlon, rlat),
                                       (U.nodes[top]["lon"], U.nodes[top]["lat"])]}
    return wn, node_id, pipe_meta, (rlon, rlat)


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def run_epanet(wn, prefix):
    sim = wntr.sim.EpanetSimulator(wn)
    return sim.run_sim(file_prefix=prefix)


def size_network(wn, workdir, max_rounds=8):
    """Upsize pipes with high headloss, then set reservoir head for min pressure."""
    prefix = str(workdir / "_tmp")
    for rnd in range(max_rounds):
        res = run_epanet(wn, prefix)
        flow = res.link["flowrate"].abs().max()
        upsized = 0
        for pid in wn.pipe_name_list:
            pipe = wn.get_link(pid)
            q = flow[pid]
            # Hazen-Williams headloss in m per km
            hl = 10.67 * pipe.length * q ** 1.852 / (
                pipe.roughness ** 1.852 * pipe.diameter ** 4.87) / pipe.length * 1000
            if hl > MAX_HEADLOSS_M_PER_KM and pipe.diameter < STANDARD_DIAMETERS[-1]:
                pipe.diameter = next_size_up(pipe.diameter)
                upsized += 1
        print(f"  sizing round {rnd + 1}: upsized {upsized} pipes")
        if upsized == 0:
            break

    res = run_epanet(wn, prefix)
    junctions = wn.junction_name_list
    min_p = res.node["pressure"][junctions].min().min()
    reservoir = wn.get_node("R_1")
    reservoir.head_timeseries.base_value += (MIN_PRESSURE_M - min_p)
    res = run_epanet(wn, prefix)
    p = res.node["pressure"][junctions]
    print(f"  reservoir head set to {reservoir.head_timeseries.base_value:.1f} m; "
          f"pressure range {p.min().min():.1f} to {p.max().max():.1f} m")
    return res


# ---------------------------------------------------------------- 3. sensors
def choose_sensors(wn, n_sensors, seed=7, rounds=15):
    """Spread sensors evenly: k-means on junction positions, snapped to junctions."""
    junctions = wn.junction_name_list
    coords = {j: wn.get_node(j).coordinates for j in junctions}
    k = min(n_sensors, len(junctions))
    centres = [coords[j] for j in random.Random(seed).sample(junctions, k)]
    for _ in range(rounds):
        groups = [[] for _ in range(k)]
        for j in junctions:
            i = min(range(k), key=lambda c: math.dist(coords[j], centres[c]))
            groups[i].append(coords[j])
        centres = [(sum(x for x, _ in g) / len(g), sum(y for _, y in g) / len(g))
                   if g else centres[i] for i, g in enumerate(groups)]
    chosen = []
    for c in centres:
        best = min((j for j in junctions if j not in chosen),
                   key=lambda j: math.dist(coords[j], c))
        chosen.append(best)
    return chosen


# ---------------------------------------------------------------- 4. leak demo
def leak_demo(inp_path, sensors, leak_pipe, leak_coeff, workdir, seed=11):
    """Add a leak in the middle of a pipe as an EPANET emitter and compare."""
    wn = wntr.network.WaterNetworkModel(str(inp_path))
    base = run_epanet(wn, str(workdir / "_base"))

    if leak_pipe is None:
        rng = random.Random(seed)
        leak_pipe = rng.choice([p for p in wn.pipe_name_list if p != "P_INLET"])
    wn = wntr.morph.split_pipe(wn, leak_pipe, f"{leak_pipe}_B", "LEAK_NODE")
    wn.get_node("LEAK_NODE").emitter_coefficient = leak_coeff
    leak = run_epanet(wn, str(workdir / "_leak"))

    # night hour (03:00) is when leaks show most clearly
    t_night = 3 * 3600
    p0 = base.node["pressure"].loc[t_night]
    p1 = leak.node["pressure"].loc[t_night]
    drop = (p0[sensors] - p1[sensors]).sort_values(ascending=False)
    inflow0 = base.link["flowrate"]["P_INLET"].abs()
    inflow1 = leak.link["flowrate"]["P_INLET"].abs()
    lost_m3_day = float((inflow1 - inflow0).mean() * 86400)

    return {
        "leak_pipe": leak_pipe,
        "emitter_coefficient": leak_coeff,
        "leak_flow_lps_at_3am": round(float(inflow1.loc[t_night] - inflow0.loc[t_night]) * 1000, 2),
        "water_lost_m3_per_day": round(lost_m3_day, 1),
        # small leaks change pressure by millimetres to centimetres, so report mm
        "pressure_drop_mm_at_3am": {s: round(float(v) * 1000, 2) for s, v in drop.items()},
        "closest_sensors_by_drop": list(drop.index[:3]),
    }


# ---------------------------------------------------------------- 5. exports
def export_geojson(wn, res, node_id, U, pipe_meta, reservoir_lonlat, sensors, out):
    inv = {v: k for k, v in node_id.items()}
    p_peak = res.node["pressure"].loc[8 * 3600]
    nodes = []
    for j in wn.junction_name_list:
        d = U.nodes[inv[j]]
        node = wn.get_node(j)
        nodes.append({"type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [d["lon"], d["lat"]]},
                      "properties": {"id": j, "elevation_m": node.elevation,
                                     "base_demand_lps": round(node.base_demand * 1000, 4),
                                     "pressure_8am_m": round(float(p_peak[j]), 2),
                                     "is_sensor": j in sensors}})
    nodes.append({"type": "Feature",
                  "geometry": {"type": "Point", "coordinates": list(reservoir_lonlat)},
                  "properties": {"id": "R_1", "kind": "reservoir (assumed)"}})
    pipes = []
    for pid in wn.pipe_name_list:
        pipe = wn.get_link(pid)
        meta = pipe_meta[pid]
        pipes.append({"type": "Feature",
                      "geometry": {"type": "LineString",
                                   "coordinates": [list(c) for c in meta["lonlat"]]},
                      "properties": {"id": pid, "from": pipe.start_node_name,
                                     "to": pipe.end_node_name,
                                     "length_m": pipe.length,
                                     "diameter_mm": round(pipe.diameter * 1000),
                                     "street": meta["street"],
                                     "road_type": meta["road_type"]}})
    note = "Illustrative network on real OpenStreetMap streets. Not real CWA pipes."
    for name, feats in (("nodes.geojson", nodes), ("pipes.geojson", pipes)):
        (out / name).write_text(json.dumps(
            {"type": "FeatureCollection", "note": note, "features": feats}))


def save_preview(wn, res, out, sensors):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    p = res.node["pressure"].loc[8 * 3600][wn.junction_name_list]
    ax = wntr.graphics.plot_network(wn, node_attribute=p, node_size=12,
                                    node_colorbar_label="Pressure at 8am (m)",
                                    title="Port Louis demo network (illustrative)")
    xs = [wn.get_node(s).coordinates[0] for s in sensors]
    ys = [wn.get_node(s).coordinates[1] for s in sensors]
    ax.scatter(xs, ys, s=70, facecolors="none", edgecolors="red", linewidths=1.5,
               label="Pressure sensors", zorder=5)
    ax.legend(loc="lower left")
    plt.savefig(out / "network_preview.png", dpi=150, bbox_inches="tight")
    plt.close()


# ---------------------------------------------------------------- main
def build(G, out, sensors_n=10, total_demand_lps=25, dem=None,
          leak_pipe=None, leak_coeff=0.0003):
    out.mkdir(parents=True, exist_ok=True)
    U = clean_street_graph(G)
    elev_source = add_elevations(U, dem)
    wn, node_id, pipe_meta, res_lonlat = build_model(U, total_demand_lps)
    print("Sizing pipes and reservoir ...")
    res = size_network(wn, out)

    inp = out / "port_louis_demo.inp"
    wntr.network.write_inpfile(wn, str(inp), units="LPS")
    print(f"Saved {inp}")

    sensors = choose_sensors(wn, sensors_n)
    (out / "sensors.json").write_text(json.dumps({
        "pressure_sensors": sensors, "flow_meter": "P_INLET",
        "note": "Sensor positions chosen to spread across the network (assumed)."},
        indent=2))
    res.node["pressure"][sensors].round(3).to_csv(out / "baseline_pressure.csv",
                                                  index_label="time_s")
    export_geojson(wn, res, node_id, U, pipe_meta, res_lonlat, sensors, out)
    save_preview(wn, res, out, sensors)

    print("Running leak demo ...")
    demo = leak_demo(inp, sensors, leak_pipe, leak_coeff, out)
    demo["elevation_source"] = elev_source
    (out / "leak_demo.json").write_text(json.dumps(demo, indent=2))
    print(f"  leak on {demo['leak_pipe']}: {demo['water_lost_m3_per_day']} m3/day lost, "
          f"biggest pressure drop at {demo['closest_sensors_by_drop']}")

    for f in out.glob("_*"):   # EPANET temp files
        f.unlink()
    print(f"Done. {len(wn.junction_name_list)} junctions, {len(wn.pipe_name_list)} pipes.")
    return wn


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lat", type=float, default=PORT_LOUIS_CENTRE[0])
    ap.add_argument("--lon", type=float, default=PORT_LOUIS_CENTRE[1])
    ap.add_argument("--radius", type=int, default=700, help="metres around the centre")
    ap.add_argument("--sensors", type=int, default=10)
    ap.add_argument("--demand", type=float, default=25,
                    help="average total demand for the area, litres/second")
    ap.add_argument("--dem", help="optional elevation raster (GeoTIFF), e.g. SRTM")
    ap.add_argument("--leak-pipe", help="pipe id for the leak demo (random if empty)")
    ap.add_argument("--leak-coeff", type=float, default=0.0003,
                    help="emitter coefficient for the leak (bigger = bigger leak)")
    ap.add_argument("--graphml", help="load a saved osmnx graph instead of downloading")
    ap.add_argument("--out", default="output")
    a = ap.parse_args()

    if a.graphml:
        import osmnx as ox
        G = ox.load_graphml(a.graphml)
    else:
        G = download_streets(a.lat, a.lon, a.radius)
        import osmnx as ox
        Path(a.out).mkdir(parents=True, exist_ok=True)
        ox.save_graphml(G, Path(a.out) / "port_louis_streets.graphml")  # offline reuse
    build(G, Path(a.out), a.sensors, a.demand, a.dem, a.leak_pipe, a.leak_coeff)


if __name__ == "__main__":
    main()
