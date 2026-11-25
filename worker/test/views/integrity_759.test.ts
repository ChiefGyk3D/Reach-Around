// SPDX-License-Identifier: Apache-2.0
// Rollup and retention bookkeeping (issue #759): late rows above a day's counted
// id, the re-roll closed state, key-bounded aggregate deletes, bounded click
// retention and the hour table staying in step with the daily tables.
import { beforeEach, describe, expect, it } from "vitest";
import { QueryMeter, meteredDb } from "../../src/d1budget";
import { purgeOldEvents } from "../../src/retention";
import { purgeViews } from "../../src/viewretention";
import { rollupClosedDays } from "../../src/viewrollup";
import { DB, clearAll } from "./helpers";

const DAY = 86_400_000;
const HOUR = 3_600_000;
const NOW = Date.parse("2027-03-14T12:00:30Z");
const D13 = Date.parse("2027-03-13T00:00:00Z");
const day = (ms: number) => new Date(ms).toISOString().slice(0, 10);
const q = async <T>(sql: string) => (await DB.prepare(sql).all<T>()).results;
const meta = async () => Object.fromEntries((await q<{ key: string; value: string }>("SELECT * FROM view_meta")).map((m) => [m.key, m.value]));

async function addRaw(ts: number, n = 1): Promise<void> {
  for (let i = 0; i < n; i++) {
    await DB.prepare("INSERT INTO view_events (ts, page, src_kind, bot_class, reason) VALUES (?, 'home', 'd', 'h', 'ok')").bind(ts + i).run();
  }
}
const LOG_COLS = "(site, day, day_tz, raw_views, views, uniques, rows_core, rows_dim, rows_geo, rolled_at, max_id, rerolls)";
async function sealDay(d: string, maxId: number): Promise<void> {
  await DB.prepare(`INSERT INTO view_rollup_log ${LOG_COLS} VALUES ('links', ?, 'UTC', 0, 0, 0, 0, 0, 0, 0, ?, 0)`).bind(d, maxId).run();
}

beforeEach(async () => {
  await clearAll();
  await DB.prepare("DELETE FROM click_events").run();
});

describe("F-02 hard ceiling and uncounted rows", () => {
  it("a_late_row_above_the_counted_id_is_deleted_at_the_ceiling_but_flagged_and_counted", async () => {
    const old = NOW - 30 * DAY; // past the 21 day ceiling
    await addRaw(old, 2);
    const ids = (await q<{ id: number }>("SELECT id FROM view_events ORDER BY id")).map((r) => r.id);
    await sealDay(day(old), ids[0] as number); // row 2 is above max_id: never counted
    await purgeViews({ DB }, NOW);
    expect(await q("SELECT id FROM view_events")).toEqual([]);
    const m = await meta();
    expect(m.rollup_overdue).toBe("2027-03-14");
    expect(m.rollup_overdue_rows).toBe("1");
  });

  it("counted_rows_past_the_ceiling_are_deleted_without_the_overdue_flag", async () => {
    const old = NOW - 30 * DAY;
    await addRaw(old, 2);
    const max = (await q<{ m: number }>("SELECT MAX(id) AS m FROM view_events"))[0]?.m as number;
    await sealDay(day(old), max);
    await purgeViews({ DB }, NOW);
    expect(await q("SELECT id FROM view_events")).toEqual([]);
    const m = await meta();
    expect(m.rollup_overdue).toBeUndefined();
    expect(m.rollup_overdue_rows).toBeUndefined();
  });

  it("the_overdue_count_adds_up_across_runs_and_clears_with_the_flag", async () => {
    const old = NOW - 30 * DAY;
    await addRaw(old, 3);
    await purgeViews({ DB }, NOW, { batch: 2, maxBatches: 1 });
    expect((await meta()).rollup_overdue_rows).toBe("2");
    await purgeViews({ DB }, NOW, { batch: 2, maxBatches: 1 });
    expect((await meta()).rollup_overdue_rows).toBe("3");
    await purgeViews({ DB }, NOW + DAY);
    const m = await meta();
    expect(m.rollup_overdue).toBeUndefined();
    expect(m.rollup_overdue_rows).toBeUndefined();
  });
});

describe("F-02 the overdue count cannot be lost to a failure after the delete", () => {
  const old = NOW - 30 * DAY;
  async function oneLateRow(): Promise<void> {
    await addRaw(old, 2);
    const ids = (await q<{ id: number }>("SELECT id FROM view_events ORDER BY id")).map((r) => r.id);
    await sealDay(day(old), ids[0] as number); // the second row is above max_id: never counted
  }

  it("a_failed_closing_batch_after_the_delete_still_leaves_the_count_and_the_flag", async () => {
    await oneLateRow();
    let calls = 0;
    const failing = new Proxy(DB, {
      get(t, p) {
        if (p === "batch") {
          return async (stmts: D1PreparedStatement[]) => {
            calls += 1;
            if (calls === 2) throw new Error("injected: closing batch failed");
            return t.batch(stmts);
          };
        }
        const v = Reflect.get(t, p, t);
        return typeof v === "function" ? v.bind(t) : v;
      },
    }) as D1Database;
    await expect(purgeViews({ DB: failing }, NOW)).rejects.toThrow("injected");
    expect(calls).toBe(2);
    expect(await q("SELECT id FROM view_events")).toEqual([]); // the delete went through
    const m = await meta();
    expect(m.rollup_overdue_rows).toBe("1");
    expect(m.rollup_overdue).toBe("2027-03-14");
    // The next run neither loses nor doubles it.
    await purgeViews({ DB }, NOW);
    expect((await meta()).rollup_overdue_rows).toBe("1");
  });
});

describe("F-02 the bookkeeping shares the delete's picker and does not read changes()", () => {
  const old = NOW - 30 * DAY;
  async function lateRows(n: number): Promise<void> {
    await addRaw(old, n + 1);
    const ids = (await q<{ id: number }>("SELECT id FROM view_events ORDER BY id")).map((r) => r.id);
    await sealDay(day(old), ids[0] as number); // every row after the first is uncounted
  }
  function recording() {
    const sqls: string[] = [];
    const proxy = new Proxy(DB, {
      get(t, p) {
        if (p === "prepare") {
          return (sql: string) => {
            sqls.push(sql);
            return t.prepare(sql);
          };
        }
        const v = Reflect.get(t, p, t);
        return typeof v === "function" ? v.bind(t) : v;
      },
    }) as D1Database;
    return { sqls, proxy };
  }

  it("counter_flag_and_delete_embed_one_identical_picker_and_the_bookkeeping_comes_first", async () => {
    await lateRows(2);
    const { sqls, proxy } = recording();
    await purgeViews({ DB: proxy }, NOW);
    const del = sqls.findIndex((s) => s.startsWith("DELETE FROM view_events WHERE id IN (") && s.includes("typeof(e.ts)"));
    const counter = sqls.findIndex((s) => s.includes("'rollup_overdue_rows'") && s.includes("COUNT(*)"));
    const flag = sqls.findIndex((s) => s.includes("'rollup_overdue', ?") && s.includes("COUNT(*)"));
    expect(del).toBeGreaterThan(-1);
    expect(counter).toBeGreaterThan(-1);
    expect(flag).toBeGreaterThan(-1);
    expect(counter).toBeLessThan(del);
    expect(flag).toBeLessThan(del);
    const delPick = (sqls[del] as string).slice("DELETE FROM view_events WHERE id IN (".length, -1);
    expect(delPick).toContain("LIMIT ?");
    expect(sqls[counter]).toContain(delPick);
    expect(sqls[flag]).toContain(delPick);
    for (const s of sqls) expect(s).not.toContain("changes()");
  });

  it("the_count_uses_the_same_limit_as_the_delete_so_it_never_overcounts", async () => {
    await lateRows(5);
    await purgeViews({ DB }, NOW, { batch: 2, maxBatches: 1 });
    expect((await q<{ n: number }>("SELECT COUNT(*) AS n FROM view_events"))[0]?.n).toBe(3); // 6 rows: 1 counted row (raw-purge step) and 2 uncounted ones (emergency, LIMIT 2) deleted
    expect((await meta()).rollup_overdue_rows).toBe("2");
    await purgeViews({ DB }, NOW, { batch: 2, maxBatches: 1 });
    expect((await meta()).rollup_overdue_rows).toBe("4");
  });

  it("a_failure_on_the_delete_rolls_the_bookkeeping_back", async () => {
    await lateRows(2);
    await DB.prepare(
      "CREATE TRIGGER fail_emergency_delete BEFORE DELETE ON view_events WHEN OLD.id > 0 BEGIN SELECT RAISE(ABORT, 'injected'); END",
    ).run();
    try {
      await expect(purgeViews({ DB }, NOW)).rejects.toThrow();
      expect((await q<{ n: number }>("SELECT COUNT(*) AS n FROM view_events"))[0]?.n).toBe(3);
      const m = await meta();
      expect(m.rollup_overdue_rows).toBeUndefined();
      expect(m.rollup_overdue).toBeUndefined();
    } finally {
      await DB.prepare("DROP TRIGGER IF EXISTS fail_emergency_delete").run();
    }
    await purgeViews({ DB }, NOW);
    expect((await meta()).rollup_overdue_rows).toBe("2");
  });

  // Why a row cannot arrive between the count and the delete: both are statements of one db.batch(),
  // which D1 runs as one transaction in order, so a concurrent insert lands entirely before or after
  // it. A row inserted before the batch is counted AND deleted; one inserted after is neither (it is
  // picked up by the next run). The test pins the observable consequence: count == rows removed.
  it("the_count_equals_the_rows_removed_when_a_row_arrives_just_before_the_batch", async () => {
    await lateRows(1);
    let injected = false;
    const racing = new Proxy(DB, {
      get(t, p) {
        if (p === "batch") {
          return async (stmts: D1PreparedStatement[]) => {
            if (!injected) {
              injected = true;
              await addRaw(old + 5000, 1); // arrives before the batch starts: inside it, counted and deleted
            }
            return t.batch(stmts);
          };
        }
        const v = Reflect.get(t, p, t);
        return typeof v === "function" ? v.bind(t) : v;
      },
    }) as D1Database;
    await purgeViews({ DB: racing }, NOW);
    expect(await q("SELECT id FROM view_events")).toEqual([]);
    expect((await meta()).rollup_overdue_rows).toBe("2");
  });
});

describe("F-22 the rollup log goes only after the site's other rows", () => {
  const old = day(NOW - 120 * DAY);
  const dim = (site: string, v: string) =>
    DB.prepare("INSERT INTO view_dim_daily (site, day, day_tz, dim, value, bot_class, views, uniques) VALUES (?, ?, 'UTC', 'browser', ?, 'h', 1, 0)").bind(site, old, v);
  const logRow = (site: string) =>
    DB.prepare(`INSERT INTO view_rollup_log ${LOG_COLS} VALUES (?, ?, 'UTC', 0, 0, 0, 0, 0, 0, 0, 0, 0)`).bind(site, old);
  const sites = async () => (await q<{ site: string }>("SELECT site FROM view_rollup_log ORDER BY site")).map((r) => r.site);

  it("skewed_dimension_rows_of_one_site_keep_that_sites_log_but_not_the_others", async () => {
    const stmts: D1PreparedStatement[] = [];
    for (let i = 0; i < 12; i++) stmts.push(dim("s00", `v${i}`));
    for (const s of ["s00", "s01", "s02", "s03"]) stmts.push(logRow(s));
    await DB.batch(stmts);
    await purgeViews({ DB }, NOW, { batch: 5, maxBatches: 1 });
    expect((await q<{ n: number }>("SELECT COUNT(*) AS n FROM view_dim_daily"))[0]?.n).toBe(7);
    expect(await sites()).toEqual(["s00"]); // the others had nothing left, s00 still has aggregates
    await purgeViews({ DB }, NOW, { batch: 5, maxBatches: 10 });
    expect((await q<{ n: number }>("SELECT COUNT(*) AS n FROM view_dim_daily"))[0]?.n).toBe(0);
    expect(await sites()).toEqual(["s00"]); // the log step is not a full step, so it waits for the next run
    await purgeViews({ DB }, NOW, { batch: 5, maxBatches: 10 });
    expect(await sites()).toEqual([]);
  });

  it("retained_raw_rows_of_the_day_keep_its_log", async () => {
    const stmts: D1PreparedStatement[] = [logRow("links")];
    await DB.batch(stmts);
    const ts = Date.parse(`${old}T10:00:00Z`);
    await addRaw(ts, 6);
    const max = (await q<{ m: number }>("SELECT MAX(id) AS m FROM view_events"))[0]?.m as number;
    await DB.prepare("UPDATE view_rollup_log SET max_id = ? WHERE site = 'links'").bind(max).run();
    await purgeViews({ DB }, NOW, { batch: 5, maxBatches: 1 });
    expect((await q<{ n: number }>("SELECT COUNT(*) AS n FROM view_events"))[0]?.n).toBe(1);
    expect(await sites()).toEqual(["links"]);
    await purgeViews({ DB }, NOW, { batch: 5, maxBatches: 10 });
    expect(await q("SELECT id FROM view_events")).toEqual([]);
    await purgeViews({ DB }, NOW, { batch: 5, maxBatches: 10 });
    expect(await sites()).toEqual([]);
  });
});

describe("F-03 re-roll closed state", () => {
  const env = { DB, MIN_CELL: "1" };
  async function views(): Promise<number> {
    return (await q<{ v: number }>("SELECT COALESCE(SUM(views), 0) AS v FROM view_daily"))[0]?.v ?? 0;
  }

  it("a_dropped_late_batch_closes_the_day_so_a_later_batch_cannot_re_roll_it", async () => {
    await addRaw(D13 + 1000, 3);
    await rollupClosedDays(env, NOW);
    expect(await views()).toBe(3);
    // The purge took the first counted row.
    await DB.prepare("DELETE FROM view_events WHERE id = (SELECT MIN(id) FROM view_events)").run();
    await addRaw(D13 + 5000, 1); // late batch one: cannot be re-rolled, dropped
    await rollupClosedDays(env, NOW + HOUR);
    expect(await views()).toBe(3);
    expect((await q<{ c: number }>("SELECT reroll_closed AS c FROM view_rollup_log"))[0]?.c).toBe(1);
    // Late batch two: the surviving row count now equals raw_views by coincidence.
    await addRaw(D13 + 6000, 1);
    await rollupClosedDays(env, NOW + 2 * HOUR);
    expect(await views()).toBe(3); // the correct aggregates were not replaced
    expect((await meta()).rollup_late_dropped).toBe("1");
    expect((await q<{ n: number }>("SELECT rerolls AS n FROM view_rollup_log"))[0]?.n).toBe(0);
  });

  it("a_late_row_inside_the_seal_window_still_re_rolls", async () => {
    await addRaw(D13 + 1000, 3);
    await rollupClosedDays(env, NOW);
    await addRaw(D13 + 5000, 1);
    // day end 03-14T00:00 + 2 h grace + 48 h = 03-16T02:00; one minute before it.
    await rollupClosedDays(env, Date.parse("2027-03-16T01:59:00Z"));
    expect(await views()).toBe(4);
    expect((await q<{ c: number }>("SELECT reroll_closed AS c FROM view_rollup_log"))[0]?.c).toBe(0);
  });

  it("a_late_row_after_the_seal_window_is_dropped_and_closes_the_day", async () => {
    await addRaw(D13 + 1000, 3);
    await rollupClosedDays(env, NOW);
    await addRaw(D13 + 5000, 1);
    await rollupClosedDays(env, Date.parse("2027-03-16T02:00:00Z"));
    expect(await views()).toBe(3);
    expect((await meta()).rollup_late_dropped).toBe("1");
    expect((await q<{ c: number }>("SELECT reroll_closed AS c FROM view_rollup_log"))[0]?.c).toBe(1);
    // It is not found again.
    await rollupClosedDays(env, Date.parse("2027-03-16T03:00:00Z"));
    expect((await meta()).rollup_late_dropped).toBe("0");
  });
});

describe("F-22 aggregate deletes are bounded by key", () => {
  it("a_batch_deletes_at_most_batch_rows_of_a_many_site_expired_day", async () => {
    const old = day(NOW - 120 * DAY);
    const stmts: D1PreparedStatement[] = [];
    for (let i = 0; i < 12; i++) {
      const c = String(i).padStart(2, "0");
      stmts.push(
        DB.prepare("INSERT INTO view_daily (site, day, day_tz, page, src_kind, src, country, bot_class, views, uniques) VALUES (?, ?, 'UTC', 'home', 'd', '', 'US', 'h', 1, 0)").bind(`s${c}`, old),
        DB.prepare("INSERT INTO view_dim_daily (site, day, day_tz, dim, value, bot_class, views, uniques) VALUES (?, ?, 'UTC', 'browser', 'chrome', 'h', 1, 0)").bind(`s${c}`, old),
        DB.prepare("INSERT INTO view_geo_daily (site, day, day_tz, country, region, city, bot_class, views, uniques) VALUES (?, ?, 'UTC', 'US', '', '', 'h', 1, 0)").bind(`s${c}`, old),
        DB.prepare("INSERT INTO view_hour_daily (site, day, hour, bot_class, views, clicks) VALUES (?, ?, 1, 'h', 1, 0)").bind(`s${c}`, old),
        DB.prepare(`INSERT INTO view_rollup_log ${LOG_COLS} VALUES (?, ?, 'UTC', 0, 0, 0, 0, 0, 0, 0, 0, 0)`).bind(`s${c}`, old),
      );
    }
    await DB.batch(stmts);
    await purgeViews({ DB }, NOW, { batch: 5, maxBatches: 1 });
    for (const t of ["view_daily", "view_dim_daily", "view_geo_daily", "view_hour_daily", "view_rollup_log"]) {
      expect((await q<{ n: number }>(`SELECT COUNT(*) AS n FROM ${t}`))[0]?.n, t).toBe(7);
    }
    await purgeViews({ DB }, NOW, { batch: 5, maxBatches: 10 });
    for (const t of ["view_daily", "view_dim_daily", "view_geo_daily", "view_hour_daily", "view_rollup_log"]) {
      expect((await q<{ n: number }>(`SELECT COUNT(*) AS n FROM ${t}`))[0]?.n, t).toBe(0);
    }
  });
});

describe("F-23 click retention is bounded", () => {
  const click = (ts: number) => DB.prepare("INSERT INTO click_events (ts, link_id) VALUES (?, 'example-one')").bind(ts);
  const env = { DB, SITE_HOST: "x.example.test", LINK_IDS: "[]", CLICK_LIMITER: { limit: async () => ({ success: true }) } };

  it("deletes_in_fixed_batches_records_a_marker_and_resumes", async () => {
    await DB.batch(Array.from({ length: 23 }, (_, i) => click(NOW - 200 * DAY + i)));
    await DB.batch([click(NOW - 1000)]);
    const first = await purgeOldEvents(env, NOW, 90, { batch: 5, maxBatches: 2 });
    expect(first).toBe(10);
    expect((await q<{ n: number }>("SELECT COUNT(*) AS n FROM click_events"))[0]?.n).toBe(14);
    expect((await meta()).click_purge_backlog).toBe("2027-03-14");
    const second = await purgeOldEvents(env, NOW, 90, { batch: 5, maxBatches: 10 });
    expect(second).toBe(13);
    expect((await q<{ n: number }>("SELECT COUNT(*) AS n FROM click_events"))[0]?.n).toBe(1);
    expect((await meta()).click_purge_backlog).toBeUndefined();
  });

  const expired = (n: number) => DB.batch(Array.from({ length: n }, (_, i) => click(NOW - 200 * DAY + i)));
  const marker = async () => (await meta()).click_purge_backlog;

  it("a_run_cut_short_by_the_shared_meter_still_records_the_backlog", async () => {
    await expired(4);
    const tight = meteredDb(DB, new QueryMeter(11)); // below the reserve: no delete runs, one query is left
    expect(await purgeOldEvents({ ...env, DB: tight }, NOW, 90, { batch: 5 })).toBe(0);
    expect(await marker()).toBe("2027-03-14");
  });

  it("a_stale_marker_is_cleared_by_a_run_that_deletes_nothing", async () => {
    await DB.prepare("INSERT INTO view_meta (key, value) VALUES ('click_purge_backlog', '2027-03-01')").run();
    expect(await purgeOldEvents(env, NOW, 90)).toBe(0);
    expect(await marker()).toBeUndefined();
  });

  it("a_failed_marker_write_is_repaired_by_the_next_run_even_with_nothing_to_delete", async () => {
    await expired(12);
    let calls = 0;
    const failing = new Proxy(DB, {
      get(t, p) {
        if (p === "batch") {
          return async () => {
            calls += 1;
            throw new Error("injected: marker batch failed");
          };
        }
        const v = Reflect.get(t, p, t);
        return typeof v === "function" ? v.bind(t) : v;
      },
    }) as D1Database;
    expect(await purgeOldEvents({ ...env, DB: failing }, NOW, 90, { batch: 5, maxBatches: 1 })).toBe(5);
    expect(calls).toBe(1);
    expect(await marker()).toBeUndefined(); // the advisory write was lost while 7 expired rows remain
    // The next run deletes nothing (the shared meter is low) but reconciles the marker.
    const tight = meteredDb(DB, new QueryMeter(11));
    expect(await purgeOldEvents({ ...env, DB: tight }, NOW, 90, { batch: 5 })).toBe(0);
    expect(await marker()).toBe("2027-03-14");
  });
});

describe("F-24 the hour table counts exactly what the daily tables count", () => {
  it("a_row_written_between_the_read_and_the_seal_is_not_counted_in_hours", async () => {
    await addRaw(D13 + 1000, 3);
    let armed = false;
    let injected = false;
    const racing = new Proxy(DB, {
      get(t, p) {
        if (p === "prepare") {
          return (sql: string) => {
            if (sql.startsWith("INSERT INTO view_hour_daily")) armed = true;
            return t.prepare(sql);
          };
        }
        if (p === "batch") {
          return async (stmts: D1PreparedStatement[]) => {
            if (armed && !injected) {
              injected = true;
              await addRaw(D13 + 9000, 1);
            }
            return t.batch(stmts);
          };
        }
        const v = Reflect.get(t, p, t);
        return typeof v === "function" ? v.bind(t) : v;
      },
    }) as D1Database;
    await rollupClosedDays({ DB: racing, MIN_CELL: "1" }, NOW);
    expect(injected).toBe(true);
    const hour = (await q<{ v: number }>("SELECT COALESCE(SUM(views), 0) AS v FROM view_hour_daily"))[0]?.v;
    const daily = (await q<{ v: number }>("SELECT COALESCE(SUM(views), 0) AS v FROM view_daily"))[0]?.v;
    expect(daily).toBe(3);
    expect(hour).toBe(3);
    // The late row is found next run and both tables then agree on 4.
    await rollupClosedDays({ DB, MIN_CELL: "1" }, NOW + HOUR);
    expect((await q<{ v: number }>("SELECT SUM(views) AS v FROM view_hour_daily"))[0]?.v).toBe(4);
    expect((await q<{ v: number }>("SELECT SUM(views) AS v FROM view_daily"))[0]?.v).toBe(4);
  });
});

describe("F-23 click purge options and metered cost (#759 round 3)", () => {
  const click = (ts: number) => DB.prepare("INSERT INTO click_events (ts, link_id) VALUES (?, 'example-one')").bind(ts);
  const env = { DB, SITE_HOST: "x.example.test", LINK_IDS: "[]", CLICK_LIMITER: { limit: async () => ({ success: true }) } };

  it("non_finite_options_are_refused_and_nothing_is_deleted", async () => {
    await DB.batch([click(NOW - 200 * DAY)]);
    for (const opts of [{ batch: Number.NaN }, { batch: Number.POSITIVE_INFINITY }, { maxBatches: Number.NaN }, { maxBatches: Number.POSITIVE_INFINITY }]) {
      await expect(purgeOldEvents(env, NOW, 90, opts)).rejects.toThrow(RangeError);
    }
    expect((await q<{ n: number }>("SELECT COUNT(*) AS n FROM click_events"))[0]?.n).toBe(1);
  });

  it("oversized_options_are_clamped_to_the_defaults", async () => {
    const meter = new QueryMeter(1000);
    const rows = 500 * 10 + 300;
    for (let i = 0; i < rows; i += 500) {
      await DB.batch(Array.from({ length: Math.min(500, rows - i) }, (_, j) => click(NOW - 200 * DAY + i + j)));
    }
    const n = await purgeOldEvents({ ...env, DB: meteredDb(DB, meter) }, NOW, 90, { batch: 1_000_000, maxBatches: 1_000_000 });
    expect(n).toBe(5000); // 10 rounds of 500, not everything
    expect(meter.used).toBe(11); // 10 deletes + 1 marker batch
    expect((await q<{ n: number }>("SELECT COUNT(*) AS n FROM click_events"))[0]?.n).toBe(300);
  });

  it("an_idle_run_costs_two_queries_and_a_backlog_run_one_more_than_its_deletes", async () => {
    const idle = new QueryMeter(100);
    await purgeOldEvents({ ...env, DB: meteredDb(DB, idle) }, NOW);
    expect(idle.used).toBe(2);
    await DB.batch(Array.from({ length: 7 }, (_, i) => click(NOW - 200 * DAY + i)));
    const busy = new QueryMeter(100);
    await purgeOldEvents({ ...env, DB: meteredDb(DB, busy) }, NOW, 90, { batch: 3, maxBatches: 2 });
    expect(busy.used).toBe(3); // 2 deletes + 1 marker batch
  });
});
