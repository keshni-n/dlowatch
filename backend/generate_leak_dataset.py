"""
Generate a leak training dataset: thousands of simulated leaks per zone.

Each scenario puts a leak (an EPANET emitter) at a random junction with a random
size, runs EPANET, and records how much the sensor pressures and the zone inflow
changed compared to the no-leak run. Some scenarios have no leak, so the model
can also learn to say "no leak".

It talks to the EPANET engine directly (opened once per zone), so each scenario
takes milliseconds instead of rewriting files every time. Zones run in parallel.

Works with:
  North network:      python generate_leak_dataset.py --network output_north
  Port Louis network: python generate_leak_dataset.py --network output

Output (in the network folder):
  leak_dataset.csv     one row per scenario (features + answers)
  leak_dataset_info.json   what the columns mean and the settings used

Options:
  --per-zone 3000        scenarios per zone
  --no-leak-share 0.1    share of scenarios with no leak
  --hours 3 8            hours of the day to record (3am night, 8am peak)
  --sensor-noise 0.001   random sensor error in metres (1 mm)
  --demand-noise 0.02    random +/-2% change in total water use per scenario
  --min-size 0.1 --max-size 1.0   leak size range (emitter coefficient, L/s per sqrt(m))
"""

import argparse
import json
import math
import random
import time
from multiprocessing import Pool
from pathlib import Path

import pandas as pd
import wntr
from wntr.epanet import toolkit
from wntr.epanet.util import EN


def zone_jobs(net_dir):
    """Return [(zone, inp_path, sensors, inlet_link)] for north or Port Louis output."""
    info = json.loads((net_dir / "sensors.json").read_text())
    if "pressure_sensors_by_zone" in info:   # north network
        return [(z, str(net_dir / "zones" / f"zone_{z}.inp"), s, f"P_IN_{z}")
                for z, s in info["pressure_sensors_by_zone"].items()]
    return [("PL", str(net_dir / "port_louis_demo.inp"),     # Port Louis network
             info["pressure_sensors"], info["flow_meter"])]


def simulate_zone(job):
    zone, inp, sensors, inlet, a = job
    rng = random.Random(f"{a['seed']}-{zone}")
    wn = wntr.network.WaterNetworkModel(inp)     # for coordinates and neighbours only
    junctions = wn.junction_name_list
    neighbours = {j: [l for l in wn.get_links_for_node(j) if not l.startswith("P_IN")]
                  for j in junctions}
    coords = {j: wn.get_node(j).coordinates for j in junctions}

    en = toolkit.ENepanet()
    en.ENopen(inp, inp + ".rpt", "")
    sidx = [en.ENgetnodeindex(s) for s in sensors]
    jidx = {j: en.ENgetnodeindex(j) for j in junctions}
    lidx = en.ENgetlinkindex(inlet)
    base_demand = {i: en.ENgetnodevalue(i, EN.BASEDEMAND) for i in jidx.values()}
    record = {h * 3600 for h in a["hours"]}
    # only simulate up to the last hour we record (much faster than 24 h)
    en.ENsettimeparam(EN.DURATION, max(record))
    en.ENopenH()

    def run(leak_index=None, coeff=0.0, demand_scale=1.0):
        if demand_scale != 1.0 or run.last_scale != 1.0:
            for i, d in base_demand.items():
                en.ENsetnodevalue(i, EN.BASEDEMAND, d * demand_scale)
            run.last_scale = demand_scale
        if leak_index is not None:
            en.ENsetnodevalue(leak_index, EN.EMITTER, coeff)
        en.ENinitH(0)
        out = {}
        while True:
            t = en.ENrunH()
            if t in record and t not in out:
                p = [en.ENgetnodevalue(i, EN.PRESSURE) for i in sidx]
                q = abs(en.ENgetlinkvalue(lidx, EN.FLOW))
                # actual outflow at the leak junction (normal use + leak)
                d = en.ENgetnodevalue(leak_index, EN.DEMAND) if leak_index is not None else 0.0
                out[t] = (p, q, d)
            if en.ENnextH() <= 0:
                break
        if leak_index is not None:
            en.ENsetnodevalue(leak_index, EN.EMITTER, 0.0)
        return out
    run.last_scale = 1.0

    baseline = run()   # normal day, no leak, normal demand
    # normal water use at every junction at the recorded hours (to separate the leak)
    normal_use = {t: {} for t in record}
    en.ENinitH(0)
    while True:
        t = en.ENrunH()
        if t in record:
            for i in jidx.values():
                normal_use[t][i] = en.ENgetnodevalue(i, EN.DEMAND)
        if en.ENnextH() <= 0:
            break
    rows = []
    n_total = a["per_zone"]
    for k in range(n_total):
        has_leak = rng.random() >= a["no_leak_share"]
        scale = 1 + rng.uniform(-a["demand_noise"], a["demand_noise"])
        row = {"zone": zone, "scenario": f"{zone}_{k}", "leak": int(has_leak),
               "demand_scale": round(scale, 4)}
        if has_leak:
            j = rng.choice(junctions)
            coeff = math.exp(rng.uniform(math.log(a["min_size"]), math.log(a["max_size"])))
            res = run(jidx[j], coeff, scale)
            row.update({"leak_node": j, "leak_pipes": "|".join(neighbours[j]),
                        "leak_x": round(coords[j][0], 1), "leak_y": round(coords[j][1], 1),
                        "leak_coeff": round(coeff, 4)})
        else:
            res = run(None, 0.0, scale)
            row.update({"leak_node": "", "leak_pipes": "", "leak_x": None, "leak_y": None,
                        "leak_coeff": 0.0})
        for t in sorted(record):
            h = f"h{t // 3600:02d}"
            p0, q0, _ = baseline[t]
            p1, q1, _ = res[t]
            # features: change vs the normal no-leak day, plus sensor noise
            row[f"inflow_change_lps_{h}"] = round(q1 - q0, 4)
            for s, a0, a1 in zip(sensors, p0, p1):
                row[f"dp_mm_{h}_{s}"] = round((a0 - a1 + rng.gauss(0, a["sensor_noise"])) * 1000, 2)
        if has_leak:   # answer: leak flow at the first recorded hour
            t0 = min(record)
            leak_flow = res[t0][2] - normal_use[t0][jidx[j]] * scale
            row["leak_flow_lps"] = round(leak_flow, 4)
            row["water_lost_m3_day_est"] = round(row["leak_flow_lps"] * 86.4, 1)
        else:
            row["leak_flow_lps"] = 0.0
            row["water_lost_m3_day_est"] = 0.0
        rows.append(row)
    en.ENcloseH()
    en.ENclose()
    Path(inp + ".rpt").unlink(missing_ok=True)
    return zone, rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--network", default="output_north")
    ap.add_argument("--per-zone", type=int, default=3000)
    ap.add_argument("--no-leak-share", type=float, default=0.1)
    ap.add_argument("--hours", type=int, nargs="+", default=[3, 8])
    ap.add_argument("--sensor-noise", type=float, default=0.001)
    ap.add_argument("--demand-noise", type=float, default=0.02)
    ap.add_argument("--min-size", type=float, default=0.1)
    ap.add_argument("--max-size", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workers", type=int, default=4)
    a = vars(ap.parse_args())

    net = Path(a["network"])
    jobs = [(z, inp, s, inlet, a) for z, inp, s, inlet in zone_jobs(net)]
    print(f"Simulating {a['per_zone']} scenarios in each of {len(jobs)} zone(s) ...")
    t0 = time.time()
    rows = []
    with Pool(min(a["workers"], len(jobs))) as pool:
        for zone, zrows in pool.imap_unordered(simulate_zone, jobs):
            rows += zrows
            print(f"  zone {zone}: {len(zrows)} scenarios done ({time.time() - t0:.0f} s)")

    df = pd.DataFrame(rows)
    front = ["zone", "scenario", "leak", "leak_node", "leak_pipes", "leak_x", "leak_y",
             "leak_coeff", "leak_flow_lps", "water_lost_m3_day_est", "demand_scale"]
    df = df[front + [c for c in df.columns if c not in front]]
    df.to_csv(net / "leak_dataset.csv", index=False)

    info = {
        "rows": len(df), "leak_rows": int(df["leak"].sum()), "settings": a,
        "columns": {
            "zone": "zone the scenario ran in",
            "leak": "1 = leak, 0 = no leak (answer for detection)",
            "leak_node": "junction where the leak is (answer for location)",
            "leak_pipes": "pipes connected to that junction, separated by |",
            "leak_x / leak_y": "leak position in metres (UTM 40S), for distance error",
            "leak_coeff": "EPANET emitter coefficient used (leak size setting)",
            "leak_flow_lps": "leak flow at the first recorded hour, L/s (answer for size)",
            "water_lost_m3_day_est": "leak_flow_lps x 86.4, rough m3 per day",
            "demand_scale": "random change in total water use for this scenario",
            "inflow_change_lps_hXX": "zone inlet meter change vs normal day at hour XX",
            "dp_mm_hXX_<sensor>": "pressure drop at that sensor vs normal day, mm "
                                  "(positive = pressure fell), includes sensor noise",
        },
        "note": "Simulated with EPANET via WNTR on an illustrative network. Leaks are "
                "modelled as emitters at junctions. Not real CWA data.",
    }
    (net / "leak_dataset_info.json").write_text(json.dumps(info, indent=2))
    print(f"Saved {net / 'leak_dataset.csv'}: {len(df)} rows "
          f"({info['leak_rows']} with leaks) in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
