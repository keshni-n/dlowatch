"""Train a leak locator for L-Town and test it on the 2019 leaks.

How it is kept fair (this is how BattLeDIM is meant to be used):
  - Leaks are simulated on the utility's own model, L-TOWN_v2_Model.inp, not on the "real" network that
    made the test data. The two differ (demands, valve sizes, roughness), like a real utility's model.
  - Real night-to-night noise comes from 2018 only: for each training leak, the pressure drops it causes are
    added to the leftover wobble of a real 2018 night (all 33 sensors together, so their link is kept).
    Nights shortly after a 2018 leak started are skipped.
  - 2019 is only used once, at the end, to test.

Features: drop in night pressure at each of the 33 sensors (mm), each sensor's share of the total drop,
and the rise in night inflow (m3/h). Same as the app's zone models.
Models: an area classifier (k-means areas of junctions) and a best-guess point regressor (x, y).

Usage:  python battledim/train_ltown.py <folder with L-TOWN_v2_Model.inp, ltown_2018.npz, ltown_2019.npz>
Writes: <folder>/ltown_locator.joblib, evaluation/battledim_locator.json
"""
import datetime as dt
import json
import math
import os
import sys
import time

import joblib
import numpy as np
import wntr
from sklearn.cluster import KMeans
from sklearn.ensemble import ExtraTreesClassifier, RandomForestRegressor
from wntr.epanet import toolkit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import evaluate_ltown as ev  # noqa: E402
import scoring  # noqa: E402

EN_BASEDEMAND, EN_EMITTER, EN_PRESSURE, EN_TANKLEVEL = 1, 3, 11, 8
EN_FLOW, EN_DURATION, EN_PATTERNSTART = 8, 0, 4
SEED = 7
N_TRAIN = 12000
N_AREAS = 30
COEF_RANGE = (0.4, 6.0)   # emitter coefficient, m3/h per sqrt(m): about 7 mm to 25 mm holes (BattLeDIM leaks are 9 to 23 mm)


def same_weekday_residuals(D, weeks=4):
    """Night pressure drop (mm) and inflow rise (m3/h) of every night vs the same weekday over 4 weeks."""
    out = []
    for k in range(7 * weeks, D["days"]):
        bd = [k - 7 * w for w in range(1, weeks + 1)]
        dp = (np.median(D["night_p"][bd], axis=0) - D["night_p"][k]) * 1000
        dq = D["night"][k] - float(np.median(D["night"][bd]))
        out.append((k, dp, dq))
    return out


def noise_bank_2018(folder):
    """Real wobble vectors from 2018 nights that are not within 35 days after a 2018 leak started."""
    D = ev.load(folder, "2018")
    d0 = dt.datetime(2018, 1, 1)
    starts = [(dt.datetime.strptime(lk["start"], "%Y-%m-%d %H:%M") - d0).days for lk in D["leaks"]]
    bank = [(dp, dq) for k, dp, dq in same_weekday_residuals(D) if not any(0 <= k - s <= 35 for s in starts)]
    return np.array([b[0] for b in bank]), np.array([b[1] for b in bank]), D["sensors"]


def simulate(model_inp, sensors, n, rng):
    """Leak effect on night pressures at the 33 sensors, on the utility model. One snapshot per scenario."""
    wn = wntr.network.WaterNetworkModel(model_inp)
    junctions = [j for j in wn.junction_name_list]
    xy = {j: wn.get_node(j).coordinates for j in junctions}
    en = toolkit.ENepanet(version=2.2)
    en.ENopen(model_inp, model_inp + ".rpt", "")
    en.ENsettimeparam(EN_DURATION, 0)
    sidx = [en.ENgetnodeindex(s) for s in sensors]
    jidx = [en.ENgetnodeindex(j) for j in junctions]
    tank = en.ENgetnodeindex("T1")
    inlets = [en.ENgetlinkindex("p227"), en.ENgetlinkindex("p235")]
    en.ENopenH()

    def solve(leak=None, coef=0.0):
        if leak is not None:
            en.ENsetnodevalue(leak, EN_EMITTER, coef)
        en.ENinitH(0)
        en.ENrunH()
        p = np.array([en.ENgetnodevalue(i, EN_PRESSURE) for i in sidx])
        q = sum(en.ENgetlinkvalue(i, EN_FLOW) for i in inlets)
        if leak is not None:
            en.ENsetnodevalue(leak, EN_EMITTER, 0.0)
        return p, q

    rows = []
    t0 = time.time()
    for s in range(n):
        day = int(rng.integers(0, 7))
        minute = int(rng.integers(120, 240))                    # 02:00 to 04:00
        en.ENsettimeparam(EN_PATTERNSTART, (day * 1440 + minute) * 60)
        en.ENsetnodevalue(tank, EN_TANKLEVEL, float(rng.uniform(2.4, 3.9)))
        p0, q0 = solve()
        j = int(rng.integers(0, len(junctions)))
        coef = float(math.exp(rng.uniform(*np.log(COEF_RANGE))))
        p1, q1 = solve(jidx[j], coef)
        rows.append({"dp": (p0 - p1) * 1000, "dq": q1 - q0, "node": junctions[j], "x": xy[junctions[j]][0],
                     "y": xy[junctions[j]][1], "coef": coef})
        if s % 2000 == 0:
            print(f"  {s}/{n} leaks simulated ({time.time() - t0:.0f} s)", flush=True)
    en.ENcloseH()
    en.ENclose()
    for f in (model_inp + ".rpt",):
        if os.path.exists(f):
            os.remove(f)
    return rows, wn, junctions, xy


def features(dp, dq):
    dp = np.atleast_2d(dp)
    total = np.clip(dp, 0, None).sum(axis=1, keepdims=True) + 1e-6
    return np.hstack([dp, dp / total, np.atleast_1d(dq).reshape(-1, 1)])


def make_areas(junctions, xy, k, wn):
    pts = np.array([xy[j] for j in junctions])
    lab = KMeans(n_clusters=k, n_init=5, random_state=SEED).fit_predict(pts)
    areas = {}
    for a in range(k):
        members = [j for j, l in zip(junctions, lab) if l == a]
        mp = pts[lab == a]
        c = mp.mean(axis=0)
        areas[f"A{a + 1}"] = {"centre": c.round(1).tolist(), "radius_m": round(float(np.max(np.hypot(*(mp - c).T)))),
                              "junctions": members,
                              "pipes": sorted({l for j in members for l in wn.get_links_for_node(j)})}
    return {j: f"A{l + 1}" for j, l in zip(junctions, lab)}, areas


def area_of_point(areas, x, y):
    return min(areas, key=lambda a: math.hypot(areas[a]["centre"][0] - x, areas[a]["centre"][1] - y))


def test_year(folder, year, loc, pt, areas, sensors):
    """Locate each leak of that year on the night its alert opened (wl-1.3), and 7 days after it started."""
    rs = scoring.get("wl-1.3")
    D = ev.load(folder, year)
    res, _ = ev.evaluate(D, rs, year, ev.COMMON_START)
    d0 = dt.datetime(int(year), 1, 1)
    sxy, lxy = D["sxy"], D["lxy"]
    resid = {k: (dp_, dq_) for k, dp_, dq_ in same_weekday_residuals(D)}
    tests = []
    for i, row in enumerate(res["leaks"]):
        if not row["testable"]:
            continue
        t0 = (dt.datetime.strptime(D["leaks"][i]["start"], "%Y-%m-%d %H:%M") - d0).total_seconds() / 86400
        truth = lxy[i]
        cases = {"week_after": int(math.ceil(t0)) + 7}
        if row["result"] == "found":
            cases["alert_night"] = int(round(t0 + row["days_to_find"] - 4 / 24))
        for case, k in cases.items():
            if k not in resid:
                continue
            dp_, dq_ = resid[k]
            x = features(dp_, dq_)
            pxy = pt.predict(x)[0]
            pa = loc.predict_proba(x)[0]
            ta = area_of_point(areas, *truth)
            by_class = list(loc.classes_[np.argsort(-pa)[:3]])
            by_point = sorted(areas, key=lambda a: math.hypot(areas[a]["centre"][0] - pxy[0], areas[a]["centre"][1] - pxy[1]))[:3]
            s = int(np.argmax(dp_))
            tests.append({"pipe": str(row["pipe"]), "kind": str(row["kind"]), "case": case,
                          "model_error_m": round(float(np.hypot(*(pxy - truth)))),
                          "area_top3_classifier": ta in by_class, "area_top3_near_point": ta in by_point,
                          "rules_sensor": str(sensors[s]), "rules_error_m": round(float(np.hypot(*(sxy[s] - truth))))})
    return tests


def summarise(tests):
    med = lambda xs: round(float(np.median(xs))) if xs else None  # noqa: E731
    out = {}
    for case in ("alert_night", "week_after"):
        T = [t for t in tests if t["case"] == case]
        out[case] = {"leaks": len(T), "model_median_m": med([t["model_error_m"] for t in T]),
                     "rules_median_m": med([t["rules_error_m"] for t in T]),
                     "model_within_500m": sum(t["model_error_m"] <= 500 for t in T),
                     "rules_within_500m": sum(t["rules_error_m"] <= 500 for t in T),
                     "area_top3_classifier": sum(t["area_top3_classifier"] for t in T),
                     "area_top3_near_point": sum(t["area_top3_near_point"] for t in T)}
    return out


def main(folder):
    rng = np.random.default_rng(SEED)
    print("Noise from 2018 ...")
    bank_dp, bank_dq, sensors = noise_bank_2018(folder)
    print(f"  {len(bank_dp)} quiet 2018 nights")
    print("Simulating leaks on the utility model ...")
    rows, wn, junctions, xy = simulate(os.path.join(folder, "L-TOWN_v2_Model.inp"), sensors, N_TRAIN, rng)
    pick = rng.integers(0, len(bank_dp), len(rows))
    dp = np.array([r["dp"] for r in rows]) + bank_dp[pick]
    dq = np.array([r["dq"] for r in rows]) + bank_dq[pick]
    X = features(dp, dq)
    area_of, areas = make_areas(junctions, xy, N_AREAS, wn)
    ya = np.array([area_of[r["node"]] for r in rows])
    yxy = np.array([[r["x"], r["y"]] for r in rows])
    n_tr = int(len(rows) * 0.8)
    loc = ExtraTreesClassifier(300, random_state=SEED, n_jobs=-1, min_samples_leaf=2).fit(X[:n_tr], ya[:n_tr])
    pt = RandomForestRegressor(200, random_state=SEED, n_jobs=-1, min_samples_leaf=3).fit(X[:n_tr], yxy[:n_tr])
    pr = loc.predict_proba(X[n_tr:])
    top3 = loc.classes_[np.argsort(-pr, axis=1)[:, :3]]
    err = np.hypot(*(pt.predict(X[n_tr:]) - yxy[n_tr:]).T)
    holdout = {"scenarios": len(rows) - n_tr, "area_top1": round(float((top3[:, 0] == ya[n_tr:]).mean()), 3),
               "area_top3": round(float((top3 == ya[n_tr:, None]).any(axis=1).mean()), 3),
               "median_error_m": round(float(np.median(err)))}
    print("  simulated hold-out:", holdout)
    loc.fit(X, ya)
    pt.fit(X, yxy)
    joblib.dump({"locator": loc, "point": pt, "areas": areas, "sensors": sensors}, os.path.join(folder, "ltown_locator.joblib"), compress=3)

    tests = {y: test_year(folder, y, loc, pt, areas, sensors) for y in ("2018", "2019")}
    summary = {y: summarise(t) for y, t in tests.items()}
    out = {"trained_on": "L-TOWN_v2_Model.inp (the utility's model) + 2018 night noise", "tested_on": "2019",
           "training_leaks": len(rows), "areas": N_AREAS,
           "avg_area_radius_m": round(float(np.mean([a["radius_m"] for a in areas.values()]))),
           "simulated_holdout": holdout, "validation_2018": summary["2018"], "test_2019": summary["2019"],
           "cases": {y: t for y, t in tests.items()}}
    path = os.path.join(os.path.dirname(HERE), "evaluation", "battledim_locator.json")
    json.dump(out, open(path, "w"), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
