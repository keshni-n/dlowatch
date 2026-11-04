import { ChangeDetectionStrategy, Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { Api, errorText } from '../core/api';
import { I18n } from '../core/i18n';
import { PublicNearby } from '../core/models';
import { MyReports } from '../core/my-reports';
import { shrinkImage } from '../shared/bits';
import { LeakMap } from '../shared/leak-map';
import { PublicLive } from './live';

const KINDS = [
  { key: 'water_on_road', kreol: 'Dlo lor semin', fr: "De l'eau sur la route", en: 'Water on the road' },
  { key: 'broken_pipe', kreol: 'Tiyo kase', fr: 'Tuyau casse', en: 'Broken pipe' },
  { key: 'no_water', kreol: 'Pa ena dlo', fr: "Pas d'eau", en: 'No water at home' },
  { key: 'low_pressure', kreol: 'Presion feb', fr: 'Pression faible', en: 'Low pressure' },
];

function deviceId(): string {
  try {
    let id = localStorage.getItem('dlowatch.device');
    if (!id) {
      id = crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2) + Date.now().toString(36);
      localStorage.setItem('dlowatch.device', id);
    }
    return id;
  } catch { return 'nostore-' + Math.random().toString(36).slice(2, 12); }
}

/** Centre of the network zones, used only when the phone cannot give a location. */
function networkCentre(fc: GeoJSON.FeatureCollection | null | undefined): [number, number] | null {
  const pts: number[][] = [];
  for (const f of fc?.features ?? []) {
    const g = f.geometry as GeoJSON.Polygon;
    if (g?.type === 'Polygon') pts.push(...g.coordinates[0]);
  }
  if (!pts.length) return null;
  const lon = pts.reduce((a, p) => a + p[0], 0) / pts.length;
  const lat = pts.reduce((a, p) => a + p[1], 0) / pts.length;
  return [+lat.toFixed(5), +lon.toFixed(5)];
}

@Component({
  selector: 'dw-public-report',
  imports: [FormsModule, LeakMap],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <form class="stack page" (ngSubmit)="send()">
      <div>
        <h1>{{ i.t('title') }}</h1>
        <p class="muted sub">{{ i.t('takes') }}</p>
      </div>
      <ol class="progress" aria-label="Steps">
        <li [class.done]="!!photo()">1 · {{ i.t('photo') }}</li>
        <li [class.done]="!!pin()">2 · {{ i.t('where') }}</li>
        <li [class.done]="!!kind()">3 · {{ i.t('what') }}</li>
      </ol>

      <section class="card stack">
        <label class="photo">
          <input type="file" accept="image/*" capture="environment" (change)="pickPhoto($event)">
          @if (photo()) {
            <img [src]="photo()" alt="Your photo">
          } @else {
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" style="color: var(--link)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 8h3l2-3h6l2 3h3v11H4z"></path><circle cx="12" cy="13" r="3.5"></circle></svg>
            <b>{{ i.t('photo') }}</b><span class="tiny muted">{{ i.t('photoOpt') }}</span>
          }
        </label>
        @if (photo()) { <button type="button" class="btn ghost" (click)="photo.set('')">{{ i.t('remove') }}</button> }
      </section>

      <section class="card stack">
        <div class="row between"><b>{{ i.t('where') }}</b>
          <button type="button" class="btn secondary mini" (click)="locate()">{{ i.t('useLoc') }}</button></div>
        <dw-leak-map [pin]="pin()" [pickable]="true" [height]="220" fitTo="pin" (picked)="pin.set($event)" />
        <span class="tiny muted">{{ locMsg() || i.t('tapMap') }}</span>
        @if (nearby(); as n) {
          @if (n.known; as k) {
            <div class="known">
              <b>{{ i.t('knownTitle') }}</b>
              <div class="small">{{ k.status === 'crew' ? i.t('knownCrew') : i.t('knownChecking') }} {{ k.distance_m }} {{ i.t('fromHere') }}{{ k.street ? ' (' + k.street + ')' : '' }}.</div>
              <div class="small muted">{{ i.t('knownMore') }}</div>
            </div>
          } @else if (!n.inside_network) {
            <div class="note small">{{ i.t('outside') }}</div>
          } @else if (n.area) {
            <div class="tiny muted">{{ i.t('area') }}: {{ n.area }}</div>
          }
        }
      </section>

      <section class="card stack">
        <fieldset>
          <legend class="field">{{ i.t('what') }}</legend>
          <div class="choice-grid">
            @for (k of kinds; track k.key) {
              <button type="button" class="choice" [attr.aria-pressed]="kind() === k.key" (click)="kind.set(k.key)">
                <b>{{ label(k) }}</b>@if (i.lang() !== 'en') {<span class="sub">{{ k.en }}</span>}
              </button>
            }
          </div>
        </fieldset>
        <label class="field">{{ i.t('note') }}
          <input class="input" name="note" [(ngModel)]="note" maxlength="300">
        </label>
      </section>

      @if (error()) { <div class="error" role="alert">{{ error() }}</div> }
      <button class="btn block" type="submit" [disabled]="busy()">{{ busy() ? i.t('sending') : i.t('send') }}</button>
      <div class="tiny muted center">{{ i.t('noName') }}</div>
    </form>
  `,
  styles: [`
    .page { gap: 14px; max-width: 640px; margin: 0 auto; }
    h1 { font-size: 28px; line-height: 1.15; }
    .sub { margin: 6px 0 0; }
    .progress { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; font-size: 12px; font-weight: 600; color: var(--muted); }
    .progress li { border-top: 4px solid var(--track); padding-top: 6px; }
    .progress li.done { border-color: var(--link); color: var(--text); }
    .photo { position: relative; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px; height: 140px;
      border: 2px dashed var(--line-hover); border-radius: 14px; background: var(--surface-2); cursor: pointer; overflow: hidden; }
    .photo input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }
    .photo img { width: 100%; height: 100%; object-fit: cover; }
    .mini { min-height: 38px; padding: 0 12px; font-size: 13px; }
    .known { background: var(--watch-pill); color: var(--text); border-radius: 10px; padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; }
    fieldset { border: none; margin: 0; padding: 0; }
    legend { margin-bottom: 8px; padding: 0; }
    .center { text-align: center; }
  `],
})
export class PublicReport {
  i = inject(I18n);
  private api = inject(Api);
  private router = inject(Router);
  private mine = inject(MyReports);
  private live = inject(PublicLive);
  kinds = KINDS;
  kind = signal('');
  pin = signal<[number, number] | null>(null);
  photo = signal('');
  note = '';
  busy = signal(false);
  error = signal('');
  locMsg = signal('');
  nearby = signal<PublicNearby | null>(null);
  private pinKey = computed(() => this.pin()?.join(',') ?? '');

  constructor() {
    if (!this.live.areas()) this.live.load();
    this.locate();
    // when the pin moves, ask whether this problem is already known
    effect(() => {
      const key = this.pinKey();
      const p = this.pin();
      if (!key || !p) { this.nearby.set(null); return; }
      this.api.publicNearby(p[0], p[1]).then(n => { if (this.pinKey() === key) this.nearby.set(n); }).catch(() => this.nearby.set(null));
    });
  }

  label(k: (typeof KINDS)[number]) { return k[this.i.lang()]; }

  locate() {
    if (!navigator.geolocation) { this.fallback(); return; }
    navigator.geolocation.getCurrentPosition(
      p => { this.pin.set([+p.coords.latitude.toFixed(5), +p.coords.longitude.toFixed(5)]); this.locMsg.set(''); },
      () => this.fallback(),
      { enableHighAccuracy: true, timeout: 8000 },
    );
  }

  /** No location from the phone: start the map in the middle of the network, the person taps the real spot. */
  private fallback() {
    if (!this.pin()) {
      const c = networkCentre(this.live.areas()?.areas);
      if (c) this.pin.set(c);
    }
    this.locMsg.set(this.i.t('locFail'));
  }

  async pickPhoto(e: Event) {
    const f = (e.target as HTMLInputElement).files?.[0];
    if (!f) return;
    try { this.photo.set(await shrinkImage(f)); } catch { this.error.set('Could not read that photo.'); }
  }

  async send() {
    this.error.set('');
    if (!this.pin()) { this.error.set(this.i.t('needPlace')); return; }
    if (!this.kind()) { this.error.set(this.i.t('needKind')); return; }
    this.busy.set(true);
    try {
      const [lat, lon] = this.pin()!;
      const r = await this.api.sendReport({ lat, lon, kind: this.kind(), note: this.note || null, device: deviceId(), photo: this.photo() || null });
      this.mine.add(r.code, r.status);
      this.router.navigate(['/report', r.code], { queryParams: r.note_cleaned ? { sent: 1, cleaned: 1 } : { sent: 1 } });
    } catch (e) {
      this.error.set(errorText(e));
    }
    this.busy.set(false);
  }
}
