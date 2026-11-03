"""
Draw the Port Louis demo network on a real OpenStreetMap map.

Usage:
  pip install folium
  python view_map.py    # makes output/network_map.html and opens it on localhost
"""

import functools
import http.server
import json
import socketserver
import webbrowser
from pathlib import Path

import folium

OUT = Path("output")
pipes = json.loads((OUT / "pipes.geojson").read_text())
nodes = json.loads((OUT / "nodes.geojson").read_text())
leak = json.loads((OUT / "leak_demo.json").read_text())
leak_pipe = leak["leak_pipe"]


def pipe_colour(diameter_mm):
    if diameter_mm >= 300:
        return "#08306b"   # big mains: dark blue
    if diameter_mm >= 200:
        return "#2171b5"
    return "#6baed6"       # small pipes: light blue


def pressure_colour(p):
    if p < 25:
        return "#d73027"   # low pressure: red
    if p < 35:
        return "#fdae61"
    return "#1a9850"       # good pressure: green


# centre the map on the network
lons = [f["geometry"]["coordinates"][0] for f in nodes["features"]]
lats = [f["geometry"]["coordinates"][1] for f in nodes["features"]]
m = folium.Map(location=[sum(lats) / len(lats), sum(lons) / len(lons)],
               zoom_start=16, tiles=None)
# Map tile servers block pages opened as local files (file://), so this script
# serves the page from http://localhost instead (see the bottom of the file).
folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)
folium.TileLayer("Esri.WorldStreetMap", name="Esri street map").add_to(m)

pipe_layer = folium.FeatureGroup(name="Pipes")
for f in pipes["features"]:
    p = f["properties"]
    is_leak = p["id"] == leak_pipe
    line = [[lat, lon] for lon, lat in f["geometry"]["coordinates"]]
    folium.PolyLine(
        line,
        color="#e6550d" if is_leak else pipe_colour(p["diameter_mm"]),
        weight=7 if is_leak else 2 + p["diameter_mm"] / 100,
        opacity=0.9,
        tooltip=(f"<b>{p['id']}</b>{' (LEAK DEMO)' if is_leak else ''}<br>"
                 f"{p['street'] or 'unnamed street'}<br>"
                 f"{p['diameter_mm']} mm, {p['length_m']:.0f} m"),
    ).add_to(pipe_layer)
pipe_layer.add_to(m)

node_layer = folium.FeatureGroup(name="Junctions (pressure at 8am)", show=False)
sensor_layer = folium.FeatureGroup(name="Pressure sensors")
for f in nodes["features"]:
    p = f["properties"]
    lon, lat = f["geometry"]["coordinates"]
    if p["id"] == "R_1":
        folium.Marker([lat, lon], tooltip="Reservoir (assumed)",
                      icon=folium.Icon(color="blue", icon="tint")).add_to(m)
        continue
    folium.CircleMarker(
        [lat, lon], radius=4, color=pressure_colour(p["pressure_8am_m"]),
        fill=True, fill_opacity=0.9,
        tooltip=f"{p['id']}: {p['pressure_8am_m']} m, elevation {p['elevation_m']} m",
    ).add_to(node_layer)
    if p["is_sensor"]:
        drop = leak["pressure_drop_mm_at_3am"].get(p["id"], 0)
        folium.CircleMarker(
            [lat, lon], radius=9, color="#d62728", weight=3, fill=False,
            tooltip=f"Sensor {p['id']}<br>Drop during leak: {drop} mm",
        ).add_to(sensor_layer)
node_layer.add_to(m)
sensor_layer.add_to(m)

folium.LayerControl(collapsed=False).add_to(m)
note = ("<div style='position:fixed;bottom:12px;left:12px;z-index:9999;background:white;"
        "padding:8px 10px;border-radius:6px;font:13px sans-serif;box-shadow:0 1px 4px #0003'>"
        "<b>Port Louis demo network</b> (illustrative, not real CWA pipes)<br>"
        f"Orange pipe = simulated leak on {leak_pipe}, "
        f"{leak['water_lost_m3_per_day']} m³/day lost</div>")
m.get_root().html.add_child(folium.Element(note))

path = OUT / "network_map.html"
m.save(str(path))
print(f"Saved {path}")

# Serve the output folder on localhost so map tiles load
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(OUT))
with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
    url = f"http://localhost:{httpd.server_address[1]}/network_map.html"
    print(f"Open {url}  (press Ctrl+C here to stop)")
    webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
