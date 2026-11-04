import { ChangeDetectionStrategy, Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Api, errorText } from '../core/api';
import { LevelPipe } from '../core/format';
import { AlertItem, SystemStatus } from '../core/models';
import { LeakMap } from '../shared/leak-map';

/** Admin system test: put a test leak on any pipe (simulated with EPANET) and check the alert comes through. */
@Component({
  selector: 'dw-simulate',
  imports: [FormsModule, RouterLink, LeakMap, LevelPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="stack page">
      <header>
        <h1>System test</h1>
        <p class="muted lead">Check the whole chain: put a test leak on a pipe and confirm the meters, the scoring and the alert respond.</p>
      </header>
      <section class="row two">
        <div class="card map-card stack">
          <div class="row">
            <label class="field inline">Zone
              <select class="input" [ngModel]="zone()" (ngModelChange)="zone.set($event)" name="zone">
                @for (z of zones; track z) { <option [value]="z">{{ z }}</option> }
              </select>
            </label>
            <span class="small muted">{{ pipes() ? pipes()!.features.length + ' pipes' : 'Loading pipes...' }}</span>
          </div>
          <dw-leak-map [pipes]="pipes()" [highlightPipes]="pipe() ? [pipe()] : []" [pipesClickable]="true" [height]="520"
                       fitTo="pipes" (pipeClick)="pick($event)" />
        </div>
        <div class="card side stack">
          <h2>Test leak</h2>
          <div class="field">Pipe
            <div class="pipe mono">{{ pipe() || 'Click a pipe on the map' }}</div>
            @if (street()) { <span class="small muted">{{ street() }}</span> }
          </div>
          <fieldset class="sizes">
            <legend class="field">Size</legend>
            @for (s of sizes; track s.key) {
              <label class="size"><input type="radio" name="size" [value]="s.key" [(ngModel)]="size"> <b>{{ s.label }}</b> <span class="small muted">{{ s.hint }}</span></label>
            }
          </fieldset>
          <div class="model-box">
            <div class="row between">
              <div><b>AI assist</b>
                <div class="small muted">{{ modelText() }}</div></div>
              <label class="switch">
                <input type="checkbox" [checked]="sys()?.model?.enabled" (change)="toggleModel($any($event.target).checked)" [disabled]="busy()">
                <span>{{ sys()?.model?.enabled ? 'On' : 'Off' }}</span>
              </label>
            </div>
            <div class="tiny muted">Switch off to check that alerts still come from the meters without it.</div>
          </div>
          <div class="model-box">
            <div class="row between">
              <div><b>Start fresh</b>
                <div class="small muted">Deletes every alert, public report and crew job, and clears the network. Accounts stay.</div></div>
              <button class="btn ghost" type="button" (click)="resetNetwork()" [disabled]="busy()">Reset</button>
            </div>
          </div>
          <button class="btn block" type="button" (click)="run()" [disabled]="!pipe() || busy()">{{ busy() ? 'Running the test...' : 'Start the test leak' }}</button>
          @if (error()) { <div class="error">{{ error() }}</div> }
          @if (message()) { <div class="note">{{ message() }}</div> }
          @if (result(); as r) {
            <div class="success stack res">
              <b>Test alert raised: {{ r.zone_name }} {{ r.zone }}</b>
              <span><span class="pill" [class]="'solid-' + r.level">{{ r.level | level }} · {{ r.score }}/100</span></span>
              <span>{{ r.headline }}</span>
              @if (r.model_status !== 'ok') { <span class="small">Scored by the rules only (AI assist {{ r.model_status }}).</span> }
              <a class="btn" [routerLink]="['/dashboard/alerts', r.id]">Open the alert</a>
            </div>
          }
          <p class="tiny muted">Large leaks are treated as bursts (started 20 minutes ago). Small and medium ones as slow leaks.</p>
        </div>
      </section>
    </div>
  `,
  styles: [`
    .page { gap: 20px; max-width: 1280px; } h1 { font-size: 32px; } .lead { max-width: 760px; margin: 6px 0 0; }
    .two { gap: 16px; align-items: stretch; } .map-card { flex: 3 1 560px; } .side { flex: 2 1 300px; }
    .inline { flex-direction: row; align-items: center; gap: 10px; } .inline .input { width: 110px; }
    .pipe { font-size: 18px; padding: 10px 12px; background: var(--ground); border-radius: 8px; }
    .sizes { border: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 6px; }
    .size { display: flex; gap: 8px; align-items: center; min-height: 40px; font-size: 15px; cursor: pointer; }
    .res { gap: 8px; }
    .model-box { border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; display: flex; flex-direction: column; gap: 6px; }
    .switch { display: inline-flex; align-items: center; gap: 8px; font-weight: 600; cursor: pointer; min-height: 40px; }
    .switch input { width: 20px; height: 20px; accent-color: var(--blue); }
  `],
})
export class SimulatePage {
  private api = inject(Api);
  zones = ['Z1', 'Z2', 'Z3', 'Z4', 'Z5', 'Z6', 'Z7', 'Z8'];
  zone = signal('Z3');
  pipes = signal<GeoJSON.FeatureCollection | null>(null);
  pipe = signal('');
  street = signal('');
  size = 'large';
  sizes = [
    { key: 'small', label: 'Small', hint: 'about 1 L/s, slow leak' },
    { key: 'medium', label: 'Medium', hint: 'about 2 L/s, slow leak' },
    { key: 'large', label: 'Large', hint: 'about 4 to 5 L/s, burst' },
  ];
  busy = signal(false);
  error = signal('');
  message = signal('');
  result = signal<AlertItem | null>(null);
  sys = signal<SystemStatus | null>(null);
  modelText = computed(() => {
    const m = this.sys()?.model;
    if (!m) return '';
    if (m.status === 'online') return 'Online: adds findings and a search area';
    if (m.status === 'off') return 'Off: rules only, search area from sensors';
    return `Not available (${m.last_error}): rules only`;
  });

  constructor() {
    this.api.systemStatus().then(s => this.sys.set(s)).catch(() => undefined);
    effect(() => {
      const z = this.zone();
      this.pipes.set(null);
      this.pipe.set('');
      this.api.mapPipes(z).then(p => this.pipes.set(p)).catch(e => this.error.set(errorText(e)));
    });
  }

  pick(p: { id: string; street?: string }) {
    this.pipe.set(p.id);
    this.street.set(p.street ?? '');
    this.result.set(null);
    this.message.set('');
  }

  async toggleModel(on: boolean) {
    try { this.sys.set(await this.api.switchModel(on)); } catch (e) { this.error.set(errorText(e)); }
  }

  async resetNetwork() {
    if (!confirm('Delete all alerts, public reports and crew jobs, and start fresh?')) return;
    this.busy.set(true);
    this.error.set('');
    this.result.set(null);
    try {
      await this.api.resetNetwork();
      this.message.set('Started fresh. New leaks will start in the network on their own; use a test leak to show one now.');
    } catch (e) { this.error.set(errorText(e)); }
    this.busy.set(false);
  }

  async run() {
    this.busy.set(true);
    this.error.set('');
    this.message.set('');
    try {
      const r = await this.api.demoLeak(this.pipe(), this.size);
      this.api.systemStatus().then(s => this.sys.set(s)).catch(() => undefined);
      if (r.alert && r.detail) this.result.set(r.detail);
      else this.message.set(r.message ?? 'No alert raised.');
    } catch (e) { this.error.set(errorText(e)); }
    this.busy.set(false);
  }
}
