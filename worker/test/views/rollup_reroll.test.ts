// SPDX-License-Identifier: Apache-2.0
// Full-day re-roll for late rows, SQL-side folding checked against an oracle,
// the UTM allowlist and the oversized-day mode. Synthetic data only.
import { beforeEach, describe, expect, it } from "vitest";
import { purgeViews } from "../../src/viewretention";
import { DIM_TOP, ROW_CAP_CORE, maxRowsPerDay, rollupClosedDays, utmAllowlist } from "../../src/viewrollup";
import { DB, clearAll } from "./helpers";
import { type RawRow, canon, oracleCore, oracleDim, oracleGeo } from "./oracle";

const DAY = 86_400_000;
const NOW = Date.parse("2027-03-14T12:00:30Z");
const D13 = Date.parse("2027-03-13T00:00:00Z");
const day = (ms: number) => new Date(ms).toISOString().slice(0, 10);

type R = Partial<RawRow> & { ts: number };
const COLS = ["ts", "page", "src_kind", "src", "country", "region", "city", "browser", "bmajor", "os", "device", "lang", "vp", "net", "bot_class", "reason", "first"];
const DEF = {
  page: "home", src_kind: "d", src: "", country: "US", region: "", city: "", browser: "chrome", bmajor: 1, os: "linux",
  device: "desktop", lang: "en", vp: "l", net: "residential", bot_class: "h", reason: "ok", first: 0,
};

async function insert(list: R[]) {
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
const sum = async (t: string) => (await q<{ v: number }>(`SELECT COALESCE(SUM(views), 0) AS v FROM ${t}`))[0]?.v ?? 0;
const rawCount = async () => (await q<{ n: number }>("SELECT COUNT(*) AS n FROM view_events"))[0]?.n ?? 0;

function prng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

async function snapshot(): Promise<string> {
  const out: unknown[] = [];
  for (const t of ["view_daily", "view_dim_daily", "view_geo_daily"]) out.push(await q(`SELECT * FROM ${t} ORDER BY 1,2,3,4,5,6,7`));
  out.push(await q("SELECT day, raw_views, views, uniques, rows_core, rows_dim, rows_geo, max_id FROM view_rollup_log ORDER BY day"));
  return JSON.stringify(out);
}

/** Drop every aggregate and log row but keep the raw rows, then roll from scratch. */
async function scratchSnapshot(env: Record<string, unknown>, at: number): Promise<string> {
  for (const t of ["view_daily", "view_dim_daily", "view_geo_daily", "view_rollup_log"]) await DB.prepare(`DELETE FROM ${t}`).run();
  await rollupClosedDays({ DB, ...env }, at);
  return snapshot();
}

const mk = (n: number, base: Partial<R>, start = 0): R[] => Array.from({ length: n }, (_, i) => ({ ts: D13 + start + i, ...base }));

beforeEach(clearAll);

describe("late rows re-roll the whole day", () => {
  it("a_folded_cell_that_reaches_the_minimum_after_a_late_row_becomes_a_named_cell", async () => {
    const env = { TOP_SOURCES: "1000" }; // MIN_CELL default 3
    await insert([...mk(2, { src_kind: "r", src: "rare.example.test" }), ...mk(5, { src_kind: "r", src: "big.example.test" }, 100)]);
    await rollupClosedDays({ DB, ...env }, NOW);
    expect(await q("SELECT * FROM view_daily WHERE src = 'rare.example.test'")).toEqual([]); // folded into other
    expect((await q<{ views: number }>("SELECT views FROM view_daily WHERE src = 'other'"))[0]?.views).toBe(2);

    await insert([{ ts: D13 + 500, src_kind: "r", src: "rare.example.test" }]); // the late row: now 3 views
    const r = await rollupClosedDays({ DB, ...env }, NOW + 1000);
    expect(r.late).toBe(1);
    expect((await q<{ views: number }>("SELECT views FROM view_daily WHERE src = 'rare.example.test'"))[0]?.views).toBe(3);
    expect(await q("SELECT * FROM view_daily WHERE src = 'other'")).toEqual([]); // nothing left to fold
    expect(await sum("view_daily")).toBe(8);
    const after = await snapshot();
    expect(await scratchSnapshot(env, NOW + 2000)).toBe(after);
  });

  it("a_named_cell_stays_named_and_a_top_n_change_demotes_the_one_that_falls_out", async () => {
    const env = { TOP_SOURCES: "2", MIN_CELL: "1" };
    await insert([...mk(5, { src_kind: "r", src: "a.example.test" }), ...mk(4, { src_kind: "r", src: "b.example.test" }, 50), ...mk(3, { src_kind: "r", src: "c.example.test" }, 100)]);
    await rollupClosedDays({ DB, ...env }, NOW);
    const names = async () => (await q<{ src: string; views: number }>("SELECT src, SUM(views) AS views FROM view_daily GROUP BY src")).map((r) => `${r.src}:${r.views}`).sort();
    expect(await names()).toEqual(["a.example.test:5", "b.example.test:4", "other:3"]);
    await insert(mk(3, { src_kind: "r", src: "c.example.test" }, 200)); // c: 6 views, b falls out of the top 2
    await insert([{ ts: D13 + 300, src_kind: "r", src: "a.example.test" }]); // a stays named, 6
    await rollupClosedDays({ DB, ...env }, NOW + 1000);
    expect(await names()).toEqual(["a.example.test:6", "c.example.test:6", "other:4"]);
    expect(await scratchSnapshot(env, NOW + 2000)).toBe(await snapshot());
  });

  it("the_hard_row_cap_is_applied_to_the_whole_day_after_a_late_row_changes_the_ranking", async () => {
    const env = { TOP_SOURCES: "1000", MIN_CELL: "1" };
    const rows: R[] = [];
    for (let i = 0; i < 700; i++) rows.push({ ts: D13 + i, src_kind: "r", src: `h${i}.example.test`, country: `C${i % 3}` });
    await insert(rows);
    await rollupClosedDays({ DB, ...env }, NOW);
    expect((await q("SELECT * FROM view_daily")).length).toBeLessThanOrEqual(ROW_CAP_CORE);
    // Late rows make three of the previously tail-ranked hosts the biggest cells.
    await insert([699, 698, 697].flatMap((i) => mk(5, { src_kind: "r", src: `h${i}.example.test`, country: `C${i % 3}` }, 1000 + i * 10)));
    await rollupClosedDays({ DB, ...env }, NOW + 1000);
    const core = await q<{ src: string; views: number }>("SELECT * FROM view_daily");
    expect(core.length).toBeLessThanOrEqual(ROW_CAP_CORE);
    expect(core.reduce((a, c) => a + c.views, 0)).toBe(715);
    expect(core.some((c) => c.src === "h699.example.test" && c.views === 6)).toBe(true); // ranked first, so kept
    expect(await scratchSnapshot(env, NOW + 2000)).toBe(await snapshot());
  });

  it("re_rolling_twice_gives_the_same_result_and_counts_a_late_row_once", async () => {
    await insert(mk(6, { src_kind: "r", src: "x.example.test" }));
    await rollupClosedDays({ DB }, NOW);
    await insert([{ ts: D13 + 700, first: 1 }]);
    const a = await rollupClosedDays({ DB }, NOW + 1000);
    expect(a.late).toBe(1);
    const snap = await snapshot();
    const log = (await q<{ rerolls: number; max_id: number }>("SELECT rerolls, max_id FROM view_rollup_log"))[0];
    expect(log?.rerolls).toBe(1);
    const b = await rollupClosedDays({ DB }, NOW + 2000);
    expect(b.late).toBe(0);
    expect(await snapshot()).toBe(snap);
    expect((await q<{ rerolls: number }>("SELECT rerolls FROM view_rollup_log"))[0]?.rerolls).toBe(1);
    expect((await meta()).rollup_late_rows).toBe("0");
  });

  it("random_splits_into_original_and_late_rows_always_equal_a_from_scratch_roll_and_sum_to_raw", async () => {
    for (const seed of [1, 2, 3, 4, 5, 6]) {
      await clearAll();
      const r = prng(seed);
      const pick = <T>(a: T[]) => a[Math.floor(r() * a.length)] as T;
      const row = (): R => ({
        ts: D13 + Math.floor(r() * DAY),
        src_kind: pick(["d", "i", "r", "u"]),
        src: pick(["a", "b", "c", "d", "e", "f", "g"]),
        country: pick(["US", "DE", "FR", "JP"]),
        region: pick(["", "R1", "R2"]),
        city: pick(["", "C1", "C2"]),
        browser: pick(["chrome", "firefox", "safari"]),
        lang: pick(["en", "de", "fr", "es"]),
        os: pick(["linux", "windows", "macos"]),
        bot_class: pick(["h", "h", "a", "s"]),
        first: r() < 0.5 ? 1 : 0,
      });
      const env = { TOP_SOURCES: String(pick([2, 3, 50])), MIN_CELL: String(pick([1, 3, 5])) };
      await insert(Array.from({ length: 120 }, row));
      await rollupClosedDays({ DB, ...env }, NOW);
      const lateN = 1 + Math.floor(r() * 25);
      await insert(Array.from({ length: lateN }, row));
      await rollupClosedDays({ DB, ...env }, NOW + 1000);
      const total = 120 + lateN;
      expect(await sum("view_daily")).toBe(total);
      expect(await sum("view_geo_daily")).toBe(total);
      expect(await sum("view_dim_daily")).toBe(total * Object.keys(DIM_TOP).length);
      expect((await q<{ views: number; raw_views: number }>("SELECT * FROM view_rollup_log"))[0]).toMatchObject({ views: total, raw_views: total });
      expect(await scratchSnapshot(env, NOW + 2000)).toBe(await snapshot());
    }
  });

  it("late_rows_for_a_day_whose_counted_raw_rows_were_purged_are_dropped_and_counted_as_dropped", async () => {
    const old = Math.floor((NOW - 10 * DAY) / DAY) * DAY;
    await insert([{ ts: old + 1000 }, { ts: old + 2000 }, { ts: old + 3000 }]);
    await rollupClosedDays({ DB, MIN_CELL: "1" }, NOW);
    await purgeViews({ DB }, NOW); // the keep window passed: the counted raw rows are gone
    expect(await rawCount()).toBe(0);
    const before = await snapshot();
    await insert([{ ts: old + 4000, first: 1 }]); // a late row for that day
    const r = await rollupClosedDays({ DB, MIN_CELL: "1" }, NOW + 1000);
    expect(r.late).toBe(0);
    expect((await meta()).rollup_late_dropped).toBe("1");
    // The aggregates are NOT replaced by a one-row day: they are unchanged apart from max_id.
    expect(await sum("view_daily")).toBe(3);
    expect(JSON.stringify((await q("SELECT views, uniques, raw_views FROM view_rollup_log")))).toBe(JSON.stringify([{ views: 3, uniques: 0, raw_views: 3 }]));
    expect(before.length).toBeGreaterThan(10);
    // It is not found again, and the purge may now take it (it is accounted for).
    await rollupClosedDays({ DB, MIN_CELL: "1" }, NOW + 2000);
    expect((await meta()).rollup_late_dropped).toBe("0");
    await purgeViews({ DB }, NOW + 3000);
    expect(await rawCount()).toBe(0);
  });

  it("a_late_row_survives_the_purge_uncounted_then_is_dropped_and_accounted_for_when_the_day_cannot_be_re_rolled", async () => {
    const old = Math.floor((NOW - 10 * DAY) / DAY) * DAY;
    await insert([{ ts: old + 1000 }, { ts: old + 2000 }]);
    await rollupClosedDays({ DB, MIN_CELL: "1" }, NOW);
    await insert([{ ts: old + 3000 }]);
    await purgeViews({ DB }, NOW); // the late row is above max_id: kept
    expect(await rawCount()).toBe(1);
    await rollupClosedDays({ DB, MIN_CELL: "1" }, NOW + 1000);
    expect(await sum("view_daily")).toBe(2); // the counted rows are gone, so the day cannot be re-rolled: unchanged
    expect((await meta()).rollup_late_dropped).toBe("1");
    await purgeViews({ DB }, NOW + 2000);
    expect(await rawCount()).toBe(0);
  });
});

describe("SQL-side folding matches an independent model", () => {
  it("core_dim_geo_equal_the_oracle_across_options_and_seeds", async () => {
    for (const seed of [11, 12, 13, 14, 15, 16, 17, 18]) {
      await clearAll();
      const r = prng(seed);
      const pick = <T>(a: T[]) => a[Math.floor(r() * a.length)] as T;
      const raw: R[] = Array.from({ length: 260 }, () => ({
        ts: D13 + Math.floor(r() * DAY),
        page: pick(["home", "support"]),
        src_kind: pick(["d", "i", "r", "r", "u"]),
        src: pick(["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l"]),
        country: pick(["US", "DE", "FR", "JP", "BR", "ZZ"]),
        region: pick(["", "R1", "R2", "R3"]),
        city: pick(["", "C1", "C2", "C3", "C4"]),
        browser: pick(["chrome", "firefox", "safari", "edge", "opera", "other", "samsung", "x1", "x2", "x3", "x4", "x5", "x6", "x7"]),
        lang: pick(["en", "de", "fr", "es", "it", "nl", "pt", "ru", "ja", "zh", "ko", "sv", "da"]),
        os: pick(["linux", "windows", "macos", "ios"]),
        device: pick(["desktop", "mobile", "tablet"]),
        vp: pick(["s", "m", "l", "x"]),
        net: pick(["residential", "mobile", "hosting-datacenter"]),
        bot_class: pick(["h", "h", "h", "a", "s", "v"]),
        reason: pick(["ok", "ua_bot", "net_dc"]),
        first: r() < 0.4 ? 1 : 0,
      }));
      await insert(raw);
      const opt = {
        top: pick([1, 3, 50]),
        min: pick([1, 2, 3, 6]),
        geoLevel: pick(["country", "region", "city"] as const),
        allow: pick([null, ["a", "b"]]) as string[] | null,
      };
      await rollupClosedDays(
        { DB, TOP_SOURCES: String(opt.top), MIN_CELL: String(opt.min), GEO_LEVEL: opt.geoLevel, UTM_ALLOWLIST: opt.allow ? opt.allow.join(",") : undefined },
        NOW,
      );
      const full = (await q<RawRow>("SELECT * FROM view_events")).map((x) => ({ ...x }));
      const strip = <T extends Record<string, unknown>>(rows: T[]) => rows.map(({ day: _d, day_tz: _t, ...rest }) => rest);
      const sqlCore = strip(await q<Record<string, unknown>>("SELECT * FROM view_daily"));
      const sqlDim = strip(await q<Record<string, unknown>>("SELECT * FROM view_dim_daily"));
      const sqlGeo = strip(await q<Record<string, unknown>>("SELECT * FROM view_geo_daily"));
      expect(canon(sqlCore), `core seed ${seed}`).toBe(canon(oracleCore(full, opt)));
      expect(canon(sqlDim), `dim seed ${seed}`).toBe(canon(oracleDim(full, opt)));
      expect(canon(sqlGeo), `geo seed ${seed}`).toBe(canon(oracleGeo(full, opt)));
    }
  });
});

describe("UTM allowlist", () => {
  it("parsing_keeps_only_valid_lowercase_labels_and_unset_means_no_filter", () => {
    expect(utmAllowlist(undefined)).toBeNull();
    expect(utmAllowlist("")).toBeNull();
    expect(utmAllowlist("Bluesky, mail ,bad_label,john.doe")).toEqual(["bluesky", "mail"]);
  });

  it("labels_outside_the_allowlist_are_stored_as_other_and_referrers_are_untouched", async () => {
    await insert([
      ...mk(4, { src_kind: "u", src: "bluesky" }),
      ...mk(4, { src_kind: "u", src: "john-doe" }, 10),
      ...mk(4, { src_kind: "r", src: "alice-smith.example.test" }, 20),
    ]);
    await rollupClosedDays({ DB, TOP_SOURCES: "1000", MIN_CELL: "1", UTM_ALLOWLIST: "bluesky,mail" }, NOW);
    const out = (await q<{ src_kind: string; src: string; views: number }>("SELECT * FROM view_daily")).map((r) => `${r.src_kind}:${r.src}:${r.views}`).sort();
    expect(out).toEqual(["o:other:4", "r:alice-smith.example.test:4", "u:bluesky:4"].sort());
    expect(JSON.stringify(await q("SELECT * FROM view_daily"))).not.toContain("john-doe");
  });
});

describe("the core row cap is hard", () => {
  it("5000_distinct_pages_return_at_most_the_cap_to_the_worker_and_still_sum", async () => {
    await insert(Array.from({ length: 5000 }, (_, i) => ({ ts: D13 + i, page: `p${i}`, bot_class: ["h", "a", "s", "v"][i % 4] })));
    let widest = 0;
    const spy = new Proxy(DB, {
      get(t, p) {
        if (p === "batch") {
          return async (stmts: D1PreparedStatement[]) => {
            const res = await t.batch(stmts);
            for (const x of res) widest = Math.max(widest, x.results?.length ?? 0);
            return res;
          };
        }
        const v = Reflect.get(t, p, t);
        return typeof v === "function" ? v.bind(t) : v;
      },
    }) as D1Database;
    await rollupClosedDays({ DB: spy, MIN_CELL: "1", ROLLUP_MAX_ROWS_PER_DAY: "20000" }, NOW);
    expect(widest).toBeLessThanOrEqual(ROW_CAP_CORE); // what the Worker was handed, per query
    const core = await q<{ page: string; bot_class: string; views: number }>("SELECT * FROM view_daily");
    expect(core.length).toBeLessThanOrEqual(ROW_CAP_CORE);
    expect(core.reduce((a, c) => a + c.views, 0)).toBe(5000);
    // The overflow collapsed to one 'other' page row per bot class.
    expect(core.filter((c) => c.page === "other").length).toBeLessThanOrEqual(4);
  }, 120_000);
});

describe("oversized days", () => {
  it("the_per_day_row_limit_is_parsed_with_a_default_and_a_ceiling", () => {
    expect(maxRowsPerDay(undefined)).toBe(3000);
    expect(maxRowsPerDay("junk")).toBe(3000);
    expect(maxRowsPerDay("0")).toBe(3000);
    expect(maxRowsPerDay("500")).toBe(500);
    expect(maxRowsPerDay("999999")).toBe(20000);
  });

  it("a_day_over_the_limit_is_rolled_reduced_totals_still_sum_and_geography_is_country_only", async () => {
    const r = prng(7);
    const rows: R[] = Array.from({ length: 300 }, (_, i) => ({
      ts: D13 + i,
      src_kind: "r",
      src: `h${Math.floor(r() * 40)}.example.test`,
      country: "US",
      region: "CA",
      city: "Sentinelville",
    }));
    await insert(rows);
    await rollupClosedDays({ DB, MIN_CELL: "1", ROLLUP_MAX_ROWS_PER_DAY: "100" }, NOW);
    expect(await sum("view_daily")).toBe(300);
    expect(await sum("view_geo_daily")).toBe(300);
    expect(new Set((await q<{ src: string }>("SELECT src FROM view_daily WHERE src_kind = 'r'")).map((x) => x.src)).size).toBeLessThanOrEqual(10);
    expect(JSON.stringify(await q("SELECT * FROM view_geo_daily"))).not.toContain("Sentinelville");
    expect((await meta()).rollup_reduced_days).toBe("1");
  });

  it("an_invocation_stops_taking_days_once_its_input_row_budget_is_spent", async () => {
    const d12 = D13 - DAY;
    await insert([...mk(300, {}), ...Array.from({ length: 300 }, (_, i) => ({ ts: d12 + i }))]);
    const env = { DB, MIN_CELL: "1", ROLLUP_MAX_ROWS_PER_DAY: "100" }; // budget: 4 x 100 rows
    const a = await rollupClosedDays(env, NOW);
    expect(a.days).toBe(1);
    expect((await q("SELECT day FROM view_rollup_log")).length).toBe(1);
    const b = await rollupClosedDays(env, NOW + 1000);
    expect(b.days).toBe(1);
    expect((await q("SELECT day FROM view_rollup_log")).length).toBe(2);
    expect(day(d12)).toBe("2027-03-12");
  });
});
