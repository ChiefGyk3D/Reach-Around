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
// Small cells: every cell under MIN_CELL views (default 3) in a day folds into
// the 'other' value of its dimension (view_daily: source first, then country
// to 'ZZ'); geography folds city -> region -> country under max(5, MIN_CELL).
// Totals never change.
//
// QUERY BUDGET. Everything here goes through a metered database
// (src/d1budget.ts): a Free Worker may make 50 D1 queries per invocation and
// a db.batch() counts as ONE. A day therefore costs exactly 2 queries: one
// read batch (count, core, seven dimensions, geography) and one write batch
// that holds every delete, insert and the checkpoint. The write batch is a
// transaction, so a day is either completely written and sealed or not
// written at all; there is no half-sealed day and no stage marker is needed.
// Each day checks the budget before it starts; what is left over waits for
// the next invocation (the hourly cron), and the next invocation reads the
// same raw rows, so repeated bounded runs converge on the same aggregates as
// one unbounded run.
//
// LEASE. A run takes a lease in view_meta ('rollup_lease' = token:expiry,
// 10 minutes) with an atomic upsert that only replaces an expired lease. Every
// write in the day's batch is guarded by "the lease still equals my token" and
// by the day's state (no log row yet, or the log row's max_id still what I
// read), so an overlapping run, or a run whose lease expired, writes nothing.
// The lease is released in the final heartbeat batch.
//
// GRACE AND LATE ROWS. A day is not rolled until now >= day end + GRACE
// (default 2 hours, ceiling 12), because a view that began before midnight can
// be written after it. The log row records max_id, the highest raw id the
// aggregates count. A row of an already sealed day with id > max_id (a late
// row) is folded into that same day by a SUPPLEMENT: its aggregates are added
// with ON CONFLICT DO UPDATE and max_id advances in the same transaction, so
// replaying never counts a row twice, and the raw purge may delete only
// id <= max_id. rollup_late_rows in the heartbeat counts folded rows.
//
// Heartbeat (view_meta, no free text): rollup_last_ok (ms, success only),
// rollup_days, rollup_rows, rollup_late_rows (this run), rollup_err (closed
// class), rollup_failed_day (first failed day), rolled_through (last final day).

import { BudgetExceeded, QueryMeter, meterOf, meteredDb, queryBudget } from "./d1budget";

export const ROW_CAP_CORE = 1500;
export const ROW_CAP_DIM = 600;
export const ROW_CAP_GEO = 1500;
/** Days rolled per run at most; the query budget usually stops a run earlier. */
export const MAX_DAYS_PER_RUN = 14;
export const DEFAULT_TOP_SOURCES = 50;
export const DEFAULT_MIN_CELL = 3;
export const MAX_MIN_CELL = 50;
export const DEFAULT_GRACE_HOURS = 2;
export const MAX_GRACE_HOURS = 12;
export const LEASE_MS = 600_000;
const TOP_LANG = 15;
const TOP_BROWSER = 25;
/** A city or region with fewer views than this in a day folds upward (raised by MIN_CELL if larger). */
const MIN_GEO_VIEWS = 5;
const ROWS_PER_INSERT = 10; // 9 columns x 10 rows + 3 guard values = 93 bound parameters, under D1's 100
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

function byViewsThenName(a: { views: number }, b: { views: number }, ka: string, kb: string): number {
  return b.views - a.views || (ka < kb ? -1 : ka > kb ? 1 : 0);
}

/** Merge rows with an identical key, summing views and uniques. */
function merge<T extends { views: number; uniques: number }>(rows: T[], key: (r: T) => string): T[] {
  const m = new Map<string, T>();
  for (const r of rows) {
    const k = key(r);
    const cur = m.get(k);
    if (cur) {
      cur.views += r.views;
      cur.uniques += r.uniques;
    } else m.set(k, { ...r });
  }
  return [...m.values()];
}

const coreKey = (r: Core) => [r.page, r.src_kind, r.src, r.country, r.bot_class].join("\u0000");
const dimKey = (r: Dim) => [r.dim, r.value, r.bot_class].join("\u0000");
const geoKey = (r: Geo) => [r.country, r.region, r.city, r.bot_class].join("\u0000");

/** Sources of kind r and u share a top-N by views; the rest fold to ('o','other'). Direct and internal never fold. */
export function capSources(rows: Core[], top: number): Core[] {
  const tot = new Map<string, number>();
  for (const r of rows) {
    if (r.src_kind === "r" || r.src_kind === "u") {
      const k = `${r.src_kind}\u0000${r.src}`;
      tot.set(k, (tot.get(k) ?? 0) + r.views);
    }
  }
  const keep = new Set(
    [...tot.entries()]
      .sort((a, b) => byViewsThenName({ views: a[1] }, { views: b[1] }, a[0], b[0]))
      .slice(0, top)
      .map(([k]) => k),
  );
  return merge(
    rows.map((r) =>
      (r.src_kind === "r" || r.src_kind === "u") && !keep.has(`${r.src_kind}\u0000${r.src}`)
        ? { ...r, src_kind: "o", src: "other" }
        : r,
    ),
    coreKey,
  );
}

/** Keep the top-N values of one dimension (by views, ties by name); the rest become 'other'. */
export function capDimValues(rows: Dim[], dim: string, top: number): Dim[] {
  const tot = new Map<string, number>();
  for (const r of rows) if (r.dim === dim) tot.set(r.value, (tot.get(r.value) ?? 0) + r.views);
  const keep = new Set(
    [...tot.entries()]
      .sort((a, b) => byViewsThenName({ views: a[1] }, { views: b[1] }, a[0], b[0]))
      .slice(0, top)
      .map(([v]) => v),
  );
  return merge(
    rows.map((r) => (r.dim === dim && !keep.has(r.value) ? { ...r, value: "other" } : r)),
    dimKey,
  );
}

/** Every dimension cell under `min` views folds into the 'other' value of its dimension. */
export function minCellDims(rows: Dim[], min: number): Dim[] {
  // CONTROL:small-count-fold
  return merge(
    rows.map((r) => (r.views < min && r.value !== "other" ? { ...r, value: "other" } : r)),
    dimKey,
  );
}

/**
 * Every page x source x country x class cell under `min` views folds: a
 * referrer or utm label first into ('o','other'), then the country into 'ZZ'.
 * Direct and internal visits only lose their country.
 */
export function minCellCore(rows: Core[], min: number): Core[] {
  // CONTROL:small-count-fold
  const a = merge(
    rows.map((r) =>
      r.views < min && (r.src_kind === "r" || r.src_kind === "u") ? { ...r, src_kind: "o", src: "other" } : r,
    ),
    coreKey,
  );
  return merge(a.map((r) => (r.views < min && r.country !== "ZZ" ? { ...r, country: "ZZ" } : r)), coreKey);
}

/** Hard row cap: keep the biggest rows, fold the rest with `fold` (which must land in a small closed set). */
function hardCap<T extends { views: number; uniques: number }>(
  rows: T[],
  cap: number,
  key: (r: T) => string,
  fold: (r: T) => T,
  reserve: number,
): T[] {
  if (rows.length <= cap) return rows;
  const sorted = [...rows].sort((a, b) => byViewsThenName(a, b, key(a), key(b)));
  const keepN = Math.max(1, cap - reserve);
  return merge([...sorted.slice(0, keepN), ...sorted.slice(keepN).map(fold)], key);
}

/** City under `min` views folds into its region, a region under it into its country. */
export function foldGeo(rows: Geo[], min: number = MIN_GEO_VIEWS): Geo[] {
  // CONTROL:small-count-fold
  const a = merge(
    rows.map((r) => (r.city !== "" && r.views < min ? { ...r, city: "" } : r)),
    geoKey,
  );
  return merge(
    a.map((r) => (r.city === "" && r.region !== "" && r.views < min ? { ...r, region: "" } : r)),
    geoKey,
  );
}

interface DayPlan {
  core: Core[];
  dim: Dim[];
  geo: Geo[];
  raw: number;
  views: number;
  uniques: number;
  /** Highest raw id counted. */
  maxId: number;
}

const DIM_COLUMNS = ["browser", "os", "device", "lang", "vp", "net", "reason"] as const;

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

/** ONE read batch (one query): count, core, the seven dimensions and geography of a day's rows with id > afterId. */
async function aggregateDay(env: RollupEnv, db: D1Database, day: string, afterId: number): Promise<DayPlan> {
  const lo = dayStartMs(day);
  const hi = lo + DAY_MS;
  const level = geoLevel(env.GEO_LEVEL);
  const min = minCell(env.MIN_CELL);
  const where = "FROM view_events WHERE ts >= ?1 AND ts < ?2 AND id > ?3";
  const b = (sql: string) => db.prepare(sql).bind(lo, hi, afterId);
  const cityExpr = level === "city" ? "city" : "''";
  const regionExpr = level === "country" ? "''" : "region";
  const res = await db.batch([
    b(`SELECT COUNT(*) AS n, COALESCE(MAX(id), 0) AS max_id ${where}`),
    b(
      `SELECT page, src_kind, src, country, bot_class, COUNT(*) AS views, SUM(first) AS uniques ${where} ` +
        "GROUP BY page, src_kind, src, country, bot_class",
    ),
    ...DIM_COLUMNS.map((c) =>
      b(`SELECT '${c}' AS dim, ${c} AS value, bot_class, COUNT(*) AS views, SUM(first) AS uniques ${where} GROUP BY bot_class, ${c}`),
    ),
    b(
      `SELECT country, ${regionExpr} AS region, ${cityExpr} AS city, bot_class, COUNT(*) AS views, SUM(first) AS uniques ${where} ` +
        "GROUP BY country, 2, 3, bot_class",
    ),
  ]);
  const head = (res[0]?.results ?? [])[0] as { n: number; max_id: number } | undefined;
  const rawCount = head?.n ?? 0;
  const maxId = head?.max_id ?? 0;

  let core = merge((res[1]?.results ?? []) as Core[], coreKey);
  core = capSources(core, topSources(env.TOP_SOURCES));
  core = minCellCore(core, min);
  core = hardCap(core, ROW_CAP_CORE, coreKey, (r) => ({ ...r, src_kind: "o", src: "other", country: "ZZ" }), 40);

  let dim: Dim[] = [];
  for (let i = 0; i < DIM_COLUMNS.length; i++) dim.push(...((res[2 + i]?.results ?? []) as Dim[]));
  dim = capDimValues(capDimValues(dim, "lang", TOP_LANG), "browser", TOP_BROWSER);
  dim = minCellDims(dim, min);
  dim = hardCap(dim, ROW_CAP_DIM, dimKey, (r) => ({ ...r, value: "other" }), 40);

  let geo = merge((res[2 + DIM_COLUMNS.length]?.results ?? []) as Geo[], geoKey);
  geo = foldGeo(geo, Math.max(MIN_GEO_VIEWS, min));
  geo = hardCap(geo, ROW_CAP_GEO, geoKey, (r) => ({ ...r, country: "ZZ", region: "", city: "" }), 8);

  const sum = (rows: Array<{ views: number; uniques: number }>) =>
    rows.reduce((a, r) => [a[0] + r.views, a[1] + r.uniques], [0, 0]);
  const [vc, uc] = sum(core);
  const [vg, ug] = sum(geo);
  // Every raw row must appear exactly once in each table.
  const dimOk = DIM_COLUMNS.every((c) => sum(dim.filter((r) => r.dim === c))[0] === rawCount);
  if (vc !== rawCount || vg !== rawCount || !dimOk || uc !== ug) throw new RollupMismatch();
  return { core, dim, geo, raw: rawCount, views: vc, uniques: uc, maxId };
}

/** The lease token still held, and the day in the expected state. */
interface Guard {
  sql: string;
  args: Array<string | number>;
}

function guardFor(lease: string, day: string, afterId: number): Guard {
  const held = "EXISTS (SELECT 1 FROM view_meta WHERE key = 'rollup_lease' AND value = ?)";
  return afterId === 0
    ? { sql: `${held} AND NOT EXISTS (SELECT 1 FROM view_rollup_log WHERE day = ?)`, args: [lease, day] }
    : {
        sql: `${held} AND EXISTS (SELECT 1 FROM view_rollup_log WHERE day = ? AND max_id = ?)`,
        args: [lease, day, afterId],
      };
}

function insertStatements(
  db: D1Database,
  table: string,
  cols: string[],
  conflictKey: string[],
  rows: Array<Array<string | number>>,
  guard: Guard,
  supplement: boolean,
): D1PreparedStatement[] {
  const out: D1PreparedStatement[] = [];
  for (let i = 0; i < rows.length; i += ROWS_PER_INSERT) {
    const chunk = rows.slice(i, i + ROWS_PER_INSERT);
    const ph = `(${cols.map(() => "?").join(",")})`;
    const sel = cols.map((_, k) => `column${k + 1}`).join(",");
    const upsert = supplement
      ? ` ON CONFLICT(${conflictKey.join(",")}) DO UPDATE SET views = views + excluded.views, uniques = uniques + excluded.uniques`
      : "";
    out.push(
      db
        .prepare(
          `INSERT INTO ${table} (${cols.join(",")}) SELECT ${sel} FROM (VALUES ${chunk.map(() => ph).join(",")}) ` +
            `WHERE ${guard.sql}${upsert}`,
        )
        .bind(...chunk.flat(), ...guard.args),
    );
  }
  return out;
}

/** One transaction: every delete, insert and the checkpoint of a day, each guarded by the lease and the day's state. */
async function writeDay(
  db: D1Database,
  day: string,
  p: DayPlan,
  nowMs: number,
  lease: string,
  afterId: number,
): Promise<number> {
  const supplement = afterId !== 0;
  const guard = guardFor(lease, day, afterId);
  const tz = "UTC";
  const stmts: D1PreparedStatement[] = [];
  if (!supplement) {
    for (const t of ["view_daily", "view_dim_daily", "view_geo_daily"]) {
      stmts.push(db.prepare(`DELETE FROM ${t} WHERE day = ? AND ${guard.sql}`).bind(day, ...guard.args));
    }
  }
  stmts.push(
    ...insertStatements(
      db,
      "view_daily",
      ["day", "day_tz", "page", "src_kind", "src", "country", "bot_class", "views", "uniques"],
      ["day", "day_tz", "page", "src_kind", "src", "country", "bot_class"],
      p.core.map((r) => [day, tz, r.page, r.src_kind, r.src, r.country, r.bot_class, r.views, r.uniques]),
      guard,
      supplement,
    ),
    ...insertStatements(
      db,
      "view_dim_daily",
      ["day", "day_tz", "dim", "value", "bot_class", "views", "uniques"],
      ["day", "day_tz", "dim", "value", "bot_class"],
      p.dim.map((r) => [day, tz, r.dim, r.value, r.bot_class, r.views, r.uniques]),
      guard,
      supplement,
    ),
    ...insertStatements(
      db,
      "view_geo_daily",
      ["day", "day_tz", "country", "region", "city", "bot_class", "views", "uniques"],
      ["day", "day_tz", "country", "region", "city", "bot_class"],
      p.geo.map((r) => [day, tz, r.country, r.region, r.city, r.bot_class, r.views, r.uniques]),
      guard,
      supplement,
    ),
  );
  // CONTROL:rollup-seal  the checkpoint is the last statement of the same transaction.
  if (!supplement) {
    stmts.push(
      db
        .prepare(
          "INSERT INTO view_rollup_log (day, day_tz, raw_views, views, uniques, rows_core, rows_dim, rows_geo, rolled_at, max_id) " +
            `SELECT ?,?,?,?,?,?,?,?,?,? WHERE ${guard.sql}`,
        )
        .bind(day, tz, p.raw, p.views, p.uniques, p.core.length, p.dim.length, p.geo.length, nowMs, p.maxId, ...guard.args),
    );
  } else {
    stmts.push(
      db
        .prepare(
          "UPDATE view_rollup_log SET raw_views = raw_views + ?, views = views + ?, uniques = uniques + ?, " +
            "rows_core = (SELECT COUNT(*) FROM view_daily WHERE day = ?), " +
            "rows_dim = (SELECT COUNT(*) FROM view_dim_daily WHERE day = ?), " +
            "rows_geo = (SELECT COUNT(*) FROM view_geo_daily WHERE day = ?), rolled_at = ?, max_id = ? " +
            `WHERE day = ? AND max_id = ? AND ${guard.sql.split(" AND EXISTS")[0]}`,
        )
        .bind(p.raw, p.views, p.uniques, day, day, day, nowMs, p.maxId, day, afterId, lease),
    );
  }
  if (stmts.length > 2000) throw new RangeError("a day must fit one batch"); // caps keep it near 370
  const res = await db.batch(stmts);
  const sealed = res[res.length - 1]?.meta?.changes ?? 0;
  if (sealed !== 1) throw new LeaseLost();
  return p.core.length + p.dim.length + p.geo.length + 1;
}

function heartbeatStatements(db: D1Database, entries: Record<string, string>, lease: string | null, clearFailed: boolean) {
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
  if (lease !== null) stmts.push(db.prepare("DELETE FROM view_meta WHERE key = 'rollup_lease' AND value = ?").bind(lease));
  return stmts;
}

interface Work {
  day: string;
  /** 0 for a fresh day, else the max_id already counted (a supplement of late rows). */
  afterId: number;
}

/**
 * Roll every closed UTC day (strictly before today, after the grace period)
 * that has raw rows and no log row, and fold late rows into sealed days.
 * Records the heartbeat and rethrows a failure after recording its class.
 * Never makes more D1 queries than the invocation budget.
 */
export async function rollupClosedDays(env: RollupEnv, nowMs: number): Promise<RollupResult> {
  if (!Number.isFinite(nowMs)) throw new RangeError("nowMs must be a finite number");
  const outer = meterOf(env.DB);
  const meter = outer ? outer : new QueryMeter(queryBudget(env.QUERY_BUDGET));
  const db = outer ? env.DB : meteredDb(env.DB, meter);
  const eligibleEnd = nowMs - graceMs(env.ROLLUP_GRACE_HOURS);
  const todayStart = dayStartMs(utcDate(eligibleEnd));
  let days = 0;
  let rows = 0;
  let late = 0;
  let failure: unknown = null;
  let errClass: RollupError = "none";
  let failedDay = "";
  let lastGood = "";
  let complete = false;
  let work: Work[] = [];

  // One read batch (one query): days to roll, and sealed days that got late rows.
  try {
    const found = await db.batch([
      db
        .prepare(
          "SELECT DISTINCT strftime('%Y-%m-%d', ts / 1000, 'unixepoch') AS d FROM view_events WHERE ts < ?1 " +
            "AND strftime('%Y-%m-%d', ts / 1000, 'unixepoch') NOT IN (SELECT day FROM view_rollup_log) ORDER BY d LIMIT ?2",
        )
        .bind(todayStart, MAX_DAYS_PER_RUN + 1),
      db
        .prepare(
          "SELECT l.day AS d, l.max_id AS m FROM view_rollup_log l WHERE l.max_id > 0 AND EXISTS " +
            "(SELECT 1 FROM view_events e WHERE e.ts >= (strftime('%s', l.day) * 1000) AND e.ts < (strftime('%s', l.day) * 1000 + 86400000) " +
            "AND e.id > l.max_id) ORDER BY l.day LIMIT ?1",
        )
        .bind(MAX_DAYS_PER_RUN),
    ]);
    const fresh = ((found[0]?.results ?? []) as Array<{ d: string }>).map((r) => ({ day: r.d, afterId: 0 }));
    const lateDays = ((found[1]?.results ?? []) as Array<{ d: string; m: number }>).map((r) => ({ day: r.d, afterId: r.m }));
    const all = [...lateDays, ...fresh.slice(0, MAX_DAYS_PER_RUN)];
    work = all.slice(0, MAX_DAYS_PER_RUN);
    complete = fresh.length <= MAX_DAYS_PER_RUN && lateDays.length < MAX_DAYS_PER_RUN && all.length <= MAX_DAYS_PER_RUN;
  } catch (e) {
    failure = e;
    errClass = "db";
  }

  let lease: string | null = null;
  let skipped = false;
  if (failure === null && work.length > 0) {
    const token = `${crypto.randomUUID()}:${nowMs + LEASE_MS}`;
    try {
      const acq = await db
        .prepare(
          "INSERT INTO view_meta (key, value) VALUES ('rollup_lease', ?1) ON CONFLICT(key) DO UPDATE SET value = excluded.value " +
            "WHERE CAST(substr(value, instr(value, ':') + 1) AS INTEGER) < ?2",
        )
        .bind(token, nowMs)
        .run();
      if (acq.meta.changes === 1) lease = token;
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
      try {
        const plan = await aggregateDay(env, db, w.day, w.afterId);
        if (w.afterId !== 0 && plan.raw === 0) continue;
        rows += await writeDay(db, w.day, plan, nowMs, lease, w.afterId);
        if (w.afterId === 0) {
          days += 1;
          if (failedDay === "") lastGood = w.day;
        } else late += plan.raw;
      } catch (e) {
        if (e instanceof LeaseLost) {
          // Another run owns this work now. Stop quietly: it writes the heartbeat.
          return { days, rows, late };
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

  const through = failure === null && complete ? utcDate(todayStart - DAY_MS) : lastGood;
  const entries: Record<string, string> = {
    rollup_days: String(days),
    rollup_rows: String(rows),
    rollup_late_rows: String(late),
    rollup_err: errClass,
  };
  if (through !== "") entries.rolled_through = through;
  if (failedDay !== "") entries.rollup_failed_day = failedDay;
  if (failure === null) entries.rollup_last_ok = String(Math.floor(nowMs));
  try {
    await db.batch(heartbeatStatements(db, entries, lease, failure === null));
  } catch (e) {
    failure ??= e;
  }
  if (failure !== null) throw failure;
  return { days, rows, late };
}
