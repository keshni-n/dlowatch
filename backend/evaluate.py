"""
DloWatch evaluation: run many simulated scenarios through the WHOLE pipeline
(EPANET -> noisy meters -> cleaning -> rules + model -> alert -> search area -> suspects)
and measure how well it works.

    python evaluate.py                 # 8 scenarios of each kind per zone (about 5 minutes)
    python evaluate.py --per-zone 3    # quick run

Scenario kinds:
  slow     slow leak (small or medium), started 0.5 to 4 days ago          -> should alert
  burst    large leak, started 0.5 to 6 hours ago                          -> should alert, Critical
  normal   no leak, water use 5% higher or lower than usual, sensor noise -> should NOT alert
  faulty   no leak, one meter has a fault (spikes, stuck, drift, gaps...) -> should NOT alert
  spam     3 public reports from ONE phone, no leak                       -> should NOT group
  crowd    3 public reports from 3 phones, no leak in the meters          -> groups, but Watch at most

Noise matches the training data: water use +-5%, pressure sensors +-1 mm.
Results: evaluation/results.json (shown in the app) and evaluation/results.md.
"""

import argparse
import json
import random
import statistics
import time"""
DloWatch evaluation: run many simulated scenarios through the WHOLE pipeline
(EPANET -> noisy meters -> cleaning -> rules + model -> alert -> search area -> suspects)
and measure how well it works.

    python evaluate.py                 # 8 scenarios of each kind per zone (about 5 minutes)
    python evaluate.py --per-zone 3    # quick run

Scenario kinds:
  slow     slow leak (small or medium), started 0.5 to 4 days ago          -> should alert
  burst    large leak, started 0.5 to 6 hours ago                          -> should alert, Critical
  normal   no leak, water use 5% higher or lower than usual, sensor noise -> should NOT alert
  faulty   no leak, one meter has a fault (spikes, stuck, drift, gaps...) -> should NOT alert
  spam     3 public reports from ONE phone, no leak                       -> should NOT group
  crowd    3 public reports from 3 phones, no leak in the meters          -> groups, but Watch at most

Noise matches the training data: water use +-5%, pressure sensors +-1 mm.
Results: evaluation/results.json (shown in the app) and evaluation/results.md.
"""

import argparse
import json
import random
import statistics
import time
from pathlib import Path

import dlowatch as d
import scoring

OUT = Path(__file__).with_name("evaluation")
FAULT_KINDS = ["spikes", "stuck", "drift", "gaps", "reset", "offline"]


BASE_NOW = d.NOW
DAY_RNG = random.Random(99)


def reset(zone):
    """Empty world with only this zone's meters, so each scenario is judged on its own.
    Each scenario also lands on a random day of the last month, because water use differs day to day."""
    d.LEAKS.clear(); d.ALERTS.clear(); d.REPORTS.clear(); d.JOBS.clear(); d.FAULTS.clear()
    d.METERS.clear()
    d.METERS.update({k: v for k, v in ALL_METERS.items() if v["zone"] == zone})
    d.NOW = BASE_NOW - DAY_RNG.randint(0, 30) * 86400
    d.VERSION["n"] += 1


def pipes_of(zone):
    return sorted(p for p, z in d.api.PIPE_ZONE.items() if z == zone and not p.startswith("P_IN")
                  and d.PIPE.get(p, {}).get("status", "open") == "open" and (d.PIPE.get(p, {}).get("length_m") or 0) > 20)


def rate(x, n):
    return round(x / n, 3) if n else None


def pct(v):
    return f"{v * 100:.0f}%" if v is not None else "n/a"


BEFORE_LOGGERS = "wl-1.3"     # last ruleset without the noise loggers, for the before/after comparison
LOCATION_KEYS = ("detected", "score", "level", "error_m", "in_search", "suspect_rank")


def leak_case(zone, kind, rng, model_on=True):
    """The same leak twice: once scored without the noise loggers (wl-1.3), once with them (active ruleset).
    Both runs use the same random draws, so the only difference is the loggers."""
    st, day = rng.getstate(), DAY_RNG.getstate()
    active = d.RULESET
    d.RULESET = BEFORE_LOGGERS
    before = one_leak(zone, kind, rng, model_on, loggers=False)
    d.RULESET = active
    rng.setstate(st); DAY_RNG.setstate(day)
    after = one_leak(zone, kind, rng, model_on, loggers=True)
    after["before"] = {k: before.get(k) for k in LOCATION_KEYS}
    return after


def one_leak(zone, kind, rng, model_on=True, loggers=True):
    reset(zone)
    d.MODEL["enabled"] = model_on
    pipe = rng.choice(pipes_of(zone))
    if kind == "burst":
        size, started = "large", d.NOW - int(rng.uniform(0.5, 6) * 3600)
    else:
        size, started = rng.choice(["small", "medium"]), d.NOW - int(rng.uniform(0.5, 4) * 86400)
    aid, status = d.create_leak_alert(pipe, size, started, kind, demand_scale=1 + rng.uniform(-0.05, 0.05),
                                      sensor_noise_mm=1.0, rng=rng)
    leak = next(iter(d.LEAKS.values()))
    near = min((d.km(*leak["latlon"], lg["lat"], lg["lon"]) * 1000 for lg in d.LOGGERS.values() if lg["zone"] == zone), default=None)
    row = {"zone": zone, "kind": kind, "pipe": pipe, "size": size, "age_h": round((d.NOW - started) / 3600, 1),
           "leak_lps": leak["flow_lps"], "detected": aid is not None, "model": status,
           "nearest_logger_m": round(near) if near is not None else None,
           "in_hearing_range": near is not None and near <= d.ACOUSTIC["hearing_range_m"]}
    if aid:
        a = d.ALERTS[aid]
        if loggers:
            d.apply_loggers(a)
            g = (a.get("search") or {}).get("best_guess")
            if g:
                a["check"] = {**a.get("check", {}), "distance_error_m": round(d.km(g[0], g[1], *leak["latlon"]) * 1000),
                              "pipe_in_search": any(pipe in ar["pipes"] for ar in a["search"]["areas"])}
        s = d.score_alert(a)
        row["noise_heard"] = any(x["code"] == "AC-01" and x["points"] > 0 for x in s["trace"])
        chk = {**a.get("check", {}), **d.suspect_check(a)}
        m3 = (a.get("search") or {}).get("m3_day")
        row.update(score=s["score"], level=s["level"], error_m=chk.get("distance_error_m"),
                   in_search=chk.get("pipe_in_search"), suspect_rank=chk.get("true_pipe_suspect_rank"),
                   size_error_pct=round(abs(m3 - leak["flow_lps"] * 86.4) / (leak["flow_lps"] * 86.4) * 100, 1) if m3 and leak["flow_lps"] else None)
    return row


def quiet_case(zone, kind, rng):
    reset(zone)
    d.MODEL["enabled"] = True
    fault = None
    if kind == "faulty":
        fault = rng.choice(FAULT_KINDS)
        inlet = d.api.ZONES[zone]["inlet"]
        sensors = d.api.ZONES[zone]["sensors"]
        meter = inlet if fault in ("reset", "spikes", "gaps") and rng.random() < 0.5 else rng.choice(sensors)
        d.FAULTS[meter] = fault
        d.VERSION["n"] += 1
    readings = d.simulate_normal(zone, 1 + rng.uniform(-0.05, 0.05), 1.0, rng)
    _, s = d.scan_zone(zone, readings)
    return {"zone": zone, "kind": kind, "fault": fault, "score": s["score"], "level": s["level"],
            "false_alarm": s["score"] >= scoring.get(d.RULESET)["bands"]["watch"],
            "signals": [x["code"] for x in s["trace"] if x["points"] > 0]}


def report_case(zone, kind, rng):
    reset(zone)
    sid = d.api.ZONES[zone]["sensors"][rng.randrange(len(d.api.ZONES[zone]["sensors"]))]
    lon, lat = d.NODE[sid]["geometry"]["coordinates"]
    for i in range(3):
        device = "same-phone-123456" if kind == "spam" else f"phone-{i}-{rng.random():.6f}"
        d.add_report(d.ReportIn(lat=lat + 0.0003 * i, lon=lon - 0.0002 * i, kind="water_on_road", device=device),
                     at=d.NOW - (60 - i * 10) * 60)
    alerts = list(d.ALERTS.values())
    level = d.score_alert(alerts[0])["level"] if alerts else None
    return {"zone": zone, "kind": kind, "grouped": bool(alerts), "level": level}


def summarise(rows, seconds, per_zone, seed, model_off_rows):
    slow = [r for r in rows if r["kind"] == "slow"]
    burst = [r for r in rows if r["kind"] == "burst"]
    normal = [r for r in rows if r["kind"] == "normal"]
    faulty = [r for r in rows if r["kind"] == "faulty"]
    spam = [r for r in rows if r["kind"] == "spam"]
    crowd = [r for r in rows if r["kind"] == "crowd"]

    def by_age(rs):
        buckets = {"under 1 day": (0, 24), "1 to 2 days": (24, 48), "over 2 days": (48, 10 ** 6)}
        out = {}
        for name, (lo, hi) in buckets.items():
            b = [r for r in rs if lo <= r["age_h"] < hi]
            out[name] = {"n": len(b), "detected": sum(r["detected"] for r in b), "rate": rate(sum(r["detected"] for r in b), len(b))}
        return out

    def location(rs):
        det = [r for r in rs if r["detected"]]
        errs = sorted(r["error_m"] for r in det if r.get("error_m") is not None)
        n = len(det)
        sizes = [r["size_error_pct"] for r in det if r.get("size_error_pct") is not None]
        return {"alerts": n,
                "median_error_m": round(statistics.median(errs)) if errs else None,
                "p80_error_m": round(errs[int(len(errs) * 0.8) - 1]) if len(errs) >= 5 else None,
                "pipe_in_search_area": rate(sum(1 for r in det if r.get("in_search")), n),
                "suspect_top1": rate(sum(1 for r in det if r.get("suspect_rank") == 1), n),
                "suspect_top3": rate(sum(1 for r in det if r.get("suspect_rank") and r["suspect_rank"] <= 3), n),
                "suspect_top5": rate(sum(1 for r in det if r.get("suspect_rank")), n),
                "size_error_median_pct": round(statistics.median(sizes), 1) if sizes else None}

    by_fault = {}
    for f in FAULT_KINDS:
        b = [r for r in faulty if r["fault"] == f]
        if b:
            by_fault[f] = {"n": len(b), "false_alarms": sum(r["false_alarm"] for r in b)}
    leaks = slow + burst

    def compare(rs, label):
        """Before vs after the loggers on the same leaks."""
        det_b = [r for r in rs if r["before"]["detected"]]
        det_a = [r for r in rs if r["detected"]]

        def loc(rows, key):
            errs = sorted(x for x in (key(r).get("error_m") for r in rows) if x is not None)
            n = len(rows)
            return {"alerts": n, "median_error_m": round(statistics.median(errs)) if errs else None,
                    "pipe_in_search_area": rate(sum(1 for r in rows if key(r).get("in_search")), n),
                    "suspect_top1": rate(sum(1 for r in rows if key(r).get("suspect_rank") == 1), n),
                    "suspect_top3": rate(sum(1 for r in rows if (key(r).get("suspect_rank") or 99) <= 3), n)}
        return {"label": label, "leaks": len(rs),
                "before": {**loc(det_b, lambda r: r["before"]), "detected": rate(len(det_b), len(rs))},
                "after": {**loc(det_a, lambda r: r), "detected": rate(len(det_a), len(rs)),
                          "noise_heard": rate(sum(1 for r in det_a if r.get("noise_heard")), len(det_a))}}

    old_slow = [r for r in slow if r["age_h"] >= 48]
    acoustic = {
        "hearing_range_m": d.ACOUSTIC["hearing_range_m"], "loggers": len(d.LOGGERS), "pipe_coverage_pct": d.LOGGER_COVERAGE["overall"],
        "before_ruleset": BEFORE_LOGGERS, "after_ruleset": d.RULESET,
        "groups": [
            compare(leaks, "All leaks"),
            compare(old_slow, "Slow leaks running 2+ nights"),
            compare([r for r in old_slow if r["in_hearing_range"]], "Slow leaks running 2+ nights, within hearing range of a logger"),
            compare(burst, "Bursts (under 6 hours old, loggers have not recorded yet)"),
        ] + ([compare(model_off_rows, "Rules only (model switched off)")] if model_off_rows else []),
    }
    out = {
        "run": {"date": time.strftime("%Y-%m-%d %H:%M"), "seed": seed, "per_zone": per_zone, "zones": len(d.api.ZONES),
                "ruleset": d.RULESET, "scenarios": len(rows) + len(model_off_rows), "seconds": round(seconds),
                "noise": "water use +-5%, pressure sensors +-1 mm (same as the training data)"},
        "detection": {
            "slow": {"n": len(slow), "detected": sum(r["detected"] for r in slow), "rate": rate(sum(r["detected"] for r in slow), len(slow)),
                     "by_age": by_age(slow)},
            "burst": {"n": len(burst), "detected": sum(r["detected"] for r in burst), "rate": rate(sum(r["detected"] for r in burst), len(burst)),
                      "critical": rate(sum(1 for r in burst if r.get("level") == "critical"), len(burst))},
            "all_leaks": {"n": len(leaks), "rate": rate(sum(r["detected"] for r in leaks), len(leaks))},
        },
        "false_alarms": {
            "normal_day": {"n": len(normal), "alerts": sum(r["false_alarm"] for r in normal), "rate": rate(sum(r["false_alarm"] for r in normal), len(normal))},
            "faulty_meter": {"n": len(faulty), "alerts": sum(r["false_alarm"] for r in faulty), "rate": rate(sum(r["false_alarm"] for r in faulty), len(faulty)),
                             "by_fault": by_fault},
        },
        "location": {"with_model": {**location(leaks), "leaks": len(leaks)},
                     "without_model": {**location(model_off_rows), "leaks": len(model_off_rows)} if model_off_rows else None,
                     "without_model_detection": rate(sum(r["detected"] for r in model_off_rows), len(model_off_rows)) if model_off_rows else None},
        "acoustic": acoustic,
        "public_reports": {
            "spam_one_phone": {"n": len(spam), "grouped": sum(r["grouped"] for r in spam)},
            "three_phones": {"n": len(crowd), "grouped": sum(r["grouped"] for r in crowd),
                             "above_watch": sum(1 for r in crowd if r["level"] in ("high", "critical"))},
        },
    }
    return out


def markdown(r):
    det, fa, loc, pub = r["detection"], r["false_alarms"], r["location"], r["public_reports"]
    wm, wo = loc["with_model"], loc["without_model"] or {}
    lines = [
        "# DloWatch evaluation", "",
        f"Run {r['run']['date']}, ruleset {r['run']['ruleset']}, {r['run']['scenarios']} simulated scenarios "
        f"({r['run']['per_zone']} of each kind per zone, {r['run']['zones']} zones), seed {r['run']['seed']}, {r['run']['seconds']} s.",
        f"Noise: {r['run']['noise']}. Simulated with EPANET on the illustrative North Mauritius network, not real CWA data.", "",
        "## Does it find leaks?", "",
        "| Scenario | Alerts raised |", "|---|---|",
        f"| Slow leaks | {det['slow']['detected']}/{det['slow']['n']} ({pct(det['slow']['rate'])}) |",
    ]
    for k, v in det["slow"]["by_age"].items():
        lines.append(f"| · started {k} ago | {v['detected']}/{v['n']} ({pct(v['rate'])}) |")
    lines += [f"| Bursts | {det['burst']['detected']}/{det['burst']['n']} ({pct(det['burst']['rate'])}), Critical {pct(det['burst']['critical'])} |", "",
              "Night flow only counts after 2 high nights (persistence rule). Young slow leaks can still be raised earlier by the water balance, pressure drops and the model.", "",
              "## Does it raise false alarms?", "", "| Scenario | False alarms |", "|---|---|",
              f"| Normal day (no leak) | {fa['normal_day']['alerts']}/{fa['normal_day']['n']} ({pct(fa['normal_day']['rate'])}) |",
              f"| A faulty meter (no leak) | {fa['faulty_meter']['alerts']}/{fa['faulty_meter']['n']} ({pct(fa['faulty_meter']['rate'])}) |"]
    for k, v in fa["faulty_meter"]["by_fault"].items():
        lines.append(f"| · {k} | {v['false_alarms']}/{v['n']} |")
    lines += ["", "## Does it find the right place?", "", "| | With the model | Without the model (rules only) |", "|---|---|---|",
              f"| Leaks alerted | {wm['alerts']}/{wm['leaks']} | {wo.get('alerts', 'n/a')}/{wo.get('leaks', 'n/a')} |",
              f"| Typical distance off (median) | {wm['median_error_m']} m | {wo.get('median_error_m', 'n/a')} m |",
              f"| 8 in 10 within | {wm['p80_error_m']} m | {wo.get('p80_error_m', 'n/a')} m |",
              f"| Leak pipe inside the search area | {pct(wm['pipe_in_search_area'])} | {pct(wo.get('pipe_in_search_area'))} |",
              f"| Leak in the first block to check | {pct(wm['suspect_top1'])} | {pct(wo.get('suspect_top1'))} |",
              f"| Leak in the top 3 blocks | {pct(wm['suspect_top3'])} | {pct(wo.get('suspect_top3'))} |",
              f"| Leak in the top 5 blocks | {pct(wm['suspect_top5'])} | {pct(wo.get('suspect_top5'))} |",
              f"| Size estimate off by (median) | {wm['size_error_median_pct']}% | {wo.get('size_error_median_pct', 'n/a')}% |", "",
              "## Noise loggers: before and after", "",
              f"{r['acoustic']['loggers']} loggers, {r['acoustic']['pipe_coverage_pct']}% of pipe length within {r['acoustic']['hearing_range_m']} m of one. "
              f"Each leak is run twice with the same random draws: scored with {r['acoustic']['before_ruleset']} (no loggers) and with {r['acoustic']['after_ruleset']}.", "",
              "| Leaks | n | Alerted | Median distance off | Leak in the first block | Leak in the top 3 blocks | Loggers heard it |",
              "|---|---|---|---|---|---|---|"]
    for g in r["acoustic"]["groups"]:
        b, a = g["before"], g["after"]
        lines.append(f"| {g['label']} | {g['leaks']} | {pct(b['detected'])} → {pct(a['detected'])} | "
                     f"{b['median_error_m']} → {a['median_error_m']} m | {pct(b['suspect_top1'])} → {pct(a['suspect_top1'])} | "
                     f"{pct(b['suspect_top3'])} → {pct(a['suspect_top3'])} | {pct(a['noise_heard'])} |")
    lines += ["", "Loggers record once a night and a logger only counts after 2 loud nights, so they cannot help with bursts or leaks younger than 2 nights. "
              "About half the pipes are out of hearing range; leaks there are found as before.", "",
              "## Public reports", "",
              f"- 3 reports from one phone grouped into an alert: {pub['spam_one_phone']['grouped']}/{pub['spam_one_phone']['n']} (should be 0)",
              f"- 3 reports from 3 phones grouped: {pub['three_phones']['grouped']}/{pub['three_phones']['n']}, "
              f"above Watch: {pub['three_phones']['above_watch']} (should be 0)", "",
              "## Limits (read before quoting these numbers)", "",
              "- Everything is simulated on an illustrative network with assumed pipes and demand. Real networks are messier.",
              "- The meter faults tested are the same kinds the cleaning step was built for. Real meters will fail in other ways too, so expect some false alarms in practice.",
              "- Leaks are single emitters on one pipe. Several leaks at once, or leaks that grow over weeks, are not tested here (see the BattLeDIM test for that).",
              "- The model was trained on the same simulated network, so location results are likely better than on a new real network.",
              "- Logger noise is simulated with a simple model (leak hiss louder with more water, fading along the pipe). Real loggers depend on pipe material, depth and street noise, and the hearing range is an assumption to check with a supplier.", ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per-zone", type=int, default=8, help="scenarios of each kind per zone")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--skip-model-off", action="store_true", help="do not repeat the leaks with the model switched off")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    t0 = time.time()
    rows, off = [], []
    zones = list(d.api.ZONES)
    for z in zones:
        for _ in range(a.per_zone):
            rows.append(leak_case(z, "slow", rng))
            rows.append(leak_case(z, "burst", rng))
            rows.append(quiet_case(z, "normal", rng))
            rows.append(quiet_case(z, "faulty", rng))
            if not a.skip_model_off:
                off.append(leak_case(z, rng.choice(["slow", "burst"]), rng, model_on=False))
        for _ in range(max(1, a.per_zone // 4)):
            rows.append(report_case(z, "spam", rng))
            rows.append(report_case(z, "crowd", rng))
        print(f"  zone {z} done, {len(rows) + len(off)} scenarios, {time.time() - t0:.0f} s", flush=True)
    d.MODEL["enabled"] = True
    res = summarise(rows, time.time() - t0, a.per_zone, a.seed, off)
    OUT.mkdir(exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    (OUT / "results.md").write_text(markdown(res), encoding="utf-8")
    (OUT / "scenarios.json").write_text(json.dumps({"with_model": rows, "without_model": off}, indent=1), encoding="utf-8")
    print(markdown(res))


ALL_METERS = dict(d.METERS)

if __name__ == "__main__":
    main()

from pathlib import Path

import dlowatch as d
import scoring

OUT = Path(__file__).with_name("evaluation")
FAULT_KINDS = ["spikes", "stuck", "drift", "gaps", "reset", "offline"]


BASE_NOW = d.NOW
DAY_RNG = random.Random(99)


def reset(zone):
    """Empty world with only this zone's meters, so each scenario is judged on its own.
    Each scenario also lands on a random day of the last month, because water use differs day to day."""
    d.LEAKS.clear(); d.ALERTS.clear(); d.REPORTS.clear(); d.JOBS.clear(); d.FAULTS.clear()
    d.METERS.clear()
    d.METERS.update({k: v for k, v in ALL_METERS.items() if v["zone"] == zone})
    d.NOW = BASE_NOW - DAY_RNG.randint(0, 30) * 86400
    d.VERSION["n"] += 1


def pipes_of(zone):
    return sorted(p for p, z in d.api.PIPE_ZONE.items() if z == zone and not p.startswith("P_IN")
                  and d.PIPE.get(p, {}).get("status", "open") == "open" and (d.PIPE.get(p, {}).get("length_m") or 0) > 20)


def rate(x, n):
    return round(x / n, 3) if n else None


def pct(v):
    return f"{v * 100:.0f}%" if v is not None else "n/a"


def leak_case(zone, kind, rng, model_on=True):
    reset(zone)
    d.MODEL["enabled"] = model_on
    pipe = rng.choice(pipes_of(zone))
    if kind == "burst":
        size, started = "large", d.NOW - int(rng.uniform(0.5, 6) * 3600)
    else:
        size, started = rng.choice(["small", "medium"]), d.NOW - int(rng.uniform(0.5, 4) * 86400)
    aid, status = d.create_leak_alert(pipe, size, started, kind, demand_scale=1 + rng.uniform(-0.05, 0.05),
                                      sensor_noise_mm=1.0, rng=rng)
    leak = next(iter(d.LEAKS.values()))
    row = {"zone": zone, "kind": kind, "pipe": pipe, "size": size, "age_h": round((d.NOW - started) / 3600, 1),
           "leak_lps": leak["flow_lps"], "detected": aid is not None, "model": status}
    if aid:
        a = d.ALERTS[aid]
        s = d.score_alert(a)
        chk = {**a.get("check", {}), **d.suspect_check(a)}
        m3 = (a.get("search") or {}).get("m3_day")
        row.update(score=s["score"], level=s["level"], error_m=chk.get("distance_error_m"),
                   in_search=chk.get("pipe_in_search"), suspect_rank=chk.get("true_pipe_suspect_rank"),
                   size_error_pct=round(abs(m3 - leak["flow_lps"] * 86.4) / (leak["flow_lps"] * 86.4) * 100, 1) if m3 and leak["flow_lps"] else None)
    return row


def quiet_case(zone, kind, rng):
    reset(zone)
    d.MODEL["enabled"] = True
    fault = None
    if kind == "faulty":
        fault = rng.choice(FAULT_KINDS)
        inlet = d.api.ZONES[zone]["inlet"]
        sensors = d.api.ZONES[zone]["sensors"]
        meter = inlet if fault in ("reset", "spikes", "gaps") and rng.random() < 0.5 else rng.choice(sensors)
        d.FAULTS[meter] = fault
        d.VERSION["n"] += 1
    readings = d.simulate_normal(zone, 1 + rng.uniform(-0.05, 0.05), 1.0, rng)
    _, s = d.scan_zone(zone, readings)
    return {"zone": zone, "kind": kind, "fault": fault, "score": s["score"], "level": s["level"],
            "false_alarm": s["score"] >= scoring.get(d.RULESET)["bands"]["watch"],
            "signals": [x["code"] for x in s["trace"] if x["points"] > 0]}


def report_case(zone, kind, rng):
    reset(zone)
    sid = d.api.ZONES[zone]["sensors"][rng.randrange(len(d.api.ZONES[zone]["sensors"]))]
    lon, lat = d.NODE[sid]["geometry"]["coordinates"]
    for i in range(3):
        device = "same-phone-123456" if kind == "spam" else f"phone-{i}-{rng.random():.6f}"
        d.add_report(d.ReportIn(lat=lat + 0.0003 * i, lon=lon - 0.0002 * i, kind="water_on_road", device=device),
                     at=d.NOW - (60 - i * 10) * 60)
    alerts = list(d.ALERTS.values())
    level = d.score_alert(alerts[0])["level"] if alerts else None
    return {"zone": zone, "kind": kind, "grouped": bool(alerts), "level": level}


def summarise(rows, seconds, per_zone, seed, model_off_rows):
    slow = [r for r in rows if r["kind"] == "slow"]
    burst = [r for r in rows if r["kind"] == "burst"]
    normal = [r for r in rows if r["kind"] == "normal"]
    faulty = [r for r in rows if r["kind"] == "faulty"]
    spam = [r for r in rows if r["kind"] == "spam"]
    crowd = [r for r in rows if r["kind"] == "crowd"]

    def by_age(rs):
        buckets = {"under 1 day": (0, 24), "1 to 2 days": (24, 48), "over 2 days": (48, 10 ** 6)}
        out = {}
        for name, (lo, hi) in buckets.items():
            b = [r for r in rs if lo <= r["age_h"] < hi]
            out[name] = {"n": len(b), "detected": sum(r["detected"] for r in b), "rate": rate(sum(r["detected"] for r in b), len(b))}
        return out

    def location(rs):
        det = [r for r in rs if r["detected"]]
        errs = sorted(r["error_m"] for r in det if r.get("error_m") is not None)
        n = len(det)
        sizes = [r["size_error_pct"] for r in det if r.get("size_error_pct") is not None]
        return {"alerts": n,
                "median_error_m": round(statistics.median(errs)) if errs else None,
                "p80_error_m": round(errs[int(len(errs) * 0.8) - 1]) if len(errs) >= 5 else None,
                "pipe_in_search_area": rate(sum(1 for r in det if r.get("in_search")), n),
                "suspect_top1": rate(sum(1 for r in det if r.get("suspect_rank") == 1), n),
                "suspect_top3": rate(sum(1 for r in det if r.get("suspect_rank") and r["suspect_rank"] <= 3), n),
                "suspect_top5": rate(sum(1 for r in det if r.get("suspect_rank")), n),
                "size_error_median_pct": round(statistics.median(sizes), 1) if sizes else None}

    by_fault = {}
    for f in FAULT_KINDS:
        b = [r for r in faulty if r["fault"] == f]
        if b:
            by_fault[f] = {"n": len(b), "false_alarms": sum(r["false_alarm"] for r in b)}
    leaks = slow + burst
    out = {
        "run": {"date": time.strftime("%Y-%m-%d %H:%M"), "seed": seed, "per_zone": per_zone, "zones": len(d.api.ZONES),
                "ruleset": d.RULESET, "scenarios": len(rows) + len(model_off_rows), "seconds": round(seconds),
                "noise": "water use +-5%, pressure sensors +-1 mm (same as the training data)"},
        "detection": {
            "slow": {"n": len(slow), "detected": sum(r["detected"] for r in slow), "rate": rate(sum(r["detected"] for r in slow), len(slow)),
                     "by_age": by_age(slow)},
            "burst": {"n": len(burst), "detected": sum(r["detected"] for r in burst), "rate": rate(sum(r["detected"] for r in burst), len(burst)),
                      "critical": rate(sum(1 for r in burst if r.get("level") == "critical"), len(burst))},
            "all_leaks": {"n": len(leaks), "rate": rate(sum(r["detected"] for r in leaks), len(leaks))},
        },
        "false_alarms": {
            "normal_day": {"n": len(normal), "alerts": sum(r["false_alarm"] for r in normal), "rate": rate(sum(r["false_alarm"] for r in normal), len(normal))},
            "faulty_meter": {"n": len(faulty), "alerts": sum(r["false_alarm"] for r in faulty), "rate": rate(sum(r["false_alarm"] for r in faulty), len(faulty)),
                             "by_fault": by_fault},
        },
        "location": {"with_model": {**location(leaks), "leaks": len(leaks)},
                     "without_model": {**location(model_off_rows), "leaks": len(model_off_rows)} if model_off_rows else None,
                     "without_model_detection": rate(sum(r["detected"] for r in model_off_rows), len(model_off_rows)) if model_off_rows else None},
        "public_reports": {
            "spam_one_phone": {"n": len(spam), "grouped": sum(r["grouped"] for r in spam)},
            "three_phones": {"n": len(crowd), "grouped": sum(r["grouped"] for r in crowd),
                             "above_watch": sum(1 for r in crowd if r["level"] in ("high", "critical"))},
        },
    }
    return out


def markdown(r):
    det, fa, loc, pub = r["detection"], r["false_alarms"], r["location"], r["public_reports"]
    wm, wo = loc["with_model"], loc["without_model"] or {}
    lines = [
        "# DloWatch evaluation", "",
        f"Run {r['run']['date']}, ruleset {r['run']['ruleset']}, {r['run']['scenarios']} simulated scenarios "
        f"({r['run']['per_zone']} of each kind per zone, {r['run']['zones']} zones), seed {r['run']['seed']}, {r['run']['seconds']} s.",
        f"Noise: {r['run']['noise']}. Simulated with EPANET on the illustrative North Mauritius network, not real CWA data.", "",
        "## Does it find leaks?", "",
        "| Scenario | Alerts raised |", "|---|---|",
        f"| Slow leaks | {det['slow']['detected']}/{det['slow']['n']} ({pct(det['slow']['rate'])}) |",
    ]
    for k, v in det["slow"]["by_age"].items():
        lines.append(f"| · started {k} ago | {v['detected']}/{v['n']} ({pct(v['rate'])}) |")
    lines += [f"| Bursts | {det['burst']['detected']}/{det['burst']['n']} ({pct(det['burst']['rate'])}), Critical {pct(det['burst']['critical'])} |", "",
              "Night flow only counts after 2 high nights (persistence rule). Young slow leaks can still be raised earlier by the water balance, pressure drops and the model.", "",
              "## Does it raise false alarms?", "", "| Scenario | False alarms |", "|---|---|",
              f"| Normal day (no leak) | {fa['normal_day']['alerts']}/{fa['normal_day']['n']} ({pct(fa['normal_day']['rate'])}) |",
              f"| A faulty meter (no leak) | {fa['faulty_meter']['alerts']}/{fa['faulty_meter']['n']} ({pct(fa['faulty_meter']['rate'])}) |"]
    for k, v in fa["faulty_meter"]["by_fault"].items():
        lines.append(f"| · {k} | {v['false_alarms']}/{v['n']} |")
    lines += ["", "## Does it find the right place?", "", "| | With the model | Without the model (rules only) |", "|---|---|---|",
              f"| Leaks alerted | {wm['alerts']}/{wm['leaks']} | {wo.get('alerts', 'n/a')}/{wo.get('leaks', 'n/a')} |",
              f"| Typical distance off (median) | {wm['median_error_m']} m | {wo.get('median_error_m', 'n/a')} m |",
              f"| 8 in 10 within | {wm['p80_error_m']} m | {wo.get('p80_error_m', 'n/a')} m |",
              f"| Leak pipe inside the search area | {pct(wm['pipe_in_search_area'])} | {pct(wo.get('pipe_in_search_area'))} |",
              f"| Leak in the first block to check | {pct(wm['suspect_top1'])} | {pct(wo.get('suspect_top1'))} |",
              f"| Leak in the top 3 blocks | {pct(wm['suspect_top3'])} | {pct(wo.get('suspect_top3'))} |",
              f"| Leak in the top 5 blocks | {pct(wm['suspect_top5'])} | {pct(wo.get('suspect_top5'))} |",
              f"| Size estimate off by (median) | {wm['size_error_median_pct']}% | {wo.get('size_error_median_pct', 'n/a')}% |", "",
              "## Public reports", "",
              f"- 3 reports from one phone grouped into an alert: {pub['spam_one_phone']['grouped']}/{pub['spam_one_phone']['n']} (should be 0)",
              f"- 3 reports from 3 phones grouped: {pub['three_phones']['grouped']}/{pub['three_phones']['n']}, "
              f"above Watch: {pub['three_phones']['above_watch']} (should be 0)", "",
              "## Limits (read before quoting these numbers)", "",
              "- Everything is simulated on an illustrative network with assumed pipes and demand. Real networks are messier.",
              "- The meter faults tested are the same kinds the cleaning step was built for. Real meters will fail in other ways too, so expect some false alarms in practice.",
              "- Leaks are single emitters on one pipe. Several leaks at once, or leaks that grow over weeks, are not tested here (see the BattLeDIM test for that).",
              "- The model was trained on the same simulated network, so location results are likely better than on a new real network.", ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per-zone", type=int, default=8, help="scenarios of each kind per zone")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--skip-model-off", action="store_true", help="do not repeat the leaks with the model switched off")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    t0 = time.time()
    rows, off = [], []
    zones = list(d.api.ZONES)
    for z in zones:
        for _ in range(a.per_zone):
            rows.append(leak_case(z, "slow", rng))
            rows.append(leak_case(z, "burst", rng))
            rows.append(quiet_case(z, "normal", rng))
            rows.append(quiet_case(z, "faulty", rng))
            if not a.skip_model_off:
                off.append(leak_case(z, rng.choice(["slow", "burst"]), rng, model_on=False))
        for _ in range(max(1, a.per_zone // 4)):
            rows.append(report_case(z, "spam", rng))
            rows.append(report_case(z, "crowd", rng))
        print(f"  zone {z} done, {len(rows) + len(off)} scenarios, {time.time() - t0:.0f} s", flush=True)
    d.MODEL["enabled"] = True
    res = summarise(rows, time.time() - t0, a.per_zone, a.seed, off)
    OUT.mkdir(exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    (OUT / "results.md").write_text(markdown(res), encoding="utf-8")
    (OUT / "scenarios.json").write_text(json.dumps({"with_model": rows, "without_model": off}, indent=1), encoding="utf-8")
    print(markdown(res))


ALL_METERS = dict(d.METERS)

if __name__ == "__main__":
    main()
