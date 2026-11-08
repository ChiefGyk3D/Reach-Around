// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import {
  COMBINED_MARGIN,
  CLICK_DAILY_WRITES,
  PAGE_VIEW_DAILY_WRITES,
  assessDailyBudget,
  combinedDailyWrites,
  loadKitLimits,
  KitLimitsError,
  worstCaseDailyWrites,
} from "../../src/kit/budgets";
import { runBudgetCheck } from "../../src/kit/budgetcheck";
import { pageViewDailyWrites, pageViewSiteCount } from "../../src/pvlimits";
import { MAX_SITES } from "../../src/sites";

describe("pageViewDailyWrites", () => {
  it("is exactly the documented 22,700 at the default budgets (120 human + 30 bot per hour)", () => {
    expect(pageViewDailyWrites({})).toBe(22_700);
    expect(PAGE_VIEW_DAILY_WRITES).toBe(22_700);
    // 24 x 150 = 3,600 views x 5.6 = 20,160 per-view rows, plus 2,540 fixed
    expect(Math.ceil((24 * 150 * 28) / 5)).toBe(20_160);
  });
  it("grows with each hourly budget, bots included", () => {
    const base = pageViewDailyWrites({});
    expect(pageViewDailyWrites({ VIEWS_PER_HOUR: "121" })).toBeGreaterThan(base);
    expect(pageViewDailyWrites({ BOT_VIEWS_PER_HOUR: "31" })).toBeGreaterThan(base);
    expect(pageViewDailyWrites({ VIEWS_PER_HOUR: "0", BOT_VIEWS_PER_HOUR: "0" })).toBe(2_540);
  });
  it("reads a bad value as the Worker does: the default", () => {
    for (const bad of ["abc", "-1", "1e3", " 5", "1000000000"]) {
      expect(pageViewDailyWrites({ VIEWS_PER_HOUR: bad })).toBe(22_700);
    }
  });
});

describe("the page-view budget in the kit's combined check", () => {
  it("uses the configured budgets, not the fixed default", () => {
    const e = { VIEWS_PER_HOUR: "1000", BOT_VIEWS_PER_HOUR: "200" };
    const c = loadKitLimits({ ...e, D1_DAILY_WRITE_QUOTA: "999999999" });
    expect(combinedDailyWrites(c, pageViewDailyWrites(e))).toBeGreaterThan(combinedDailyWrites(c));
    // 1000+200 per hour: 24*1200*5.6 = 161,280 rows a day alone: over the default free quota
    expect(() => loadKitLimits(e)).toThrow(KitLimitsError);
    expect(() => loadKitLimits({})).not.toThrow();
  });
  it("matches the arithmetic exactly at the 100,000 boundary", () => {
    const kit = worstCaseDailyWrites(loadKitLimits({}));
    // choose the quota to equal the combined figure at pv=500, pvbot=100
    const e = { VIEWS_PER_HOUR: "500", BOT_VIEWS_PER_HOUR: "100" };
    const need = kit + pageViewDailyWrites(e) + CLICK_DAILY_WRITES + COMBINED_MARGIN;
    expect(() => loadKitLimits({ ...e, D1_DAILY_WRITE_QUOTA: String(need) })).not.toThrow();
    expect(() => loadKitLimits({ ...e, D1_DAILY_WRITE_QUOTA: String(need - 1) })).toThrow(KitLimitsError);
  });
});

describe("assessDailyBudget agrees with loadKitLimits", () => {
  const cases: Record<string, string>[] = [
    {},
    { VIEWS_PER_HOUR: "200" },
    { VIEWS_PER_HOUR: "300", BOT_VIEWS_PER_HOUR: "60" },
    { VIEWS_PER_HOUR: "700" },
    { ROUTE_BUDGET_GATE: "200" },
    { D1_DAILY_WRITE_QUOTA: "abc" },
    { D1_DAILY_WRITE_QUOTA: "78120" },
    { D1_DAILY_WRITE_QUOTA: "78119" },
  ];
  for (const e of cases) {
    it(`verdict for ${JSON.stringify(e)}`, () => {
      const a = assessDailyBudget(e);
      let threw = false;
      let warned = false;
      try {
        warned = loadKitLimits(e).warnings.length > 0;
      } catch {
        threw = true;
      }
      expect(a.level === "fail").toBe(threw);
      expect(a.level === "warn").toBe(warned);
    });
  }
});

describe("runBudgetCheck (the deploy-time gate)", () => {
  it("passes quietly at the defaults and says ok", () => {
    const r = runBudgetCheck({});
    expect(r.code).toBe(0);
    expect(r.lines).toEqual(["combined worst-case daily D1 writes 78120 of 100000 (78.1 percent; page views 22700): ok"]);
  });
  it("warns, exit 0, above 80 percent, and not at exactly 80", () => {
    // pv 200: page views 24*230*5.6=30912+2540=33452; combined = 42420+33452+8000+5000 = 88872
    const r = runBudgetCheck({ VIEWS_PER_HOUR: "200" });
    expect(r.code).toBe(0);
    expect(r.lines[0]).toMatch(/^::warning::.*above 80 percent$/);
    const at80 = runBudgetCheck({ D1_DAILY_WRITE_QUOTA: "97650" }); // 78120 is exactly 80.0 percent of 97650
    expect(at80.lines[0]).toMatch(/^combined .*: ok$/);
    expect(runBudgetCheck({ D1_DAILY_WRITE_QUOTA: "97649" }).lines[0]).toMatch(/^::warning::/);
  });
  it("fails, exit 1, over the quota and names no value", () => {
    const r = runBudgetCheck({ VIEWS_PER_HOUR: "900" });
    expect(r.code).toBe(1);
    expect(r.lines[0]).toMatch(/^::error::.*is over the quota$/);
    expect(runBudgetCheck({ D1_DAILY_WRITE_QUOTA: "78119" }).code).toBe(1);
    expect(runBudgetCheck({ D1_DAILY_WRITE_QUOTA: "78120" }).code).toBe(0);
  });
  it("fails on an unusable value, by name only", () => {
    for (const bad of ["abc", "1e3", "-5", "100001", "1.5", "007x", "9999999"]) {
      const r = runBudgetCheck({ BOT_VIEWS_PER_HOUR: bad });
      expect(r.code).toBe(1);
      expect(r.lines).toEqual(["::error::unusable budget settings: BOT_VIEWS_PER_HOUR"]);
    }
    const q = runBudgetCheck({ D1_DAILY_WRITE_QUOTA: "12x" });
    expect(q.code).toBe(1);
    expect(q.lines[0]).toContain("D1_DAILY_WRITE_QUOTA");
    expect(q.lines[0]).not.toContain("12x");
  });
  it("treats an empty variable as unset (the workflow passes empty strings)", () => {
    expect(runBudgetCheck({ VIEWS_PER_HOUR: "", BOT_VIEWS_PER_HOUR: "" })).toEqual(runBudgetCheck({}));
  });
  it("ignores variables it does not own", () => {
    expect(runBudgetCheck({ PATH: "x", VIEWS_KILL: "1" })).toEqual(runBudgetCheck({}));
  });
});

// ---- the binding ceilings (account-wide ceiling and the number of sites) ----

const siteList = (n: number) =>
  JSON.stringify(
    Object.fromEntries(
      Array.from({ length: n }, (_, i) => [
        `s${i}`,
        { hosts: [`s${i}.example.test`], mode: "beacon", pages: ["home"], link_ids: {} },
      ]),
    ),
  );
/** Rows per day for a given accepted views per hour (humans + bots). */
const rows = (perHour: number) => Math.ceil((24 * perHour * 28) / 5) + 2_540;

describe("pageViewDailyWrites uses the binding ceiling per class", () => {
  it("one site: the per-site budget binds below the account ceiling", () => {
    expect(pageViewDailyWrites({ SITE_HOST: "links.example.test", PAGE_IDS: '["home"]' })).toBe(rows(150));
    expect(pageViewDailyWrites({}, 1)).toBe(rows(150));
  });
  it("one site: an account ceiling below the per-site budget binds", () => {
    expect(pageViewDailyWrites({ VIEWS_PER_HOUR: "400", ACCOUNT_VIEWS_PER_HOUR: "100" }, 1)).toBe(rows(100 + 30));
    // the default account ceiling (480 / 120) binds a single site that asks for more
    expect(pageViewDailyWrites({ VIEWS_PER_HOUR: "1000", BOT_VIEWS_PER_HOUR: "500" }, 1)).toBe(rows(480 + 120));
  });
  it("many sites where the per-site budgets bind: the sum of the sites", () => {
    // 3 x 120 = 360 < 480 humans; 3 x 30 = 90 < 120 bots
    expect(pageViewDailyWrites({}, 3)).toBe(rows(360 + 90));
  });
  it("many sites where the account ceiling binds", () => {
    // 8 x 120 = 960 > 480, 8 x 30 = 240 > 120
    expect(pageViewDailyWrites({}, 8)).toBe(rows(480 + 120));
    expect(pageViewDailyWrites({ ACCOUNT_VIEWS_PER_HOUR: "200", ACCOUNT_BOT_VIEWS_PER_HOUR: "50" }, 4)).toBe(rows(200 + 50));
  });
  it("humans and bots are bound separately", () => {
    // humans: 2 x 120 = 240 (sum binds); bots: 2 x 30 = 60 but ceiling 40 binds
    expect(pageViewDailyWrites({ ACCOUNT_BOT_VIEWS_PER_HOUR: "40" }, 2)).toBe(rows(240 + 40));
    // humans: ceiling 100 binds; bots: 2 x 30 = 60 (sum binds)
    expect(pageViewDailyWrites({ ACCOUNT_VIEWS_PER_HOUR: "100" }, 2)).toBe(rows(100 + 60));
  });
  it("counts the sites from the SITES text with the Worker's own loader", () => {
    expect(pageViewSiteCount({ SITES: siteList(4) })).toBe(4);
    expect(pageViewDailyWrites({ SITES: siteList(4) })).toBe(rows(480 + 120));
    expect(pageViewSiteCount({})).toBe(1);
  });
  it("fails closed: a site list that does not load, or a bad count, is the worst case", () => {
    const worst = pageViewDailyWrites({}, MAX_SITES);
    expect(pageViewSiteCount({ SITES: "{not json" })).toBe(MAX_SITES);
    expect(pageViewDailyWrites({ SITES: "{not json" })).toBe(worst);
    for (const bad of [0, -1, 1.5, Number.NaN, MAX_SITES + 1]) {
      expect(pageViewDailyWrites({}, bad)).toBe(worst);
    }
  });
  it("REGRESSION: N sites at the per-site cap project the account ceiling, never just one site's budget", () => {
    const e = { VIEWS_PER_HOUR: "200", BOT_VIEWS_PER_HOUR: "50" };
    // old projection: one site only = rows(250). Real worst case with 8 sites: ceilings 480 + 120.
    expect(pageViewDailyWrites(e, 8)).toBe(rows(600));
    expect(pageViewDailyWrites(e, 8)).toBeGreaterThan(rows(250));
    // and the total is refused through the kit's own function
    const sites = siteList(8);
    expect(() => loadKitLimits({ ...e, SITES: sites })).toThrow(KitLimitsError);
    expect(assessDailyBudget({ ...e, SITES: sites }).level).toBe("fail");
    // one site at the same budgets is still within the quota
    expect(assessDailyBudget(e).level).not.toBe("fail");
  });
});

describe("the deploy check counts sites and ceilings", () => {
  const one = { SITE_HOST: "links.example.test", PAGE_IDS: '["home"]', LINK_IDS: '["a"]' };
  it("the one-site shorthand from the workflow is one site", () => {
    expect(runBudgetCheck(one)).toEqual(runBudgetCheck({}));
  });
  it("refuses a SITES text that makes the total pass the quota", () => {
    const r = runBudgetCheck({ SITES: siteList(8) });
    expect(r.code).toBe(1);
    expect(r.lines[0]).toMatch(/is over the quota$/);
  });
  it("fails closed when the site list does not load", () => {
    for (const bad of [{ SITES: "{not json" }, { SITE_HOST: "NOT A HOST", PAGE_IDS: '["home"]' }, { SITE_HOST: "links.example.test", PAGE_IDS: "x" }]) {
      const r = runBudgetCheck(bad);
      expect(r.code).toBe(1);
      expect(r.lines[0]).toContain("site list does not load");
    }
  });
  it("fails closed on an unusable account ceiling, by name", () => {
    for (const n of ["ACCOUNT_VIEWS_PER_HOUR", "ACCOUNT_BOT_VIEWS_PER_HOUR"]) {
      const r = runBudgetCheck({ [n]: "12x" });
      expect(r).toEqual({ code: 1, lines: [`::error::unusable budget settings: ${n}`] });
    }
  });
  it("a lower account ceiling lowers the projection", () => {
    const hi = assessDailyBudget({});
    const lo = assessDailyBudget({ ACCOUNT_VIEWS_PER_HOUR: "50" });
    expect(lo.pageViews).toBeLessThan(hi.pageViews);
  });
});

// ---- the gate sees the same environment the Worker's loader reads ----

import budgetsSrc from "../../src/kit/budgets.ts?raw";
import pvSrc from "../../src/pvlimits.ts?raw";
import { KIT_LIMIT_VARS } from "../../src/kit/budgets";
import { PAGE_VIEW_VARS } from "../../src/pvlimits";
import { BUDGET_ENV_VARS } from "../../src/kit/budgetcheck";

describe("the deploy gate reads every variable the Worker's loader reads", () => {
  it("KIT_LIMIT_VARS is exactly the set parseKitLimits reads (enumerated from the source)", () => {
    const read = [...budgetsSrc.matchAll(/\bget\("([A-Z0-9_]+)"/g)].map((m) => m[1]);
    expect(read.length).toBeGreaterThan(0);
    expect([...read].sort()).toEqual([...KIT_LIMIT_VARS].sort());
  });
  it("PAGE_VIEW_VARS is exactly the set pageViewDailyWrites reads", () => {
    const read = [...pvSrc.matchAll(/\be\.([A-Z_]+)/g)].map((m) => m[1]);
    expect(read.length).toBeGreaterThan(0);
    expect([...new Set(read)].sort()).toEqual([...PAGE_VIEW_VARS].sort());
  });
  it("the gate passes on all of them", () => {
    for (const n of [...KIT_LIMIT_VARS, ...PAGE_VIEW_VARS, "SITES", "SITE_HOST", "PAGE_IDS", "LINK_IDS"]) {
      expect(BUDGET_ENV_VARS).toContain(n);
    }
  });
  it("an override of each kit limit reaches the verdict (a value the Worker refuses fails the gate)", () => {
    const bad: Record<string, string> = {
      ROUTE_BUDGET_GATE: "100001",
      ROUTE_BUDGET_VIEW: "100001",
      MAX_LEADS: "0",
      MAX_KIT_EVENTS: "0",
      DAILY_ALERT_BUDGET: "1001",
      DAILY_SEND_BUDGET: "1001",
      D1_DAILY_WRITE_QUOTA: "999",
    };
    for (const n of KIT_LIMIT_VARS) {
      expect(bad[n], n).toBeDefined();
      const r = runBudgetCheck({ [n]: bad[n] });
      expect(r.code, n).toBe(1);
      expect(r.lines[0], n).toContain(n);
    }
  });
  it("a kit limit override changes the verdict, not just the validation", () => {
    expect(runBudgetCheck({}).code).toBe(0);
    const warn = runBudgetCheck({ ROUTE_BUDGET_GATE: "200" });
    expect(warn.lines[0]).toMatch(/^::warning::/);
    expect(runBudgetCheck({ ROUTE_BUDGET_GATE: "400" }).code).toBe(1);
    expect(runBudgetCheck({ D1_DAILY_WRITE_QUOTA: "50000" }).code).toBe(1);
    expect(runBudgetCheck({ D1_DAILY_WRITE_QUOTA: "200000" }).lines[0]).toMatch(/: ok$/);
  });
});
