import { ChangeDetectionStrategy, Component, ElementRef, computed, effect, inject, input, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { Api, errorText } from '../core/api';
import { Auth } from '../core/auth';
import { AgoPipe, DateTimePipe, LevelPipe, NumPipe, fmtDay } from '../core/format';
import { AlertDetail, Crew, CrewResult } from '../core/models';
import { LeakMap, MapCircle, MapPoint } from '../shared/leak-map';
import { ChartBand, ChartSeries, LineChart } from '../shared/line-chart';

const SRC_CLASS: Record<string, string> = { Rules: 'low', 'AI model': 'ai', Combo: 'watch', Public: 'ok' };
const AREA_COLORS = ['#E8590C', '#B9351C', '#7F95A6'];

@Component({
  selector: 'dw-alert-detail',
  imports: [RouterLink, FormsModule, LeakMap, LineChart, LevelPipe, NumPipe, AgoPipe, DateTimePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (error()) { <div class="error">{{ error() }}</div> }
    @if (a(); as a) {
      <div class="stack page">
        <div class="small muted"><a routerLink="/dashboard">Overview</a> / <a routerLink="/dashboard/alerts">Leak alerts</a> / {{ a.zone_name }} {{ a.zone }}</div>

        <section class="hero">
          <div class="hero-main">
            <div class="row"><span class="muted-light">Leak risk</span><span class="pill" [class]="'solid-' + a.level">{{ a.level | level }}</span>
              @if (a.status === 'resolved') { <span class="pill ok">Closed</span> }</div>
            <div class="big"><span class="mono">{{ a.score }}</span><span class="of">/ 100</span></div>
            <h1>{{ a.zone_name }}, zone {{ a.zone }}</h1>
            <p class="muted-light">{{ a.headline }}</p>
          </div>
          <div class="hero-stats">
            <div class="stat"><div class="label">Started</div><div class="v">{{ a.started | ago }}</div><div class="label">{{ a.started | dateTime }}</div></div>
            <div class="stat"><div class="label">Extra water lost</div>
              <div class="v sand mono">{{ a.model.water_lost_m3_per_day ? (a.model.water_lost_m3_per_day | num) : '–' }} <small>m³/day</small></div>
              <div class="label">{{ a.model.leak_flow_lps ? (a.model.leak_flow_lps | num: 2) + ' L/s, ' + (a.search_source === 'model' ? 'AI estimate' : 'from the inlet meter') : 'No estimate' }}</div></div>
            <div class="stat"><div class="label">Most likely spot</div>
              <div class="v small-v">{{ a.areas[0]?.streets?.slice(0, 2)?.join(', ') || (a.kind === 'public' ? 'Where people reported' : '–') }}</div>
              <div class="label">{{ a.areas[0] ? (a.areas[0].from_loggers ? 'Leak noise at ' + a.areas[0].from_loggers[0] : a.areas[0].from_sensors ? 'Around sensor ' + a.areas[0].from_sensors[0] : 'Area ' + a.areas[0].area) + ', ' + (a.areas[0].radius_m | num) + ' m radius' : '' }}</div></div>
          </div>
        </section>

        @if (a.if_not_fixed; as n) {
          <section class="card notfixed" aria-label="If this leak is not fixed">
            <b>If not fixed</b>
            <span><span class="mono">{{ n.week_m3 | num }} m³</span> lost in a week</span>
            <span><span class="mono">{{ n.month_m3 | num }} m³</span> in a month</span>
            <span><span class="mono">{{ n.year_m3 | num }} m³</span> in a year, about {{ n.olympic_pools_per_year }} Olympic pools</span>
            @if (n.money; as m) { <span><span class="mono">{{ m.currency }} {{ m.month | num }}</span> a month</span> }
          </section>
        }
        @if (a.crew_result; as cr) {
          <section class="card stack" aria-label="Crew result">
            <div class="row between">
              <h2>Crew result</h2>
              <span class="small muted">{{ cr.crew }} · job <span class="mono">{{ cr.job }}</span> · closed {{ cr.finished | dateTime }}</span>
            </div>
            <div><b>{{ cr.outcome_text }}</b>{{ cr.cause ? ', ' + cr.cause : '' }}
              @if (cr.found_at || cr.fixed_at) { <span class="small muted"> · {{ cr.found_at ? 'found ' + cr.found_at : '' }}{{ cr.found_at && cr.fixed_at ? ', ' : '' }}{{ cr.fixed_at ? 'fixed ' + cr.fixed_at : '' }}</span> }
            </div>
            @if (cr.notes) { <div class="small">"{{ cr.notes }}"</div> }
            @if (cr.has_photo_before || cr.has_photo_after) {
              <div class="crew-photos">
                @for (w of photoSlots(cr); track w.key) {
                  <figure>
                    @if (crewPhotos()[w.key]; as src) {
                      <img [src]="src" [alt]="w.label + ' photo from the crew'" [class.big]="bigPhoto() === w.key" (click)="bigPhoto.set(bigPhoto() === w.key ? '' : w.key)">
                    } @else { <div class="ph tiny muted">Loading the photo...</div> }
                    <figcaption class="tiny muted">{{ w.label }}</figcaption>
                  </figure>
                }
              </div>
              <div class="tiny muted">Tap a photo to see it bigger.</div>
            } @else {
              <div class="tiny muted">The crew did not add photos.</div>
            }
          </section>
        }
        @if (a.model_status !== 'ok' && a.kind !== 'public') {
          <section class="offline" role="status">
            <b>{{ a.model_status === 'off' ? 'AI assist was switched off' : 'AI assist was not available' }}</b>
            for this alert. The rules raised it from the meters alone, and the search area is {{ a.areas[0]?.from_loggers ? 'around the noise loggers that hear the leak' : 'around the sensor with the biggest pressure drop' }}.
          </section>
        }

        <section class="card dq row" aria-label="Data quality">
          <div class="row"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" style="color: var(--link)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z"></path><path d="M8.5 12l2.5 2.5 4.5-5"></path></svg>
            <b>Data checked before scoring</b></div>
          <span class="small">Meters used:
            @for (m of a.data_quality.meters; track m.id; let last = $last) {
              <span class="mono"><b>{{ m.id }}</b></span> trust {{ m.trust }}%{{ last ? '' : ', ' }}
            }</span>
          <span class="small">{{ fixLine() }}</span>
          <a routerLink="/dashboard/meters" class="small push">See meter data</a>
        </section>

        <section class="card stack">
          <div class="row between"><h2>How this score was worked out</h2>
            <span class="small muted mono" [title]="a.ruleset_info.description">rules {{ a.ruleset_info.version }}</span></div>
          @if (a.ruleset_info.version !== a.ruleset_info.active) {
            <div class="note">Scored with <b>{{ a.ruleset_info.version }}</b>, the ruleset in use when this alert was raised. New alerts use {{ a.ruleset_info.active }}.</div>
          }
          <div class="table-wrap">
            <table class="data trace">
              <thead><tr><th>Code</th><th>Finding</th><th>Evidence</th><th>Found by</th><th class="r">Points</th></tr></thead>
              <tbody>
                @for (r of a.trace; track r.code) {
                  <tr [class.dim]="r.supports"><td class="mono"><b>{{ r.code }}</b></td><td>{{ r.finding }}</td><td class="mono small">{{ r.evidence }}</td>
                    <td><span class="pill" [class]="srcClass(r.source)">{{ r.source }}</span></td>
                    @if (r.supports) {
                      <td class="r small muted support">supports {{ r.supports }}<div class="tiny">same fact, counted once</div></td>
                    } @else { <td class="r mono pts">+{{ r.points }}</td> }</tr>
                }
                <tr class="total-row"><td colspan="4"><b>Total</b> <span class="small muted">· 20+ Watch, {{ a.bands.high }}+ High, {{ a.bands.critical }}+ Critical (max 100)</span></td>
                  <td class="r mono total">{{ a.score }}</td></tr>
              </tbody>
            </table>
          </div>
          @for (n of a.notes; track n) { <div class="note"><b>Rule note:</b> {{ n }}</div> }
          @if (a.rejected.length) {
            <div class="rejected">
              <b>Not counted</b>
              @for (x of a.rejected; track x.code) {
                <div class="small"><span class="mono"><b>{{ x.code }}</b></span> {{ x.finding }}: {{ x.reason }}.</div>
              }
            </div>
          }
          <p class="note">AI findings count only when the meters agree, and add at most {{ a.ruleset_info.ai_cap }} points. The rules calculate the score.</p>
        </section>

        <section class="row two">
          <div class="card grow">
            <h2>Night flow, zone inlet {{ a.inlet }}</h2>
            <dw-line-chart [series]="nightSeries()" [baseline]="a.flow.baseline_lps" xMode="day" unit="L/s" [bands]="nightBands()" [width]="560" [height]="250" ariaLabel="Zone night flow" />
          </div>
          <div class="card grow">
            <h2>Pressure at the closest sensors, last 72 h</h2>
            @if (pressureSeries().length) {
              <dw-line-chart [series]="pressureSeries()" unit="m" [bands]="startBand()" [width]="560" [height]="250" ariaLabel="Pressure at sensors" />
            } @else { <p class="muted">No pressure sensors linked to this alert.</p> }
          </div>
        </section>

        @if (a.suspects.length) {
          <section class="card stack">
            <div class="row between"><h2>Where to check first</h2>
              <span class="small muted">Blocks of about 300 m, named after their main street. Shares add up to 100%.</span></div>
            <div class="table-wrap">
              <table class="data">
                <thead><tr><th>#</th><th>Block</th><th>Likely</th><th>Pipes</th><th>Pipe length</th><th>From best guess</th></tr></thead>
                <tbody>
                  @for (s of a.suspects; track s.rank) {
                    <tr class="clickable" [class.sel]="s.rank === pick()" (click)="pickBlock(s.rank)">
                      <td class="mono"><b>{{ s.rank }}</b></td>
                      <td>{{ s.street }}<div class="tiny muted mono">{{ s.pipes.slice(0, 4).join(', ') }}{{ s.pipe_count > 4 ? ' +' + (s.pipe_count - 4) : '' }}</div></td>
                      <td><div class="row nowrap"><span class="pbar"><span [style.width.%]="s.probability * 100"></span></span><b class="mono">{{ s.probability * 100 | num }}%</b></div></td>
                      <td class="mono">{{ s.pipe_count }}</td><td class="mono">{{ s.length_m | num }} m</td><td class="mono">{{ s.distance_from_guess_m | num }} m</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
            <p class="tiny muted">Click a row to show it on the map below.</p>
          </section>
        }

        <section class="row two">
          <div class="card map-card" #mapCard>
            <div class="row between"><h2>Where to look</h2>
              <span class="small muted">Orange pipes: {{ a.suspects.length ? 'block ' + pick() + ' in the list' : 'search area 1' }}. Circles: search areas. Blue dots: pressure sensors (darker = bigger drop). Purple: noise loggers (dark = leak noise).</span></div>
            <dw-leak-map [pipes]="pipes()" [highlightPipes]="highlight()" [circles]="circles()" [points]="points()" [height]="460" fitTo="circles" [fitCircle]="a.areas[0]?.from_loggers ? 0 : null" />
          </div>
          <div class="card side stack">
            <h2>What to do next</h2>
            @if (a.job) {
              <div class="success">{{ a.job.crew }} has this job ({{ a.job.status }}).</div>
            }
            <div class="facts">
              <div><div class="tiny muted">Households in area</div><b class="mono">{{ a.households_est !== null ? (a.households_est | num) : '–' }}</b></div>
              <div><div class="tiny muted">Repair</div><b>{{ a.ease.label === 'unknown' ? '–' : a.ease.label }}</b></div>
              <div class="span"><div class="tiny muted">{{ a.ease.reasons.join(' · ') }}</div></div>
            </div>
            @if (a.status === 'open' && !auth.canAct()) {
              <div class="note">View only. An operator sends crews and closes alerts.</div>
              <a class="btn secondary" [routerLink]="['/dashboard/replay', a.id]">Replay this leak</a>
            } @else if (a.status === 'open') {
              <ol class="steps">
                <li>Send a crew to walk {{ a.suspects[0]?.street || a.areas[0]?.streets?.[0] || 'the reported spot' }} with a listening stick</li>
                <li>Check for planned works or new connections in {{ a.zone }} first</li>
                <li>When it is fixed, the crew logs it and the night flow is checked again</li>
              </ol>
              <label class="field">Crew
                <select class="input" [ngModel]="crew" (ngModelChange)="crew = $event; crewTick.update(n => n + 1)" name="crew">
                  @for (c of crews(); track c.id) {
                    <option [value]="c.id">{{ c.name }} · {{ c.base }} · {{ c.state === 'busy' ? 'on a job' : 'free' }}</option>
                  }
                </select>
              </label>
              @if (picked(); as c) {
                @if (c.state === 'busy' && a.job?.crew_id !== c.id) {
                  <div class="warn">{{ c.name }} is already busy: {{ c.detail }}{{ c.open_jobs > 1 ? ' and ' + (c.open_jobs - 1) + ' more' : '' }}. This job will wait in their list.</div>
                } @else {
                  <div class="tiny muted">{{ c.people }}</div>
                }
              }
              <div class="row">
                <button class="btn" type="button" (click)="send()" [disabled]="busy()">{{ a.job ? 'Send again' : 'Send crew' }}</button>
                <a class="btn secondary" [routerLink]="['/dashboard/replay', a.id]">Replay this leak</a>
                <button class="btn ghost" type="button" (click)="dismiss()" [disabled]="busy()">Not a leak</button>
              </div>
            } @else {
              <p class="muted">Closed {{ a.resolved | ago }}.</p>
              <a class="btn secondary" [routerLink]="['/dashboard/replay', a.id]">Replay this leak</a>
            }
            @if (msg()) { <div class="success">{{ msg() }}</div> }

            @if (nearLoggers().length) {
              <h3>Noise loggers nearby</h3>
              @for (l of nearLoggers(); track l.id) {
                <div class="small row nowrap between">
                  <span><span class="mono"><b>{{ l.id }}</b></span> · {{ l.distance_m === null ? '' : l.distance_m < 30 ? 'at the best guess' : (l.distance_m | num) + ' m away' }}</span>
                  <span class="pill" [class]="l.status === 'noisy' ? 'high' : l.status === 'loud once' ? 'watch' : 'low'">{{ loggerText(l) }}</span>
                </div>
              }
              <a class="small" routerLink="/dashboard/loggers">All noise loggers</a>
            }

            @if (a.reports.length) {
              <h3>Public reports ({{ a.reports.length }})</h3>
              @for (r of a.reports; track r.code) {
                <div class="small"><span class="mono"><b>{{ r.code }}</b></span> · {{ r.kind_en }} · {{ r.created | ago }}</div>
              }
            }
          </div>
        </section>
      </div>
    } @else if (!error()) { <div class="skeleton">Loading the alert...</div> }
  `,
  styles: [`
    @media (max-width: 640px) {
      .trace thead { display: none; }
      .trace tr { display: grid; grid-template-columns: auto 1fr auto; gap: 4px 10px; padding: 10px 4px; border-bottom: 1px solid var(--line-soft); }
      .trace td { display: block; padding: 0; border: none; }
      .trace td:nth-child(1) { grid-column: 1; grid-row: 1; }
      .trace td:nth-child(2) { grid-column: 2; grid-row: 1; }
      .trace td:nth-child(5) { grid-column: 3; grid-row: 1; }
      .trace td:nth-child(3) { grid-column: 2 / 4; grid-row: 2; }
      .trace td:nth-child(4) { grid-column: 2 / 4; grid-row: 3; }
      .trace tr.total-row td:first-child { grid-column: 1 / 3; }
      .trace tr.total-row td:last-child { grid-column: 3; grid-row: 1; }
    }
    .notfixed { display: flex; flex-wrap: wrap; gap: 8px 22px; align-items: baseline; padding: 14px 18px; border-left: 4px solid var(--sand); }
    .notfixed .mono { font-weight: 700; }
    .page { gap: 20px; max-width: 1280px; }
    .hero { background: var(--panel); color: #fff; border-radius: 16px; padding: 26px; display: flex; flex-wrap: wrap; gap: 26px; align-items: center; }
    .hero-main { flex: 1 1 260px; }
    .muted-light { color: #B5C4CF; font-size: 14px; margin: 6px 0 0; }
    .big { display: flex; align-items: baseline; gap: 8px; margin-top: 6px; }
    .big .mono { font-size: 72px; font-weight: 700; line-height: 1; }
    .of { font-size: 22px; color: #9FB2C0; }
    h1 { font-size: 24px; font-weight: 600; margin-top: 10px; }
    .hero-stats { flex: 2 1 420px; display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }
    .stat { background: var(--panel-2); border-radius: 12px; padding: 16px; }
    .stat .label { font-size: 12px; color: #B5C4CF; }
    .stat .v { font-size: 24px; font-weight: 700; margin: 6px 0 4px; }
    .stat .small-v { font-size: 17px; }
    .stat small { font-size: 13px; color: #B5C4CF; font-weight: 400; }
    .sand { color: var(--sand); }
    .dq { gap: 10px 24px; padding: 14px 20px; }
    .push { margin-left: auto; font-weight: 600; }
    .trace td.r, .trace th.r { text-align: right; }
    .pts { font-size: 16px; font-weight: 600; }
    .total { font-size: 20px; font-weight: 700; }
    .two { gap: 16px; align-items: stretch; }
    .two > .card { flex: 1 1 420px; display: flex; flex-direction: column; gap: 10px; }
    .map-card { flex: 3 1 520px !important; }
    .side { flex: 2 1 320px !important; }
    .steps { margin: 0; padding-left: 20px; font-size: 14px; display: flex; flex-direction: column; gap: 6px; color: var(--text-2); }
    h3 { font-size: 15px; margin-top: 6px; }
    .pbar { width: 90px; height: 6px; background: var(--track); border-radius: 99px; overflow: hidden; flex: none; display: inline-block; }
    .pbar span { display: block; height: 100%; background: var(--high-bg); }
    .nowrap { flex-wrap: nowrap; gap: 8px; }
    tr.sel td { background: var(--sel); }
    .facts { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .facts .span { grid-column: 1 / -1; }
    .offline { background: var(--watch-pill); color: var(--text); border-radius: 12px; padding: 14px 18px; font-size: 14px; }
    .rejected { background: var(--chip); border-radius: 8px; padding: 12px 14px; display: flex; flex-direction: column; gap: 6px; }
    tr.dim td { color: var(--muted); }
    .support { white-space: nowrap; }
    .warn { font-size: 13px; background: var(--watch-pill); color: var(--text); border-radius: 8px; padding: 10px 12px; }
    .crew-photos { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
    .crew-photos figure { margin: 0; display: flex; flex-direction: column; gap: 4px; }
    .crew-photos img { width: 100%; max-height: 260px; object-fit: cover; border-radius: 10px; cursor: zoom-in; }
    .crew-photos img.big { max-height: none; object-fit: contain; cursor: zoom-out; }
    .crew-photos .ph { height: 160px; border-radius: 10px; background: var(--chip); display: flex; align-items: center; justify-content: center; }
    .demo { font-size: 13px; background: var(--ai-pill); color: var(--ai-text); border-radius: 8px; padding: 12px 14px; }
  `],
})
export class AlertDetailPage {
  private api = inject(Api);
  private router = inject(Router);
  auth = inject(Auth);
  id = input.required<string>();
  a = signal<AlertDetail | null>(null);
  pipes = signal<GeoJSON.FeatureCollection | null>(null);
  error = signal('');
  busy = signal(false);
  msg = signal('');
  crew = '';
  crews = signal<Crew[]>([]);
  crewTick = signal(0);
  picked = computed(() => { this.crewTick(); return this.crews().find(c => c.id === this.crew) ?? null; });
  pick = signal(1);
  private mapCard = viewChild<ElementRef<HTMLElement>>('mapCard');

  /** Show the block on the map. The map sits under the list, so bring it into view if it is off screen. */
  pickBlock(rank: number) {
    this.pick.set(rank);
    const el = this.mapCard()?.nativeElement;
    if (!el) return;
    const r = el.getBoundingClientRect();
    if (r.top < 0 || r.bottom > window.innerHeight) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  highlight = computed(() => {
    const a = this.a();
    if (!a) return [];
    const s = a.suspects.find(x => x.rank === this.pick());
    return s ? s.pipes : a.areas[0]?.pipes ?? [];
  });

  srcClass = (s: string) => SRC_CLASS[s] ?? 'neutral';

  fixLine = computed(() => {
    const f = this.a()?.data_quality.fixes ?? {};
    const parts = [];
    if (f['DQ-01']) parts.push(`${f['DQ-01']} spikes removed`);
    if (f['DQ-02']) parts.push(`${f['DQ-02'] * 5} min of short gaps filled`);
    if (f['DQ-04']) parts.push(`${f['DQ-04']} impossible values dropped`);
    if (f['DQ-06']) parts.push(`${f['DQ-06']} time problems fixed`);
    return (parts.join(', ') || 'No cleaning needed') + '. Filled readings never count toward the score.';
  });

  nightSeries = computed<ChartSeries[]>(() => {
    const n = this.a()?.flow.nights ?? [];
    return [{ t: n.map(x => x.night), v: n.map(x => x.lps), color: '#0B5FA5', width: 3, label: 'Night flow (L/s)' }];
  });
  nightBands = computed<ChartBand[]>(() => {
    const a = this.a();
    if (!a || a.flow.night_pct < 4) return [];
    return [{ from: a.started, to: a.flow.nights[a.flow.nights.length - 1].night + 43200, color: '#FDEBDD',
      label: `${a.flow.night_pct > 0 ? '+' : ''}${a.flow.night_pct}% since ${fmtDay(a.started)}` }];
  });
  pressureSeries = computed<ChartSeries[]>(() => (this.a()?.pressure ?? []).map((p, i) => ({
    t: p.t, v: p.v, color: i === 0 ? '#0B5FA5' : '#7F95A6', width: i === 0 ? 2.5 : 2, label: `${p.id}${i === 0 ? ' (biggest drop)' : ''}`,
  })));
  startBand = computed<ChartBand[]>(() => {
    const a = this.a();
    return a && a.kind !== 'public' ? [{ from: a.started, to: a.resolved ?? Date.now() / 1000, color: '#FDE8E4', label: 'leak running' }] : [];
  });
  circles = computed<MapCircle[]>(() => (this.a()?.areas ?? []).map((ar, i) => ({
    latlon: ar.centre_latlon, radius_m: ar.radius_m, color: AREA_COLORS[i] ?? '#7F95A6', dashed: i > 0,
    label: ar.probability !== null ? `Area ${ar.area}: ${Math.round(ar.probability * 100)}% likely`
      : ar.from_loggers ? `Leak noise heard by ${ar.from_loggers.join(', ')}` : `Search area around sensor ${ar.from_sensors?.[0] ?? ''}`,
  })));
  points = computed<MapPoint[]>(() => {
    const a = this.a();
    if (!a) return [];
    const max = Math.max(1, ...a.sensors.map(s => s.drop_mm));
    const pts: MapPoint[] = a.sensors.map(s => ({
      latlon: s.latlon, color: shade(s.drop_mm / max), size: 6,
      label: `${s.id}: ${s.drop_mm} mm drop, trust ${s.trust}%`,
    }));
    // noise loggers within 2 km of the best guess, quiet ones first so loud ones are drawn on top
    const near = (a.loggers ?? []).filter(l => l.distance_m === null || l.distance_m <= 2000);
    for (const l of [...near].reverse()) {
      pts.push({ latlon: l.latlon, color: LOGGER_COLOR[l.status], size: l.status === 'noisy' ? 8 : l.status === 'loud once' ? 6 : 4,
        ring: l.status === 'noisy' ? '#0F1B24' : undefined, label: `${l.id}: ${this.loggerText(l).toLowerCase()}` });
    }
    for (const r of a.reports) pts.push({ latlon: [r.lat, r.lon], color: '#2F8F5B', size: 6, label: `${r.code}: ${r.kind_en}` });
    const g = a.model.best_guess_latlon ?? a.best_guess_latlon;
    if (g) pts.push({ latlon: g, color: '#E8590C', size: 9, ring: '#0F1B24', label: 'Best guess' });
    return pts;
  });

  constructor() {
    effect(() => { this.load(this.id()); });
  }

  async load(id: string) {
    try {
      const a = await this.api.alert(id);
      this.a.set(a);
      this.error.set('');
      this.loadCrewPhotos(a.crew_result);
      await this.loadCrews();
      if (a.kind !== 'public') this.pipes.set(await this.api.mapPipes(a.zone));
      else this.pipes.set(null);
    } catch (e) { this.error.set(errorText(e)); }
  }

  crewPhotos = signal<Record<string, string>>({});
  bigPhoto = signal('');
  photoSlots(cr: CrewResult) {
    return [{ key: 'before', label: 'Before', on: cr.has_photo_before }, { key: 'after', label: 'After', on: cr.has_photo_after }].filter(x => x.on);
  }

  async loadCrewPhotos(cr: CrewResult | null) {
    if (!cr || !(cr.has_photo_before || cr.has_photo_after)) { this.crewPhotos.set({}); return; }
    try {
      const p = await this.api.jobPhotos(cr.job);
      this.crewPhotos.set({ ...(p.before ? { before: p.before } : {}), ...(p.after ? { after: p.after } : {}) });
    } catch { /* the rest of the page still works */ }
  }

  async loadCrews() {
    try {
      const list = (await this.api.crews()).crews;
      this.crews.set(list);
      if (!list.some(c => c.id === this.crew)) {
        // start on the first free leak crew, else the first crew
        this.crew = (list.find(c => c.state === 'available' && !/maint/i.test(c.name)) ?? list[0])?.id ?? '';
      }
      this.crewTick.update(n => n + 1);
    } catch { /* the page still works, the list just stays empty */ }
  }

  nearLoggers = computed(() => {
    const ls = this.a()?.loggers ?? [];
    const noisy = ls.filter(l => l.status === 'noisy');
    const rest = ls.filter(l => l.status !== 'noisy').slice(0, Math.max(0, 3 - noisy.length));
    return [...noisy.slice(0, 4), ...rest];
  });

  loggerText(l: { status: string; change_db: number; loud_nights: number }) {
    const d = `${l.change_db > 0 ? '+' : ''}${l.change_db.toFixed(0)} dB`;
    return l.status === 'noisy' ? `Leak noise, ${d}, ${l.loud_nights} nights` : l.status === 'loud once' ? `Loud once, ${d}` : 'Quiet';
  }

  async send() {
    this.busy.set(true);
    try {
      const j = await this.api.dispatch(this.id(), this.crew);
      this.msg.set(`Job ${j.id} sent. It is now on the crew's phone.`);
      await this.load(this.id());
    } catch (e) { this.error.set(errorText(e)); }
    this.busy.set(false);
  }

  async dismiss() {
    this.busy.set(true);
    try {
      await this.api.dismiss(this.id());
      this.router.navigateByUrl('/dashboard/alerts');
    } catch (e) { this.error.set(errorText(e)); }
    this.busy.set(false);
  }
}

const LOGGER_COLOR: Record<string, string> = { noisy: '#6D28D9', 'loud once': '#A78BFA', quiet: '#CFC4F3' };

function shade(x: number) {
  const a = [191, 214, 232], b = [11, 61, 110];
  const c = a.map((v, i) => Math.round(v + (b[i] - v) * Math.min(1, Math.max(0, x))));
  return `rgb(${c.join(',')})`;
}
