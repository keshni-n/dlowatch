import { AfterViewInit, ChangeDetectionStrategy, Component, ElementRef, OnDestroy, effect, input, output, viewChild } from '@angular/core';
import * as L from 'leaflet';

export interface MapCircle { latlon: [number, number]; radius_m: number; color: string; label?: string; dashed?: boolean; }
export interface MapPoint { latlon: [number, number]; color: string; label?: string; size?: number; ring?: string; }

const LEVEL_FILL: Record<string, string> = { low: '#CFDCE7', watch: '#F3D27E', high: '#EE9550', critical: '#B9351C' };

/** Leaflet map on OpenStreetMap tiles: zones, pipes, search circles, points and an optional pin. */
@Component({
  selector: 'dw-leak-map',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<div #box class="map" [style.height.px]="height()"></div>`,
  styles: [`.map { width: 100%; border-radius: 10px; overflow: hidden; border: 1px solid var(--line); z-index: 0; }`],
})
export class LeakMap implements AfterViewInit, OnDestroy {
  zones = input<GeoJSON.FeatureCollection | null>(null);
  pipes = input<GeoJSON.FeatureCollection | null>(null);
  highlightPipes = input<string[]>([]);
  circles = input<MapCircle[]>([]);
  points = input<MapPoint[]>([]);
  pin = input<[number, number] | null>(null);
  pickable = input(false);
  pipesClickable = input(false);
  height = input(420);
  fitTo = input<'zones' | 'pipes' | 'circles' | 'pin'>('zones');
  /** Optional: colour zones by another property (e.g. public "status") instead of leak level. */
  zoneProp = input('level');
  zoneColors = input<Record<string, string> | null>(null);
  /** Optional tooltip text per zone. Default shows the staff leak score. */
  zoneTooltip = input<((p: Record<string, unknown>) => string) | null>(null);

  zoneClick = output<string>();
  pipeClick = output<{ id: string; street?: string }>();
  picked = output<[number, number]>();

  private box = viewChild.required<ElementRef<HTMLDivElement>>('box');
  private map?: L.Map;
  private layers: Record<string, L.Layer | null> = {};
  private fitted = '';

  constructor() {
    effect(() => { this.drawZones(this.zones()); });
    effect(() => { this.drawPipes(this.pipes(), this.highlightPipes(), this.pipesClickable()); });
    effect(() => { this.drawCircles(this.circles(), this.points()); });
    effect(() => { this.drawPin(this.pin()); });
  }

  ngAfterViewInit() {
    this.map = L.map(this.box().nativeElement, { zoomControl: true, attributionControl: true, preferCanvas: true })
      .setView([-20.08, 57.58], 11);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(this.map);
    this.map.on('click', (e: L.LeafletMouseEvent) => {
      if (this.pickable()) this.picked.emit([+e.latlng.lat.toFixed(5), +e.latlng.lng.toFixed(5)]);
    });
    this.drawZones(this.zones());
    this.drawPipes(this.pipes(), this.highlightPipes(), this.pipesClickable());
    this.drawCircles(this.circles(), this.points());
    this.drawPin(this.pin());
    setTimeout(() => this.map?.invalidateSize(), 50);
  }

  ngOnDestroy() { this.map?.remove(); }

  private swap(key: string, layer: L.Layer | null) {
    if (!this.map) return;
    const old = this.layers[key];
    if (old) this.map.removeLayer(old);
    this.layers[key] = layer;
    if (layer) layer.addTo(this.map);
  }

  private fit(kind: string, bounds: L.LatLngBounds | null) {
    if (!this.map || !bounds || !bounds.isValid()) return;
    const key = kind + bounds.toBBoxString();
    if (this.fitTo() !== kind || this.fitted === key) return;
    this.fitted = key;
    this.map.fitBounds(bounds, { padding: [20, 20], maxZoom: 16 });
  }

  private drawZones(fc: GeoJSON.FeatureCollection | null) {
    if (!this.map) return;
    if (!fc) return this.swap('zones', null);
    const layer = L.geoJSON(fc, {
      style: f => ({
        color: '#FFFFFF', weight: 2,
        fillColor: (this.zoneColors() ?? LEVEL_FILL)[f?.properties?.[this.zoneProp()]] ?? '#CFDCE7', fillOpacity: 0.65,
      }),
      onEachFeature: (f, l) => {
        const p = f.properties as Record<string, unknown>;
        const tip = this.zoneTooltip();
        l.bindTooltip(tip ? tip(p) : `<b>${p['name']}</b> (${p['zone']})<br>Leak risk ${p['score']}/100`, { sticky: true });
        l.on('click', () => this.zoneClick.emit(String(p['zone'])));
      },
    });
    this.swap('zones', layer);
    this.fit('zones', layer.getBounds());
  }

  private drawPipes(fc: GeoJSON.FeatureCollection | null, hl: string[], clickable: boolean) {
    if (!this.map) return;
    if (!fc) { this.swap('pipes', null); this.swap('hl', null); return; }
    const set = new Set(hl);
    const base = L.geoJSON(fc, {
      filter: f => !set.has(f.properties?.['id']),
      style: () => ({ color: '#7F95A6', weight: clickable ? 3 : 1.6, opacity: 0.8 }),
      onEachFeature: (f, l) => {
        const p = f.properties as Record<string, unknown>;
        if (clickable) {
          l.bindTooltip(`${p['id']}${p['street'] ? ' · ' + p['street'] : ''}`, { sticky: true });
          l.on('click', (e: L.LeafletMouseEvent) => { L.DomEvent.stopPropagation(e); this.pipeClick.emit({ id: String(p['id']), street: p['street'] as string }); });
        }
      },
    });
    const top = L.geoJSON(fc, {
      filter: f => set.has(f.properties?.['id']),
      style: () => ({ color: '#E8590C', weight: 4.5, opacity: 0.95 }),
      onEachFeature: (f, l) => {
        const p = f.properties as Record<string, unknown>;
        l.bindTooltip(`Check ${p['id']}${p['street'] ? ' · ' + p['street'] : ''}`, { sticky: true });
      },
    });
    this.swap('pipes', base);
    this.swap('hl', top);
    this.fit('pipes', base.getBounds().extend(top.getBounds()));
  }

  private drawCircles(circles: MapCircle[], points: MapPoint[]) {
    if (!this.map) return;
    const g = L.featureGroup();
    for (const c of circles) {
      const circle = L.circle(c.latlon, {
        radius: c.radius_m, color: c.color, weight: 2, fillColor: c.color, fillOpacity: 0.08, dashArray: c.dashed ? '6 6' : undefined,
      });
      if (c.label) circle.bindTooltip(c.label, { sticky: true });
      g.addLayer(circle);
    }
    for (const p of points) {
      const m = L.circleMarker(p.latlon, {
        radius: p.size ?? 7, color: p.ring ?? '#FFFFFF', weight: 2.5, fillColor: p.color, fillOpacity: 1,
      });
      if (p.label) m.bindTooltip(p.label);
      g.addLayer(m);
    }
    this.swap('circles', circles.length || points.length ? g : null);
    if (circles.length || points.length) this.fit('circles', g.getBounds());
  }

  private drawPin(pin: [number, number] | null) {
    if (!this.map) return;
    if (!pin) return this.swap('pin', null);
    const g = L.featureGroup([
      L.circleMarker(pin, { radius: 16, color: '#0B5FA5', weight: 1, fillColor: '#0B5FA5', fillOpacity: 0.15 }),
      L.circleMarker(pin, { radius: 8, color: '#FFFFFF', weight: 3, fillColor: '#0B5FA5', fillOpacity: 1 }),
    ]);
    this.swap('pin', g);
    if (this.fitTo() === 'pin') this.map.setView(pin, Math.max(this.map.getZoom(), 16));
  }
}
