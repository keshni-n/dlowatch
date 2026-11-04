import { Routes } from '@angular/router';
import { crewGuard, opsGuard, staffGuard } from './core/auth';

export const routes: Routes = [
  // Public: no login, phone first, with a tab bar (home, my area, report, my reports, learn)
  {
    path: '',
    loadComponent: () => import('./public/shell').then(m => m.PublicShell),
    children: [
      { path: '', pathMatch: 'full', loadComponent: () => import('./public/home').then(m => m.PublicHome), title: 'DloWatch' },
      { path: 'area', loadComponent: () => import('./public/area').then(m => m.PublicArea), title: 'Water in my area' },
      { path: 'report', loadComponent: () => import('./public/report').then(m => m.PublicReport), title: 'Report a leak' },
      { path: 'report/:code', loadComponent: () => import('./public/status').then(m => m.PublicStatusPage), title: 'My report' },
      { path: 'my-reports', loadComponent: () => import('./public/mine').then(m => m.PublicMine), title: 'My reports' },
      { path: 'learn', loadComponent: () => import('./public/learn').then(m => m.PublicLearn), title: 'Learn' },
    ],
  },

  // Utility staff: web dashboard, login required
  { path: 'login', loadComponent: () => import('./staff/login').then(m => m.StaffLogin), title: 'Staff sign in' },
  {
    path: 'dashboard',
    canActivate: [staffGuard],
    loadComponent: () => import('./staff/shell').then(m => m.StaffShell),
    children: [
      { path: '', loadComponent: () => import('./staff/overview').then(m => m.OverviewPage), title: 'Overview' },
      { path: 'alerts', loadComponent: () => import('./staff/alerts').then(m => m.AlertsPage), title: 'Leak alerts' },
      { path: 'alerts/:id', loadComponent: () => import('./staff/alert-detail').then(m => m.AlertDetailPage), title: 'Leak alert' },
      { path: 'repairs', loadComponent: () => import('./staff/repairs').then(m => m.RepairsPage), title: 'Repair priority list' },
      { path: 'replay/:id', loadComponent: () => import('./staff/replay').then(m => m.ReplayPage), title: 'Leak replay' },
      { path: 'meters', loadComponent: () => import('./staff/meters').then(m => m.MetersPage), title: 'Meter data quality' },
      { path: 'reports', loadComponent: () => import('./staff/reports').then(m => m.ReportsPage), title: 'Public reports' },
      { path: 'crews', loadComponent: () => import('./staff/crews').then(m => m.CrewsPage), title: 'Field crews' },
      { path: 'report', loadComponent: () => import('./staff/report').then(m => m.ReportPage), title: 'Water loss report' },
      { path: 'simulate', canActivate: [opsGuard], loadComponent: () => import('./staff/simulate').then(m => m.SimulatePage), title: 'System test' },
    ],
  },

  // Field crew: phone, crew login
  { path: 'crew/login', loadComponent: () => import('./crew/login').then(m => m.CrewLoginPage), title: 'Crew sign in' },
  { path: 'crew', canActivate: [crewGuard], loadComponent: () => import('./crew/jobs').then(m => m.CrewJobsPage), title: "Tonight's jobs" },
  { path: 'crew/jobs/:id', canActivate: [crewGuard], loadComponent: () => import('./crew/job').then(m => m.CrewJobPage), title: 'Job' },
  { path: 'crew/jobs/:id/result', canActivate: [crewGuard], loadComponent: () => import('./crew/result').then(m => m.CrewResultPage), title: 'Log the result' },

  { path: '**', redirectTo: '' },
];
