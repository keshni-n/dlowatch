import { ChangeDetectionStrategy, Component, computed, effect, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api, errorText } from '../core/api';
import { DateTimePipe, NumPipe, fmtDay } from '../core/format';
import { WaterReport } from '../core/models';
import { ChartSeries, LineChart } from '../shared/line-chart';

/** Weekly water loss report for the network manager: lost, saved, money, worst zones, and how well the system works. */
@Component({
  selector: 'dw-report',
  imports: [RouterLink, LineChart, NumPipe, DateTimePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="stack page">
      <header class="row between">
        <div>
          @if (r(); as r) { <div class="small muted mono">{{ r.start | dateTime }} to {{ r.end | dateTime }}</div> }
          <h1>Water loss report</h1>
          <p class="muted lead">Water lost, water saved by repairs, and which zones lose the most.</p>
        </div>
        <div class="row no-print">
          <label class="row small">Period
            <select class="input narrow" [value]="days()" (change)="days.set(+$any($event.target).value)">
              <option value="7">Last 7 days</option><option value="3">Last 3 days</option><option value="13">Last 13 days</option>
            </select></label>
          <button type="button" class="btn ghost" (click)="csv()" [disabled]="!r()">Download CSV</button>
          <button type="button" class="btn secondary" (click)="print()">Print or save as PDF</button>
        </div>
      </header>
      @if (error()) { <div class="error">{{ error() }}</div> }

      @if (r(); as r) {
        <section class="kpis">
          <div class="card kpi"><div class="label">Water put in</div><div class="value">{{ r.totals.water_in_m3 | num }} m³</div>
            <div class="sub">Expected use {{ r.totals.expected_m3 | num }} m³</div></div>
          <div class="card kpi dark"><div class="label">Water lost</div><div class="value sand">{{ r.totals.lost_m3 | num }} m³</div>
            <div class="sub">More than expected use, all zones</div></div>
          <div class="card kpi"><div class="label">Saved by repairs</div><div class="value blue">{{ r.totals.saved_m3 | num }} m³</div>
            <div class="sub">{{ r.totals.repairs_done }} repair{{ r.totals.repairs_done === 1 ? '' : 's' }}, now {{ r.totals.saved_per_day_m3 | num }} m³ a day less</div></div>
          <div class="card kpi"><div class="label">Still losing</div><div class="value">{{ r.totals.still_losing_m3_per_day | num }} m³/day</div>
            <div class="sub">Estimated, {{ r.totals.open_leaks }} open leak{{ r.totals.open_leaks === 1 ? '' : 's' }} · <a routerLink="/dashboard/repairs">repair list</a></div></div>
        </section>

        <section class="card money">
          @if (r.money; as m) {
            <div class="row between"><h2>Money</h2><span class="small muted">at {{ m.currency }} {{ m.cost_per_m3 }} per m³</span></div>
            <div class="mrow">
              <div><div class="tiny muted">Lost</div><b class="mono">{{ m.currency }} {{ m.lost | num }}</b></div>
              <div><div class="tiny muted">Saved by repairs</div><b class="mono">{{ m.currency }} {{ m.saved | num }}</b></div>
              <div><div class="tiny muted">Still losing</div><b class="mono">{{ m.currency }} {{ m.still_losing_per_day | num }} a day</b></div>
            </div>
          } @else {
            <h2>Money</h2>
            <p class="small muted">{{ r.money_note }}</p>
          }
        </section>

        <section class="card stack">
          <h2>Water lost each day</h2>
          <dw-line-chart [series]="dailySeries()" xMode="day" unit="m³" [yMin]="0" [height]="210" ariaLabel="Water lost per day" />
        </section>

        <section class="row two">
          <div class="card grow stack">
            <h2>Zones, most water lost first</h2>
            <div class="table-wrap">
              <table class="data">
                <thead><tr><th>Zone</th><th class="r">Water in</th><th class="r">Lost</th><th class="r">Lost %</th><th class="r">Alerts</th><th class="r">Data</th></tr></thead>
                <tbody>
                  @for (z of r.zones; track z.zone; let i = $index) {
                    <tr [class.worst]="i < 3 && z.lost_m3 > 0">
                      <td><b>{{ z.name }}</b><span class="mono tiny code">{{ z.zone }}</span></td>
                      <td class="r mono">{{ z.water_in_m3 | num }}</td><td class="r mono"><b>{{ z.lost_m3 | num }}</b></td>
                      <td class="r mono">{{ z.lost_pct }}%</td><td class="r mono">{{ z.alerts }}</td><td class="r mono">{{ z.data_coverage_pct }}%</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
            <p class="tiny muted">Data = share of meter readings received. {{ r.note }}</p>
          </div>
          <div class="card side stack">
            <h2>Repairs</h2>
            @for (f of r.fixed; track f.alert) {
              <div class="fix"><b>{{ f.zone_name }}</b> · {{ f.cause || 'leak' }}<div class="small muted">{{ f.crew }}, {{ f.fixed | dateTime }} · {{ f.m3_per_day | num }} m³ a day stopped</div></div>
            } @empty { <p class="small muted">No repairs logged in this period.</p> }
            <div class="small muted">{{ r.totals.alerts_raised }} alerts raised, {{ r.totals.reports }} public reports in the period.</div>
          </div>
        </section>
      } @else if (!error()) { <div class="skeleton">Building the report...</div> }

    </div>
  `,
  styles: [`
    .page { gap: 18px; max-width: 1280px; }
    h1 { font-size: 32px; margin-top: 4px; } .lead { margin: 6px 0 0; }
    .narrow { width: auto; height: 40px; font-size: 14px; }
    .sand { color: var(--sand); } .blue { color: var(--link); }
    .money { display: flex; flex-direction: column; gap: 10px; }
    .mrow { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; font-size: 18px; }
    .two { gap: 16px; align-items: stretch; } .two > .grow { flex: 3 1 520px; } .side { flex: 2 1 280px; }
    .r { text-align: right; } .code { margin-left: 6px; color: var(--muted); }
    tr.worst td { background: var(--high-pill); }
    .fix { border-bottom: 1px solid var(--line-soft); padding-bottom: 8px; }
    @media print { .no-print { display: none !important; } .card { break-inside: avoid; } }
  `],
})
export class ReportPage {
  private api = inject(Api);
  days = signal(7);
  r = signal<WaterReport | null>(null);
  error = signal('');

  dailySeries = computed<ChartSeries[]>(() => {
    const d = this.r()?.daily ?? [];
    return [{ t: d.map(x => x.day + 43200), v: d.map(x => x.lost_m3), color: '#C2410C', width: 3, label: 'Water lost (m³)' }];
  });

  constructor() {
    effect(() => { this.load(this.days()); });
  }

  async load(days: number) {
    try { this.r.set(await this.api.report(days)); this.error.set(''); }
    catch (e) { this.error.set(errorText(e)); }
  }

  print() { window.print(); }

  /** Zone table and daily losses as a CSV file, for Excel. */
  csv() {
    const r = this.r();
    if (!r) return;
    const rows: (string | number)[][] = [
      ['DloWatch water loss report', `${fmtDay(r.start)} to ${fmtDay(r.end)}`], [],
      ['Zone', 'Area', 'Water in (m3)', 'Expected (m3)', 'Lost (m3)', 'Lost %', 'Alerts', 'Data received %'],
      ...r.zones.map(z => [z.zone, z.name, z.water_in_m3, z.expected_m3, z.lost_m3, z.lost_pct, z.alerts, z.data_coverage_pct]),
      [], ['Day', 'Water lost (m3)'], ...r.daily.map(d => [fmtDay(d.day + 43200), d.lost_m3]),
      [], ['Total lost (m3)', r.totals.lost_m3], ['Saved by repairs (m3)', r.totals.saved_m3], ['Still losing (m3/day)', r.totals.still_losing_m3_per_day],
    ];
    const text = rows.map(row => row.map(c => `"${String(c).replace(/"/g, '""')}"`).join(',')).join('\r\n');
    const url = URL.createObjectURL(new Blob(['﻿' + text], { type: 'text/csv;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `dlowatch-report-${r.days}-days.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }
}
