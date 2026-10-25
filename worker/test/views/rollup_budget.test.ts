// SPDX-License-Identifier: Apache-2.0
// Query budget, lease, grace period, late rows, small cells and the overdue flag
// of the page-view rollup. Synthetic data only.
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  BudgetExceeded,
  DEFAULT_QUERY_BUDGET,
  MAX_QUERY_BUDGET,
  QueryMeter,
  meteredDb,
  queryBudget,
} from "../../src/d1budget";
import worker from "../../src/index";
import { purgeViews } from "../../src/viewretention";
import { graceMs, minCell, rollupClosedDays } from "../../src/viewrollup";
import { DB, clearAll, fakeLimiter } from "./helpers";

const DAY = 86_400_000;
const NOW = Date.parse("2027-03-14T12:00:30Z");
const D13 = Date.parse("2027-03-13T00:00:00Z");

interface Raw {
  ts: number;
  src_kind?: string;
  src?: string;
  country?: string;
  region?: string;
  city?: string;
  browser?: string;
  lang?: string;
  os?: string;
  bot_class?: string;
  first?: number;
}
const COLS = ["ts", "page", "src_kind", "src", "country", "region", "city", "browser", "bmajor", "os", "device", "lang", "vp", "net", "bot_class", "reason", "first"];
const DEF = {
  page: "home", src_kind: "d", src: "", country: "US", region: "", city: "", browser: "chrome", bmajor: 1, os: "linux",
  device: "desktop", lang: "en", vp: "l", net: "residential", bot_class: "h", reason: "ok", first: 0,
};

async function insert(list: Raw[]) {
  for (let i = 0; i < list.length; i += 25) {
    await DB.batch(
      list.slice(i, i + 25).map((r) => {
        const full = { ...DEF, ...r } as Record<string, string | number>;
        return DB.prepare(`INSERT INTO view_events (${COLS.join(",")}) VALUES (${COLS.map(() => "?").join(",")})`).bind(
          ...COLS.map((c) => full[c] as string | number),
        );
      }),
    );
  }
}
const q = async <T>(sql: string) => (await DB.prepare(sql).all<T>()).results;
const meta = async () => Object.fromEntries((await q<{ key: string; value: string }>("SELECT * FROM view_meta")).map((m) => [m.key, m.value]));
const envOf = (over: Record<string, unknown> = {}) => ({ DB, TOP_SOURCES: "1000", MIN_CELL: "1", ...over });
const rawCount = async () => (await q<{ n: number }>("SELECT COUNT(*) AS n FROM view_events"))[0]?.n ?? 0;
const sumViews = async (t: string) => (await q<{ v: number }>(`SELECT COALESCE(SUM(views), 0) AS v FROM ${t}`))[0]?.v ?? 0;

function prng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

/** A D1 double that counts queries the way Cloudflare does: a batch() is one, each run/all/first/raw is one. */
function countingDb() {
  const stats = { queries: 0 };
  const real = new WeakMap<object, D1PreparedStatement>();
  const wrap = (st: D1PreparedStatement): D1PreparedStatement => {
    const p: D1PreparedStatement = new Proxy(st, {
      get(t, prop) {
        if (prop === "bind") return (...a: unknown[]) => wrap(t.bind(...a));
        if (prop === "run" || prop === "all" || prop === "first" || prop === "raw") {
          return (...a: unknown[]) => {
            stats.queries += 1;
            return (t as unknown as Record<string, (...x: unknown[]) => unknown>)[prop as string]!(...a);
          };
        }
        const v = Reflect.get(t, prop, t);
        return typeof v === "function" ? v.bind(t) : v;
      },
    });
    real.set(p, st);
    return p;
  };
  const db = new Proxy(DB, {
    get(t, prop) {
      if (prop === "prepare") return (sql: string) => wrap(t.prepare(sql));
      if (prop === "batch") {
        return (stmts: D1PreparedStatement[]) => {
          stats.queries += 1;
          return t.batch(stmts.map((s) => real.get(s) ?? s));
        };
      }
      if (prop === "exec") {
        return (sql: string) => {
          stats.queries += 1;
          return t.exec(sql);
        };
      }
      const v = Reflect.get(t, prop, t);
      return typeof v === "function" ? v.bind(t) : v;
    },
  }) as D1Database;
  return { db, stats };
}

beforeEach(clearAll);

describe("query meter", () => {
  it("budget_parsing_has_a_free_default_and_a_paid_ceiling", () => {
    expect(DEFAULT_QUERY_BUDGET).toBe(40);
    expect(MAX_QUERY_BUDGET).toBe(900);
    expect(queryBudget(undefined)).toBe(40);
    expect(queryBudget("abc")).toBe(40);
    expect(queryBudget("2")).toBe(40);
    expect(queryBudget("12")).toBe(12);
    expect(queryBudget("5000")).toBe(900);
    expect(queryBudget("900")).toBe(900);
  });

  it("every_call_counts_once_a_batch_counts_once_and_the_limit_throws_before_calling", async () => {
    const meter = new QueryMeter(3);
    const db = meteredDb(DB, meter);
    await db.prepare("SELECT 1").run();
    await db.prepare("SELECT ?").bind(1).first();
    await db.batch([db.prepare("SELECT 1"), db.prepare("SELECT 2"), db.prepare("SELECT 3")]);
    expect(meter.used).toBe(3);
    expect(meter.remaining()).toBe(0);
    await expect(db.prepare("SELECT 1").all()).rejects.toThrow(BudgetExceeded);
    expect(meter.used).toBe(3);
  });

  it("a_slice_counts_against_its_parent_and_is_held_to_its_own_limit", async () => {
    const parent = new QueryMeter(10);
    const child = parent.slice(2);
    const db = meteredDb(DB, child);
    await db.prepare("SELECT 1").run();
    await db.prepare("SELECT 1").run();
    await expect(db.prepare("SELECT 1").run()).rejects.toThrow(BudgetExceeded);
    expect(parent.used).toBe(2);
    const tight = new QueryMeter(10);
    tight.used = 9;
    expect(tight.slice(5).remaining()).toBe(1);
  });
});

describe("rollup under a hard query budget", () => {
  /** A worst-case day: every raw row has a value nobody else has, so every cap and fold is exercised. */
  function worstDay(dayStart: number, n: number, seed: number): Raw[] {
    const r = prng(seed);
    const out: Raw[] = [];
    for (let i = 0; i < n; i++) {
      out.push({
        ts: dayStart + Math.floor(r() * DAY),
        src_kind: i % 2 ? "r" : "u",
        src: `h${i}x${Math.floor(r() * 1e6)}`,
        country: `C${i % 200}`,
        region: `R${i % 900}`,
        city: `T${i}`,
        browser: `b${i}`,
        lang: `l${i}`,
        os: `o${i % 7}`,
        bot_class: ["h", "a", "s", "v"][i % 4],
        first: i % 3 === 0 ? 1 : 0,
      });
    }
    return out;
  }

  async function snapshot(): Promise<string> {
    const tables = ["view_daily", "view_dim_daily", "view_geo_daily"];
    const out: unknown[] = [];
    for (const t of tables) out.push(await q(`SELECT * FROM ${t} ORDER BY 1,2,3,4,5,6,7`));
    out.push(await q("SELECT day, raw_views, views, uniques, rows_core, rows_dim, rows_geo, max_id FROM view_rollup_log ORDER BY day"));
    return JSON.stringify(out);
  }

  async function invoke(env: Record<string, unknown>) {
    const pending: Promise<unknown>[] = [];
    const ctx = { waitUntil: (p: Promise<unknown>) => void pending.push(p) } as unknown as ExecutionContext;
    await worker.scheduled({} as ScheduledController, env as never, ctx);
    await Promise.all(pending);
  }

  it("no_invocation_exceeds_the_budget_and_repeated_invocations_converge_to_the_unbounded_result", async () => {
    const today = Math.floor(Date.now() / DAY) * DAY;
    await insert([
      ...worstDay(today - 5 * DAY, 3600, 1),
      ...worstDay(today - 4 * DAY, 3600, 2),
      ...worstDay(today - 3 * DAY, 60, 3),
      ...worstDay(today - 2 * DAY, 60, 4),
    ]);
    const total = await rawCount();
    expect(total).toBe(7320);

    // Tight budget: 8 queries per invocation.
    const c = countingDb();
    const envFor = (budget: string, db: D1Database) => ({ DB: db, SITE_HOST: "x.example.test", LINK_IDS: "[]", CLICK_LIMITER: fakeLimiter(), QUERY_BUDGET: budget });
    let runs = 0;
    while ((await q("SELECT 1 FROM view_rollup_log")).length < 4 && runs < 30) {
      c.stats.queries = 0;
      await invoke(envFor("8", c.db));
      expect(c.stats.queries).toBeLessThanOrEqual(8);
      runs += 1;
    }
    expect(runs).toBeGreaterThanOrEqual(4); // one day per tight invocation
    expect((await q("SELECT 1 FROM view_rollup_log")).length).toBe(4);
    const bounded = await snapshot();
    // Every raw row is counted exactly once in every table, on the worst-case days too.
    expect(await sumViews("view_daily")).toBe(total);
    expect(await sumViews("view_geo_daily")).toBe(total);
    expect(await sumViews("view_dim_daily")).toBe(total * 7);
    expect((await q<{ n: number }>("SELECT MAX(rows_core) AS n FROM view_rollup_log"))[0]?.n).toBeLessThanOrEqual(1500);

    // The same data in one default-budget invocation (40) gives the same aggregates.
    await DB.prepare("DELETE FROM view_daily").run();
    await DB.prepare("DELETE FROM view_dim_daily").run();
    await DB.prepare("DELETE FROM view_geo_daily").run();
    await DB.prepare("DELETE FROM view_rollup_log").run();
    c.stats.queries = 0;
    await invoke(envFor("40", c.db));
    expect(c.stats.queries).toBeLessThanOrEqual(40);
    expect(await snapshot()).toBe(bounded);
  }, 120_000);

  it("the_purge_alone_stays_inside_a_tiny_budget_and_continues_next_time", async () => {
    const today = Math.floor(Date.now() / DAY) * DAY;
    const rows: Raw[] = [];
    for (let i = 0; i < 400; i++) rows.push({ ts: today - 30 * DAY + i });
    await insert(rows);
    const c = countingDb();
    const env = { DB: c.db, QUERY_BUDGET: "5" };
    let runs = 0;
    while ((await rawCount()) > 0 && runs < 20) {
      c.stats.queries = 0;
      await purgeViews(env, Date.now(), { batch: 50, maxBatches: 200 });
      expect(c.stats.queries).toBeLessThanOrEqual(5);
      runs += 1;
    }
    expect(await rawCount()).toBe(0);
    expect(runs).toBeGreaterThan(1);
  });
});

describe("rollup lease", () => {
  function gatedDb(blockOnBatch: number) {
    let n = 0;
    let release!: () => void;
    let reached!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const hit = new Promise<void>((r) => (reached = r));
    const db = new Proxy(DB, {
      get(t, p) {
        if (p === "batch") {
          return async (stmts: D1PreparedStatement[]) => {
            n += 1;
            if (n === blockOnBatch) {
              reached();
              await gate;
            }
            return t.batch(stmts);
          };
        }
        const v = Reflect.get(t, p, t);
        return typeof v === "function" ? v.bind(t) : v;
      },
    }) as D1Database;
    return { db, hit, release };
  }
  const seed = async () => insert(Array.from({ length: 150 }, (_, i) => ({ ts: D13 + i * 1000, src_kind: "r", src: `h${i % 9}.example.test`, first: i % 2 })));

  it("a_second_overlapping_run_does_nothing_and_never_touches_the_first_runs_rows", async () => {
    await seed();
    const a = gatedDb(3); // discovery, day read, then the write batch is held
    const runA = rollupClosedDays(envOf({ DB: a.db }), NOW);
    await a.hit;
    const b = await rollupClosedDays(envOf(), NOW + 1000);
    expect(b).toMatchObject({ skipped: true, days: 0 });
    expect(await q("SELECT * FROM view_rollup_log")).toEqual([]);
    expect(await q("SELECT * FROM view_daily")).toEqual([]);
    a.release();
    expect(await runA).toMatchObject({ days: 1 });
    expect(await sumViews("view_daily")).toBe(150);
    expect(await q("SELECT * FROM view_rollup_log")).toMatchObject([{ raw_views: 150 }]);
    expect((await meta()).rollup_lease).toBeUndefined(); // released
  });

  it("a_run_whose_lease_expired_cannot_seal_or_delete_after_another_run_finished_the_day", async () => {
    await seed();
    const a = gatedDb(3);
    const runA = rollupClosedDays(envOf({ DB: a.db }), NOW);
    await a.hit;
    // The lease expires (a crashed or very slow run) and a new run takes over and finishes.
    await DB.prepare("UPDATE view_meta SET value = 'expired:1' WHERE key = 'rollup_lease'").run();
    const b = await rollupClosedDays(envOf(), NOW + 2000);
    expect(b).toMatchObject({ days: 1 });
    const before = JSON.stringify([await q("SELECT * FROM view_daily"), await q("SELECT * FROM view_rollup_log")]);
    a.release();
    await runA; // quietly gives up
    expect(JSON.stringify([await q("SELECT * FROM view_daily"), await q("SELECT * FROM view_rollup_log")])).toBe(before);
    expect(await sumViews("view_daily")).toBe(150);
    expect(await sumViews("view_dim_daily")).toBe(150 * 7);
    expect((await q("SELECT * FROM view_rollup_log")).length).toBe(1);
  });

  it("a_run_that_lost_its_lease_writes_nothing_even_though_the_day_is_still_open", async () => {
    await seed();
    const a = gatedDb(3);
    const runA = rollupClosedDays(envOf({ DB: a.db }), NOW);
    await a.hit;
    // Someone else now holds the lease but has not written anything yet.
    await DB.prepare("UPDATE view_meta SET value = 'another:99999999999999' WHERE key = 'rollup_lease'").run();
    a.release();
    await runA;
    expect(await q("SELECT * FROM view_rollup_log")).toEqual([]);
    expect(await q("SELECT * FROM view_daily")).toEqual([]);
    expect(await q("SELECT * FROM view_dim_daily")).toEqual([]);
    expect(await q("SELECT * FROM view_geo_daily")).toEqual([]);
    expect((await meta()).rollup_lease).toBe("another:99999999999999"); // not released by a non-owner
  });

  it("a_reroll_is_dropped_if_the_day_changed_under_it", async () => {
    const old = Math.floor((NOW - 10 * DAY) / DAY) * DAY;
    await insert([{ ts: old + 1000 }, { ts: old + 2000 }]);
    await rollupClosedDays(envOf(), NOW);
    await insert([{ ts: old + 3000 }]); // a late row
    const a = gatedDb(3);
    const run = rollupClosedDays(envOf({ DB: a.db }), NOW + 1000);
    await a.hit;
    // The day's counted max_id moves (another folder got there first).
    await DB.prepare("UPDATE view_rollup_log SET max_id = max_id + 1000").run();
    const before = JSON.stringify([await q("SELECT * FROM view_daily"), await q("SELECT views, uniques, rerolls FROM view_rollup_log")]);
    a.release();
    await run;
    expect(JSON.stringify([await q("SELECT * FROM view_daily"), await q("SELECT views, uniques, rerolls FROM view_rollup_log")])).toBe(before);
    expect(await sumViews("view_daily")).toBe(2);
  });

  it("a_fresh_day_that_got_sealed_in_between_is_left_alone_without_an_error", async () => {
    await seed();
    const a = gatedDb(3);
    const runA = rollupClosedDays(envOf({ DB: a.db }), NOW);
    await a.hit;
    // The lease is still valid and still mine, but the day's state is no longer "fresh".
    await DB.prepare("INSERT INTO view_rollup_log VALUES ('2027-03-13', 'UTC', 150, 150, 0, 1, 1, 1, 0, 99999, 0)").run();
    a.release();
    await expect(runA).resolves.toBeDefined(); // gives up quietly, no database error
    expect(await q("SELECT * FROM view_daily")).toEqual([]);
    expect((await meta()).rollup_err).toBeUndefined();
  });

  it("one_clock_reading_covers_every_guard_and_the_checkpoint_of_a_reroll_batch", async () => {
    await seed();
    await rollupClosedDays(envOf(), NOW);
    await insert([{ ts: D13 + 900_000 }]); // a late row, so the next run re-rolls the day
    // Our module's clock jumps 6 minutes on every reading; the lease lasts 10
    // minutes from the renewal. Two readings after the renewal would straddle
    // the expiry (guards pass, checkpoint fails after the rows were inserted).
    const real = Date.now.bind(Date);
    let n = 0;
    const base = real();
    const spy = vi.spyOn(Date, "now").mockImplementation(() => {
      if (!(new Error().stack ?? "").includes("viewrollup")) return real();
      n += 1;
      return base + n * 6 * 60_000;
    });
    try {
      const r = await rollupClosedDays(envOf(), NOW + 1000);
      expect(r.late).toBe(1);
    } finally {
      spy.mockRestore();
    }
    expect(n, "the stubbed clock was read by the rollup").toBeGreaterThanOrEqual(3);
    expect(await q("SELECT * FROM view_rollup_log")).toMatchObject([{ raw_views: 151, views: 151, rerolls: 1 }]);
    expect(await sumViews("view_daily")).toBe(151);
    expect(await sumViews("view_geo_daily")).toBe(151);
  });

  it("a_day_that_changed_under_the_run_leaves_no_live_owned_lease", async () => {
    await seed();
    const a = gatedDb(3);
    const runA = rollupClosedDays(envOf({ DB: a.db }), NOW);
    await a.hit;
    expect((await meta()).rollup_lease).toBeDefined(); // held while the write is pending
    await DB.prepare("INSERT INTO view_rollup_log VALUES ('2027-03-13', 'UTC', 150, 150, 0, 1, 1, 1, 0, 99999, 0)").run();
    a.release();
    await runA;
    expect((await meta()).rollup_lease).toBeUndefined(); // released on the lost-guard path
    expect(await q("SELECT * FROM view_daily")).toEqual([]);
  });

  it("rows_arriving_between_discovery_and_the_read_put_the_day_in_reduced_mode", async () => {
    await seed(); // 150 rows, no city
    const a = gatedDb(2); // hold the day's read batch
    const run = rollupClosedDays(envOf({ DB: a.db, ROLLUP_MAX_ROWS_PER_DAY: "200" }), NOW);
    await a.hit;
    await insert(Array.from({ length: 100 }, (_, i) => ({ ts: D13 + 200_000 + i, country: "US", region: "CA", city: "Sentinelville" })));
    a.release();
    await run;
    expect((await meta()).rollup_reduced_days).toBe("1");
    expect(await sumViews("view_daily")).toBe(250);
    expect(JSON.stringify(await q("SELECT * FROM view_geo_daily"))).not.toContain("Sentinelville");
  });

  it("the_invocation_input_budget_uses_the_recount_not_the_discovery_count", async () => {
    const d12 = D13 - DAY;
    await insert([...Array.from({ length: 300 }, (_, i) => ({ ts: d12 + i })), ...Array.from({ length: 50 }, (_, i) => ({ ts: D13 + i }))]);
    // Budget: 4 x 100 = 400 input rows. Day 1 takes 300; day 2 looked like 50 at discovery.
    const a = gatedDb(4); // hold day 2's read batch
    const run = rollupClosedDays(envOf({ DB: a.db, ROLLUP_MAX_ROWS_PER_DAY: "100" }), NOW);
    await a.hit;
    await insert(Array.from({ length: 400 }, (_, i) => ({ ts: D13 + 1000 + i })));
    a.release();
    const r = await run;
    expect(r.days).toBe(1); // day 2 recounted as 450 and waits for the next invocation
    expect((await q("SELECT day FROM view_rollup_log")).length).toBe(1);
    const next = await rollupClosedDays(envOf({ ROLLUP_MAX_ROWS_PER_DAY: "100" }), NOW + 1000);
    expect(next.days).toBe(1);
    expect(await sumViews("view_daily")).toBe(750);
  });

  it("an_expired_lease_with_no_takeover_writes_nothing", async () => {
    await seed();
    const a = gatedDb(3);
    const runA = rollupClosedDays(envOf({ DB: a.db }), NOW);
    await a.hit;
    // Same token, but its expiry has passed and nobody took over.
    await DB.prepare("UPDATE view_meta SET value = substr(value, 1, instr(value, ':')) || '1' WHERE key = 'rollup_lease'").run();
    a.release();
    await runA;
    expect(await q("SELECT * FROM view_rollup_log")).toEqual([]);
    expect(await q("SELECT * FROM view_daily")).toEqual([]);
    expect(await q("SELECT * FROM view_dim_daily")).toEqual([]);
  });

  it("an_expired_lease_is_taken_over_and_a_live_one_is_not", async () => {
    await seed();
    await DB.prepare("INSERT INTO view_meta VALUES ('rollup_lease', 'someone:" + (NOW + 60_000) + "')").run();
    expect(await rollupClosedDays(envOf(), NOW)).toMatchObject({ skipped: true });
    await DB.prepare("UPDATE view_meta SET value = 'someone:" + (NOW - 1) + "' WHERE key = 'rollup_lease'").run();
    expect(await rollupClosedDays(envOf(), NOW)).toMatchObject({ days: 1 });
  });
});

describe("grace period", () => {
  it("a_day_is_not_rolled_until_the_grace_after_midnight_has_passed", async () => {
    await insert([{ ts: D13 + 5000 }]);
    const before = Date.parse("2027-03-14T01:59:59Z");
    expect(await rollupClosedDays(envOf(), before)).toMatchObject({ days: 0 });
    expect(await q("SELECT * FROM view_rollup_log")).toEqual([]);
    expect(await rollupClosedDays(envOf(), Date.parse("2027-03-14T02:00:00Z"))).toMatchObject({ days: 1 });
  });

  it("grace_is_configurable_and_capped_at_twelve_hours", async () => {
    expect(graceMs(undefined)).toBe(2 * 3_600_000);
    expect(graceMs("0")).toBe(0);
    expect(graceMs("99")).toBe(12 * 3_600_000);
    expect(graceMs("junk")).toBe(2 * 3_600_000);
    await insert([{ ts: D13 + 5000 }]);
    expect(await rollupClosedDays(envOf({ ROLLUP_GRACE_HOURS: "99" }), Date.parse("2027-03-14T11:59:00Z"))).toMatchObject({ days: 0 });
    expect(await rollupClosedDays(envOf({ ROLLUP_GRACE_HOURS: "99" }), Date.parse("2027-03-14T12:00:00Z"))).toMatchObject({ days: 1 });
  });
});

describe("small cells", () => {
  it("min_cell_parsing_defaults_to_three_and_is_clamped", () => {
    expect(minCell(undefined)).toBe(3);
    expect(minCell("0")).toBe(3);
    expect(minCell("1")).toBe(1);
    expect(minCell("50")).toBe(50);
    expect(minCell("500")).toBe(50); // clamped to the ceiling
  });

  it("with_the_default_minimum_no_published_cell_is_small_except_the_other_buckets_and_totals_hold", async () => {
    for (const seed of [1, 2, 3, 4, 5]) {
      await clearAll();
      const r = prng(seed);
      const pick = <T>(a: T[]) => a[Math.floor(r() * a.length)] as T;
      const raw: Raw[] = [];
      for (let i = 0; i < 200; i++) {
        raw.push({
          ts: D13 + Math.floor(r() * DAY),
          src_kind: pick(["d", "r", "u"]),
          src: pick(["a", "b", "c", "d", "e", "f"]),
          country: pick(["US", "DE", "FR", "JP", "BR"]),
          browser: pick(["chrome", "firefox", "safari", "edge", "lynx"]),
          lang: pick(["en", "de", "fr", "es", "xx"]),
          os: pick(["linux", "windows", "macos", "ios", "haiku"]),
          bot_class: pick(["h", "h", "h", "a"]),
          first: r() < 0.5 ? 1 : 0,
        });
      }
      await insert(raw);
      await rollupClosedDays({ DB, TOP_SOURCES: "1000" }, NOW); // default MIN_CELL 3
      expect(await sumViews("view_daily")).toBe(200);
      expect(await sumViews("view_geo_daily")).toBe(200);
      expect(await sumViews("view_dim_daily")).toBe(200 * 7);
      const uniq = raw.reduce((a, x) => a + (x.first ?? 0), 0);
      expect((await q<{ u: number }>("SELECT SUM(uniques) AS u FROM view_daily"))[0]?.u).toBe(uniq);
      expect(await q("SELECT * FROM view_dim_daily WHERE views < 3 AND value <> 'other'")).toEqual([]);
      expect(await q("SELECT * FROM view_daily WHERE views < 3 AND country <> 'ZZ'")).toEqual([]);
    }
  });
});

describe("overdue flag", () => {
  const flag = async () => (await meta()).rollup_overdue;

  it("the_flag_is_set_when_unrolled_rows_are_dropped_and_cleared_by_a_later_clean_run", async () => {
    await insert([{ ts: NOW - 30 * DAY }]);
    await purgeViews({ DB }, NOW);
    expect(await rawCount()).toBe(0);
    expect(await flag()).toBe("2027-03-14");
    // Same day, nothing left: the flag stays visible for the rest of the UTC day.
    await purgeViews({ DB }, NOW + 1000);
    expect(await flag()).toBe("2027-03-14");
    // The next UTC day, with nothing beyond the ceiling, clears it.
    await purgeViews({ DB }, NOW + DAY);
    expect(await flag()).toBeUndefined();
  });

  it("the_flag_is_not_cleared_while_unrolled_rows_are_still_beyond_the_ceiling", async () => {
    await DB.prepare("INSERT INTO view_meta VALUES ('rollup_overdue', '2027-03-13')").run();
    await insert([{ ts: NOW - 30 * DAY }]);
    await purgeViews({ DB }, NOW);
    expect(await flag()).toBe("2027-03-14"); // refreshed, not cleared
  });
});
