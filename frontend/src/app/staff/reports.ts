import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api, errorText } from '../core/api';
import { AgoPipe, TimePipe } from '../core/format';
import { Cluster, PublicReportRow } from '../core/models';
import { LeakMap, MapPoint } from '../shared/leak-map';

const STATE: Record<string, { text: string; cls: string }> = {
  linked: { text: 'Linked to alert', cls: 'critical' }, cluster: { text: 'In a cluster', cls: 'watch' },
  new: { text: 'New', cls: 'low' }, fixed: { text: 'Fixed', cls: 'ok' },
};

@Component({
  selector: 'dw-reports',
  imports: [RouterLink, LeakMap, AgoPipe, TimePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="stack page">
      <header class="row between">
        <div><h1>Public reports</h1>
          <p class="muted">Sent from the public phone page. No names or phone numbers are stored.</p></div>
        <div class="row" role="group" aria-label="Filter">
          @for (f of filters; track f.key) {
            <button type="button" class="btn" [class.ghost]="filter() !== f.key" [class.dark]="filter() === f.key"
                    [attr.aria-pressed]="filter() === f.key" (click)="filter.set(f.key)">{{ f.label }} ({{ count(f.key) }})</button>
          }
        </div>
      </header>
      @if (error()) { <div class="error">{{ error() }}</div> }

      <section class="clusters">
        @for (c of clusters(); track c.alert) {
          <div class="card stack cl">
            <div class="row between">
              <span class="kicker" [class.red]="c.kind !== 'public'">{{ c.kind === 'public' ? 'Cluster · no meter alert yet' : 'Cluster · linked to a meter alert' }}</span>
              <span class="mono small"><b>score {{ c.score }}</b></span>
            </div>
            <div class="title">{{ c.zone_name }}{{ c.street ? ', ' + c.street : '' }}</div>
            <div class="small">{{ c.count }} reports from {{ c.devices }} different phones, within {{ c.minutes }} minutes.
              {{ c.kind === 'public' ? 'The meters here look normal, so this raised the zone to Watch.' : 'Matches the meter alert in this zone.' }}</div>
            <a class="small strong" [routerLink]="['/dashboard/alerts', c.alert]">Open the alert</a>
          </div>
        } @empty { <div class="card muted">No clusters right now.</div> }
      </section>

      <section class="row two">
        <div class="card grow table-card">
          <h2>All reports</h2>
          <div class="table-wrap">
            <table class="data">
              <thead><tr><th>Code</th><th>Time</th><th>Area</th><th>What they saw</th><th>Photo</th><th>Status</th></tr></thead>
              <tbody>
                @for (r of shown(); track r.code) {
                  <tr>
                    <td class="mono nowrap"><b>{{ r.code }}</b></td>
                    <td class="mono small">{{ r.created | time }}<div class="tiny muted">{{ r.created | ago }}</div></td>
                    <td>{{ r.zone_name }}</td>
                    <td>{{ r.kind_kreol }} <span class="muted small">· {{ r.kind_en }}</span>@if (r.note) {<div class="tiny muted">"{{ r.note }}"</div>}</td>
                    <td>@if (r.has_photo) {<button type="button" class="btn ghost mini" (click)="showPhoto(r.code)">View</button>} @else {<span class="muted small">No</span>}</td>
                    <td>
                      <span class="pill" [class]="state(r.status).cls">{{ state(r.status).text }}</span>
                      @if (r.alert) { <div><a class="tiny" [routerLink]="['/dashboard/alerts', r.alert]">alert</a></div> }
                    </td>
                  </tr>
                } @empty { <tr><td colspan="6" class="muted">No reports.</td></tr> }
              </tbody>
            </table>
          </div>
        </div>
        <div class="card side stack">
          <h2>On the map</h2>
          <dw-leak-map [points]="points()" [height]="320" fitTo="circles" />
          @if (photo()) {
            <figure><img [src]="photo()" alt="Photo sent with the report"><figcaption class="tiny muted">Photo from {{ photoCode() }}</figcaption></figure>
          }
        </div>
      </section>

      <section class="card dark stack">
        <h2>Keeping fake or repeat reports out</h2>
        <ul class="rules">
          <li>A cluster needs 3 different phones, not 3 reports from one</li>
          <li>Public reports alone can raise a zone to Watch, never higher</li>
          <li>Only reports from the last 24 hours, within 150 m of each other, are grouped</li>
          <li>The phone id is hashed. No name, number or exact home address is kept</li>
        </ul>
      </section>
    </div>
  `,
  styles: [`
    .page { gap: 20px; max-width: 1280px; } h1 { font-size: 32px; }
    .clusters { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }
    .cl { gap: 8px; }
    .kicker { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--watch-text); }
    .kicker.red { color: var(--crit-text); }
    .title { font-size: 20px; font-weight: 700; }
    .strong { font-weight: 600; }
    .two { gap: 16px; align-items: stretch; }
    .table-card { flex: 3 1 560px; }
    .side { flex: 2 1 320px; }
    .nowrap { white-space: nowrap; }
    .mini { min-height: 34px; padding: 0 12px; font-size: 13px; }
    figure { margin: 0; } figure img { width: 100%; border-radius: 10px; border: 1px solid var(--line); }
    .rules { margin: 0; padding-left: 18px; color: #D3DEE6; display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 8px 28px; font-size: 14px; }
  `],
})
export class ReportsPage {
  private api = inject(Api);
  reports = signal<PublicReportRow[]>([]);
  clusters = signal<Cluster[]>([]);
  filter = signal<'all' | 'grouped' | 'alone'>('all');
  photo = signal('');
  photoCode = signal('');
  error = signal('');
  filters = [{ key: 'all' as const, label: 'All' }, { key: 'grouped' as const, label: 'Linked or in a cluster' }, { key: 'alone' as const, label: 'On their own' }];
  state = (s: string) => STATE[s] ?? { text: s, cls: 'neutral' };

  shown = computed(() => this.reports().filter(r => this.match(r, this.filter())));
  points = computed<MapPoint[]>(() => this.reports().map(r => ({
    latlon: [r.lat, r.lon] as [number, number], size: 7, label: `${r.code}: ${r.kind_en}`,
    color: r.status === 'linked' ? '#B9351C' : r.status === 'cluster' ? '#E0A33B' : r.status === 'fixed' ? '#2F8F5B' : '#0B5FA5',
  })));

  constructor() { this.load(); }

  match(r: PublicReportRow, f: string) { return f === 'all' || (f === 'grouped' ? !!r.alert : !r.alert); }
  count(f: string) { return this.reports().filter(r => this.match(r, f)).length; }

  async load() {
    try {
      const d = await this.api.reports();
      this.reports.set(d.reports);
      this.clusters.set(d.clusters);
    } catch (e) { this.error.set(errorText(e)); }
  }

  async showPhoto(code: string) {
    try { this.photo.set((await this.api.reportPhoto(code)).photo); this.photoCode.set(code); }
    catch (e) { this.error.set(errorText(e)); }
  }
}
