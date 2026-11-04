import { ChangeDetectionStrategy, Component, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { Api, errorText } from '../core/api';
import { Auth } from '../core/auth';
import { Logo, ThemeToggle } from '../shared/bits';

@Component({
  selector: 'dw-staff-login',
  imports: [FormsModule, RouterLink, Logo, ThemeToggle],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="wrap">
      <section class="hero">
        <div class="row"><dw-logo [size]="34" /><span class="brand">DloWatch</span></div>
        <div class="pitch">
          <h1>Find leaks before the water is gone.</h1>
          <p>Clean meter readings, explainable leak scores and one place to send crews. For water utility staff only.</p>
        </div>
      </section>
      <section class="form-side">
        <dw-theme-toggle class="corner" />
        <form (ngSubmit)="submit()" class="stack form">
          <div>
            <h2>Staff sign in</h2>
            <p class="muted">Use your work account. Accounts are created by an admin.</p>
          </div>
          <label class="field">Work email
            <input class="input" type="email" name="email" [(ngModel)]="email" autocomplete="username" required>
          </label>
          <label class="field">Password
            <input class="input" type="password" name="password" [(ngModel)]="password" autocomplete="current-password" required>
          </label>
          @if (error()) { <div class="error" role="alert">{{ error() }}</div> }
          <button class="btn block" type="submit" [disabled]="busy()">{{ busy() ? 'Signing in...' : 'Sign in' }}</button>
          <div class="small other">Not staff? <a routerLink="/">Report a leak without an account</a> · <a routerLink="/crew/login">Crew sign in</a></div>
        </form>
      </section>
    </div>
  `,
  styles: [`
    .wrap { min-height: 100dvh; display: flex; flex-wrap: wrap; background: var(--surface); }
    .hero { flex: 1 1 420px; background: var(--panel); color: #fff; padding: 56px; display: flex; flex-direction: column; gap: 40px; }
    .hero .pitch { margin: auto 0; padding-bottom: 60px; }
    .brand { font-weight: 700; font-size: 22px; }
    .hero h1 { font-size: clamp(34px, 4vw, 48px); line-height: 1.08; letter-spacing: -0.02em; max-width: 540px; }
    .hero p { color: #C9D6DF; font-size: 17px; line-height: 1.5; max-width: 520px; }
    .corner { position: absolute; top: 16px; right: 16px; }
    .form-side { position: relative; flex: 1 1 420px; padding: 56px 24px; display: flex; align-items: center; justify-content: center; }
    .form { width: 100%; max-width: 400px; gap: 18px; }
    h2 { font-size: 30px; }
    .other { border-top: 1px solid var(--line); padding-top: 16px; color: var(--text-2); }
    @media (max-width: 700px) { .hero { padding: 32px 24px; } }
  `],
})
export class StaffLogin {
  private api = inject(Api);
  private auth = inject(Auth);
  private router = inject(Router);
  next = input<string>();

  email = '';
  password = '';
  busy = signal(false);
  error = signal('');

  async submit() {
    this.busy.set(true);
    this.error.set('');
    try {
      const r = await this.api.loginStaff(this.email, this.password);
      this.auth.save('staff', r);
      this.router.navigateByUrl(this.next() || (r.user.role === 'manager' ? '/dashboard/report' : '/dashboard'));
    } catch (e) {
      this.error.set(errorText(e));
    } finally {
      this.busy.set(false);
    }
  }
}
