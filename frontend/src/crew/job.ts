import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { Api, errorText } from '../core/api';
import { JobStatusPipe, LevelPipe } from '../core/format';
import { Job } from '../core/models';
import { LeakMap, MapCircle, MapPoint } from '../shared/leak-map';

@Component({
  selector: 'dw-crew-job',
  imports: [RouterLink, LeakMap, LevelPipe, JobStatusPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="phone-wrap">
      <div class="phone">
        <a routerLink="/crew" class="small strong">Back to jobs</a>
        @if (error()) { <div class="error">{{ error() }}</div> }
        @if (j(); as j) {
          <div class="row between">
            <h1>{{ j.zone_name }}</h1>
            <span class="pill" [class]="'solid-' + j.level">{{ j.level | level }} · {{ j.score }}</span>
          </div>
          <div class="card map">
            <dw-leak-map [pipes]="pipes()" [highlightPipes]="highlight()" [circles]="circles()" [points]="me()" [height]="230" fitTo="circles" />
            <div class="pad">
              <div class="where">{{ j.streets.slice(0, 2).join(', ') || 'Reported spot' }}</div>
              <div class="small">Walk the orange pipes{{ j.search_radius_m ? ', search area about ' + j.search_radius_m + ' m around the centre' : '' }}.</div>
            </div>
          </div>
          @if (j.suspects.length) {
            <div class="card stack sus">
              <div class="row between">
                <div class="label">Check in this order</div>
                <div class="seg" role="group" aria-label="Order">
                  <button type="button" [attr.aria-pressed]="order() === 'likely'" (click)="order.set('likely')">Most likely</button>
                  <button type="button" [attr.aria-pressed]="order() === 'closest'" (click)="order.set('closest')" [disabled]="!here()">Closest first</button>
                </div>
              </div>
              @for (s of ordered(); track s.rank; let i = $index) {
                <button type="button" class="srow" [class.on]="s.rank === pick()" (click)="pick.set(s.rank)">
                  <span class="n mono">{{ i + 1 }}</span>
                  <span class="grow"><b>{{ s.street }}</b><span class="tiny muted"> · {{ s.pipe_count }} pipes, {{ s.length_m }} m</span></span>
                  <span class="mono small">{{ order() === 'closest' && s.dist !== null ? (s.dist < 1000 ? s.dist + ' m' : (s.dist / 1000).toFixed(1) + ' km') : (s.probability * 100).toFixed(0) + '%' }}</span>
                </button>
              }
              @if (!here()) { <div class="tiny muted">Allow location to sort by distance.</div> }
            </div>
          }
          <div class="card">
            <div class="label">Why this was flagged</div>
            <ul>@for (r of j.reasons; track r) { <li>{{ r }}</li> }</ul>
          </div>
          <div class="row">
            @if (j.latlon) {
              <a class="btn grow" [href]="directions()" target="_blank" rel="noopener">Get directions</a>
            }
            @if (j.status === 'sent') { <button class="btn ghost grow" type="button" (click)="setStatus('on_the_way')">On my way</button> }
            @if (j.status === 'on_the_way') { <button class="btn ghost grow" type="button" (click)="setStatus('on_site')">I am on site</button> }
          </div>
          <div class="small muted">Status: {{ j.status | jobStatus }}</div>
          @if (j.status !== 'done') {
            <div class="stack found">
              <b>What did you find?</b>
              <div class="three">
                <button type="button" class="btn dark tall" (click)="result('leak_found')">Leak found</button>
                <button type="button" class="btn ghost tall" (click)="result('no_leak')">No leak</button>
                <button type="button" class="btn ghost tall" (click)="result('planned_works')">Planned works</button>
              </div>
              <button type="button" class="btn ghost" (click)="result('meter_fault')">It is a meter problem</button>
              <div class="tiny muted">Your answer helps find the next leaks faster.</div>
            </div>
          } @else {
            <div class="success">Closed: {{ j.outcome_text }}{{ j.cause ? ', ' + j.cause : '' }}.</div>
          }
        } @else if (!error()) { <div class="skeleton">Loading the job...</div> }
      </div>
    </div>
  `,
  styles: [`
    .strong { font-weight: 600; }
    h1 { font-size: 26px; }
    .map { padding: 0; overflow: hidden; }
    .pad { padding: 14px 16px; display: flex; flex-direction: column; gap: 4px; }
    .where { font-weight: 700; font-size: 18px; }
    .label { font-size: 13px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
    ul { margin: 8px 0 0; padding-left: 18px; font-size: 14px; display: flex; flex-direction: column; gap: 6px; }
    .three { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
    .tall { min-height: 56px; padding: 0 8px; font-size: 14px; }
    .found { margin-top: 4px; gap: 8px; }
    .sus { gap: 6px; }
    .seg { display: flex; background: var(--chip); border-radius: 99px; padding: 3px; }
    .seg button { border: none; background: transparent; border-radius: 99px; padding: 0 10px; min-height: 32px; font-size: 12px; font-weight: 600; color: var(--text-2); cursor: pointer; }
    .seg button[aria-pressed="true"] { background: var(--inverse-bg); color: var(--inverse-text); }
    .seg button:disabled { opacity: 0.5; }
    .srow { display: flex; gap: 10px; align-items: center; text-align: left; width: 100%; min-height: 48px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); color: var(--text); cursor: pointer; }
    .srow.on { border: 2px solid var(--high-bg); }
    .n { width: 22px; height: 22px; border-radius: 99px; background: var(--chip); display: flex; align-items: center; justify-content: center; font-size: 12px; flex: none; }
  `],
})
export class CrewJobPage {
  private api = inject(Api);
  private router = inject(Router);
  id = input.required<string>();
  j = signal<Job | null>(null);
  pipes = signal<GeoJSON.FeatureCollection | null>(null);
  here = signal<[number, number] | null>(null);
  order = signal<'likely' | 'closest'>('likely');
  pick = signal(1);
  ordered = computed(() => {
    const j = this.j();
    if (!j) return [];
    const h = this.here();
    const rows = j.suspects.map(s => ({ ...s, dist: h ? Math.round(distM(h, s.centre_latlon)) : null }));
    return this.order() === 'closest' && h ? rows.sort((a, b) => (a.dist ?? 0) - (b.dist ?? 0)) : rows;
  });
  highlight = computed(() => {
    const j = this.j();
    if (!j) return [];
    return j.suspects.find(s => s.rank === this.pick())?.pipes ?? j.pipes;
  });
  error = signal('');

  circles = computed<MapCircle[]>(() => {
    const j = this.j();
    return j?.latlon ? [{ latlon: j.latlon, radius_m: j.search_radius_m ?? 150, color: '#E8590C', label: 'Search area' }] : [];
  });
  me = computed<MapPoint[]>(() => (this.here() ? [{ latlon: this.here()!, color: '#0B5FA5', size: 8, label: 'You' }] : []));
  directions = computed(() => {
    const ll = this.j()?.latlon;
    return ll ? `https://www.google.com/maps/dir/?api=1&destination=${ll[0]},${ll[1]}` : '#';
  });

  constructor() {
    effect(() => { this.load(this.id()); });
    navigator.geolocation?.getCurrentPosition(p => this.here.set([p.coords.latitude, p.coords.longitude]), () => undefined, { timeout: 8000 });
  }

  async load(id: string) {
    try {
      this.j.set(await this.api.crewJob(id));
      this.pipes.set(await this.api.crewJobPipes(id));
    } catch (e) { this.error.set(errorText(e)); }
  }

  async setStatus(s: 'on_the_way' | 'on_site') {
    try { this.j.set(await this.api.crewStatus(this.id(), s)); } catch (e) { this.error.set(errorText(e)); }
  }

  result(outcome: string) {
    this.router.navigate(['/crew/jobs', this.id(), 'result'], { queryParams: { outcome } });
  }
}

/** Straight-line distance in metres. */
function distM(a: [number, number], b: [number, number]) {
  const r = Math.PI / 180, dLat = (b[0] - a[0]) * r, dLon = (b[1] - a[1]) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dLon / 2) ** 2;
  return 6371000 * 2 * Math.asin(Math.sqrt(h));
}
