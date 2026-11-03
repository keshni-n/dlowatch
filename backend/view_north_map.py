"""
Draw the North Mauritius demo network on a real map, coloured by zone.

Usage:
  pip install folium
  python view_north_map.py      # makes output_north/network_map.html, opens on localhost
"""

import functools
import http.server
import json
import socketserver
import webbrowser
from pathlib import Path

import folium

OUT = Path("output_north")
pipes = json.loads((OUT / "pipes.geojson").read_text())
nodes = json.loads((OUT / "nodes.geojson").read_text())
zones = json.loads((OUT / "zones.geojson").read_text())
leak = json.loads((OUT / "leak_demo.json").read_text())
colour = {f["properties"]["zone"]: f["properties"]["colour"] for f in zones["features"]}

m = folium.Map(location=[-20.07, 57.62], zoom_start=12, tiles=None, prefer_canvas=True)
folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)
folium.TileLayer("Esri.WorldStreetMap", name="Esri street map").add_to(m)

# Zone outlines (island view)
folium.GeoJson(
    zones, name="Zones",
    style_function=lambda f: {"color": f["properties"]["colour"], "weight": 2,
                              "fillColor": f["properties"]["colour"], "fillOpacity": 0.12},
    tooltip=folium.GeoJsonTooltip(
        fields=["zone", "junctions", "pipe_km", "avg_demand_lps", "inlet_meter"],
        aliases=["Zone", "Junctions", "Pipe length (km)", "Avg demand (L/s)", "Inlet meter"]),
).add_to(m)


# Pipes: one fast GeoJSON layer, coloured by zone, thicker for bigger pipes
def pipe_style(f):
    p = f["properties"]
    if p["id"] == leak["leak_pipe"]:
        return {"color": "#e6550d", "weight": 8, "opacity": 1}
    if p["status"] == "closed valve":
        return {"color": "#555555", "weight": 2, "dashArray": "4 4", "opacity": 0.8}
    return {"color": colour.get(p["zone"], "#3182bd"), "weight": 1 + p["diameter_mm"] / 120,
            "opacity": 0.85}


folium.GeoJson(
    pipes, name="Pipes", style_function=pipe_style,
    tooltip=folium.GeoJsonTooltip(
        fields=["id", "zone", "status", "street", "diameter_mm", "length_m"],
        aliases=["Pipe", "Zone", "Status", "Street", "Diameter (mm)", "Length (m)"]),
).add_to(m)

# Reservoirs and sensors
res_layer = folium.FeatureGroup(name="Reservoirs")
sensor_layer = folium.FeatureGroup(name="Pressure sensors")
drops = leak["pressure_drop_mm_at_3am_in_leak_zone"]
for f in nodes["features"]:
    p = f["properties"]
    lon, lat = f["geometry"]["coordinates"]
    if p.get("kind", "").startswith("reservoir"):
        folium.Marker([lat, lon], tooltip=f"Reservoir {p['zone']} (assumed), head {p['head_m']} m",
                      icon=folium.Icon(color="blue", icon="tint")).add_to(res_layer)
    elif p.get("is_sensor"):
        tip = f"Sensor {p['id']} (zone {p['zone']})"
        if p["id"] in drops:
            tip += f"<br>Drop during leak: {drops[p['id']]} mm"
        folium.CircleMarker([lat, lon], radius=6, color="#d62728", weight=3, fill=True,
                            fill_color="white", fill_opacity=1, tooltip=tip).add_to(sensor_layer)
res_layer.add_to(m)
sensor_layer.add_to(m)
folium.LayerControl(collapsed=False).add_to(m)

lz = leak["leak_zone"]
note = ("<div style='position:fixed;bottom:12px;left:12px;z-index:9999;background:white;"
        "padding:8px 10px;border-radius:6px;font:13px sans-serif;box-shadow:0 1px 4px #0003'>"
        "<b>North Mauritius demo network</b> (illustrative, not real CWA pipes)<br>"
        "Dashed grey = closed valves between zones<br>"
        f"Orange pipe = simulated leak in zone {lz}: inflow +"
        f"{leak['inflow_change_lps_at_3am_by_zone'][lz]} L/s at 3am, "
        f"{leak['water_lost_m3_per_day']} m³/day lost</div>")
m.get_root().html.add_child(folium.Element(note))

path = OUT / "network_map.html"
m.save(str(path))
print(f"Saved {path}")

handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(OUT))
with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
    url = f"http://localhost:{httpd.server_address[1]}/network_map.html"
    print(f"Open {url}  (press Ctrl+C here to stop)")
    webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
