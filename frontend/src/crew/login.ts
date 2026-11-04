import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { Api, errorText } from '../core/api';
import { Auth } from '../core/auth';
import { Logo } from '../shared/bits';

@Component({
  selector: 'dw-crew-login',
  imports: [FormsModule, RouterLink, Logo],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="phone-wrap dark">
      <form class="phone dark" (ngSubmit)="submit()">
        <div class="row"><dw-logo [size]="30" /><b class="brand">DloWatch Crew</b></div>
        <div>
          <h1>Crew sign in</h1>
          <p class="sub">Field staff only. You will only see jobs sent to your crew.</p>
        </div>
        <label class="field light">Staff ID
          <input class="input dark-input mono" name="sid" [(ngModel)]="staffId" autocomplete="username" autocapitalize="characters" required>
        </label>
        <label class="field light">PIN
          <input class="input dark-input pin" type="password" name="pin" inputmode="numeric" [(ngModel)]="pin" autocomplete="current-password" required>
        </label>
        @if (error()) { <div class="error" role="alert">{{ error() }}</div> }
        <button class="btn block light" type="submit" [disabled]="busy()">{{ busy() ? 'Signing in...' : 'Sign in' }}</button>
        <div class="foot small">
          <span>Lost your PIN? Call the control room.</span>
          <span>Not crew? <a routerLink="/">Report a leak instead</a></span>
        </div>
      </form>
    </div>
  `,
  styles: [`
    .brand { font-size: 19px; }
    h1 { font-size: 32px; margin-top: 18px; }
    .sub { color: #C9D6DF; font-size: 15px; }
    .light { color: #E6EDF2; }
    .dark-input { background: var(--panel-2); border-color: #3A5163; color: #fff; height: 52px; font-size: 18px; }
    .pin { letter-spacing: 0.3em; font-size: 22px; }
    .demo { color: #9FB2C0; background: var(--panel-hover); border-radius: 8px; padding: 10px 12px; }
    .foot { margin-top: auto; display: flex; flex-direction: column; gap: 8px; color: #B5C4CF; }
    .foot a { color: #9CCBF2; font-weight: 600; }
  `],
})
export class CrewLoginPage {
  private api = inject(Api);
  private auth = inject(Auth);
  private router = inject(Router);
  staffId = '';
  pin = '';
  busy = signal(false);
  error = signal('');

  async submit() {
    this.busy.set(true);
    this.error.set('');
    try {
      this.auth.save('crew', await this.api.loginCrew(this.staffId, this.pin));
      this.router.navigateByUrl('/crew');
    } catch (e) { this.error.set(errorText(e)); }
    this.busy.set(false);
  }
}
