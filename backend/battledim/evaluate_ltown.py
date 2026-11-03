"""Score every night of an L-Town year with the DloWatch rules and compare with the official leaks.

Rules only: the anomaly model was trained on the Port Louis network, so it is not used here.
Signals, computed like the app does:
  NF-01  night flow (02:00 to 04:00) vs the usual night, persistence rule applied
  NF-02  night flow high several nights in a row
  WB-01  last 24 h of water vs the usual day
  PR-01  biggest night pressure drop at any of the 33 pressure sensors, in mm
Then scoring.score() with the ruleset, exactly as in the app.

Two optional ruleset settings (added in wl-1.3, both need 4 weeks of history):
  "baseline": {"mode": "same_weekday", "weeks": 4}   the usual night = the same weekday over the last 4 weeks
  "pressure_check": {"spread_k": 3, "history_nights": 28}
       a sensor's drop only counts if it is bigger than 3 times that sensor's usual night-to-night wobble

Usage:
  python battledim/evaluate_ltown.py <folder> 2019 [ruleset]     score one year, write evaluation/battledim_<year>.json
  python battledim/evaluate_ltown.py <folder> tune               try settings on 2018 (the tuning year) only
"""
import copy
import datetime as dt
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import scoring  # noqa: E402

STEP, PER_DAY = 300, 288
MATCH_DAYS = 30       # an alert counts for a leak if it opens within 30 days of the leak starting
CONFIGS = {"2019": "dataset_configuration_evaluation.yalm", "2018": "dataset_configuration_historical.yalm"}


def load(folder, year):
    d = np.load(os.path.join(folder, f"ltown_{year}.npz"))
    flow_ids = list(d["flow_ids"])
    flow, level = d["flow"].astype(float), d["tank_level"].astype(float)
    # water used by the town = water in from the two reservoirs minus water stored in the tank
    q_in = flow[:, flow_ids.index("p227")] + flow[:, flow_ids.index("p235")]
    area = np.pi * (float(d["tank_diameter"]) / 2) ** 2
    used = q_in - area * np.gradient(level) * (3600 / STEP)
    days = len(used) // PER_DAY
    used = used[: days * PER_DAY].reshape(days, PER_DAY)
    pres = d["pressure"].astype(float)[: days * PER_DAY].reshape(days, PER_DAY, -1)
    return {"days": days, "night": used[:, 24:48].mean(axis=1), "day_m3": used.sum(axis=1) * STEP / 3600,
            "night_p": pres[:, 24:48, :].mean(axis=1), "sensors": list(d["sensors"]), "sxy": d["sensor_xy"],
            "lxy": d["leak_xy"], "leak_flow": d["leak_flow"].astype(float),
            "leaks": [dict(zip(["pipe", "start", "end", "kind", "peak", "diameter_m"], r)) for r in d["leaks"]]}


def base_days(k, rs):
    b = rs.get("baseline") or {}
    if b.get("mode") == "same_weekday":
        return [k - 7 * w for w in range(1, b.get("weeks", 4) + 1)]
    return list(range(k - 12, k - 5))            # the 7 nights 12 to 6 days before, like the app


def first_day(rs):
    b = rs.get("baseline") or {}
    pc = rs.get("pressure_check") or {}
    need = 7 * b.get("weeks", 4) if b.get("mode") == "same_weekday" else 12
    if pc.get("spread_k"):
        need += pc.get("history_nights", 28)
    return need + 1


def score_nights(D, rs, start):
    P = rs["points"]
    high = min(t for t, _ in P["night_pct"])
    need = (rs.get("persistence") or {}).get("min_nights", 0)
    pc = rs.get("pressure_check") or {}
    night, day_m3, night_p, sensors = D["night"], D["day_m3"], D["night_p"], D["sensors"]

    def pct_at(j):
        b = float(np.median(night[base_days(j, rs)]))
        return (night[j] - b) / b * 100

    def drop_at(j):
        return (np.median(night_p[base_days(j, rs)], axis=0) - night_p[j]) * 1000

    out = []
    for k in range(start, D["days"]):
        pct = pct_at(k)
        running = 0
        for j in range(k, start - 1, -1):
            if pct_at(j) >= high:
                running += 1
            else:
                break
        bd = base_days(k, rs)
        exp_m3 = float(np.median(day_m3[bd]))
        gap = (day_m3[k] - exp_m3) / exp_m3 * 100
        drop = drop_at(k)
        if pc.get("spread_k"):
            hist = np.array([drop_at(j) for j in range(k - pc.get("history_nights", 28), k)])
            spread = 1.4826 * np.median(np.abs(hist - np.median(hist, axis=0)), axis=0)
            drop = np.where(drop > pc["spread_k"] * np.maximum(spread, 1.0), drop, 0.0)
        top = int(np.argmax(drop))

        sig, held = [], []

        def add(code, pts):
            if pts > 0:
                sig.append({"code": code, "finding": code, "evidence": "", "source": "Rules", "points": pts, "weight": 1.0})
        nf = scoring.tier(pct, P["night_pct"])
        if nf and running < need:
            held.append("NF-01")
        else:
            add("NF-01", nf)
        if running >= P["nights_running"]["min_nights"]:
            add("NF-02", P["nights_running"]["points"])
        add("WB-01", scoring.tier(gap, P["balance_pct"]))
        add("PR-01", scoring.tier(float(drop[top]), P["pressure_mm"]))
        s = scoring.score(sig, rs)
        out.append({"day": k, "score": s["score"], "level": s["level"], "night_m3h": round(float(night[k]), 2),
                    "night_pct": round(pct, 1), "running": running, "balance_pct": round(gap, 1),
                    "top_sensor": sensors[top], "drop_mm": round(float(drop[top])),
                    "codes": [x["code"] for x in sig], "held_back": held})
    return out


def alerts_from(nights, watch):
    """An alert opens at the first night at or above Watch and closes after 2 nights below."""
    events, cur, below = [], None, 0
    for n in nights:
        if n["score"] >= watch:
            below = 0
            if cur is None:
                cur = {"open_day": n["day"], "max_score": n["score"], "level": n["level"],
                       "sensor": n["top_sensor"], "drop_mm": n["drop_mm"], "nights": 0}
            cur["nights"] += 1
            if n["score"] > cur["max_score"]:
                cur["max_score"], cur["level"] = n["score"], n["level"]
        elif cur is not None:
            below += 1
            if below >= 2:
                events.append(cur)
                cur = None
    if cur:
        events.append(cur)
    return events


def evaluate(D, rs, year, start=None):
    d0 = dt.datetime(int(year), 1, 1)
    start = max(start or 0, first_day(rs))
    nights = score_nights(D, rs, start)
    events = alerts_from(nights, rs["bands"]["watch"])
    date = lambda day: (d0 + dt.timedelta(days=int(day))).strftime("%d %b")  # noqa: E731
    for e in events:
        e["date"] = date(e["open_day"])
    for n in nights:
        n["date"] = date(n["day"])
    t_day = lambda text: (dt.datetime.strptime(text, "%Y-%m-%d %H:%M") - d0).total_seconds() / 86400  # noqa: E731
    sxy, lxy, sensors = D["sxy"], D["lxy"], D["sensors"]
    radius = rs.get("fallback_search", {}).get("radius_m", 1200)
    rows = []
    for i, lk in enumerate(D["leaks"]):
        t0, t1 = t_day(lk["start"]), t_day(lk["end"])
        near = np.hypot(sxy[:, 0] - lxy[i][0], sxy[:, 1] - lxy[i][1])
        row = {"pipe": lk["pipe"], "kind": lk["kind"], "start": lk["start"][:10], "end": lk["end"][:10],
               "peak_m3h": round(float(np.nanmax(D["leak_flow"][:, i])), 1),
               "nearest_sensor_m": round(float(near.min())), "testable": t0 >= start}
        if not row["testable"]:
            row["result"] = "before scoring starts" if t0 > 0 else "already leaking on 1 Jan, nothing to compare with"
            rows.append(row)
            continue
        open_before = [e for e in events if e["open_day"] + 4 / 24 <= t0 and
                       e["open_day"] + e["nights"] + 2 >= t0]
        hit = [e for e in events if t0 < e["open_day"] + 4 / 24 and e["open_day"] <= min(t1, t0 + MATCH_DAYS)]
        if hit:
            e = hit[0]
            e.setdefault("leaks", []).append(lk["pipe"])
            j = sensors.index(e["sensor"])
            dist = float(np.hypot(sxy[j, 0] - lxy[i][0], sxy[j, 1] - lxy[i][1]))
            row.update(result="found", days_to_find=round(e["open_day"] + 4 / 24 - t0, 1), alert_date=e["date"],
                       level=e["level"], sensor=e["sensor"], sensor_to_leak_m=round(dist), in_radius=dist <= radius)
        elif open_before:
            row["result"] = "started while another alert was open"
        else:
            row["result"] = "missed"
        rows.append(row)
    testable = [r for r in rows if r["testable"]]
    found = [r for r in testable if r["result"] == "found"]
    false = [e for e in events if not e.get("leaks")]
    med = lambda xs: round(float(np.median(xs)), 1) if xs else None  # noqa: E731
    by_kind = lambda kind: {"n": len([r for r in testable if r["kind"] == kind]),  # noqa: E731
                            "found": len([r for r in found if r["kind"] == kind])}
    summary = {
        "year": int(year), "ruleset": rs["version"], "nights_scored": len(nights),
        "leaks_total": len(rows), "leaks_testable": len(testable), "found": len(found),
        "found_rate": round(len(found) / len(testable), 3) if testable else None,
        "abrupt": by_kind("abrupt"), "incipient": by_kind("incipient"),
        "days_to_find_median": med([r["days_to_find"] for r in found]),
        "found_within": {str(w): len([r for r in found if r["days_to_find"] <= w]) for w in (3, 7, 30)},
        "scored_from": date(start),
        "alerts": len(events), "false_alarms": len(false),
        "false_alarms_per_month": round(len(false) / (len(nights) / 30.4), 2),
        "sensor_to_leak_median_m": med([r["sensor_to_leak_m"] for r in found]),
        "within_search_radius": round(float(np.mean([r["in_radius"] for r in found])), 3) if found else None,
        "search_radius_m": radius, "nearest_sensor_median_m": med([r["nearest_sensor_m"] for r in testable]),
    }
    return {"summary": summary, "leaks": rows, "alerts": events, "false_alarm_dates": [e["date"] for e in false]}, nights


def variant(rs, mode, k):
    r = copy.deepcopy(rs)
    r["baseline"] = {"mode": "same_weekday", "weeks": 4} if mode == "same_weekday" else {"mode": "rolling"}
    r["pressure_check"] = {"spread_k": k, "history_nights": 28} if k else {}
    r["version"] = f"{mode}, spread_k={k or 'off'}"
    return r


def tune(folder):
    """Try settings on 2018 only. 2019 is kept for the final test."""
    D = load(folder, "2018")
    base = scoring.get("wl-1.2")
    rows = []
    for mode in ("rolling", "same_weekday"):
        for k in (0, 2, 3, 4, 5, 6):
            s = evaluate(D, variant(base, mode, k), "2018", COMMON_START)[0]["summary"]
            rows.append({"baseline": mode, "spread_k": k, "found": s["found"], "within_7_days": s["found_within"]["7"],
                         "testable": s["leaks_testable"],
                         "false_alarms": s["false_alarms"], "per_month": s["false_alarms_per_month"],
                         "days_to_find": s["days_to_find_median"], "nights": s["nights_scored"]})
            print(rows[-1], flush=True)
    out = os.path.join(os.path.dirname(HERE), "evaluation", "battledim_tuning_2018.json")
    json.dump(rows, open(out, "w"), indent=1)


COMMON_START = 57   # 2 months: the first night both wl-1.2 and wl-1.3 can score, so they are compared on the same nights


def main(folder, year, version=None):
    rs = scoring.get(version or scoring.ACTIVE)
    res, nights = evaluate(load(folder, year), rs, year, COMMON_START)
    res["method"] = ("Rules only, signals computed as in the app. Data rebuilt with battledim/simulate_ltown.py from the "
                     "official L-Town network and leak list (EPANET 2.2, leaks as emitters).")
    out_dir = os.path.join(os.path.dirname(HERE), "evaluation")
    tag = f"{year}_{rs['version']}"
    json.dump(res, open(os.path.join(out_dir, f"battledim_{tag}.json"), "w"), indent=1)
    json.dump(nights, open(os.path.join(out_dir, f"battledim_nights_{tag}.json"), "w"))
    print(json.dumps(res["summary"], indent=1))
    for r in res["leaks"]:
        print(r["pipe"], r["kind"], r["start"], r["peak_m3h"], r["result"], r.get("days_to_find", ""), r.get("sensor_to_leak_m", ""))
    print("false alarms:", res["false_alarm_dates"])
    return res


if __name__ == "__main__":
    folder = sys.argv[1] if len(sys.argv) > 1 else "."
    what = sys.argv[2] if len(sys.argv) > 2 else "2019"
    if what == "tune":
        tune(folder)
    else:
        main(folder, what, sys.argv[3] if len(sys.argv) > 3 else None)
