// SPDX-License-Identifier: Apache-2.0
//
// Daily rollup of page views (Plan 4 Task A5).
//
// For every UTC day that is closed (plus a grace period) and still has raw
// rows in view_events and no row in view_rollup_log, this job aggregates the
// day into the three marginal tables of migration 0006 and writes the day's
// view_rollup_log row.
//
//   view_daily      page x source x country x bot class        (open: capped)
//   view_dim_daily  ONE dimension at a time: browser, os, device, lang, vp,
//                   net, reason, each with the bot class
//   view_geo_daily  country / region / city x bot class         (never joined
//                   to browser, system or language)
//
// What is never aggregated: the address, the user agent, any referrer URL,
// the browser major number (a version number is a fingerprint bit), and the
// visitor hash (it is in a different table and has no column to join on).
// `uniques` is the sum of the raw `first` flags, which the view route set
// from the daily hash at write time; no hash is needed here.
//
// WHERE THE WORK HAPPENS. Ranking, top-N and every fold (small cells, hard
// row caps, UTM allowlist, geography level) run in SQL, in D1, with window
// functions. The Worker receives only the capped output rows (at most
// ROW_CAP_* per table, a few hundred in total) and does no sorting or
// grouping of raw-sized data. This matters because a Free Worker has 10 ms of
// CPU per invocation. CPU cannot be measured in the test environment; see
// docs/deploy.md for the arithmetic of rows handled in JavaScript.
//
// Small cells (label suppression, NOT k-anonymity): every cell under MIN_CELL
// views (default 3) in a day folds into the 'other' value of its dimension
// (view_daily: source first, then country to 'ZZ'); geography folds city ->
// region -> country under max(5, MIN_CELL). Totals never change.
//
// QUERY BUDGET. Everything here goes through a metered database
// (src/d1budget.ts): a Free Worker may make 50 D1 queries per invocation and
// a db.batch() counts as ONE. A day therefore costs exactly 2 queries: one
// read batch (lease renewal, count, core, seven dimensions, geography) and one
// write batch that holds every delete, insert and the checkpoint. The write
// batch is a transaction, so a day is either completely written and sealed or
// not written at all; no stage marker is needed. Each day checks the budget
// before it starts, and the input size too (ROLLUP_MAX_ROWS_PER_DAY): what is
// left over waits for the next invocation.
//
// LEASE. A run takes a lease in view_meta ('rollup_lease' = token:expiry,
// 10 minutes) with an atomic upsert that only replaces an expired lease, and
// renews it in each day's read batch. Every write in the day's batch is
// guarded by "the lease token is mine AND its expiry has not passed" (a bound
// now, not SQL time) and by the day's state, so an overlapping run, or a run
// whose lease expired, writes nothing.
//
// GRACE AND LATE ROWS (full re-roll). A day is not rolled until now >= day end
// + GRACE (default 2 hours, ceiling 12). The log row records max_id, the
// highest raw id the aggregates count. A raw row of a sealed day with a larger
// id is a LATE row. Folding is not additive (a 3-view cell plus one late view,
// top-N order, caps), so late rows are never added as a delta: the whole day is
// RE-ROLLED from all its raw rows, and the day's aggregate rows and log row are
// replaced in one transaction (max_id advances, `rerolls` counts). Re-rolling
// is only safe while every originally counted raw row still exists; if the
// purge already took some (the day is older than the keep window), the late
// rows are DROPPED instead: max_id advances so they are not found again and
// they are counted in rollup_late_dropped, never in the aggregates.
//
// State is explicit: no log row = FRESH; a log row with raw rows above its
// max_id = RE-ROLL. Nothing keys off max_id being zero.
//
// Heartbeat (view_meta, no free text): rollup_last_ok (ms, success only),
// rollup_days, rollup_rows, rollup_late_rows (late rows re-rolled),
// rollup_late_dropped, rollup_reduced_days, rollup_err (closed class),
// rollup_failed_day (first failed day), rolled_through (last final day).

import { BudgetExceeded, QueryMeter, meterOf, meteredDb, queryBudget } from "./d1budget";

export const ROW_CAP_CORE = 500;
export const ROW_CAP_DIM = 300;
export const ROW_CAP_GEO = 300;
const CORE_RESERVE = 40;
const GEO_RESERVE = 8;
/** Days rolled per run at most; the query budget and the input size usually stop a run earlier. */
export const MAX_DAYS_PER_RUN = 14;
export const DEFAULT_TOP_SOURCES = 50;
export const REDUCED_TOP_SOURCES = 10;
export const DEFAULT_MIN_CELL = 3;
export const MAX_MIN_CELL = 50;
export const DEFAULT_GRACE_HOURS = 2;
export const MAX_GRACE_HOURS = 12;
export const DEFAULT_MAX_ROWS_PER_DAY = 3000;
export const MAX_MAX_ROWS_PER_DAY = 20000;
/** Input rows one invocation may roll in total, as a multiple of the per-day size. */
const INVOCATION_ROWS_FACTOR = 4;
export const LEASE_MS = 600_000;
const MAX_UTM_ALLOW = 40;
/** Values kept per dimension per bot class; the rest become 'other'. Closed enums fit entirely. */
export const DIM_TOP: Record<string, number> = {
  browser: 12,
  lang: 10,
  os: 10,
  device: 6,
  vp: 6,
  net: 10,
  reason: 20,
};
/** A city or region with fewer views than this in a day folds upward (raised by MIN_CELL if larger). */
const MIN_GEO_VIEWS = 5;
const ROWS_PER_INSERT = 10; // 9 columns x 10 rows + 4 guard values = 94 bound parameters, under D1's 100
const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;
/** Queries a day needs: one read batch and one write batch. */
const QUERIES_PER_DAY = 2;
/** Queries kept back for the final heartbeat batch. */
const RESERVE = 1;

export const ERROR_CLASSES = ["none", "mismatch", "db"] as const;
export type RollupError = (typeof ERROR_CLASSES)[number];

export interface RollupEnv {
  DB: D1Database;
  GEO_LEVEL?: string;
  TOP_SOURCES?: string;
  MIN_CELL?: string;
  ROLLUP_GRACE_HOURS?: string;
  ROLLUP_MAX_ROWS_PER_DAY?: string;
  UTM_ALLOWLIST?: string;
  QUERY_BUDGET?: string;
}

export interface RollupResult {
  days: number;
  rows: number;
  late: number;
  /** True when another run holds the lease and this one did nothing. */
  skipped?: boolean;
}

type Core = {
  page: string;
  src_kind: string;
  src: string;
  country: string;
  bot_class: string;
  views: number;
  uniques: number;
};
type Dim = { dim: string; value: string; bot_class: string; views: number; uniques: number };
type Geo = { country: string; region: string; city: string; bot_class: string; views: number; uniques: number };

function utcDate(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

function dayStartMs(day: string): number {
  return Date.parse(`${day}T00:00:00Z`);
}

function geoLevel(raw: string | undefined): "country" | "region" | "city" {
  const v = (raw ?? "").toLowerCase();
  return v === "country" || v === "region" ? v : "city";
}

function topSources(raw: string | undefined): number {
  if (typeof raw !== "string" || !/^\d{1,4}$/.test(raw)) return DEFAULT_TOP_SOURCES;
  const n = Number(raw);
  return n >= 1 ? n : DEFAULT_TOP_SOURCES;
}

export function minCell(raw: string | undefined): number {
  if (typeof raw !== "string" || !/^\d{1,3}$/.test(raw)) return DEFAULT_MIN_CELL;
  const n = Number(raw);
  if (n < 1) return DEFAULT_MIN_CELL;
  return Math.min(n, MAX_MIN_CELL);
}

export function graceMs(raw: string | undefined): number {
  if (typeof raw !== "string" || !/^\d{1,3}$/.test(raw)) return DEFAULT_GRACE_HOURS * HOUR_MS;
  return Math.min(Number(raw), MAX_GRACE_HOURS) * HOUR_MS;
}

export function maxRowsPerDay(raw: string | undefined): number {
  if (typeof raw !== "string" || !/^\d{1,6}$/.test(raw)) return DEFAULT_MAX_ROWS_PER_DAY;
  const n = Number(raw);
  if (n < 1) return DEFAULT_MAX_ROWS_PER_DAY;
  return Math.min(n, MAX_MAX_ROWS_PER_DAY);
}

/** UTM_ALLOWLIST: comma-separated lower-case labels; returns null when unset or empty. */
export function utmAllowlist(raw: string | undefined): string[] | null {
  if (typeof raw !== "string") return null;
  const items = raw
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter((s) => /^[a-z0-9-]{1,30}$/.test(s));
  return items.length === 0 ? null : [...new Set(items)].slice(0, MAX_UTM_ALLOW);
}

interface DayPlan {
  core: Core[];
  dim: Dim[];
  geo: Geo[];
  raw: number;
  views: number;
  uniques: number;
  /** Highest raw id in the day. */
  maxId: number;
  /** Raw rows of the day with id <= the logged max_id (re-roll only). */
  oldCount: number;
  reduced: boolean;
}

const DIM_COLUMNS = Object.keys(DIM_TOP);

class RollupMismatch extends Error {
  constructor() {
    super("mismatch");
  }
}
class LeaseLost extends Error {
  constructor() {
    super("lease lost");
  }
}

interface Work {
  day: string;
  /** Raw rows of the day (all ids). */
  n: number;
  /** Fresh day (no log row) or a re-roll of a sealed day that got late rows. */
  mode: "fresh" | "reroll";
  /** For a re-roll: the logged max_id and raw_views. */
  oldMax: number;
  oldViews: number;
}

interface Lease {
  token: string;
  /** Wall clock for guards: the run's start plus real elapsed time. */
  clock: () => number;
}

const tokenMatch = "substr(value, 1, instr(value, ':') - 1) = ?";
const expiryOk = "CAST(substr(value, instr(value, ':') + 1) AS INTEGER) >= ?";
/** The lease is mine and has not expired (needs the bound token and now). */
const LEASE_OK = `EXISTS (SELECT 1 FROM view_meta WHERE key = 'rollup_lease' AND ${tokenMatch} AND ${expiryOk})`;

/** SQL for one dimension: top-N per dimension, then small cells to 'other', all in D1. */
function dimSql(col: string, top: number): string {
  return (
    "WITH g AS (SELECT bot_class, " + col + " AS value, COUNT(*) AS v, SUM(first) AS u FROM view_events " +
    "WHERE ts >= ?1 AND ts < ?2 GROUP BY bot_class, " + col + "), " +
    "r AS (SELECT value, ROW_NUMBER() OVER (ORDER BY SUM(v) DESC, value ASC) AS rn FROM g GROUP BY value), " +
    "t AS (SELECT g.bot_class AS bot_class, CASE WHEN r.rn <= " + top + " THEN g.value ELSE 'other' END AS value, g.v AS v, g.u AS u " +
    "FROM g JOIN r ON r.value = g.value), " +
    "m AS (SELECT bot_class, value, SUM(v) AS v, SUM(u) AS u FROM t GROUP BY bot_class, value) " +
    // CONTROL:small-count-fold
    "SELECT '" + col + "' AS dim, CASE WHEN v < ?3 AND value <> 'other' THEN 'other' ELSE value END AS value, bot_class, " +
    "SUM(v) AS views, SUM(u) AS uniques FROM m GROUP BY 2, bot_class"
  );
}

/** SQL for the core table; the UTM allowlist (when set) binds ?10 and up. */
function coreSql(allowN: number): string {
  const allow = Array.from({ length: allowN }, (_, i) => `?${10 + i}`).join(",");
  const sk = allowN > 0 ? `CASE WHEN src_kind = 'u' AND src NOT IN (${allow}) THEN 'o' ELSE src_kind END` : "src_kind";
  const sv = allowN > 0 ? `CASE WHEN src_kind = 'u' AND src NOT IN (${allow}) THEN 'other' ELSE src END` : "src";
  return (
    `WITH g AS (SELECT page, ${sk} AS src_kind, ${sv} AS src, country, bot_class, COUNT(*) AS v, SUM(first) AS u ` +
    "FROM view_events WHERE ts >= ?1 AND ts < ?2 GROUP BY 1, 2, 3, country, bot_class), " +
    "r AS (SELECT src_kind, src, ROW_NUMBER() OVER (ORDER BY SUM(v) DESC, src_kind ASC, src ASC) AS rn FROM g " +
    "WHERE src_kind IN ('r','u') GROUP BY src_kind, src), " +
    "a AS (SELECT g.page AS page, CASE WHEN r.rn > ?4 THEN 'o' ELSE g.src_kind END AS src_kind, " +
    "CASE WHEN r.rn > ?4 THEN 'other' ELSE g.src END AS src, g.country AS country, g.bot_class AS bot_class, g.v AS v, g.u AS u " +
    "FROM g LEFT JOIN r ON r.src_kind = g.src_kind AND r.src = g.src), " +
    "am AS (SELECT page, src_kind, src, country, bot_class, SUM(v) AS v, SUM(u) AS u FROM a GROUP BY 1, 2, 3, 4, 5), " +
    // CONTROL:small-count-fold  a small source cell loses its label first, then its country.
    "b1 AS (SELECT page, CASE WHEN v < ?3 AND src_kind IN ('r','u') THEN 'o' ELSE src_kind END AS src_kind, " +
    "CASE WHEN v < ?3 AND src_kind IN ('r','u') THEN 'other' ELSE src END AS src, country, bot_class, v, u FROM am), " +
    "b1m AS (SELECT page, src_kind, src, country, bot_class, SUM(v) AS v, SUM(u) AS u FROM b1 GROUP BY 1, 2, 3, 4, 5), " +
    "b2 AS (SELECT page, src_kind, src, CASE WHEN v < ?3 AND country <> 'ZZ' THEN 'ZZ' ELSE country END AS country, bot_class, v, u FROM b1m), " +
    "b2m AS (SELECT page, src_kind, src, country, bot_class, SUM(v) AS v, SUM(u) AS u FROM b2 GROUP BY 1, 2, 3, 4, 5), " +
    "k AS (SELECT page, src_kind, src, country, bot_class, v, u, " +
    "ROW_NUMBER() OVER (ORDER BY v DESC, page, src_kind, src, country, bot_class) AS rn, (SELECT COUNT(*) FROM b2m) AS total FROM b2m) " +
    // The overflow rows lose their page too, so they collapse to one row per bot class and the cap is hard.
    `SELECT CASE WHEN total > ${ROW_CAP_CORE} AND rn > ${ROW_CAP_CORE - CORE_RESERVE} THEN 'other' ELSE page END AS page, ` +
    `CASE WHEN total > ${ROW_CAP_CORE} AND rn > ${ROW_CAP_CORE - CORE_RESERVE} THEN 'o' ELSE src_kind END AS src_kind, ` +
    `CASE WHEN total > ${ROW_CAP_CORE} AND rn > ${ROW_CAP_CORE - CORE_RESERVE} THEN 'other' ELSE src END AS src, ` +
    `CASE WHEN total > ${ROW_CAP_CORE} AND rn > ${ROW_CAP_CORE - CORE_RESERVE} THEN 'ZZ' ELSE country END AS country, bot_class, ` +
    "SUM(v) AS views, SUM(u) AS uniques FROM k GROUP BY 1, 2, 3, 4, 5"
  );
}

/** SQL for geography at a level; city under ?5 folds to region, region under ?5 to country. */
function geoSql(level: "country" | "region" | "city"): string {
  const city = level === "city" ? "city" : "''";
  const region = level === "country" ? "''" : "region";
  return (
    `WITH g AS (SELECT country, ${region} AS region, ${city} AS city, bot_class, COUNT(*) AS v, SUM(first) AS u ` +
    "FROM view_events WHERE ts >= ?1 AND ts < ?2 GROUP BY country, 2, 3, bot_class), " +
    // CONTROL:small-count-fold
    "f1 AS (SELECT country, region, CASE WHEN city <> '' AND v < ?5 THEN '' ELSE city END AS city, bot_class, SUM(v) AS v, SUM(u) AS u " +
    "FROM g GROUP BY country, region, 3, bot_class), " +
    "f2 AS (SELECT country, CASE WHEN city = '' AND region <> '' AND v < ?5 THEN '' ELSE region END AS region, city, bot_class, " +
    "SUM(v) AS v, SUM(u) AS u FROM f1 GROUP BY country, 2, city, bot_class), " +
    "k AS (SELECT country, region, city, bot_class, v, u, ROW_NUMBER() OVER (ORDER BY v DESC, country, region, city, bot_class) AS rn, " +
    "(SELECT COUNT(*) FROM f2) AS total FROM f2) " +
    `SELECT CASE WHEN total > ${ROW_CAP_GEO} AND rn > ${ROW_CAP_GEO - GEO_RESERVE} THEN 'ZZ' ELSE country END AS country, ` +
    `CASE WHEN total > ${ROW_CAP_GEO} AND rn > ${ROW_CAP_GEO - GEO_RESERVE} THEN '' ELSE region END AS region, ` +
    `CASE WHEN total > ${ROW_CAP_GEO} AND rn > ${ROW_CAP_GEO - GEO_RESERVE} THEN '' ELSE city END AS city, bot_class, ` +
    "SUM(v) AS views, SUM(u) AS uniques FROM k GROUP BY 1, 2, 3, 4"
  );
}

/**
 * ONE read batch (one query) for a day: renew the lease, then count, core, the
 * seven dimensions and geography of ALL the day's raw rows. Ranking and folding
 * happen in D1; this function only checks that the capped output adds up.
 */
async function aggregateDay(env: RollupEnv, db: D1Database, w: Work, lease: Lease, meter: QueryMeter): Promise<DayPlan> {
  // The discovery count is only a first guess: rows can arrive before the read.
  // The read batch recounts, and if the recount puts the day on the other side
  // of ROLLUP_MAX_ROWS_PER_DAY the read is repeated once in the right mode.
  const limit = maxRowsPerDay(env.ROLLUP_MAX_ROWS_PER_DAY);
  let reduced = w.n > limit;
  for (let attempt = 0; ; attempt++) {
    const plan = await readDay(env, db, w, lease, reduced);
    const should = plan.raw > limit;
    if (should === reduced || attempt === 1) return plan;
    reduced = should;
    if (meter.remaining() < QUERIES_PER_DAY + RESERVE) throw new BudgetExceeded();
  }
}

async function readDay(env: RollupEnv, db: D1Database, w: Work, lease: Lease, reduced: boolean): Promise<DayPlan> {
  const lo = dayStartMs(w.day);
  const hi = lo + DAY_MS;
  const level = reduced ? "country" : geoLevel(env.GEO_LEVEL);
  const top = reduced ? Math.min(REDUCED_TOP_SOURCES, topSources(env.TOP_SOURCES)) : topSources(env.TOP_SOURCES);
  const min = minCell(env.MIN_CELL);
  const allow = utmAllowlist(env.UTM_ALLOWLIST) ?? [];
  const now = lease.clock();
  const geoMin = Math.max(MIN_GEO_VIEWS, min);
  const coreArgs = allow.length > 0 ? [lo, hi, min, top, 0, 0, 0, 0, 0, ...allow] : [lo, hi, min, top];
  const res = await db.batch([
    db
      .prepare(
        "UPDATE view_meta SET value = ? WHERE key = 'rollup_lease' AND " +
          `${tokenMatch} AND ${expiryOk}`,
      )
      .bind(`${lease.token}:${now + LEASE_MS}`, lease.token, now),
    db
      .prepare(
        "SELECT COUNT(*) AS n, COALESCE(MAX(id), 0) AS max_id, COALESCE(SUM(CASE WHEN id <= ?3 THEN 1 ELSE 0 END), 0) AS old_n " +
          "FROM view_events WHERE ts >= ?1 AND ts < ?2",
      )
      .bind(lo, hi, w.oldMax),
    db.prepare(coreSql(allow.length)).bind(...coreArgs),
    ...DIM_COLUMNS.map((c) => db.prepare(dimSql(c, DIM_TOP[c] as number)).bind(lo, hi, min)),
    db.prepare(geoSql(level)).bind(lo, hi, 0, 0, geoMin),
  ]);
  if ((res[0]?.meta?.changes ?? 0) !== 1) throw new LeaseLost();
  const head = (res[1]?.results ?? [])[0] as { n: number; max_id: number; old_n: number } | undefined;
  const rawCount = head?.n ?? 0;
  const core = (res[2]?.results ?? []) as Core[];
  const dim: Dim[] = [];
  for (let i = 0; i < DIM_COLUMNS.length; i++) dim.push(...((res[3 + i]?.results ?? []) as Dim[]));
  const geo = (res[3 + DIM_COLUMNS.length]?.results ?? []) as Geo[];

  const sum = (rows: Array<{ views: number; uniques: number }>) =>
    rows.reduce((a, r) => [a[0] + r.views, a[1] + r.uniques], [0, 0]);
  const [vc, uc] = sum(core);
  const [vg, ug] = sum(geo);
  // Every raw row must appear exactly once in each table.
  const dimOk = DIM_COLUMNS.every((c) => sum(dim.filter((r) => r.dim === c))[0] === rawCount);
  if (vc !== rawCount || vg !== rawCount || !dimOk || uc !== ug) throw new RollupMismatch();
  return { core, dim, geo, raw: rawCount, views: vc, uniques: uc, maxId: head?.max_id ?? 0, oldCount: head?.old_n ?? 0, reduced };
}

/** The lease is mine and live, and the day is in the state this run read. */
interface Guard {
  sql: string;
  args: Array<string | number>;
}

function guardFor(lease: Lease, w: Work, now: number): Guard {
  return w.mode === "fresh"
    ? { sql: `${LEASE_OK} AND NOT EXISTS (SELECT 1 FROM view_rollup_log WHERE day = ?)`, args: [lease.token, now, w.day] }
    : {
        sql: `${LEASE_OK} AND EXISTS (SELECT 1 FROM view_rollup_log WHERE day = ? AND max_id = ?)`,
        args: [lease.token, now, w.day, w.oldMax],
      };
}

function insertStatements(
  db: D1Database,
  table: string,
  cols: string[],
  rows: Array<Array<string | number>>,
  guard: Guard,
): D1PreparedStatement[] {
  const out: D1PreparedStatement[] = [];
  for (let i = 0; i < rows.length; i += ROWS_PER_INSERT) {
    const chunk = rows.slice(i, i + ROWS_PER_INSERT);
    const ph = `(${cols.map(() => "?").join(",")})`;
    const sel = cols.map((_, k) => `column${k + 1}`).join(",");
    out.push(
      db
        .prepare(`INSERT INTO ${table} (${cols.join(",")}) SELECT ${sel} FROM (VALUES ${chunk.map(() => ph).join(",")}) WHERE ${guard.sql}`)
        .bind(...chunk.flat(), ...guard.args),
    );
  }
  return out;
}

/**
 * One transaction: for a fresh day, delete any leftovers, insert the
 * aggregates and the log row; for a re-roll, replace the day's aggregates and
 * update the log row; every statement guarded by the lease and the day's state.
 * `drop` skips the aggregates and only advances max_id (late rows that cannot
 * be re-rolled because the purge already took part of the day).
 */
async function writeDay(db: D1Database, w: Work, p: DayPlan, nowMs: number, lease: Lease, drop: boolean): Promise<number> {
  // ONE clock reading for every guard and the checkpoint of this batch: the
  // statements must all pass or all fail the lease-expiry test together, never
  // insert rows and then fail the checkpoint because time moved in between.
  const leaseNow = lease.clock();
  const guard = guardFor(lease, w, leaseNow);
  const tz = "UTC";
  const stmts: D1PreparedStatement[] = [];
  if (!drop) {
    for (const t of ["view_daily", "view_dim_daily", "view_geo_daily"]) {
      stmts.push(db.prepare(`DELETE FROM ${t} WHERE day = ? AND ${guard.sql}`).bind(w.day, ...guard.args));
    }
    stmts.push(
      ...insertStatements(
        db,
        "view_daily",
        ["day", "day_tz", "page", "src_kind", "src", "country", "bot_class", "views", "uniques"],
        p.core.map((r) => [w.day, tz, r.page, r.src_kind, r.src, r.country, r.bot_class, r.views, r.uniques]),
        guard,
      ),
      ...insertStatements(
        db,
        "view_dim_daily",
        ["day", "day_tz", "dim", "value", "bot_class", "views", "uniques"],
        p.dim.map((r) => [w.day, tz, r.dim, r.value, r.bot_class, r.views, r.uniques]),
        guard,
      ),
      ...insertStatements(
        db,
        "view_geo_daily",
        ["day", "day_tz", "country", "region", "city", "bot_class", "views", "uniques"],
        p.geo.map((r) => [w.day, tz, r.country, r.region, r.city, r.bot_class, r.views, r.uniques]),
        guard,
      ),
    );
  }
  // CONTROL:rollup-seal  the checkpoint is the last statement of the same transaction.
  if (w.mode === "fresh") {
    stmts.push(
      db
        .prepare(
          "INSERT INTO view_rollup_log (day, day_tz, raw_views, views, uniques, rows_core, rows_dim, rows_geo, rolled_at, max_id, rerolls) " +
            `SELECT ?,?,?,?,?,?,?,?,?,?,0 WHERE ${guard.sql}`,
        )
        .bind(w.day, tz, p.raw, p.views, p.uniques, p.core.length, p.dim.length, p.geo.length, nowMs, p.maxId, ...guard.args),
    );
  } else if (drop) {
    stmts.push(
      db
        .prepare(`UPDATE view_rollup_log SET max_id = ?, rolled_at = ? WHERE day = ? AND max_id = ? AND ${LEASE_OK}`)
        .bind(p.maxId, nowMs, w.day, w.oldMax, lease.token, leaseNow),
    );
  } else {
    stmts.push(
      db
        .prepare(
          "UPDATE view_rollup_log SET raw_views = ?, views = ?, uniques = ?, rows_core = ?, rows_dim = ?, rows_geo = ?, " +
            `rolled_at = ?, max_id = ?, rerolls = rerolls + 1 WHERE day = ? AND max_id = ? AND ${LEASE_OK}`,
        )
        .bind(p.raw, p.views, p.uniques, p.core.length, p.dim.length, p.geo.length, nowMs, p.maxId, w.day, w.oldMax, lease.token, leaseNow),
    );
  }
  if (stmts.length > 2000) throw new RangeError("a day must fit one batch"); // caps keep it near 100
  const res = await db.batch(stmts);
  const sealed = res[res.length - 1]?.meta?.changes ?? 0;
  if (sealed !== 1) throw new LeaseLost();
  return drop ? 1 : p.core.length + p.dim.length + p.geo.length + 1;
}

function heartbeatStatements(db: D1Database, entries: Record<string, string>, token: string | null, clearFailed: boolean) {
  const keys = Object.keys(entries);
  const stmts = [
    db
      .prepare(
        `INSERT INTO view_meta (key, value) VALUES ${keys.map(() => "(?,?)").join(",")} ` +
          "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
      )
      .bind(...keys.flatMap((k) => [k, entries[k] as string])),
  ];
  if (clearFailed) stmts.push(db.prepare("DELETE FROM view_meta WHERE key = 'rollup_failed_day'"));
  if (token !== null) {
    stmts.push(db.prepare(`DELETE FROM view_meta WHERE key = 'rollup_lease' AND ${tokenMatch}`).bind(token));
  }
  return stmts;
}

/**
 * Roll every closed UTC day (strictly before today, after the grace period)
 * that has raw rows and no log row, and re-roll sealed days that got late rows.
 * Records the heartbeat and rethrows a failure after recording its class.
 * Never makes more D1 queries than the invocation budget, and never takes on
 * more input rows than the per-invocation row budget.
 */
export async function rollupClosedDays(env: RollupEnv, nowMs: number): Promise<RollupResult> {
  if (!Number.isFinite(nowMs)) throw new RangeError("nowMs must be a finite number");
  const outer = meterOf(env.DB);
  const meter = outer ? outer : new QueryMeter(queryBudget(env.QUERY_BUDGET));
  const db = outer ? env.DB : meteredDb(env.DB, meter);
  const t0 = Date.now();
  const clock = () => nowMs + (Date.now() - t0);
  const eligibleEnd = nowMs - graceMs(env.ROLLUP_GRACE_HOURS);
  const todayStart = dayStartMs(utcDate(eligibleEnd));
  const rowBudget = maxRowsPerDay(env.ROLLUP_MAX_ROWS_PER_DAY) * INVOCATION_ROWS_FACTOR;
  let days = 0;
  let rows = 0;
  let late = 0;
  let dropped = 0;
  let reducedDays = 0;
  let inputRows = 0;
  let failure: unknown = null;
  let errClass: RollupError = "none";
  let failedDay = "";
  let lastGood = "";
  let complete = false;
  let lost = false;
  let work: Work[] = [];

  // One read batch (one query): days to roll, and sealed days that got late rows.
  try {
    const found = await db.batch([
      db
        .prepare(
          "SELECT strftime('%Y-%m-%d', ts / 1000, 'unixepoch') AS d, COUNT(*) AS n FROM view_events WHERE ts < ?1 " +
            "AND strftime('%Y-%m-%d', ts / 1000, 'unixepoch') NOT IN (SELECT day FROM view_rollup_log) GROUP BY 1 ORDER BY 1 LIMIT ?2",
        )
        .bind(todayStart, MAX_DAYS_PER_RUN + 1),
      db
        .prepare(
          "SELECT l.day AS d, l.max_id AS m, l.raw_views AS rv, " +
            "(SELECT COUNT(*) FROM view_events x WHERE x.ts >= (strftime('%s', l.day) * 1000) AND x.ts < (strftime('%s', l.day) * 1000 + 86400000)) AS n " +
            "FROM view_rollup_log l WHERE EXISTS (SELECT 1 FROM view_events e WHERE e.ts >= (strftime('%s', l.day) * 1000) " +
            "AND e.ts < (strftime('%s', l.day) * 1000 + 86400000) AND e.id > l.max_id) ORDER BY l.day LIMIT ?1",
        )
        .bind(MAX_DAYS_PER_RUN),
    ]);
    const fresh: Work[] = ((found[0]?.results ?? []) as Array<{ d: string; n: number }>).map((r) => ({
      day: r.d,
      n: r.n,
      mode: "fresh",
      oldMax: 0,
      oldViews: 0,
    }));
    const redo: Work[] = ((found[1]?.results ?? []) as Array<{ d: string; m: number; rv: number; n: number }>).map((r) => ({
      day: r.d,
      n: r.n,
      mode: "reroll",
      oldMax: r.m,
      oldViews: r.rv,
    }));
    const all = [...redo, ...fresh.slice(0, MAX_DAYS_PER_RUN)];
    work = all.slice(0, MAX_DAYS_PER_RUN);
    complete = fresh.length <= MAX_DAYS_PER_RUN && redo.length < MAX_DAYS_PER_RUN && all.length <= MAX_DAYS_PER_RUN;
  } catch (e) {
    failure = e;
    errClass = "db";
  }

  let lease: Lease | null = null;
  let skipped = false;
  if (failure === null && work.length > 0) {
    const token = crypto.randomUUID();
    try {
      const acq = await db
        .prepare(
          "INSERT INTO view_meta (key, value) VALUES ('rollup_lease', ?1) ON CONFLICT(key) DO UPDATE SET value = excluded.value " +
            "WHERE CAST(substr(value, instr(value, ':') + 1) AS INTEGER) < ?2",
        )
        .bind(`${token}:${nowMs + LEASE_MS}`, nowMs)
        .run();
      if (acq.meta.changes === 1) lease = { token, clock };
      else skipped = true;
    } catch (e) {
      failure = e;
      errClass = "db";
    }
  }
  if (skipped) return { days: 0, rows: 0, late: 0, skipped: true };

  if (lease !== null) {
    for (const w of work) {
      if (meter.remaining() < QUERIES_PER_DAY + RESERVE) {
        complete = false;
        break;
      }
      // The input-size budget: never start a day that would push this
      // invocation past it (an oversized first day still runs alone, reduced).
      if (inputRows > 0 && inputRows + w.n > rowBudget) {
        complete = false;
        break;
      }
      try {
        const plan = await aggregateDay(env, db, w, lease, meter);
        // The read batch recounted the day: budget on that count, not on the
        // pre-lease discovery count, which rows arriving in between can outgrow.
        if (inputRows > 0 && inputRows + plan.raw > rowBudget) {
          complete = false;
          break;
        }
        inputRows += plan.raw;
        if (w.mode === "reroll") {
          const lateRows = plan.raw - plan.oldCount;
          if (lateRows <= 0) continue;
          // Re-roll only while every originally counted raw row still exists.
          const safe = plan.oldCount === w.oldViews;
          rows += await writeDay(db, w, plan, nowMs, lease, !safe);
          if (safe) late += lateRows;
          else dropped += lateRows;
        } else {
          rows += await writeDay(db, w, plan, nowMs, lease, false);
          days += 1;
          if (plan.reduced) reducedDays += 1;
          if (failedDay === "") lastGood = w.day;
        }
      } catch (e) {
        if (e instanceof LeaseLost) {
          // Another run owns this work now, or the lease ran out, or the day
          // changed under us. Stop, but go through the cleanup below: the
          // token-guarded release is safe if the lease is no longer ours.
          lost = true;
          complete = false;
          break;
        }
        if (e instanceof BudgetExceeded) {
          complete = false;
          break;
        }
        failure ??= e;
        if (failedDay === "") {
          failedDay = w.day;
          errClass = e instanceof RollupMismatch ? "mismatch" : "db";
        }
      }
    }
  }

  if (lost && lease !== null) {
    // The heartbeat belongs to whoever owns the work now. Only release a lease
    // that is still ours (token-guarded, so a no-op if someone else holds it).
    try {
      await db.prepare(`DELETE FROM view_meta WHERE key = 'rollup_lease' AND ${tokenMatch}`).bind(lease.token).run();
    } catch (e) {
      failure ??= e;
    }
    if (failure !== null) throw failure;
    return { days, rows, late };
  }

  const through = failure === null && complete ? utcDate(todayStart - DAY_MS) : lastGood;
  const entries: Record<string, string> = {
    rollup_days: String(days),
    rollup_rows: String(rows),
    rollup_late_rows: String(late),
    rollup_late_dropped: String(dropped),
    rollup_reduced_days: String(reducedDays),
    rollup_err: errClass,
  };
  if (through !== "") entries.rolled_through = through;
  if (failedDay !== "") entries.rollup_failed_day = failedDay;
  if (failure === null) entries.rollup_last_ok = String(Math.floor(nowMs));
  try {
    await db.batch(heartbeatStatements(db, entries, lease?.token ?? null, failure === null));
  } catch (e) {
    failure ??= e;
  }
  if (failure !== null) throw failure;
  return { days, rows, late };
}
