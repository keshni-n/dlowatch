import { HttpInterceptorFn } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { LoginResult, User } from './models';

type Kind = 'staff' | 'crew';

interface Session { token: string; user: User; }

function load(kind: Kind): Session | null {
  try {
    const raw = localStorage.getItem(`dlowatch.${kind}`);
    if (!raw) return null;
    const s = JSON.parse(raw) as Session;
    const payload = JSON.parse(atob(s.token.split('.')[0].replace(/-/g, '+').replace(/_/g, '/')));
    return payload.exp * 1000 > Date.now() ? s : null;
  } catch {
    return null;
  }
}

/** Staff and crew sessions are kept apart, so one phone or PC can hold one of each. */
@Injectable({ providedIn: 'root' })
export class Auth {
  readonly staff = signal<Session | null>(load('staff'));
  readonly crew = signal<Session | null>(load('crew'));
  readonly staffUser = computed(() => this.staff()?.user ?? null);
  readonly crewUser = computed(() => this.crew()?.user ?? null);
  /** Managers can look at everything but cannot send crews or close alerts. */
  readonly isAdmin = computed(() => this.staff()?.user.role === 'admin');
  readonly canAct = computed(() => ['operator', 'admin'].includes(this.staff()?.user.role ?? ''));

  save(kind: Kind, r: LoginResult) {
    const s = { token: r.token, user: r.user };
    try { localStorage.setItem(`dlowatch.${kind}`, JSON.stringify(s)); } catch { /* private mode */ }
    (kind === 'staff' ? this.staff : this.crew).set(s);
  }

  signOut(kind: Kind) {
    try { localStorage.removeItem(`dlowatch.${kind}`); } catch { /* ignore */ }
    (kind === 'staff' ? this.staff : this.crew).set(null);
  }

  tokenFor(url: string): string | null {
    if (url.includes('/api/crew/')) return this.crew()?.token ?? null;
    if (url.includes('/api/public/') || url.includes('/api/auth/')) return null;
    return this.staff()?.token ?? null;
  }
}

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(Auth);
  const token = auth.tokenFor(req.url);
  return next(token ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : req);
};

export const staffGuard: CanActivateFn = (_r, state) => {
  const auth = inject(Auth);
  const s = auth.staff();
  if (s && (s.user.role === 'operator' || s.user.role === 'admin' || s.user.role === 'manager')) return true;
  return inject(Router).createUrlTree(['/login'], { queryParams: { next: state.url } });
};

export const crewGuard: CanActivateFn = () => {
  const s = inject(Auth).crew();
  if (s && s.user.role === 'crew') return true;
  return inject(Router).createUrlTree(['/crew/login']);
};

/** The system test page is for admins only. */
export const opsGuard: CanActivateFn = () => {
  const auth = inject(Auth);
  return auth.isAdmin() ? true : inject(Router).createUrlTree(['/dashboard']);
};
