// SPDX-License-Identifier: Apache-2.0
//
// Daily rollup of page views (Plan 4 Task A5).
//
// For every closed UTC day that still has raw rows in view_events and no row
// in view_rollup_log, this job aggregates the day into the three marginal
// tables of migration 0006 and then writes the day's view_rollup_log row.
//
//   view_daily      page x source x country x bot class        (open: capped)
//   view_dim_daily  ONE dimension at a time: browser, os, device, lang, vp,
//                   net, reason, each with the bot class        (closed enums
//                   except lang and browser, which fold to 'other')
//   view_geo_daily  country / region / city x bot class         (never joined
//                   to browser, system or language)
//
// What is never aggregated: the address, the user agent, any referrer URL,
// the browser major number (a version number is a fingerprint bit), and the
// visitor hash (it is in a different table and has no column to join on).
// `uniques` is the sum of the raw `first` flags, which the view route set
// from the daily hash at write time; no hash is needed here.
//
// Idempotent and resumable. The checkpoint is the day's view_rollup_log row.
// It is written LAST, after the aggregate rows, so a crash in between leaves
// a day with partial aggregates and no log row; the next run deletes that
// day's aggregate rows and writes them again (raw rows are still there,
// because the purge never deletes an unrolled day before the hard ceiling).
// A day with a log row is sealed: nothing here reads or writes it again.
//
// Write budget (D1 free plan counts one written row per inserted row of these
// WITHOUT ROWID tables). Per day: at most ROW_CAP_* rows in the three tables
// plus 1 log row, plus 5 heartbeat rows. See docs/deploy.md for the arithmetic.
// Writes go out in batches of BATCH_STATEMENTS statements of ROWS_PER_INSERT
// rows each, never one unbounded batch.
//
// Heartbeat (view_meta, no free text): rollup_last_ok (ms, success only),
// rollup_days and rollup_rows (this run), rollup_err (closed class), and
// rolled_through (last day that is final).

export const ROW_CAP_CORE = 1500;
export const ROW_CAP_DIM = 600;
export const ROW_CAP_GEO = 1500;
/** Days rolled per run; a backlog continues on the next run. */
export const MAX_DAYS_PER_RUN = 14;
export const DEFAULT_TOP_SOURCES = 50;
const TOP_LANG = 15;
const TOP_BROWSER = 25;
/** A city or region with fewer views than this in a day folds upward. */
const MIN_GEO_VIEWS = 5;
const ROWS_PER_INSERT = 10; // 9 columns x 10 rows = 90 bound parameters, under D1's limit of 100
const BATCH_STATEMENTS = 25;
const DAY_MS = 86_400_000;

export const ERROR_CLASSES = ["none", "mismatch", "db"] as const;
export type RollupError = (typeof ERROR_CLASSES)[number];

export interface RollupEnv {
  DB: D1Database;
  GEO_LEVEL?: string;
  TOP_SOURCES?: string;
}

export interface RollupResult {
  days: number;
  rows: number;
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

/** City under MIN_GEO_VIEWS folds into its region, a region under it into its country. */
export function foldGeo(rows: Geo[]): Geo[] {
  // CONTROL:small-count-fold
  const a = merge(
    rows.map((r) => (r.city !== "" && r.views < MIN_GEO_VIEWS ? { ...r, city: "" } : r)),
    geoKey,
  );
  return merge(
    a.map((r) => (r.city === "" && r.region !== "" && r.views < MIN_GEO_VIEWS ? { ...r, region: "" } : r)),
    geoKey,
  );
}

async function allRows<T>(db: D1Database, sql: string, ...args: Array<string | number>): Promise<T[]> {
  return (await db.prepare(sql).bind(...args).all<T>()).results;
}

interface DayPlan {
  core: Core[];
  dim: Dim[];
  geo: Geo[];
  raw: number;
  views: number;
  uniques: number;
}

const DIM_COLUMNS = ["browser", "os", "device", "lang", "vp", "net", "reason"] as const;

async function aggregateDay(env: RollupEnv, day: string): Promise<DayPlan> {
  const lo = dayStartMs(day);
  const hi = lo + DAY_MS;
  const level = geoLevel(env.GEO_LEVEL);
  const db = env.DB;
  const rawCount =
    (await db.prepare("SELECT COUNT(*) AS n FROM view_events WHERE ts >= ?1 AND ts < ?2").bind(lo, hi).first<{ n: number }>())
      ?.n ?? 0;

  let core = await allRows<Core>(
    db,
    "SELECT page, src_kind, src, country, bot_class, COUNT(*) AS views, SUM(first) AS uniques FROM view_events WHERE ts >= ?1 AND ts < ?2 " +
      "GROUP BY page, src_kind, src, country, bot_class",
    lo,
    hi,
  );
  core = capSources(merge(core, coreKey), topSources(env.TOP_SOURCES));
  core = hardCap(core, ROW_CAP_CORE, coreKey, (r) => ({ ...r, src_kind: "o", src: "other", country: "ZZ" }), 40);

  let dim: Dim[] = [];
  for (const c of DIM_COLUMNS) {
    dim.push(
      ...(await allRows<Dim>(
        db,
        `SELECT '${c}' AS dim, ${c} AS value, bot_class, COUNT(*) AS views, SUM(first) AS uniques ` +
          "FROM view_events WHERE ts >= ?1 AND ts < ?2 GROUP BY bot_class, " +
          c,
        lo,
        hi,
      )),
    );
  }
  dim = capDimValues(capDimValues(dim, "lang", TOP_LANG), "browser", TOP_BROWSER);
  dim = hardCap(dim, ROW_CAP_DIM, dimKey, (r) => ({ ...r, value: "other" }), 40);

  const cityExpr = level === "city" ? "city" : "''";
  const regionExpr = level === "country" ? "''" : "region";
  let geo = await allRows<Geo>(
    db,
    `SELECT country, ${regionExpr} AS region, ${cityExpr} AS city, bot_class, COUNT(*) AS views, SUM(first) AS uniques ` +
      "FROM view_events WHERE ts >= ?1 AND ts < ?2 GROUP BY country, 2, 3, bot_class",
    lo,
    hi,
  );
  geo = foldGeo(merge(geo, geoKey));
  geo = hardCap(geo, ROW_CAP_GEO, geoKey, (r) => ({ ...r, country: "ZZ", region: "", city: "" }), 8);

  const sum = (rows: Array<{ views: number; uniques: number }>) =>
    rows.reduce((a, r) => [a[0] + r.views, a[1] + r.uniques], [0, 0]);
  const [vc, uc] = sum(core);
  const [vd] = sum(dim.filter((r) => r.dim === "browser"));
  const [vg, ug] = sum(geo);
  // Every raw row must appear exactly once in each table. DIM_COLUMNS rows
  // each cover the day once, so check one dimension per column.
  const dimOk = DIM_COLUMNS.every((c) => sum(dim.filter((r) => r.dim === c))[0] === rawCount);
  if (vc !== rawCount || vg !== rawCount || vd !== rawCount || !dimOk || uc !== ug) {
    throw new RollupMismatch();
  }
  return { core, dim, geo, raw: rawCount, views: vc, uniques: uc };
}

class RollupMismatch extends Error {
  constructor() {
    super("mismatch");
  }
}

function insertStatements(
  db: D1Database,
  table: string,
  cols: string[],
  rows: Array<Array<string | number>>,
): D1PreparedStatement[] {
  const out: D1PreparedStatement[] = [];
  for (let i = 0; i < rows.length; i += ROWS_PER_INSERT) {
    const chunk = rows.slice(i, i + ROWS_PER_INSERT);
    const ph = `(${cols.map(() => "?").join(",")})`;
    out.push(
      db
        .prepare(`INSERT INTO ${table} (${cols.join(",")}) VALUES ${chunk.map(() => ph).join(",")}`)
        .bind(...chunk.flat()),
    );
  }
  return out;
}

async function runBatched(db: D1Database, stmts: D1PreparedStatement[]): Promise<void> {
  for (let i = 0; i < stmts.length; i += BATCH_STATEMENTS) {
    await db.batch(stmts.slice(i, i + BATCH_STATEMENTS));
  }
}

async function writeDay(env: RollupEnv, day: string, p: DayPlan, nowMs: number): Promise<number> {
  const db = env.DB;
  // Clear any partial aggregates from an interrupted run of this very day.
  await db.batch([
    db.prepare("DELETE FROM view_daily WHERE day = ?1").bind(day),
    db.prepare("DELETE FROM view_dim_daily WHERE day = ?1").bind(day),
    db.prepare("DELETE FROM view_geo_daily WHERE day = ?1").bind(day),
  ]);
  const tz = "UTC";
  const stmts = [
    ...insertStatements(
      db,
      "view_daily",
      ["day", "day_tz", "page", "src_kind", "src", "country", "bot_class", "views", "uniques"],
      p.core.map((r) => [day, tz, r.page, r.src_kind, r.src, r.country, r.bot_class, r.views, r.uniques]),
    ),
    ...insertStatements(
      db,
      "view_dim_daily",
      ["day", "day_tz", "dim", "value", "bot_class", "views", "uniques"],
      p.dim.map((r) => [day, tz, r.dim, r.value, r.bot_class, r.views, r.uniques]),
    ),
    ...insertStatements(
      db,
      "view_geo_daily",
      ["day", "day_tz", "country", "region", "city", "bot_class", "views", "uniques"],
      p.geo.map((r) => [day, tz, r.country, r.region, r.city, r.bot_class, r.views, r.uniques]),
    ),
  ];
  await runBatched(db, stmts);
  // CONTROL:rollup-seal  the log row is the checkpoint and is written last.
  await db
    .prepare(
      "INSERT INTO view_rollup_log (day, day_tz, raw_views, views, uniques, rows_core, rows_dim, rows_geo, rolled_at) " +
        "VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9)",
    )
    .bind(day, tz, p.raw, p.views, p.uniques, p.core.length, p.dim.length, p.geo.length, nowMs)
    .run();
  return p.core.length + p.dim.length + p.geo.length + 1;
}

async function heartbeat(
  db: D1Database,
  entries: Record<string, string>,
): Promise<void> {
  const keys = Object.keys(entries);
  await db
    .prepare(
      `INSERT INTO view_meta (key, value) VALUES ${keys.map(() => "(?,?)").join(",")} ` +
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
    )
    .bind(...keys.flatMap((k) => [k, entries[k] as string]))
    .run();
}

/**
 * Roll every closed UTC day (strictly before today) that has raw rows and no
 * log row. Records the heartbeat either way and rethrows a failure after
 * recording its class.
 */
export async function rollupClosedDays(env: RollupEnv, nowMs: number): Promise<RollupResult> {
  if (!Number.isFinite(nowMs)) throw new RangeError("nowMs must be a finite number");
  const db = env.DB;
  const todayStart = dayStartMs(utcDate(nowMs));
  let days = 0;
  let rows = 0;
  let lastDay = "";
  let failure: unknown = null;
  let errClass: RollupError = "none";
  let complete = false;
  try {
    const pending = await allRows<{ d: string }>(
      db,
      "SELECT DISTINCT strftime('%Y-%m-%d', ts / 1000, 'unixepoch') AS d FROM view_events WHERE ts < ?1 " +
        "AND strftime('%Y-%m-%d', ts / 1000, 'unixepoch') NOT IN (SELECT day FROM view_rollup_log) ORDER BY d LIMIT ?2",
      todayStart,
      MAX_DAYS_PER_RUN + 1,
    );
    complete = pending.length <= MAX_DAYS_PER_RUN;
    for (const { d } of pending.slice(0, MAX_DAYS_PER_RUN)) {
      const plan = await aggregateDay(env, d);
      rows += await writeDay(env, d, plan, nowMs);
      days += 1;
      lastDay = d;
    }
  } catch (e) {
    failure = e;
    errClass = e instanceof RollupMismatch ? "mismatch" : "db";
    complete = false;
  }
  const through = complete ? utcDate(todayStart - DAY_MS) : lastDay;
  const entries: Record<string, string> = {
    rollup_days: String(days),
    rollup_rows: String(rows),
    rollup_err: errClass,
  };
  if (through !== "") entries.rolled_through = through;
  if (failure === null) entries.rollup_last_ok = String(Math.floor(nowMs));
  try {
    await heartbeat(db, entries);
  } catch (e) {
    failure ??= e;
  }
  if (failure !== null) throw failure;
  return { days, rows };
}
