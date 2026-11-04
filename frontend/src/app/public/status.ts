import { ChangeDetectionStrategy, Component, effect, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { HttpErrorResponse } from '@angular/common/http';
import { Api, errorText } from '../core/api';
import { DateTimePipe } from '../core/format';
import { I18n } from '../core/i18n';
import { PublicStatus, PublicStep } from '../core/models';
import { MyReports } from '../core/my-reports';

@Component({
  selector: 'dw-public-status',
  imports: [RouterLink, DateTimePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="stack page">
        @if (error()) { <div class="error">{{ error() }}</div> }
        @if (s(); as s) {
          @if (sent()) {
            <section class="hero">
              <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#5FB3F0" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9.5"></circle><path d="M7.5 12.5l3 3 6-6.5"></path></svg>
              <h1>{{ i.t('thanks') }}</h1>
              <p>{{ i.t('sent') }}</p>
              @if (cleaned()) { <p class="small light">{{ i.t('noteCleaned') }}</p> }
              <div class="row"><span class="small light">{{ i.t('yourCode') }}</span><span class="code mono">{{ s.code }}</span></div>
              <span class="tiny light">{{ i.t('keepCode') }}</span>
            </section>
          } @else {
            <div><div class="small muted">{{ s.created | dateTime }} · {{ i.t('area') }}: {{ s.area }}</div><h1 class="dark-h">{{ s.code }}</h1></div>
          }
          @if (s.others_nearby) {
            <div class="others row nowrap">
              <div class="num mono">{{ s.others_nearby }}</div>
              <div><b>{{ i.t('others') }}</b><div class="small muted">{{ i.t('othersSub') }}</div></div>
            </div>
          }
          <section aria-label="Report status" class="steps">
            <div class="label">{{ i.t('status') }}</div>
            @for (st of s.steps; track st.key; let last = $last; let idx = $index) {
              <div class="step">
                <div class="rail"><span class="dot" [class.done]="st.done" [class.now]="isNow(s.steps, idx)"></span>@if (!last) {<span class="line" [class.done]="st.done"></span>}</div>
                <div class="txt"><div [class.next]="!st.done && !isNow(s.steps, idx)" class="st">{{ stepText(st) }}</div>
                  <div class="tiny muted">{{ i.lang() !== 'en' ? st.en : '' }}{{ st.at ? (i.lang() !== 'en' ? ' · ' : '') + (st.at | dateTime) : '' }}</div></div>
              </div>
            }
          </section>
          <div class="end stack">
            <button type="button" class="btn secondary" (click)="load(code())">{{ i.t('refresh') }}</button>
            <a routerLink="/my-reports" class="btn">{{ i.t('navMine') }}</a>
            <a routerLink="/report" class="btn ghost">{{ i.t('another') }}</a>
          </div>
        } @else if (!error()) { <div class="skeleton">...</div> }
        @if (error()) { <a routerLink="/" class="btn ghost">{{ i.t('back') }}</a> }
    </div>
  `,
  styles: [`
    .page { gap: 16px; max-width: 640px; margin: 0 auto; }
    .hero { background: var(--panel); color: #fff; border-radius: 18px; padding: 24px 20px; display: flex; flex-direction: column; gap: 8px; }
    .hero h1 { font-size: 28px; }
    .hero p { margin: 0; color: #D3DEE6; }
    .light { color: #B5C4CF; }
    .code { font-weight: 600; font-size: 20px; background: var(--panel-2); padding: 4px 12px; border-radius: 8px; }
    .dark-h { font-size: 30px; margin-top: 4px; }
    .others { border: 1px solid var(--line); border-radius: 14px; padding: 16px; gap: 14px; }
    .nowrap { flex-wrap: nowrap; }
    .num { width: 52px; height: 52px; flex: none; border-radius: 12px; background: var(--crit-pill); color: var(--crit-text); display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 22px; }
    .label { font-size: 13px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 10px; }
    .step { display: flex; gap: 14px; min-height: 58px; }
    .rail { display: flex; flex-direction: column; align-items: center; }
    .dot { width: 16px; height: 16px; border-radius: 99px; background: var(--surface); border: 2px solid var(--line-hover); flex: none; }
    .dot.done { background: var(--blue); border-color: var(--link); }
    .dot.now { border: 4px solid var(--blue); }
    .line { width: 2px; flex: 1; background: var(--track); margin-top: 2px; }
    .line.done { background: var(--blue); }
    .st { font-weight: 600; font-size: 15px; line-height: 16px; }
    .st.next { font-weight: 500; color: var(--muted); }
    .txt { padding-bottom: 12px; }
    .end { gap: 8px; }
  `],
})
export class PublicStatusPage {
  i = inject(I18n);
  private api = inject(Api);
  private mine = inject(MyReports);
  code = input.required<string>();
  sent = input<string>();
  cleaned = input<string>();
  s = signal<PublicStatus | null>(null);
  error = signal('');

  constructor() { effect(() => { this.load(this.code()); }); }

  async load(code: string) {
    try {
      const st = await this.api.reportStatus(code);
      this.s.set(st);
      this.error.set('');
      // keep "My reports" up to date (only for codes this phone already knows, or the one just sent)
      if (this.sent() || this.mine.items().some(i => i.code === st.code)) this.mine.add(st.code, st);
    }
    catch (e) {
      this.error.set(e instanceof HttpErrorResponse && e.status === 404 ? this.i.t('notFound') : errorText(e));
    }
  }

  stepText(st: PublicStep) { return this.i.lang() === 'kreol' ? st.kreol : this.i.lang() === 'fr' ? st.fr : st.en; }
  isNow(steps: PublicStep[], idx: number) { return !steps[idx].done && (idx === 0 || steps[idx - 1].done); }
}
