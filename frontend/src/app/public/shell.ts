import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { I18n } from '../core/i18n';
import { LangSwitch, Logo, ThemeToggle } from '../shared/bits';

/** Frame for every public page: top bar, menu on desktop, tab bar on phones. */
@Component({
  selector: 'dw-public-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, Logo, LangSwitch, ThemeToggle],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="frame">
      <header class="top">
        <div class="bar">
          <a routerLink="/" class="brand"><dw-logo [size]="26" color="var(--link)" /><b>DloWatch</b></a>
          <nav class="desk" aria-label="Main">
            @for (t of tabs; track t.path) {
              <a [routerLink]="t.path" routerLinkActive="on" [routerLinkActiveOptions]="{ exact: t.exact }" [class.cta]="t.cta">{{ i.t(t.key) }}</a>
            }
          </nav>
          <div class="tools"><dw-lang /><dw-theme-toggle /></div>
        </div>
      </header>
      <div class="proto" role="note">{{ i.t('prototype') }}</div>
      <main class="content"><router-outlet /></main>
      <nav class="tabs" aria-label="Main">
        @for (t of tabs; track t.path) {
          <a [routerLink]="t.path" routerLinkActive="on" [routerLinkActiveOptions]="{ exact: t.exact }" [class.cta]="t.cta">
            <span class="ic" aria-hidden="true">
              @switch (t.icon) {
                @case ('home') { <svg viewBox="0 0 24 24"><path d="M3 11l9-7 9 7"></path><path d="M5 10v10h14V10"></path></svg> }
                @case ('map') { <svg viewBox="0 0 24 24"><path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"></path><circle cx="12" cy="9.5" r="2.5"></circle></svg> }
                @case ('plus') { <svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"></path></svg> }
                @case ('list') { <svg viewBox="0 0 24 24"><path d="M8 6h13M8 12h13M8 18h13"></path><path d="M3.5 6h.01M3.5 12h.01M3.5 18h.01"></path></svg> }
                @case ('book') { <svg viewBox="0 0 24 24"><path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z"></path><path d="M4 19V5"></path></svg> }
              }
            </span>
            <span class="lbl">{{ i.t(t.key) }}</span>
          </a>
        }
      </nav>
    </div>
  `,
  styles: [`
    .frame { min-height: 100dvh; background: var(--bg); display: flex; flex-direction: column; }
    .top { background: var(--surface); border-bottom: 1px solid var(--line); position: sticky; top: 0; z-index: 10; }
    .bar { max-width: 1120px; margin: 0 auto; padding: 12px 18px; display: flex; align-items: center; gap: 20px; }
    .brand { display: inline-flex; align-items: center; gap: 8px; color: var(--text); text-decoration: none; font-size: 18px; }
    .desk { display: none; gap: 4px; margin-left: 12px; flex: 1; }
    .desk a { padding: 10px 14px; border-radius: 8px; color: var(--text-2); text-decoration: none; font-weight: 600; font-size: 15px; }
    .desk a:hover { background: var(--ground); color: var(--text); }
    .desk a.on { color: var(--text); background: var(--blue-soft); }
    .desk a.cta { background: var(--blue); color: #fff; order: 9; margin-left: auto; }
    .desk a.cta.on, .desk a.cta:hover { background: var(--blue-dark); color: #fff; }
    .tools { display: flex; align-items: center; gap: 8px; margin-left: auto; }
    .proto { background: var(--ground); border-bottom: 1px solid var(--line); color: var(--text-2); font-size: 12.5px; line-height: 1.4; text-align: center; padding: 7px 16px; }
    .content { flex: 1; width: 100%; max-width: 1120px; margin: 0 auto; padding: 18px 16px 96px; }
    .tabs { position: fixed; left: 0; right: 0; bottom: 0; z-index: 20; background: var(--surface); border-top: 1px solid var(--line);
      display: grid; grid-template-columns: repeat(5, 1fr); padding: 6px 4px calc(6px + env(safe-area-inset-bottom)); }
    .tabs a { display: flex; flex-direction: column; align-items: center; gap: 3px; padding: 6px 2px; min-height: 52px; color: var(--muted); text-decoration: none; font-size: 11px; font-weight: 600; }
    .tabs a.on { color: var(--link); }
    .ic { width: 26px; height: 26px; display: flex; align-items: center; justify-content: center; }
    .ic svg { width: 22px; height: 22px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
    .tabs a.cta .ic { width: 46px; height: 46px; margin-top: -20px; border-radius: 99px; background: var(--blue); color: #fff; box-shadow: 0 4px 12px rgba(11, 95, 165, 0.35); }
    .tabs a.cta { color: var(--link); }
    .lbl { text-align: center; line-height: 1.1; }
    @media (min-width: 860px) {
      .desk { display: flex; }
      .tools { margin-left: 0; }
      .tabs { display: none; }
      .content { padding: 28px 24px 48px; }
    }
  `],
})
export class PublicShell {
  i = inject(I18n);
  tabs = [
    { path: '/', key: 'navHome', icon: 'home', exact: true, cta: false },
    { path: '/area', key: 'navArea', icon: 'map', exact: false, cta: false },
    { path: '/report', key: 'navReport', icon: 'plus', exact: true, cta: true },
    { path: '/my-reports', key: 'navMine', icon: 'list', exact: false, cta: false },
    { path: '/learn', key: 'navLearn', icon: 'book', exact: false, cta: false },
  ];
}
