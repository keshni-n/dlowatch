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

**On a phone (same Wi-Fi):** run `npm run phone` and open `https://<laptop IP>:4200` (tap Advanced, then Proceed, at the certificate warning).
It uses https so the phone shares its location, and turns off live reload: otherwise the page reloads when you come back from the camera and the report is lost.

## Public side (no login)

A tab bar on phones (top menu on desktop) with:

- **Home:** live notices on the network, community numbers, your latest report, how reporting works
- **My area:** map of public notices (being checked, crew working, fixed recently). No scores or meter data
- **Report:** photo, place, what you see. Warns you if the problem is already known nearby
- **My reports:** every report sent from this phone, with live status (codes are kept on the phone)
- **Learn:** check for a leak at home with your water meter, signs of a street leak, saving water

Fallbacks: live info and report statuses are saved on the phone. If the server cannot be reached,
the pages show the last saved info with a note instead of an error. Learn works fully offline.

Optional settings (environment variables for the backend). If not set, the line is simply not shown:

| Variable | Shown as |
|---|---|
| `DLOWATCH_HOTLINE` | Emergency phone number on Home and Learn |
| `DLOWATCH_UTILITY_NAME` | Name of the water utility |

## Accounts

| Who | Login |
|---|---|
| Operator | operator@dlowatch.mu / demo123 |
| Admin (also has the System test page) | admin@dlowatch.mu / admin123 |
| Network manager (view only) | manager@dlowatch.mu / manager123 |
| Crew 1 | CW-0108 / PIN 1111 |
| Crew 2 | CW-0217 / PIN 1234 |
| Maintenance | CW-0350 / PIN 3333 |

These are created in the database (hashed) from `backend/config/people.json` on the first start. Set `DLOWATCH_SECRET` for a real deployment.

## How it runs (live, with a database)

There is no prepared story. DloWatch runs like a deployed system:

- **Database:** `backend/data/dlowatch.db` (SQLite, made on the first start). Alerts, public reports, crew jobs, crew findings, accounts and the network's leaks are saved there and survive restarts. Passwords and PINs are stored hashed.
- **Start fresh:** admin, **System test**, **Reset** (deletes alerts, reports, jobs and network leaks; accounts stay). Same from the command line: `python store.py reset`. `python store.py info` shows what is stored. Deleting the `data` folder starts from zero, accounts included.
- **Accounts:** created in the database from `config/people.json` on the very first start. Changing the file later does not change existing accounts.
- **The network:** the meters read a simulated network (there are no live CWA meters to connect yet). Leaks start in it **on their own, at random** (about 4 a day across the 8 zones, a third of them bursts). If the server was off, the leaks of that time are added when it starts again (up to 2 days).
- **The detector:** every 5 minutes it checks each zone's meters and scores them with the rules. It is never told about the leaks: it only sees the readings. It raises an alert when the score reaches Watch and there is a direct leak sign (night flow, a flow jump or a pressure drop).
  - Bursts: found within minutes (sudden flow jump plus a pressure drop at more than one sensor).
  - Slow leaks: found after the night readings show them, usually the next morning (night flow must stay high 2 nights to count fully).
- **Fixing:** when a crew logs "Leak found", the leak stops in the network and the zone's night flow goes back to normal. "No leak" or "Not a leak" pauses new alerts in that zone for 6 hours.
- **Settings:** `backend/config/network.json` (leaks per day, size mix, scan interval, cooldown, burst thresholds).
- **Meter faults:** a few meters have lasting problems (spikes, gaps, stuck, offline, drifting, counter reset, duplicate times), so the data-cleaning step always has work to do.

Tested over 6 virtual days with leaks starting at random: bursts alerted in a median of about 20 minutes, 25 of 31 leaks found, 1 false alarm (Watch level).

**For a live demo:** press **Reset** a few minutes before you start, so the screens are clean. Then, as admin, use **System test** to start a leak on the spot: a large leak (burst) is found within a minute.

### Good live demo flow

1. Staff: sign in as operator, show the overview and any alerts the network has raised by itself.
2. Open **Zones and meters** and show the raw vs cleaned chart (noisy data question answered).
3. In a second window signed in as **admin**, open **System test**, click a pipe, choose Large, start the test leak. The detector finds it from the meters and the alert appears on the operator's screen.
4. Walk through "How this score was worked out", then **Send crew** to Crew 1.
5. On a phone (or a small browser window): `/crew`, sign in as CW-0108, open the job, Leak found, Close the job.
6. Back on the dashboard: alert closed, result saved under **Field crews**. Restart the server: everything is still there.
7. Public: `/` on a phone, report a leak with a photo, get a code, track it.

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

## Repair-priority list and pipe suspects

**Repair list** (staff menu): open leaks ranked by what fixing them is worth.

- **Water saved if fixed** (m³/day and litres per hour), **households** in the search area,
  and **how easy** the repair is (pipe size, busy road, how much pipe to walk).
- Default weights: 70% water, 15% households, 15% ease. Sort by any one of them on the page.
- Settings in `backend/config/priority.json` (weights, household water use, block size).
  If the file is missing or broken, built-in defaults are used and a warning is logged.

**Where to check first** (alert page and crew phone): the search area is cut into blocks of about 300 m,
named after their main street. Each pipe is rated by how likely its area is and how close it is to the best guess,
so the blocks get a share that adds up to 100%. The crew can switch between "Most likely" and "Closest first".

Tested on 64 simulated leaks (8 per zone): the leak was in the **first block 27%** of the time,
in the **top 3 blocks 47%**, and in the **top 5 blocks 67%**.

## Water loss report (for the network manager)

Staff menu, **Water loss report** (`/dashboard/report`, API `GET /api/report?days=7`). The manager account lands here.

- Water put in, expected use, water lost, water saved by repairs, and how much the open leaks still lose each day.
- Water lost each day (chart), zones sorted by water lost, repairs done.
- **Download CSV** (opens in Excel) and **Print or save as PDF**.
- **Money** stays hidden until you set `water_cost_rs_per_m3` in `backend/config/report.json`. It is empty on purpose: put in the real CWA cost, not a guess.
- Lost = water put in minus the zone's normal demand pattern. Zones that used less than expected count as 0.

The manager can see every page but cannot send crews or close alerts (the server returns 403). Only the admin sees the System test page.

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

## Photo check on the phone

When someone takes a photo on the Report page, the phone checks it before sending (`frontend/src/app/shared/photo-check.ts`):

- **Too dark**, **nearly blank** (camera covered) or **blurry**: the person sees why, with **Retake** or **Keep it anyway**. Nothing is ever blocked.
- The result goes with the report as a hint. Staff see it under the photo on Public reports ("Photo check: blurry (sent anyway)"), and crews see it on the job. It never changes the score, and the server does not trust it.
- Limits are lenient on purpose, so a dark photo of a real burst at night still passes.
- **A trained model can be added later:** put it in `PHOTO_MODEL` in `photo-check.ts` (for example a small MobileNet trained on "water or leak", "pipe", "not relevant" photos). It then also warns when a photo for "Water on the road" or "Broken pipe" does not look relevant. Until then only the simple checks run.

## Public report safety

Settings in `backend/config/public.json`.

- Phone numbers and email addresses typed in the note are replaced with `[removed]` before saving. The person is told why.
- One phone (a random id it keeps, no personal data) can send 5 reports an hour and 15 a day. After that it is asked to call the hotline.
- The same photo cannot be sent again within 24 hours.
- Public reports alone can never go above Watch, and reports from one phone count as one person.

## "If not fixed"

Each open alert shows how much water it will lose if left for a week, a month and a year (and the money, once `water_cost_rs_per_m3` is set in `config/report.json`). It makes the urgency clear in one line.

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

## Impact and ethics

**Why it matters.** Mauritius loses a large share of its treated water before it reaches a tap. Finding leaks earlier saves water during dry seasons, and saves the energy and chemicals used to treat water that is then lost.

**What the system decides and what people decide.** The rules decide the score, and every point is shown with its reason. The AI can add at most 20 points and only when the meters agree. A person always decides whether to send a crew, and crews confirm what they found. Nothing is dug up because of the AI alone.

**Fairness.** The repair list ranks by water lost first (70%), then households affected and ease of repair. Areas with fewer sensors get bigger search areas, not lower priority. Public reports help everywhere, so areas with few sensors can still be heard.

**Privacy.**
- Public reports need no account, name or phone number. A phone is a random id it keeps on the device.
- Phone numbers and emails typed in notes are removed before saving.
- The public side shows leak notices (a zone, a street, or the middle of a group of reports), never a single report's location or who sent it. A report's status can only be seen with its code.
- Staff and crew accounts are separate, and the manager account can only view.

**Honesty.** The app says on screen that the network and readings are simulated. The evaluation lists its limits, and the BattLeDIM test shows where the system is weak (finding the exact place).

**Risks we know about.**
- A false alarm can send a crew for nothing. That is why public reports alone stay at Watch, and why an alert needs night flow to stay high 2 nights.
- People could spam reports. Hence the per-phone limits, the photo check, and "one phone = one person".
- The model was trained on simulated data. Real CWA data would need retraining, and crew results are kept as labels for that.

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

## Build for production (one server)

```bash
cd frontend
npm run build
```

Copy `frontend/dist/frontend/browser` to `backend/web`, then start only the backend:

```bash
cd backend
py -3.13 -m uvicorn server:app --host 0.0.0.0 --port 8000
```

Everything runs from `http://<this PC>:8000`: the app at `/`, the API at `/api`. Phones on the same Wi-Fi can open it too. The offline support (service worker) only runs in this production build.

Security notes:
- Login tokens are signed with `DLOWATCH_SECRET` if set, otherwise with a random key made on the first start and kept in the database.
- The original test routes from `api.py` (`/predict`, `/simulate-leak`, `/repairs`, the old test dashboard) have no login, so they are switched off. `DLOWATCH_LEGACY_ROUTES=on` brings them back for local testing.
- Change the account passwords in `config/people.json` before the very first start of a real deployment.

## Be honest in the pitch

The network is built from real OpenStreetMap streets, but pipe sizes, demand, reservoirs and elevations are assumed,
and the meter readings are simulated. It is not real CWA data. The screens are kept clean like a deployed system, so say this yourself in the pitch. Accounts and test results are only in this README, not on screen.
