import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { I18n } from '../core/i18n';
import { PublicLive } from './live';

/** Plain advice. Works offline once the app has been opened (all text ships with the app). */
@Component({
  selector: 'dw-public-learn',
  imports: [RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="stack page">
      <h1>{{ i.t('learnTitle') }}</h1>

      <section class="card stack">
        <h2>{{ i.t('l1t') }}</h2>
        <ol class="numbered">
          @for (k of ['l1a', 'l1b', 'l1c', 'l1d']; track k; let n = $index) {
            <li><span class="n mono">{{ n + 1 }}</span><span>{{ i.t(k) }}</span></li>
          }
        </ol>
      </section>

      <section class="card stack">
        <h2>{{ i.t('l2t') }}</h2>
        <ul class="ticks">@for (k of ['l2a', 'l2b', 'l2c', 'l2d']; track k) { <li>{{ i.t(k) }}</li> }</ul>
        <a routerLink="/report" class="btn">{{ i.t('reportCta') }}</a>
      </section>

      <section class="card stack">
        <h2>{{ i.t('l3t') }}</h2>
        <ol class="numbered">
          @for (k of ['l3a', 'l3b', 'l3c', 'l3d']; track k; let n = $index) {
            <li><span class="n mono">{{ n + 1 }}</span><span>{{ i.t(k) }}</span></li>
          }
        </ol>
      </section>

      <section class="card stack">
        <h2>{{ i.t('l4t') }}</h2>
        <ul class="ticks">@for (k of ['l4a', 'l4b', 'l4c', 'l4d']; track k) { <li>{{ i.t(k) }}</li> }</ul>
      </section>

      @if (live.info()?.hotline) {
        <div class="note">{{ i.t('emergency') }} <a [href]="'tel:' + live.info()!.hotline"><b>{{ live.info()!.hotline }}</b></a></div>
      }
    </div>
  `,
  styles: [`
    .page { gap: 16px; max-width: 760px; margin: 0 auto; }
    h1 { font-size: 28px; }
    h2 { font-size: 18px; }
    .numbered { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
    .numbered li { display: flex; gap: 12px; align-items: flex-start; font-size: 15px; }
    .n { width: 28px; height: 28px; border-radius: 99px; background: var(--panel); color: #fff; display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 600; flex: none; }
    .ticks { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 8px; font-size: 15px; }
    .btn { align-self: flex-start; }
  `],
})
export class PublicLearn {
  i = inject(I18n);
  live = inject(PublicLive);
  constructor() { if (!this.live.info()) this.live.load(); }
}
