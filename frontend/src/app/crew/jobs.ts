import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { Api, errorText } from '../core/api';
import { Auth } from '../core/auth';
import { AgoPipe, JobStatusPipe, LevelPipe, TimePipe } from '../core/format';
import { ThemeToggle } from '../shared/bits';
import { Job } from '../core/models';

@Component({
  selector: 'dw-crew-jobs',
  imports: [RouterLink, LevelPipe, TimePipe, AgoPipe, JobStatusPipe, ThemeToggle],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="phone-wrap">
      <div class="phone">
        <div class="row between">
          <div><div class="small muted">{{ auth.crewUser()?.name }} · {{ auth.crewUser()?.sub }}</div><h1>Your jobs</h1></div>
          <div class="row tools"><dw-theme-toggle />
          <button type="button" class="round" aria-label="Sign out" (click)="signOut()">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 4h4v16h-4"></path><path d="M10 8l-4 4 4 4"></path><path d="M6 12h10"></path></svg>
          </button></div>
        </div>
        @if (error()) { <div class="error">{{ error() }}</div> }
        <div class="label">To do</div>
        @for (j of todo(); track j.id; let first = $first) {
          <a class="job" [class.first]="first" [routerLink]="['/crew/jobs', j.id]">
            <div class="row between"><span class="mono small muted">{{ j.id }} · sent {{ j.created | time }}</span>
              <span class="pill" [class]="'solid-' + j.level">{{ j.level | level }} · {{ j.score }}</span></div>
            <div class="where">{{ j.zone_name }}{{ j.streets[0] ? ', ' + j.streets[0] : '' }}</div>
            <div class="small">{{ j.why }}.</div>
            <div class="row between small"><span class="open">Open job</span><span class="muted">{{ j.status | jobStatus }}</span></div>
          </a>
        } @empty { <div class="empty">No jobs right now. New jobs appear here automatically.</div> }
        @if (done().length) {
          <div class="label">Done</div>
          @for (j of done(); track j.id) {
            <div class="done row between">
              <div><b>{{ j.zone_name }} {{ j.zone }}</b><div class="small muted">{{ j.cause || j.outcome_text }} · {{ j.finished | ago }}</div></div>
              <span class="pill ok">{{ j.outcome_text }}</span>
            </div>
          }
        }
        <div class="tiny muted center">You only see jobs sent to {{ auth.crewUser()?.name }}. This page refreshes every 20 seconds.</div>
      </div>
    </div>
  `,
  styles: [`
    h1 { font-size: 26px; margin-top: 2px; }
    .tools { gap: 8px; flex-wrap: nowrap; }
    .round { width: 44px; height: 44px; border-radius: 99px; background: var(--surface); border: 1px solid var(--line); display: flex; align-items: center; justify-content: center; cursor: pointer; }
    .label { font-size: 13px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; margin-top: 4px; }
    .job { display: flex; flex-direction: column; gap: 8px; background: var(--surface); border: 1px solid var(--line); border-radius: 14px; padding: 16px; text-decoration: none; color: var(--text); }
    .job.first { border: 2px solid var(--text); }
    .where { font-weight: 700; font-size: 19px; }
    .open { color: var(--link); font-weight: 600; }
    .done { background: var(--surface); border: 1px solid var(--line); border-radius: 14px; padding: 14px 16px; }
    .empty { background: var(--surface); border: 1px dashed var(--line-hover); border-radius: 14px; padding: 22px; text-align: center; color: var(--muted); }
    .center { text-align: center; margin-top: auto; }
  `],
})
export class CrewJobsPage {
  private api = inject(Api);
  private router = inject(Router);
  auth = inject(Auth);
  jobs = signal<Job[]>([]);
  error = signal('');
  todo = computed(() => this.jobs().filter(j => j.status !== 'done'));
  done = computed(() => this.jobs().filter(j => j.status === 'done'));

  constructor() {
    this.load();
    const timer = setInterval(() => this.load(), 20000);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }

  async load() {
    try { this.jobs.set(await this.api.crewJobs()); this.error.set(''); }
    catch (e) { this.error.set(errorText(e)); }
  }

  signOut() {
    this.auth.signOut('crew');
    this.router.navigateByUrl('/crew/login');
  }
}
