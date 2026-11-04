# DloWatch: water loss detection (Tech Wave 2026, Track 3)

One backend, one Angular web app, three kinds of users:

| User | Where | Login | URL |
|---|---|---|---|
| Utility staff (control room) | Computer | Email + password | `/login`, then `/dashboard` |
| Field crew | Phone (installable app) | Staff ID + PIN | `/crew` |
| Public | Phone, link or QR code | None | `/` (Home, My area, Report, My reports, Learn) |

Light and dark mode: follows the phone or computer setting until the person taps the sun/moon button (top bar on public pages, menu footer for staff, job list for crews). The choice is remembered on the device.

The app is a PWA: on a phone, open it in Chrome and tap **Add to Home Screen** to install it like an app.

## What is in this repo

```
backend/
  api.py, build_*.py, generate_leak_dataset.py, train_models.py   network, dataset and models (EPANET + WNTR)
  dlowatch.py      logins, rules engine, meter cleaning, reports, crews, jobs
  server.py        starts api.py + dlowatch.py together
  output_north/    network files (GeoJSON, .inp per zone, sensors, metrics)
frontend/          Angular 22 app (all screens), installable as a PWA
```

The 8 trained model files (`output_north/models/zone_Z*.joblib`, about 210 MB) are **not in git**.
Copy them into `backend/output_north/models/` from your local copy, or rebuild them:

```bash
cd backend
python generate_leak_dataset.py --network output_north
python train_models.py --network output_north
```

## Run it (2 terminals)

**1. Backend**

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn server:app --reload
```

Wait for `DloWatch: ready` (about 15 seconds). Endpoints: http://localhost:8000/docs

> Windows with more than one Python: use `py -3.13 -m uvicorn server:app --reload`
> so it runs the same Python that pip installed into.

**2. Frontend**

```bash
cd frontend
npm install
npm start
```

Open http://localhost:4200. `npm start` forwards `/api` to the backend on port 8000.

**On a phone (same Wi-Fi):** run `npm start -- --host 0.0.0.0` and open the "Network" address it prints.

## How the score works

The rules decide, the model only helps. Same idea as FraudLens ("the AI reads, the rules decide").

- **Versioned rules:** the points and limits live in `backend/rules/wl-*.json` (see `backend/rules/README.md`).
  New alerts use the newest version, and each alert keeps the version it was scored with.
  Never edit a published file: copy it to a new version. A broken file is skipped with a warning,
  and if none can be read a built-in copy is used.
- **The rules raise alerts, not the model.** If the model is switched off, times out (`DLOWATCH_MODEL_TIMEOUT`, default 5 s)
  or fails, alerts still come from the meters. The search area is then around the sensor with the biggest
  pressure drop, and the water lost comes from the inlet meter. Tested on 24 simulated leaks: about 700 m off
  without the model, about 300 m with it.
- **AI findings are checked:** AI-01 only counts if a meter signal agrees, AI-02 only if a trusted sensor or
  a public report inside that area saw something. Rejected findings are shown under "Not counted".
- **Same fact counts once:** night flow (NF-01), flow jump (NF-03) and water balance (WB-01) all measure extra
  water at the inlet meter. Only the strongest scores, the others show as "supports".
- **Switch:** admin only, System test page, "AI assist" On/Off. Or start with `DLOWATCH_MODEL=off`.

| Code | Signal | Points (wl-1.3) |
|---|---|---|
| NF-01 | Night flow (02:00 to 04:00) above the usual level | 14 / 22 / 30 |
| NF-02 | Still high several nights in a row | 8 |
| NF-03 | Flow jumped in the last hour (bursts) | 14 / 20 |
| WB-01 | More water going in than expected use (24 h) | 12 / 18 |
| PR-01 | Pressure dropped at trusted sensors | 12 / 18 |
| PR-02 | Sudden pressure drop with a flow jump (burst) | 20 |
| AI-01, AI-02 | Model says leak / points to one area (only when the meters agree) | max 20 together |
| PUB-01 | 3+ different phones reported nearby | 10 (22 if no meter alert) |
| IX-1 | Flow and pressure signals agree | 8 |

Bands: 20+ Watch, 45+ High, 70+ Critical. A burst (PR-02 with a flow signal) is always at least Critical.
Public reports alone are capped at Watch. Meters under 80% trust count for half, under 50% not at all.

## Persistence rule (ruleset wl-1.2)

High night flow on one night alone gives no points. It only counts once the zone has stayed high for 2 nights in a row (`"persistence": {"min_nights": 2}` in `rules/wl-1.2.json`). A single odd night (a pool filling, a fire hydrant test) no longer raises an alert. Bursts still alert at once through the pressure and flow signals.

## Ruleset wl-1.3 (tuned on a public benchmark)

Same points as wl-1.2, plus two checks chosen on BattLeDIM L-Town 2018 data:

- the usual night is the **same weekday over the last 4 weeks**, so busy weekend nights are not leaks
- a pressure sensor's drop only counts if it is **more than 3 times that sensor's usual night-to-night wobble**

Both need 4 weeks of history. The demo keeps 13 days, so the app falls back to wl-1.2 behaviour and says so in the alert's rule notes.

## How well does it work? (evaluation)

```
cd backend
python evaluate.py          # about 2 minutes, writes evaluation/results.json and results.md
```

It runs 352 simulated scenarios on the network (seed 7, water use +-5%, pressure sensors +-1 mm, the same as the training data). Each scenario lands on a random day of the past month, because water use differs from day to day.

| Test | Result |
|---|---|
| Slow leaks found | 62/64 (97%) |
| Bursts found | 64/64 (100%), all rated Critical |
| False alarms on normal days | 1/64 (2%) |
| False alarms with a faulty meter (spikes, stuck, drift, gaps, reset, offline) | 4/64 (6%) |
| Typical distance off, with the model | 396 m (80% within 670 m) |
| Leak pipe inside the search area, with the model | 90% |
| Leak in the top 3 / top 5 blocks to check | 44% / 56% |
| Leak size off by (median) | 1.7% |
| Model switched off (rules only) | 52/64 alerted, 944 m off, 60% inside the search area |
| One phone sending 16 reports | grouped as 1, no alert |
| Three phones reporting the same spot | grouped, stays below Watch until sensors agree |

### Public benchmark: BattLeDIM L-Town (rules only)

L-Town has 782 junctions, 905 pipes, 33 pressure sensors, a year of realistic demand and an official list of leaks. Tuned on 2018, tested once on 2019, on the same 308 nights for both rulesets (16 leaks):

| 2019 test | wl-1.2 | wl-1.3 |
|---|---|---|
| Leaks found within 3 days | 2/16 | 5/16 |
| Leaks found within 7 days | 3/16 | 7/16 |
| Leaks found within 30 days | 11/16 | 13/16 |
| False alarms, whole town | 9 in 10 months | 7 in 10 months |
| Sensor with the biggest drop, distance to the leak | 595 m | 1275 m |

Finding the place, 2019, on the night the alert opened (13 leaks). The model was trained on the utility's own L-Town model plus real 2018 night noise:

| | AI model | Rules only |
|---|---|---|
| Typical distance off | 803 m | 1249 m |
| Within 500 m | 5/13 | 2/13 |

The town is only about 2.7 km by 1.4 km, so this is still rough: say so in the pitch. The 2019 sensor files are on Zenodo, which was not reachable, so the data was rebuilt from the official network and leak list (EPANET instead of WNTR). Full write-up: `backend/evaluation/battledim.md`. To run it yourself: `backend/battledim/README.md`.

Limits, say these in the pitch: everything is simulated on an illustrative network; the model was trained on the same network; the meter faults tested are the kinds the cleaning step was built for; one leak at a time. Expect more false alarms and a bigger distance on real CWA data. The BattLeDIM test above shows what happens with realistic demand.

## How the demo data was made

Nothing here is real CWA data. This is how each part was made, so you can explain it:

| Part | How it was made |
|---|---|
| Streets | Real, from OpenStreetMap (north of Mauritius: Port Louis, Pamplemousses, Riviere du Rempart) |
| Pipes | One pipe per street. Sizes assumed from road type (300 mm main roads down to 100 mm), enlarged where pressure was too low |
| Zones | Split into 8 zones (DMAs), each with its own reservoir and an inlet flow meter, like a real district setup |
| Water use | Assumed demand spread by street length, with a 24-hour pattern (low at night, peaks at 7 to 8am and 6pm) |
| Elevations | Assumed unless a DEM file is given |
| Sensors | Pressure sensors spread evenly in each zone, plus the inlet meter |
| Training data for the AI | 24,000 EPANET runs: a leak of random size at a random junction (or no leak), at 3am and 8am, with +-5% water use and 1 mm sensor noise. The model learns which zone, which area, the best-guess point and the size |
| Meter readings in the app | The zone's demand pattern plus small day-to-day changes, plus the effect of any leak in the network. Then noise (about 5 mm for pressure loggers), the odd lost reading, and lasting faults on a few meters. Fixed per day, so past readings never change |
| Leaks | Start at random in the simulated network (`config/network.json`), each one a real EPANET emitter simulation. The detector only sees the meters |

The BattLeDIM L-Town test is different: that network, its demand and its leak list are a published benchmark, not ours.

## Noisy meter readings (DQ-01 to DQ-06)

Every reading is cleaned before any rule runs (`clean()` in `dlowatch.py`):

| Code | Problem | Fix |
|---|---|---|
| DQ-01 | Spikes | Replaced with the median of the 7 readings around it |
| DQ-02 | Missing readings | Gaps up to 30 min filled in a straight line, longer gaps left empty |
| DQ-03 | Stuck meter (same value 3 h+) | Readings ignored, meter marked faulty |
| DQ-04 | Impossible values (below zero, too big) | Dropped |
| DQ-05 | Drift against expected level | Trust lowered |
| DQ-06 | Duplicate or out of order timestamps | Removed and sorted |

Each meter gets a trust score (0 to 100%) that the rules use.
