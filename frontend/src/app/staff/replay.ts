import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api, errorText } from '../core/api';
import { DateTimePipe, LevelPipe, TimePipe, fmtDateTime } from '../core/format';
import { Replay } from '../core/models';
import { ChartMarker, ChartSeries, LineChart } from '../shared/line-chart';

const TAG: Record<string, string> = { Sensor: 'low', Rules: 'low', 'AI model': 'ai', Public: 'ok', Crew: 'watch' };

@Component({
  selector: 'dw-replay',
  imports: [RouterLink, LineChart, LevelPipe, TimePipe, DateTimePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (error()) { <div class="error">{{ error() }}</div> }
    @if (r(); as r) {
      <div class="stack page">
        <div class="small muted"><a routerLink="/dashboard/alerts">Leak alerts</a> / <a [routerLink]="['/dashboard/alerts', r.id]">{{ r.zone_name }} {{ r.zone }}</a> / Replay</div>
        <header class="row between">
          <div><div class="small muted mono">{{ r.window[0] | dateTime }} to {{ r.window[1] | dateTime }}</div>
            <h1>How the {{ r.zone_name }} leak was caught</h1></div>
          <div class="row"><span class="pill" [class]="'solid-' + r.level">{{ r.level | level }} · {{ r.score }}/100</span>
            @if (r.status === 'resolved') { <span class="pill ok">Fixed {{ r.resolved | time }}</span> }</div>
        </header>

        <section class="card stack">
          <dw-line-chart [series]="series()" [markers]="markers()" [baseline]="0" baselineLabel="usual" unit="L/s" [height]="260" ariaLabel="Extra flow and pressure drop during the leak" />
          <label class="field scrub">Scrub through the timeline: {{ scrubLabel() }}
            <input type="range" min="0" [max]="r.events.length" [value]="step()" (input)="step.set(+$any($event.target).value)">
          </label>
          <p class="tiny muted">Both lines compare each reading with the same time of day on previous days, so normal ups and downs cancel out. A leak shows as both lines lifting off zero together.</p>
        </section>

        <section class="card timeline" aria-label="Timeline">
          @for (e of r.events; track e.n) {
            <div class="ev" [class.dim]="e.n > step()">
              <div class="n">{{ e.n }}</div>
              <div class="time mono">{{ e.t | time }}</div>
              <div class="grow"><div class="t">{{ e.title }}</div><div class="small d">{{ e.detail }}</div></div>
              <span class="pill" [class]="tag(e.tag)">{{ e.tag }}</span>
            </div>
          } @empty { <p class="muted">No events yet.</p> }
        </section>
      </div>
    } @else if (!error()) { <div class="skeleton">Loading the replay...</div> }
  `,
  styles: [`
    .page { gap: 20px; max-width: 1280px; }
    h1 { font-size: 30px; margin-top: 4px; }
    .scrub { font-weight: 500; color: var(--muted); }
    .scrub input { width: 100%; accent-color: var(--link); height: 32px; }
    .timeline { padding: 6px 20px; }
    .ev { display: flex; gap: 16px; padding: 16px 0; border-bottom: 1px solid var(--line-soft); align-items: flex-start; transition: opacity 0.2s; }
    .ev:last-child { border-bottom: none; }
    .ev.dim { opacity: 0.35; }
    .n { width: 30px; height: 30px; flex: none; border-radius: 99px; background: var(--panel); color: #fff; display: flex; align-items: center; justify-content: center; font-family: var(--mono); font-weight: 600; font-size: 14px; }
    .time { width: 56px; flex: none; font-weight: 600; padding-top: 5px; }
    .t { font-weight: 600; font-size: 16px; padding-top: 4px; }
    .d { color: var(--text-2); margin-top: 3px; }
  `],
})
export class ReplayPage {
  private api = inject(Api);
  id = input.required<string>();
  r = signal<Replay | null>(null);
  error = signal('');
  step = signal(99);
  tag = (t: string) => TAG[t] ?? 'neutral';

  scrubLabel = computed(() => {
    const r = this.r();
    if (!r) return '';
    const s = Math.min(this.step(), r.events.length);
    return s === 0 ? 'before the leak' : `step ${s}, ${fmtDateTime(r.events[s - 1].t)}`;
  });

  series = computed<ChartSeries[]>(() => {
    const r = this.r();
    if (!r) return [];
    const s = Math.min(this.step(), r.events.length);
    const until = s === 0 ? r.window[0] + 1 : s >= r.events.length ? Infinity : r.events[s - 1].t;
    const cut = (t: number[], v: (number | null)[]) => v.map((x, i) => (t[i] <= until ? x : null));
    const out: ChartSeries[] = [{ t: r.flow.t, v: cut(r.flow.t, r.flow.v), color: '#0B5FA5', width: 3, label: 'Extra flow vs usual (L/s)' }];
    if (r.pressure) {
      // pressure drop (m) is put on the flow scale so both shapes line up on one chart
      const fv = r.flow.v.filter((x): x is number => x !== null);
      const pv = r.pressure.v.filter((x): x is number => x !== null);
      const fh = Math.max(...fv, 0.1), ph = Math.max(...pv.map(Math.abs), 0.001);
      const scaled = r.pressure.v.map(x => (x === null ? null : (x / ph) * fh));
      out.push({ t: r.pressure.t, v: cut(r.pressure.t, scaled), color: '#C2410C', width: 2.5, label: `Pressure drop vs usual (${r.pressure.id}, scaled)` });
    }
    return out;
  });
  markers = computed<ChartMarker[]>(() => (this.r()?.events ?? []).filter(e => e.n <= this.step()).map(e => ({ t: e.t, label: String(e.n) })));

  constructor() {
    effect(() => { this.load(this.id()); });
  }

  async load(id: string) {
    try {
      const r = await this.api.replay(id);
      this.r.set(r);
      this.step.set(r.events.length);
    } catch (e) { this.error.set(errorText(e)); }
  }
}
