import { Injectable, inject, signal } from '@angular/core';
import { Api } from './api';
import { PublicStatus } from './models';

const KEY = 'dlowatch.myReports';

interface Saved { code: string; sentAt: number; status: PublicStatus | null; checkedAt: number | null; }

function read(): Saved[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return JSON.parse(raw) as Saved[];
    // older versions only kept the last code
    const last = localStorage.getItem('dlowatch.lastReport');
    return last ? [{ code: last, sentAt: Date.now() / 1000, status: null, checkedAt: null }] : [];
  } catch { return []; }
}

/**
 * Reports sent from this phone. Codes and the last known status are kept on the phone,
 * so "My reports" still shows something when the server cannot be reached.
 */
@Injectable({ providedIn: 'root' })
export class MyReports {
  private api = inject(Api);
  readonly items = signal<Saved[]>(read());
  readonly offline = signal(false);

  add(code: string, status: PublicStatus | null) {
    const rest = this.items().filter(i => i.code !== code);
    this.save([{ code, sentAt: Date.now() / 1000, status, checkedAt: status ? Date.now() / 1000 : null }, ...rest].slice(0, 30));
  }

  remove(code: string) { this.save(this.items().filter(i => i.code !== code)); }

  /** Ask the server for fresh statuses. On failure keep what we had and flag offline. */
  async refresh() {
    const codes = this.items().map(i => i.code);
    if (!codes.length) return;
    try {
      const fresh = await this.api.reportStatuses(codes);
      const byCode = new Map(fresh.map(s => [s.code, s]));
      const now = Date.now() / 1000;
      this.save(this.items().map(i => (byCode.has(i.code) ? { ...i, status: byCode.get(i.code)!, checkedAt: now } : i)));
      this.offline.set(false);
    } catch {
      this.offline.set(true);
    }
  }

  private save(items: Saved[]) {
    this.items.set(items);
    try { localStorage.setItem(KEY, JSON.stringify(items)); } catch { /* private mode: keep in memory only */ }
  }
}

/** Where a report is now, as one short key: received, linked, crew, fixed. */
export function stage(s: PublicStatus | null): 'received' | 'linked' | 'crew' | 'fixed' | 'unknown' {
  if (!s) return 'unknown';
  const done = s.steps.filter(x => x.done).map(x => x.key);
  if (done.includes('fixed')) return 'fixed';
  if (done.includes('crew')) return 'crew';
  if (done.includes('linked')) return 'linked';
  return 'received';
}
