// SPDX-License-Identifier: Apache-2.0
// Rollup and retention are per (site, day) (docs/analytics-design.md 3.6).
import { beforeEach, describe, expect, it } from "vitest";
import worker from "../../src/index";
import { purgeViews } from "../../src/viewretention";
import { rollupClosedDays } from "../../src/viewrollup";
import { DB, clearAll } from "./helpers";

const DAY = 86_400_000;
const NOW = Date.parse("2027-03-14T12:00:30Z");
const D13 = Date.parse("2027-03-13T00:00:00Z");
const D01 = Date.parse("2027-03-01T00:00:00Z");
const SITES = JSON.stringify({
  links: { hosts: ["links.example.test"], mode: "beacon", pages: ["home", "support"] },
  blog: { hosts: ["blog.example.test"], mode: "beacon", pages: ["post-one"], geo: "country", min_cell: 1 },
});
const q = async <T>(sql: string) => (await DB.prepare(sql).all<T>()).results;
const meta = async () => Object.fromEntries((await q<{ key: string; value: string }>("SELECT * FROM view_meta")).map((m) => [m.key, m.value]));
const env = (over: Record<string, unknown> = {}) => ({ DB, SITES, TOP_SOURCES: "1000", MIN_CELL: "1", ...over });

interface Raw {
  site: string;
  ts: number;
  page?: string;
  country?: string;
  region?: string;
  city?: string;
  net?: string;
  os?: string;
  first?: number;
  src_kind?: string;
  src?: string;
}
async function insert(list: Raw[]) {
  for (let i = 0; i < list.length; i += 20) {
    await DB.batch(
      list.slice(i, i + 20).map((r) =>
        DB.prepare(
          "INSERT INTO view_events (site, ts, page, src_kind, src, country, region, city, net, os, bot_class, reason, first) VALUES (?,?,?,?,?,?,?,?,?,?,'h','ok',?)",
        ).bind(r.site, r.ts, r.page ?? "home", r.src_kind ?? "d", r.src ?? "", r.country ?? "US", r.region ?? "", r.city ?? "", r.net ?? "unknown", r.os ?? "linux", r.first ?? 0),
      ),
    );
  }
}
const many = (site: string, ts: number, n: number, over: Partial<Raw> = {}): Raw[] => Array.from({ length: n }, () => ({ site, ts, ...over }));
const sealed = async (site: string, day: string, maxId = 1_000_000_000, rolledAt = 0) =>
  DB.prepare("INSERT INTO view_rollup_log VALUES (?, ?, 'UTC', 0, 0, 0, 0, 0, 0, ?, ?, 0)").bind(site, day, rolledAt, maxId).run();

beforeEach(clearAll);

describe("rollup per site", () => {
  it("each_site_is_aggregated_on_its_own_with_its_own_log_row", async () => {
    await insert([...many("links", D13 + 1000, 5, { first: 1 }), ...many("blog", D13 + 2000, 3, { page: "post-one" })]);
    await rollupClosedDays(env(), NOW);
    const core = await q<{ site: string; page: string; views: number }>("SELECT site, page, SUM(views) AS views FROM view_daily GROUP BY site, page ORDER BY site");
    expect(core).toEqual([
      { site: "blog", page: "post-one", views: 3 },
      { site: "links", page: "home", views: 5 },
    ]);
    const log = await q<{ site: string; raw_views: number; uniques: number }>("SELECT site, raw_views, uniques FROM view_rollup_log ORDER BY site");
    expect(log).toEqual([
      { site: "blog", raw_views: 3, uniques: 0 },
      { site: "links", raw_views: 5, uniques: 5 },
    ]);
    for (const t of ["view_dim_daily", "view_geo_daily"]) {
      const sites = (await q<{ site: string }>(`SELECT DISTINCT site FROM ${t}`)).map((r) => r.site).sort();
      expect(sites, t).toEqual(["blog", "links"]);
    }
    const geo = await q<{ site: string; v: number }>("SELECT site, SUM(views) AS v FROM view_geo_daily GROUP BY site ORDER BY site");
    expect(geo).toEqual([{ site: "blog", v: 3 }, { site: "links", v: 5 }]);
  });

  it("a_late_row_re_rolls_only_its_own_site_and_day", async () => {
    await insert([...many("links", D13 + 1000, 4), ...many("blog", D13 + 1000, 4, { page: "post-one" })]);
    await rollupClosedDays(env(), NOW);
    const before = JSON.stringify(await q("SELECT * FROM view_daily WHERE site = 'links'"));
    await insert(many("blog", D13 + 5000, 2, { page: "post-one" }));
    await rollupClosedDays(env(), NOW + 1000);
    const log = await q<{ site: string; raw_views: number; rerolls: number }>("SELECT site, raw_views, rerolls FROM view_rollup_log ORDER BY site");
    expect(log).toEqual([
      { site: "blog", raw_views: 6, rerolls: 1 },
      { site: "links", raw_views: 4, rerolls: 0 },
    ]);
    expect(JSON.stringify(await q("SELECT * FROM view_daily WHERE site = 'links'"))).toBe(before);
  });

  it("site_options_apply_per_site_and_fall_back_to_the_global_ones", async () => {
    // Two US views with a city, one view each from three other countries.
    const shape = (site: string, page: string) => [
      ...many(site, D13 + 1000, 2, { page, region: "CA", city: "Sentinelville" }),
      { site, ts: D13 + 1000, page, country: "DE" },
      { site, ts: D13 + 1000, page, country: "FR" },
    ];
    await insert([...shape("links", "home"), ...shape("blog", "post-one")]);
    await rollupClosedDays(env({ MIN_CELL: "3" }), NOW); // links: global min 3; blog: its own min_cell 1 and geo country
    const geo = (site: string) => q<{ country: string; region: string; city: string; views: number }>(`SELECT country, region, city, views FROM view_geo_daily WHERE site = '${site}' ORDER BY country, views`);
    expect((await geo("blog")).every((g) => g.region === "" && g.city === "")).toBe(true); // site geo=country
    expect((await geo("blog")).map((g) => g.country).sort()).toEqual(["DE", "FR", "US"]); // site min_cell=1: nothing folded
    expect((await geo("links")).some((g) => g.city === "Sentinelville")).toBe(false); // global min 3: the 2-view city folded
  });

  it("per_site_heartbeat_keys_replace_the_global_ones", async () => {
    await insert([...many("links", D13 + 1000, 2), ...many("blog", D13 + 1000, 2, { page: "post-one" })]);
    await rollupClosedDays(env(), NOW);
    const m = await meta();
    expect(m["rolled_through:links"]).toBe("2027-03-13");
    expect(m["rolled_through:blog"]).toBe("2027-03-13");
    expect(m.rolled_through).toBeUndefined();
    expect(m.rollup_err).toBe("none");
  });

  it("a_site_removed_from_the_config_is_still_rolled_with_the_global_options", async () => {
    await insert(many("retired", D13 + 1000, 3));
    await rollupClosedDays(env({ SITES: JSON.stringify({ links: { hosts: ["links.example.test"], mode: "beacon", pages: ["home"] } }) }), NOW);
    expect(await q("SELECT site, raw_views FROM view_rollup_log")).toEqual([{ site: "retired", raw_views: 3 }]);
  });

  it("a_broken_SITES_still_rolls_every_site_in_the_data_with_global_options", async () => {
    await insert(many("links", D13 + 1000, 2));
    await rollupClosedDays(env({ SITES: "{nope" }), NOW);
    expect(await q("SELECT site, raw_views FROM view_rollup_log")).toEqual([{ site: "links", raw_views: 2 }]);
  });

  it("no_aggregate_table_joins_geography_to_device_facts_or_the_network_class", async () => {
    const tables = ["view_daily", "view_dim_daily", "view_geo_daily"];
    const cols: Record<string, string[]> = {};
    for (const t of tables) cols[t] = (await q<{ name: string }>(`SELECT name FROM pragma_table_info('${t}')`)).map((c) => c.name);
    const device = ["browser", "os", "device", "lang", "vp", "net", "dim", "value"];
    for (const t of tables) {
      const c = cols[t] ?? [];
      expect(c, t).toContain("site");
      if (c.includes("city") || c.includes("region")) expect(c.filter((x) => device.includes(x)), t).toEqual([]);
      if (c.includes("src_kind")) expect(c.filter((x) => device.includes(x)), t).toEqual([]);
      expect(c, t).not.toContain("net"); // the class is a value of view_dim_daily.dim, never a column beside city or source
    }
    expect(tables.filter((t) => (cols[t] ?? []).includes("city"))).toEqual(["view_geo_daily"]);
  });
});

describe("retention per site", () => {
  it("sealing_one_site_never_lets_the_purge_take_the_other_sites_rows_of_that_day", async () => {
    await insert([...many("links", D01 + 1000, 2), ...many("blog", D01 + 1000, 2, { page: "post-one" })]);
    await sealed("links", "2027-03-01");
    await purgeViews({ DB, SITES }, NOW);
    expect(await q("SELECT site, COUNT(*) AS n FROM view_events GROUP BY site")).toEqual([{ site: "blog", n: 2 }]);
  });

  it("and_the_reverse", async () => {
    await insert([...many("links", D01 + 1000, 2), ...many("blog", D01 + 1000, 2, { page: "post-one" })]);
    await sealed("blog", "2027-03-01");
    await purgeViews({ DB, SITES }, NOW);
    expect(await q("SELECT site, COUNT(*) AS n FROM view_events GROUP BY site")).toEqual([{ site: "links", n: 2 }]);
  });

  it("a_sealed_site_only_loses_rows_up_to_its_own_max_id", async () => {
    await insert(many("links", D01 + 1000, 3));
    const ids = (await q<{ id: number }>("SELECT id FROM view_events ORDER BY id")).map((r) => r.id);
    await sealed("links", "2027-03-01", ids[1]);
    await purgeViews({ DB, SITES }, NOW);
    expect((await q<{ id: number }>("SELECT id FROM view_events")).map((r) => r.id)).toEqual([ids[2]]);
  });

  it("the_unrolled_day_ceiling_is_evaluated_per_site", async () => {
    const old = NOW - 25 * DAY; // past 3 x 7 days
    await insert([...many("links", old, 1), ...many("blog", old, 1, { page: "post-one" })]);
    const day = new Date(old).toISOString().slice(0, 10);
    await sealed("links", day);
    await purgeViews({ DB, SITES }, NOW);
    expect(await q("SELECT COUNT(*) AS n FROM view_events")).toEqual([{ n: 0 }]); // links by age, blog by the hard ceiling
    expect((await meta()).rollup_overdue).toBe("2027-03-14"); // the unsealed blog day was dropped past the ceiling
  });

  it("a_lower_per_site_keep_is_honored_and_the_other_site_keeps_the_global_window", async () => {
    const s = JSON.stringify({
      links: { hosts: ["links.example.test"], mode: "beacon", pages: ["home"], raw_keep_days: 2 },
      blog: { hosts: ["blog.example.test"], mode: "beacon", pages: ["post-one"] },
    });
    const t = NOW - 4 * DAY;
    await insert([...many("links", t, 1), ...many("blog", t, 1, { page: "post-one" })]);
    const day = new Date(t).toISOString().slice(0, 10);
    await sealed("links", day);
    await sealed("blog", day);
    await purgeViews({ DB, SITES: s }, NOW);
    expect(await q("SELECT site FROM view_events")).toEqual([{ site: "blog" }]);
  });

  it("a_per_site_keep_above_the_global_one_is_clamped_down_never_up", async () => {
    const s = JSON.stringify({ links: { hosts: ["links.example.test"], mode: "beacon", pages: ["home"], raw_keep_days: 90 } });
    const t = NOW - 4 * DAY;
    await insert(many("links", t, 1));
    await sealed("links", new Date(t).toISOString().slice(0, 10));
    await purgeViews({ DB, SITES: s, RAW_KEEP_DAYS: "2" }, NOW);
    expect(await q("SELECT * FROM view_events")).toEqual([]);
  });

  it("rows_of_a_retired_site_age_out_with_the_global_window", async () => {
    const t = NOW - 10 * DAY;
    await insert(many("retired", t, 2));
    await sealed("retired", new Date(t).toISOString().slice(0, 10));
    await purgeViews({ DB, SITES }, NOW);
    expect(await q("SELECT * FROM view_events")).toEqual([]);
  });

  it("a_broken_SITES_neither_wipes_nor_freezes_retention", async () => {
    const t = NOW - 10 * DAY;
    await insert([...many("links", t, 1), ...many("links", NOW, 1)]);
    await sealed("links", new Date(t).toISOString().slice(0, 10));
    await purgeViews({ DB, SITES: "{nope" }, NOW);
    expect((await q<{ ts: number }>("SELECT ts FROM view_events")).map((r) => r.ts)).toEqual([NOW]);
  });

  it("aggregates_and_log_rows_of_every_site_age_out_after_90_days", async () => {
    await DB.batch([
      DB.prepare("INSERT INTO view_geo_daily VALUES ('links','2026-11-01','UTC','US','','','h',1,0), ('blog','2026-11-01','UTC','US','','','h',1,0), ('blog','2027-03-10','UTC','US','','','h',1,0)"),
      DB.prepare("INSERT INTO view_rollup_log VALUES ('links','2026-11-01','UTC',1,1,0,1,1,1,0,0,0), ('blog','2026-11-01','UTC',1,1,0,1,1,1,0,0,0)"),
    ]);
    await purgeViews({ DB, SITES }, NOW);
    expect(await q("SELECT site, day FROM view_geo_daily")).toEqual([{ site: "blog", day: "2027-03-10" }]);
    expect(await q("SELECT * FROM view_rollup_log")).toEqual([]);
  });
});

describe("the network class leaves counted raw rows (D-A7)", () => {
  const OLD = NOW - 3 * DAY;
  const dayOf = (t: number) => new Date(t).toISOString().slice(0, 10);
  it("a_counted_row_of_a_day_sealed_over_a_day_ago_is_cleared_and_the_aggregate_keeps_the_class", async () => {
    await insert(many("links", OLD, 2, { net: "hosting-datacenter" }));
    await rollupClosedDays(env(), NOW);
    expect(await q("SELECT value, views FROM view_dim_daily WHERE dim = 'net'")).toEqual([{ value: "hosting-datacenter", views: 2 }]);
    // Sealed just now: still inside the re-roll window, so the class stays for one more day.
    await purgeViews({ DB, SITES }, NOW);
    expect(await q("SELECT DISTINCT net FROM view_events")).toEqual([{ net: "hosting-datacenter" }]);
    await purgeViews({ DB, SITES }, NOW + DAY + 1000);
    expect(await q("SELECT DISTINCT net FROM view_events")).toEqual([{ net: "unknown" }]);
    expect(await q("SELECT value, views FROM view_dim_daily WHERE dim = 'net'")).toEqual([{ value: "hosting-datacenter", views: 2 }]);
  });

  it("a_late_row_above_max_id_keeps_its_class_until_it_is_counted", async () => {
    await insert(many("links", OLD, 1, { net: "tor" }));
    await sealed("links", dayOf(OLD), 0, 0);
    await purgeViews({ DB, SITES }, NOW);
    expect(await q("SELECT net FROM view_events")).toEqual([{ net: "tor" }]);
  });

  it("an_unsealed_day_is_never_cleared", async () => {
    await insert(many("blog", OLD, 1, { net: "vpn-proxy", page: "post-one" }));
    await purgeViews({ DB, SITES }, NOW);
    expect(await q("SELECT net FROM view_events")).toEqual([{ net: "vpn-proxy" }]);
  });
});

describe("config errors are visible and closed", () => {
  const run = async (e: Record<string, unknown>) => {
    const pending: Promise<unknown>[] = [];
    const ctx = { waitUntil: (p: Promise<unknown>) => void pending.push(p) } as unknown as ExecutionContext;
    await worker.scheduled({} as ScheduledController, { DB, CLICK_LIMITER: { limit: async () => ({ success: true }) }, ...e } as never, ctx);
    await Promise.all(pending);
  };

  it("the_cron_records_codes_in_view_meta_and_clears_them_when_the_config_is_fixed", async () => {
    const dup = JSON.stringify({
      links: { hosts: ["links.example.test"], mode: "beacon", pages: ["home"] },
      blog: { hosts: ["links.example.test"], mode: "beacon", pages: ["home"] },
      "../x": { hosts: ["x.example.test"], mode: "beacon", pages: ["home"] },
    });
    await run({ SITES: dup });
    expect((await meta()).config_err).toBe("bad-site-id,dup-host");
    await run({ SITES });
    expect((await meta()).config_err).toBeUndefined();
  });

  it("the_value_never_contains_text_from_the_config", async () => {
    await run({ SITES: '{"SENTINEL-SITE-ID":{"hosts":["SENTINEL.example.test"]},"links":{"hosts":["SENTINEL.example.test"],"mode":"beacon","pages":["home"]}}' });
    const value = (await meta()).config_err ?? "";
    expect(value).not.toMatch(/sentinel/i);
    expect(value).toMatch(/^[a-z0-9:,-]+$/);
  });
});
