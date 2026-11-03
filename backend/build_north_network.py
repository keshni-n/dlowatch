"""
Build an illustrative EPANET network for the NORTH of Mauritius, split into zones.

Covers roughly Port Louis, Pamplemousses and Riviere du Rempart (a bounding box,
not exact district borders). Real streets come from OpenStreetMap. Pipe sizes,
demand, reservoirs and (unless you pass --dem) elevations are ASSUMED.
This is NOT real CWA pipe data.

The network is split into zones (DMAs). Each zone has its own reservoir and an
inlet flow meter. Pipes between zones are closed boundary valves, like a real
DMA setup. A leak raises the inflow of ONE zone, which is the first clue the
AI uses (which zone), before finding the pipe inside that zone.

Outputs (in --out, default output_north/):
  north_mauritius.inp        whole network (EPANET)
  zones/zone_Z1.inp ...      one EPANET file per zone (fast for leak datasets)
  pipes.geojson              pipes with zone id (boundary valves marked)
  nodes.geojson              junctions with zone, elevation, demand, pressure
  zones.geojson              zone outlines + stats, for the island view
  sensors.json               pressure sensors per zone + inlet meters
  leak_demo.json             one simulated leak: which zone, flow lost, drops
  network_preview.png        quick picture coloured by zone

Usage:
  python build_north_network.py
  python build_north_network.py --zones 10 --sensors-per-zone 5
  python build_north_network.py --dem srtm_north.tif          # real elevations
  python build_north_network.py --graphml output_north/north_streets.graphml
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

from build_port_louis_network import (DIURNAL, HAZEN_WILLIAMS_C, MAX_HEADLOSS_M_PER_KM,
                                      MIN_PRESSURE_M, STANDARD_DIAMETERS, UTM_CRS,
                                      clean_street_graph, next_size_up, pick_diameter)

# west, south, east, north (lon/lat). Roughly Port Louis to Cap Malheureux
# to Poste de Flacq side. Edit with --bbox if you want a different area.
NORTH_BBOX = (57.47, -20.18, 57.80, -19.97)

# How much water use each road type carries (housing along it).
# Main roads through cane fields carry little; residential streets carry most.
DEMAND_WEIGHT = {
    "residential": 1.0, "living_street": 1.0, "unclassified": 0.5,
    "tertiary": 0.6, "secondary": 0.3, "primary": 0.2, "trunk": 0.1,
    "motorway": 0.0, "service": 0.4,
}
ZONE_COLOURS = ["#1f77b4", "#2ca02c", "#9467bd", "#8c564b", "#e377c2", "#17becf",
                "#bcbd22", "#7f7f7f", "#393b79", "#637939", "#8c6d31", "#843c39"]


# ---------------------------------------------------------------- streets
# Free Overpass servers that osmnx can download OpenStreetMap streets from.
# The main one is often busy, so try the others if it times out.
OVERPASS_SERVERS = [
    "https://overpass-api.de/api",
    "https://overpass.kumi.systems/api",
    "https://overpass.private.coffee/api",
    "https://maps.mail.ru/osm/tools/overpass/api",
]


def download_streets(bbox, server=None):
    import osmnx as ox
    servers = [server] if server else OVERPASS_SERVERS
    ox.settings.requests_timeout = 300
    last_error = None
    for url in servers:
        ox.settings.overpass_url = url
        print(f"Downloading streets in {bbox} from {url} (can take a few minutes) ...")
        try:
            return ox.graph_from_bbox(bbox, network_type="drive", simplify=True)
        except Exception as e:   # timeout, busy server, connection refused
            print(f"  failed: {type(e).__name__}. Trying the next server ...")
            last_error = e
    raise SystemExit(f"All download servers failed ({last_error}). Try again in a few "
                     "minutes, or on another network (some university/office Wi-Fi block it).")


def drop_small_roads(U):
    """Remove service roads (car parks, driveways) to keep the network manageable."""
    small = [(u, v) for u, v, d in U.edges(data=True) if d["highway"] == "service"]
    U.remove_edges_from(small)
    biggest = max(nx.connected_components(U), key=len)
    U = U.subgraph(biggest).copy()
    print(f"After removing service roads: {U.number_of_nodes()} junctions, "
          f"{U.number_of_edges()} pipes")
    return U


def add_elevations(U, dem_path=None):
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
    # Assumed: land rises southwards from the north coast, about 4 m per km.
    top_lat = max(d["lat"] for _, d in U.nodes(data=True))
    for n, d in U.nodes(data=True):
        km_south = (top_lat - d["lat"]) * 110.7
        U.nodes[n]["elevation"] = round(2 + 4 * km_south, 2)
    print("Elevations ASSUMED (rising southwards). Use --dem for real values.")
    return "assumed"


# ---------------------------------------------------------------- zones
def make_zones(U, n_zones, seed=3, rounds=20):
    """k-means on junction positions to find zone centres (towns), then give each
    junction to its nearest centre ALONG THE PIPES, so every zone is connected."""
    nodes = list(U.nodes)
    xy = {n: (U.nodes[n]["lon"] * 104.5, U.nodes[n]["lat"] * 110.7) for n in nodes}  # km
    centres = [xy[n] for n in random.Random(seed).sample(nodes, n_zones)]
    for _ in range(rounds):
        groups = [[] for _ in range(n_zones)]
        for n in nodes:
            groups[min(range(n_zones), key=lambda c: math.dist(xy[n], centres[c]))].append(xy[n])
        centres = [(sum(p[0] for p in g) / len(g), sum(p[1] for p in g) / len(g))
                   if g else centres[i] for i, g in enumerate(groups)]
    seeds = []
    for c in centres:
        seeds.append(min((n for n in nodes if n not in seeds), key=lambda n: math.dist(xy[n], c)))
    cells = nx.voronoi_cells(U, seeds, weight="length")
    zone_of = {}
    for i, s in enumerate(seeds):
        for n in cells[s]:
            zone_of[n] = f"Z{i + 1}"
    # sort zone names west to east so they read sensibly on the map
    order = sorted({z for z in zone_of.values()},
                   key=lambda z: sum(U.nodes[n]["lon"] for n in zone_of if zone_of[n] == z)
                   / sum(1 for n in zone_of if zone_of[n] == z))
    rename = {old: f"Z{i + 1}" for i, old in enumerate(order)}
    zone_of = {n: rename[z] for n, z in zone_of.items()}
    for z in sorted(set(zone_of.values()), key=lambda s: int(s[1:])):
        print(f"  zone {z}: {sum(1 for v in zone_of.values() if v == z)} junctions")
    return zone_of


# ---------------------------------------------------------------- model
class Plan:
    """Everything needed to (re)build a model: ids, sizes, demands, heads."""

    def __init__(self, U, zone_of, total_demand_lps):
        self.U, self.zone_of = U, zone_of
        self.to_utm = Transformer.from_crs("EPSG:4326", UTM_CRS, always_xy=True)
        self.to_ll = Transformer.from_crs(UTM_CRS, "EPSG:4326", always_xy=True)
        self.node_id = {n: f"J_{i + 1}" for i, n in enumerate(U.nodes)}
        self.zones = sorted(set(zone_of.values()), key=lambda s: int(s[1:]))

        self.pipes = {}   # pid -> dict(u, v, length, diameter, zone or None, street...)
        for i, (u, v, d) in enumerate(U.edges(data=True)):
            zu, zv = zone_of[u], zone_of[v]
            lonlat = d["coords"] or [(U.nodes[u]["lon"], U.nodes[u]["lat"]),
                                     (U.nodes[v]["lon"], U.nodes[v]["lat"])]
            if d["coords"] and math.dist(lonlat[0], (U.nodes[u]["lon"], U.nodes[u]["lat"])) > \
                    math.dist(lonlat[-1], (U.nodes[u]["lon"], U.nodes[u]["lat"])):
                lonlat = lonlat[::-1]
            self.pipes[f"P_{i + 1}"] = dict(
                u=u, v=v, length=max(d["length"], 1.0), diameter=pick_diameter(d["highway"]),
                zone=zu if zu == zv else None, street=d["name"], road_type=d["highway"],
                lonlat=lonlat)

        # demand by weighted street length, only along pipes inside a zone
        served = {n: 0.0 for n in U.nodes}
        for p in self.pipes.values():
            if p["zone"] is None:
                continue
            w = DEMAND_WEIGHT.get(p["road_type"], 0.5) * p["length"] / 2
            served[p["u"]] += w
            served[p["v"]] += w
        total = sum(served.values()) or 1.0
        self.demand = {n: total_demand_lps / 1000 * served[n] / total for n in U.nodes}

        # one reservoir per zone, on the high side of the zone's main built-up
        # area (highest junction among the half of the zone nearest its centre),
        # so it doesn't end up at the far end of a long country road
        self.res = {}
        for z in self.zones:
            members = [n for n in U.nodes if zone_of[n] == z]
            cx = sum(U.nodes[n]["lon"] for n in members) / len(members)
            cy = sum(U.nodes[n]["lat"] for n in members) / len(members)
            near = sorted(members, key=lambda n: math.dist(
                (U.nodes[n]["lon"], U.nodes[n]["lat"]), (cx, cy)))[: max(1, len(members) // 2)]
            top = max(near, key=lambda n: U.nodes[n]["elevation"])
            x, y = self.xy(top)
            self.res[z] = dict(top=top, head=U.nodes[top]["elevation"] + 45,
                               xy=(x + 150, y - 150), inlet_diameter=0.40)

    def xy(self, n):
        x, y = self.to_utm.transform(self.U.nodes[n]["lon"], self.U.nodes[n]["lat"])
        return round(x, 2), round(y, 2)

    def build(self, zones=None):
        """Build a WNTR model for all zones (boundary valves closed) or a subset."""
        zones = set(zones or self.zones)
        U = self.U
        wn = wntr.network.WaterNetworkModel()
        wn.options.hydraulic.headloss = "H-W"
        for k in ("duration",):
            setattr(wn.options.time, k, 24 * 3600)
        wn.options.time.hydraulic_timestep = 3600
        wn.options.time.report_timestep = 3600
        wn.options.time.pattern_timestep = 3600
        wn.add_pattern("DIURNAL", DIURNAL)
        for n in U.nodes:
            if self.zone_of[n] in zones:
                wn.add_junction(self.node_id[n], base_demand=self.demand[n],
                                demand_pattern="DIURNAL", elevation=U.nodes[n]["elevation"],
                                coordinates=self.xy(n))
        for pid, p in self.pipes.items():
            inside = p["zone"] in zones
            boundary = p["zone"] is None and len(zones) == len(self.zones)
            if not (inside or boundary):
                continue
            wn.add_pipe(pid, self.node_id[p["u"]], self.node_id[p["v"]],
                        length=round(p["length"], 2), diameter=p["diameter"],
                        roughness=HAZEN_WILLIAMS_C,
                        initial_status="CLOSED" if boundary else "OPEN")
        for z in zones:
            r = self.res[z]
            wn.add_reservoir(f"R_{z}", base_head=r["head"], coordinates=r["xy"])
            wn.add_pipe(f"P_IN_{z}", f"R_{z}", self.node_id[r["top"]], length=200,
                        diameter=r["inlet_diameter"], roughness=HAZEN_WILLIAMS_C)
        return wn


def run_epanet(wn, prefix):
    return wntr.sim.EpanetSimulator(wn).run_sim(file_prefix=prefix)


def size_plan(plan, workdir, max_rounds=10):
    """Upsize pipes that lose too much pressure, then set each zone's reservoir
    so the lowest pressure in the zone is MIN_PRESSURE_M."""
    prefix = str(workdir / "_tmp")
    for rnd in range(max_rounds):
        wn = plan.build()
        res = run_epanet(wn, prefix)
        q = res.link["flowrate"].abs().max()
        upsized = 0
        for pid, p in plan.pipes.items():
            if p["zone"] is None:
                continue
            hl = 10.67 * q[pid] ** 1.852 / (HAZEN_WILLIAMS_C ** 1.852 * p["diameter"] ** 4.87) * 1000
            if hl > MAX_HEADLOSS_M_PER_KM and p["diameter"] < STANDARD_DIAMETERS[-1]:
                p["diameter"] = next_size_up(p["diameter"])
                upsized += 1
        for z, r in plan.res.items():
            qin = q[f"P_IN_{z}"]
            while (10.67 * qin ** 1.852 / (HAZEN_WILLIAMS_C ** 1.852 * r["inlet_diameter"] ** 4.87)
                   * 1000 > MAX_HEADLOSS_M_PER_KM and r["inlet_diameter"] < 1.0):
                r["inlet_diameter"] = round(r["inlet_diameter"] + 0.1, 2)
        print(f"  sizing round {rnd + 1}: upsized {upsized} pipes")
        if upsized == 0:
            break

    for _ in range(2):   # second pass fixes small knock-on changes
        wn = plan.build()
        res = run_epanet(wn, prefix)
        p = res.node["pressure"]
        for z in plan.zones:
            ids = [plan.node_id[n] for n in plan.U.nodes if plan.zone_of[n] == z]
            plan.res[z]["head"] += float(MIN_PRESSURE_M - p[ids].min().min())
    wn = plan.build()
    res = run_epanet(wn, prefix)
    pj = res.node["pressure"][wn.junction_name_list]
    print(f"  pressure range {pj.min().min():.1f} to {pj.max().max():.1f} m")
    return wn, res


# ---------------------------------------------------------------- sensors
def choose_sensors(plan, wn, per_zone, seed=7):
    out = {}
    for z in plan.zones:
        ids = [plan.node_id[n] for n in plan.U.nodes if plan.zone_of[n] == z]
        coords = {j: wn.get_node(j).coordinates for j in ids}
        k = min(per_zone, len(ids))
        centres = [coords[j] for j in random.Random(seed).sample(ids, k)]
        for _ in range(15):
            groups = [[] for _ in range(k)]
            for j in ids:
                groups[min(range(k), key=lambda c: math.dist(coords[j], centres[c]))].append(coords[j])
            centres = [(sum(a for a, _ in g) / len(g), sum(b for _, b in g) / len(g))
                       if g else centres[i] for i, g in enumerate(groups)]
        chosen = []
        for c in centres:
            chosen.append(min((j for j in ids if j not in chosen),
                              key=lambda j: math.dist(coords[j], c)))
        out[z] = chosen
    return out


# ---------------------------------------------------------------- leak demo
def leak_demo(inp, plan, sensors, leak_pipe, leak_coeff, workdir, seed=11):
    wn = wntr.network.WaterNetworkModel(str(inp))
    base = run_epanet(wn, str(workdir / "_base"))
    if leak_pipe is None:
        leak_pipe = random.Random(seed).choice(
            [pid for pid, p in plan.pipes.items() if p["zone"] is not None])
    zone = plan.pipes[leak_pipe]["zone"]
    wn = wntr.morph.split_pipe(wn, leak_pipe, f"{leak_pipe}_B", "LEAK_NODE")
    wn.get_node("LEAK_NODE").emitter_coefficient = leak_coeff
    leak = run_epanet(wn, str(workdir / "_leak"))

    t = 3 * 3600   # 3am, when leaks show most clearly
    inflow_change = {}
    for z in plan.zones:
        f0 = base.link["flowrate"][f"P_IN_{z}"].abs()
        f1 = leak.link["flowrate"][f"P_IN_{z}"].abs()
        inflow_change[z] = round(float(f1.loc[t] - f0.loc[t]) * 1000, 3)
    lost = float((leak.link["flowrate"][f"P_IN_{zone}"].abs()
                  - base.link["flowrate"][f"P_IN_{zone}"].abs()).mean() * 86400)
    s = sensors[zone]
    drop = (base.node["pressure"].loc[t][s] - leak.node["pressure"].loc[t][s]).sort_values(
        ascending=False)
    return {
        "leak_pipe": leak_pipe, "leak_zone": zone, "emitter_coefficient": leak_coeff,
        "inflow_change_lps_at_3am_by_zone": inflow_change,
        "water_lost_m3_per_day": round(lost, 1),
        "pressure_drop_mm_at_3am_in_leak_zone": {k: round(float(v) * 1000, 2)
                                                 for k, v in drop.items()},
        "closest_sensors_by_drop": list(drop.index[:3]),
    }


# ---------------------------------------------------------------- exports
def export(plan, wn, res, sensors, out, elev_source):
    from shapely.geometry import MultiPoint, mapping
    import shapely

    p8 = res.node["pressure"].loc[8 * 3600]
    all_sensors = {j for v in sensors.values() for j in v}
    inv = {v: k for k, v in plan.node_id.items()}
    note = "Illustrative network on real OpenStreetMap streets. Not real CWA pipes."

    nodes = []
    for j in wn.junction_name_list:
        n = inv[j]
        d = plan.U.nodes[n]
        nodes.append({"type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [d["lon"], d["lat"]]},
                      "properties": {"id": j, "zone": plan.zone_of[n],
                                     "elevation_m": d["elevation"],
                                     "base_demand_lps": round(plan.demand[n] * 1000, 4),
                                     "pressure_8am_m": round(float(p8[j]), 2),
                                     "is_sensor": j in all_sensors}})
    for z, r in plan.res.items():
        lon, lat = plan.to_ll.transform(*r["xy"])
        nodes.append({"type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [lon, lat]},
                      "properties": {"id": f"R_{z}", "zone": z, "kind": "reservoir (assumed)",
                                     "head_m": round(r["head"], 1)}})

    pipes = []
    for pid, p in plan.pipes.items():
        pipes.append({"type": "Feature",
                      "geometry": {"type": "LineString",
                                   "coordinates": [[round(a, 6), round(b, 6)] for a, b in p["lonlat"]]},
                      "properties": {"id": pid, "zone": p["zone"] or "boundary",
                                     "status": "closed valve" if p["zone"] is None else "open",
                                     "diameter_mm": round(p["diameter"] * 1000),
                                     "length_m": round(p["length"], 1),
                                     "street": p["street"], "road_type": p["road_type"]}})
    for z, r in plan.res.items():
        lon, lat = plan.to_ll.transform(*r["xy"])
        t = plan.U.nodes[r["top"]]
        pipes.append({"type": "Feature",
                      "geometry": {"type": "LineString", "coordinates": [[lon, lat], [t["lon"], t["lat"]]]},
                      "properties": {"id": f"P_IN_{z}", "zone": z, "status": "inlet meter",
                                     "diameter_mm": round(r["inlet_diameter"] * 1000),
                                     "length_m": 200, "street": "Supply main (assumed)",
                                     "road_type": "inlet"}})

    zones, stats = [], {}
    for i, z in enumerate(plan.zones):
        members = [n for n in plan.U.nodes if plan.zone_of[n] == z]
        pts = MultiPoint([(plan.U.nodes[n]["lon"], plan.U.nodes[n]["lat"]) for n in members])
        hull = shapely.concave_hull(pts, ratio=0.15).buffer(0.001)
        km = sum(p["length"] for p in plan.pipes.values() if p["zone"] == z) / 1000
        stats[z] = {"junctions": len(members), "pipe_km": round(km, 1),
                    "avg_demand_lps": round(sum(plan.demand[n] for n in members) * 1000, 1),
                    "inlet_meter": f"P_IN_{z}", "reservoir": f"R_{z}",
                    "sensors": sensors[z], "colour": ZONE_COLOURS[i % len(ZONE_COLOURS)]}
        zones.append({"type": "Feature", "geometry": mapping(hull),
                      "properties": {"zone": z, **stats[z]}})

    for name, feats in (("nodes.geojson", nodes), ("pipes.geojson", pipes),
                        ("zones.geojson", zones)):
        (out / name).write_text(json.dumps({"type": "FeatureCollection", "note": note,
                                            "features": feats}))
    (out / "sensors.json").write_text(json.dumps(
        {"pressure_sensors_by_zone": sensors,
         "inlet_meters": {z: f"P_IN_{z}" for z in plan.zones},
         "elevation_source": elev_source, "zones": stats}, indent=2))


def save_preview(plan, wn, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 9))
    for i, z in enumerate(plan.zones):
        colour = ZONE_COLOURS[i % len(ZONE_COLOURS)]
        for pid, p in plan.pipes.items():
            if p["zone"] == z:
                a, b = wn.get_node(plan.node_id[p["u"]]).coordinates, \
                    wn.get_node(plan.node_id[p["v"]]).coordinates
                ax.plot([a[0], b[0]], [a[1], b[1]], color=colour, lw=0.4 + p["diameter"] * 4)
        x, y = plan.res[z]["xy"]
        ax.scatter([x], [y], marker="s", s=60, color=colour, edgecolor="black", zorder=5)
        ax.annotate(z, (x, y), xytext=(6, 6), textcoords="offset points", fontsize=11,
                    weight="bold", color=colour, zorder=10,
                    bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=colour, alpha=0.9))
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.set_title("North Mauritius demo network by zone (illustrative, not real CWA pipes)")
    plt.savefig(out / "network_preview.png", dpi=150, bbox_inches="tight")
    plt.close()


# ---------------------------------------------------------------- main
def build(G, out, n_zones=8, per_zone=4, demand_lps=400, dem=None,
          leak_pipe=None, leak_coeff=0.0003):
    out.mkdir(parents=True, exist_ok=True)
    (out / "zones").mkdir(exist_ok=True)
    U = drop_small_roads(clean_street_graph(G))
    elev_source = add_elevations(U, dem)
    print(f"Splitting into {n_zones} zones ...")
    zone_of = make_zones(U, n_zones)
    plan = Plan(U, zone_of, demand_lps)
    print("Sizing pipes and reservoirs ...")
    wn, res = size_plan(plan, out)

    inp = out / "north_mauritius.inp"
    wntr.network.write_inpfile(wn, str(inp), units="LPS")
    for z in plan.zones:
        wntr.network.write_inpfile(plan.build([z]), str(out / "zones" / f"zone_{z}.inp"),
                                   units="LPS")
    print(f"Saved {inp} and {len(plan.zones)} zone files")

    sensors = choose_sensors(plan, wn, per_zone)
    export(plan, wn, res, sensors, out, elev_source)
    save_preview(plan, wn, out)

    print("Running leak demo ...")
    demo = leak_demo(inp, plan, sensors, leak_pipe, leak_coeff, out)
    (out / "leak_demo.json").write_text(json.dumps(demo, indent=2))
    print(f"  leak on {demo['leak_pipe']} in zone {demo['leak_zone']}: "
          f"{demo['water_lost_m3_per_day']} m3/day lost")
    for f in out.glob("_*"):
        f.unlink()
    print(f"Done. {len(wn.junction_name_list)} junctions, {len(wn.pipe_name_list)} pipes, "
          f"{len(plan.zones)} zones.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bbox", type=float, nargs=4, default=NORTH_BBOX,
                    metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    ap.add_argument("--zones", type=int, default=8)
    ap.add_argument("--sensors-per-zone", type=int, default=4)
    ap.add_argument("--demand", type=float, default=400,
                    help="average total demand for the region, L/s (assumed)")
    ap.add_argument("--dem", help="elevation GeoTIFF (e.g. SRTM 30 m) for real elevations")
    ap.add_argument("--leak-pipe")
    ap.add_argument("--leak-coeff", type=float, default=0.0003)
    ap.add_argument("--graphml", help="load saved streets instead of downloading")
    ap.add_argument("--server", help="one Overpass server URL to use (default: try several)")
    ap.add_argument("--out", default="output_north")
    a = ap.parse_args()

    import osmnx as ox
    out = Path(a.out)
    if a.graphml:
        G = ox.load_graphml(a.graphml)
    else:
        G = download_streets(tuple(a.bbox), a.server)
        out.mkdir(parents=True, exist_ok=True)
        ox.save_graphml(G, out / "north_streets.graphml")
    build(G, out, a.zones, a.sensors_per_zone, a.demand, a.dem, a.leak_pipe, a.leak_coeff)


if __name__ == "__main__":
    main()