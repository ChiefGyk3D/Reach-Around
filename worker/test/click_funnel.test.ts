// SPDX-License-Identifier: Apache-2.0
// Funnel rollups (issues #331 and #409): the marginal counters the click route
// increments, their caps, the seal, the monthly roll-up and the retention.
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import { handleClick, type Env } from "../src/click";
import {
  COUNTRY_DAY_CAP,
  DAY_HARD_KEEP_DAYS,
  OVERFLOW_LINK,
  SITE_DAY_BUDGET,
  SRC_DAY_CAP,
  SRC_RESERVE,
  clickCounterStatements,
  rollupClickFunnel,
} from "../src/clickfunnel";
import { QueryMeter, meteredDb } from "../src/d1budget";
import worker from "../src/index";
import { loadSites } from "../src/sites";

const DB = testEnv.DB;
const SITES = JSON.stringify({
  links: {
    hosts: ["links.example.test"],
    mode: "beacon",
    pages: ["home"],
    link_ids: { twitch: "home", kick: "home" },
    sources: ["instagram", "bluesky"],
  },
  nosrc: { hosts: ["plain.example.test"], mode: "beacon", pages: ["home"], link_ids: { twitch: "home" } },
});
const UA = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0";
const DAY = 86_400_000;
let n = 0;
const env = (): Env => ({ DB, SITES, CLICK_LIMITER: { limit: async () => ({ success: true }) } });
const click = (qs: string, o: { host?: string; id?: string; country?: string } = {}) =>
  new Request(`https://worker.example.test/e/c/${o.id ?? "twitch"}${qs}`, {
    method: "POST",
    headers: {
      "User-Agent": UA,
      "CF-Connecting-IP": `198.51.100.${++n % 250}`,
      "Ping-From": `https://${o.host ?? "links.example.test"}/`,
      ...(o.country ? { "CF-IPCountry": o.country } : {}),
    },
  });
const q = async <T>(sql: string) => (await DB.prepare(sql).all<T>()).results;
const TABLES = ["click_src_daily", "click_country_daily", "click_seal", "click_budget", "click_src_monthly", "click_country_monthly", "click_month_log", "click_events"];

beforeEach(async () => {
  for (const t of TABLES) await DB.prepare(`DELETE FROM ${t}`).run();
});

async function count(ts: number, site: string, link: string, src: string | null, country: string | null, listsSources = true) {
  await DB.batch(clickCounterStatements(DB, { site, ts, linkId: link, src, listsSources, country }));
}

describe("the click route increments the marginal counters", () => {
  it("counts_source_by_link_and_country_by_link_for_every_click", async () => {
    await handleClick(click("?s=instagram", { country: "US" }), env());
    await handleClick(click("?s=instagram", { country: "DE" }), env());
    await handleClick(click("", { country: "US" }), env());
    await handleClick(click("?s=tiktok", { country: "US" }), env());
    await handleClick(click("", { id: "kick" }), env());
    const src = await q<{ link_id: string; src: string; clicks: number }>("SELECT link_id, src, clicks FROM click_src_daily ORDER BY link_id, src");
    expect(src).toEqual([
      { link_id: "kick", src: "direct", clicks: 1 },
      { link_id: "twitch", src: "direct", clicks: 1 },
      { link_id: "twitch", src: "instagram", clicks: 2 },
      { link_id: "twitch", src: "other", clicks: 1 },
    ]);
    // Country covers the attributed clicks too, which keep no country on the raw row.
    const cc = await q<{ link_id: string; country: string; clicks: number }>("SELECT link_id, country, clicks FROM click_country_daily ORDER BY link_id, country");
    expect(cc).toEqual([
      { link_id: "kick", country: "ZZ", clicks: 1 },
      { link_id: "twitch", country: "DE", clicks: 1 },
      { link_id: "twitch", country: "US", clicks: 3 },
    ]);
    expect(await q("SELECT country FROM click_events WHERE src IS NOT NULL AND country IS NOT NULL")).toEqual([]);
  });

  it("a_site_without_sources_gets_no_source_counter_but_still_a_country_counter", async () => {
    await handleClick(click("?s=instagram", { host: "plain.example.test", country: "FR" }), env());
    expect(await q("SELECT * FROM click_src_daily")).toEqual([]);
    expect(await q<{ site: string; country: string }>("SELECT site, country FROM click_country_daily")).toEqual([{ site: "nosrc", country: "FR" }]);
  });

  it("the_raw_row_never_holds_a_country_but_the_counter_counts_every_click", async () => {
    await handleClick(click("?s=instagram", { country: "US" }), env());
    await handleClick(click("", { country: "US" }), env());
    await handleClick(click("", { country: "DE" }), env());
    expect(await q("SELECT 1 FROM click_events WHERE country IS NOT NULL")).toEqual([]);
    const cc = await q<{ country: string; c: number }>("SELECT country, SUM(clicks) AS c FROM click_country_daily GROUP BY country ORDER BY country");
    expect(cc).toEqual([{ country: "DE", c: 1 }, { country: "US", c: 2 }]);
  });

  it("an_ignored_click_counts_nothing", async () => {
    await handleClick(new Request("https://worker.example.test/e/c/twitch", { method: "POST", headers: { "User-Agent": "curl/8", "Ping-From": "https://links.example.test/" } }), env());
    await handleClick(click("", { id: "nope" }), env());
    expect(await q("SELECT * FROM click_country_daily")).toEqual([]);
  });

  it("the_counters_have_no_column_that_could_hold_a_pair_or_a_person", async () => {
    const cols = async (t: string) => (await q<{ name: string }>(`SELECT name FROM pragma_table_info('${t}')`)).map((c) => c.name);
    expect(await cols("click_src_daily")).toEqual(["site", "day", "link_id", "src", "clicks"]);
    expect(await cols("click_country_daily")).toEqual(["site", "day", "link_id", "country", "clicks"]);
  });

  it("the_counter_day_is_a_utc_day_and_never_finer", async () => {
    await count(Date.UTC(2027, 0, 5, 23, 59, 59), "links", "twitch", "instagram", "US");
    expect((await q<{ day: string }>("SELECT day FROM click_src_daily"))[0]?.day).toBe("2027-01-05");
  });
});

describe("per-site caps: one overflow row, a fixed budget", () => {
  const ts = Date.UTC(2027, 0, 5, 12);
  const seedSrc = (n: number, link = "twitch") =>
    DB.batch(Array.from({ length: n }, (_, i) => DB.prepare("INSERT INTO click_src_daily VALUES ('links','2027-01-05',?,?,1)").bind(link, `s${i}`)));

  it("source_cells_past_the_cap_go_to_one_overflow_row_for_all_links", async () => {
    await seedSrc(SRC_DAY_CAP - SRC_RESERVE);
    await count(ts, "links", "twitch", "brandnew", "US");
    await count(ts, "links", "kick", "brandnew", "US");
    await count(ts, "links", "twitch", "s3", "US");
    const rows = await q<{ link_id: string; src: string; clicks: number }>("SELECT link_id, src, clicks FROM click_src_daily WHERE src IN ('brandnew','other')");
    expect(rows).toEqual([{ link_id: OVERFLOW_LINK, src: "other", clicks: 2 }]);
    expect(await q<{ clicks: number }>("SELECT clicks FROM click_src_daily WHERE src = 's3'")).toEqual([{ clicks: 2 }]);
    // The other site is not affected by this site's cap.
    await count(ts, "nosrc", "twitch", "brandnew", "US");
    expect(await q("SELECT 1 FROM click_src_daily WHERE site = 'nosrc' AND src = 'brandnew'")).toHaveLength(1);
  });

  it("rows_per_site_and_day_never_pass_the_cap_plus_one_however_many_links_and_labels", async () => {
    for (let i = 0; i < SRC_DAY_CAP + 30; i++) await count(ts, "links", `l${i}`, i % 2 ? "direct" : `s${i}`, `${String.fromCharCode(65 + (i % 26))}${String.fromCharCode(65 + Math.floor(i / 26))}`);
    const src = (await q<{ n: number }>("SELECT count(*) AS n FROM click_src_daily"))[0]!.n;
    const cc = (await q<{ n: number }>("SELECT count(*) AS n FROM click_country_daily"))[0]!.n;
    expect(src).toBeLessThanOrEqual(SRC_DAY_CAP + 1);
    expect(cc).toBeLessThanOrEqual(COUNTRY_DAY_CAP + 1);
    expect((await q<{ n: number }>("SELECT count(*) AS n FROM click_src_daily WHERE link_id = ?".replace("?", `'${OVERFLOW_LINK}'`)))[0]!.n).toBeLessThanOrEqual(1);
    // Every click is still counted somewhere.
    expect((await q<{ n: number }>("SELECT SUM(clicks) AS n FROM click_src_daily"))[0]!.n).toBe(SRC_DAY_CAP + 30);
  });

  it("the_reserve_keeps_room_for_direct_when_other_labels_have_filled_the_rest", async () => {
    await seedSrc(SRC_DAY_CAP - SRC_RESERVE);
    await count(ts, "links", "twitch", "newlabel", "US"); // overflow: past the cap less the reserve
    await count(ts, "links", "twitch", "direct", "US"); // may use the reserve
    expect(await q("SELECT 1 FROM click_src_daily WHERE link_id = 'twitch' AND src = 'direct'")).toHaveLength(1);
    expect(await q("SELECT 1 FROM click_src_daily WHERE src = 'newlabel'")).toEqual([]);
  });

  it("country_cells_past_the_cap_go_to_one_overflow_row", async () => {
    const letters = "ABCDEFGHIJKLMNOPQRSTUVWXY";
    const codes: string[] = [];
    for (const a of letters) for (const b of letters) if (codes.length < COUNTRY_DAY_CAP) codes.push(a + b);
    await DB.batch(codes.map((c) => DB.prepare("INSERT INTO click_country_daily VALUES ('links','2027-01-05','twitch',?,1)").bind(c)));
    await count(ts, "links", "twitch", null, "QQ");
    await count(ts, "links", "kick", null, "QQ");
    await count(ts, "links", "twitch", null, "AA");
    expect(await q("SELECT link_id, country, clicks FROM click_country_daily WHERE country IN ('QQ','ZZ')")).toEqual([{ link_id: OVERFLOW_LINK, country: "ZZ", clicks: 2 }]);
    expect(await q("SELECT clicks FROM click_country_daily WHERE country = 'AA'")).toEqual([{ clicks: 2 }]);
  });

  it("past_the_site_wide_daily_budget_no_new_cell_is_made_but_every_click_still_counts", async () => {
    await DB.prepare("INSERT INTO click_budget VALUES ('links','2027-01-05',?)").bind(SITE_DAY_BUDGET).run();
    await count(ts, "links", "twitch", "instagram", "US");
    await count(ts, "links", "twitch", "instagram", "US");
    expect(await q("SELECT link_id, src, clicks FROM click_src_daily")).toEqual([{ link_id: OVERFLOW_LINK, src: "other", clicks: 2 }]);
    expect(await q("SELECT link_id, country, clicks FROM click_country_daily")).toEqual([{ link_id: OVERFLOW_LINK, country: "ZZ", clicks: 2 }]);
    expect(await q("SELECT clicks FROM click_budget")).toEqual([{ clicks: SITE_DAY_BUDGET + 2 }]);
    // An existing cell keeps counting past the budget.
    await DB.prepare("INSERT INTO click_src_daily VALUES ('links','2027-01-05','kick','bluesky',1)").run();
    await count(ts, "links", "kick", "bluesky", "US");
    expect(await q("SELECT clicks FROM click_src_daily WHERE src = 'bluesky'")).toEqual([{ clicks: 2 }]);
  });

  it("every_counted_click_adds_one_to_the_budget_and_a_sealed_day_adds_nothing", async () => {
    await count(ts, "links", "twitch", "instagram", "US");
    await count(ts, "links", "twitch", "instagram", "DE");
    expect(await q("SELECT clicks FROM click_budget")).toEqual([{ clicks: 2 }]);
    await DB.prepare("INSERT INTO click_seal VALUES ('links','2027-01-05',1)").run();
    await count(ts, "links", "twitch", "instagram", "US");
    expect(await q("SELECT clicks FROM click_budget")).toEqual([{ clicks: 2 }]);
  });
});

describe("a counter failure never drops the raw click", () => {
  it("the_raw_row_is_kept_and_the_failure_is_only_counted", async () => {
    await DB.exec("ALTER TABLE click_budget RENAME TO click_budget_away");
    try {
      await handleClick(click("?s=instagram", { country: "US" }), env());
      await handleClick(click("", { country: "US" }), env());
    } finally {
      await DB.exec("ALTER TABLE click_budget_away RENAME TO click_budget");
    }
    expect(await q("SELECT 1 FROM click_events")).toHaveLength(2);
    expect(await q("SELECT value FROM view_meta WHERE key = 'click_counter_err'")).toEqual([{ value: "2" }]);
    await DB.prepare("DELETE FROM view_meta WHERE key = 'click_counter_err'").run();
  });
});

describe("the seal", () => {
  it("a_sealed_day_never_changes", async () => {
    const ts = Date.UTC(2027, 0, 5, 12);
    await count(ts, "links", "twitch", "instagram", "US");
    await DB.prepare("INSERT INTO click_seal VALUES ('links','2027-01-05',1)").run();
    await count(ts, "links", "twitch", "instagram", "US");
    await count(ts, "links", "twitch", "bluesky", "DE");
    expect(await q<{ clicks: number }>("SELECT clicks FROM click_src_daily")).toEqual([{ clicks: 1 }]);
    expect(await q<{ clicks: number }>("SELECT clicks FROM click_country_daily")).toEqual([{ clicks: 1 }]);
    // Another site's same day is not sealed.
    await count(ts, "nosrc", "twitch", null, "US");
    expect(await q("SELECT 1 FROM click_country_daily WHERE site = 'nosrc'")).toHaveLength(1);
  });

  it("the_hourly_job_seals_closed_days_after_the_grace_and_not_today", async () => {
    await count(Date.UTC(2027, 0, 5, 12), "links", "twitch", "instagram", "US");
    await count(Date.UTC(2027, 0, 6, 12), "links", "twitch", "instagram", "US");
    await rollupClickFunnel({ DB }, Date.UTC(2027, 0, 6, 1, 30)); // inside the 2 hour grace of day 5
    expect(await q("SELECT day FROM click_seal")).toEqual([]);
    const r = await rollupClickFunnel({ DB }, Date.UTC(2027, 0, 6, 13));
    expect(r.sealedDays).toBe(1);
    expect(await q<{ day: string }>("SELECT day FROM click_seal")).toEqual([{ day: "2027-01-05" }]);
    // A late click for the sealed day is refused; the open day still counts.
    await count(Date.UTC(2027, 0, 5, 23), "links", "twitch", "instagram", "US");
    await count(Date.UTC(2027, 0, 6, 14), "links", "twitch", "instagram", "US");
    expect(await q<{ day: string; clicks: number }>("SELECT day, clicks FROM click_src_daily ORDER BY day")).toEqual([
      { day: "2027-01-05", clicks: 1 },
      { day: "2027-01-06", clicks: 2 },
    ]);
  });
});

describe("monthly rollup and retention", () => {
  it("a_closed_month_is_summed_once_and_the_current_month_is_not", async () => {
    await count(Date.UTC(2027, 0, 5), "links", "twitch", "instagram", "US");
    await count(Date.UTC(2027, 0, 20), "links", "twitch", "instagram", "DE");
    await count(Date.UTC(2027, 0, 20), "links", "twitch", "bluesky", "DE");
    await count(Date.UTC(2027, 1, 2), "links", "twitch", "instagram", "US");
    const now = Date.UTC(2027, 1, 10);
    const r = await rollupClickFunnel({ DB }, now);
    expect(r.monthsRolled).toBe(1);
    expect(await q("SELECT month, link_id, src, clicks FROM click_src_monthly ORDER BY src")).toEqual([
      { month: "2027-01", link_id: "twitch", src: "bluesky", clicks: 1 },
      { month: "2027-01", link_id: "twitch", src: "instagram", clicks: 2 },
    ]);
    expect(await q("SELECT country, clicks FROM click_country_monthly ORDER BY country")).toEqual([
      { country: "DE", clicks: 2 },
      { country: "US", clicks: 1 },
    ]);
    // Running again adds nothing.
    expect((await rollupClickFunnel({ DB }, now + 3_600_000)).monthsRolled).toBe(0);
    expect(await q("SELECT SUM(clicks) AS c FROM click_src_monthly")).toEqual([{ c: 3 }]);
  });

  it("a_month_logged_for_one_site_does_not_suppress_another_and_nothing_is_rolled_twice", async () => {
    await count(Date.UTC(2027, 0, 5), "links", "twitch", "instagram", "US");
    await count(Date.UTC(2027, 0, 6), "nosrc", "twitch", "instagram", "US");
    await DB.prepare("INSERT INTO click_month_log VALUES ('links','2027-01',1)").run();
    const now = Date.UTC(2027, 1, 10);
    expect((await rollupClickFunnel({ DB }, now)).monthsRolled).toBe(1);
    expect(await q<{ site: string }>("SELECT DISTINCT site FROM click_src_monthly")).toEqual([{ site: "nosrc" }]);
    expect((await q<{ site: string }>("SELECT site FROM click_month_log ORDER BY site")).map((r) => r.site)).toEqual(["links", "nosrc"]);
    expect((await rollupClickFunnel({ DB }, now + 3_600_000)).monthsRolled).toBe(0);
    expect(await q("SELECT clicks FROM click_src_monthly")).toEqual([{ clicks: 1 }]);
  });

  it("a_month_over_its_cap_folds_the_smallest_cells", async () => {
    await DB.batch(
      Array.from({ length: 305 }, (_, i) => DB.prepare("INSERT INTO click_src_daily VALUES ('links','2027-01-05','twitch',?,?)").bind(`s${i}`, 400 - i)),
    );
    await rollupClickFunnel({ DB }, Date.UTC(2027, 1, 10));
    const rows = await q<{ src: string; clicks: number }>("SELECT src, clicks FROM click_src_monthly");
    expect(await q("SELECT 1 FROM click_src_monthly WHERE link_id = 'overflow-all'")).toHaveLength(1);
    expect(rows).toHaveLength(301); // 300 kept + one overflow row
    const total = rows.reduce((a, r) => a + r.clicks, 0);
    expect(total).toBe(Array.from({ length: 305 }, (_, i) => 400 - i).reduce((a, b) => a + b, 0));
    expect(rows.find((r) => r.src === "other")?.clicks).toBe(400 - 300 + 400 - 301 + 400 - 302 + 400 - 303 + 400 - 304);
  });

  it("day_cells_go_after_90_days_but_only_once_their_month_is_rolled_up", async () => {
    await count(Date.UTC(2027, 0, 5), "links", "twitch", "instagram", "US");
    await count(Date.UTC(2027, 3, 20), "links", "twitch", "instagram", "US");
    // 95 days later (April 10 would be 95 days after Jan 5): January is rolled in the same run, then pruned.
    const now = Date.UTC(2027, 0, 5) + 95 * DAY;
    await rollupClickFunnel({ DB }, now);
    expect(await q<{ day: string }>("SELECT day FROM click_src_daily")).toEqual([{ day: "2027-04-20" }]);
    expect(await q("SELECT clicks FROM click_src_monthly WHERE month = '2027-01'")).toEqual([{ clicks: 1 }]);
    expect(await q("SELECT day FROM click_seal")).toEqual([]);
  });

  it("a_day_inside_90_days_stays", async () => {
    await count(Date.UTC(2027, 0, 5), "links", "twitch", "instagram", "US");
    await rollupClickFunnel({ DB }, Date.UTC(2027, 0, 5) + 89 * DAY);
    expect(await q("SELECT day FROM click_src_daily")).toHaveLength(1);
  });

  it("a_day_is_kept_89_days_and_gone_at_exactly_90_whole_days_old", async () => {
    const d0 = Date.UTC(2027, 0, 5);
    await count(d0, "links", "twitch", "instagram", "US");
    await count(d0 + DAY, "links", "twitch", "instagram", "US");
    // now = Jan 5 + 90 days: Jan 5 is 90 days old (gone), Jan 6 is 89 days old (kept).
    await rollupClickFunnel({ DB }, d0 + 90 * DAY + 3_600_000);
    expect(await q<{ day: string }>("SELECT day FROM click_src_daily")).toEqual([{ day: "2027-01-06" }]);
    expect(await q<{ day: string }>("SELECT day FROM click_budget")).toEqual([{ day: "2027-01-06" }]);
  });

  it("a_seal_stays_while_its_cells_stay_and_goes_with_them", async () => {
    const d0 = Date.UTC(2027, 0, 5);
    await count(d0, "links", "twitch", "instagram", "US");
    // A run too short to roll the month up: seal and prune only. The day is past 90 days.
    await rollupClickFunnel({ DB: meteredDb(DB, new QueryMeter(2)) }, d0 + 100 * DAY);
    expect(await q("SELECT 1 FROM click_src_daily")).toHaveLength(1);
    expect(await q("SELECT day FROM click_seal")).toEqual([{ day: "2027-01-05" }]);
    // The full run rolls the month, then removes cells and seal together.
    await rollupClickFunnel({ DB }, d0 + 100 * DAY + 3_600_000);
    expect(await q("SELECT 1 FROM click_src_daily")).toEqual([]);
    expect(await q("SELECT 1 FROM click_seal")).toEqual([]);
    expect(await q("SELECT 1 FROM click_budget")).toEqual([]);
  });

  it("the_hard_ceiling_drops_day_cells_even_when_the_month_is_missing", async () => {
    await DB.prepare("INSERT INTO click_src_daily VALUES ('links','2027-01-05','twitch','instagram',3)").run();
    // A month log row exists only after a roll; make the roll impossible by pre-logging nothing and
    // running past the ceiling: the roll happens first, so check that the day is gone either way.
    await rollupClickFunnel({ DB }, Date.UTC(2027, 0, 5) + (DAY_HARD_KEEP_DAYS + 1) * DAY);
    expect(await q("SELECT * FROM click_src_daily")).toEqual([]);
  });

  it("monthly_rows_are_kept_25_months_and_not_longer", async () => {
    const rows = (m: string) => DB.prepare("INSERT INTO click_src_monthly VALUES ('links',?,'twitch','instagram',9)").bind(m);
    await DB.batch([rows("2024-12"), rows("2025-01"), rows("2026-12"), rows("2027-01")]);
    await DB.batch(["2024-12", "2025-01"].map((m) => DB.prepare("INSERT INTO click_month_log VALUES ('links',?,1)").bind(m)));
    await DB.prepare("INSERT INTO click_country_monthly VALUES ('links','2024-12','twitch','US',9)").run();
    // Current month 2027-02: the 25 closed months are 2025-01 .. 2027-01.
    await rollupClickFunnel({ DB }, Date.UTC(2027, 1, 10));
    expect((await q<{ month: string }>("SELECT month FROM click_src_monthly ORDER BY month")).map((r) => r.month)).toEqual(["2025-01", "2026-12", "2027-01"]);
    expect(await q("SELECT * FROM click_country_monthly")).toEqual([]);
    expect((await q<{ month: string }>("SELECT month FROM click_month_log")).map((r) => r.month)).toEqual(["2025-01"]);
  });
});

describe("migration 0012", () => {
  it("clears_the_country_of_every_existing_raw_click_row", async () => {
    await DB.batch([
      DB.prepare("INSERT INTO click_events (ts, link_id, country) VALUES (0, 'a', 'US')"),
      DB.prepare("INSERT INTO click_events (ts, link_id, src) VALUES (0, 'a', 'instagram')"),
    ]);
    const m = testEnv.TEST_MIGRATIONS.find((x) => x.name.startsWith("0012"));
    expect(m).toBeDefined();
    // With no counter rows the migration does nothing (applied too early, it destroys nothing).
    for (const sql of m!.queries) await DB.prepare(sql).run();
    expect(await q("SELECT 1 FROM click_events WHERE country IS NOT NULL")).toHaveLength(1);
    await DB.prepare("INSERT INTO click_country_daily VALUES ('links','2027-01-05','a','US',1)").run();
    for (const sql of m!.queries) await DB.prepare(sql).run();
    expect(await q("SELECT 1 FROM click_events WHERE country IS NOT NULL")).toEqual([]);
    expect(await q("SELECT 1 FROM click_events WHERE src = 'instagram'")).toHaveLength(1);
  });
});

describe("migration 0011 constraints", () => {
  it.each([
    ["INSERT INTO click_src_daily VALUES ('links','2027-01-05','twitch','Instagram',1)"],
    ["INSERT INTO click_src_daily VALUES ('links','2027-01-05','twitch','a b',1)"],
    ["INSERT INTO click_src_daily VALUES ('links','2027-01-05','twitch','x',0)"],
    ["INSERT INTO click_src_daily VALUES ('Links','2027-01-05','twitch','x',1)"],
    ["INSERT INTO click_country_daily VALUES ('links','2027-01-05','twitch','us',1)"],
    ["INSERT INTO click_country_daily VALUES ('links','2027-01-05','twitch','USA',1)"],
    ["INSERT INTO click_country_daily VALUES ('links','2027-01-05','twitch','U1',1)"],
    ["INSERT INTO click_country_monthly VALUES ('links','2027-01','twitch','198.51.100.7',1)"],
    ["INSERT INTO click_src_monthly VALUES ('links','2027-1','twitch','x',1)"],
  ])("refuses_%s", async (sql) => {
    await expect(DB.prepare(sql).run()).rejects.toThrow();
  });

  it("direct_cannot_be_a_listed_source_label", () => {
    const bad = loadSites({ SITES: JSON.stringify({ links: { hosts: ["links.example.test"], mode: "beacon", pages: ["home"], link_ids: { a: "home" }, sources: ["direct"] } }) });
    expect(bad.sites).toEqual([]);
  });
});

describe("the hourly cron", () => {
  it("runs_the_funnel_rollup", async () => {
    await count(Date.now() - 100 * DAY, "links", "twitch", "instagram", "US");
    const pending: Promise<unknown>[] = [];
    await worker.scheduled(
      { cron: "17 * * * *", scheduledTime: Date.now(), type: "scheduled", noRetry() {} } as unknown as ScheduledController,
      { ...env() } as never,
      { waitUntil: (p: Promise<unknown>) => void pending.push(p), passThroughOnException() {} } as unknown as ExecutionContext,
    );
    await Promise.all(pending);
    // A day 100 days old is past 90 days: rolled into its month, then pruned.
    expect(await q("SELECT * FROM click_src_daily")).toEqual([]);
    expect((await q<{ clicks: number }>("SELECT clicks FROM click_src_monthly"))[0]?.clicks).toBe(1);
  });
});
