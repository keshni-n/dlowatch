import { ChangeDetectionStrategy, Component, Injectable, computed, inject, input, signal } from '@angular/core';
import { I18n, Lang } from '../core/i18n';

@Component({
  selector: 'dw-logo',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <svg [attr.width]="size()" [attr.height]="size()" viewBox="0 0 24 24" fill="none" [style.stroke]="color()" stroke-width="2"
         stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M12 2.5c3.5 4.5 6 8 6 11a6 6 0 0 1-12 0c0-3 2.5-6.5 6-11z"></path><path d="M9 14.5a3 3 0 0 0 3 3"></path>
    </svg>`,
  styles: [`:host { display: inline-flex; }`],
})
export class Logo {
  size = input(28);
  color = input('#5FB3F0');
}

@Component({
  selector: 'dw-lang',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="lang" role="group" aria-label="Language">
      @for (l of langs; track l.key) {
        <button type="button" [attr.aria-pressed]="i18n.lang() === l.key" (click)="i18n.set(l.key)">{{ l.label }}</button>
      }
    </div>`,
})
export class LangSwitch {
  i18n = inject(I18n);
  langs: { key: Lang; label: string }[] = [{ key: 'en', label: 'EN' }, { key: 'fr', label: 'FR' }, { key: 'kreol', label: 'Kreol' }];
}

/** Read a picked image, shrink it, and return a JPEG data URL (keeps uploads small on mobile data). */
export function shrinkImage(file: File, max = 1280): Promise<string> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const scale = Math.min(1, max / Math.max(img.width, img.height));
      const c = document.createElement('canvas');
      c.width = Math.round(img.width * scale);
      c.height = Math.round(img.height * scale);
      c.getContext('2d')!.drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      resolve(c.toDataURL('image/jpeg', 0.75));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Could not read the photo')); };
    img.src = url;
  });
}

export type ThemeMode = 'light' | 'dark';

/**
 * Light or dark theme. Until the person picks one, it follows the phone or computer setting.
 * The choice is kept on the device. index.html applies it before the app loads (no flash).
 */
@Injectable({ providedIn: 'root' })
export class Theme {
  private media = typeof matchMedia === 'function' ? matchMedia('(prefers-color-scheme: dark)') : null;
  private chosen = signal<ThemeMode | null>(this.saved());
  private system = signal<ThemeMode>(this.media?.matches ? 'dark' : 'light');
  readonly mode = computed<ThemeMode>(() => this.chosen() ?? this.system());

  constructor() {
    this.media?.addEventListener?.('change', e => { this.system.set(e.matches ? 'dark' : 'light'); this.apply(); });
    this.apply();
  }

  toggle() {
    this.chosen.set(this.mode() === 'dark' ? 'light' : 'dark');
    try { localStorage.setItem('dlowatch.theme', this.chosen()!); } catch { /* private mode */ }
    this.apply();
  }

  private saved(): ThemeMode | null {
    try { const t = localStorage.getItem('dlowatch.theme'); return t === 'dark' || t === 'light' ? t : null; } catch { return null; }
  }

  private apply() {
    document.documentElement.dataset['theme'] = this.mode();
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', this.mode() === 'dark' ? '#0D151B' : '#0F1B24');
  }
}

@Component({
  selector: 'dw-theme-toggle',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <button type="button" class="tt" (click)="theme.toggle()"
            [attr.aria-label]="theme.mode() === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'"
            [attr.title]="theme.mode() === 'dark' ? 'Light mode' : 'Dark mode'">
      @if (theme.mode() === 'dark') {
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4.5"></circle><path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4L6 18M18 6l1.4-1.4"></path></svg>
      } @else {
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"></path></svg>
      }
      @if (label()) { <span>{{ theme.mode() === 'dark' ? 'Light mode' : 'Dark mode' }}</span> }
    </button>`,
  styles: [`
    .tt { display: inline-flex; align-items: center; gap: 8px; min-width: 40px; height: 40px; padding: 0 10px; justify-content: center;
      border-radius: 99px; border: 1px solid var(--line); background: var(--surface); color: var(--text); cursor: pointer; font-size: 13px; font-weight: 600; }
    .tt:hover { border-color: var(--line-hover); }
    svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
    :host(.on-dark) .tt { background: transparent; border-color: var(--ink-line); color: #E6EDF2; }
  `],
})
export class ThemeToggle {
  theme = inject(Theme);
  label = input(false);
}
