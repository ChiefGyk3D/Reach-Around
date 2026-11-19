// SPDX-License-Identifier: Apache-2.0
// Landing and hop funnel tables (issue #566, PR 1: collect first): three closed labels
// (s, h, l) read by one parser, two more marginal counters, their caps, a separate hour slice,
// the seal and the roll-up. No table holds a pair of labels.
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import { closedLabel, handleClick, type Env } from "../src/click";
import {
  HOP_DAY_CAP,
  HOP_MONTH_CAP,
  LANDING_DAY_CAP,
  LANDING_MONTH_CAP,
  LH_HOUR_BUDGET,
  LH_RESERVED_PER_LINK,
  OVERFLOW_LINK,
  clickCounterStatements,
  rollupClickFunnel,
} from "../src/clickfunnel";
import { FUNNEL_CAPS } from "../src/funnelcaps";
import { loadSites } from "../src/sites";

const DB = testEnv.DB;
const SITES = JSON.stringify({
  links: {
    hosts: ["links.example.test"],
    mode: "beacon",
    pages: ["home"],
    link_ids: { twitch: "home", kick: "home" },
    destination_kinds: { twitch: "social_icon", kick: "link" },
    sources: ["instagram", "bluesky"],
    landings: ["home", "support"],
    hops: ["blog", "support"],
  },
  plain: { hosts: ["plain.example.test"], mode: "beacon", pages: ["home"], link_ids: { twitch: "home" }, sources: ["instagram"] },
});
const UA = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0";
let n = 0;
const env = (): Env => ({ DB, SITES, CLICK_LIMITER: { limit: async () => ({ success: true }) } });
const click = (qs: string, o: { host?: string; id?: string } = {}) =>
  new Request(`https://worker.example.test/e/c/${o.id ?? "twitch"}${qs}`, {
    method: "POST",
    headers: { "User-Agent": UA, "CF-Connecting-IP": `198.51.100.${++n % 250}`, "Ping-From": `https://${o.host ?? "links.example.test"}/` },
  });
const q = async <T>(sql: string) => (await DB.prepare(sql).all<T>()).results;
const TABLES = [
  "click_src_daily", "click_country_daily", "click_landing_daily", "click_hop_daily", "click_seal", "click_budget",
  "click_budget_lh", "click_capacity", "click_funnel_state", "click_landing_state", "click_src_monthly",
  "click_country_monthly", "click_landing_monthly", "click_hop_monthly", "click_month_log", "click_events", "view_hour_daily",
];
beforeEach(async () => {
  for (const t of TABLES) await DB.prepare(`DELETE FROM ${t}`).run();
});

const u = (qs: string) => new URL(`https://w.example.test/e/c/x${qs}`);
const LIST = ["instagram"];

describe("one shared parser for s, h and l", () => {
  it.each(["s", "h", "l"])("%s_keeps_a_listed_label_folds_an_unlisted_one_and_is_null_when_missing", (p) => {
    expect(closedLabel(u(`?${p}=instagram`), p, LIST)).toBe("instagram");
    expect(closedLabel(u(`?${p}=Instagram`), p, LIST)).toBe("instagram");
    expect(closedLabel(u(`?${p}=tiktok`), p, LIST)).toBe("other");
    expect(closedLabel(u(""), p, LIST)).toBeNull();
    expect(closedLabel(u(`?${p}=instagram`), p, undefined)).toBeNull();
  });
  it.each([
    ["empty", "="], ["too_long", `=${"a".repeat(31)}`], ["repeated", "=instagram&@=instagram"], ["space", "=a%20b"],
    ["sql", "=%27%3B%20DROP%20TABLE%20click_events%3B--"], ["ipv4", "=198.51.100.7"], ["path", "=..%2F..%2Fetc"],
    ["null_byte", "=a%00b"], ["kelvin_sign", "=%E2%84%AAick"], ["dotless_i", "=%C4%B1nstagram"], ["script", "=%3Cscript%3E"],
    ["unicode", "=%E3%82%A4"],
  ])("hostile_%s_is_null_for_every_parameter", (_name, rest) => {
    for (const p of ["s", "h", "l"]) expect(closedLabel(u(`?${p}${rest.replace("@", p)}`), p, LIST)).toBeNull();
  });
  it("a_percent_escape_that_decodes_to_a_listed_label_is_that_label_for_all_three", () => {
    for (const p of ["s", "h", "l"]) expect(closedLabel(u(`?${p}=inst%61gram`), p, LIST)).toBe("instagram");
  });
  it("only_the_named_parameter_is_read", () => {
    expect(closedLabel(u("?s=instagram"), "h", LIST)).toBeNull();
    expect(closedLabel(u("?hh=instagram"), "h", LIST)).toBeNull();
  });
});

describe("per-site lists", () => {
  const site = (extra: Record<string, unknown>) =>
    loadSites({ SITES: JSON.stringify({ a: { hosts: ["a.example.test"], mode: "beacon", pages: ["home"], link_ids: {}, ...extra } }) });
  it("accepts_closed_lists_and_rejects_bad_ones", () => {
    const ok = site({ landings: ["home"], hops: ["blog"] });
    expect(ok.sites[0]?.landings).toEqual(["home"]);
    expect(ok.sites[0]?.hops).toEqual(["blog"]);
    expect(site({}).sites[0]?.landings).toBeUndefined();
    for (const key of ["landings", "hops"]) {
      for (const bad of [["other"], ["none"], ["Bad"], ["a b"], ["x".repeat(31)], [1], "home", Array.from({ length: 13 }, (_, i) => `p${i}`)]) {
        expect(site({ [key]: bad }).sites).toEqual([]);
      }
      expect(site({ [key]: Array.from({ length: 12 }, (_, i) => `p${i}`) }).sites).toHaveLength(1);
      expect(site({ [key]: [] }).sites[0]?.[key as "landings"]).toEqual([]);
    }
  });
});

describe("the click route counts landing and hop", () => {
  const rows = (t: string, col: string) => q<{ link_id: string; destination_kind: string; label: string; clicks: number }>(`SELECT link_id, destination_kind, ${col} AS label, clicks FROM ${t} ORDER BY link_id, ${col}`);
  it("writes_exactly_one_to_each_enabled_table_per_click_and_keeps_kinds_apart", async () => {
    await handleClick(click("?l=home&h=blog"), env());
    await handleClick(click("?l=home&h=blog"), env());
    await handleClick(click("?l=support"), env());
    await handleClick(click("?l=nope&h=nope", { id: "kick" }), env());
    await handleClick(click("?l=%20&h=a%20b"), env());
    expect(await rows("click_landing_daily", "landing")).toEqual([
      { link_id: "kick", destination_kind: "link", label: "other", clicks: 1 },
      { link_id: "twitch", destination_kind: "social_icon", label: "home", clicks: 2 },
      { link_id: "twitch", destination_kind: "social_icon", label: "none", clicks: 1 },
      { link_id: "twitch", destination_kind: "social_icon", label: "support", clicks: 1 },
    ]);
    expect(await rows("click_hop_daily", "hop")).toEqual([
      { link_id: "kick", destination_kind: "link", label: "other", clicks: 1 },
      { link_id: "twitch", destination_kind: "social_icon", label: "blog", clicks: 2 },
      { link_id: "twitch", destination_kind: "social_icon", label: "none", clicks: 2 },
    ]);
    // the three marginals each hold all five clicks
    for (const t of ["click_landing_daily", "click_hop_daily", "click_src_daily", "click_country_daily"]) {
      expect((await q<{ s: number }>(`SELECT SUM(clicks) AS s FROM ${t}`))[0]?.s).toBe(5);
    }
  });
  it("a_site_without_the_lists_writes_no_cells_and_a_site_with_them_does", async () => {
    await handleClick(click("?l=home&h=blog", { host: "plain.example.test" }), env());
    expect(await q("SELECT 1 FROM click_landing_daily")).toEqual([]);
    expect(await q("SELECT 1 FROM click_hop_daily")).toEqual([]);
    expect(await q("SELECT 1 FROM click_src_daily")).toHaveLength(1);
    await handleClick(click("?l=home&h=blog"), env());
    expect(await q("SELECT 1 FROM click_landing_daily")).toHaveLength(1);
    expect(await q("SELECT 1 FROM click_hop_daily")).toHaveLength(1);
  });
  it("a_percent_escape_to_a_listed_label_counts_as_that_label", async () => {
    await handleClick(click("?l=ho%6De&h=bl%6Fg"), env());
    expect((await rows("click_landing_daily", "landing"))[0]?.label).toBe("home");
    expect((await rows("click_hop_daily", "hop"))[0]?.label).toBe("blog");
  });
  it("a_sealed_day_writes_nothing_to_either_table", async () => {
    await DB.prepare("INSERT INTO click_seal VALUES ('links', ?1, 1)").bind(new Date().toISOString().slice(0, 10)).run();
    await handleClick(click("?l=home&h=blog"), env());
    expect(await q("SELECT 1 FROM click_landing_daily")).toEqual([]);
    expect(await q("SELECT 1 FROM click_hop_daily")).toEqual([]);
  });
  it("hostile_values_leave_the_label_sets_equal_to_the_config", async () => {
    const bad = ["'; DROP TABLE click_landing_daily;--", "<script>alert(1)</script>", "${x}", "a--b/*", "%00", "\\", "\"x\""];
    for (const b of bad) await handleClick(click(`?l=${encodeURIComponent(b)}&h=${encodeURIComponent(b)}&s=${encodeURIComponent(b)}`), env());
    const l = await q<{ landing: string }>("SELECT DISTINCT landing FROM click_landing_daily");
    const h = await q<{ hop: string }>("SELECT DISTINCT hop FROM click_hop_daily");
    expect(l.map((r) => r.landing)).toEqual(["none"]);
    expect(h.map((r) => r.hop)).toEqual(["none"]);
  });
});

const T0 = Date.UTC(2027, 0, 5, 0);
async function count(ts: number, link: string, landing: string | undefined, hop: string | undefined, kind: "link" | "social_icon" = "link", site = "links") {
  await DB.batch(clickCounterStatements(DB, { site, ts, linkId: link, destinationKind: kind, src: null, listsSources: false, country: null, landing, hop }));
}
const cnt = async (t: string) => (await q<{ n: number }>(`SELECT count(*) AS n FROM ${t}`))[0]!.n;

describe("caps, fold and the separate hour slice", () => {
  it("the_caps_are_the_free_profile_values", () => {
    expect([LANDING_DAY_CAP, HOP_DAY_CAP, LH_RESERVED_PER_LINK, LANDING_MONTH_CAP, HOP_MONTH_CAP, LH_HOUR_BUDGET]).toEqual([24, 16, 1, 40, 30, 16]);
    expect({ l: FUNNEL_CAPS.landingDayCap, h: FUNNEL_CAPS.hopDayCap }).toEqual({ l: 24, h: 16 });
  });
  it("labels_past_the_pool_fold_into_one_overflow_row_and_existing_cells_keep_counting", async () => {
    await count(T0, "a", "first", "first");
    // spread over hours so the hour slice does not bind
    for (let i = 0; i < 80; i++) await count(T0 + (i % 24) * 3_600_000, `l${i % 6}`, `x${i}`, `y${i}`);
    expect(await cnt("click_landing_daily")).toBeLessThanOrEqual(LANDING_DAY_CAP + 7 * LH_RESERVED_PER_LINK + 1);
    expect(await cnt("click_hop_daily")).toBeLessThanOrEqual(HOP_DAY_CAP + 7 * LH_RESERVED_PER_LINK + 1);
    const over = await q<{ landing: string; clicks: number }>(`SELECT landing, clicks FROM click_landing_daily WHERE link_id = '${OVERFLOW_LINK}'`);
    expect(over).toHaveLength(1);
    expect(over[0]?.landing).toBe("other");
    expect(over[0]?.clicks).toBeGreaterThan(0);
    const before = (await q<{ clicks: number }>("SELECT clicks FROM click_landing_daily WHERE link_id = 'a' AND landing = 'first'"))[0]!.clicks;
    await count(T0, "a", "first", "first");
    expect((await q<{ clicks: number }>("SELECT clicks FROM click_landing_daily WHERE link_id = 'a' AND landing = 'first'"))[0]!.clicks).toBe(before + 1);
    expect((await q<{ s: number }>("SELECT SUM(clicks) AS s FROM click_landing_daily"))[0]?.s).toBe(82);
    expect((await q<{ s: number }>("SELECT SUM(clicks) AS s FROM click_hop_daily"))[0]?.s).toBe(82);
    expect(await q("SELECT 1 FROM click_capacity")).toHaveLength(1);
  });
  it("a_reserved_cell_survives_a_flood_of_fake_labels_on_another_destination", async () => {
    for (let i = 0; i < 60; i++) await count(T0 + (i % 24) * 3_600_000, "flood", `f${i}`, `g${i}`);
    await count(T0 + 23 * 3_600_000, "quiet", "real", "real");
    expect(await q("SELECT 1 FROM click_landing_daily WHERE link_id = 'quiet' AND landing = 'real'")).toHaveLength(1);
    expect(await q("SELECT 1 FROM click_hop_daily WHERE link_id = 'quiet' AND hop = 'real'")).toHaveLength(1);
  });
  it("the_hour_slice_caps_new_cells_and_folds_the_rest", async () => {
    // one hour, many links so neither the pool nor reserved cells bind first
    for (let i = 0; i < LH_HOUR_BUDGET + 10; i++) await count(T0, `l${i}`, "home", undefined);
    const cells = await q<{ link_id: string }>(`SELECT link_id FROM click_landing_daily WHERE link_id <> '${OVERFLOW_LINK}'`);
    expect(cells).toHaveLength(LH_HOUR_BUDGET);
    expect((await q<{ s: number }>("SELECT SUM(clicks) AS s FROM click_landing_daily"))[0]?.s).toBe(LH_HOUR_BUDGET + 10);
  });
  it("a_source_flood_cannot_starve_landing_and_hop_and_the_reverse", async () => {
    // Exhaust the source and country slice (12) in one hour.
    for (let i = 0; i < 40; i++) {
      await DB.batch(clickCounterStatements(DB, { site: "links", ts: T0, linkId: `s${i}`, src: `src${i}`, listsSources: true, country: null }));
    }
    const srcBudget = (await q<{ clicks: number }>("SELECT clicks FROM click_budget"))[0]!.clicks;
    expect(srcBudget).toBeGreaterThanOrEqual(12);
    await count(T0, "fresh", "home", "blog");
    expect(await q("SELECT 1 FROM click_landing_daily WHERE link_id = 'fresh'")).toHaveLength(1);
    expect(await q("SELECT 1 FROM click_hop_daily WHERE link_id = 'fresh'")).toHaveLength(1);
    // Now exhaust the landing and hop slice in another hour; a fresh source cell still gets made.
    const t1 = T0 + 5 * 3_600_000;
    for (let i = 0; i < 40; i++) await count(t1, "m", `p${i}`, `q${i}`); // one link: no country cell eats the source slice
    await DB.batch(clickCounterStatements(DB, { site: "links", ts: t1, linkId: "late", src: "late", listsSources: true, country: null }));
    expect(await q("SELECT 1 FROM click_src_daily WHERE link_id = 'late' AND src = 'late'")).toHaveLength(1);
  });
  it("the_slice_lives_in_its_own_table_and_source_country_never_touch_it", async () => {
    await DB.batch(clickCounterStatements(DB, { site: "links", ts: T0, linkId: "a", src: "x", listsSources: true, country: "US" }));
    expect(await cnt("click_budget_lh")).toBe(0);
    await count(T0, "a", "home", "blog");
    expect((await q<{ clicks: number }>("SELECT clicks FROM click_budget_lh"))[0]?.clicks).toBe(2);
  });
  it("a_click_that_folds_only_on_the_landing_axis_marks_the_day_once", async () => {
    for (let i = 0; i < LH_HOUR_BUDGET + 3; i++) await count(T0, "a", `p${i}`, undefined); // one link: no source or country cell folds
    const cap = await q<{ folded: number; first_hour: number }>("SELECT folded, first_hour FROM click_capacity");
    expect(cap).toEqual([{ folded: 3, first_hour: 0 }]);
  });
  it("kinds_stay_separate_cells", async () => {
    await count(T0, "a", "home", "blog", "link");
    await count(T0, "a", "home", "blog", "social_icon");
    expect(await cnt("click_landing_daily")).toBe(2);
    expect(await cnt("click_hop_daily")).toBe(2);
  });
});

describe("seal, roll-up and retention cover the new tables", () => {
  const DAY = 86_400_000;
  it("one_seal_closes_all_four_tables", async () => {
    await count(T0, "a", "home", "blog");
    await DB.prepare("INSERT INTO click_seal VALUES ('links','2027-01-05',1)").run();
    await count(T0, "a", "home", "blog");
    await count(T0, "b", "support", "blog");
    expect((await q<{ s: number }>("SELECT SUM(clicks) AS s FROM click_landing_daily"))[0]?.s).toBe(1);
    expect((await q<{ s: number }>("SELECT SUM(clicks) AS s FROM click_hop_daily"))[0]?.s).toBe(1);
  });
  it("a_closed_month_is_summed_with_the_fold_caps", async () => {
    for (let d = 1; d <= 3; d++) {
      await count(Date.UTC(2027, 0, d, 3), "a", "home", "blog");
      await count(Date.UTC(2027, 0, d, 3), "a", "home", "blog");
    }
    await rollupClickFunnel({ DB, sites: ["links"] }, Date.UTC(2027, 1, 3, 12));
    expect(await q("SELECT link_id, destination_kind, landing, clicks FROM click_landing_monthly")).toEqual([
      { link_id: "a", destination_kind: "link", landing: "home", clicks: 6 },
    ]);
    expect(await q("SELECT link_id, destination_kind, hop, clicks FROM click_hop_monthly")).toEqual([
      { link_id: "a", destination_kind: "link", hop: "blog", clicks: 6 },
    ]);
  });
  it("month_cells_past_the_cap_fold_to_one_row_with_the_same_total", async () => {
    // 60 distinct (link, landing) cells in one month; pool caps are bypassed by writing the daily rows directly
    for (let i = 0; i < 60; i++) {
      await DB.prepare("INSERT INTO click_landing_daily (site, day, link_id, destination_kind, landing, clicks) VALUES ('links', '2027-01-05', ?1, 'link', 'home', ?2)").bind(`l${i}`, 60 - i).run();
    }
    await rollupClickFunnel({ DB, sites: ["links"] }, Date.UTC(2027, 1, 3, 12));
    expect(await cnt("click_landing_monthly")).toBeLessThanOrEqual(LANDING_MONTH_CAP + 1);
    expect((await q<{ s: number }>("SELECT SUM(clicks) AS s FROM click_landing_monthly"))[0]?.s).toBe((60 * 61) / 2);
  });
  it("retention_removes_day_cells_at_90_days_and_keeps_seal_and_budget_until_no_cell_is_left", async () => {
    const old = Date.UTC(2027, 0, 5, 3);
    await count(old, "a", "home", "blog");
    await rollupClickFunnel({ DB, sites: ["links"] }, old + 2 * DAY); // seals the day
    await rollupClickFunnel({ DB, sites: ["links"] }, old + 40 * DAY); // month rolled
    expect(await cnt("click_landing_daily")).toBe(1); // 35 days old, kept
    await rollupClickFunnel({ DB, sites: ["links"] }, old + 91 * DAY);
    expect(await cnt("click_landing_daily")).toBe(0);
    expect(await cnt("click_hop_daily")).toBe(0);
    expect(await cnt("click_budget_lh")).toBe(0);
    expect(await q("SELECT 1 FROM click_seal WHERE day = '2027-01-05'")).toEqual([]);
  });
  it("the_hard_ceiling_removes_unrolled_new_cells_at_125_days", async () => {
    await DB.prepare("INSERT INTO click_hop_daily (site, day, link_id, destination_kind, hop, clicks) VALUES ('links','2027-01-05','a','link','blog',1)").run();
    await DB.prepare("INSERT INTO click_funnel_state VALUES ('links','2027-06-01')").run();
    await rollupClickFunnel({ DB }, Date.UTC(2027, 0, 5) + 126 * DAY);
    expect(await cnt("click_hop_daily")).toBe(0);
  });
  it("monthly_rows_older_than_25_months_go", async () => {
    await DB.prepare("INSERT INTO click_hop_monthly (site, month, link_id, destination_kind, hop, clicks) VALUES ('links','2020-01','a','link','blog',1)").run();
    await DB.prepare("INSERT INTO click_landing_monthly (site, month, link_id, destination_kind, landing, clicks) VALUES ('links','2020-01','a','link','home',1)").run();
    await rollupClickFunnel({ DB }, Date.UTC(2027, 0, 5));
    expect(await cnt("click_hop_monthly")).toBe(0);
    expect(await cnt("click_landing_monthly")).toBe(0);
  });
  it("the_first_day_marker_is_written_once_from_the_first_new_cells", async () => {
    await count(Date.UTC(2027, 0, 7, 3), "a", "home", undefined);
    await count(Date.UTC(2027, 0, 5, 3), "a", undefined, "blog");
    await rollupClickFunnel({ DB }, Date.UTC(2027, 0, 20));
    await rollupClickFunnel({ DB }, Date.UTC(2027, 0, 21));
    expect(await q("SELECT site, from_day FROM click_landing_state")).toEqual([{ site: "links", from_day: "2027-01-05" }]);
  });
});

describe("migration 0017", () => {
  const NEW = ["click_landing_daily", "click_hop_daily", "click_landing_monthly", "click_hop_monthly"];
  const LABELS = new Set(["site", "day", "month", "link_id", "destination_kind", "clicks"]);
  /** The label columns of a table: every column that is not a key part or the count. */
  const labelColumns = async (t: string) => (await q<{ name: string }>(`SELECT name FROM pragma_table_info('${t}')`)).map((r) => r.name).filter((c) => !LABELS.has(c));
  it("creates_the_tables_with_the_key_site_day_link_kind_label", async () => {
    const pk = async (t: string) => (await q<{ name: string }>(`SELECT name FROM pragma_table_info('${t}') WHERE pk > 0 ORDER BY pk`)).map((r) => r.name);
    expect(await pk("click_landing_daily")).toEqual(["site", "day", "link_id", "destination_kind", "landing"]);
    expect(await pk("click_hop_daily")).toEqual(["site", "day", "link_id", "destination_kind", "hop"]);
    expect(await pk("click_landing_monthly")).toEqual(["site", "month", "link_id", "destination_kind", "landing"]);
    expect(await pk("click_hop_monthly")).toEqual(["site", "month", "link_id", "destination_kind", "hop"]);
  });
  it("check_refuses_a_bad_label_a_bad_kind_and_a_zero_count", async () => {
    const ins = (label: string, kind: string, clicks: number) =>
      DB.prepare("INSERT INTO click_hop_daily (site, day, link_id, destination_kind, hop, clicks) VALUES ('links','2027-01-05','a',?1,?2,?3)").bind(kind, label, clicks).run();
    await expect(ins("Bad", "link", 1)).rejects.toThrow();
    await expect(ins("a b", "link", 1)).rejects.toThrow();
    await expect(ins("", "link", 1)).rejects.toThrow();
    await expect(ins("x".repeat(31), "link", 1)).rejects.toThrow();
    await expect(ins("blog", "other", 1)).rejects.toThrow();
    await expect(ins("blog", "link", 0)).rejects.toThrow();
    await ins("blog", "link", 1);
  });
  it("no_new_table_holds_two_label_columns", async () => {
    for (const t of NEW) expect(await labelColumns(t)).toHaveLength(1);
  });
  it("the_pair_guard_bites_on_a_table_with_two_labels", async () => {
    await DB.prepare("CREATE TABLE tmp_pair (site TEXT, day TEXT, link_id TEXT, destination_kind TEXT, landing TEXT, hop TEXT, clicks INTEGER)").run();
    try {
      expect(await labelColumns("tmp_pair")).toHaveLength(2);
    } finally {
      await DB.prepare("DROP TABLE tmp_pair").run();
    }
  });
});
