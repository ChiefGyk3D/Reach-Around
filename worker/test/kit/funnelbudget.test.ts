// SPDX-License-Identifier: Apache-2.0
// Issue #332, criterion 1: the deploy-time budget check models the click funnel
// tables (migration 0011) at their configured caps, and the Worker and the check
// read one set of caps.
import { env as testEnv } from "cloudflare:workers";
import { describe, expect, it } from "vitest";
import {
  COUNTRY_DAY_CAP,
  COUNTRY_MONTH_CAP,
  HOUR_BUDGET,
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
  return worstCaseDailyWrites(c) + pageViewDailyWrites({}) + 8_000 + 5_000;
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
  it("the deploy check reports it and still passes at the defaults", () => {
    const r = runBudgetCheck({});
    expect(r.code).toBe(0);
    expect(r.lines[0]).toContain(`funnel ${assessDailyBudget({}).funnel}`);
  });
});

describe("(b) a cap set too high fails the check", () => {
  const high: FunnelCaps = { ...FUNNEL_CAPS, srcDayCap: 5_000, countryDayCap: 5_000, hourBudget: 5_000, srcMonthCap: 5_000, countryMonthCap: 5_000 };
  it("the same environment passes with the profile caps and fails with high caps", () => {
    expect(assessDailyBudget({}, FUNNEL_CAPS).level).not.toBe("fail");
    const a = assessDailyBudget({}, high);
    expect(a.level).toBe("fail");
    expect(a.funnel).toBe(funnelDailyWrites(high, [{ links: 0 }]));
  });
  it("the profile table itself passes the default budget for the Free profile", () => {
    expect(assessDailyBudget({}, FUNNEL_PROFILES.free).level).not.toBe("fail");
    expect(FUNNEL_CAPS).toBe(FUNNEL_PROFILES.free);
  });
  it("the Free caps are lower than the standard profile on every cap", () => {
    for (const k of Object.keys(FUNNEL_PROFILES.free) as (keyof FunnelCaps)[]) {
      if (k === "reservedPerLink") continue;
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
    }).toEqual(FUNNEL_CAPS);
  });
  it("real rows written by a flood never exceed the cells the model allows", async () => {
    const DB = testEnv.DB;
    for (const t of ["click_src_daily", "click_country_daily", "click_seal", "click_budget", "click_capacity"]) await DB.prepare(`DELETE FROM ${t}`).run();
    const links = 12;
    const ts = Date.UTC(2027, 0, 5, 3);
    for (let i = 0; i < 700; i++) {
      const hour = i % 24;
      await DB.batch(
        clickCounterStatements(DB, {
          site: "links", ts: ts + hour * 3_600_000, linkId: `l${i % links}`, src: `s${i}`, listsSources: true,
          country: `${String.fromCharCode(65 + (i % 26))}${String.fromCharCode(65 + (Math.floor(i / 26) % 26))}`,
        }),
      );
    }
    const n = async (t: string) => ((await DB.prepare(`SELECT count(*) AS n FROM ${t}`).all<{ n: number }>()).results[0]!.n);
    const observed = (await n("click_src_daily")) + (await n("click_country_daily"));
    expect(observed).toBeGreaterThan(FUNNEL_CAPS.srcDayCap); // the flood really reached the caps
    expect(observed).toBeLessThanOrEqual(funnelRowsPerSiteDay(FUNNEL_CAPS, links));
  });
});
