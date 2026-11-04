import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AgoPipe, NumPipe } from '../core/format';
import { I18n } from '../core/i18n';
import { MyReports, stage } from '../core/my-reports';
import { PublicLive, STATUS_CLASS, STATUS_KEY, noticeText } from './live';

const STAGE_KEY: Record<string, string> = { received: 'sRec', linked: 'sLinked', crew: 'sCrew', fixed: 'sFixed', unknown: 'unknown' };

@Component({
  selector: 'dw-public-home',
  imports: [FormsModule, RouterLink, AgoPipe, NumPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="grid">
      <div class="col">
        <section class="hero">
          <h1>{{ i.t('heroTitle') }}</h1>
          <p>{{ i.t('heroSub') }}</p>
          <div class="ctas">
            <a routerLink="/report" class="btn block light">{{ i.t('reportCta') }}</a>
            <a routerLink="/area" class="btn block outline">{{ i.t('checkArea') }}</a>
          </div>
        </section>

        @if (latest(); as r) {
          <a class="card latest" [routerLink]="['/report', r.code]">
            <div class="row between"><span class="kicker">{{ i.t('myLatest') }}</span><span class="mono small">{{ r.code }}</span></div>
            <div class="row between">
              <b>{{ stageText(r) }}</b>
              <span class="small muted">{{ r.sentAt | ago }}</span>
            </div>
            <div class="bar"><span [style.width.%]="stagePct(r)"></span></div>
          </a>
        }

        <section class="card stack how">
          <h2>{{ i.t('l3t') }}</h2>
          <ol>
            @for (k of ['l3a', 'l3b', 'l3c', 'l3d']; track k; let n = $index) {
              <li><span class="n mono">{{ n + 1 }}</span><span class="small">{{ i.t(k) }}</span></li>
            }
          </ol>
        </section>

        <a class="card teaser" routerLink="/learn">
          <div class="drop" aria-hidden="true">
            <svg viewBox="0 0 24 24"><path d="M12 2.5c3.5 4.5 6 8 6 11a6 6 0 0 1-12 0c0-3 2.5-6.5 6-11z"></path></svg>
          </div>
          <div class="grow"><b>{{ i.t('learnTeaserT') }}</b><div class="small muted">{{ i.t('learnTeaserS') }}</div></div>
          <span class="go">{{ i.t('learnCta') }}</span>
        </a>
      </div>

      <div class="col">
        <section class="card stack live">
          <div class="row between"><h2>{{ i.t('liveTitle') }}</h2><a routerLink="/area" class="small strong">{{ i.t('liveAll') }}</a></div>
          @if (live.offline()) {
            <div class="note">{{ live.areas() ? i.t('savedCopy') : i.t('liveOff') }}</div>
          }
          @for (n of notices(); track n.id) {
            <a class="notice" routerLink="/area" [queryParams]="{ focus: n.id }">
              <div class="row between top-line">
                <span class="row gap8"><b>{{ n.area }}</b><span class="pill" [class]="cls(n.status)">{{ i.t(key(n.status)) }}</span></span>
                <span class="tiny muted when">{{ n.since | ago }}</span>
              </div>
              <div class="small">{{ text(n) }}</div>
            </a>
          } @empty {
            @if (live.areas()) { <p class="muted small ok-line">{{ i.t('noNotices') }}</p> }
          }
        </section>

        @if (live.stats(); as s) {
          <section class="card stack">
            <h2>{{ i.t('impactTitle') }}</h2>
            <div class="impact">
              <div><div class="num mono">{{ s.reports_7d }}</div><div class="small muted">{{ i.t('impReports') }}</div></div>
              <div><div class="num mono">{{ s.leaks_fixed_30d }}</div><div class="small muted">{{ i.t('impFixed') }}</div></div>
              <div><div class="num mono blue">{{ s.water_saved_m3_per_day | num }}</div><div class="small muted">{{ i.t('impSaved') }}</div></div>
            </div>
          </section>
        }

        <form class="card stack track" (ngSubmit)="track()">
          <div><b class="t">{{ i.t('track') }}</b><div class="small muted">{{ i.t('trackSub') }}</div></div>
          <div class="row nowrap">
            <label class="field grow small-label">{{ i.t('myCode') }}
              <input class="input mono" name="code" [(ngModel)]="code" placeholder="R-0000" autocapitalize="characters">
            </label>
            <button class="btn dark go" type="submit" [disabled]="!code.trim()">{{ i.t('get') }}</button>
          </div>
        </form>

        @if (live.info()?.hotline) {
          <div class="note">{{ i.t('emergency') }} <a [href]="'tel:' + live.info()!.hotline"><b>{{ live.info()!.hotline }}</b></a></div>
        }
        <div class="foot small muted">{{ i.t('staff') }} <a routerLink="/login">Control room</a> · <a routerLink="/crew/login">Crew</a></div>
      </div>
    </div>
  `,
  styles: [`
    .grid { display: grid; gap: 16px; }
    .col { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
    .hero { background: var(--panel); color: #fff; border-radius: 20px; padding: 26px 22px; display: flex; flex-direction: column; gap: 12px; }
    .hero h1 { font-size: clamp(28px, 4vw, 40px); line-height: 1.1; }
    .hero p { margin: 0; color: #D3DEE6; font-size: 15px; }
    .ctas { display: flex; flex-direction: column; gap: 10px; margin-top: 6px; }
    .outline { background: transparent; border: 1px solid #3A5163; color: #fff; }
    .outline:hover { background: var(--panel-2); color: #fff; }
    .kicker { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); }
    .latest { display: flex; flex-direction: column; gap: 8px; text-decoration: none; color: var(--text); }
    .latest:hover { border-color: var(--line-hover); color: var(--text); }
    .bar { height: 6px; background: var(--track); border-radius: 99px; overflow: hidden; }
    .bar span { display: block; height: 100%; background: var(--blue); border-radius: 99px; }
    .teaser { display: flex; align-items: center; gap: 14px; text-decoration: none; color: var(--text); }
    .teaser:hover { border-color: var(--line-hover); color: var(--text); }
    .drop { width: 46px; height: 46px; border-radius: 12px; background: var(--blue-soft); display: flex; align-items: center; justify-content: center; flex: none; }
    .drop svg { width: 24px; height: 24px; fill: none; stroke: var(--blue); stroke-width: 2; }
    .go { color: var(--link); font-weight: 600; font-size: 14px; white-space: nowrap; }
    h2 { font-size: 18px; }
    .strong { font-weight: 600; }
    .live { gap: 10px; }
    .top-line { flex-wrap: nowrap; }
    .gap8 { gap: 8px; flex-wrap: wrap; }
    .notice { display: flex; flex-direction: column; gap: 6px; padding: 12px; border: 1px solid var(--line); border-radius: 10px; text-decoration: none; color: var(--text); }
    .notice:hover { border-color: var(--line-hover); color: var(--text); }
    .when { white-space: nowrap; }
    .ok-line { margin: 4px 0; }
    .impact { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
    .num { font-size: 26px; font-weight: 700; }
    .blue { color: var(--link); }
    .track { gap: 10px; }
    .t { font-size: 17px; }
    .nowrap { flex-wrap: nowrap; align-items: flex-end; gap: 8px; }
    .small-label { font-size: 12px; color: var(--muted); font-weight: 500; }
    .go.btn, .btn.go { min-height: 48px; }
    .foot { text-align: center; padding: 8px 0; }
    .how { display: none; }
    .how ol { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
    .how li { display: flex; gap: 10px; align-items: flex-start; }
    .how .n { width: 24px; height: 24px; border-radius: 99px; background: var(--blue-soft); color: var(--link); display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; flex: none; }
    @media (min-width: 860px) {
      .grid { grid-template-columns: 1.1fr 1fr; align-items: start; gap: 24px; }
      .ctas { flex-direction: row; }
      .hero { padding: 36px 32px; }
      .how { display: flex; }
    }
  `],
})
export class PublicHome {
  i = inject(I18n);
  live = inject(PublicLive);
  private mine = inject(MyReports);
  private router = inject(Router);
  code = '';

  notices = computed(() => (this.live.areas()?.notices ?? []).slice(0, 4));
  latest = computed(() => this.mine.items()[0] ?? null);
  cls = (s: string) => STATUS_CLASS[s] ?? 'neutral';
  key = (s: string) => STATUS_KEY[s] ?? 'stNormal';
  text = (n: Parameters<typeof noticeText>[0]) => noticeText(n, this.i);

  constructor() {
    this.live.load();
    this.mine.refresh();
  }

  stageText(r: { status: Parameters<typeof stage>[0] }) { return this.i.t(STAGE_KEY[stage(r.status)]); }
  stagePct(r: { status: Parameters<typeof stage>[0] }) {
    return { received: 25, linked: 50, crew: 75, fixed: 100, unknown: 10 }[stage(r.status)];
  }

  track() {
    const c = this.code.trim().toUpperCase();
    if (c) this.router.navigate(['/report', c.startsWith('R-') ? c : 'R-' + c]);
  }
}
