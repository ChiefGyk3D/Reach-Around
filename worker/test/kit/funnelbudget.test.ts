// SPDX-License-Identifier: Apache-2.0
// Issue #332, criterion 1: the deploy-time budget check models the click funnel
// tables (migration 0011) at their configured caps, and the Worker and the check
// read one set of caps.
import { env as testEnv } from "cloudflare:workers";
import { describe, expect, it } from "vitest";
import {
  COUNTRY_DAY_CAP,
  COUNTRY_MONTH_CAP,
  HOP_DAY_CAP,
  HOP_MONTH_CAP,
  HOUR_BUDGET,
  LANDING_DAY_CAP,
  LANDING_MONTH_CAP,
  LH_HOUR_BUDGET,
  LH_RESERVED_PER_LINK,
  RESERVED_PER_LINK,
  SRC_DAY_CAP,
  SRC_MONTH_CAP,
  clickCounterStatements,
} from "../../src/clickfunnel";
import { FUNNEL_CAPS, FUNNEL_PROFILES, funnelDailyWrites, funnelRowsPerSiteDay, type FunnelCaps } from "../../src/funnelcaps";
import { assessDailyBudget, combinedDailyWrites, loadKitLimits, worstCaseDailyWrites } from "../../src/kit/budgets";
import { runBudgetCheck } from "../../src/kit/budgetcheck";
import { pageViewDailyWrites } from "../../src/pvlimits";

const base = (() => {
  const c = loadKitLimits({});
  return worstCaseDailyWrites(c) + pageViewDailyWrites({}) + 300_500 + 5_000;
})();

describe("(a) funnel writes are counted in the worst case", () => {
  it("the combined figure is the old parts plus the funnel part, and the funnel part is not zero", () => {
    const a = assessDailyBudget({});
    expect(a.funnel).toBeGreaterThan(0);
    expect(a.combined).toBe(base + a.funnel);
    expect(combinedDailyWrites(loadKitLimits({}), pageViewDailyWrites({}), a.funnel)).toBe(a.combined);
  });
  it("grows with the number of sites and with link ids", () => {
    const one = assessDailyBudget({ SITE_HOST: "a.example.test", PAGE_IDS: '["home"]', LINK_IDS: '["x"]' });
    const many = assessDailyBudget({ SITE_HOST: "a.example.test", PAGE_IDS: '["home"]', LINK_IDS: JSON.stringify(Array.from({ length: 40 }, (_, i) => `l${i}`)) });
    expect(many.funnel).toBeGreaterThan(one.funnel);
    const sites = JSON.stringify({
      a: { hosts: ["a.example.test"], mode: "beacon", pages: ["home"], link_ids: {} },
      b: { hosts: ["b.example.test"], mode: "beacon", pages: ["home"], link_ids: {} },
    });
    expect(assessDailyBudget({ SITES: sites }).funnel).toBe(2 * assessDailyBudget({ SITE_HOST: "a.example.test", PAGE_IDS: '["home"]' }).funnel);
  });
  it("a list with one malformed site still counts the valid sites the Worker serves", () => {
    const ids = Object.fromEntries(Array.from({ length: 6 }, (_, i) => [`l${i}`, "home"]));
    const good = { hosts: ["a.example.test"], mode: "beacon", pages: ["home"], link_ids: ids };
    const bad = { hosts: ["b.example.test"], mode: "nope", pages: ["home"], link_ids: {} };
    const alone = assessDailyBudget({ SITES: JSON.stringify({ a: good }) }).funnel;
    const mixed = assessDailyBudget({ SITES: JSON.stringify({ a: good, b: bad }) }).funnel;
    expect(alone).toBeGreaterThan(assessDailyBudget({}).funnel);
    expect(mixed).toBe(alone);
  });
  it("the deploy check reports it and still passes at the defaults", () => {
    const r = runBudgetCheck({});
    expect(r.code).toBe(0);
    expect(r.lines[0]).toContain(`funnel ${assessDailyBudget({}).funnel}`);
  });
});

describe("(b) a cap set too high fails the check", () => {
  const high: FunnelCaps = { ...FUNNEL_CAPS, srcDayCap: 5_000, countryDayCap: 5_000, hourBudget: 5_000, srcMonthCap: 5_000, countryMonthCap: 5_000, landingDayCap: 5_000, hopDayCap: 5_000, landingMonthCap: 5_000, hopMonthCap: 5_000, lhHourBudget: 5_000 };
  // Caps this high only overrun the paid-plan default quota (1600000) when raised much further.
  const veryHigh: FunnelCaps = Object.fromEntries(Object.entries(high).map(([k, v]) => [k, k === "reservedPerLink" || k === "lhReservedPerLink" ? v : 200_000])) as unknown as FunnelCaps;
  it("the same environment passes with the profile caps and fails with high caps", () => {
    expect(assessDailyBudget({}, FUNNEL_CAPS).level).not.toBe("fail");
    const a = assessDailyBudget({}, veryHigh);
    expect(a.level).toBe("fail");
    expect(a.funnel).toBe(funnelDailyWrites(veryHigh, [{ links: 0 }]));  });
  it("the profile table itself passes the default budget for the Free profile", () => {
    expect(assessDailyBudget({}, FUNNEL_PROFILES.free).level).not.toBe("fail");
    expect(FUNNEL_CAPS).toBe(FUNNEL_PROFILES.free);
  });
  it("the Free caps are lower than the standard profile on every cap", () => {
    for (const k of Object.keys(FUNNEL_PROFILES.free) as (keyof FunnelCaps)[]) {
      if (k === "reservedPerLink" || k === "lhReservedPerLink") continue;
      expect(FUNNEL_PROFILES.free[k]).toBeLessThan(FUNNEL_PROFILES.standard[k]);
    }
  });
  it("the standard profile is not for the Free plan: it is much costlier", () => {
    expect(funnelDailyWrites(FUNNEL_PROFILES.standard, [{ links: 5 }])).toBeGreaterThan(funnelDailyWrites(FUNNEL_PROFILES.free, [{ links: 5 }]) * 2);
  });
});

describe("(c) the Worker and the check read the same caps", () => {
  it("the Worker's exported constants are the profile's values", () => {
    expect({
      srcDayCap: SRC_DAY_CAP,
      countryDayCap: COUNTRY_DAY_CAP,
      reservedPerLink: RESERVED_PER_LINK,
      srcMonthCap: SRC_MONTH_CAP,
      countryMonthCap: COUNTRY_MONTH_CAP,
      hourBudget: HOUR_BUDGET,
      landingDayCap: LANDING_DAY_CAP,
      hopDayCap: HOP_DAY_CAP,
      lhReservedPerLink: LH_RESERVED_PER_LINK,
      landingMonthCap: LANDING_MONTH_CAP,
      hopMonthCap: HOP_MONTH_CAP,
      lhHourBudget: LH_HOUR_BUDGET,
    }).toEqual(FUNNEL_CAPS);
  });
  it("real rows written by a flood never exceed the cells the model allows", async () => {
    const DB = testEnv.DB;
    for (const t of ["click_src_daily", "click_country_daily", "click_landing_daily", "click_hop_daily", "click_seal", "click_budget", "click_budget_lh", "click_capacity"]) await DB.prepare(`DELETE FROM ${t}`).run();
    const links = 12;
    const ts = Date.UTC(2027, 0, 5, 3);
    for (let i = 0; i < 700; i++) {
      const hour = i % 24;
      await DB.batch(
        clickCounterStatements(DB, {
          site: "links", ts: ts + hour * 3_600_000, linkId: `l${i % links}`, src: `s${i}`, listsSources: true, landing: `p${i}`, hop: `h${i}`,
          country: `${String.fromCharCode(65 + (i % 26))}${String.fromCharCode(65 + (Math.floor(i / 26) % 26))}`,
        }),
      );
    }
    const n = async (t: string) => ((await DB.prepare(`SELECT count(*) AS n FROM ${t}`).all<{ n: number }>()).results[0]!.n);
    const observed = (await n("click_src_daily")) + (await n("click_country_daily")) + (await n("click_landing_daily")) + (await n("click_hop_daily"));
    expect(await n("click_landing_daily")).toBeGreaterThan(FUNNEL_CAPS.landingDayCap); // the flood really reached the new caps too
    expect(await n("click_hop_daily")).toBeGreaterThan(FUNNEL_CAPS.hopDayCap);
    expect(observed).toBeGreaterThan(FUNNEL_CAPS.srcDayCap);
    expect(observed).toBeLessThanOrEqual(funnelRowsPerSiteDay(FUNNEL_CAPS, links, { landings: true, hops: true }));
    // A site that lists neither is modeled lower, and the flood above (which wrote both) is over that lower figure.
    expect(observed).toBeGreaterThan(funnelRowsPerSiteDay(FUNNEL_CAPS, links));
  });
  it("a_model_without_the_landing_and_hop_tables_would_fail_the_flood_check (negative)", () => {
    const reduced: FunnelCaps = { ...FUNNEL_CAPS, landingDayCap: 0, hopDayCap: 0, lhReservedPerLink: 0, lhHourBudget: 0 };
    const on = { landings: true, hops: true };
    expect(funnelRowsPerSiteDay(reduced, 12, on)).toBeLessThan(funnelRowsPerSiteDay(FUNNEL_CAPS, 12, on));
    const both = (links: number) => ({ links, landings: true, hops: true });
    expect(funnelDailyWrites(FUNNEL_CAPS, [both(0)])).toBe(1038); // docs/analytics-funnel-tables-design.md section 10
    expect(funnelDailyWrites(FUNNEL_CAPS, [both(5)])).toBe(1278);
    expect(funnelDailyWrites(FUNNEL_CAPS, [both(20)])).toBe(1998);
    // A site that lists neither list costs what it did before the tables existed.
    expect(funnelDailyWrites(FUNNEL_CAPS, [{ links: 0 }])).toBe(758);
    expect(funnelDailyWrites(FUNNEL_CAPS, [{ links: 20 }])).toBe(1424);
  });
  it("a site that lists landings or hops is modeled, one that does not is not (through the real site list)", () => {
    const site = (extra: object) => JSON.stringify({ a: { hosts: ["a.example.test"], mode: "beacon", pages: ["home"], link_ids: {}, ...extra } });
    const plain = assessDailyBudget({ SITES: site({}) }).funnel;
    expect(plain).toBe(758);
    expect(assessDailyBudget({ SITES: site({ landings: ["home"] }) }).funnel).toBeGreaterThan(plain);
    expect(assessDailyBudget({ SITES: site({ landings: ["home"], hops: ["blog"] }) }).funnel).toBe(1038);
    expect(assessDailyBudget({ SITES: site({ hops: ["blog"] }) }).funnel).toBeGreaterThan(plain);
  });
});
