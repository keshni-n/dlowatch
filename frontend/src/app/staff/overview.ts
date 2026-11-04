import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { Api, errorText } from '../core/api';
import { DateTimePipe, NumPipe, LevelPipe, fmtDay } from '../core/format';
import { Overview, RepairList } from '../core/models';
import { LeakMap, MapPoint } from '../shared/leak-map';
import { ChartBand, LineChart } from '../shared/line-chart';

@Component({
  selector: 'dw-overview',
  imports: [RouterLink, LeakMap, LineChart, NumPipe, LevelPipe, DateTimePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (error()) { <div class="error">{{ error() }}</div> }
    @if (data(); as o) {
      <div class="stack page">
        <header class="row between">
          <div>
            <div class="small muted mono">{{ o.now | dateTime }}</div>
            <h1>Network overview</h1>
          </div>
          <div class="row">
            <button class="btn secondary" type="button" (click)="load()">Refresh</button>
          </div>
        </header>

        <section class="kpis six" aria-label="Key numbers">
          <div class="card kpi"><div class="label">Water put into network (24 h)</div><div class="value">{{ o.water_in_m3 | num }} m³</div><div class="sub">8 zone inlet meters, after cleaning</div></div>
          <div class="card kpi"><div class="label">Expected use (24 h)</div><div class="value">{{ o.expected_m3 | num }} m³</div><div class="sub">Normal demand pattern per zone</div></div>
          <div class="card kpi dark"><div class="label">Extra water, likely lost</div><div class="value sand">{{ o.extra_m3 | num }} m³</div><div class="sub">{{ o.extra_pct }}% more than expected</div></div>
          <div class="card kpi"><div class="label">Open leak alerts</div><div class="value">{{ o.alerts.length }}</div>
            <div class="sub warn">{{ levelLine() }}</div></div>
          <a class="card kpi link" routerLink="/dashboard/meters"><div class="label">Meters reporting</div><div class="value">{{ o.meters.total - o.meters.offline }}/{{ o.meters.total }}</div>
            <div class="sub">{{ o.meters.noisy }} noisy, {{ o.meters.offline }} offline or stuck</div></a>
          @if (o.loggers) {
            <a class="card kpi link" routerLink="/dashboard/loggers"><div class="label">Noise loggers hearing a leak</div><div class="value">{{ o.loggers.noisy }}</div>
              <div class="sub">{{ o.loggers.total }} loggers, {{ o.loggers.coverage_pct }}% of pipes heard</div></a>
          }
        </section>

        @if (repairs(); as rp) {
          <section class="card stack">
            <div class="row between"><h2>Fix first</h2><a routerLink="/dashboard/repairs" class="small">Full repair list</a></div>
            <div class="fix">
              @for (r of rp.items.slice(0, 3); track r.id; let i = $index) {
                <a class="fixrow" [routerLink]="['/dashboard/alerts', r.id]">
                  <span class="rk mono">{{ i + 1 }}</span>
                  <div class="grow"><b>{{ r.zone_name }}</b><div class="small muted">{{ r.suspects[0]?.street || r.headline }}</div></div>
                  <div class="r"><div class="mono"><b>{{ r.water_lost_m3_per_day ? (r.water_lost_m3_per_day | num) + ' m³/day' : '–' }}</b></div>
                    <div class="tiny muted">{{ r.households_est !== null ? (r.households_est | num) + ' households' : '' }}</div></div>
                </a>
              }
            </div>
          </section>
        }

        <section class="row top">
          <div class="card grow map-card">
            <div class="row between"><h2>Leak risk by zone</h2>
              <div class="legend"><span><i class="low"></i>Low</span><span><i class="watch"></i>Watch</span><span><i class="high"></i>High</span><span><i class="critical"></i>Critical</span></div>
            </div>
            <dw-leak-map [zones]="zones()" [points]="points()" [height]="440" (zoneClick)="openZone($event)" />
            <p class="tiny muted">Click a zone to see its alerts. Dots show the best guess for each open leak.</p>
          </div>
          <div class="card alerts-card">
            <div class="row between"><h2>Open alerts</h2><a routerLink="/dashboard/alerts" class="small">See all</a></div>
            <div class="stack list">
              @for (a of o.alerts; track a.id) {
                <a class="alert" [routerLink]="['/dashboard/alerts', a.id]">
                  <div class="score-box" [class]="a.level">{{ a.score }}</div>
                  <div class="grow">
                    <div class="row between"><b>{{ a.zone_name }} {{ a.zone }}</b><span class="pill" [class]="a.level">{{ a.level | level }}</span></div>
                    <div class="small muted">{{ a.headline }}</div>
                    @if (a.job) { <div class="tiny job">{{ a.job.crew }}: {{ a.job.status === 'done' ? 'done' : 'sent' }}</div> }
                  </div>
                </a>
              } @empty { <p class="muted">No open alerts. All zones look normal.</p> }
            </div>
          </div>
        </section>

        <section class="card">
          <div class="row between"><h2>Minimum night flow, whole network (02:00 to 04:00)</h2>
            <span class="small muted">Night is when real use is lowest, so extra flow is mostly leaks</span></div>
          <dw-line-chart [series]="nightSeries()" [baseline]="o.night_baseline_lps" baselineLabel="usual level" xMode="day" unit="L/s"
                         [bands]="nightBands()" [height]="210" ariaLabel="Network night flow for the last 7 nights" />
        </section>

        <section class="card">
          <h2>Zones</h2>
          <div class="table-wrap">
            <table class="data">
              <thead><tr><th>Zone</th><th>Area</th><th>Leak risk</th><th>Night flow vs usual</th><th>Average demand</th><th>Pipes</th></tr></thead>
              <tbody>
                @for (z of o.zones; track z.zone) {
                  <tr class="clickable" (click)="openZone(z.zone)">
                    <td class="mono"><b>{{ z.zone }}</b></td><td>{{ z.name }}</td>
                    <td><span class="pill" [class]="z.level">{{ z.level | level }} · {{ z.score }}</span></td>
                    <td class="mono">{{ z.night_pct > 0 ? '+' : '' }}{{ z.night_pct }}%</td>
                    <td class="mono">{{ z.avg_demand_lps }} L/s</td><td class="mono">{{ z.pipe_km }} km</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        </section>
      </div>
    } @else if (!error()) { <div class="skeleton">Loading the network...</div> }
  `,
  styles: [`
    .kpis.six { grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
    .page { gap: 22px; max-width: 1280px; }
    h1 { font-size: 32px; margin-top: 4px; }
    .top { align-items: stretch; gap: 16px; }
    .map-card { flex: 3 1 520px; display: flex; flex-direction: column; gap: 12px; }
    .alerts-card { flex: 2 1 340px; display: flex; flex-direction: column; gap: 12px; }
    .list { gap: 10px; }
    .alert { display: flex; gap: 14px; align-items: center; padding: 12px; border: 1px solid var(--line); border-radius: 10px; text-decoration: none; color: var(--text); }
    .alert:hover { border-color: var(--line-hover); background: var(--surface-2); color: var(--text); }
    .job { color: var(--link); margin-top: 3px; font-weight: 600; }
    .sand { color: var(--sand); }
    .warn { color: var(--high-text); font-weight: 600; }
    .link { text-decoration: none; color: var(--text); }
    .link:hover { border-color: var(--line-hover); color: var(--text); }
    .fix { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; }
    .fixrow { display: flex; gap: 12px; align-items: center; padding: 12px; border: 1px solid var(--line); border-radius: 10px; text-decoration: none; color: var(--text); }
    .fixrow:hover { border-color: var(--line-hover); color: var(--text); }
    .rk { width: 30px; height: 30px; border-radius: 99px; background: var(--inverse-bg); color: var(--inverse-text); display: flex; align-items: center; justify-content: center; font-weight: 700; flex: none; }
    .fixrow .r { text-align: right; }
    .legend { display: flex; gap: 12px; font-size: 12px; color: var(--text-2); flex-wrap: wrap; }
    .legend span { display: inline-flex; gap: 6px; align-items: center; }
    .legend i { width: 12px; height: 12px; border-radius: 3px; display: inline-block; }
    i.low { background: var(--low-bg); } i.watch { background: var(--watch-bg); } i.high { background: var(--high-bg); } i.critical { background: var(--crit-bg); }
  `],
})
export class OverviewPage {
  private api = inject(Api);
  private router = inject(Router);
  data = signal<Overview | null>(null);
  zones = signal<GeoJSON.FeatureCollection | null>(null);
  repairs = signal<RepairList | null>(null);
  error = signal('');

  levelLine = computed(() => {
    const l = this.data()?.levels ?? {};
    const parts = (['critical', 'high', 'watch'] as const).filter(k => l[k]).map(k => `${l[k]} ${k}`);
    return parts.join(' · ') || 'Nothing urgent';
  });
  points = computed<MapPoint[]>(() => (this.data()?.alerts ?? [])
    .filter(a => a.best_guess_latlon)
    .map(a => ({ latlon: a.best_guess_latlon!, color: '#0F1B24', ring: '#FFFFFF', size: 7, label: `${a.zone_name}: ${a.headline} (${a.score})` })));
  nightSeries = computed(() => {
    const n = this.data()?.night_flow ?? [];
    return [{ t: n.map(x => x.night), v: n.map(x => x.lps), color: '#0B5FA5', width: 3, label: 'Night flow' }];
  });
  nightBands = computed<ChartBand[]>(() => {
    const o = this.data();
    if (!o) return [];
    const high = o.night_flow.filter(n => n.lps > o.night_baseline_lps * 1.01);
    return high.length ? [{ from: high[0].night - 43200, to: high[high.length - 1].night + 43200, color: '#FDEBDD', label: `above usual since ${fmtDay(high[0].night)}` }] : [];
  });

  constructor() {
    this.load();
    const timer = setInterval(() => this.load(), 30000);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }

  async load() {
    try {
      const [o, z] = await Promise.all([this.api.overview(), this.api.mapZones()]);
      this.data.set(o);
      this.api.repairs().then(r => this.repairs.set(r)).catch(() => undefined);
      this.zones.set(z);
      this.error.set('');
    } catch (e) { this.error.set(errorText(e)); }
  }

  openZone(zone: string) {
    const a = this.data()?.alerts.find(x => x.zone === zone);
    if (a) this.router.navigate(['/dashboard/alerts', a.id]);
    else this.router.navigate(['/dashboard/alerts'], { queryParams: { zone } });
  }
}
