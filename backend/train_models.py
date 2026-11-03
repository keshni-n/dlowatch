"""
Train the leak models from leak_dataset.csv, one set per zone.

For each zone it trains:
  1. Detector   : leak or no leak?                       (Random Forest classifier)
  2. Locator    : which search area? top-3 areas          (Random Forest classifier)
                  + best-guess point (x, y)               (Random Forest regressor)
  3. Sizer      : leak flow in L/s -> m3 lost per day     (Random Forest regressor)

Search areas: nearby junctions grouped into areas (k-means). The number of
areas follows the number of sensors (--areas-per-sensor, default 3.5): with few
sensors the model can only tell big areas apart, so add sensors for smaller ones. A crew is sent to an area, and the pipes in that
area are the "check these pipes" list.

Usage:
  python train_models.py --network output_north
  python train_models.py --network output            # Port Louis
  python train_models.py --network output_north --demo   # show one prediction

Outputs (in <network>/models/):
  zone_<Z>.joblib    everything the API needs for that zone
  metrics.json       scores per zone (quote these in the pitch)

Use from your API:
  from train_models import load_models, predict
  models = load_models("output_north/models")
  predict(models, "Z3", readings)   # readings = {column name: value}
"""

import argparse
import json
import math
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import wntr
from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier, RandomForestRegressor
from sklearn.model_selection import train_test_split

from generate_leak_dataset import zone_jobs


# ---------------------------------------------------------------- search areas
def make_areas(wn, n_areas, seed=0):
    """Group junctions into n_areas search areas with k-means on their coordinates."""
    from sklearn.cluster import KMeans
    junctions = wn.junction_name_list
    xy = np.array([wn.get_node(j).coordinates for j in junctions])
    k = max(2, min(n_areas, len(junctions) // 5))
    labels = KMeans(n_clusters=k, n_init=5, random_state=seed).fit_predict(xy)
    area_of = {j: f"A{l + 1}" for j, l in zip(junctions, labels)}
    areas = {}
    for a in sorted(set(area_of.values()), key=lambda s: int(s[1:])):
        members = [j for j in junctions if area_of[j] == a]
        pts = xy[[junctions.index(j) for j in members]]
        pipes = sorted({l for j in members for l in wn.get_links_for_node(j)
                        if not l.startswith("P_IN")})
        areas[a] = {"centre": pts.mean(axis=0).round(1).tolist(),
                    "radius_m": round(float(np.max(np.hypot(*(pts - pts.mean(axis=0)).T))), 0),
                    "junctions": members, "pipes": pipes}
    return area_of, areas


# ---------------------------------------------------------------- features
def add_shape(frame, raw):
    """Add each sensor's share of the total pressure drop (per hour).
    Raw drops mix leak size and distance; the shares keep only the pattern,
    which is what points to the location."""
    frame = frame[raw].copy()
    hours = sorted({c.split("_")[2] for c in raw if c.startswith("dp_mm_")})
    for h in hours:
        cols = [c for c in raw if c.startswith(f"dp_mm_{h}_")]
        total = frame[cols].clip(lower=0).sum(axis=1) + 1e-6
        for c in cols:
            frame["share_" + c] = frame[c] / total
    return frame


# ---------------------------------------------------------------- training
def train_zone(df, zone, inp, n_sensors, areas_per_sensor, seed=0):
    z = df[df.zone == zone]
    features = [c for c in z.columns if c.startswith(("dp_mm_", "inflow_change_"))
                and z[c].notna().all()]
    wn = wntr.network.WaterNetworkModel(inp)
    area_of, areas = make_areas(wn, round(n_sensors * areas_per_sensor), seed)
    m = {"zone": zone, "features": features, "areas": areas}
    scores = {"zone": zone, "scenarios": len(z), "areas": len(areas),
              "avg_area_radius_m": round(float(np.mean([a["radius_m"] for a in areas.values()])))}

    # 1. detector
    X, y = add_shape(z, features), z["leak"]
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, random_state=seed, stratify=y)
    det = RandomForestClassifier(200, random_state=seed, n_jobs=-1, class_weight="balanced",
                                 min_samples_leaf=2)
    det.fit(Xtr, ytr)
    pred = det.predict(Xte)
    tp = int(((pred == 1) & (yte == 1)).sum())
    scores["detection_accuracy"] = round(float((pred == yte).mean()), 3)
    scores["detection_recall"] = round(tp / max(int((yte == 1).sum()), 1), 3)
    scores["false_alarm_rate"] = round(int(((pred == 1) & (yte == 0)).sum())
                                       / max(int((yte == 0).sum()), 1), 3)
    m["detector"] = det.fit(X, y)          # refit on all data for use

    # 2 + 3. locator and sizer, trained on leak scenarios only
    L = z[z.leak == 1].copy()
    L["area"] = L["leak_node"].map(area_of)
    X = add_shape(L, features)
    tr, te = train_test_split(L.index, test_size=0.25, random_state=seed)

    loc = ExtraTreesClassifier(300, random_state=seed, n_jobs=-1, min_samples_leaf=2)
    loc.fit(X.loc[tr], L.loc[tr, "area"])
    proba = loc.predict_proba(X.loc[te])
    top3 = np.asarray(loc.classes_, dtype=object)[np.argsort(-proba, axis=1)[:, :3]]
    truth = np.asarray(L.loc[te, "area"].tolist(), dtype=object)
    scores["area_top1_accuracy"] = round(float((top3[:, 0] == truth).mean()), 3)
    scores["area_top3_accuracy"] = round(float((top3 == truth[:, None]).any(axis=1).mean()), 3)

    xy = RandomForestRegressor(200, random_state=seed, n_jobs=-1, min_samples_leaf=3)
    xy.fit(X.loc[tr], L.loc[tr, ["leak_x", "leak_y"]])
    p = xy.predict(X.loc[te])
    err = np.hypot(p[:, 0] - L.loc[te, "leak_x"], p[:, 1] - L.loc[te, "leak_y"])
    scores["location_error_median_m"] = round(float(np.median(err)))
    scores["location_error_80pct_m"] = round(float(np.percentile(err, 80)))

    size = RandomForestRegressor(200, random_state=seed, n_jobs=-1, min_samples_leaf=3)
    size.fit(X.loc[tr], L.loc[tr, "leak_flow_lps"])
    ps = size.predict(X.loc[te])
    true = L.loc[te, "leak_flow_lps"].values
    scores["size_mae_lps"] = round(float(np.mean(np.abs(ps - true))), 3)
    scores["size_error_pct_median"] = round(float(np.median(np.abs(ps - true) / true) * 100), 1)

    m["locator"] = loc.fit(X, L["area"])
    m["point"] = xy.fit(X, L[["leak_x", "leak_y"]])
    m["sizer"] = size.fit(X, L["leak_flow_lps"])
    return m, scores


# ---------------------------------------------------------------- prediction
def load_models(folder):
    return {p.stem.replace("zone_", ""): joblib.load(p) for p in Path(folder).glob("zone_*.joblib")}


def predict(models, zone, readings):
    """readings: dict of feature name -> value (same names as the dataset columns)."""
    m = models[zone]
    raw = pd.DataFrame([[float(readings.get(f, 0.0)) for f in m["features"]]],
                       columns=m["features"])
    X = add_shape(raw, m["features"])
    leak_prob = float(m["detector"].predict_proba(X)[0][list(m["detector"].classes_).index(1)])
    out = {"zone": zone, "leak_probability": round(leak_prob, 3), "leak": leak_prob >= 0.5}
    if not out["leak"]:
        return out
    proba = m["locator"].predict_proba(X)[0]
    order = np.argsort(-proba)[:3]
    flow = float(m["sizer"].predict(X)[0])
    x, y = m["point"].predict(X)[0]
    out.update({
        "leak_flow_lps": round(flow, 2),
        "water_lost_m3_per_day": round(flow * 86.4, 1),
        "best_guess_xy": [round(float(x), 1), round(float(y), 1)],
        "top_areas": [{"area": m["locator"].classes_[i],
                       "probability": round(float(proba[i]), 3),
                       "centre_xy": m["areas"][m["locator"].classes_[i]]["centre"],
                       "radius_m": m["areas"][m["locator"].classes_[i]]["radius_m"],
                       "pipes_to_check": m["areas"][m["locator"].classes_[i]]["pipes"]}
                      for i in order],
    })
    return out


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--network", default="output_north")
    ap.add_argument("--areas-per-sensor", type=float, default=3.5,
                    help="search areas per pressure sensor (more sensors = smaller areas)")
    ap.add_argument("--demo", action="store_true", help="print one example prediction")
    a = ap.parse_args()

    net = Path(a.network)
    df = pd.read_csv(net / "leak_dataset.csv")
    out = net / "models"
    out.mkdir(exist_ok=True)
    all_scores = []
    for zone, inp, sensors, _ in zone_jobs(net):
        if zone not in set(df.zone):
            continue
        print(f"Training zone {zone} ...")
        m, s = train_zone(df, zone, inp, len(sensors), a.areas_per_sensor)
        joblib.dump(m, out / f"zone_{zone}.joblib", compress=3)
        all_scores.append(s)
        print(f"  detection {s['detection_accuracy']:.0%} (false alarms {s['false_alarm_rate']:.0%}) | "
              f"area top-3 {s['area_top3_accuracy']:.0%} of {s['areas']} areas | "
              f"location error {s['location_error_median_m']} m | "
              f"size error {s['size_error_pct_median']}%")

    (out / "metrics.json").write_text(json.dumps(all_scores, indent=2))
    sc = pd.DataFrame(all_scores)
    w = sc["scenarios"]
    print("\nAverage over zones:")
    for col, label in [("detection_accuracy", "Detection accuracy"),
                       ("false_alarm_rate", "False alarm rate"),
                       ("area_top1_accuracy", "Right area, 1st guess"),
                       ("area_top3_accuracy", "Right area in top 3"),
                       ("location_error_median_m", "Median location error (m)"),
                       ("avg_area_radius_m", "Typical search area radius (m)"),
                       ("size_error_pct_median", "Median leak size error (%)")]:
        v = float(np.average(sc[col], weights=w))
        print(f"  {label:32s} {v:.0%}" if col.endswith(("accuracy", "rate")) else
              f"  {label:32s} {v:.0f}")
    print(f"Saved models and metrics to {out}")

    if a.demo:
        models = load_models(out)
        row = df[df.leak == 1].sample(1, random_state=1).iloc[0]
        res = predict(models, row["zone"], row.to_dict())
        print("\nDemo: real answer", row["leak_node"], f"({row['leak_flow_lps']} L/s)")
        res["top_areas"] = [{k: v for k, v in t.items() if k != "pipes_to_check"} |
                            {"pipes_to_check": t["pipes_to_check"][:5] + ["..."]}
                            for t in res.get("top_areas", [])]
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
