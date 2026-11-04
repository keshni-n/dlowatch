import { ChangeDetectionStrategy, Component, computed, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api } from '../core/api';
import { AgoPipe, DateTimePipe } from '../core/format';
import { I18n } from '../core/i18n';
import { PublicNotice } from '../core/models';
import { LeakMap, MapPoint } from '../shared/leak-map';
import { PublicLive, STATUS_CLASS, STATUS_FILL, STATUS_KEY, noticeText } from './live';

@Component({
  selector: 'dw-public-area',
  imports: [RouterLink, LeakMap, AgoPipe, DateTimePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="stack page">
      <header>
        <h1>{{ i.t('areaTitle') }}</h1>
        <p class="muted sub">{{ i.t('areaSub') }}
          @if (live.savedAt()) { <span class="tiny"> · {{ i.t('updated') }} {{ live.savedAt() | ago }}</span> }</p>
      </header>
      @if (live.offline()) { <div class="note">{{ live.areas() ? i.t('savedCopy') : i.t('liveOff') }}</div> }

      <div class="layout">
        <section class="card map-card">
          <div class="row between">
            <div class="legend">
              @for (s of statuses; track s) { <span><i [style.background]="fill(s)"></i>{{ i.t(key(s)) }}</span> }
            </div>
            <button type="button" class="btn secondary mini" (click)="locate()">{{ i.t('useLoc') }}</button>
          </div>
          <dw-leak-map [zones]="live.areas()?.areas ?? null" zoneProp="status" [zoneColors]="fills" [zoneTooltip]="tip"
                       [points]="points()" [pin]="here()" [height]="420" (zoneClick)="zone.set($event)" />
          @if (locError()) { <div class="small muted">{{ i.t('locFail') }}</div> }
          @if (mine(); as m) {
            <div class="mine">
              @if (m.area) {
                <b>{{ m.area }}</b>: <span class="pill" [class]="cls(m.status)">{{ i.t(key(m.status)) }}</span>
              } @else { {{ i.t('notInNetwork') }} }
            </div>
          }
        </section>

        <section class="list stack">
          @if (zone()) {
            <div class="row between"><b>{{ zoneName() }}</b><button type="button" class="linkish small" (click)="zone.set('')">{{ i.t('liveAll') }}</button></div>
          }
          @for (n of shown(); track n.id) {
            <div class="card notice" [class.focus]="n.id === focus()">
              <div class="row between"><b>{{ n.area }}</b><span class="pill" [class]="cls(n.status)">{{ i.t(key(n.status)) }}</span></div>
              <div class="small">{{ text(n) }}</div>
              <div class="tiny muted">{{ n.since | dateTime }}</div>
            </div>
          } @empty {
            <div class="card muted small">{{ live.areas() ? i.t('noNotices') : '...' }}</div>
          }
          <a routerLink="/report" class="btn block">{{ i.t('reportCta') }}</a>
        </section>
      </div>
    </div>
  `,
  styles: [`
    .page { gap: 16px; }
    h1 { font-size: 28px; }
    .sub { margin: 6px 0 0; }
    .layout { display: grid; gap: 16px; }
    .map-card { display: flex; flex-direction: column; gap: 10px; padding: 14px; }
    .legend { display: flex; gap: 10px; flex-wrap: wrap; font-size: 12px; color: var(--text-2); }
    .legend span { display: inline-flex; gap: 6px; align-items: center; }
    .legend i { width: 12px; height: 12px; border-radius: 3px; display: inline-block; }
    .mini { min-height: 38px; padding: 0 12px; font-size: 13px; }
    .mine { font-size: 15px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .notice { display: flex; flex-direction: column; gap: 6px; padding: 14px 16px; }
    .notice.focus { border: 2px solid var(--blue); }
    .linkish { background: none; border: none; color: var(--link); cursor: pointer; text-decoration: underline; min-height: 32px; }
    @media (min-width: 860px) { .layout { grid-template-columns: 1.6fr 1fr; align-items: start; } }
  `],
})
export class PublicArea {
  i = inject(I18n);
  live = inject(PublicLive);
  private api = inject(Api);
  focus = input<string>();
  zone = signal('');
  here = signal<[number, number] | null>(null);
  mine = signal<{ area: string | null; status: string } | null>(null);
  locError = signal(false);

  statuses = ['normal', 'checking', 'crew', 'fixed'];
  fills = STATUS_FILL;
  cls = (s: string) => STATUS_CLASS[s] ?? 'neutral';
  key = (s: string) => STATUS_KEY[s] ?? 'stNormal';
  fill = (s: string) => STATUS_FILL[s];
  text = (n: PublicNotice) => noticeText(n, this.i);
  tip = (p: Record<string, unknown>) => `<b>${p['name']}</b><br>${this.i.t(STATUS_KEY[String(p['status'])] ?? 'stNormal')}`;

  shown = computed(() => (this.live.areas()?.notices ?? []).filter(n => !this.zone() || n.zone === this.zone()));
  zoneName = computed(() => this.live.areas()?.areas.features.find(f => f.properties?.['zone'] === this.zone())?.properties?.['name'] ?? this.zone());
  points = computed<MapPoint[]>(() => (this.live.areas()?.notices ?? []).filter(n => n.latlon).map(n => ({
    latlon: n.latlon!, color: n.status === 'fixed' ? '#2F8F5B' : n.status === 'crew' ? '#C2410C' : '#B07A00', size: 7,
    label: `${n.area}: ${this.i.t(STATUS_KEY[n.status])}`,
  })));

  constructor() { this.live.load(); }

  locate() {
    this.locError.set(false);
    if (!navigator.geolocation) { this.locError.set(true); return; }
    navigator.geolocation.getCurrentPosition(async p => {
      const ll: [number, number] = [+p.coords.latitude.toFixed(5), +p.coords.longitude.toFixed(5)];
      this.here.set(ll);
      try {
        const n = await this.api.publicNearby(ll[0], ll[1]);
        const z = this.live.areas()?.areas.features.find(f => f.properties?.['zone'] === n.zone);
        this.mine.set({ area: n.area, status: String(z?.properties?.['status'] ?? 'normal') });
        if (n.zone) this.zone.set(n.zone);
      } catch { this.mine.set(null); }
    }, () => { this.mine.set(null); this.locError.set(true); }, { timeout: 8000 });
  }
}
