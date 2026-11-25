// SPDX-License-Identifier: Apache-2.0
//
// Retention for the page-view tables, run by the hourly and daily crons.
//
//   visitor_seen   every row before today's UTC date, unconditionally. It is
//                  pseudonymous (a daily hash) and the rollup never reads it
//                  (uniques come from the raw `first` flags), so it never
//                  waits on the rollup.
//   view_events    rows older than the raw keep window (default 7 days, Plan 4
//                  open decision P-3, RAW_KEEP_DAYS overrides, ceiling 90),
//                  but ONLY rows whose UTC day has a view_rollup_log row and
//                  only up to that row's max_id (a late row above it is not
//                  yet counted, so it stays until the rollup folds it in). A
//                  day not yet rolled up is never deleted by age until the
//                  hard ceiling of min(3x the keep window, 90 days); then it
//                  is dropped and view_meta('rollup_overdue') records the
//                  date. The flag is cleared by the first later run (a later
//                  UTC day) that finds nothing left beyond the ceiling.
//   view_city_daily  the city counters (migration 0019): a sealed day past the raw keep, or any day past
//                  the hard ceiling, per site like the raw rows.
//   view_daily, view_dim_daily, view_geo_daily, view_rollup_log
//                  rows older than 90 days.
//   view_meta      hourly counters. Their keys are `bud:<hour>:<route>` and
//                  `cap:<hour>:<hash>` with the hour zero-padded to eight
//                  digits, so keys sort by hour and expiry is a plain range
//                  delete on the primary key (no computed-suffix scan):
//                  bud:* older than 48 hours, cap:* older than the previous
//                  hour. Other keys are never touched.
//
// SITES (docs/analytics-design.md 3.6 and threat B1). A raw row is purgeable only
// if ITS site's day is sealed and its id is at most that log row's max_id, and the
// unrolled-day ceiling is evaluated per site. A site may set a SHORTER raw keep
// (raw_keep_days in SITES); the effective keep is min(site, global), so no site
// can keep rows longer than RAW_KEEP_DAYS or the 90 day ceiling. Rows of a site
// that is no longer in the config (or of any site while SITES is broken) age out
// with the global window: retention iterates the data, not the config.
//
// The network class (`net`) is cleared from a raw row once it is counted: when
// the day's log row for the row's site is more than a day old (past the window in
// which a late row re-rolls the day) and the row's id is at most its max_id, the
// aggregate already holds the class, and the raw row goes back to 'unknown'
// (decision D-A7). A late row above max_id keeps its class until it is counted.
//
// The emergency picker reads a bounded window of the oldest rows of each group (never a predicate scan of
// view_events) and runs at most EMERGENCY_ROUNDS rounds a run; docs/deploy.md has the worst case in rows read.
//
// Every bounded delete is keyed by the whole primary key of its table (a day of
// many sites is never taken in one statement).
//
// Deletes run in bounded batches so one run cannot hold the database for long,
// and within the invocation's D1 query budget (src/d1budget.ts): round one is
// ONE db.batch() holding the overdue check and every step's first bounded
// delete; later rounds repeat only the steps that came back full, while the
// budget lasts. A run that stops with work left records `purge_backlog` in
// view_meta and the next run simply continues (every delete is a predicate on
// age, so it resumes where it stopped); a run that finishes clears the marker.
// A bad keep value falls back to the default; it never means "delete all".

import { QueryMeter, meterOf, meteredDb, queryBudget } from "./d1budget";
import { loadSites, type SitesEnv } from "./sites";

const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;
const BATCH = 500;
const MAX_BATCHES = 200;
/**
 * The emergency picker looks at the oldest WINDOW_FACTOR x batch rows of each group (the global window and
 * every site with a shorter keep) and no further, so one picker reads a bounded number of rows however
 * large view_events is (docs/analytics-design.md 3.6, issue #759).
 */
export const WINDOW_FACTOR = 4;
/** Rounds of the emergency step in one run (the first round included); the rest waits for the next run. */
export const EMERGENCY_ROUNDS = 20;

export const DEFAULT_RAW_KEEP_DAYS = 7;
export const MAX_RAW_KEEP_DAYS = 90;
/** Unrolled raw rows are dropped at this multiple of the keep window (capped at MAX_RAW_KEEP_DAYS). */
export const HARD_CEILING_FACTOR = 3;
export const AGGREGATE_KEEP_DAYS = 90;

export interface RetentionEnv extends SitesEnv {
  DB: D1Database;
  RAW_KEEP_DAYS?: string;
  QUERY_BUDGET?: string;
}

export interface PurgeOptions {
  /** Rows per delete statement (default 500). */
  batch?: number;
  /** Rounds of delete batches per run (default 200). */
  maxBatches?: number;
}

/** Hour number as an eight-digit, zero-padded string so keys sort by hour. */
export function padHour(hour: number): string {
  return String(Math.max(0, Math.trunc(hour))).padStart(8, "0");
}

function keepDays(raw: string | undefined): number {
  if (typeof raw !== "string" || !/^\d{1,9}$/.test(raw)) return DEFAULT_RAW_KEEP_DAYS;
  const n = Number(raw);
  if (n < 1) return DEFAULT_RAW_KEEP_DAYS;
  return Math.min(n, MAX_RAW_KEEP_DAYS);
}

interface Step {
  sql: string;
  args: Array<string | number>;
  /**
   * Deletes rows no aggregate counts (the hard ceiling); its row count is recorded. `pick` is the ONE
   * `SELECT e.id ... LIMIT ?` (and its binds, `args` then the batch size) that the delete, the count
   * and the flag all embed, so the three can never select different rows.
   */
  emergency?: { pick: string };
  /** The ordinary raw purge: while it comes back full the emergency step runs again (its window moves). */
  rawPurge?: boolean;
}

/** Purge every view table, inside the invocation's D1 query budget. */
export async function purgeViews(env: RetentionEnv, nowMs: number, opts: PurgeOptions = {}): Promise<void> {
  if (!Number.isFinite(nowMs)) throw new RangeError("nowMs must be a finite number");
  const outer = meterOf(env.DB);
  const meter = outer ?? new QueryMeter(queryBudget(env.QUERY_BUDGET));
  const db = outer ? env.DB : meteredDb(env.DB, meter);
  const batch = opts.batch ?? BATCH;
  const maxRounds = opts.maxBatches ?? MAX_BATCHES;
  const today = new Date(nowMs).toISOString().slice(0, 10);
  const keep = keepDays(env.RAW_KEEP_DAYS);
  const cutoff = Math.floor(nowMs - keep * DAY_MS);
  const hardCutoff = Math.floor(nowMs - Math.min(keep * HARD_CEILING_FACTOR, MAX_RAW_KEEP_DAYS) * DAY_MS);
  const aggCutoff = new Date(nowMs - AGGREGATE_KEEP_DAYS * DAY_MS).toISOString().slice(0, 10);
  const hour = Math.floor(nowMs / HOUR_MS);
  // Per-site raw windows: a VALUES list (site, cutoff, hard cutoff) for the
  // sites that set a shorter keep. The dummy row keeps the list non-empty; the
  // empty site never matches a real one. Everything else uses the global window.
  const lows: Array<{ site: string; keep: number }> = [];
  for (const site of loadSites(env).sites) {
    if (site.rawKeepDays !== undefined && site.rawKeepDays < keep) lows.push({ site: site.id, keep: site.rawKeepDays });
  }
  const kArgs: Array<string | number> = ["", 0, 0];
  for (const l of lows) {
    kArgs.push(
      l.site,
      Math.floor(nowMs - l.keep * DAY_MS),
      Math.floor(nowMs - Math.min(l.keep * HARD_CEILING_FACTOR, MAX_RAW_KEEP_DAYS) * DAY_MS),
    );
  }
  const kRows = Array.from({ length: kArgs.length / 3 }, () => "(?,?,?)").join(",");
  // Parameters are positional: the k list first, then the step's own, then the batch size.
  const kCte = `WITH k(site, cut, hard) AS (VALUES ${kRows}) `;
  const cutOf = "COALESCE((SELECT k.cut FROM k WHERE k.site = e.site), ?)";
  const hardOf = "COALESCE((SELECT k.hard FROM k WHERE k.site = e.site), ?)";
  const sealedJoin = "JOIN view_rollup_log l ON l.site = e.site AND l.day = strftime('%Y-%m-%d', e.ts / 1000, 'unixepoch')";
  const leftJoin = sealedJoin.replace("JOIN view_rollup_log l", "LEFT JOIN view_rollup_log l");
  // The emergency delete's row picker, built ONCE: the delete, the counter and the flag each embed this
  // exact text and bind the exact same args (`pickArgs`, then the batch size), so the predicate and the
  // LIMIT cannot drift between them.
  //
  // BOUNDED READS. `LIMIT` bounds rows returned, not rows examined, so the picker never scans view_events
  // for a predicate. It reads ONE window per group, the oldest `win` rows by ts below that group's hard
  // cutoff (an index range read on idx_view_events_ts, or idx_view_events_site_ts for a site with a
  // shorter keep), joins only those to the rollup log by its primary key, and filters. A third arm reads
  // the tail of the ts index (text or blob timestamps sort after every integer) for rows whose ts is not
  // a number. Counted rows in a window are taken by the raw-purge step just before it in the same batch,
  // so the window moves on; an uncounted row therefore waits at most for the rows older than it.
  const win = Math.max(1, Math.trunc(batch)) * WINDOW_FACTOR;
  const arms: string[] = ["SELECT id, ts, site FROM (SELECT id, ts, site FROM view_events WHERE ts < ? ORDER BY ts LIMIT ?)"];
  const armArgs: Array<string | number> = [hardCutoff, win];
  for (const l of lows) {
    arms.push("SELECT id, ts, site FROM (SELECT id, ts, site FROM view_events WHERE site = ? AND ts < ? ORDER BY ts LIMIT ?)");
    armArgs.push(l.site, Math.floor(nowMs - Math.min(l.keep * HARD_CEILING_FACTOR, MAX_RAW_KEEP_DAYS) * DAY_MS), win);
  }
  const emergencyPick =
    `${kCte}SELECT id FROM (SELECT e.id AS id FROM (${arms.join(" UNION ")}) e ${leftJoin} ` +
    `WHERE e.ts < ${hardOf} AND (l.site IS NULL OR e.id > l.max_id) ` +
    "UNION SELECT id FROM (SELECT id FROM view_events WHERE ts > 9223372036854775807 LIMIT ?)) LIMIT ?";
  const pickArgs: Array<string | number> = [...kArgs, ...armArgs, hardCutoff, batch];
  const steps: Step[] = [
    {
      // CONTROL:seen-purge  unconditional: pseudonymous, and the rollup never reads it.
      sql: "DELETE FROM visitor_seen WHERE (day, h) IN (SELECT day, h FROM visitor_seen WHERE day < ? LIMIT ?)",
      args: [today],
    },
    {
      // CONTROL:raw-purge  a rolled day's rows, only up to the id the aggregate counted, per site.
      rawPurge: true,
      sql:
        `DELETE FROM view_events WHERE id IN (${kCte}SELECT e.id FROM view_events e ${sealedJoin} ` +
        `WHERE e.ts < ${cutOf} AND e.id <= l.max_id LIMIT ?)`,
      args: [...kArgs, cutoff],
    },
    {
      // The hard ceiling, UNCOUNTED rows only: unrolled days and late rows above a day's max_id,
      // past min(3 x keep, 90 days), per site. Counted rows past the ceiling are already taken by
      // the raw-purge step above (the ceiling is never shorter than the keep window), so this step
      // is the only one that loses data no aggregate holds, and its row count is recorded.
      emergency: { pick: emergencyPick },
      sql: `DELETE FROM view_events WHERE id IN (${emergencyPick})`,
      args: pickArgs,
    },
    {
      // CONTROL:net-clear  the class is already in the aggregate once the day is counted and settled.
      sql:
        `UPDATE view_events SET net = 'unknown' WHERE id IN (SELECT e.id FROM view_events e ${sealedJoin} ` +
        "WHERE e.net <> 'unknown' AND e.id <= l.max_id AND l.rolled_at < ? LIMIT ?)",
      args: [Math.floor(nowMs - DAY_MS)],
    },
    // Keyed by the whole primary key, never by day: a day of many sites must not go in one statement.
    { sql: "DELETE FROM view_daily WHERE (site, day, day_tz, page, src_kind, src, country, bot_class) IN (SELECT site, day, day_tz, page, src_kind, src, country, bot_class FROM view_daily WHERE day < ? LIMIT ?)", args: [aggCutoff] },
    {
      // CONTROL:city-counter-purge  the city counters follow the raw window of their site: a sealed day past the
      // keep, or any day past the hard ceiling.
      sql:
        `DELETE FROM view_city_daily WHERE (site, day, country, region, city, bot_class) IN (${kCte}` +
        "SELECT c.site, c.day, c.country, c.region, c.city, c.bot_class FROM view_city_daily c WHERE " +
        "(c.day < strftime('%Y-%m-%d', COALESCE((SELECT k.cut FROM k WHERE k.site = c.site), ?) / 1000, 'unixepoch') " +
        "AND EXISTS (SELECT 1 FROM view_rollup_log l WHERE l.site = c.site AND l.day = c.day)) " +
        "OR c.day < strftime('%Y-%m-%d', COALESCE((SELECT k.hard FROM k WHERE k.site = c.site), ?) / 1000, 'unixepoch') LIMIT ?)",
      args: [...kArgs, cutoff, hardCutoff],
    },
    { sql: "DELETE FROM view_dim_daily WHERE (site, day, day_tz, dim, value, bot_class) IN (SELECT site, day, day_tz, dim, value, bot_class FROM view_dim_daily WHERE day < ? LIMIT ?)", args: [aggCutoff] },
    { sql: "DELETE FROM view_geo_daily WHERE (site, day, day_tz, country, region, city, bot_class) IN (SELECT site, day, day_tz, country, region, city, bot_class FROM view_geo_daily WHERE day < ? LIMIT ?)", args: [aggCutoff] },
    { sql: "DELETE FROM view_hour_daily WHERE (site, day, hour, bot_class) IN (SELECT site, day, hour, bot_class FROM view_hour_daily WHERE day < ? LIMIT ?)", args: [aggCutoff] },
    {
      // CONTROL:log-after-aggregates  a day's log row is the proof its raw rows were counted, so it goes only once
      // no aggregate, city or raw row of THAT (site, day) is left: the tables above are bounded independently
      // and may leave different sites' rows behind, and an early log delete would orphan them.
      sql:
        "DELETE FROM view_rollup_log WHERE (site, day) IN (SELECT l.site, l.day FROM view_rollup_log l WHERE l.day < ? " +
        ["view_daily", "view_dim_daily", "view_geo_daily", "view_hour_daily", "view_city_daily"]
          .map((t) => `AND NOT EXISTS (SELECT 1 FROM ${t} a WHERE a.site = l.site AND a.day = l.day) `)
          .join("") +
        "AND NOT EXISTS (SELECT 1 FROM view_events e WHERE e.site = l.site " +
        "AND e.ts >= CAST(strftime('%s', l.day) AS INTEGER) * 1000 " +
        "AND e.ts < (CAST(strftime('%s', l.day) AS INTEGER) + 86400) * 1000) LIMIT ?)",
      args: [aggCutoff],
    },
    {
      sql: "DELETE FROM view_meta WHERE key IN (SELECT key FROM view_meta WHERE key >= 'bud:' AND key < ? LIMIT ?)",
      args: [`bud:${padHour(hour - 48)}`],
    },
    {
      sql: "DELETE FROM view_meta WHERE key IN (SELECT key FROM view_meta WHERE key >= 'cap:' AND key < ? LIMIT ?)",
      args: [`cap:${padHour(hour - 1)}`],
    },
  ];
  const del = (st: Step) => db.prepare(st.sql).bind(...st.args, batch);
  // An emergency delete carries its own bookkeeping in the SAME db.batch() (one transaction, rolled back
  // together on any failure). The bookkeeping goes FIRST and does not read changes(): it computes the
  // count with SELECT COUNT(*) over the very same picker (same text, same binds, same LIMIT) that the
  // delete then runs. Both see one snapshot (no write can land between two statements of a batch), so
  // the count equals the rows the delete removes. If the delete fails, the count rolls back with it and
  // the rows are still there for the next run; there is no "after" in which the count could be lost.
  // Order: counter, flag, delete (the flag also reads the count, so both must precede the delete).
  const countOf = (st: Step) => `SELECT COUNT(*) AS n FROM (${(st.emergency as { pick: string }).pick})`;
  const unit = (st: Step): { stmts: D1PreparedStatement[]; delAt: number } => {
    if (!st.emergency) return { stmts: [del(st)], delAt: 0 };
    return {
      stmts: [
        db
          .prepare(
            `INSERT INTO view_meta (key, value) SELECT 'rollup_overdue_rows', CAST(c.n AS TEXT) FROM (${countOf(st)}) c WHERE c.n > 0 ` +
              "ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(view_meta.value AS INTEGER) + CAST(excluded.value AS INTEGER) AS TEXT)",
          )
          .bind(...st.args, batch),
        db
          .prepare(
            `INSERT INTO view_meta (key, value) SELECT 'rollup_overdue', ? FROM (${countOf(st)}) c WHERE c.n > 0 ` +
              "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
          )
          .bind(today, ...st.args, batch),
        del(st),
      ],
      delAt: 2,
    };
  };
  /** Index of each step's delete inside the flattened statement list of one batch. */
  const flatten = (set: Step[], head: D1PreparedStatement[]) => {
    const stmts = [...head];
    const at: number[] = [];
    for (const st of set) {
      const u = unit(st);
      at.push(stmts.length + u.delAt);
      stmts.push(...u.stmts);
    }
    return { stmts, at };
  };

  const changed = (r: D1Result | undefined) => r?.meta?.changes ?? 0;
  let failure: unknown = null;
  let backlog = false;
  let hardHit = false;
  let emergencyRows = 0;
  let flagDay = "";
  try {
    // Round one: the overdue check, then every step once, in ONE query.
    const head = [
      // Any row beyond the ceiling? The same windowed picker, asked for one row.
      db.prepare(emergencyPick).bind(...pickArgs, 1),
      db.prepare("SELECT value FROM view_meta WHERE key = 'rollup_overdue'"),
    ];
    const f = flatten(steps, head);
    const first = await db.batch(f.stmts);
    hardHit = (first[0]?.results ?? []).length > 0;
    emergencyRows += steps.reduce((n, st, i) => n + (st.emergency ? changed(first[f.at[i] as number]) : 0), 0);
    flagDay = String(((first[1]?.results ?? [])[0] as { value?: string } | undefined)?.value ?? "");
    const emergencyStep = steps.find((st) => st.emergency);
    const rawStep = steps.find((st) => st.rawPurge);
    // A step runs again while it came back full; the emergency step also runs again while the raw purge
    // does, because that purge is what moves the emergency window past the counted rows.
    const nextOpen = (run: Step[], res: D1Result[], at: number[]): Step[] => {
      const full = new Set(run.filter((_, i) => changed(res[at[i] as number]) >= batch));
      return steps.filter((st) => full.has(st) || (st === emergencyStep && rawStep !== undefined && full.has(rawStep)));
    };
    let open = nextOpen(steps, first, f.at);
    let emergencyRounds = 1;
    let capped = false;
    // Later rounds repeat only the full steps; two queries stay free for the closing batch. The emergency
    // step is read-heavy, so it gets at most EMERGENCY_ROUNDS rounds a run; what is left waits for the next.
    for (let round = 1; open.length > 0 && round < maxRounds; round++) {
      if (meter.remaining() < 3) break;
      let run = open;
      if (emergencyStep && run.includes(emergencyStep)) {
        if (emergencyRounds >= EMERGENCY_ROUNDS) {
          run = run.filter((st) => st !== emergencyStep);
          capped = true;
        } else {
          emergencyRounds += 1;
        }
      }
      if (run.length === 0) break;
      const g = flatten(run, []);
      const res = await db.batch(g.stmts);
      emergencyRows += run.reduce((n, st, i) => n + (st.emergency ? changed(res[g.at[i] as number]) : 0), 0);
      open = nextOpen(run, res, g.at);
    }
    backlog = open.length > 0 || capped;
  } catch (e) {
    failure = e;
  }

  if (failure === null) {
    const close: D1PreparedStatement[] = [];
    if (backlog) {
      close.push(
        db
          .prepare(
            "INSERT INTO view_meta (key, value) VALUES ('purge_backlog', ?1) " +
              "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
          )
          .bind(today),
      );
    } else {
      close.push(db.prepare("DELETE FROM view_meta WHERE key = 'purge_backlog'"));
    }
    if (hardHit || emergencyRows > 0) {
      // The count and the flag for deleted rows were committed with the delete itself; this write
      // only covers rows still beyond the ceiling (hardHit) and is idempotent.
      close.push(
        db
          .prepare(
            "INSERT INTO view_meta (key, value) VALUES ('rollup_overdue', ?1) " +
              "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
          )
          .bind(today),
      );
    } else if (flagDay !== "" && flagDay < today && !backlog) {
      // Nothing is beyond the ceiling any more and the flag is from an earlier day: clear it.
      close.push(db.prepare("DELETE FROM view_meta WHERE key IN ('rollup_overdue', 'rollup_overdue_rows')"));
    }
    try {
      await db.batch(close);
    } catch (e) {
      failure = e;
    }
  }
  if (failure !== null) throw failure;
}
