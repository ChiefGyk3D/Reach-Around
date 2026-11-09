// SPDX-License-Identifier: Apache-2.0
//
// Analytics health (issues #401, #402). One bounded step in the hourly cron that
//
//   1. records collection COVERAGE for the hour that just ended
//      (analytics_coverage, migration 0014), so a missing stretch is a gap with
//      a reason and never a zero, and
//   2. when the owner turned the `analytics_health` alert on, compares a few
//      counters with fixed limits and tells the owner once when a condition
//      starts and once when it clears.
//
// CONDITIONS (closed set, types.ts HEALTH_CONDITIONS):
//   rollup_overdue     rollup_last_ok (the heartbeat) is older than 3 hours
//   rollup_stale       a site's rolled_through checkpoint is more than 2 UTC days behind
//   zero_views         no human view counted in the last 24 hours on a site that had
//                      views in the 24 hours before, and the kill switch is off
//   view_budget_hit    the account hourly view budget was reached in the last 3 hours
//   alert_budget_near  today's alert budget is at least 80 percent used
//
// RULES.
// - Reads only counters and the heartbeat in view_meta (the same keys the
//   rollup, purge and view route already write) plus the alert day counter. It
//   never reads view_events, click_events, a lead or any raw table.
// - Content-free: an alert names a condition word and at most one count. No
//   site id, page, link, URL or label.
// - UTC days and hours only.
// - Quiet: nothing is sent unless alerts are configured, the event is enabled,
//   and a condition changes state. A state is remembered in view_meta
//   (`health:<condition>`) ONLY after at least one channel took the message, so a
//   message nobody received is tried again next hour (bounded by the shared
//   dedup, per-minute and daily alert budgets).
// - Bounded: at most MAX_DELIVERIES_PER_TICK channel deliveries (a message goes to every channel), and every query goes through
//   the invocation's meter (two queries when idle, one more per state change
//   plus what the dispatcher uses). Out of budget means the step stops.
// - Fail closed: nothing here throws into the cron. An alert failure never
//   blocks collection, the rollup or the purges.
// - A condition whose input cannot be read or parsed is unknown and changes no state.

import { alertStatus as sharedStatus, notifyOwner, parseAlertsConfig } from "./alerts";
import { buildChannels } from "./alerts/dispatcher";
import { QueryMeter, meterOf, meteredDb } from "./d1budget";
import { logEvent } from "./kit/log";
import type { AlertEvent, AlertStatus, AlertsEnv, DispatchDeps } from "./alerts";
import { siteKey, windowStart } from "./alerts/quota";
import { DEFAULT_ACCOUNT_HUMAN_PER_HOUR, MAX_VIEWS_PER_HOUR, positiveInt } from "./pvlimits";
import { loadKitLimits } from "./kit/budgets";
import { HEALTH_CONDITIONS, type HealthCondition } from "./alerts/types";
import { padHour } from "./viewretention";
import { killedSites, loadSites } from "./sites";
import { flagOn } from "./view";

export const HOUR_MS = 3_600_000;
export const DAY_MS = 86_400_000;
export const ROLLUP_OVERDUE_MS = 3 * HOUR_MS;
/** A checkpoint older than this many UTC days before today is stale (2 is normal just after midnight). */
export const STALE_DAYS = 2;
export const VIEW_BUDGET_WINDOW_HOURS = 3;
export const ALERT_BUDGET_NEAR_PERCENT = 80;
/** An active zero_views with no counters left is kept this long, then cleared. */
export const ZERO_VIEWS_MAX_AGE_MS = 7 * DAY_MS;
/** Channel deliveries (one per channel per message) one cron run may attempt. */
export const MAX_DELIVERIES_PER_TICK = 3;
export const COVERAGE_KEEP_DAYS = 400;
/** Metered D1 queries the cron sets aside for this step, and the smallest QUERY_BUDGET that runs it. */
export const HEALTH_QUERIES = 8;
export const HEALTH_MIN_BUDGET = 12;

export interface HealthEnv extends AlertsEnv {
  VIEWS_KILL?: string;
  VIEWS_KILL_SITES?: string;
  SITES?: string;
  ACCOUNT_VIEWS_PER_HOUR?: string;
}

export interface HealthDeps {
  /** Test seam: a dispatcher instance. Defaults to the shared one. */
  dispatcher?: { notify(env: AlertsEnv, event: AlertEvent, deps?: DispatchDeps): Promise<void>; status(): AlertStatus };
  fetchFn?: typeof fetch;
}

export interface HealthResult {
  /** The coverage letter written for the hour that ended, or "" when the write failed. */
  coverage: "o" | "k" | "p" | "q" | "";
  /** Conditions found active (unknown ones are left out). */
  active: HealthCondition[];
  /** Messages a channel took (firing or cleared). */
  notified: number;
  /** Set to a fixed word when the step runs in degraded mode (no coverage, no alerts). */
  degraded?: "coverage_schema";
}

function utcDate(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

const EMPTY_DAY = "-".repeat(24);

function sentTotal(s: AlertStatus): number {
  let n = 0;
  for (const c of Object.values(s.channels)) n += c?.sent ?? 0;
  return n;
}

interface Inputs {
  meta: Map<string, string>;
  /** hour number -> accepted human views, account-wide, from bud:<hour>:_all:pv */
  views: Map<number, number>;
  /** site id -> hour number -> accepted human views, from bud:<hour>:<site>:pv */
  siteViews: Map<string, Map<number, number>>;
  alertToday: number;
  /** False when analytics_coverage is not the table migration 0014 creates. */
  schemaOk: boolean;
}

/** What the table definition must contain (migration 0014): strict, keyed by day, 24 closed letters. */
const SCHEMA_MARKS = ["STRICT", "WITHOUT ROWID", "length(hours) = 24", "[^okpq-]", "GLOB '[0-9]"];

/** One metered read batch. Throws on a database error (the caller treats that as "unknown"). */
async function readInputs(db: D1Database, env: HealthEnv, hour: number, nowMs: number): Promise<Inputs> {
  const lo = `bud:${padHour(hour - 47)}`;
  const hi = `bud:${padHour(hour + 1)}`;
  const res = await db.batch([
    db
      .prepare(
        "SELECT key, value FROM view_meta WHERE key IN ('rollup_last_ok', 'rollup_err') " +
          "OR key GLOB 'rolled_through:*' OR key GLOB 'health:*' LIMIT 64",
      ),
    db
      .prepare("SELECT key, value FROM view_meta WHERE key >= ?1 AND key < ?2 AND key GLOB 'bud:*:pv' LIMIT 600")
      .bind(lo, hi),
    db
      .prepare(
        "SELECT count FROM alert_quota WHERE site = ?1 AND channel = 'all' AND scope = 'day' AND window_start = ?2",
      )
      .bind(siteKey(env.SITE_HOST), windowStart("day", nowMs)),
    db.prepare("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'analytics_coverage'"),
  ]);
  const ddl = String(((res[3]?.results ?? [])[0] as { sql?: string } | undefined)?.sql ?? "");
  const schemaOk = SCHEMA_MARKS.every((m) => ddl.includes(m));
  const meta = new Map<string, string>();
  for (const r of (res[0]?.results ?? []) as Array<{ key: string; value: string }>) {
    if (typeof r.key === "string" && typeof r.value === "string") meta.set(r.key, r.value);
  }
  const views = new Map<number, number>();
  const siteViews = new Map<string, Map<number, number>>();
  for (const r of (res[1]?.results ?? []) as Array<{ key: string; value: string }>) {
    const m = /^bud:(\d{8}):(_all|[a-z0-9][a-z0-9-]{0,19}):pv$/.exec(String(r.key));
    const n = Number(r.value);
    if (m === null || !Number.isInteger(n) || n < 0) continue;
    if (m[2] === "_all") views.set(Number(m[1]), n);
    else {
      const per = siteViews.get(m[2]!) ?? new Map<number, number>();
      per.set(Number(m[1]), n);
      siteViews.set(m[2]!, per);
    }
  }
  const row = (res[2]?.results ?? [])[0] as { count?: number } | undefined;
  const alertToday = typeof row?.count === "number" && Number.isInteger(row.count) && row.count >= 0 ? row.count : 0;
  return { meta, views, siteViews, alertToday, schemaOk };
}

interface Verdict {
  active: boolean;
  count: number;
}

/** Pure: each condition is active, inactive or unknown (absent). */
export function evaluate(
  inp: Inputs,
  nowMs: number,
  opts: { killed: boolean; killedSites?: ReadonlySet<string>; accountLimit: number; alertBudget: number },
): Map<HealthCondition, Verdict> {
  const out = new Map<HealthCondition, Verdict>();
  const hour = Math.floor(nowMs / HOUR_MS);

  // rollup_overdue: a missing heartbeat is unknown (a fresh install), a malformed one is unknown.
  const lastOkRaw = inp.meta.get("rollup_last_ok");
  if (lastOkRaw !== undefined && /^\d{1,15}$/.test(lastOkRaw)) {
    const age = nowMs - Number(lastOkRaw);
    out.set("rollup_overdue", { active: age > ROLLUP_OVERDUE_MS, count: Math.max(0, Math.floor(age / HOUR_MS)) });
  }

  // rollup_stale: any site's checkpoint more than STALE_DAYS UTC days before today.
  let worst = -1;
  let seen = false;
  const today = Date.parse(`${utcDate(nowMs)}T00:00:00Z`);
  for (const [k, v] of inp.meta) {
    if (!k.startsWith("rolled_through:") || !/^\d{4}-\d{2}-\d{2}$/.test(v)) continue;
    const t = Date.parse(`${v}T00:00:00Z`);
    if (!Number.isFinite(t)) continue;
    seen = true;
    worst = Math.max(worst, Math.round((today - t) / DAY_MS));
  }
  if (seen) out.set("rollup_stale", { active: worst > STALE_DAYS, count: Math.max(0, worst) });

  // zero_views, per site: a site that had views in the 24 hours before and none in the last 24 is quiet,
  // unless its route is switched off (VIEWS_KILL, or its id in VIEWS_KILL_SITES). The alert names no site.
  // No counters at all in the window (the 48 hour purge, or a new database) is NO DATA: it neither raises
  // the alert nor clears an active one, until ZERO_VIEWS_MAX_AGE_MS has passed since it started.
  const killedSites = opts.killedSites ?? new Set<string>();
  const windows = [...inp.siteViews.entries()].map(([site, per]) => {
    let recent = 0;
    let before = 0;
    for (const [h, n] of per) {
      if (h > hour - 24 && h <= hour) recent += n;
      else if (h > hour - 48 && h <= hour - 24) before += n;
    }
    return { site, recent, before };
  });
  const quiet = windows.some((w) => !opts.killed && !killedSites.has(w.site) && w.recent === 0 && w.before > 0);
  const anyData = windows.some((w) => w.recent > 0 || w.before > 0);
  const sinceRaw = inp.meta.get("health:zero_views");
  if (quiet) out.set("zero_views", { active: true, count: 0 });
  else if (anyData || sinceRaw === undefined) out.set("zero_views", { active: false, count: 0 });
  else {
    const since = /^\d{1,15}$/.test(sinceRaw) ? Number(sinceRaw) : 0;
    if (nowMs - since > ZERO_VIEWS_MAX_AGE_MS) out.set("zero_views", { active: false, count: 0 });
    // else: no data, keep the state (no verdict)
  }

  // view_budget_hit: the account ceiling was reached in a recent hour.
  if (opts.accountLimit > 0) {
    let hit = false;
    for (const [h, n] of inp.views) if (h > hour - VIEW_BUDGET_WINDOW_HOURS && h <= hour && n >= opts.accountLimit) hit = true;
    out.set("view_budget_hit", { active: hit, count: 0 });
  }

  // alert_budget_near: today's alert units against the daily budget.
  if (opts.alertBudget > 0) {
    out.set("alert_budget_near", {
      active: inp.alertToday * 100 >= opts.alertBudget * ALERT_BUDGET_NEAR_PERCENT,
      count: 0,
    });
  }
  return out;
}

/** The coverage letter for the hour that ended, from what is known now. */
export function coverageLetter(
  inp: Inputs,
  hour: number,
  opts: { killed: boolean; someSitesKilled?: boolean; accountLimit: number },
): "o" | "k" | "p" | "q" {
  if (opts.killed) return "k";
  if (opts.someSitesKilled === true) return "p";
  const n = inp.views.get(hour - 1) ?? 0;
  return opts.accountLimit > 0 && n >= opts.accountLimit ? "q" : "o";
}

/**
 * Run the step. `db` is the invocation's metered database. Never throws.
 */
export async function runHealth(env: HealthEnv, db: D1Database, nowMs: number, deps: HealthDeps = {}): Promise<HealthResult> {
  const result: HealthResult = { coverage: "", active: [], notified: 0 };
  try {
    if (!Number.isFinite(nowMs)) return result;
    const hour = Math.floor(nowMs / HOUR_MS);
    // The page-view kill switches: VIEWS_KILL (all), VIEWS_KILL_SITES (the sites named). All configured
    // sites named counts as all; some named is the letter "p" (coverage is account-wide, not per site).
    const killSites = killedSites(env.VIEWS_KILL_SITES);
    const configured = loadSites(env).sites.map((x) => x.id);
    const allSitesKilled = configured.length > 0 && configured.every((id) => killSites.has(id));
    const killed = flagOn(env.VIEWS_KILL) || allSitesKilled;
    const someSitesKilled = !killed && killSites.size > 0;
    const accountLimit = positiveInt(env.ACCOUNT_VIEWS_PER_HOUR, DEFAULT_ACCOUNT_HUMAN_PER_HOUR, MAX_VIEWS_PER_HOUR);
    let inp: Inputs;
    try {
      inp = await readInputs(db, env, hour, nowMs);
    } catch {
      return result; // no read, no claim: a hole in coverage is shown as a gap, never guessed
    }

    // 0. The table must be the one the migration made. Otherwise: degraded, no coverage write, no alert.
    if (!inp.schemaOk) {
      result.degraded = "coverage_schema";
      logEvent("log", "failed", "other");
      return result;
    }

    // 1. Coverage for the hour that just ended.
    const letter = coverageLetter(inp, hour, { killed, someSitesKilled, accountLimit });
    const ended = (hour - 1) * HOUR_MS;
    const idx = new Date(ended).getUTCHours();
    try {
      await db.batch([
        db
          .prepare(
            "INSERT INTO analytics_coverage (day, hours) VALUES (?1, substr(?4, 1, ?2) || ?3 || substr(?4, ?2 + 2)) " +
              "ON CONFLICT(day) DO UPDATE SET hours = substr(hours, 1, ?2) || ?3 || substr(hours, ?2 + 2)",
          )
          .bind(utcDate(ended), idx, letter, EMPTY_DAY),
        db.prepare("DELETE FROM analytics_coverage WHERE day < ?1").bind(utcDate(nowMs - COVERAGE_KEEP_DAYS * DAY_MS)),
      ]);
      result.coverage = letter;
    } catch {
      // Collection is unaffected; the hour stays "-" and shows as an outage.
    }

    // 2. Alerts, only when the owner switched them on.
    const cfg = parseAlertsConfig(env.ALERTS_CONFIG);
    if (cfg === null || !cfg.events.has("analytics_health")) return result;
    let alertBudget = 0; // unusable limits: that one condition is unknown, the others still run
    try {
      alertBudget = loadKitLimits(env).dailyAlertBudget;
    } catch {
      alertBudget = 0;
    }
    const verdicts = evaluate(inp, nowMs, { killed, killedSites: killSites, accountLimit, alertBudget });
    for (const [c, v] of verdicts) if (v.active) result.active.push(c);

    const disp = deps.dispatcher ?? { notify: notifyOwner, status: sharedStatus };
    const meter = meterOf(db);
    const fanout = Math.max(1, buildChannels(env, cfg.channels, cfg.matrixAllowedHosts).channels.length);
    let deliveries = MAX_DELIVERIES_PER_TICK;
    for (const cond of HEALTH_CONDITIONS) {
      const v = verdicts.get(cond);
      if (v === undefined) continue;
      const key = `health:${cond}`;
      const had = inp.meta.has(key);
      if (v.active === had) continue;
      // Bound the channel deliveries (each message goes to every configured channel).
      if (fanout > deliveries) break;
      // Keep one query for this transition's state write: the dispatcher may use the rest, never that one.
      let notifyDb = db;
      if (meter !== undefined) {
        const rem = meter.remaining();
        if (rem < 2) break;
        // An independent cap on top of db (db itself still counts each query once against the run's meter).
        notifyDb = meteredDb(db, new QueryMeter(rem - 1));
      }
      deliveries -= fanout;
      const before = sentTotal(disp.status());
      await disp.notify(
        { ...env, DB: notifyDb },
        { kind: "analytics_health", atMs: nowMs, condition: cond, state: v.active ? "firing" : "cleared", count: v.count },
        { fetchFn: deps.fetchFn, nowMs },
      );
      if (sentTotal(disp.status()) > before) {
        result.notified++;
        // Persist right away, before anything else can use the allowance.
        try {
          await (v.active
            ? db
                .prepare("INSERT INTO view_meta (key, value) VALUES (?1, ?2) ON CONFLICT(key) DO UPDATE SET value = excluded.value")
                .bind(key, String(Math.floor(nowMs)))
                .run()
            : db.prepare("DELETE FROM view_meta WHERE key = ?1").bind(key).run());
        } catch {
          // Cannot be recorded; the dispatcher's dedup covers ten minutes only. Stop sending this run.
          break;
        }
      }
    }
  } catch {
    // Fail closed: health must never break the cron.
  }
  return result;
}
