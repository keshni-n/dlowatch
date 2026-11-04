// Shapes returned by the DloWatch API (backend/dlowatch.py)

export type Level = 'low' | 'watch' | 'high' | 'critical';
export type Role = 'operator' | 'admin' | 'manager' | 'crew';

export interface User { sub: string; role: Role; name: string; crew_id?: string; }
export interface LoginResult { token: string; user: User; }

export interface JobSummary { id: string; crew_id: string; crew: string; status: string; outcome: string | null; }

export interface AlertItem {
  id: string; zone: string; zone_name: string; kind: 'slow' | 'burst' | 'public';
  status: 'open' | 'resolved'; created: number; started: number; resolved?: number;
  score: number; level: Level; headline: string; street: string | null;
  water_lost_m3_per_day: number | null; best_guess_latlon: [number, number] | null;
  job: JobSummary | null; report_count: number; top_area: { area: string; probability: number | null } | null;
  model_status: 'ok' | 'off' | 'unavailable'; search_source: 'model' | 'meters' | 'reports' | null; ruleset: string;
}

export interface TraceRow { code: string; finding: string; evidence: string; source: 'Rules' | 'AI model' | 'Combo' | 'Public'; points: number; supports?: string; support_points?: number; }
export interface Suspect {
  rank: number; street: string; probability: number; pipes: string[]; pipe_count: number; length_m: number;
  centre_latlon: [number, number]; distance_from_guess_m: number; max_diameter_mm: number; roads: string[];
}
export interface Ease { value: number | null; label: 'easy' | 'medium' | 'hard' | 'unknown'; reasons: string[]; }
export interface RepairItem extends AlertItem {
  rank: number; priority: number; priority_parts: { water: number; households: number; ease: number };
  suspects: Suspect[]; households_est: number | null; ease: Ease; litres_per_hour: number | null;
}
export interface RepairList {
  items: RepairItem[]; weights: { water: number; households: number; ease: number }; household_use_litres_per_day: number;
  totals: { leaks: number; m3_per_day: number; households: number };
}
export interface Rejected { code: string; finding: string; reason: string; }
export interface SystemStatus {
  model: { status: 'online' | 'off' | 'unavailable'; enabled: boolean; timeout_s: number; zones_with_model: string[]; last_error: string | null };
  ruleset: { active: string; available: { version: string; description: string; published: string }[] };
}

export interface Series { t: number[]; v: (number | null)[]; filled?: boolean[]; raw_t?: number[]; raw_v?: number[]; }

export interface FlowStats {
  nights: { night: number; lps: number }[]; baseline_lps: number; last_night_lps: number; night_pct: number;
  nights_running: number; water_in_m3: number; expected_m3: number; balance_gap_pct: number; inlet_trust: number;
  now_lps: number; usual_now_lps: number; now_pct: number;
}

export interface Area { area: string; probability: number | null; centre_latlon: [number, number]; radius_m: number; pipes: string[]; streets: string[]; from_sensors?: string[]; from_loggers?: string[]; }

export interface AlertDetail extends AlertItem {
  if_not_fixed: { m3_per_day: number; week_m3: number; month_m3: number; year_m3: number; olympic_pools_per_year: number;
    money?: { currency: string; week: number; month: number; year: number } } | null;
  trace: TraceRow[]; notes: string[]; ruleset: string; bands: { watch: number; high: number; critical: number };
  rejected: Rejected[]; suspects: Suspect[]; households_est: number | null; ease: Ease; ruleset_info: { version: string; active: string; description: string; ai_cap: number };
  flow: FlowStats; data_quality: { meters: { id: string; type: string; trust: number; drop_mm?: number }[]; fixes: Record<string, number> };
  pressure: (Series & { id: string })[]; areas: Area[];
  sensors: { id: string; latlon: [number, number]; trust: number; drop_mm: number }[];
  loggers: { id: string; latlon: [number, number]; status: LoggerStatus; change_db: number; loud_nights: number;
             last_db: number; usual_db: number; distance_m: number | null }[];
  hearing_range_m: number;
  reports: PublicReportRow[];
  model: { leak_probability: number; leak_flow_lps?: number; water_lost_m3_per_day?: number; best_guess_latlon?: [number, number] };
  demo_truth?: { pipe_id: string; leak_flow_lps: number; water_lost_m3_per_day: number; latlon: [number, number] };
  demo_check?: { distance_error_m?: number; pipe_in_search?: boolean; true_pipe_suspect_rank?: number | null };
  inlet: string;
}

export interface ReplayEvent { n: number; t: number; tag: string; title: string; detail: string; }
export interface Replay extends AlertItem {
  events: ReplayEvent[]; flow: Series; pressure: (Series & { id: string }) | null; window: [number, number];
}

export interface ZoneSummary { zone: string; name: string; score: number; level: Level; night_pct: number; avg_demand_lps: number; pipe_km: number; }

export interface Overview {
  now: number; water_in_m3: number; expected_m3: number; extra_m3: number; extra_pct: number;
  alerts: AlertItem[]; levels: Partial<Record<Level, number>>;
  meters: { total: number; healthy: number; noisy: number; offline: number };
  loggers: { total: number; noisy: number; coverage_pct: number };
  zones: ZoneSummary[]; night_flow: { night: number; lps: number }[]; night_baseline_lps: number;
  ruleset: string;
}

export interface MeterRow {
  id: string; zone: string; zone_name: string; type: 'flow' | 'pressure'; status: string; trust: number;
  fixes: Record<string, number>; issues: string; used: 'yes' | 'half' | 'no'; problem: string;
}
export interface DqRule { code: string; name: string; looks: string; fix: string; }
export type LoggerStatus = 'noisy' | 'loud once' | 'quiet';
export interface Logger {
  id: string; zone: string; zone_name: string; junction: string; lat: number; lon: number; main_road: boolean;
  last_db: number; usual_db: number; change_db: number; loud_nights: number; status: LoggerStatus;
}
export interface LoggersResponse {
  loggers: Logger[]; hearing_range_m: number; coverage: { zones: Record<string, number>; overall: number };
  summary: { total: number; noisy: number; loud_once: number; quiet: number };
}
export interface LoggerDetail extends Logger { nights: { night: number; db: number }[]; }
export interface MetersResponse { meters: MeterRow[]; rules: DqRule[]; summary: { total: number; healthy: number; noisy: number; offline: number; fixed: number }; }
export interface MeterDetail {
  id: string; zone: string; zone_name: string; type: string; unit: string; status: string; trust: number;
  fixes: Record<string, number>; events: { code: string; t: number; raw: number | null; action: string }[];
  problem: string; series: Series;
}

export interface PublicReportRow {
  code: string; created: number; lat: number; lon: number; kind: string; kind_kreol: string; kind_en: string;
  zone: string | null; zone_name: string; note: string | null; has_photo: boolean; alert: string | null;
  status: 'new' | 'linked' | 'cluster' | 'fixed' | 'outside' | 'forwarded';
  forwarded?: number | null; forwarded_by?: string | null;
  photo_hint?: string | null; photo_ok?: boolean | null;
}
export interface Cluster { alert: string; kind: string; zone: string; zone_name: string; count: number; devices: number; street: string | null; minutes: number; score: number; }

export interface Crew { id: string; name: string; people: string; base: string; state: 'busy' | 'available'; open_jobs: number; detail: string; }
export interface Job {
  id: string; alert: string; zone: string; zone_name: string; crew_id: string; crew: string;
  status: 'sent' | 'on_the_way' | 'on_site' | 'done' | 'cancelled'; created: number; finished: number | null;
  outcome: string | null; outcome_text: string | null; cause: string | null; notes: string | null;
  score: number; level: Level; why: string; reasons: string[]; streets: string[];
  latlon: [number, number] | null; search_radius_m: number | null; pipes: string[]; suspects: Suspect[];
  reports: JobReport[];
}
export interface JobReport {
  code: string; created: number; kind_en: string; kind_kreol: string; note: string | null;
  latlon: [number, number]; has_photo: boolean; photo_hint?: string | null;
}
export interface Label { alert: string; zone: string; label: string; cause?: string; by: string; at: number; true_pipe?: string; }

export interface PublicStep { key: string; kreol: string; fr: string; en: string; done: boolean; at: number | null; }
export interface PublicStatus { code: string; created: number; kind: string; area: string; others_nearby: number; steps: PublicStep[]; }

export interface ModelMetric {
  zone: string; detection_accuracy: number; false_alarm_rate: number; area_top3_accuracy: number;
  location_error_median_m: number; size_error_pct_median: number;
}

export type PublicAreaStatus = 'normal' | 'checking' | 'crew' | 'fixed';
export interface PublicNotice {
  id: string; zone: string; area: string; status: PublicAreaStatus; since: number | null;
  street: string | null; latlon: [number, number] | null; burst: boolean;
}
export interface PublicAreas { areas: GeoJSON.FeatureCollection; notices: PublicNotice[]; updated: number; }
export interface PublicNearby { zone: string | null; area: string | null; inside_network: boolean; known: (PublicNotice & { distance_m: number }) | null; }
export interface PublicStats { reports_7d: number; leaks_fixed_30d: number; water_saved_m3_per_day: number; avg_fix_hours: number | null; open_notices: number; }

export interface WaterReport {
  days: number; start: number; end: number;
  totals: { water_in_m3: number; expected_m3: number; lost_m3: number; lost_pct: number; saved_m3: number; saved_per_day_m3: number;
    still_losing_m3_per_day: number; open_leaks: number; alerts_raised: number; reports: number; repairs_done: number };
  money: { cost_per_m3: number; currency: string; lost: number; saved: number; still_losing_per_day: number } | null;
  money_note: string | null;
  zones: { zone: string; name: string; water_in_m3: number; expected_m3: number; lost_m3: number; lost_pct: number; data_coverage_pct: number; alerts: number; open_leaks: number }[];
  fixed: { alert: string; zone: string; zone_name: string; fixed: number; m3_per_day: number; saved_m3_in_period: number; cause: string | null; crew: string }[];
  daily: { day: number; lost_m3: number }[];
  note: string;
}
export interface EvalLocation {
  alerts: number; leaks: number; median_error_m: number | null; p80_error_m: number | null; pipe_in_search_area: number | null;
  suspect_top1: number | null; suspect_top3: number | null; suspect_top5: number | null; size_error_median_pct: number | null;
}
export interface Evaluation {
  available: boolean; how?: string;
  run?: { date: string; seed: number; per_zone: number; zones: number; ruleset: string; scenarios: number; seconds: number; noise: string };
  detection?: { slow: { n: number; detected: number; rate: number; by_age: Record<string, { n: number; detected: number; rate: number | null }> };
    burst: { n: number; detected: number; rate: number; critical: number }; all_leaks: { n: number; rate: number } };
  false_alarms?: { normal_day: { n: number; alerts: number; rate: number }; faulty_meter: { n: number; alerts: number; rate: number; by_fault: Record<string, { n: number; false_alarms: number }> } };
  location?: { with_model: EvalLocation; without_model: EvalLocation | null };
  public_reports?: { spam_one_phone: { n: number; grouped: number }; three_phones: { n: number; grouped: number; above_watch: number } };
  battledim?: BattledimResults | null;
}

export interface BattledimSummary {
  ruleset: string; nights_scored: number; scored_from: string; leaks_testable: number; found: number;
  found_within: Record<string, number>; days_to_find_median: number | null;
  false_alarms: number; false_alarms_per_month: number;
  sensor_to_leak_median_m: number | null; within_search_radius: number | null; search_radius_m: number;
  abrupt: { n: number; found: number }; incipient: { n: number; found: number };
}

export interface BattledimResults {
  network: string; tuned_on: string; tested_on: string; newest: string;
  rulesets: Record<string, BattledimSummary>;
  leaks: { pipe: string; kind: string; start: string; peak_m3h: number; result: string; days_to_find?: number }[];
  locator?: { training_leaks: number; areas: number; avg_area_radius_m: number;
    test_2019: Record<string, LocatorCase>; validation_2018: Record<string, LocatorCase> } | null;
}

export interface LocatorCase {
  leaks: number; model_median_m: number | null; rules_median_m: number | null;
  model_within_500m: number; rules_within_500m: number; area_top3_classifier: number;
}
