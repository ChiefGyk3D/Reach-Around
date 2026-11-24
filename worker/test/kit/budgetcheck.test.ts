// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import {
  COMBINED_MARGIN,
  CLICK_DAILY_WRITES,
  FUNNEL_DAILY_WRITES,
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
import { FUNNEL_CAPS, funnelDailyWrites } from "../../src/funnelcaps";
import { parseClickCeiling } from "../../src/clickceiling";

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

describe("the beacon lane row (one more row per accepted view while BEACON_MODE is on)", () => {
  const off = 22_700;
  const on = Math.ceil((24 * 150 * 33) / 5) + 2_540; // 6.6 rows per view
  it("is unchanged while the mode is off, unset or empty", () => {
    for (const m of [undefined, "", "off"]) expect(pageViewDailyWrites({ BEACON_MODE: m }), String(m)).toBe(off);
  });
  it("is one row per view higher in observe and enforce, and for a value it cannot read (the worst case)", () => {
    for (const m of ["observe", "enforce", "Observe", "junk"]) expect(pageViewDailyWrites({ BEACON_MODE: m }), m).toBe(on);
    expect(on).toBeGreaterThan(off);
  });
  it("the deploy gate reads BEACON_MODE, and refuses a value the Worker cannot use", () => {
    expect(runBudgetCheck({ BEACON_MODE: "observe" }).lines.join(" ")).toContain(`page views ${on}`);
    expect(runBudgetCheck({ BEACON_MODE: "observe" }).code).toBe(0);
    for (const m of ["Observe", "on", "1", "enforce"]) {
      const r = runBudgetCheck({ BEACON_MODE: m });
      expect(r.code, m).toBe(1);
      expect(r.lines.join(" ")).toContain("BEACON_MODE");
    }
    expect(runBudgetCheck({ BEACON_MODE: "off" }).code).toBe(0);
  });
});

describe("the city counter row (one more row per accepted view while a site's level is city, #778)", () => {
  const off = 22_700;
  const city = Math.ceil((24 * 150 * 33) / 5) + 2_540; // 6.6 rows per view
  const both = Math.ceil((24 * 150 * 38) / 5) + 2_540; // 7.6 rows per view
  it("is unchanged at the default level (region) and for country and for a value the Worker cannot use", () => {
    for (const g of [undefined, "", "region", "country", "junk"]) {
      expect(pageViewDailyWrites({ GEO_LEVEL: g }), String(g)).toBe(off);
    }
  });
  it("adds one row per view for the global level city and for any site at city", () => {
    expect(pageViewDailyWrites({ GEO_LEVEL: "city" })).toBe(city);
    const sites = JSON.stringify({ links: { hosts: ["links.example.test"], mode: "beacon", pages: ["home"], geo: "city" } });
    expect(pageViewDailyWrites({ SITES: sites })).toBe(city);
    expect(pageViewDailyWrites({ GEO_LEVEL: "city", BEACON_MODE: "observe" })).toBe(both);
  });
});

describe("the page-view budget in the kit's combined check", () => {
  it("uses the configured budgets, not the fixed default", () => {
    const e = { VIEWS_PER_HOUR: "1000", BOT_VIEWS_PER_HOUR: "200" };
    const c = loadKitLimits({ ...e, D1_DAILY_WRITE_QUOTA: "999999999" });
    expect(combinedDailyWrites(c, pageViewDailyWrites(e))).toBeGreaterThan(combinedDailyWrites(c));
    // 1000+200 per hour: one site is held to the account ceilings (480 and 120), so this still fits
    expect(() => loadKitLimits(e)).not.toThrow();
    // 10000+2000 per hour with ceilings raised to match: 24*12000*5.6 = 1,612,800 rows a day alone, over the default quota
    const big = { VIEWS_PER_HOUR: "10000", BOT_VIEWS_PER_HOUR: "2000", ACCOUNT_VIEWS_PER_HOUR: "10000", ACCOUNT_BOT_VIEWS_PER_HOUR: "2000" };
    expect(() => loadKitLimits(big)).toThrow(KitLimitsError);
    expect(() => loadKitLimits({ ...big, D1_DAILY_WRITE_QUOTA: "999999999" })).not.toThrow();
    expect(() => loadKitLimits({})).not.toThrow();
  });
  it("matches the arithmetic exactly at the quota boundary", () => {
    const kit = worstCaseDailyWrites(loadKitLimits({}));
    // choose the quota to equal the combined figure at pv=500, pvbot=100
    const e = { VIEWS_PER_HOUR: "500", BOT_VIEWS_PER_HOUR: "100" };
    const need = kit + pageViewDailyWrites(e) + CLICK_DAILY_WRITES + FUNNEL_DAILY_WRITES + COMBINED_MARGIN;
    expect(() => loadKitLimits({ ...e, D1_DAILY_WRITE_QUOTA: String(need) })).not.toThrow();
    expect(() => loadKitLimits({ ...e, D1_DAILY_WRITE_QUOTA: String(need - 1) })).toThrow(KitLimitsError);
  });
});

/** The default figure without the funnel: kit 46040 + page views 22700 + clicks 300501 + margin 5000. */
const BASE = 73_740 + CLICK_DAILY_WRITES;

describe("assessDailyBudget agrees with loadKitLimits", () => {
  const cases: Record<string, string>[] = [
    {},
    { VIEWS_PER_HOUR: "200" },
    { VIEWS_PER_HOUR: "300", BOT_VIEWS_PER_HOUR: "60" },
    { VIEWS_PER_HOUR: "700" },
    { ROUTE_BUDGET_GATE: "200" },
    { D1_DAILY_WRITE_QUOTA: "abc" },
    { D1_DAILY_WRITE_QUOTA: String(BASE + FUNNEL_DAILY_WRITES) },
    { D1_DAILY_WRITE_QUOTA: String(BASE - 21 + FUNNEL_DAILY_WRITES) },
    { ROUTE_BUDGET_GATE: "8000" },
    { ROUTE_BUDGET_GATE: "20000" },
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
    const total = BASE + FUNNEL_DAILY_WRITES;
    expect(r.lines).toEqual([`combined worst-case daily D1 writes ${total} of 1600000 (${((100 * total) / 1_600_000).toFixed(1)} percent; page views 22700; funnel ${FUNNEL_DAILY_WRITES}): ok`]);
  });
  it("warns, exit 0, above 80 percent, and not at exactly 80", () => {
    // gate 8000/h: kit 24*(8000*5+600)+10040 = 1,000,000+ ; combined = 1,313,398 on the default funnel, 82 percent of 1600000
    const r = runBudgetCheck({ ROUTE_BUDGET_GATE: "8000" });
    expect(r.code).toBe(0);
    expect(r.lines[0]).toMatch(/^::warning::.*above 80 percent$/);
    const total = BASE + FUNNEL_DAILY_WRITES;
    const at80 = Math.ceil((total * 10) / 8); // the smallest quota at which total is still at most 80 percent
    expect(runBudgetCheck({ D1_DAILY_WRITE_QUOTA: String(at80) }).lines[0]).toMatch(/^combined .*: ok$/);
    expect(runBudgetCheck({ D1_DAILY_WRITE_QUOTA: String(at80 - 1) }).lines[0]).toMatch(/^::warning::/);
  });
  it("fails, exit 1, over the quota and names no value", () => {
    const r = runBudgetCheck({ ROUTE_BUDGET_GATE: "20000" });
    expect(r.code).toBe(1);
    expect(r.lines[0]).toMatch(/^::error::.*is over the quota$/);
    expect(runBudgetCheck({ D1_DAILY_WRITE_QUOTA: String(BASE - 21 + FUNNEL_DAILY_WRITES) }).code).toBe(1);
    expect(runBudgetCheck({ D1_DAILY_WRITE_QUOTA: String(BASE + FUNNEL_DAILY_WRITES) }).code).toBe(0);
  });
  it("a free-plan quota (100000 rows a day) no longer fits the click ceiling, so the check refuses it", () => {
    const r = runBudgetCheck({ D1_DAILY_WRITE_QUOTA: "100000" });
    expect(r.code).toBe(1);
    expect(r.lines[0]).toMatch(/is over the quota$/);
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
    // A list that does not load might serve a city-level site, so it counts the city counter row too.
    expect(pageViewDailyWrites({ SITES: "{not json" })).toBe(pageViewDailyWrites({ GEO_LEVEL: "city" }, MAX_SITES));
    expect(pageViewDailyWrites({ SITES: "{not json" })).toBeGreaterThan(worst);
    for (const bad of [0, -1, 1.5, Number.NaN, MAX_SITES + 1]) {
      expect(pageViewDailyWrites({}, bad)).toBe(worst);
    }
  });
  it("REGRESSION: N sites at the per-site cap project the account ceiling, never just one site's budget", () => {
    const e = { VIEWS_PER_HOUR: "200", BOT_VIEWS_PER_HOUR: "50" };
    // old projection: one site only = rows(250). Real worst case with 8 sites: ceilings 480 + 120.
    expect(pageViewDailyWrites(e, 8)).toBe(rows(600));
    expect(pageViewDailyWrites(e, 8)).toBeGreaterThan(rows(250));
    // the eight-site total is the larger one, through the kit's own function (the refusal at a quota
    // between the two is the next test)
    const sites = siteList(8);
    expect(assessDailyBudget({ ...e, SITES: sites }).combined).toBeGreaterThan(assessDailyBudget(e).combined);
    // one site at the same budgets is within the quota
    expect(assessDailyBudget(e).level).not.toBe("fail");
  });
  it("the same refusal holds at a quota between one site and eight sites", () => {
    const e = { VIEWS_PER_HOUR: "200", BOT_VIEWS_PER_HOUR: "50" };
    const eight = assessDailyBudget({ ...e, SITES: siteList(8) }).combined;
    const one = assessDailyBudget(e).combined;
    expect(eight).toBeGreaterThan(one);
    const quota = String(eight - 1);
    expect(() => loadKitLimits({ ...e, SITES: siteList(8), D1_DAILY_WRITE_QUOTA: quota })).toThrow(KitLimitsError);
    expect(assessDailyBudget({ ...e, SITES: siteList(8), D1_DAILY_WRITE_QUOTA: quota }).level).toBe("fail");
    expect(assessDailyBudget({ ...e, D1_DAILY_WRITE_QUOTA: quota }).level).not.toBe("fail");
  });
});

describe("the deploy check counts sites and ceilings", () => {
  const one = { SITE_HOST: "links.example.test", PAGE_IDS: '["home"]', LINK_IDS: '["a"]' };
  it("the one-site shorthand from the workflow is one site", () => {
    // One link id adds reserved funnel cells, so compare the site-count part (page views) and the funnel at one site.
    expect(assessDailyBudget(one).pageViews).toBe(assessDailyBudget({}).pageViews);
    expect(assessDailyBudget(one).funnel).toBe(funnelDailyWrites(FUNNEL_CAPS, [{ links: 1 }]));
  });
  it("refuses a SITES text that makes the total pass the quota", () => {
    const eight = assessDailyBudget({ SITES: siteList(8) }).combined;
    const r = runBudgetCheck({ SITES: siteList(8), D1_DAILY_WRITE_QUOTA: String(eight - 1) });
    expect(r.code).toBe(1);
    expect(r.lines[0]).toMatch(/is over the quota$/);
    expect(runBudgetCheck({ SITES: siteList(8), D1_DAILY_WRITE_QUOTA: String(eight) }).code).toBe(0);
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
import { PAGE_VIEW_MODE_VARS, PAGE_VIEW_VARS } from "../../src/pvlimits";
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
    expect([...new Set(read)].sort()).toEqual([...PAGE_VIEW_VARS, ...PAGE_VIEW_MODE_VARS].sort());
  });
  it("the gate passes on all of them", () => {
    for (const n of [...KIT_LIMIT_VARS, ...PAGE_VIEW_VARS, ...PAGE_VIEW_MODE_VARS, "SITES", "SITE_HOST", "PAGE_IDS", "LINK_IDS"]) {
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
      CLICK_DAILY_WRITE_CEILING: "999",
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
    const warn = runBudgetCheck({ ROUTE_BUDGET_GATE: "8000" });
    expect(warn.lines[0]).toMatch(/^::warning::/);
    expect(runBudgetCheck({ ROUTE_BUDGET_GATE: "20000" }).code).toBe(1);
    expect(runBudgetCheck({ D1_DAILY_WRITE_QUOTA: "50000" }).code).toBe(1);
    expect(runBudgetCheck({ D1_DAILY_WRITE_QUOTA: "2000000" }).lines[0]).toMatch(/: ok$/);
  });
});

describe("the first-click reserve is checked against the number of links", () => {
  // One site, no optional lists: a click is charged 9. At a 1000 ceiling the site's reserve is 200 (20 percent),
  // which holds 22 first clicks (22 x 9 = 198), not 23.
  const ids = (n: number) => JSON.stringify(Array.from({ length: n }, (_, i) => `l${i}`));
  const one = (n: number, extra: Record<string, string> = {}) => ({
    SITE_HOST: "links.example.test",
    PAGE_IDS: '["home"]',
    LINK_IDS: ids(n),
    CLICK_DAILY_WRITE_CEILING: "1000",
    D1_DAILY_WRITE_QUOTA: "999999999",
    ...extra,
  });
  it.each([
    [22, 0],
    [23, 1],
  ])("%i links at a 1000 ceiling: exit %i", (n, code) => {
    const r = runBudgetCheck(one(n));
    expect(r.code).toBe(code);
    if (code === 1) {
      expect(r.lines).toEqual(["::error::CLICK_DAILY_WRITE_CEILING is too small to keep a first click for every link: raise it"]);
    }
  });
  it("the model reports it and the level is fail, whatever the quota", () => {
    expect(assessDailyBudget(one(23)).reserveShort).toBe(true);
    expect(assessDailyBudget(one(23)).level).toBe("fail");
    expect(assessDailyBudget(one(22)).reserveShort).toBe(false);
    expect(assessDailyBudget(one(22)).level).not.toBe("fail");
  });
  it("uses each site's own maximum charge (a site that lists everything is charged 13)", () => {
    const sites = (n: number) =>
      JSON.stringify({
        a: {
          hosts: ["a.example.test"], mode: "beacon", pages: ["home"],
          link_ids: Object.fromEntries(Array.from({ length: n }, (_, i) => [`l${i}`, "home"])),
          sources: ["x"], landings: ["home"], hops: ["blog"],
        },
      });
    // 200 / 13 = 15 first clicks
    expect(assessDailyBudget({ SITES: sites(15), CLICK_DAILY_WRITE_CEILING: "1000", D1_DAILY_WRITE_QUOTA: "999999999" }).reserveShort).toBe(false);
    expect(assessDailyBudget({ SITES: sites(16), CLICK_DAILY_WRITE_CEILING: "1000", D1_DAILY_WRITE_QUOTA: "999999999" }).reserveShort).toBe(true);
  });
  it("more sites share the ceiling, so each site's reserve is smaller", () => {
    const two = JSON.stringify({
      a: { hosts: ["a.example.test"], mode: "beacon", pages: ["home"], link_ids: Object.fromEntries(Array.from({ length: 12 }, (_, i) => [`l${i}`, "home"])) },
      b: { hosts: ["b.example.test"], mode: "beacon", pages: ["home"], link_ids: {} },
    });
    // share 500, reserve 100: 11 first clicks at 9, so 12 links is short
    expect(assessDailyBudget({ SITES: two, CLICK_DAILY_WRITE_CEILING: "1000", D1_DAILY_WRITE_QUOTA: "999999999" }).reserveShort).toBe(true);
    expect(assessDailyBudget({ SITES: two, CLICK_DAILY_WRITE_CEILING: "2000", D1_DAILY_WRITE_QUOTA: "999999999" }).reserveShort).toBe(false);
  });
  it("the defaults and the documented Free-plan pair keep every link a first click up to a large site", () => {
    expect(assessDailyBudget({ SITE_HOST: "links.example.test", PAGE_IDS: '["home"]', LINK_IDS: ids(200) }).reserveShort).toBe(false);
    expect(assessDailyBudget({ SITE_HOST: "links.example.test", PAGE_IDS: '["home"]', LINK_IDS: ids(115), D1_DAILY_WRITE_QUOTA: "100000", CLICK_DAILY_WRITE_CEILING: "7500" }).reserveShort).toBe(false);
  });
});

describe("a click-only deployment (page views off) is checked with the same function", () => {
  it("passes the Free-plan pair and refuses the default ceiling on a Free quota", () => {
    const base = { SITE_HOST: "links.example.test", PAGE_IDS: '["home"]', LINK_IDS: '["alpha"]' };
    expect(runBudgetCheck({ ...base, D1_DAILY_WRITE_QUOTA: "100000", CLICK_DAILY_WRITE_CEILING: "7500" }).code).toBe(0);
    expect(runBudgetCheck({ ...base, D1_DAILY_WRITE_QUOTA: "100000" }).code).toBe(1);
  });
});

describe("the click ceiling setting in the budget check (Free-plan deployment)", () => {
  const free = { D1_DAILY_WRITE_QUOTA: "100000", CLICK_DAILY_WRITE_CEILING: "7500" };
  it("the documented Free-plan pair passes with a warning (82.5 percent since the claim rows are counted, #650)", () => {
    const r = runBudgetCheck(free);
    expect(r.code).toBe(0);
    expect(r.lines[0]).toMatch(/^::warning::.*above 80 percent$/);
    expect(r.lines[0]).toContain(`${81_741 + FUNNEL_DAILY_WRITES} of 100000`);
    expect(assessDailyBudget(free).combined).toBe(81_741 + FUNNEL_DAILY_WRITES);
  });
  it("the Free-plan quota alone (default ceiling) is refused, and so is the ceiling alone on a too-small quota", () => {
    expect(runBudgetCheck({ D1_DAILY_WRITE_QUOTA: "100000" }).code).toBe(1);
    // 73740 + ceiling + 501 + funnel (758) must stay at or under 100000: 25001 is the largest ceiling
    expect(runBudgetCheck({ D1_DAILY_WRITE_QUOTA: "100000", CLICK_DAILY_WRITE_CEILING: "25001" }).code).toBe(0);
    expect(runBudgetCheck({ D1_DAILY_WRITE_QUOTA: "100000", CLICK_DAILY_WRITE_CEILING: "25002" }).code).toBe(1);
  });
  it("the verdict follows the configured ceiling exactly (the Worker and the check read one value)", () => {
    for (const c of ["1000", "7500", "300000", "5000000"]) {
      const a = assessDailyBudget({ CLICK_DAILY_WRITE_CEILING: c, D1_DAILY_WRITE_QUOTA: "999999999" });
      expect(a.combined).toBe(73_740 + Number(c) + 501 + FUNNEL_DAILY_WRITES);
      expect(combinedDailyWrites(loadKitLimits({ CLICK_DAILY_WRITE_CEILING: c, D1_DAILY_WRITE_QUOTA: "999999999" }), a.pageViews, a.funnel)).toBe(a.combined);
    }
  });
  it("an unusable ceiling fails the deploy check by name, but does not take the kit's own limits down", () => {
    for (const bad of ["999", "10000001", "1e5", "abc", "-1"]) {
      expect(runBudgetCheck({ CLICK_DAILY_WRITE_CEILING: bad })).toEqual({ code: 1, lines: ["::error::unusable budget settings: CLICK_DAILY_WRITE_CEILING"] });
      // the click route refuses and flags it; the kit routes and the owner alerts keep their own limits
      expect(() => loadKitLimits({ CLICK_DAILY_WRITE_CEILING: bad })).not.toThrow();
      expect(assessDailyBudget({ CLICK_DAILY_WRITE_CEILING: bad }).invalid).toEqual(["CLICK_DAILY_WRITE_CEILING"]);
    }
    // another bad variable still refuses the kit, whatever the ceiling says
    expect(() => loadKitLimits({ CLICK_DAILY_WRITE_CEILING: "abc", MAX_LEADS: "0" })).toThrow(KitLimitsError);
  });
  it("the Worker's click parser and the budget loader agree on every usable value", () => {
    for (const v of [undefined, "", "300000", "1000", "10000000", "007500"]) {
      const worker = parseClickCeiling(v);
      expect(worker, String(v)).not.toBeNull();
      expect(loadKitLimits({ CLICK_DAILY_WRITE_CEILING: v, D1_DAILY_WRITE_QUOTA: "999999999" }).clickCeiling, String(v)).toBe(worker);
    }
    for (const v of ["999", "10000001", "12x", "-1", "0"]) {
      expect(parseClickCeiling(v), v).toBeNull();
      expect(assessDailyBudget({ CLICK_DAILY_WRITE_CEILING: v }).invalid, v).toEqual(["CLICK_DAILY_WRITE_CEILING"]);
    }
  });
});
