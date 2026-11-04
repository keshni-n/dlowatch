import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api, errorText } from '../core/api';
import { LevelPipe, NumPipe } from '../core/format';
import { RepairItem, RepairList } from '../core/models';
import { LeakMap, MapPoint } from '../shared/leak-map';

type SortKey = 'priority' | 'water' | 'households' | 'ease';
const EASE_CLASS: Record<string, string> = { easy: 'ok', medium: 'watch', hard: 'critical', unknown: 'neutral' };

/** Required deliverable: which leaks to fix first, ranked by what fixing them is worth. */
@Component({
  selector: 'dw-repairs',
  imports: [RouterLink, LeakMap, LevelPipe, NumPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="stack page">
      <header class="row between">
        <div>
          <h1>Repair priority list</h1>
          <p class="muted lead">Which leaks to fix first. Ranked mostly by water saved if fixed, then by households in the area and how easy the repair is.</p>
        </div>
        <div class="row" role="group" aria-label="Sort by">
          @for (s of sorts; track s.key) {
            <button type="button" class="btn" [class.ghost]="sort() !== s.key" [class.dark]="sort() === s.key"
                    [attr.aria-pressed]="sort() === s.key" (click)="sort.set(s.key)">{{ s.label }}</button>
          }
        </div>
      </header>
      @if (error()) { <div class="error">{{ error() }}</div> }

      @if (data(); as d) {
        <section class="kpis">
          <div class="card kpi dark"><div class="label">Water saved if all are fixed</div><div class="value sand">{{ d.totals.m3_per_day | num }} m³/day</div>
            <div class="sub">{{ d.totals.m3_per_day * 1000 / 24 | num }} litres an hour</div></div>
          <div class="card kpi"><div class="label">Leaks to fix</div><div class="value">{{ d.totals.leaks }}</div></div>
          <div class="card kpi"><div class="label">Households in the search areas</div><div class="value">{{ d.totals.households | num }}</div>
            <div class="sub">Estimate, {{ d.household_use_litres_per_day }} L a day per household</div></div>
          <div class="card kpi"><div class="label">How the rank is worked out</div>
            <div class="weights">
              <span><b>{{ pct(d.weights.water) }}</b> water saved</span>
              <span><b>{{ pct(d.weights.households) }}</b> households</span>
              <span><b>{{ pct(d.weights.ease) }}</b> ease</span>
            </div><div class="sub">How the list is ranked</div></div>
        </section>

        <section class="list">
          @for (r of rows(); track r.id; let i = $index) {
            <article class="card item" [class.first]="i === 0">
              <div class="rank mono" [class]="r.level">{{ i + 1 }}</div>
              <div class="main">
                <div class="row between top">
                  <div>
                    <a [routerLink]="['/dashboard/alerts', r.id]" class="title">{{ r.zone_name }} {{ r.zone }}</a>
                    <div class="small muted">{{ r.headline }}</div>
                  </div>
                  <div class="row tags">
                    <span class="pill" [class]="r.level">{{ r.level | level }} · {{ r.score }}</span>
                    <span class="pill" [class]="easeClass(r.ease.label)">{{ r.ease.label === 'unknown' ? 'Ease unknown' : r.ease.label + ' repair' }}</span>
                  </div>
                </div>
                <div class="facts">
                  <div><div class="tiny muted">Water saved if fixed</div><div class="mono big">{{ r.water_lost_m3_per_day ? (r.water_lost_m3_per_day | num) + ' m³/day' : 'Not measured' }}</div>
                    @if (r.litres_per_hour) { <div class="tiny muted">{{ r.litres_per_hour | num }} L/hour</div> }</div>
                  <div><div class="tiny muted">Households in area</div><div class="mono big">{{ r.households_est !== null ? (r.households_est | num) : '–' }}</div></div>
                  <div><div class="tiny muted">Priority</div><div class="mono big">{{ r.priority }}</div>
                    <div class="bar"><span class="w" [style.width.%]="r.priority_parts.water * 100 * weights().water"></span><span class="h" [style.width.%]="r.priority_parts.households * 100 * weights().households"></span><span class="e" [style.width.%]="r.priority_parts.ease * 100 * weights().ease"></span></div></div>
                  <div><div class="tiny muted">Crew</div><div class="small strong">{{ r.job ? r.job.crew + ' (' + r.job.status + ')' : 'Not sent yet' }}</div></div>
                </div>
                @if (r.suspects.length) {
                  <div class="sus">
                    <div class="tiny muted">Check first</div>
                    @for (s of r.suspects; track s.rank) {
                      <div class="srow"><span class="mono n">{{ s.rank }}</span><span class="grow">{{ s.street }}</span>
                        <span class="pbar"><span [style.width.%]="s.probability * 100"></span></span><span class="mono small">{{ s.probability * 100 | num }}%</span></div>
                    }
                  </div>
                } @else if (r.kind === 'public') {
                  <div class="small muted">No meter data yet: send someone to check where people reported water.</div>
                }
                @if (r.ease.reasons.length && r.ease.label !== 'unknown') { <div class="tiny muted">{{ r.ease.reasons.join(' · ') }}</div> }
              </div>
            </article>
          } @empty { <div class="card muted">No open leaks. Nothing to repair.</div> }
        </section>

        <section class="card stack">
          <h2>On the map</h2>
          <dw-leak-map [points]="points()" [height]="360" fitTo="circles" />
          <p class="tiny muted">Number = place in the list. Darker = higher priority.</p>
        </section>
      } @else if (!error()) { <div class="skeleton">Working out the list...</div> }
    </div>
  `,
  styles: [`
    .page { gap: 18px; max-width: 1280px; }
    h1 { font-size: 32px; } .lead { max-width: 760px; margin: 6px 0 0; }
    .sand { color: var(--sand); }
    .weights { display: flex; flex-direction: column; gap: 2px; font-size: 13px; margin-top: 6px; }
    .list { display: flex; flex-direction: column; gap: 12px; }
    .item { display: flex; gap: 16px; align-items: flex-start; }
    .item.first { border: 2px solid var(--text); }
    .rank { width: 44px; height: 44px; flex: none; border-radius: 99px; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 18px; }
    .rank.low { background: var(--low-bg); color: var(--low-fg); } .rank.watch { background: var(--watch-bg); color: var(--watch-fg); }
    .rank.high { background: var(--high-bg); color: var(--high-fg); } .rank.critical { background: var(--crit-bg); color: var(--crit-fg); }
    .main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 12px; }
    .top { align-items: flex-start; }
    .title { font-size: 19px; font-weight: 700; text-decoration: none; color: var(--text); }
    .title:hover { color: var(--link); }
    .tags { gap: 6px; }
    .facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
    .big { font-size: 18px; font-weight: 700; margin-top: 2px; }
    .strong { font-weight: 600; margin-top: 4px; }
    .bar { display: flex; height: 6px; border-radius: 99px; overflow: hidden; background: var(--track); margin-top: 6px; max-width: 160px; }
    .bar .w { background: var(--blue); } .bar .h { background: #7F95A6; } .bar .e { background: var(--watch-bg); }
    .sus { display: flex; flex-direction: column; gap: 6px; border-top: 1px solid var(--line-soft); padding-top: 10px; }
    .srow { display: flex; gap: 10px; align-items: center; font-size: 14px; }
    .n { width: 20px; color: var(--muted); }
    .pbar { width: 120px; height: 6px; background: var(--track); border-radius: 99px; overflow: hidden; flex: none; }
    .pbar span { display: block; height: 100%; background: var(--high-bg); }
  `],
})
export class RepairsPage {
  private api = inject(Api);
  data = signal<RepairList | null>(null);
  error = signal('');
  sort = signal<SortKey>('priority');
  sorts: { key: SortKey; label: string }[] = [
    { key: 'priority', label: 'Priority' }, { key: 'water', label: 'Water saved' },
    { key: 'households', label: 'Households' }, { key: 'ease', label: 'Easiest first' },
  ];
  easeClass = (l: string) => EASE_CLASS[l] ?? 'neutral';
  pct = (w: number) => `${Math.round((w / this.weightSum()) * 100)}%`;
  weightSum = computed(() => { const w = this.data()?.weights; return w ? w.water + w.households + w.ease || 1 : 1; });
  weights = computed(() => { const w = this.data()?.weights ?? { water: 1, households: 0, ease: 0 }; const t = this.weightSum();
    return { water: w.water / t, households: w.households / t, ease: w.ease / t }; });

  rows = computed<RepairItem[]>(() => {
    const items = [...(this.data()?.items ?? [])];
    const by: Record<SortKey, (r: RepairItem) => number> = {
      priority: r => r.priority, water: r => r.water_lost_m3_per_day ?? -1,
      households: r => r.households_est ?? -1, ease: r => r.ease.value ?? -1,
    };
    const f = by[this.sort()];
    return items.sort((a, b) => f(b) - f(a));
  });
  points = computed<MapPoint[]>(() => this.rows().filter(r => r.best_guess_latlon).map((r, i) => ({
    latlon: r.best_guess_latlon!, size: 9 - Math.min(i, 4), color: i === 0 ? '#B9351C' : i === 1 ? '#EE9550' : '#F3D27E',
    label: `${i + 1}. ${r.zone_name}: ${r.water_lost_m3_per_day ?? '?'} m³/day`,
  })));

  constructor() {
    this.load();
    const t = setInterval(() => this.load(), 30000);
    inject(DestroyRef).onDestroy(() => clearInterval(t));
  }

  async load() {
    try { this.data.set(await this.api.repairs()); this.error.set(''); }
    catch (e) { this.error.set(errorText(e)); }
  }
}
