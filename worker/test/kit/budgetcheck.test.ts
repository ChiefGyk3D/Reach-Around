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
import { pageViewDailyWrites } from "../../src/pvlimits";

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
