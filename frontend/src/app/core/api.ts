import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import {
  AlertDetail, AlertItem, Cluster, Crew, Job, Label, LoggerDetail, LoggersResponse, LoginResult, MeterDetail, MetersResponse, ModelMetric,
  Evaluation, Overview, PublicAreas, RepairList, WaterReport, SystemStatus, PublicNearby, PublicReportRow, PublicStats, PublicStatus, Replay,
} from './models';

const B = environment.apiBase;

/** Every call returns a Promise, so pages can simply `await` and put results in signals. */
@Injectable({ providedIn: 'root' })
export class Api {
  private http = inject(HttpClient);

  private get<T>(path: string) { return firstValueFrom(this.http.get<T>(B + path)); }
  private post<T>(path: string, body: unknown = {}) { return firstValueFrom(this.http.post<T>(B + path, body)); }

  // auth
  loginStaff(email: string, password: string) { return this.post<LoginResult>('/auth/staff', { email, password }); }
  loginCrew(staff_id: string, pin: string) { return this.post<LoginResult>('/auth/crew', { staff_id, pin }); }

  // staff
  overview() { return this.get<Overview>('/overview'); }
  alerts(status: 'open' | 'resolved' | 'all' = 'open') { return this.get<AlertItem[]>(`/alerts?status=${status}`); }
  alert(id: string) { return this.get<AlertDetail>(`/alerts/${id}`); }
  replay(id: string) { return this.get<Replay>(`/alerts/${id}/replay`); }
  dispatch(id: string, crew_id: string) { return this.post<Job>(`/alerts/${id}/dispatch`, { crew_id }); }
  dismiss(id: string) { return this.post<AlertItem>(`/alerts/${id}/dismiss`); }
  meters() { return this.get<MetersResponse>('/meters'); }
  meter(id: string, hours = 24) { return this.get<MeterDetail>(`/meters/${id}?hours=${hours}`); }
  loggers(zone?: string) { return this.get<LoggersResponse>(`/loggers${zone ? '?zone=' + zone : ''}`); }
  logger(id: string) { return this.get<LoggerDetail>(`/loggers/${id}`); }
  reports() { return this.get<{ reports: PublicReportRow[]; clusters: Cluster[] }>('/reports'); }
  reportToAlert(code: string) { return this.post<{ alert: string }>(`/reports/${code}/alert`); }
  forwardReport(code: string) { return this.post<PublicReportRow>(`/reports/${code}/forward`); }
  reportPhoto(code: string) { return this.get<{ photo: string }>(`/reports/${code}/photo`); }
  jobPhotos(id: string) { return this.get<{ before: string | null; after: string | null }>(`/jobs/${id}/photos`); }
  crews() { return this.get<{ crews: Crew[]; jobs: Job[]; labels: Label[] }>('/crews'); }
  mapZones() { return this.get<GeoJSON.FeatureCollection>('/map/zones'); }
  mapPipes(zone: string) { return this.get<GeoJSON.FeatureCollection>(`/map/pipes?zone=${zone}`); }
  demoLeak(pipe_id: string, size: string, nights_ago = 0) { return this.post<{ alert: string | null; message?: string; model_status: string; detail?: AlertItem }>('/demo/leak', { pipe_id, size, nights_ago }); }
  metrics() { return this.get<ModelMetric[]>('/model/metrics'); }
  report(days?: number) { return this.get<WaterReport>(`/report${days ? '?days=' + days : ''}`); }
  evaluation() { return this.get<Evaluation>('/evaluation'); }
  repairs() { return this.get<RepairList>('/repairs/priority'); }
  systemStatus() { return this.get<SystemStatus>('/system/status'); }
  switchModel(enabled: boolean) { return this.post<SystemStatus>('/system/model', { enabled }); }
  resetNetwork() { return this.post<{ alerts: number; reports: number; jobs: number }>('/system/reset', {}); }

  // crew
  crewJobs() { return this.get<Job[]>('/crew/jobs'); }
  crewJob(id: string) { return this.get<Job>(`/crew/jobs/${id}`); }
  crewReportPhoto(id: string, code: string) { return this.get<{ photo: string }>(`/crew/jobs/${id}/reports/${code}/photo`); }
  crewJobPipes(id: string) { return this.get<GeoJSON.FeatureCollection>(`/crew/jobs/${id}/pipes`); }
  crewStatus(id: string, status: 'on_the_way' | 'on_site') { return this.post<Job>(`/crew/jobs/${id}/status`, { status }); }
  crewResult(id: string, body: unknown) { return this.post<Job>(`/crew/jobs/${id}/result`, body); }

  // public (no login)
  sendReport(body: unknown) { return this.post<{ code: string; status: PublicStatus; note_cleaned?: boolean }>('/public/reports', body); }
  reportStatus(code: string) { return this.get<PublicStatus>(`/public/reports/${encodeURIComponent(code)}`); }
  reportStatuses(codes: string[]) { return this.get<PublicStatus[]>(`/public/reports-batch?codes=${encodeURIComponent(codes.join(','))}`); }
  publicAreas() { return this.get<PublicAreas>('/public/areas'); }
  publicNearby(lat: number, lon: number) { return this.get<PublicNearby>(`/public/nearby?lat=${lat}&lon=${lon}`); }
  publicInfo() { return this.get<{ hotline: string | null; utility: string | null; area: string }>('/public/info'); }
  publicStats() { return this.get<PublicStats>('/public/stats'); }
}

export function errorText(e: unknown): string {
  if (e instanceof HttpErrorResponse) {
    if (e.status === 0) return 'Cannot reach the server. Is the backend running?';
    const d = e.error?.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return 'Please check the form and try again.';
    return `Something went wrong (${e.status}).`;
  }
  return 'Something went wrong.';
}
