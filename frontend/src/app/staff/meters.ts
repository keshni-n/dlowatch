import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Api, errorText } from '../core/api';
import { TimePipe } from '../core/format';
import { MeterDetail, MetersResponse } from '../core/models';
import { ChartDot, ChartSeries, LineChart } from '../shared/line-chart';

const STATUS: Record<string, { text: string; cls: string }> = {
  healthy: { text: 'Healthy', cls: 'ok' }, noisy: { text: 'Noisy', cls: 'watch' }, drifting: { text: 'Drifting', cls: 'watch' },
  stuck: { text: 'Stuck', cls: 'critical' }, offline: { text: 'Offline', cls: 'critical' },
};

@Component({
  selector: 'dw-meters',
  imports: [LineChart, TimePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (error()) { <div class="error">{{ error() }}</div> }
    @if (data(); as d) {
      <div class="stack page">
        <header>
          <div class="small muted mono">Last 24 hours · readings every 5 min</div>
          <h1>Meter data quality</h1>
          <p class="muted lead">Every reading is cleaned before the leak checks run. A noisy or broken meter can never raise a leak alert on its own.</p>
        </header>

        <section class="kpis">
          <div class="card kpi"><div class="label">Meters</div><div class="value">{{ d.summary.total }}</div><div class="sub">8 inlet flow meters, 64 pressure sensors</div></div>
          <div class="card kpi"><div class="label">Healthy</div><div class="value blue">{{ d.summary.healthy }}</div></div>
          <div class="card kpi"><div class="label">Noisy or drifting</div><div class="value amber">{{ d.summary.noisy }}</div></div>
          <div class="card kpi"><div class="label">Offline or stuck</div><div class="value red">{{ d.summary.offline }}</div></div>
          <div class="card kpi dark"><div class="label">Readings fixed</div><div class="value sand">{{ d.summary.fixed }}</div><div class="sub">across all meters</div></div>
        </section>

        @if (meter(); as m) {
          <section class="card stack">
            <div class="row between">
              <div>
                <h2>Raw vs cleaned: {{ m.id }}, {{ m.zone_name }} {{ m.zone }}</h2>
                <div class="small muted">{{ m.type === 'flow' ? 'Zone inlet flow meter' : 'Pressure sensor' }} · trust {{ m.trust }}% · {{ m.problem }}</div>
              </div>
              <div class="row">
                <span class="pill" [class]="status(m.status).cls">{{ status(m.status).text }}</span>
                <label class="sr" for="hours">Time span</label>
                <select id="hours" class="input narrow" [value]="hours()" (change)="setHours(+$any($event.target).value)">
                  <option value="24">Last 24 h</option><option value="72">Last 72 h</option>
                </select>
              </div>
            </div>
            <dw-line-chart [series]="series()" [dots]="dots()" [unit]="m.unit" [height]="260" ariaLabel="Raw and cleaned meter readings" />
            <div class="legend small"><span><i class="raw"></i>Raw reading</span><span><i class="clean"></i>Cleaned</span><span><i class="dot"></i>Fixed point</span>
              @if (hidden()) { <span class="muted">{{ hidden() }} impossible values not drawn</span> }</div>
            @if (m.events.length) {
              <details>
                <summary class="small">What was fixed ({{ m.events.length }} most recent)</summary>
                <div class="table-wrap"><table class="data">
                  <thead><tr><th>Time</th><th>Rule</th><th>Raw value</th><th>Action</th></tr></thead>
                  <tbody>@for (e of m.events.slice().reverse(); track $index) {
                    <tr><td class="mono">{{ e.t | time }}</td><td class="mono">{{ e.code }}</td><td class="mono">{{ e.raw ?? '–' }}</td><td>{{ e.action }}</td></tr>
                  }</tbody>
                </table></div>
              </details>
            }
          </section>
        }

        <section class="card stack">
          <h2>What counts as noise, and what we do about it</h2>
          <div class="rules">
            @for (r of d.rules; track r.code) {
              <div class="rule">
                <div class="row between"><b>{{ r.name }}</b><span class="code mono">{{ r.code }}</span></div>
                <div class="small muted">{{ r.looks }}</div>
                <div class="small"><b>Fix:</b> {{ r.fix }}</div>
              </div>
            }
          </div>
        </section>

        <section class="row two">
          <div class="card grow table-card">
            <div class="row between"><h2>All meters</h2>
              <label class="row small">Show
                <select class="input narrow" [value]="filter()" (change)="filter.set($any($event.target).value)">
                  <option value="problems">Only problems</option><option value="all">All 72</option>
                </select></label></div>
            <div class="table-wrap">
              <table class="data">
                <thead><tr><th>Meter</th><th>Zone</th><th>Problem</th><th class="r">Trust</th><th>Used in leak checks?</th></tr></thead>
                <tbody>
                  @for (m of rows(); track m.id) {
                    <tr class="clickable" [class.sel]="m.id === selected()" (click)="pick(m.id)">
                      <td class="mono"><b>{{ m.id }}</b><div class="tiny muted">{{ m.type }}</div></td>
                      <td>{{ m.zone_name }} <span class="mono tiny">{{ m.zone }}</span></td>
                      <td>{{ m.problem }}</td><td class="r mono"><b>{{ m.trust }}%</b></td>
                      <td><span class="pill" [class]="m.used === 'yes' ? 'low' : m.used === 'half' ? 'watch' : 'critical'">{{ m.used === 'yes' ? 'Yes' : m.used === 'half' ? 'Half weight' : 'No' }}</span></td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          </div>
          <div class="card dark side stack">
            <h2>How noise is kept out of alerts</h2>
            <ul>
              <li>Meters under 80% trust count for half in every rule</li>
              <li>Meters that are stuck or offline count for nothing</li>
              <li>A leak needs the inlet meter and pressure sensors to agree, or several nights in a row</li>
              <li>If the problem is the meter, the job goes to the maintenance team, not a leak crew</li>
              <li>Filled gaps are marked, so a score never rests on made-up readings</li>
            </ul>
          </div>
        </section>
      </div>
    } @else if (!error()) { <div class="skeleton">Loading meters...</div> }
  `,
  styles: [`
    .page { gap: 20px; max-width: 1280px; }
    h1 { font-size: 32px; margin-top: 4px; }
    .lead { max-width: 720px; margin: 6px 0 0; }
    .blue { color: var(--link); } .amber { color: var(--watch-text); } .red { color: var(--crit-text); } .sand { color: var(--sand); }
    .narrow { width: auto; height: 40px; font-size: 14px; }
    .legend { display: flex; gap: 16px; flex-wrap: wrap; color: var(--text-2); }
    .legend span { display: inline-flex; gap: 6px; align-items: center; }
    .legend i { display: inline-block; }
    i.raw { width: 18px; height: 2px; background: #9FB2C0; } i.clean { width: 18px; height: 3px; background: var(--blue); }
    i.dot { width: 10px; height: 10px; border-radius: 99px; background: #C2410C; }
    .rules { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }
    .rule { border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; display: flex; flex-direction: column; gap: 6px; }
    .code { font-size: 12px; background: var(--chip); padding: 2px 8px; border-radius: 6px; }
    .two { gap: 16px; align-items: stretch; }
    .table-card { flex: 3 1 520px; }
    .side { flex: 2 1 300px; }
    .side ul { margin: 0; padding-left: 18px; color: #D3DEE6; display: flex; flex-direction: column; gap: 10px; font-size: 14px; }
    .r { text-align: right; }
    tr.sel td { background: var(--sel); }
    details summary { cursor: pointer; color: var(--link); font-weight: 600; min-height: 32px; }
    .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
  `],
})
export class MetersPage {
  private api = inject(Api);
  data = signal<MetersResponse | null>(null);
  meter = signal<MeterDetail | null>(null);
  selected = signal('');
  hours = signal(24);
  filter = signal<'problems' | 'all'>('problems');
  error = signal('');
  status = (s: string) => STATUS[s] ?? { text: s, cls: 'neutral' };

  rows = computed(() => (this.data()?.meters ?? []).filter(m => this.filter() === 'all' || m.status !== 'healthy'));

  series = computed<ChartSeries[]>(() => {
    const m = this.meter();
    if (!m) return [];
    const s = m.series;
    const good = s.v.filter((x): x is number => x !== null).sort((a, b) => a - b);
    const med = good[Math.floor(good.length / 2)] ?? 1;
    const keep = (x: number) => x >= 0 && x <= med * 4;
    const rv = (s.raw_v ?? []).map(x => (keep(x) ? x : null));
    return [
      { t: s.raw_t ?? [], v: rv, color: '#9FB2C0', width: 1.5 },
      { t: s.t, v: s.v, color: '#0B5FA5', width: 2.5 },
    ];
  });
  hidden = computed(() => {
    const m = this.meter();
    if (!m) return 0;
    const good = m.series.v.filter((x): x is number => x !== null).sort((a, b) => a - b);
    const med = good[Math.floor(good.length / 2)] ?? 1;
    return (m.series.raw_v ?? []).filter(x => x < 0 || x > med * 4).length;
  });
  dots = computed<ChartDot[]>(() => {
    const m = this.meter();
    if (!m) return [];
    const t0 = m.series.t[0];
    const byT = new Map(m.series.t.map((t, i) => [t, m.series.v[i]]));
    return m.events.filter(e => e.t >= t0 && (e.code === 'DQ-01' || e.code === 'DQ-02')).map(e => ({
      t: e.t, v: (byT.get(e.t) ?? e.raw ?? 0) as number, color: '#C2410C',
    }));
  });

  constructor() { this.load(); }

  async load() {
    try {
      const d = await this.api.meters();
      this.data.set(d);
      const first = d.meters.find(m => m.fixes['DQ-01'] && m.type === 'flow') ?? d.meters.find(m => m.status === 'noisy') ?? d.meters[0];
      if (first) await this.pick(first.id);
    } catch (e) { this.error.set(errorText(e)); }
  }

  async pick(id: string) {
    this.selected.set(id);
    try { this.meter.set(await this.api.meter(id, this.hours())); }
    catch (e) { this.error.set(errorText(e)); }
    window.scrollTo({ top: 260, behavior: 'smooth' });
  }

  setHours(h: number) {
    this.hours.set(h);
    if (this.selected()) this.pick(this.selected());
  }
}
