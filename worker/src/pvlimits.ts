// SPDX-License-Identifier: Apache-2.0
//
// The page-view hourly budgets and the D1 writes they imply. One place, so the
// view route (view.ts), the kit's combined D1 check (kit/budgets.ts) and the
// deploy-time check (scripts/check-budget.ts) cannot disagree.
//
// There is no sampling: a view over an hourly budget is dropped, so the budgets
// are the cap on accepted views. The projection uses the binding ceiling for
// humans and for bots separately: the lower of every served site's own budget
// added up and the account-wide ceiling. A site count or ceiling that cannot be
// determined is read as the worst case, never as zero.

import { loadSites, MAX_SITES, type SitesEnv } from "./sites";

export const DEFAULT_HUMAN_PER_HOUR = 120;
export const DEFAULT_BOT_PER_HOUR = 30;
export const MAX_VIEWS_PER_HOUR = 100_000;
/** Account-wide ceilings above the per-site budgets (view.ts enforces them). */
export const DEFAULT_ACCOUNT_HUMAN_PER_HOUR = 480;
export const DEFAULT_ACCOUNT_BOT_PER_HOUR = 120;

/** The Worker's lenient integer read: digits only, at most `max`, else the fallback. */
export function positiveInt(v: string | undefined, fallback: number, max: number): number {
  if (typeof v !== "string" || !/^\d{1,9}$/.test(v)) return fallback;
  const n = Number(v);
  return n <= max ? n : fallback;
}

/** Rows written per accepted view: 28/5 = 5.6 (docs/deploy.md, Plan 4 P-6: 20,160 for 3,600 views). */
export const PV_WRITES_PER_VIEW = { num: 28, den: 5 } as const;
/** Rollup, purge and idle-hour heartbeat rows per day, whatever the traffic (2,200 + 250 + rounding, docs/deploy.md). */
export const PV_FIXED_DAILY_WRITES = 2_540;

export interface PageViewBudgetEnv extends SitesEnv {
  VIEWS_PER_HOUR?: string;
  BOT_VIEWS_PER_HOUR?: string;
  ACCOUNT_VIEWS_PER_HOUR?: string;
  ACCOUNT_BOT_VIEWS_PER_HOUR?: string;
}

/**
 * How many sites share the account ceilings. A configuration that does not load
 * cleanly (any error, so a site might be served that we cannot count) is read
 * as MAX_SITES: the projection is then high, never low. Nothing configured is
 * one site, so a kit-only deployment keeps the documented reserve.
 */
export function pageViewSiteCount(env: SitesEnv): number {
  const r = loadSites(env ?? {});
  if (r.errors.length > 0) return MAX_SITES;
  return Math.max(1, Math.min(r.sites.length, MAX_SITES));
}

/** Accepted views per hour that can bind, for one class: the lower of all sites' budgets and the account ceiling. */
function bindingPerHour(perSite: number, sites: number, account: number): number {
  return Math.min(sites * perSite, account);
}

/**
 * Worst-case rows written per UTC day by the page-view feature. `sites` is the
 * number of sites served (default: counted from the environment by
 * pageViewSiteCount); a value that is not an integer from 1 to MAX_SITES is
 * read as MAX_SITES.
 */
export function pageViewDailyWrites(env: PageViewBudgetEnv, sites?: number): number {
  const e = env ?? {};
  const n = sites === undefined ? pageViewSiteCount(e) : Number.isInteger(sites) && sites >= 1 && sites <= MAX_SITES ? sites : MAX_SITES;
  const human = bindingPerHour(
    positiveInt(e.VIEWS_PER_HOUR, DEFAULT_HUMAN_PER_HOUR, MAX_VIEWS_PER_HOUR),
    n,
    positiveInt(e.ACCOUNT_VIEWS_PER_HOUR, DEFAULT_ACCOUNT_HUMAN_PER_HOUR, MAX_VIEWS_PER_HOUR),
  );
  const bot = bindingPerHour(
    positiveInt(e.BOT_VIEWS_PER_HOUR, DEFAULT_BOT_PER_HOUR, MAX_VIEWS_PER_HOUR),
    n,
    positiveInt(e.ACCOUNT_BOT_VIEWS_PER_HOUR, DEFAULT_ACCOUNT_BOT_PER_HOUR, MAX_VIEWS_PER_HOUR),
  );
  const views = 24 * (human + bot);
  return Math.ceil((views * PV_WRITES_PER_VIEW.num) / PV_WRITES_PER_VIEW.den) + PV_FIXED_DAILY_WRITES;
}
