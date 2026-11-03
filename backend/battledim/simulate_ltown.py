"""Rebuild the BattLeDIM 2019 sensor data for L-Town with the 23 official leaks.

The official 2019 SCADA files are on Zenodo. This script makes the same kind of data from the official
network (L-TOWN_v2_Real.inp, with a full year of 5-minute demands) and the official leak list
(dataset_configuration_evaluation.yalm, and _historical.yalm for 2018), so it can run where Zenodo cannot be reached.

Differences from the official generator (WNTR, pressure-driven): this uses the EPANET 2.2 toolkit,
demand-driven, and models every leak as an emitter in the middle of its pipe. Abrupt leaks switch on
at full size, incipient leaks grow linearly in diameter until their peak time, as in the original.
Readings are rounded like the official files: pressure to 0.01 m, flow to 0.01 m3/h.

Usage:  python simulate_ltown.py <folder with L-TOWN_v2_Real.inp and the .yalm files> [2019|2018]
Writes: <folder>/ltown_<year>.npz   (2018 is the "historical" year, used to tune; 2019 is the test year)
"""
import math
import os
import sys
import time

import numpy as np
from wntr.epanet import toolkit

EN_EMITTER, EN_PRESSURE, EN_HEAD, EN_ELEVATION = 3, 11, 10, 0
EN_FLOW, EN_DURATION = 8, 0
STEP = 300
CD, G = 0.75, 9.81


def read_config(path):
    leaks, sensors, flows, section = [], [], [], None
    for line in open(path, encoding="utf-8", errors="ignore"):
        s = line.strip()
        if s.endswith(":") and not s.startswith("-"):
            section = s[:-1]
            continue
        if not s.startswith("- ") or s.startswith("- #"):
            continue
        item = s[2:].strip()
        if section == "leakages":
            pipe, start, end, d, kind, peak = [x.strip() for x in item.split(",")]
            leaks.append({"pipe": pipe, "start": start, "end": end, "diameter_m": float(d), "kind": kind, "peak": peak})
        elif section == "pressure_sensors":
            sensors.append(item)
        elif section == "flow_sensors":
            flows.append(item)
    return leaks, sensors, flows


def ts(text):
    """'2019-03-03 13:10' -> seconds since 1 January of that year (the simulation starts there)."""
    import datetime as dt
    t = dt.datetime.strptime(text, "%Y-%m-%d %H:%M")
    return int((t - dt.datetime(t.year, 1, 1)).total_seconds())


def split_leak_pipes(src, dst, leaks):
    """Copy the network, putting a new node in the middle of every leak pipe."""
    want = {lk["pipe"] for lk in leaks}
    elev, coords, pipes = {}, {}, {}
    section = None
    with open(src, encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if s.startswith("["):
                section = s
                if section == "[PATTERNS]":
                    break
                continue
            if not s or s.startswith(";"):
                continue
            parts = s.split()
            if section == "[JUNCTIONS]":
                elev[parts[0]] = float(parts[1])
            elif section == "[PIPES]" and parts[0] in want:
                pipes[parts[0]] = parts
    with open(src, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.strip() == "[COORDINATES]":
                break
        for line in f:
            s = line.strip()
            if s.startswith("["):
                break
            p = s.split()
            if len(p) >= 3 and not s.startswith(";"):
                coords[p[0]] = (float(p[1]), float(p[2]))
    missing = want - set(pipes)
    if missing:
        raise SystemExit(f"Leak pipes not in the network: {sorted(missing)}")
    section = None
    with open(src, encoding="utf-8", errors="ignore") as f, open(dst, "w", encoding="utf-8") as out:
        for line in f:
            s = line.strip()
            if s.startswith("["):
                section = s
                out.write(line)
                if s == "[JUNCTIONS]":
                    for pid, p in pipes.items():
                        out.write(f" {pid}_leak\t{(elev[p[1]] + elev[p[2]]) / 2:.4f}\t0\t\t;\n")
                elif s == "[COORDINATES]":
                    for pid, p in pipes.items():
                        (x1, y1), (x2, y2) = coords[p[1]], coords[p[2]]
                        out.write(f" {pid}_leak\t{(x1 + x2) / 2:.3f}\t{(y1 + y2) / 2:.3f}\n")
                continue
            if section == "[REPORT]" and s.startswith("Status"):
                out.write(" Status\tNo\n")
                continue
            if section == "[PIPES]" and s and not s.startswith(";") and s.split()[0] in pipes:
                p = s.split()
                half = float(p[3]) / 2
                rest = "\t".join(p[4:8])
                out.write(f" {p[0]}\t{p[1]}\t{p[0]}_leak\t{half}\t{rest}\n")
                out.write(f" {p[0]}_B\t{p[0]}_leak\t{p[2]}\t{half}\t{rest}\n")
                continue
            out.write(line)
    return {pid: ((coords[p[1]][0] + coords[p[2]][0]) / 2, (coords[p[1]][1] + coords[p[2]][1]) / 2) for pid, p in pipes.items()}, coords


def emitter_coef(diameter_m):
    """Emitter coefficient in m3/h per sqrt(m) for a round hole of this diameter."""
    area = math.pi * (diameter_m / 2) ** 2
    return CD * area * math.sqrt(2 * G) * 3600


CONFIGS = {"2019": "dataset_configuration_evaluation.yalm", "2018": "dataset_configuration_historical.yalm"}


def main(folder, year="2019"):
    leaks, sensors, flow_ids = read_config(os.path.join(folder, CONFIGS[year]))
    src = os.path.join(folder, "L-TOWN_v2_Real.inp")
    inp = os.path.join(folder, f"ltown_leaks_{year}.inp")
    leak_xy, coords = split_leak_pipes(src, inp, leaks)
    for lk in leaks:
        lk["t0"], lk["t1"], lk["tp"] = ts(lk["start"]), ts(lk["end"]), ts(lk["peak"])

    en = toolkit.ENepanet(version=2.2)
    en.ENopen(inp, os.path.join(folder, "ltown.rpt"), "")
    duration = 365 * 86400 - STEP
    en.ENsettimeparam(EN_DURATION, duration)
    p_idx = [en.ENgetnodeindex(s) for s in sensors]
    l_idx = [en.ENgetlinkindex(f) for f in flow_ids]
    tank = en.ENgetnodeindex("T1")
    leak_idx = [en.ENgetnodeindex(f"{lk['pipe']}_leak") for lk in leaks]
    n = duration // STEP + 1
    P = np.full((n, len(sensors)), np.nan, dtype=np.float32)
    Q = np.full((n, len(flow_ids)), np.nan, dtype=np.float32)
    LV = np.full(n, np.nan, dtype=np.float32)
    LEAK = np.zeros((n, len(leaks)), dtype=np.float32)
    tank_elev = en.ENgetnodevalue(tank, EN_ELEVATION)

    def coef_at(lk, t):
        if t < lk["t0"] or t > lk["t1"]:
            return 0.0
        d = lk["diameter_m"]
        if lk["kind"] == "incipient" and lk["tp"] > lk["t0"] and t < lk["tp"]:
            d = d * (t - lk["t0"]) / (lk["tp"] - lk["t0"])
        return emitter_coef(d)

    en.ENopenH()
    en.ENinitH(0)
    started, last_t = time.time(), -1
    while True:
        # leaks change size between steps: set them before solving
        tnow = last_t + 1 if last_t >= 0 else 0
        for i, lk in enumerate(leaks):
            en.ENsetnodevalue(leak_idx[i], EN_EMITTER, coef_at(lk, tnow))
        t = en.ENrunH()
        if t % STEP == 0 and t != last_t:
            k = t // STEP
            if k < n:
                for j, ix in enumerate(p_idx):
                    P[k, j] = round(en.ENgetnodevalue(ix, EN_PRESSURE), 2)
                for j, ix in enumerate(l_idx):
                    Q[k, j] = round(en.ENgetlinkvalue(ix, EN_FLOW), 2)
                LV[k] = round(en.ENgetnodevalue(tank, EN_HEAD) - tank_elev, 2)
                for i, ix in enumerate(leak_idx):
                    LEAK[k, i] = en.ENgetnodevalue(ix, 9)  # demand at the leak node = leak flow, m3/h
            if k % 8640 == 0:
                print(f"  day {k // 288:3d}  ({time.time() - started:.0f} s)", flush=True)
            last_t = t
        step = en.ENnextH()
        if step <= 0:
            break
    en.ENcloseH()
    en.ENclose()
    out = os.path.join(folder, f"ltown_{year}.npz")
    np.savez_compressed(out, pressure=P, flow=Q, tank_level=LV, leak_flow=LEAK, sensors=np.array(sensors),
                        flow_ids=np.array(flow_ids), sensor_xy=np.array([coords[s] for s in sensors]),
                        leak_xy=np.array([leak_xy[lk["pipe"]] for lk in leaks]),
                        leaks=np.array([[lk["pipe"], lk["start"], lk["end"], lk["kind"], lk["peak"], str(lk["diameter_m"])] for lk in leaks]),
                        tank_diameter=15.4251)
    os.remove(inp)   # the copy with split pipes is 175 MB
    print(f"Done in {time.time() - started:.0f} s -> {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".", sys.argv[2] if len(sys.argv) > 2 else "2019")
