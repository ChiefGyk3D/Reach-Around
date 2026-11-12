// SPDX-License-Identifier: Apache-2.0
//
// Route budgets, the daily send and alert budgets, and storage caps for the
// media kit, sized to the Cloudflare FREE plan.
//
// RULING (cutover slice, hosting plan): an outage when a limit is hit is
// accepted over any bill. Everything here fails CLOSED. A reservation is false
// when the limit is reached, when the configuration is invalid, when the time
// or route is bad, and when the database errors. No path grants on error.
//
// ENV. Strict, like the kit kill switch: a value must be an unsigned decimal
// integer of at most nine digits, exactly, no sign, space, exponent or other
// form, inside the variable's range. Unset or "" is the default. Anything else
// is invalid; the load throws KitLimitsError listing variable NAMES only, and
// every reservation then refuses. Nothing is clamped or guessed.
//
//   ROUTE_BUDGET_GATE    0..100000 per UTC hour, default 150
//   ROUTE_BUDGET_VIEW    0..100000 per UTC hour, default 300
//   MAX_LEADS            1..50000, default 5000 (the lead cap upsertLead enforces)
//   MAX_KIT_EVENTS       1..500000, default 50000 (cap on stored kit_events)
//   DAILY_ALERT_BUDGET   0..1000 per UTC day, default 20 (owner alerts)
//   DAILY_SEND_BUDGET    0..1000 per UTC day, default 0 (no send route in v1)
//   D1_DAILY_WRITE_QUOTA 1000..999999999, default 100000 (the free D1 plan's
//                        daily rows-written figure; re-check against Cloudflare)
//
// The load REFUSES a configuration whose COMBINED worst-case daily D1 writes
// (kit, page views, clicks, the click funnel, a margin) exceed the quota, and warns above 80
// percent. The D1 write allowance is one per account, so the kit never gets to
// count only itself.
//
// COST. Counters are one row per (key, hour or day), written by one conditional
// upsert; a refused reservation writes nothing. Event storage is capped by the
// LIVE row count. The rowid span (MAX(id) - MIN(id) + 1) is O(1) and never
// smaller than the row count, so a span under the cap proves there is room;
// only when the span is at or over the cap is the exact count taken (bounded
// to `cap` rows by a LIMIT), so deletes that leave sparse rows free room again.
// A refusal is remembered for EVENT_FULL_MEMORY_MS so a flood at the cap does
// not rescan the table on every request; the memory only ever refuses.
//
// WIRING. Per-client throttling (checkIpMinute) must run BEFORE a global route
// counter is consumed, so one client cannot spend the shared budget.

import { MAX_LEADS_CEILING } from "./leads";
import { reserveBudget, utcDay, utcHour, MAX_RESERVE_LIMIT } from "./limits";
import type { KitEventKind } from "./leads";
import { pageViewDailyWrites, type PageViewBudgetEnv } from "../pvlimits";
import { FUNNEL_CAPS, funnelDailyWrites, type FunnelCaps } from "../funnelcaps";
import { loadSites } from "../sites";

export type RouteName = "gate" | "view";

export const DEFAULTS = {
  routeGatePerHour: 150,
  routeViewPerHour: 300,
  maxLeads: 5000,
  maxKitEvents: 50_000,
  dailyAlertBudget: 20,
  dailySendBudget: 0,
  d1DailyWriteQuota: 100_000,
} as const;

/** Every variable parseKitLimits reads. A test keeps this equal to the names parseKitLimits asks for below. */
export const KIT_LIMIT_VARS = [
  "ROUTE_BUDGET_GATE",
  "ROUTE_BUDGET_VIEW",
  "MAX_LEADS",
  "MAX_KIT_EVENTS",
  "DAILY_ALERT_BUDGET",
  "DAILY_SEND_BUDGET",
  "D1_DAILY_WRITE_QUOTA",
] as const;

export const MAX_KIT_EVENTS_CEILING = 500_000;
const MAX_DAILY_BUDGET = 1000;
const MIN_QUOTA = 1000;
const MAX_QUOTA = 999_999_999; // nine digits, the most parseStrict accepts

/** Worst-case rows written by one accepted operation (the row, its indexes, the counter). */
export const WRITES_PER_OP = { gate: 5, view: 2, send: 7, alert: 1, inquiry: 1 } as const;
/**
 * Page-view feature, rollup and purge worst case per day at the DEFAULT hourly
 * budgets (docs/deploy.md, page views). The loader uses the configured
 * VIEWS_PER_HOUR and BOT_VIEWS_PER_HOUR instead (pvlimits.ts).
 */
export const PAGE_VIEW_DAILY_WRITES = pageViewDailyWrites({});
/** Click route allowance per day. The click route has no global budget, only a per-client limiter, so this is a reserve, not a measured bound. */
export const CLICK_DAILY_WRITES = 8_000;
/**
 * Click funnel worst case per day at `caps` (default: the caps the Worker enforces, funnelcaps.ts),
 * for the sites the environment configures (at least one, as for page views). The sites' link ids set
 * the reserved cells. Counting on existing cells stays in CLICK_DAILY_WRITES above.
 */
export function funnelDailyWritesFor(env: PageViewBudgetEnv, caps: FunnelCaps = FUNNEL_CAPS): number {
  const r = loadSites(env ?? {});
  // A malformed entry disables only itself (sites.ts); the valid ones are still served, so count them.
  const sites = r.sites.length > 0 ? r.sites.map((s) => ({ links: s.linkIds.size })) : [{ links: 0 }];
  return funnelDailyWrites(caps, sites);
}
/** Default funnel figure: one site with no link ids at the Worker's caps. */
export const FUNNEL_DAILY_WRITES = funnelDailyWritesFor({});
/** Headroom over everything above. */
export const COMBINED_MARGIN = 5_000;
/** Daily reserve for the purge and the hourly rollup, counted against the quota. */
export const PURGE_RESERVE = 10_000;
/** Pessimistic stored size per row, indexes included. */
export const BYTES_PER_LEAD = 600;
export const BYTES_PER_EVENT = 100;

export interface KitLimitsEnv extends PageViewBudgetEnv {
  ROUTE_BUDGET_GATE?: string;
  ROUTE_BUDGET_VIEW?: string;
  MAX_LEADS?: string;
  MAX_KIT_EVENTS?: string;
  DAILY_ALERT_BUDGET?: string;
  DAILY_SEND_BUDGET?: string;
  D1_DAILY_WRITE_QUOTA?: string;
}

/** The limits are unusable. Carries variable names only, never values. */
export class KitLimitsError extends Error {
  readonly fields: readonly string[];
  constructor(fields: readonly string[]) {
    super(`kit limits invalid: ${fields.join(", ")}`);
    this.name = "KitLimitsError";
    this.fields = fields;
  }
}

export interface KitLimits {
  routeGatePerHour: number;
  routeViewPerHour: number;
  maxLeads: number;
  maxKitEvents: number;
  dailyAlertBudget: number;
  dailySendBudget: number;
  d1DailyWriteQuota: number;
  /** Non-fatal notes, e.g. worst-case writes above 80 percent of the quota. */
  warnings: readonly string[];
}

const DIGITS = /^[0-9]{1,9}$/;

/** Returns the number, `fallback` for unset or "", or NaN for anything invalid. */
function parseStrict(v: unknown, lo: number, hi: number, fallback: number): number {
  if (v === undefined || v === "") return fallback;
  if (typeof v !== "string" || !DIGITS.test(v)) return NaN;
  const n = Number(v);
  return n >= lo && n <= hi ? n : NaN;
}

export function worstCaseDailyWrites(
  c: Pick<KitLimits, "routeGatePerHour" | "routeViewPerHour" | "dailyAlertBudget" | "dailySendBudget">,
): number {
  return (
    24 * (c.routeGatePerHour * WRITES_PER_OP.gate + c.routeViewPerHour * WRITES_PER_OP.view) +
    c.dailyAlertBudget * WRITES_PER_OP.alert +
    // An accepted inquiry is capped by the daily alert budget (kit/inquiry.ts) and writes one counter row.
    c.dailyAlertBudget * WRITES_PER_OP.inquiry +
    c.dailySendBudget * WRITES_PER_OP.send +
    PURGE_RESERVE
  );
}

/**
 * The figure checked against the quota: the kit plus everything else sharing the
 * allowance. `pageViews` is the page-view feature's worst case (pageViewDailyWrites).
 */
export function combinedDailyWrites(
  c: Parameters<typeof worstCaseDailyWrites>[0],
  pageViews: number = PAGE_VIEW_DAILY_WRITES,
  funnel: number = FUNNEL_DAILY_WRITES,
): number {
  return worstCaseDailyWrites(c) + pageViews + CLICK_DAILY_WRITES + funnel + COMBINED_MARGIN;
}

export function worstCaseStorageBytes(c: Pick<KitLimits, "maxLeads" | "maxKitEvents">): number {
  return c.maxLeads * BYTES_PER_LEAD + c.maxKitEvents * BYTES_PER_EVENT;
}

type Parsed = Omit<KitLimits, "warnings">;

/** Parse every limit. `bad` names the variables that are not usable (never their values). */
function parseKitLimits(env: KitLimitsEnv): { c: Parsed; bad: string[] } {
  const e = (env ?? {}) as unknown as Record<string, unknown>;
  const bad: string[] = [];
  const get = (name: string, lo: number, hi: number, fallback: number): number => {
    const n = parseStrict(e[name], lo, hi, fallback);
    if (Number.isNaN(n)) {
      bad.push(name);
      return fallback;
    }
    return n;
  };
  const c: Parsed = {
    routeGatePerHour: get("ROUTE_BUDGET_GATE", 0, MAX_RESERVE_LIMIT, DEFAULTS.routeGatePerHour),
    routeViewPerHour: get("ROUTE_BUDGET_VIEW", 0, MAX_RESERVE_LIMIT, DEFAULTS.routeViewPerHour),
    maxLeads: get("MAX_LEADS", 1, MAX_LEADS_CEILING, DEFAULTS.maxLeads),
    maxKitEvents: get("MAX_KIT_EVENTS", 1, MAX_KIT_EVENTS_CEILING, DEFAULTS.maxKitEvents),
    dailyAlertBudget: get("DAILY_ALERT_BUDGET", 0, MAX_DAILY_BUDGET, DEFAULTS.dailyAlertBudget),
    dailySendBudget: get("DAILY_SEND_BUDGET", 0, MAX_DAILY_BUDGET, DEFAULTS.dailySendBudget),
    d1DailyWriteQuota: get("D1_DAILY_WRITE_QUOTA", MIN_QUOTA, MAX_QUOTA, DEFAULTS.d1DailyWriteQuota),
  };
  return { c, bad };
}

/** True above 80 percent of the quota (integer math, no rounding). */
const above80 = (worst: number, quota: number): boolean => worst * 10 > quota * 8;

/** Parse and validate every limit. Throws KitLimitsError (names only). Cheap: call per request. */
export function loadKitLimits(env: KitLimitsEnv): KitLimits {
  const { c, bad } = parseKitLimits(env);
  if (bad.length > 0) throw new KitLimitsError(bad);

  const worst = combinedDailyWrites(c, pageViewDailyWrites(env), funnelDailyWritesFor(env));
  if (worst > c.d1DailyWriteQuota) throw new KitLimitsError(["D1_DAILY_WRITE_QUOTA"]);
  const warnings = above80(worst, c.d1DailyWriteQuota) ? ["combined worst-case daily D1 writes are above 80 percent of D1_DAILY_WRITE_QUOTA"] : [];
  return { ...c, warnings };
}

export interface BudgetAssessment {
  /** ok: at or under 80 percent. warn: above 80, within the quota. fail: invalid, or over the quota. */
  level: "ok" | "warn" | "fail";
  /** Names of unusable variables, if any (level is then fail). */
  invalid: readonly string[];
  /** Worst-case daily D1 writes: the kit, page views, clicks, the click funnel and the margin. */
  combined: number;
  /** The page-view part of `combined`. */
  pageViews: number;
  /** The click funnel part of `combined` (funnelcaps.ts). */
  funnel: number;
  quota: number;
}

/**
 * The same verdict loadKitLimits reaches, as data, for the deploy-time check
 * (scripts/check-budget.ts). It calls the same functions on the same figures,
 * so the deploy gate and the Worker's own refusal cannot drift apart.
 */
export function assessDailyBudget(env: KitLimitsEnv, funnelCaps: FunnelCaps = FUNNEL_CAPS): BudgetAssessment {
  const { c, bad } = parseKitLimits(env);
  const pageViews = pageViewDailyWrites(env);
  const funnel = funnelDailyWritesFor(env, funnelCaps);
  const combined = combinedDailyWrites(c, pageViews, funnel);
  const level = bad.length > 0 || combined > c.d1DailyWriteQuota ? "fail" : above80(combined, c.d1DailyWriteQuota) ? "warn" : "ok";
  return { level, invalid: bad, combined, pageViews, funnel, quota: c.d1DailyWriteQuota };
}

type DbEnv = KitLimitsEnv & { DB: D1Database };

/**
 * Reserve one request on a route for the current UTC hour. False means answer
 * with the generic failure and do nothing else. Fails closed on every error.
 */
export async function reserveRoute(env: DbEnv, route: RouteName, nowMs: number): Promise<boolean> {
  try {
    const c = loadKitLimits(env);
    let limit: number;
    if (route === "gate") limit = c.routeGatePerHour;
    else if (route === "view") limit = c.routeViewPerHour;
    else return false;
    return await reserveBudget(env.DB, `route-${route}`, utcHour(nowMs), limit); // CONTROL:route-budget
  } catch {
    return false;
  }
}

async function reserveDaily(env: DbEnv, key: string, pick: (c: KitLimits) => number, nowMs: number): Promise<boolean> {
  try {
    return await reserveBudget(env.DB, key, utcDay(nowMs), pick(loadKitLimits(env)));
  } catch {
    return false;
  }
}

/** One outgoing email against the daily send budget (default 0: none allowed). */
export function reserveSend(env: DbEnv, nowMs: number): Promise<boolean> {
  return reserveDaily(env, "mail", (c) => c.dailySendBudget, nowMs); // CONTROL:send-budget
}

/** One owner alert against the daily alert budget. */
export function reserveAlert(env: DbEnv, nowMs: number): Promise<boolean> {
  return reserveDaily(env, "alert", (c) => c.dailyAlertBudget, nowMs); // CONTROL:alert-budget
}

const KINDS: readonly string[] = ["unlock", "view", "verify"];
const HOUR_MS = 3_600_000;
const EVENT_FULL_MEMORY_MS = 30_000;
/** Refusal memory per database object and cap: a new cap or another database is always checked. */
let eventsFull = new WeakMap<object, { cap: number; until: number }>();

/** Forget a remembered refusal. For tests. */
export function resetEventCapCache(): void {
  eventsFull = new WeakMap();
}

/**
 * Record a kit event only while the live row count is below `maxEvents` (see
 * the header). The check and the insert are one statement, so
 * concurrent callers cannot overshoot. Returns false when full. The time is
 * rounded down to the hour, as recordKitEvent does.
 */
export async function recordKitEventCapped(
  db: D1Database,
  leadId: string,
  kind: KitEventKind,
  nowMs: number,
  maxEvents: number,
): Promise<boolean> {
  if (!KINDS.includes(kind)) throw new RangeError("unknown kit event kind");
  if (typeof nowMs !== "number" || !Number.isFinite(nowMs) || nowMs < 0) throw new RangeError("nowMs must be a finite number");
  const cap: unknown = maxEvents;
  if (typeof cap !== "number" || !Number.isInteger(cap) || cap < 1 || cap > MAX_KIT_EVENTS_CEILING) {
    throw new RangeError(`maxEvents must be an integer from 1 to ${MAX_KIT_EVENTS_CEILING}`);
  }
  const ts = Math.floor(nowMs / HOUR_MS) * HOUR_MS;
  const seen = eventsFull.get(db);
  if (seen !== undefined && seen.cap === cap && Date.now() < seen.until) return false;
  const r = await db
    .prepare(
      `INSERT INTO kit_events (ts, lead_id, kind)
       SELECT ?1, ?2, ?3
       WHERE (SELECT COALESCE(MAX(id) - MIN(id) + 1, 0) FROM kit_events) < ?4
          OR (SELECT COUNT(*) FROM (SELECT 1 FROM kit_events LIMIT ?4)) < ?4`,
    )
    .bind(ts, leadId, kind, cap)
    .run();
  if (r.meta.changes === 1) return true;
  eventsFull.set(db, { cap, until: Date.now() + EVENT_FULL_MEMORY_MS });
  return false;
}
