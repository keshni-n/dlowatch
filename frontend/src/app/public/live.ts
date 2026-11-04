import { Injectable, inject, signal } from '@angular/core';
import { Api } from '../core/api';
import { I18n } from '../core/i18n';
import { PublicAreas, PublicNotice, PublicStats } from '../core/models';

type Info = { hotline: string | null; utility: string | null; area: string };

function cached<T>(key: string): { data: T; at: number } | null {
  try { const raw = localStorage.getItem('dlowatch.cache.' + key); return raw ? JSON.parse(raw) : null; } catch { return null; }
}
function store(key: string, data: unknown) {
  try { localStorage.setItem('dlowatch.cache.' + key, JSON.stringify({ data, at: Date.now() / 1000 })); } catch { /* ignore */ }
}

/**
 * Live public info with a fallback: every answer is saved on the phone, and when the
 * server cannot be reached the last saved answer is shown with an "offline" flag.
 */
@Injectable({ providedIn: 'root' })
export class PublicLive {
  private api = inject(Api);
  readonly areas = signal<PublicAreas | null>(cached<PublicAreas>('areas')?.data ?? null);
  readonly stats = signal<PublicStats | null>(cached<PublicStats>('stats')?.data ?? null);
  readonly info = signal<Info | null>(cached<Info>('info')?.data ?? null);
  readonly offline = signal(false);
  readonly savedAt = signal<number | null>(cached<PublicAreas>('areas')?.at ?? null);

  async load() {
    const results = await Promise.allSettled([this.api.publicAreas(), this.api.publicStats(), this.api.publicInfo()]);
    const [a, s, i] = results;
    if (a.status === 'fulfilled') { this.areas.set(a.value); store('areas', a.value); this.savedAt.set(a.value.updated); }
    if (s.status === 'fulfilled') { this.stats.set(s.value); store('stats', s.value); }
    if (i.status === 'fulfilled') { this.info.set(i.value); store('info', i.value); }
    this.offline.set(a.status === 'rejected');
  }
}

export const STATUS_KEY: Record<string, string> = { normal: 'stNormal', checking: 'stChecking', crew: 'stCrew', fixed: 'stFixed' };
export const STATUS_CLASS: Record<string, string> = { normal: 'low', checking: 'watch', crew: 'high', fixed: 'ok' };
export const STATUS_FILL: Record<string, string> = { normal: '#CFDCE7', checking: '#F3D27E', crew: '#EE9550', fixed: '#9ED3B5' };

/** One plain sentence for a notice, in the chosen language. */
export function noticeText(n: PublicNotice, i: I18n): string {
  const where = n.street ? `${i.t('near')} ${n.street}` : `${i.t('inArea')} ${n.area}`;
  if (n.status === 'crew') return `${i.t('ntCrew')} ${where}. ${i.t('ntCut')}`;
  if (n.status === 'fixed') return `${i.t('ntFixed')} ${where}.`;
  return n.burst ? i.t('ntBurst') : `${i.t('ntChecking')} ${where}.`;
}
