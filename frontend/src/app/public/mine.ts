import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AgoPipe, DateTimePipe } from '../core/format';
import { I18n } from '../core/i18n';
import { MyReports, stage } from '../core/my-reports';
import { PublicStatus } from '../core/models';

const STAGE_KEY: Record<string, string> = { received: 'sRec', linked: 'sLinked', crew: 'sCrew', fixed: 'sFixed', unknown: 'unknown' };
const STAGE_CLS: Record<string, string> = { received: 'low', linked: 'watch', crew: 'high', fixed: 'ok', unknown: 'neutral' };
const KIND: Record<string, Record<string, string>> = {
  water_on_road: { kreol: 'Dlo lor semin', fr: "De l'eau sur la route", en: 'Water on the road' },
  broken_pipe: { kreol: 'Tiyo kase', fr: 'Tuyau casse', en: 'Broken pipe' },
  no_water: { kreol: 'Pa ena dlo', fr: "Pas d'eau", en: 'No water at home' },
  low_pressure: { kreol: 'Presion feb', fr: 'Pression faible', en: 'Low pressure' },
};

@Component({
  selector: 'dw-public-mine',
  imports: [FormsModule, RouterLink, AgoPipe, DateTimePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="stack page">
      <header class="row between">
        <div><h1>{{ i.t('mineTitle') }}</h1><p class="muted sub">{{ i.t('mineSub') }}</p></div>
        @if (mine.items().length) { <button type="button" class="btn secondary" (click)="mine.refresh()">{{ i.t('refresh') }}</button> }
      </header>
      @if (mine.offline()) { <div class="note">{{ i.t('savedCopy') }}</div> }

      <div class="list">
        @for (r of mine.items(); track r.code) {
          <a class="card item" [routerLink]="['/report', r.code]">
            <div class="row between">
              <span class="mono code">{{ r.code }}</span>
              <span class="pill" [class]="cls(r.status)">{{ stageText(r.status) }}</span>
            </div>
            @if (r.status) {
              <div class="small">{{ kind(r.status.kind) }} · {{ r.status.area }}</div>
              <div class="steps" aria-hidden="true">
                @for (s of r.status.steps; track s.key) { <span [class.done]="s.done"></span> }
              </div>
            }
            <div class="tiny muted">{{ r.sentAt | dateTime }}@if (r.checkedAt) { · {{ i.t('lastChecked') }} {{ r.checkedAt | ago }} }</div>
          </a>
        } @empty {
          <div class="card empty stack">
            <p class="muted">{{ i.t('mineEmpty') }}</p>
            <a routerLink="/report" class="btn">{{ i.t('reportCta') }}</a>
          </div>
        }
      </div>

      <form class="card stack" (ngSubmit)="track()">
        <b>{{ i.t('trackOther') }}</b>
        <div class="row nowrap">
          <label class="field grow small-label">{{ i.t('myCode') }}
            <input class="input mono" name="code" [(ngModel)]="code" placeholder="R-0000" autocapitalize="characters">
          </label>
          <button class="btn dark go" type="submit" [disabled]="!code.trim()">{{ i.t('get') }}</button>
        </div>
      </form>
    </div>
  `,
  styles: [`
    .page { gap: 16px; max-width: 720px; margin: 0 auto; }
    h1 { font-size: 28px; }
    .sub { margin: 6px 0 0; }
    .list { display: flex; flex-direction: column; gap: 10px; }
    .item { display: flex; flex-direction: column; gap: 8px; text-decoration: none; color: var(--text); }
    .item:hover { border-color: var(--line-hover); color: var(--text); }
    .code { font-weight: 600; font-size: 17px; }
    .steps { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; }
    .steps span { height: 6px; border-radius: 99px; background: var(--track); }
    .steps span.done { background: var(--blue); }
    .empty { align-items: flex-start; }
    .nowrap { flex-wrap: nowrap; align-items: flex-end; gap: 8px; }
    .small-label { font-size: 12px; color: var(--muted); font-weight: 500; }
    .go { min-height: 48px; }
  `],
})
export class PublicMine {
  i = inject(I18n);
  mine = inject(MyReports);
  private router = inject(Router);
  code = '';

  constructor() { this.mine.refresh(); }

  stageText(s: PublicStatus | null) { return this.i.t(STAGE_KEY[stage(s)]); }
  cls(s: PublicStatus | null) { return STAGE_CLS[stage(s)]; }
  kind(k: string) { return KIND[k]?.[this.i.lang()] ?? k; }

  track() {
    const c = this.code.trim().toUpperCase();
    if (c) this.router.navigate(['/report', c.startsWith('R-') ? c : 'R-' + c]);
  }
}
