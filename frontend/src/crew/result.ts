import { ChangeDetectionStrategy, Component, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { Api, errorText } from '../core/api';
import { shrinkImage } from '../shared/bits';

const TITLES: Record<string, string> = {
  leak_found: 'Leak found: log it', no_leak: 'No leak found', planned_works: 'Planned works', meter_fault: 'Meter problem',
};
const CAUSES: Record<string, string[]> = {
  leak_found: ['Pipe joint', 'Cracked main', 'House connection', 'Valve or hydrant'],
  no_leak: ['Nothing visible', 'Normal high use', 'Could not access'],
  planned_works: ['New connection', 'Pipe flushing', 'Other works'],
  meter_fault: ['Meter stuck', 'Meter offline', 'Wrong readings'],
};

@Component({
  selector: 'dw-crew-result',
  imports: [FormsModule, RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="phone-wrap">
      <form class="phone" (ngSubmit)="save()">
        <div class="row">
          <a [routerLink]="['/crew/jobs', id()]" class="round" aria-label="Back to job">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 5l-7 7 7 7"></path></svg>
          </a>
          <div><div class="small muted mono">{{ id() }}</div><h1>{{ title() }}</h1></div>
        </div>

        <fieldset>
          <legend class="field">{{ outcomeKey() === 'leak_found' ? 'What was the cause?' : 'What was it?' }}</legend>
          <div class="choice-grid">
            @for (c of causes(); track c) {
              <button type="button" class="choice center" [attr.aria-pressed]="cause() === c" (click)="cause.set(c)"><b>{{ c }}</b></button>
            }
          </div>
        </fieldset>

        @if (outcomeKey() === 'leak_found') {
          <div class="choice-grid">
            <label class="photo">
              <input type="file" accept="image/*" capture="environment" (change)="photo($event, 'before')">
              @if (before()) { <img [src]="before()" alt="Before photo"> } @else { <span>Photo before</span> }
            </label>
            <label class="photo">
              <input type="file" accept="image/*" capture="environment" (change)="photo($event, 'after')">
              @if (after()) { <img [src]="after()" alt="After photo"> } @else { <span>Photo after</span> }
            </label>
          </div>
          <div class="choice-grid">
            <label class="field">Found at <input class="input" type="time" name="found" [(ngModel)]="foundAt"></label>
            <label class="field">Fixed at <input class="input" type="time" name="fixed" [(ngModel)]="fixedAt"></label>
          </div>
        }

        <label class="field">Notes (optional)
          <textarea class="input" rows="3" name="notes" [(ngModel)]="notes" maxlength="500"></textarea>
        </label>
        @if (error()) { <div class="error">{{ error() }}</div> }
        <div class="end stack">
          <button class="btn block" type="submit" [disabled]="busy() || !cause()">{{ busy() ? 'Saving...' : 'Close the job' }}</button>
          <div class="tiny muted center">{{ outcomeKey() === 'leak_found' ? 'The alert closes. Night flow is checked again tomorrow to confirm the fix.' : 'The alert closes and your answer is saved as a label for the model.' }}</div>
        </div>
      </form>
    </div>
  `,
  styles: [`
    h1 { font-size: 24px; }
    .round { width: 44px; height: 44px; border-radius: 99px; background: var(--surface); border: 1px solid var(--line); display: flex; align-items: center; justify-content: center; color: var(--text); flex: none; }
    fieldset { border: none; margin: 0; padding: 0; }
    legend { margin-bottom: 8px; padding: 0; }
    .center { align-items: center; justify-content: center; text-align: center; }
    .photo { height: 100px; border: 2px dashed var(--line-hover); border-radius: 12px; background: var(--surface); display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: 14px; cursor: pointer; overflow: hidden; position: relative; }
    .photo input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }
    .photo img { width: 100%; height: 100%; object-fit: cover; }
    .end { margin-top: auto; gap: 8px; }
    .center { text-align: center; }
  `],
})
export class CrewResultPage {
  private api = inject(Api);
  private router = inject(Router);
  id = input.required<string>();
  outcome = input<string>();
  outcomeKey = () => (this.outcome() && CAUSES[this.outcome()!] ? this.outcome()! : 'leak_found');
  title = () => TITLES[this.outcomeKey()];
  causes = () => CAUSES[this.outcomeKey()];

  cause = signal('');
  before = signal('');
  after = signal('');
  foundAt = '';
  fixedAt = '';
  notes = '';
  busy = signal(false);
  error = signal('');

  async photo(e: Event, which: 'before' | 'after') {
    const f = (e.target as HTMLInputElement).files?.[0];
    if (!f) return;
    try { (which === 'before' ? this.before : this.after).set(await shrinkImage(f, 1024)); }
    catch { this.error.set('Could not read that photo.'); }
  }

  async save() {
    this.busy.set(true);
    this.error.set('');
    try {
      await this.api.crewResult(this.id(), {
        outcome: this.outcomeKey(), cause: this.cause(), notes: this.notes || null,
        found_at: this.foundAt || null, fixed_at: this.fixedAt || null,
        photo_before: this.before() || null, photo_after: this.after() || null,
      });
      this.router.navigateByUrl('/crew');
    } catch (e) { this.error.set(errorText(e)); }
    this.busy.set(false);
  }
}
