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
