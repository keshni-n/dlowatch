# Rulesets

Each file is one version of the leak scoring rules. The newest version is used for new alerts,
unless `DLOWATCH_RULESET` is set (for example `DLOWATCH_RULESET=wl-1.0`).

- **Never edit a published file.** Copy it, raise the version (`wl-1.2.json`) and change the copy.
  Every alert keeps the version it was scored with, so old scores can still be explained.
- A broken or incomplete file is skipped with a warning. If no file works, a built-in copy is used.

| Key | Meaning |
|---|---|
| `bands` | Score where Watch, High and Critical start |
| `trust` | Meter trust (%) for full weight, and the minimum to count at all (half weight in between) |
| `points` | Points per signal. Lists are `[threshold, points]`, highest threshold first |
| `caps` | Most points the AI can add, the limit for public-only alerts, the overall maximum |
| `dedupe` + `facts` | Signals mapped to the same fact count once (the strongest), the rest show as support |
| `ground_ai` + `grounding` | Only count AI findings that the meters agree with |
| `fallback_search` | Search radius used when the model is off: around the sensor with the biggest drop |
| `persistence` | `min_nights`: night flow only counts after this many high nights in a row (wl-1.2 and later) |
| `baseline` | `{"mode": "same_weekday", "weeks": 4}`: the usual night is the same weekday over the last 4 weeks. Falls back to the oldest 7 nights when there is less history (wl-1.3) |
| `pressure_check` | `{"spread_k": 3, "history_nights": 28}`: a pressure drop only counts if it is more than 3 times the sensor's usual night-to-night wobble (wl-1.3, used in the BattLeDIM test; the demo has too little history) |
| `interactions` | Extra points when two kinds of signal agree |
| `floors` | Minimum score when certain signals appear together |
