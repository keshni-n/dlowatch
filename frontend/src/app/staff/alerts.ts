import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api, errorText } from '../core/api';
import { AgoPipe, LevelPipe, NumPipe } from '../core/format';
import { AlertItem } from '../core/models';

@Component({
  selector: 'dw-alerts',
  imports: [RouterLink, LevelPipe, AgoPipe, NumPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="stack page">
      <header class="row between">
        <div><h1>Leak alerts</h1><p class="muted">Highest score first. Each score is worked out by the rules, step by step.</p></div>
        <div class="row" role="group" aria-label="Filter">
          @for (s of tabs; track s.key) {
            <button type="button" class="btn" [class.ghost]="status() !== s.key" [class.dark]="status() === s.key"
                    [attr.aria-pressed]="status() === s.key" (click)="status.set(s.key)">{{ s.label }}</button>
          }
        </div>
      </header>
      @if (zone()) { <div class="note">Showing zone {{ zone() }} only. <a routerLink="/dashboard/alerts">Show all zones</a></div> }
      @if (error()) { <div class="error">{{ error() }}</div> }
      <div class="card">
        <div class="table-wrap">
          <table class="data">
            <thead><tr><th>Score</th><th>Zone</th><th>What the rules saw</th><th>Where to look</th><th>Water lost</th><th>Raised</th><th>Crew</th></tr></thead>
            <tbody>
              @for (a of shown(); track a.id) {
                <tr class="clickable" [routerLink]="['/dashboard/alerts', a.id]">
                  <td><div class="row nowrap"><div class="score-box" [class]="a.level">{{ a.score }}</div><span class="pill" [class]="a.level">{{ a.level | level }}</span></div></td>
                  <td><b>{{ a.zone_name }}</b><div class="tiny muted mono">{{ a.zone }}</div></td>
                  <td>{{ a.headline }}@if (a.status === 'resolved') {<div class="tiny ok">Closed</div>}
                    @if (a.model_status !== 'ok' && a.kind !== 'public') {<div class="tiny muted">Rules only (AI assist {{ a.model_status }})</div>}</td>
                  <td>{{ a.street || (a.kind === 'public' ? 'Where people reported' : '–') }}
                    @if (a.top_area?.probability) {<div class="tiny muted">Area {{ a.top_area!.area }}, {{ a.top_area!.probability! * 100 | num }}% likely</div>}</td>
                  <td class="mono">{{ a.water_lost_m3_per_day ? (a.water_lost_m3_per_day | num) + ' m³/day' : '–' }}</td>
                  <td class="small">{{ a.created | ago }}</td>
                  <td class="small">{{ a.job ? a.job.crew + ' (' + a.job.status + ')' : 'Not sent' }}</td>
                </tr>
              } @empty { <tr><td colspan="7" class="muted">Nothing here.</td></tr> }
            </tbody>
          </table>
        </div>
      </div>
    </div>
  `,
  styles: [`.page { gap: 20px; max-width: 1280px; } h1 { font-size: 32px; } .nowrap { flex-wrap: nowrap; gap: 8px; } .ok { color: var(--ok-text); font-weight: 600; }`],
})
export class AlertsPage {
  private api = inject(Api);
  zone = input<string>();
  status = signal<'open' | 'resolved' | 'all'>('open');
  items = signal<AlertItem[]>([]);
  error = signal('');
  tabs = [{ key: 'open' as const, label: 'Open' }, { key: 'resolved' as const, label: 'Closed' }, { key: 'all' as const, label: 'All' }];
  shown = computed(() => this.items().filter(a => !this.zone() || a.zone === this.zone()));

  constructor() {
    effect(() => { this.load(this.status()); });
  }

  async load(status: 'open' | 'resolved' | 'all') {
    try { this.items.set(await this.api.alerts(status)); this.error.set(''); }
    catch (e) { this.error.set(errorText(e)); }
  }
}
