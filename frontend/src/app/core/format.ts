import { Pipe, PipeTransform } from '@angular/core';

const TZ = 'Indian/Mauritius';

export function fmtTime(t: number | null | undefined): string {
  if (!t) return '';
  return new Intl.DateTimeFormat('en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit' }).format(t * 1000);
}

export function fmtDay(t: number | null | undefined): string {
  if (!t) return '';
  return new Intl.DateTimeFormat('en-GB', { timeZone: TZ, day: 'numeric', month: 'short' }).format(t * 1000);
}

export function fmtDateTime(t: number | null | undefined): string {
  if (!t) return '';
  return `${fmtDay(t)}, ${fmtTime(t)}`;
}

export function ago(t: number | null | undefined): string {
  if (!t) return '';
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 90) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  const d = Math.round(s / 86400);
  return d === 1 ? '1 day ago' : `${d} days ago`;
}

export function num(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '–';
  return v.toLocaleString('en-GB', { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export const LEVEL_TEXT: Record<string, string> = { low: 'Low', watch: 'Watch', high: 'High', critical: 'Critical' };

export const JOB_STATUS: Record<string, string> = {
  sent: 'Sent', on_the_way: 'On the way', on_site: 'On site', done: 'Done', cancelled: 'Cancelled',
};

@Pipe({ name: 'time' })
export class TimePipe implements PipeTransform { transform(t: number | null | undefined) { return fmtTime(t); } }

@Pipe({ name: 'dateTime' })
export class DateTimePipe implements PipeTransform { transform(t: number | null | undefined) { return fmtDateTime(t); } }

@Pipe({ name: 'ago' })
export class AgoPipe implements PipeTransform { transform(t: number | null | undefined) { return ago(t); } }

@Pipe({ name: 'num' })
export class NumPipe implements PipeTransform { transform(v: number | null | undefined, d = 0) { return num(v, d); } }

@Pipe({ name: 'level' })
export class LevelPipe implements PipeTransform { transform(l: string | null | undefined) { return l ? LEVEL_TEXT[l] ?? l : ''; } }

@Pipe({ name: 'jobStatus' })
export class JobStatusPipe implements PipeTransform { transform(s: string | null | undefined) { return s ? JOB_STATUS[s] ?? s : ''; } }

export const PIPES = [TimePipe, DateTimePipe, AgoPipe, NumPipe, LevelPipe, JobStatusPipe];
