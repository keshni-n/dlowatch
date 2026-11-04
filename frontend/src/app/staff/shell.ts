import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { Api } from '../core/api';
import { Auth } from '../core/auth';
import { SystemStatus } from '../core/models';
import { Logo, ThemeToggle } from '../shared/bits';

/** Side menu + page area for every staff page. */
@Component({
  selector: 'dw-staff-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, Logo, ThemeToggle],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="layout">
      <nav aria-label="Main">
        <div class="row brand"><dw-logo /><div><div class="name">DloWatch</div><div class="tag">Water loss detection</div></div></div>
        <div class="links">
          <a routerLink="/dashboard" routerLinkActive="on" [routerLinkActiveOptions]="{ exact: true }">Overview</a>
          <a routerLink="/dashboard/alerts" routerLinkActive="on"><span>Leak alerts</span>@if (counts().alerts) {<b class="badge orange">{{ counts().alerts }}</b>}</a>
          <a routerLink="/dashboard/repairs" routerLinkActive="on">Repair list</a>
          <a routerLink="/dashboard/meters" routerLinkActive="on"><span>Zones and meters</span>@if (counts().meters) {<b class="badge sand">{{ counts().meters }}</b>}</a>
          <a routerLink="/dashboard/loggers" routerLinkActive="on"><span>Noise loggers</span>@if (counts().noisy) {<b class="badge orange">{{ counts().noisy }}</b>}</a>
          <a routerLink="/dashboard/reports" routerLinkActive="on"><span>Public reports</span>@if (counts().reports) {<b class="badge blue">{{ counts().reports }}</b>}</a>
          <a routerLink="/dashboard/crews" routerLinkActive="on">Field crews</a>
          <a routerLink="/dashboard/report" routerLinkActive="on">Water loss report</a>
          @if (auth.isAdmin()) {
            <a routerLink="/dashboard/simulate" routerLinkActive="on">System test</a>
          }
        </div>
        <div class="foot">
          @if (sys(); as st) {
            <span class="status" [class.bad]="st.model.status !== 'online'">
              <i></i>{{ st.model.status === 'online' ? 'All systems running' : 'AI assist offline, rules still running' }}
            </span>
          }
          <dw-theme-toggle class="on-dark" [label]="true" />
          <span>Signed in as {{ auth.staffUser()?.name }}</span>
          <button type="button" class="linkish" (click)="signOut()">Sign out</button>
        </div>
      </nav>
      <main><router-outlet /></main>
    </div>
  `,
  styles: [`
    .layout { display: flex; flex-wrap: wrap; min-height: 100dvh; align-items: stretch; }
    nav { flex: 1 1 220px; max-width: 260px; background: var(--panel); color: #E6EDF2; padding: 26px 18px; display: flex; flex-direction: column; gap: 26px; }
    .brand { gap: 10px; }
    .name { font-weight: 700; font-size: 18px; }
    .tag { font-size: 12px; color: #9FB2C0; }
    .links { display: flex; flex-direction: column; gap: 4px; }
    .links a { display: flex; justify-content: space-between; align-items: center; padding: 11px 12px; border-radius: 8px; color: #C9D6DF; text-decoration: none; font-size: 15px; min-height: 44px; }
    .links a:hover { background: var(--panel-hover); color: #fff; }
    .links a.on { background: var(--panel-2); color: #fff; font-weight: 600; }
    .badge { font-size: 12px; padding: 2px 8px; border-radius: 99px; }
    .badge.orange { background: #F29A55; color: #2A1405; }
    .badge.sand { background: #F3D27E; color: #3A2A00; }
    .badge.blue { background: #9CCBF2; color: var(--text); }
    .demo { font-size: 11px; background: #2A3F4E; color: #C9D6DF; padding: 1px 6px; border-radius: 6px; margin-left: 6px; }
    .foot { margin-top: auto; border-top: 1px solid var(--ink-line); padding-top: 16px; font-size: 13px; color: #B5C4CF; display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
    .status { display: inline-flex; align-items: center; gap: 8px; color: #C9D6DF; text-decoration: none; }
    .status i { width: 8px; height: 8px; border-radius: 99px; background: #5FB3F0; display: inline-block; }
    .status.bad { color: #F6B07C; } .status.bad i { background: #F6B07C; }
    .tiny-light { font-size: 12px; color: #9FB2C0; }
    .linkish { background: none; border: none; color: #9CCBF2; padding: 0; cursor: pointer; text-decoration: underline; min-height: 32px; }
    main { flex: 999 1 560px; min-width: 0; padding: 30px 36px 48px; }
    @media print { nav { display: none !important; } main { padding: 0 !important; } }
    @media (max-width: 820px) {
      nav { max-width: none; flex-basis: 100%; padding: 16px; gap: 14px; }
      .links { flex-direction: row; flex-wrap: wrap; }
      .links a { padding: 8px 10px; gap: 6px; }
      .foot { flex-direction: row; margin-top: 0; padding-top: 10px; align-items: center; }
      main { padding: 20px 16px 40px; }
    }
  `],
})
export class StaffShell {
  auth = inject(Auth);
  private api = inject(Api);
  private router = inject(Router);
  counts = signal({ alerts: 0, meters: 0, reports: 0, noisy: 0 });
  sys = signal<SystemStatus | null>(null);

  constructor() {
    this.load();
    const timer = setInterval(() => this.load(), 30000);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }

  async load() {
    try {
      this.api.systemStatus().then(s => this.sys.set(s)).catch(() => undefined);
      const o = await this.api.overview();
      const r = await this.api.reports();
      this.counts.set({
        alerts: o.alerts.length,
        meters: o.meters.noisy + o.meters.offline,
        reports: r.reports.filter(x => x.status !== 'fixed').length,
        noisy: o.loggers?.noisy ?? 0,
      });
    } catch { /* the page itself shows errors */ }
  }

  signOut() {
    this.auth.signOut('staff');
    this.router.navigateByUrl('/login');
  }
}
