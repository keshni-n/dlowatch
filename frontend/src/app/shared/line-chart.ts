import { ChangeDetectionStrategy, Component, computed, inject, input } from '@angular/core';
import { fmtDay, fmtTime } from '../core/format';
import { Theme } from './bits';

export interface ChartSeries {
  t: number[]; v: (number | null)[]; color: string; width?: number; dashed?: boolean; label?: string; dotsOnly?: boolean;
}
export interface ChartBand { from: number; to: number; color: string; label?: string; labelColor?: string; }
export interface ChartMarker { t: number; label: string; }
export interface ChartDot { t: number; v: number; color: string; }


/** Small dependency-free SVG line chart, good enough for time series in the dashboard. */
@Component({
  selector: 'dw-line-chart',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <svg [attr.viewBox]="'0 0 ' + W() + ' ' + height()" class="chart" role="img" [attr.aria-label]="ariaLabel()">
      @if (unit()) { <text x="4" y="12" class="tick">{{ unit() }}</text> }
      @for (b of view().bands; track $index) {
        <rect class="band" [attr.x]="b.x" [attr.y]="pad.t" [attr.width]="b.w" [attr.height]="view().ih" [attr.fill]="b.color"></rect>
        @if (b.label) {
          <text [attr.x]="b.x + 6" [attr.y]="pad.t + 16" class="band-label">{{ b.label }}</text>
        }
      }
      @for (g of view().yTicks; track $index) {
        <line [attr.x1]="pad.l" [attr.x2]="W() - pad.r" [attr.y1]="g.y" [attr.y2]="g.y" class="grid"></line>
        <text [attr.x]="pad.l - 8" [attr.y]="g.y + 4" class="tick" text-anchor="end">{{ g.label }}</text>
      }
      @for (x of view().xTicks; track $index) {
        <text [attr.x]="x.x" [attr.y]="height() - 8" class="tick" text-anchor="middle">{{ x.label }}</text>
      }
      @if (view().baseY !== null) {
        <line [attr.x1]="pad.l" [attr.x2]="W() - pad.r" [attr.y1]="view().baseY" [attr.y2]="view().baseY" class="baseline"></line>
        <text [attr.x]="pad.l + 6" [attr.y]="(view().baseY ?? 0) - 6" class="base-label">{{ baselineLabel() }}</text>
      }
      @for (s of view().lines; track $index) {
        @if (s.dotsOnly) {
          @for (p of s.points; track $index) {
            <circle [attr.cx]="p[0]" [attr.cy]="p[1]" r="1.8" [attr.fill]="s.color"></circle>
          }
        } @else {
          <path [attr.d]="s.d" fill="none" [attr.stroke]="s.color" [attr.stroke-width]="s.width"
                [attr.stroke-dasharray]="s.dashed ? '6 6' : null" stroke-linejoin="round" stroke-linecap="round"></path>
        }
      }
      @for (d of view().dots; track $index) {
        <circle class="dot" [attr.cx]="d.x" [attr.cy]="d.y" r="5" [attr.fill]="d.color" stroke-width="1.5"></circle>
      }
      @for (m of view().markers; track $index) {
        <line [attr.x1]="m.x" [attr.x2]="m.x" [attr.y1]="pad.t" [attr.y2]="pad.t + view().ih" class="marker-line"></line>
        <circle class="marker-dot" [attr.cx]="m.x" [attr.cy]="pad.t + 2" r="11"></circle>
        <text [attr.x]="m.x" [attr.y]="pad.t + 6" class="marker-label" text-anchor="middle">{{ m.label }}</text>
      }
    </svg>
    @if (legend().length) {
      <div class="legend">
        @for (s of legend(); track $index) {
          <span><i [style.background]="s.dashed ? 'none' : s.color" [style.border-top]="s.dashed ? '2px dashed ' + s.color : null"></i>{{ s.label }}</span>
        }
      </div>
    }
  `,
  styles: [`
    :host { display: block; }
    .chart { width: 100%; height: auto; display: block; }
    .grid { stroke: var(--line); stroke-width: 1; }
    .tick { font-family: var(--mono); font-size: 12px; fill: var(--muted); }
    .baseline { stroke: #7F95A6; stroke-width: 2; stroke-dasharray: 6 6; }
    .base-label { font-size: 12px; fill: var(--text-2); }
    .band-label { font-size: 12px; font-weight: 600; fill: var(--high-text); }
    :host-context([data-theme="dark"]) .band { opacity: 0.22; }
    .marker-line { stroke: var(--text); stroke-width: 1; stroke-dasharray: 2 4; opacity: 0.5; }
    .marker-dot { fill: var(--inverse-bg); }
    .marker-label { fill: var(--inverse-text); font-family: var(--mono); font-size: 12px; font-weight: 600; }
    .dot { stroke: var(--surface); }
    .legend { display: flex; gap: 16px; flex-wrap: wrap; font-size: 13px; color: var(--text-2); margin-top: 6px; }
    .legend span { display: inline-flex; align-items: center; gap: 6px; }
    .legend i { display: inline-block; width: 18px; height: 3px; }
  `],
})
export class LineChart {
  readonly pad = { l: 52, r: 12, t: 22, b: 28 };
  private theme = inject(Theme);
  /** Dark blue and dark orange lines are hard to see on a dark background, so lighten them there. */
  private tone = (c: string) => this.theme.mode() !== 'dark' ? c : ({ '#0B5FA5': '#6CB8F0', '#C2410C': '#F28C52', '#7F95A6': '#9DB1C0', '#9FB2C0': '#6E8595' } as Record<string, string>)[c] ?? c;
  /** Width of the drawing in chart units. Use a smaller value for half-width cards so the text stays readable. */
  width = input(900);
  W = computed(() => this.width());

  series = input<ChartSeries[]>([]);
  bands = input<ChartBand[]>([]);
  markers = input<ChartMarker[]>([]);
  dots = input<ChartDot[]>([]);
  baseline = input<number | null>(null);
  baselineLabel = input('usual level');
  height = input(220);
  xMode = input<'time' | 'day'>('time');
  unit = input('');
  ariaLabel = input('Chart');
  yMin = input<number | null>(null);

  legend = computed(() => this.series().filter(s => s.label).map(s => ({ ...s, color: this.tone(s.color) })));

  view = computed(() => {
    const series = this.series();
    const h = this.height();
    const ih = h - this.pad.t - this.pad.b;
    const W = this.W();
    const iw = W - this.pad.l - this.pad.r;
    const ts: number[] = [];
    const vs: number[] = [];
    for (const s of series) {
      s.t.forEach((t, i) => { const v = s.v[i]; ts.push(t); if (v !== null && v !== undefined) vs.push(v); });
    }
    if (!ts.length || !vs.length) {
      return { lines: [], xTicks: [], yTicks: [], bands: [], markers: [], dots: [], baseY: null as number | null, ih };
    }
    const t0 = Math.min(...ts), t1 = Math.max(...ts);
    const base = this.baseline();
    let lo = Math.min(...vs, base ?? Infinity), hi = Math.max(...vs, base ?? -Infinity);
    if (this.yMin() !== null) lo = Math.min(lo, this.yMin()!);
    const span = hi - lo || Math.abs(hi) * 0.1 || 1;
    lo -= span * 0.08; hi += span * 0.12;
    const x = (t: number) => this.pad.l + ((t - t0) / (t1 - t0 || 1)) * iw;
    const y = (v: number) => this.pad.t + (1 - (v - lo) / (hi - lo)) * ih;

    const lines = series.map(s => {
      let d = '';
      let pen = false;
      const points: [number, number][] = [];
      s.t.forEach((t, i) => {
        const v = s.v[i];
        if (v === null || v === undefined) { pen = false; return; }
        const px = x(t).toFixed(1), py = y(v).toFixed(1);
        points.push([+px, +py]);
        d += (pen ? 'L' : 'M') + px + ',' + py;
        pen = true;
      });
      return { d, points, color: this.tone(s.color), width: s.width ?? 2.5, dashed: !!s.dashed, dotsOnly: !!s.dotsOnly };
    });

    const yTicks = [0, 1, 2, 3].map(i => {
      const v = lo + ((hi - lo) * (i + 0.5)) / 4;
      return { y: y(v), label: fmtNum(v) };
    });
    const n = W < 700 ? 4 : 6;
    const xTicks = Array.from({ length: n }, (_, i) => {
      const t = t0 + ((t1 - t0) * i) / (n - 1);
      const label = this.xMode() === 'day' ? fmtDay(t) : (t1 - t0 > 86400 * 1.5 ? `${fmtDay(t)} ${fmtTime(t)}` : fmtTime(t));
      return { x: Math.min(Math.max(x(t), this.pad.l + 30), W - this.pad.r - 30), label };
    });
    const bands = this.bands().filter(b => b.to > t0 && b.from < t1).map(b => {
      const a = x(Math.max(b.from, t0)), z = x(Math.min(b.to, t1));
      return { x: a, w: Math.max(2, z - a), color: b.color, label: b.label, labelColor: b.labelColor };
    });
    const markers = this.markers().filter(m => m.t >= t0 && m.t <= t1).map(m => ({ x: x(m.t), label: m.label }));
    const dots = this.dots().filter(d => d.t >= t0 && d.t <= t1).map(d => ({ x: x(d.t), y: y(d.v), color: d.color }));
    return { lines, xTicks, yTicks, bands, markers, dots, baseY: base !== null ? y(base) : null, ih };
  });
}

function fmtNum(v: number) {
  const a = Math.abs(v);
  return a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : v.toFixed(2);
}
