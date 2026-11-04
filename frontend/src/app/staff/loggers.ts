import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Api, errorText } from '../core/api';
import { Logger, LoggerDetail, LoggersResponse, LoggerStatus } from '../core/models';
import { LeakMap, MapCircle, MapPoint } from '../shared/leak-map';
import { ChartDot, ChartSeries, LineChart } from '../shared/line-chart';

const STATUS: Record<LoggerStatus, { text: string; cls: string; color: string }> = {
  noisy: { text: 'Leak noise', cls: 'high', color: '#6D28D9' },
  'loud once': { text: 'Loud once', cls: 'watch', color: '#A78BFA' },
  quiet: { text: 'Quiet', cls: 'low', color: '#B8A9EC' },
};

/** Acoustic noise loggers: where they are, which ones hear a leak, and each logger's last 14 nights. */
@Component({
  selector: 'dw-loggers',
  imports: [LeakMap, LineChart],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (error()) { <div class="error">{{ error() }}</div> }
    @if (data(); as d) {
      <div class="stack page">
        <header>
          <div class="small muted mono">Recorded every night from 02:00 to 04:00</div>
          <h1>Noise loggers</h1>
          <p class="muted lead">Loggers in valve and hydrant chambers listen to the pipes at night, when the streets are quiet.
            Escaping water makes a steady hiss, so a logger near a leak stays loud night after night.</p>
        </header>

        <section class="kpis">
          <div class="card kpi"><div class="label">Loggers</div><div class="value">{{ d.summary.total }}</div><div class="sub">across 8 zones</div></div>
          <div class="card kpi"><div class="label">Pipes they can hear</div><div class="value blue">{{ d.coverage.overall }}%</div><div class="sub">within {{ d.hearing_range_m }} m of a logger</div></div>
          <div class="card kpi"><div class="label">Leak noise</div><div class="value red">{{ d.summary.noisy }}</div><div class="sub">loud 2 nights or more</div></div>
          <div class="card kpi"><div class="label">Loud once</div><div class="value amber">{{ d.summary.loud_once }}</div><div class="sub">often traffic or a pump, not counted yet</div></div>
          <div class="card kpi dark"><div class="label">Quiet</div><div class="value sand">{{ d.summary.quiet }}</div><div class="sub">no leak close to them</div></div>
        </section>

        <section class="card stack">
          <div class="row between">
            <h2>{{ zone() ? zoneName() + ' ' + zone() : 'Whole network' }}</h2>
            <div class="row">
              <div class="legend small">
                <span><i [style.background]="color('noisy')"></i>Leak noise</span>
                <span><i [style.background]="color('loud once')"></i>Loud once</span>
                <span><i class="sm" [style.background]="color('quiet')"></i>Quiet</span>
              </div>
              <label class="sr" for="zone">Zone</label>
              <select id="zone" class="input narrow" [value]="zone()" (change)="zone.set($any($event.target).value)">
                <option value="">All zones</option>
                @for (z of zoneList(); track z.zone) { <option [value]="z.zone">{{ z.name }} {{ z.zone }} · {{ d.coverage.zones[z.zone] }}% heard</option> }
              </select>
            </div>
          </div>
          <dw-leak-map [zones]="zones()" [points]="points()" [circles]="ring()" [height]="440" fitTo="zones" zoneProp="none" [zoneTooltip]="zoneTip" />
        </section>

        @if (logger(); as l) {
          <section class="card stack">
            <div class="row between">
              <div>
                <h2>{{ l.id }}, {{ l.zone_name }} {{ l.zone }}</h2>
                <div class="small muted">At junction <span class="mono">{{ l.junction }}</span>{{ l.main_road ? ', next to a main road' : '' }} ·
                  usual night {{ l.usual_db }} dB · last night {{ l.last_db }} dB ({{ sign(l.change_db) }} dB)</div>
              </div>
              <span class="pill" [class]="st(l.status).cls">{{ st(l.status).text }}{{ l.loud_nights > 1 ? ', ' + l.loud_nights + ' nights' : '' }}</span>
            </div>
            <dw-line-chart [series]="series()" [dots]="dots()" [baseline]="l.usual_db" baselineLabel="usual night" xMode="day" unit="dB"
                           [height]="230" ariaLabel="Noise level for the last 14 nights" />
            <div class="small muted">{{ explain(l) }}</div>
          </section>
        }

        <section class="row two">
          <div class="card grow table-card">
            <div class="row between"><h2>{{ filter() === 'loud' ? 'Loggers to look at' : 'All loggers' }}</h2>
              <label class="row small">Show
                <select class="input narrow" [value]="filter()" (change)="filter.set($any($event.target).value)">
                  <option value="loud">Only loud ones</option><option value="all">All</option>
                </select></label></div>
            <div class="table-wrap">
              <table class="data">
                <thead><tr><th>Logger</th><th class="wide">Zone</th><th class="r wide">Last night</th><th class="r wide">Usual</th><th class="r">Change</th><th>Status</th></tr></thead>
                <tbody>
                  @for (l of rows(); track l.id) {
                    <tr class="clickable" [class.sel]="l.id === selected()" (click)="pick(l.id)">
                      <td class="mono nowrap"><b>{{ l.id }}</b></td>
                      <td class="wide">{{ l.zone_name }} <span class="mono tiny">{{ l.zone }}</span></td>
                      <td class="r mono wide">{{ l.last_db }} dB</td><td class="r mono muted wide">{{ l.usual_db }} dB</td>
                      <td class="r mono"><b>{{ sign(l.change_db) }}</b></td>
                      <td><span class="pill" [class]="st(l.status).cls">{{ st(l.status).text }}<span class="wide">{{ l.loud_nights > 1 ? ', ' + l.loud_nights + ' nights' : '' }}</span></span></td>
                    </tr>
                  } @empty {
                    <tr><td colspan="6" class="muted">No logger was loud last night{{ zone() ? ' in this zone' : '' }}.</td></tr>
                  }
                </tbody>
              </table>
            </div>
          </div>
          <div class="card dark side stack">
            <h2>How loggers are used</h2>
            <ul>
              <li>A logger counts once it is at least 4 dB louder than its usual night, 2 nights in a row, and steady. Traffic or a pump jumps around, a leak does not</li>
              <li>One loud night is ignored: traffic, roadworks or a night pump do not last</li>
              <li>Loggers that hear a leak where the meters point move the search area onto them, so the crew walks a few hundred metres, not a whole zone</li>
              <li>Pipes right next to a quiet logger are checked last, because a leak there would have been heard</li>
              <li>One logger alone cannot raise an alert. Two loggers, or a logger plus flow or pressure, can. Loggers alone never go above Watch</li>
            </ul>
          </div>
        </section>
      </div>
    } @else if (!error()) { <div class="skeleton">Loading loggers...</div> }
  `,
  styles: [`
    .page { gap: 20px; max-width: 1280px; }
    h1 { font-size: 32px; margin-top: 4px; }
    .lead { max-width: 760px; margin: 6px 0 0; }
    .blue { color: var(--link); } .amber { color: var(--watch-text); } .red { color: var(--crit-text); } .sand { color: var(--sand); }
    .narrow { width: auto; height: 40px; font-size: 14px; }
    .legend { display: flex; gap: 14px; flex-wrap: wrap; color: var(--text-2); }
    .legend span { display: inline-flex; gap: 6px; align-items: center; }
    .legend i { display: inline-block; width: 11px; height: 11px; border-radius: 99px; }
    .legend i.sm { width: 8px; height: 8px; }
    .two { gap: 16px; align-items: stretch; }
    .table-card { flex: 3 1 520px; }
    .side { flex: 2 1 300px; }
    .side ul { margin: 0; padding-left: 18px; color: #D3DEE6; display: flex; flex-direction: column; gap: 10px; font-size: 14px; }
    .r { text-align: right; }
    tr.sel td { background: var(--sel); }
    .nowrap { white-space: nowrap; }
    @media (max-width: 640px) { .wide { display: none; } td, th { padding-left: 6px; padding-right: 6px; } }
    .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
  `],
})
export class LoggersPage {
  private api = inject(Api);
  data = signal<LoggersResponse | null>(null);
  allZones = signal<GeoJSON.FeatureCollection | null>(null);
  logger = signal<LoggerDetail | null>(null);
  selected = signal('');
  zone = signal('');
  filter = signal<'loud' | 'all'>('loud');
  error = signal('');
  st = (s: LoggerStatus) => STATUS[s];
  color = (s: LoggerStatus) => STATUS[s].color;
  zoneTip = (p: Record<string, unknown>) => {
    const z = String(p['zone']);
    const n = (this.data()?.loggers ?? []).filter(l => l.zone === z);
    return `<b>${p['name'] ?? z}</b> (${z})<br>${n.length} loggers, ${this.data()?.coverage.zones[z] ?? 0}% of pipes heard`;
  };
  sign = (x: number) => (x > 0 ? '+' : '') + x.toFixed(1);

  zoneList = computed(() => {
    const seen = new Map<string, string>();
    for (const l of this.data()?.loggers ?? []) seen.set(l.zone, l.zone_name);
    return [...seen].map(([zone, name]) => ({ zone, name })).sort((a, b) => a.zone.localeCompare(b.zone));
  });
  zoneName = computed(() => this.zoneList().find(z => z.zone === this.zone())?.name ?? '');
  inZone = computed(() => (this.data()?.loggers ?? []).filter(l => !this.zone() || l.zone === this.zone()));
  rows = computed(() => this.inZone()
    .filter(l => this.filter() === 'all' || l.status !== 'quiet')
    .sort((a, b) => rank(a) - rank(b) || b.change_db - a.change_db));
  zones = computed(() => {
    const z = this.allZones();
    if (!z || !this.zone()) return z;
    return { ...z, features: z.features.filter(f => f.properties?.['zone'] === this.zone()) };
  });
  points = computed<MapPoint[]>(() => this.inZone()
    .slice().sort((a, b) => rank(b) - rank(a))     // draw quiet ones first, loud ones on top
    .map(l => ({
      latlon: [l.lat, l.lon], color: STATUS[l.status].color,
      size: l.status === 'noisy' ? 8 : l.status === 'loud once' ? 6 : 4,
      ring: l.id === this.selected() ? '#0F1B24' : undefined,
      label: `${l.id}: ${STATUS[l.status].text.toLowerCase()}, ${this.sign(l.change_db)} dB`,
    })));
  ring = computed<MapCircle[]>(() => {
    const l = this.logger();
    const r = this.data()?.hearing_range_m ?? 300;
    return l ? [{ latlon: [l.lat, l.lon], radius_m: r, color: STATUS[l.status].color, dashed: true, label: `${l.id} hears about ${r} m around it` }] : [];
  });
  series = computed<ChartSeries[]>(() => {
    const l = this.logger();
    return l ? [{ t: l.nights.map(n => n.night), v: l.nights.map(n => n.db), color: '#0B5FA5', width: 2.5, label: 'Noise (dB)' }] : [];
  });
  dots = computed<ChartDot[]>(() => {
    const l = this.logger();
    return l ? l.nights.filter(n => n.db - l.usual_db >= 4).map(n => ({ t: n.night, v: n.db, color: '#6D28D9' })) : [];
  });

  constructor() { this.load(); }

  async load() {
    try {
      const [d, z] = await Promise.all([this.api.loggers(), this.api.mapZones()]);
      this.data.set(d);
      this.allZones.set(z);
      const first = d.loggers.filter(l => l.status === 'noisy').sort((a, b) => b.change_db - a.change_db)[0]
        ?? d.loggers.filter(l => l.status === 'loud once').sort((a, b) => b.change_db - a.change_db)[0];
      if (first) await this.pick(first.id, false);
    } catch (e) { this.error.set(errorText(e)); }
  }

  async pick(id: string, scroll = true) {
    this.selected.set(id);
    try { this.logger.set(await this.api.logger(id)); }
    catch (e) { this.error.set(errorText(e)); }
    if (scroll) window.scrollTo({ top: 760, behavior: 'smooth' });
  }

  explain(l: Logger) {
    if (l.status === 'noisy') return `Loud ${l.loud_nights} nights in a row. A steady rise like this is typical of a leak within a few hundred metres. It counts in the leak score for ${l.zone_name}.`;
    if (l.status === 'loud once') return 'Loud last night only. It counts if it stays loud tomorrow night.';
    return 'Close to its usual level. Pipes right next to it are checked last when there is a leak in this zone.';
  }
}

function rank(l: Logger) { return l.status === 'noisy' ? 0 : l.status === 'loud once' ? 1 : 2; }
