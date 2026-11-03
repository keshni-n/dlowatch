"""
DloWatch rules engine (pure functions, no I/O except reading rule files at start-up).

  - Rulesets are versioned JSON files in backend/rules/ (wl-1.0.json, wl-1.1.json ...).
    Never edit a published version: add a new file. Each alert keeps the version it was
    scored with, so old decisions stay explainable.
  - score() turns a list of signals into points, applying trust weights, the AI cap,
    "same fact counts once", combos, floors and bands. Every point lands in the trace.

If no rule file can be read, BUILTIN is used and a warning is logged, so the API still starts.
"""

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("dlowatch.rules")

RULES_DIR = Path(os.environ.get("DLOWATCH_RULES_DIR") or Path(__file__).with_name("rules"))

# Fallback only. The real values live in rules/*.json.
BUILTIN = {
    "version": "wl-builtin",
    "description": "Built-in fallback, used only when no rules file could be read.",
    "bands": {"watch": 20, "high": 45, "critical": 70},
    "trust": {"full": 80, "min": 50},
    "points": {
        "night_pct": [[30, 30], [15, 22], [6, 14]],
        "now_pct": [[5, 20], [2, 14]],
        "balance_pct": [[6, 18], [2, 12]],
        "pressure_mm": [[60, 18], [10, 12]],
        "nights_running": {"min_nights": 2, "points": 8},
        "burst": 20,
        "ai_probability": [[0.9, 15], [0.7, 10]],
        "ai_area": {"min_probability": 0.4, "points": 8},
        "public": {"min_phones": 3, "points": 10, "points_without_meters": 22, "one_report": 4},
    },
    "caps": {"ai_total": 20, "public_only_max": 44, "max": 100},
    "dedupe": True,
    "facts": {"NF-01": "extra water at the inlet meter", "NF-03": "extra water at the inlet meter",
              "WB-01": "extra water at the inlet meter"},
    "ground_ai": True,
    "grounding": {"area_drop_mm": 10, "area_margin_m": 300},
    "fallback_search": {"radius_m": 1200},
    "interactions": [
        {"id": "IX-1", "points": 8, "finding": "Flow rise and pressure drop together",
         "any_a": ["NF-01", "NF-03", "WB-01"], "any_b": ["PR-01"]},
    ],
    "floors": [
        {"id": "FL-BURST", "min": 70, "all": ["PR-02"], "any": ["NF-01", "NF-03"],
         "note": "Burst rule: sudden pressure drop plus a flow jump is at least Critical."},
    ],
}

REQUIRED = ["version", "bands", "trust", "points", "caps", "interactions", "floors"]


def _valid(rs):
    missing = [k for k in REQUIRED if k not in rs]
    if missing:
        return f"missing {', '.join(missing)}"
    b = rs["bands"]
    if not (0 < b.get("watch", 0) < b.get("high", 0) < b.get("critical", 0) <= 100):
        return "bands must be 0 < watch < high < critical <= 100"
    return None


def _version_key(v):
    parts = v.replace("wl-", "").split(".")
    return tuple(int(p) if p.isdigit() else 0 for p in parts)


def load_rulesets():
    found = {}
    if RULES_DIR.is_dir():
        for f in sorted(RULES_DIR.glob("*.json")):
            try:
                rs = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:     # broken JSON: skip the file, keep going
                log.warning("Rules file %s skipped: %s", f.name, e)
                continue
            problem = _valid(rs)
            if problem:
                log.warning("Rules file %s skipped: %s", f.name, problem)
                continue
            found[rs["version"]] = {**BUILTIN, **rs}
    if not found:
        log.warning("No usable rules file in %s, using the built-in fallback", RULES_DIR)
        found[BUILTIN["version"]] = BUILTIN
    wanted = os.environ.get("DLOWATCH_RULESET")
    if wanted and wanted in found:
        active = wanted
    else:
        if wanted:
            log.warning("DLOWATCH_RULESET=%s not found, using the newest ruleset", wanted)
        active = max(found, key=_version_key)
    return found, active


RULESETS, ACTIVE = load_rulesets()


def get(version):
    """The ruleset an alert was scored with. Falls back to the active one if it was removed."""
    return RULESETS.get(version) or RULESETS[ACTIVE]


def tier(value, table):
    """[[threshold, points], ...] from high to low: points of the first threshold reached, else 0."""
    for threshold, pts in sorted(table, key=lambda r: -r[0]):
        if value >= threshold:
            return pts
    return 0


def band(score, rs):
    b = rs["bands"]
    if score >= b["critical"]:
        return "critical"
    if score >= b["high"]:
        return "high"
    if score >= b["watch"]:
        return "watch"
    return "low"


def score(signals, rs):
    """
    signals: [{code, finding, evidence, source, points, weight}] from the detectors.
    Returns {score, level, trace, notes}. Pure: same signals + same ruleset = same answer.
    """
    rows, notes = [], []
    for s in signals:
        pts = round(s["points"] * s.get("weight", 1.0))
        if pts > 0:
            rows.append({**{k: s[k] for k in ("code", "finding", "evidence", "source")}, "points": pts})

    # the model can never add more than caps.ai_total points in total
    budget = rs["caps"].get("ai_total", 20)
    for r in rows:
        if r["source"] == "AI model":
            use = min(r["points"], budget)
            if use < r["points"]:
                notes.append(f"{r['code']} cut from {r['points']} to {use} points: AI findings are capped at {rs['caps']['ai_total']}.")
            r["points"], budget = use, budget - use

    # same fact counts once: keep the strongest, the others are listed as support
    if rs.get("dedupe"):
        facts = rs.get("facts", {})
        best = {}
        for r in rows:
            f = facts.get(r["code"])
            if f and (f not in best or r["points"] > best[f]["points"]):
                best[f] = r
        for r in rows:
            f = facts.get(r["code"])
            if f and best[f] is not r:
                r["supports"] = best[f]["code"]
                r["support_points"] = r["points"]
                r["points"] = 0

    rows = [r for r in rows if r["points"] > 0 or r.get("supports")]
    scoring_codes = {r["code"] for r in rows if r["points"] > 0}
    all_codes = {r["code"] for r in rows}

    for ix in rs.get("interactions", []):
        # name the signals that actually scored first, support rows only if nothing else
        a = [c for c in ix["any_a"] if c in scoring_codes] or [c for c in ix["any_a"] if c in all_codes]
        b = [c for c in ix["any_b"] if c in scoring_codes] or [c for c in ix["any_b"] if c in all_codes]
        if a and b:
            rows.append({"code": ix["id"], "finding": ix["finding"], "evidence": f"{a[0]} + {b[0]}",
                         "source": "Combo", "points": ix["points"]})
            scoring_codes.add(ix["id"])

    total = min(rs["caps"].get("max", 100), sum(r["points"] for r in rows))
    for fl in rs.get("floors", []):
        if all(c in all_codes for c in fl.get("all", [])) and (not fl.get("any") or any(c in all_codes for c in fl["any"])):
            if total < fl["min"]:
                total = fl["min"]
                notes.append(fl.get("note") or f"{fl['id']}: minimum {fl['min']} points.")
    public_only = scoring_codes and all(c.startswith("PUB") for c in scoring_codes)
    cap = rs["caps"].get("public_only_max")
    if public_only and cap is not None and total > cap:
        total = cap
        notes.append("Public reports alone can raise a zone to Watch, never higher.")
    return {"score": total, "level": band(total, rs), "trace": rows, "notes": notes}
