# Port Louis demo network (EPANET)

Builds an illustrative EPANET water network on the real streets of central Port Louis.

**Important:** this is not real CWA pipe data. The streets are real (OpenStreetMap). Pipe sizes, demand, the reservoir and (unless you pass a DEM) elevations are assumptions. Say this on the dashboard and in the pitch.

## Setup

```bash
pip install -r requirements.txt
python build_port_louis_network.py
```

Needs internet the first time (downloads streets from OpenStreetMap). It also saves `output/port_louis_streets.graphml`, so you can rebuild offline later:

```bash
python build_port_louis_network.py --graphml output/port_louis_streets.graphml
```

## What it does

1. Downloads drivable streets within 700 m of Place d'Armes.
2. Turns every street junction into a pipe junction and every street into a pipe.
3. Sets assumed pipe sizes by road type (main roads 300 mm down to 100 mm for service roads).
4. Spreads about 25 L/s of demand across junctions by street length, with a 24-hour pattern (low at night, peaks at 7 to 8am and 6pm).
5. Adds a reservoir next to the highest point.
6. Runs EPANET, makes pipes bigger where they lose too much pressure, then sets the reservoir level so the lowest pressure is 20 m.
7. Spreads 10 pressure sensors evenly across the network, plus a flow meter on the supply pipe.
8. Runs one leak demo (an EPANET emitter in the middle of a random pipe) and records the water lost and the pressure drop at each sensor at 3am.

## Output files (in `output/`)

| File | Use |
|---|---|
| `port_louis_demo.inp` | The EPANET network. Open in EPANET desktop or load with WNTR |
| `pipes.geojson`, `nodes.geojson` | Lat/lon for Leaflet. Pipes follow the real street shapes |
| `sensors.json` | Sensor junction IDs + flow meter pipe |
| `baseline_pressure.csv` | Hourly pressure at sensors with no leak |
| `leak_demo.json` | Leak pipe, m³/day lost, pressure drop per sensor (mm) |
| `network_preview.png` | Quick check picture, good for the deck |

## Useful options

```bash
--radius 900          # bigger area (more pipes, slower leak runs)
--sensors 12          # number of pressure sensors
--demand 30           # total average demand in L/s
--dem srtm.tif        # real elevations from a GeoTIFF (e.g. SRTM 30 m from OpenTopography)
--leak-pipe P_42      # choose the leak pipe
--leak-coeff 0.0005   # bigger leak (default 0.0003 gives about 1.5 to 2 L/s)
```

## Using it in the project

- **Leak dataset:** load `port_louis_demo.inp` in your leak simulator instead of L-Town, same code. Use `EpanetSimulator` with emitters for speed.
- **Dashboard:** load `pipes.geojson` and `nodes.geojson` in Leaflet on OpenStreetMap tiles. Colour suspect pipes from the model output by pipe `id`.
- **Pressure drops are small** (millimetres to a few centimetres for a 2 L/s leak). That is realistic, and it is why the model needs many simulated leaks to learn the pattern.

## Line for the README / pitch

"The Port Louis demo network was generated from OpenStreetMap streets using osmnx and simulated with EPANET via WNTR. Pipe sizes, demand and elevations are assumed; it is illustrative, not real CWA infrastructure."

Map data © OpenStreetMap contributors.

---

# North Mauritius network (zones)

Same idea for the whole north: roughly Port Louis, Pamplemousses and Riviere du Rempart (a bounding box, not exact district borders).

```bash
python build_north_network.py          # downloads streets (a few minutes), builds everything
pip install folium
python view_north_map.py               # opens the map on localhost
```

Rebuild offline later: `python build_north_network.py --graphml output_north/north_streets.graphml`

## What's different from the Port Louis version

- **Zones (DMAs):** the network is split into 8 zones (`--zones`). Each zone has its own reservoir and an inlet flow meter (`P_IN_Z1` ...). Pipes between zones are closed valves, like a real DMA setup.
- **Two-step leak finding:** a leak raises the inflow of only its own zone. So the AI can first say which zone (from the inlet meters), then which pipe inside that zone (from the pressure sensors).
- **Demand follows housing:** residential streets get most demand, main roads through cane fields get little. Default total is 400 L/s for the region (assumed, `--demand`).
- **Service roads removed** to keep the network a manageable size.
- **One `.inp` per zone** in `output_north/zones/`. Use these for the leak dataset: much faster than simulating the whole north each time.

## Output files (in `output_north/`)

| File | Use |
|---|---|
| `north_mauritius.inp` | Whole network, opens in EPANET desktop |
| `zones/zone_Z1.inp` ... | One network per zone, for fast leak simulations |
| `pipes.geojson`, `nodes.geojson` | Map data with a `zone` field on every pipe and junction |
| `zones.geojson` | Zone outlines + stats, for the island view in the dashboard |
| `sensors.json` | Pressure sensors per zone + inlet meters |
| `leak_demo.json` | One leak: which zone, inflow change per zone, pressure drops |
| `network_preview.png` | Picture coloured by zone |

## Real elevations (recommended for the north)

Without `--dem`, ground height is assumed to rise 4 m per km going south. That's rough. For real heights:

1. Go to opentopography.org (free account) and choose **SRTM GL1 (30 m)**.
2. Draw a box over the north of Mauritius and download as **GeoTIFF**.
3. Run `python build_north_network.py --graphml output_north/north_streets.graphml --dem your_file.tif`

## Line for the README / pitch

"The North Mauritius demo network was generated from OpenStreetMap streets using osmnx, split into district metered areas, and simulated with EPANET via WNTR. Pipe sizes, demand, reservoirs and elevations are assumed; it is illustrative, not real CWA infrastructure."

---

# Leak dataset and models

Run these after building a network (Port Louis: `--network output`, North: `--network output_north`).

```bash
python generate_leak_dataset.py --network output_north     # thousands of simulated leaks -> leak_dataset.csv
python train_models.py --network output_north --demo       # trains models -> models/ + metrics.json
```

- `generate_leak_dataset.py`: 3,000 leaks per zone by default (10% no-leak). Records pressure drops at sensors and the zone inflow change at 3am and 8am. Add `--demand-noise 0.05` for a harder, more realistic dataset.
- `train_models.py`: per zone, a detector (leak or not), a locator (top-3 search areas + pipes to check, plus a best-guess point) and a sizer (L/s and m³/day). Scores are saved in `models/metrics.json`.
- More sensors per zone (`--sensors-per-zone 8` when building the north network) gives smaller search areas and a smaller location error.
- Use in your API: `from train_models import load_models, predict`.

---

# Backend API (FastAPI)

Serves the network, the models and leak simulations to the Angular dashboard. Run it after the network is built and the models are trained.

```bash
uvicorn api:app --reload                     # North network (output_north)
set NETWORK_DIR=output&& uvicorn api:app --reload   # Port Louis, Windows PowerShell: $env:NETWORK_DIR="output"; uvicorn api:app --reload
```

Open **http://localhost:8000/docs** to try every endpoint in the browser.

| Endpoint | What it does |
|---|---|
| `GET /health` | Running? Which network and zones |
| `GET /zones` | Zone outlines + stats (island view) |
| `GET /pipes?zone=Z3` | Pipes, all or one zone (GeoJSON for Leaflet) |
| `GET /nodes?zone=Z3` | Junctions, reservoirs, sensors |
| `GET /sensors` | Sensors per zone + inlet meters |
| `POST /predict` | `{"zone": "Z3", "readings": {...}}` → leak probability, top-3 areas with pipes to check, L/s and m³/day |
| `POST /simulate-leak` | `{"pipe_id": "P_1234", "size": "medium"}` → runs EPANET with a leak on that pipe, returns the real answer and the model's answer side by side, and adds an alert |
| `GET /repairs` | Open alerts, biggest water loss first |
| `POST /repairs/{id}/resolve` | Mark an alert as repaired |

- Positions come back in both UTM metres (`_xy`) and lat/lon (`_latlon`). Use lat/lon in Leaflet.
- Alerts are kept in memory, so they reset when the server restarts. Fine for the demo.
- CORS is open, so the Angular app on localhost:4200 can call it.
