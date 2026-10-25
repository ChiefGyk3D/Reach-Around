// SPDX-License-Identifier: Apache-2.0
import { beforeEach, describe, expect, it } from "vitest";
import worker from "../../src/index";
import { purgeViews } from "../../src/viewretention";
import {
  MAX_DAYS_PER_RUN,
  ROW_CAP_CORE,
  capDimValues,
  capSources,
  foldGeo,
  rollupClosedDays,
} from "../../src/viewrollup";
import { handleView } from "../../src/view";
import { CHROME_UA, DB, IP4, SECRET, beacon, clearAll, dumpDatabase, fakeLimiter, makeEnv, spyDb } from "./helpers";

const DAY = 86_400_000;
const NOW = Date.parse("2027-03-14T12:00:30Z"); // day 2027-03-14 is open
const D13 = Date.parse("2027-03-13T00:00:00Z");
const D12 = Date.parse("2027-03-12T00:00:00Z");

interface Raw {
  ts: number;
  page?: string;
  src_kind?: string;
  src?: string;
  country?: string;
  region?: string;
  city?: string;
  browser?: string;
  bmajor?: number;
  os?: string;
  device?: string;
  lang?: string;
  vp?: string;
  net?: string;
  bot_class?: string;
  reason?: string;
  first?: number;
}

const COLS = [
  "ts", "page", "src_kind", "src", "country", "region", "city", "browser", "bmajor", "os", "device", "lang", "vp", "net",
  "bot_class", "reason", "first",
] as const;
const DEFAULTS: Required<Raw> = {
  ts: 0, page: "home", src_kind: "d", src: "", country: "US", region: "", city: "", browser: "chrome", bmajor: 128,
  os: "linux", device: "desktop", lang: "en", vp: "l", net: "residential", bot_class: "h", reason: "ok", first: 0,
};

async function insert(list: Raw[]) {
  for (let i = 0; i < list.length; i += 20) {
    await DB.batch(
      list.slice(i, i + 20).map((r) => {
        const full = { ...DEFAULTS, ...r };
        return DB.prepare(
          `INSERT INTO view_events (${COLS.join(",")}) VALUES (${COLS.map(() => "?").join(",")})`,
        ).bind(...COLS.map((c) => full[c]));
      }),
    );
  }
}

const q = async <T>(sql: string) => (await DB.prepare(sql).all<T>()).results;
const meta = async () => Object.fromEntries((await q<{ key: string; value: string }>("SELECT * FROM view_meta")).map((m) => [m.key, m.value]));
const envOf = (over: Record<string, unknown> = {}) => ({ DB, TOP_SOURCES: "1000", ...over });

/** Small seeded PRNG so a failure is reproducible. */
function prng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

function randomRows(seed: number, n: number): Raw[] {
  const r = prng(seed);
  const pick = <T>(a: T[]) => a[Math.floor(r() * a.length)] as T;
  const out: Raw[] = [];
  for (let i = 0; i < n; i++) {
    const kind = pick(["d", "i", "r", "u"]);
    const country = pick(["US", "DE", "FR", "JP"]);
    out.push({
      ts: (r() < 0.5 ? D12 : D13) + Math.floor(r() * DAY),
      page: pick(["home", "support"]),
      src_kind: kind,
      src: kind === "r" ? pick(["a.example.test", "b.example.test", "c.example.test"]) : kind === "u" ? pick(["bluesky", "mail"]) : "",
      country,
      region: pick(["", "R1", "R2"]),
      city: pick(["", "C1", "C2", "C3"]),
      browser: pick(["chrome", "firefox", "safari", "edge"]),
      os: pick(["linux", "windows", "macos", "ios"]),
      device: pick(["desktop", "mobile", "tablet"]),
      lang: pick(["en", "de", "fr", ""]),
      vp: pick(["s", "m", "l", "x"]),
      net: pick(["residential", "mobile", "hosting-datacenter"]),
      bot_class: pick(["h", "h", "h", "a", "s", "v"]),
      reason: pick(["ok", "ua_bot", "net_dc"]),
      first: r() < 0.4 ? 1 : 0,
    });
  }
  return out;
}

function group<T extends object>(rows: T[], keyOf: (r: T) => string[], first: (r: T) => number) {
  const m = new Map<string, { views: number; uniques: number }>();
  for (const r of rows) {
    const k = keyOf(r).join("|");
    const c = m.get(k) ?? { views: 0, uniques: 0 };
    c.views += 1;
    c.uniques += first(r);
    m.set(k, c);
  }
  return m;
}

const day = (ts: number) => new Date(ts).toISOString().slice(0, 10);

beforeEach(clearAll);

describe("rollup totals", () => {
  it("totals_per_dimension_equal_a_brute_force_count_over_random_rows", async () => {
    for (const seed of [1, 2, 3, 4, 5, 6, 7, 8]) {
      await clearAll();
      const raw = randomRows(seed, 150).map((r) => ({ ...DEFAULTS, ...r }));
      await insert(raw);
      await rollupClosedDays(envOf(), NOW);

      const core = new Map(
        (await q<{ day: string; page: string; src_kind: string; src: string; country: string; bot_class: string; views: number; uniques: number }>(
          "SELECT * FROM view_daily",
        )).map((r) => [[r.day, r.page, r.src_kind, r.src, r.country, r.bot_class].join("|"), r]),
      );
      const expectCore = group(raw, (r) => [day(r.ts), r.page, r.src_kind, r.src, r.country, r.bot_class], (r) => r.first);
      expect(core.size).toBe(expectCore.size);
      for (const [k, v] of expectCore) expect(core.get(k)).toMatchObject(v);

      const dims = await q<{ day: string; dim: string; value: string; bot_class: string; views: number; uniques: number }>(
        "SELECT * FROM view_dim_daily",
      );
      for (const dim of ["browser", "os", "device", "lang", "vp", "net", "reason"] as const) {
        const exp = group(raw, (r) => [day(r.ts), r[dim], r.bot_class], (r) => r.first);
        const got = dims.filter((d) => d.dim === dim);
        expect(got.length).toBe(exp.size);
        for (const g of got) expect(exp.get([g.day, g.value, g.bot_class].join("|"))).toMatchObject({ views: g.views, uniques: g.uniques });
      }

      // Geography is folded for small counts, so compare what folding preserves.
      const geo = await q<{ day: string; country: string; bot_class: string; views: number; uniques: number }>("SELECT * FROM view_geo_daily");
      const expGeo = group(raw, (r) => [day(r.ts), r.country, r.bot_class], (r) => r.first);
      const gotGeo = group(
        geo.flatMap((g) => Array.from({ length: g.views }, (_, i) => ({ ...g, first: i < g.uniques ? 1 : 0 }))),
        (g) => [g.day, g.country, g.bot_class],
        (g) => g.first,
      );
      expect(Object.fromEntries(gotGeo)).toEqual(Object.fromEntries(expGeo));

      const log = await q<{ day: string; raw_views: number; views: number; uniques: number }>("SELECT * FROM view_rollup_log");
      for (const l of log) {
        const n = raw.filter((r) => day(r.ts) === l.day);
        expect(l.raw_views).toBe(n.length);
        expect(l.views).toBe(n.length);
        expect(l.uniques).toBe(n.reduce((a, r) => a + r.first, 0));
      }
      expect(log.length).toBe(2);
    }
  });

  it("uniques_are_the_first_flags_and_each_table_sums_to_the_raw_count", async () => {
    await insert([
      { ts: D13 + 1000, first: 1 },
      { ts: D13 + 2000, first: 0 },
      { ts: D13 + 3000, first: 1, src_kind: "r", src: "n.example.test" },
      { ts: D13 + 4000, first: 0, src_kind: "r", src: "n.example.test" },
    ]);
    await rollupClosedDays(envOf(), NOW);
    const sums = await q<{ t: string; v: number; u: number }>(
      "SELECT 'core' AS t, SUM(views) AS v, SUM(uniques) AS u FROM view_daily UNION ALL " +
        "SELECT 'geo', SUM(views), SUM(uniques) FROM view_geo_daily UNION ALL " +
        "SELECT 'browser', SUM(views), SUM(uniques) FROM view_dim_daily WHERE dim = 'browser'",
    );
    for (const s of sums) expect([s.t, s.v, s.u]).toEqual([s.t, 4, 2]);
    const log = await q<{ raw_views: number; uniques: number }>("SELECT * FROM view_rollup_log");
    expect(log).toMatchObject([{ raw_views: 4, uniques: 2 }]);
  });

  it("bot_classes_are_separate_rows_and_never_added_into_the_human_row", async () => {
    await insert([
      { ts: D13 + 1, bot_class: "h", first: 1 },
      { ts: D13 + 2, bot_class: "a", reason: "ua_script" },
      { ts: D13 + 3, bot_class: "a", reason: "ua_script" },
      { ts: D13 + 4, bot_class: "s", reason: "net_dc" },
      { ts: D13 + 5, bot_class: "v", reason: "se_verified" },
    ]);
    await rollupClosedDays(envOf(), NOW);
    const core = Object.fromEntries(
      (await q<{ bot_class: string; views: number }>("SELECT bot_class, SUM(views) AS views FROM view_daily GROUP BY bot_class")).map((r) => [r.bot_class, r.views]),
    );
    expect(core).toEqual({ h: 1, a: 2, s: 1, v: 1 });
    const dimRows = await q<{ bot_class: string; views: number }>("SELECT * FROM view_dim_daily WHERE dim = 'os'");
    expect(dimRows.length).toBe(4);
    expect(dimRows.find((r) => r.bot_class === "h")?.views).toBe(1);
  });
});

describe("rollup geography", () => {
  const geoRows = (): Raw[] =>
    Array.from({ length: 12 }, (_, i) => ({ ts: D13 + i, country: "US", region: "CA", city: "Sentinelville" }));

  it("city_and_region_are_absent_when_GEO_LEVEL_is_country", async () => {
    await insert(geoRows());
    await rollupClosedDays(envOf({ GEO_LEVEL: "country" }), NOW);
    const geo = await q<{ region: string; city: string; views: number }>("SELECT * FROM view_geo_daily");
    expect(geo).toMatchObject([{ region: "", city: "", views: 12 }]);
    expect(await dumpAggregates()).not.toMatch(/Sentinelville|"CA"/);
  });

  it("city_is_absent_when_GEO_LEVEL_is_region_and_present_at_city_with_enough_views", async () => {
    await insert(geoRows());
    await rollupClosedDays(envOf({ GEO_LEVEL: "region" }), NOW);
    expect(await q("SELECT * FROM view_geo_daily")).toMatchObject([{ region: "CA", city: "" }]);
    await clearAll();
    await insert(geoRows());
    await rollupClosedDays(envOf({ GEO_LEVEL: "city" }), NOW);
    expect(await q("SELECT * FROM view_geo_daily")).toMatchObject([{ region: "CA", city: "Sentinelville", views: 12 }]);
  });

  it("a_city_with_four_views_folds_into_its_region_and_a_small_region_into_its_country", () => {
    const g = (city: string, region: string, views: number) => ({ country: "US", region, city, bot_class: "h", views, uniques: 1 });
    const out = foldGeo([g("A", "R1", 4), g("B", "R1", 5), g("C", "R2", 2)]);
    const keys = out.map((r) => `${r.region}/${r.city}/${r.views}`).sort();
    // A (4) folds into R1, which then has 4 < 5 and folds into the country with C (2); B (5) stays.
    expect(keys).toEqual(["//6", "R1/B/5"].sort());
  });
});

describe("rollup caps", () => {
  it("sources_beyond_the_top_n_fold_to_other_and_direct_internal_never_fold", () => {
    const c = (src_kind: string, src: string, views: number) => ({ page: "home", src_kind, src, country: "US", bot_class: "h", views, uniques: 0 });
    const out = capSources([c("r", "a", 10), c("r", "b", 9), c("u", "c", 8), c("r", "d", 1), c("d", "", 1), c("i", "", 1)], 2);
    expect(out.map((r) => `${r.src_kind}:${r.src}:${r.views}`).sort()).toEqual(["d::1", "i::1", "o:other:9", "r:a:10", "r:b:9"].sort());
  });

  it("top_n_ties_break_by_name_and_lang_browser_fold_to_other", () => {
    const d = (value: string, views: number) => ({ dim: "lang", value, bot_class: "h", views, uniques: 0 });
    const out = capDimValues([d("zz", 3), d("aa", 3), d("mm", 3)], "lang", 2);
    expect(out.map((r) => r.value).sort()).toEqual(["aa", "mm", "other"].sort());
    expect(out.reduce((a, r) => a + r.views, 0)).toBe(9);
  });

  it("a_flood_of_random_referrers_stays_inside_the_row_cap_and_keeps_the_sum", async () => {
    const r = prng(99);
    const list: Raw[] = [];
    for (let i = 0; i < 1200; i++) list.push({ ts: D13 + i, src_kind: "r", src: `h${Math.floor(r() * 1e9)}.example.test`, country: ["US", "DE", "FR"][i % 3] });
    await insert(list);
    await rollupClosedDays({ DB, TOP_SOURCES: "5" }, NOW);
    const core = await q<{ src_kind: string; src: string; views: number }>("SELECT * FROM view_daily");
    expect(core.reduce((a, c) => a + c.views, 0)).toBe(1200);
    const named = new Set(core.filter((c) => c.src_kind === "r").map((c) => c.src));
    expect(named.size).toBeLessThanOrEqual(5);
    expect(core.length).toBeLessThanOrEqual(ROW_CAP_CORE);
    expect(core.some((c) => c.src_kind === "o" && c.src === "other")).toBe(true);
  });
});

describe("rollup idempotency, resume and heartbeat", () => {
  it("running_twice_never_double_counts_and_the_second_run_writes_only_the_heartbeat", async () => {
    await insert(randomRows(11, 80).map((r) => ({ ...r, ts: D13 + (r.ts % DAY) })));
    const first = await rollupClosedDays(envOf(), NOW);
    expect(first.days).toBe(1);
    const snap = async () => JSON.stringify([await q("SELECT * FROM view_daily"), await q("SELECT * FROM view_dim_daily"), await q("SELECT * FROM view_geo_daily"), await q("SELECT * FROM view_rollup_log")]);
    const before = await snap();
    const spy = spyDb();
    const second = await rollupClosedDays(envOf({ DB: spy.DB }), NOW + 1000);
    expect(second).toEqual({ days: 0, rows: 0 });
    expect(await snap()).toBe(before);
    expect(spy.calls.batch).toBe(0); // no aggregate write at all
  });

  it("an_interrupted_run_resumes_without_double_counting", async () => {
    const raw = randomRows(21, 120).map((r) => ({ ...r, ts: D13 + (r.ts % DAY) }));
    await insert(raw);
    const flaky = new Proxy(DB, {
      get(t, p) {
        if (p === "prepare") {
          return (sql: string) =>
            sql.startsWith("INSERT INTO view_rollup_log")
              ? ({ bind: () => ({ run: async () => { throw new Error("simulated crash before the checkpoint"); } }) } as unknown as D1PreparedStatement)
              : t.prepare(sql);
        }
        const v = Reflect.get(t, p, t);
        return typeof v === "function" ? v.bind(t) : v;
      },
    }) as D1Database;
    await expect(rollupClosedDays(envOf({ DB: flaky }), NOW)).rejects.toThrow();
    // Partial aggregates may exist, but the day is not sealed.
    expect(await q("SELECT * FROM view_rollup_log")).toEqual([]);
    expect((await q("SELECT * FROM view_daily")).length).toBeGreaterThan(0);
    const m1 = await meta();
    expect(m1.rollup_err).toBe("db");
    expect(m1.rollup_last_ok).toBeUndefined();

    await rollupClosedDays(envOf(), NOW + 5000);
    const total = await q<{ v: number }>("SELECT SUM(views) AS v FROM view_daily");
    expect(total[0]?.v).toBe(120);
    expect((await q<{ v: number }>("SELECT SUM(views) AS v FROM view_dim_daily WHERE dim = 'os'"))[0]?.v).toBe(120);
    expect((await q<{ v: number }>("SELECT SUM(views) AS v FROM view_geo_daily"))[0]?.v).toBe(120);
    expect(await q("SELECT * FROM view_rollup_log")).toMatchObject([{ raw_views: 120, views: 120 }]);
    expect((await meta()).rollup_err).toBe("none");
  });

  it("heartbeat_records_time_days_rows_and_a_closed_error_class", async () => {
    await insert([{ ts: D12 + 1 }, { ts: D13 + 1 }]);
    const res = await rollupClosedDays(envOf(), NOW);
    const m = await meta();
    expect(m.rollup_last_ok).toBe(String(NOW));
    expect(m.rollup_days).toBe("2");
    expect(m.rollup_rows).toBe(String(res.rows));
    expect(m.rollup_err).toBe("none");
    expect(m.rolled_through).toBe("2027-03-13");
    // Values are digits or a closed word, never free text.
    for (const k of ["rollup_last_ok", "rollup_days", "rollup_rows", "rollup_err", "rolled_through"]) {
      expect(m[k]).toMatch(/^[0-9a-z-]{1,20}$/);
    }
  });

  it("a_failure_keeps_the_old_last_ok_and_sets_the_error_class", async () => {
    await insert([{ ts: D13 + 1 }]);
    await rollupClosedDays(envOf(), NOW);
    await insert([{ ts: D12 + 1 }]);
    const broken = new Proxy(DB, {
      get(t, p) {
        if (p === "batch") return async () => { throw new Error("secret text must not be stored: person@example.test"); };
        const v = Reflect.get(t, p, t);
        return typeof v === "function" ? v.bind(t) : v;
      },
    }) as D1Database;
    await expect(rollupClosedDays(envOf({ DB: broken }), NOW + 9999)).rejects.toThrow();
    const m = await meta();
    expect(m.rollup_last_ok).toBe(String(NOW));
    expect(m.rollup_err).toBe("db");
    expect(JSON.stringify(m)).not.toContain("person@example.test");
  });

  it("only_closed_utc_days_are_rolled_and_today_is_left_alone", async () => {
    await insert([{ ts: Date.parse("2027-03-14T00:00:00Z") }, { ts: D13 + DAY - 1 }]);
    await rollupClosedDays(envOf(), NOW);
    expect((await q<{ day: string }>("SELECT day FROM view_rollup_log")).map((r) => r.day)).toEqual(["2027-03-13"]);
  });

  it("a_backlog_longer_than_the_per_run_limit_continues_on_the_next_run", async () => {
    const list: Raw[] = [];
    for (let i = 1; i <= MAX_DAYS_PER_RUN + 3; i++) list.push({ ts: NOW - (i + 1) * DAY });
    await insert(list);
    const a = await rollupClosedDays(envOf(), NOW);
    expect(a.days).toBe(MAX_DAYS_PER_RUN);
    const b = await rollupClosedDays(envOf(), NOW);
    expect(b.days).toBe(3);
  });
});

describe("rollup budget", () => {
  it("rows_written_for_N_raw_rows_stay_inside_the_documented_bound", async () => {
    const N = 300;
    await insert(randomRows(5, N).map((r) => ({ ...r, ts: D13 + (r.ts % DAY) })));
    const spy = spyDb();
    const res = await rollupClosedDays(envOf({ DB: spy.DB }), NOW);
    // Documented: at most one core row, one geo row and seven dimension rows
    // per raw row, plus one log row and the heartbeat (5 keys).
    expect(res.rows).toBeLessThanOrEqual(N * 9 + 1);
    // Tighter on real distributions: the random generator has few distinct values.
    expect(res.rows).toBeLessThan(N);
    expect(spy.calls.rowsWritten).toBeLessThanOrEqual(res.rows + 5);
    expect(spy.calls.rowsWritten).toBeGreaterThanOrEqual(res.rows - 1); // the log row is a plain run(), not part of a batch
  });

  it("writes_go_out_in_bounded_batches", async () => {
    const r = prng(3);
    const list: Raw[] = [];
    for (let i = 0; i < 400; i++) list.push({ ts: D13 + i, src_kind: "r", src: `h${i}.example.test`, city: `c${i}`, region: `r${i % 40}`, country: `C${i % 50}` });
    await insert(list);
    void r;
    const sizes: number[] = [];
    const spy = spyDb((stmts) => {
      sizes.push(stmts.length);
      return stmts;
    });
    await rollupClosedDays({ DB: spy.DB, TOP_SOURCES: "1000" }, NOW);
    expect(Math.max(...sizes)).toBeLessThanOrEqual(25);
    expect(sizes.length).toBeGreaterThan(2);
  });
});

async function dumpAggregates(): Promise<string> {
  const t = ["view_daily", "view_dim_daily", "view_geo_daily", "view_rollup_log", "view_meta"];
  const out: unknown[] = [];
  for (const n of t) out.push(await q(`SELECT * FROM ${n}`));
  return JSON.stringify(out);
}

describe("rollup privacy", () => {
  it("no_address_or_user_agent_reaches_any_aggregate_table", async () => {
    const env = makeEnv({ VIEW_LIMITER: fakeLimiter() });
    const t0 = Date.parse("2027-03-13T10:00:00Z");
    for (let i = 0; i < 6; i++) {
      const res = await handleView(
        beacon({ headers: { "CF-Connecting-IP": `198.51.100.${10 + i}`, "User-Agent": `${CHROME_UA} SentinelUA${i}` } }),
        env,
        t0 + i * 60_000,
      );
      expect(res.status).toBe(204);
    }
    const stored = (await q<{ n: number }>("SELECT COUNT(*) AS n FROM view_events"))[0]?.n;
    expect(stored).toBe(6); // the route recorded them, so the scan below is meaningful
    await rollupClosedDays({ DB, TOP_SOURCES: "50" }, NOW);
    const dump = await dumpAggregates();
    expect(dump.length).toBeGreaterThan(100);
    for (const bad of [IP4, "198.51.100", "SentinelUA", "Mozilla", "6613", "x86_64", SECRET]) {
      expect(dump).not.toContain(bad);
    }
    // And no browser major number: the aggregate tables have no such column.
    const cols = (await q<{ name: string }>("SELECT name FROM pragma_table_info('view_dim_daily')")).map((c) => c.name);
    expect(cols).not.toContain("bmajor");
    // The whole database, including raw rows, never held the address either.
    expect(await dumpDatabase()).not.toContain(IP4);
  });

  it("no_aggregate_table_combines_dimensions", async () => {
    const dims = ["browser", "os", "device", "lang", "vp", "net", "city"];
    for (const t of ["view_daily", "view_dim_daily", "view_geo_daily"]) {
      const cols = (await q<{ name: string }>(`SELECT name FROM pragma_table_info('${t}')`)).map((c) => c.name);
      expect(cols.filter((c) => dims.includes(c)).length).toBeLessThanOrEqual(1);
    }
  });
});

describe("retention order", () => {
  const ageDay = (daysAgo: number) => Date.parse("2027-03-14T12:00:00Z") - daysAgo * DAY;
  const rawCount = async () => (await q<{ n: number }>("SELECT COUNT(*) AS n FROM view_events"))[0]?.n ?? 0;

  it("an_unrolled_day_older_than_the_keep_window_is_not_deleted", async () => {
    await insert([{ ts: ageDay(10) }]);
    await purgeViews({ DB }, NOW);
    expect(await rawCount()).toBe(1);
  });

  it("a_rolled_day_older_than_the_keep_window_is_deleted_and_a_recent_rolled_day_stays", async () => {
    await insert([{ ts: ageDay(10) }, { ts: ageDay(2) }]);
    await rollupClosedDays(envOf(), NOW);
    await purgeViews({ DB }, NOW);
    expect((await q<{ ts: number }>("SELECT ts FROM view_events")).map((r) => r.ts)).toEqual([ageDay(2)]);
  });

  it("unrolled_rows_past_three_times_the_keep_window_are_dropped_and_flagged", async () => {
    await insert([{ ts: ageDay(22) }, { ts: ageDay(15) }]);
    await purgeViews({ DB }, NOW);
    expect((await q<{ ts: number }>("SELECT ts FROM view_events")).map((r) => r.ts)).toEqual([ageDay(15)]);
    expect((await meta()).rollup_overdue).toBe("2027-03-14");
  });

  it("rollup_then_purge_in_one_scheduled_run_counts_before_it_deletes", async () => {
    const now = Date.now();
    await insert([{ ts: now - 9 * DAY, first: 1 }, { ts: now - 9 * DAY + 1000, first: 1 }]);
    const pending: Promise<unknown>[] = [];
    const ctx = { waitUntil: (p: Promise<unknown>) => void pending.push(p) } as unknown as ExecutionContext;
    await worker.scheduled({} as ScheduledController, { DB, SITE_HOST: "x.example.test", LINK_IDS: "[]", CLICK_LIMITER: fakeLimiter() }, ctx);
    await Promise.all(pending);
    expect(await rawCount()).toBe(0);
    expect((await q<{ v: number; u: number }>("SELECT SUM(views) AS v, SUM(uniques) AS u FROM view_daily"))[0]).toEqual({ v: 2, u: 2 });
  });

  it("when_the_rollup_fails_the_raw_rows_survive_the_purge_and_nothing_throws", async () => {
    const now = Date.now();
    await insert([{ ts: now - 9 * DAY }]);
    const noRollup = new Proxy(DB, {
      get(t, p) {
        if (p === "batch") return async () => { throw new Error("down"); };
        const v = Reflect.get(t, p, t);
        return typeof v === "function" ? v.bind(t) : v;
      },
    }) as D1Database;
    const pending: Promise<unknown>[] = [];
    const ctx = { waitUntil: (p: Promise<unknown>) => void pending.push(p) } as unknown as ExecutionContext;
    await worker.scheduled({} as ScheduledController, { DB: noRollup, SITE_HOST: "x.example.test", LINK_IDS: "[]", CLICK_LIMITER: fakeLimiter() }, ctx);
    await expect(Promise.all(pending)).resolves.toBeDefined();
    expect(await rawCount()).toBe(1);
    expect((await meta()).rollup_err).toBe("db");
  });

  it("visitor_seen_for_an_older_day_is_kept_until_that_day_is_rolled_up", async () => {
    await DB.prepare("INSERT INTO visitor_seen (day, h) VALUES ('2027-03-13', 'aaaaaaaa'), ('2027-03-14', 'bbbbbbbb')").run();
    await insert([{ ts: D13 + 5, first: 1 }]);
    await purgeViews({ DB }, NOW);
    expect((await q<{ day: string }>("SELECT day FROM visitor_seen")).map((r) => r.day)).toEqual(["2027-03-13", "2027-03-14"]);
    await rollupClosedDays(envOf(), NOW);
    await purgeViews({ DB }, NOW);
    expect((await q<{ day: string }>("SELECT day FROM visitor_seen")).map((r) => r.day)).toEqual(["2027-03-14"]);
  });

  it("visitor_seen_older_than_the_hard_bound_goes_even_if_never_rolled_up", async () => {
    await DB.prepare("INSERT INTO visitor_seen (day, h) VALUES ('2027-03-11', 'aaaaaaaa')").run();
    await purgeViews({ DB }, NOW);
    expect(await q("SELECT * FROM visitor_seen")).toEqual([]);
  });

  it("aggregates_older_than_90_days_are_deleted", async () => {
    await DB.prepare("INSERT INTO view_rollup_log VALUES ('2026-12-01','UTC',1,1,0,1,1,1,0), ('2027-03-10','UTC',1,1,0,1,1,1,0)").run();
    await DB.prepare("INSERT INTO view_geo_daily VALUES ('2026-12-01','UTC','US','','','h',1,0), ('2027-03-10','UTC','US','','','h',1,0)").run();
    await purgeViews({ DB }, NOW);
    expect((await q<{ day: string }>("SELECT day FROM view_rollup_log")).map((r) => r.day)).toEqual(["2027-03-10"]);
    expect((await q<{ day: string }>("SELECT day FROM view_geo_daily")).map((r) => r.day)).toEqual(["2027-03-10"]);
  });
});
