import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api, errorText } from '../core/api';
import { AgoPipe, DateTimePipe, JobStatusPipe, LevelPipe } from '../core/format';
import { Crew, Job, Label } from '../core/models';

const JOB_CLS: Record<string, string> = { sent: 'low', on_the_way: 'watch', on_site: 'watch', done: 'ok', cancelled: 'neutral' };
const LABEL: Record<string, string> = { leak_found: 'Real leak', no_leak: 'No leak', meter_fault: 'Meter fault', planned_works: 'Planned works', not_a_leak: 'Not a leak (operator)' };

@Component({
  selector: 'dw-crews',
  imports: [RouterLink, AgoPipe, DateTimePipe, JobStatusPipe, LevelPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="stack page">
      <header class="row between">
        <div><h1>Field crews</h1><p class="muted">Jobs go to the crew phones at /crew. Results come back here.</p></div>
        <button class="btn secondary" type="button" (click)="load()">Refresh</button>
      </header>
      @if (error()) { <div class="error">{{ error() }}</div> }

      <section class="crews">
        @for (c of crews(); track c.id) {
          <div class="card stack crew">
            <div class="row between"><b class="name">{{ c.name }}</b>
              <span class="pill" [class]="c.state === 'busy' ? 'watch' : 'ok'">{{ c.state === 'busy' ? 'On a job' : 'Available' }}</span></div>
            <div class="small">{{ c.detail }}</div>
            <div class="tiny muted">{{ c.people }}</div>
          </div>
        }
      </section>

      <section class="card stack">
        <h2>Jobs</h2>
        <div class="table-wrap">
          <table class="data">
            <thead><tr><th>Job</th><th>Zone</th><th>Why</th><th>Crew</th><th>Status</th><th>Result</th></tr></thead>
            <tbody>
              @for (j of jobs(); track j.id) {
                <tr>
                  <td class="mono"><b>{{ j.id }}</b><div class="tiny muted">{{ j.created | ago }}</div></td>
                  <td><a [routerLink]="['/dashboard/alerts', j.alert]">{{ j.zone_name }} {{ j.zone }}</a><div><span class="pill" [class]="j.level">{{ j.level | level }} · {{ j.score }}</span></div></td>
                  <td class="small">{{ j.why }}</td>
                  <td>{{ j.crew }}</td>
                  <td><span class="pill" [class]="cls(j.status)">{{ j.status | jobStatus }}</span></td>
                  <td class="small">@if (j.outcome_text) {<b>{{ j.outcome_text }}</b>{{ j.cause ? ', ' + j.cause : '' }}<div class="tiny muted">{{ j.finished | dateTime }}</div>}</td>
                </tr>
              } @empty { <tr><td colspan="6" class="muted">No jobs yet. Send one from a leak alert.</td></tr> }
            </tbody>
          </table>
        </div>
        <p class="tiny muted">Every crew result is saved (real leak, no leak, meter fault, planned works) and used to improve detection.</p>
      </section>

      <section class="row two">
        <div class="card grow stack">
          <h2>Crew findings</h2>
          @for (l of labels(); track $index) {
            <div class="row between small lab"><span><b>{{ labelText(l.label) }}</b>{{ l.cause ? ' · ' + l.cause : '' }} · zone {{ l.zone }}</span><span class="muted">{{ l.by }}, {{ l.at | ago }}</span></div>
          } @empty { <p class="muted small">None yet.</p> }
        </div>
      </section>
    </div>
  `,
  styles: [`
    .page { gap: 20px; max-width: 1280px; } h1 { font-size: 32px; }
    .crews { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; }
    .crew { gap: 8px; } .name { font-size: 18px; }
    .two { gap: 16px; align-items: stretch; } .two > .card { flex: 1 1 420px; }
    .lab { border-bottom: 1px solid var(--line-soft); padding-bottom: 8px; }
  `],
})
export class CrewsPage {
  private api = inject(Api);
  crews = signal<Crew[]>([]);
  jobs = signal<Job[]>([]);
  labels = signal<Label[]>([]);
  error = signal('');
  cls = (s: string) => JOB_CLS[s] ?? 'neutral';
  labelText = (l: string) => LABEL[l] ?? l;

  constructor() {
    this.load();
    const timer = setInterval(() => this.load(), 15000);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }

  async load() {
    try {
      const d = await this.api.crews();
      this.crews.set(d.crews);
      this.jobs.set(d.jobs);
      this.labels.set(d.labels);
      this.error.set('');
    } catch (e) { this.error.set(errorText(e)); }
  }
}
