// SPDX-License-Identifier: Apache-2.0
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import {
  DEFAULTS,
  KitLimitsError,
  PURGE_RESERVE,
  WRITES_PER_OP,
  loadKitLimits,
  recordKitEventCapped,
  reserveAlert,
  reserveRoute,
  reserveSend,
  worstCaseDailyWrites,
  worstCaseStorageBytes,
} from "../../src/kit/budgets";
import { upsertLead } from "../../src/kit/leads";

const DB = testEnv.DB;
const T0 = Date.UTC(2027, 2, 14, 5, 30);
const HOUR = 3_600_000;

beforeEach(async () => {
  await DB.prepare("DELETE FROM counters").run();
  await DB.prepare("DELETE FROM kit_events").run();
  await DB.prepare("DELETE FROM leads").run();
});

const L = (e: Record<string, unknown> = {}) => loadKitLimits(e as never);

describe("loadKitLimits", () => {
  it("returns the cutover defaults", () => {
    const c = L();
    expect(c.routeGatePerHour).toBe(150);
    expect(c.routeViewPerHour).toBe(300);
    expect(c.maxLeads).toBe(5000);
    expect(c.dailySendBudget).toBe(0);
    expect(c.dailyAlertBudget).toBe(20);
    expect(c.warnings).toEqual([]);
    expect(DEFAULTS.maxLeads).toBe(5000);
  });
  it("treats empty and unset alike", () => {
    expect(L({ MAX_LEADS: "", ROUTE_BUDGET_GATE: undefined }).maxLeads).toBe(5000);
  });
  it("accepts in-range values and the boundaries", () => {
    const c = L({ ROUTE_BUDGET_GATE: "0", ROUTE_BUDGET_VIEW: "100000", MAX_LEADS: "1", MAX_KIT_EVENTS: "1", DAILY_ALERT_BUDGET: "0", DAILY_SEND_BUDGET: "0", D1_DAILY_WRITE_QUOTA: "10000000" });
    expect([c.routeGatePerHour, c.routeViewPerHour, c.maxLeads]).toEqual([0, 100_000, 1]);
  });
  it.each([
    ["ROUTE_BUDGET_GATE", "-1"],
    ["ROUTE_BUDGET_GATE", "100001"],
    ["ROUTE_BUDGET_VIEW", "999999999999999999999"],
    ["ROUTE_BUDGET_VIEW", "1e3"],
    ["ROUTE_BUDGET_VIEW", "0x10"],
    ["ROUTE_BUDGET_VIEW", " 5"],
    ["ROUTE_BUDGET_VIEW", "5 "],
    ["ROUTE_BUDGET_VIEW", "5.0"],
    ["ROUTE_BUDGET_VIEW", "+5"],
    ["ROUTE_BUDGET_VIEW", "NaN"],
    ["ROUTE_BUDGET_VIEW", "Infinity"],
    ["ROUTE_BUDGET_VIEW", "٥"],
    ["MAX_LEADS", "0"],
    ["MAX_LEADS", "50001"],
    ["MAX_KIT_EVENTS", "0"],
    ["MAX_KIT_EVENTS", "500001"],
    ["DAILY_ALERT_BUDGET", "1001"],
    ["DAILY_SEND_BUDGET", "-0"],
    ["D1_DAILY_WRITE_QUOTA", "999"],
  ])("rejects %s=%j and names only the variable", (name, value) => {
    try {
      L({ [name]: value });
      throw new Error("did not throw");
    } catch (e) {
      expect(e).toBeInstanceOf(KitLimitsError);
      expect((e as KitLimitsError).fields).toEqual([name]);
      expect((e as Error).message).not.toContain(value.trim() === "" ? "\u0000" : value);
    }
  });
  it.each([5, true, null, {}, [], ["5"], Symbol.for("x")])("rejects a non-string %s", (v) => {
    expect(() => L({ MAX_LEADS: v })).toThrow(KitLimitsError);
  });
  it("lists every bad variable at once", () => {
    try {
      L({ MAX_LEADS: "x", ROUTE_BUDGET_GATE: "y" });
    } catch (e) {
      expect((e as KitLimitsError).fields).toEqual(["ROUTE_BUDGET_GATE", "MAX_LEADS"]);
    }
  });
  it("refuses a configuration whose worst case exceeds the quota, naming the quota", () => {
    expect(() => L({ ROUTE_BUDGET_GATE: "100000" })).toThrow(KitLimitsError);
    try {
      L({ ROUTE_BUDGET_VIEW: "100000" });
    } catch (e) {
      expect((e as KitLimitsError).fields).toEqual(["D1_DAILY_WRITE_QUOTA"]);
    }
  });
  it("a raised quota lets the same budgets pass", () => {
    expect(() => L({ ROUTE_BUDGET_GATE: "100000", D1_DAILY_WRITE_QUOTA: "100000000" })).not.toThrow();
  });
  it("warns above 70 percent of the quota", () => {
    // 24*(5*g + 2*300) + 10000 = 70000+ -> g = 400: 24*(2000+600)+20+10000 = 72420
    const c = L({ ROUTE_BUDGET_GATE: "400" });
    expect(worstCaseDailyWrites(c)).toBe(72_420);
    expect(c.warnings).toEqual(["worst-case daily D1 writes are above 70 percent of D1_DAILY_WRITE_QUOTA"]);
  });
});

describe("worstCaseDailyWrites", () => {
  it("spells out the default arithmetic", () => {
    const c = L();
    const expected = 24 * (150 * WRITES_PER_OP.gate + 300 * WRITES_PER_OP.view) + 20 * 1 + 0 * 7 + PURGE_RESERVE;
    expect(worstCaseDailyWrites(c)).toBe(expected);
    expect(expected).toBe(42_420);
    expect(expected).toBeLessThan(c.d1DailyWriteQuota);
  });
  it("storage worst case is far under the 500 MB database limit at the defaults", () => {
    expect(worstCaseStorageBytes(L())).toBeLessThan(100 * 1024 * 1024);
  });
});

describe("reserveRoute", () => {
  const env = (e: Record<string, unknown> = {}) => ({ DB, ...e }) as never;
  it("grants exactly the hourly limit then refuses", async () => {
    const e = env({ ROUTE_BUDGET_GATE: "3" });
    const r = [];
    for (let i = 0; i < 5; i++) r.push(await reserveRoute(e, "gate", T0));
    expect(r).toEqual([true, true, true, false, false]);
  });
  it("fifty concurrent requests with limit 5 grant exactly five", async () => {
    const e = env({ ROUTE_BUDGET_VIEW: "5" });
    const r = await Promise.all(Array.from({ length: 50 }, () => reserveRoute(e, "view", T0)));
    expect(r.filter(Boolean)).toHaveLength(5);
  });
  it("resets at the next UTC hour; routes do not share a count", async () => {
    const e = env({ ROUTE_BUDGET_GATE: "1", ROUTE_BUDGET_VIEW: "1" });
    expect(await reserveRoute(e, "gate", T0)).toBe(true);
    expect(await reserveRoute(e, "gate", T0 + 1000)).toBe(false);
    expect(await reserveRoute(e, "view", T0)).toBe(true);
    expect(await reserveRoute(e, "gate", T0 + HOUR)).toBe(true);
  });
  it("limit 0 refuses", async () => {
    expect(await reserveRoute(env({ ROUTE_BUDGET_GATE: "0" }), "gate", T0)).toBe(false);
  });
  it("fails closed on a bad env value, a bad route, a bad time and a broken database", async () => {
    expect(await reserveRoute(env({ ROUTE_BUDGET_GATE: "-5" }), "gate", T0)).toBe(false);
    expect(await reserveRoute(env({ ROUTE_BUDGET_GATE: "99999999999" }), "gate", T0)).toBe(false);
    expect(await reserveRoute(env(), "send" as never, T0)).toBe(false);
    expect(await reserveRoute(env(), "gate", NaN)).toBe(false);
    const broken = { prepare: () => { throw new Error("db down"); } };
    expect(await reserveRoute({ DB: broken } as never, "gate", T0)).toBe(false);
    const n = await DB.prepare("SELECT COUNT(*) AS n FROM counters").first<{ n: number }>();
    expect(n!.n).toBe(0);
  });
  it("a bad env value does not write a counter row", async () => {
    await reserveRoute(env({ MAX_LEADS: "0" }), "gate", T0);
    const n = await DB.prepare("SELECT COUNT(*) AS n FROM counters").first<{ n: number }>();
    expect(n!.n).toBe(0);
  });
});

describe("daily send and alert budgets", () => {
  const env = (e: Record<string, unknown> = {}) => ({ DB, ...e }) as never;
  it("send defaults to zero: always refused, nothing written", async () => {
    expect(await reserveSend(env(), T0)).toBe(false);
    const n = await DB.prepare("SELECT COUNT(*) AS n FROM counters").first<{ n: number }>();
    expect(n!.n).toBe(0);
  });
  it("send grants exactly the daily limit, concurrently, and resets next UTC day", async () => {
    const e = env({ DAILY_SEND_BUDGET: "4" });
    const r = await Promise.all(Array.from({ length: 12 }, () => reserveSend(e, T0)));
    expect(r.filter(Boolean)).toHaveLength(4);
    expect(await reserveSend(e, T0 + 24 * HOUR)).toBe(true);
  });
  it("the last second of the day is still the same day", async () => {
    const e = env({ DAILY_SEND_BUDGET: "1" });
    const end = Date.UTC(2027, 2, 14, 23, 59, 59, 999);
    expect(await reserveSend(e, end)).toBe(true);
    expect(await reserveSend(e, end - 5000)).toBe(false);
    expect(await reserveSend(e, end + 1)).toBe(true);
  });
  it("alerts are capped separately from sends", async () => {
    const e = env({ DAILY_ALERT_BUDGET: "2", DAILY_SEND_BUDGET: "1" });
    expect([await reserveAlert(e, T0), await reserveAlert(e, T0), await reserveAlert(e, T0)]).toEqual([true, true, false]);
    expect(await reserveSend(e, T0)).toBe(true);
  });
  it("both fail closed on invalid configuration", async () => {
    expect(await reserveAlert(env({ DAILY_ALERT_BUDGET: "-1" }), T0)).toBe(false);
    expect(await reserveSend(env({ DAILY_SEND_BUDGET: "1e9" }), T0)).toBe(false);
  });
});

describe("recordKitEventCapped", () => {
  async function lead(i: number): Promise<string> {
    const r = await upsertLead(DB, `u${i}@example.com`, "gate", null, T0, 5000);
    return r!.id;
  }
  const count = async () => (await DB.prepare("SELECT COUNT(*) AS n FROM kit_events").first<{ n: number }>())!.n;

  it("stores events up to the cap and refuses beyond it", async () => {
    const id = await lead(1);
    const r = [];
    for (let i = 0; i < 5; i++) r.push(await recordKitEventCapped(DB, id, "view", T0 + i * HOUR, 3));
    expect(r).toEqual([true, true, true, false, false]);
    expect(await count()).toBe(3);
  });
  it("cap boundary: cap-1 stored then one more fits, cap itself refuses", async () => {
    const id = await lead(1);
    for (let i = 0; i < 2; i++) expect(await recordKitEventCapped(DB, id, "view", T0, 3)).toBe(true);
    expect(await recordKitEventCapped(DB, id, "view", T0, 3)).toBe(true);
    expect(await recordKitEventCapped(DB, id, "view", T0, 3)).toBe(false);
  });
  it("twenty concurrent writers never overshoot the cap", async () => {
    const id = await lead(1);
    const r = await Promise.all(Array.from({ length: 20 }, () => recordKitEventCapped(DB, id, "unlock", T0, 7)));
    expect(r.filter(Boolean)).toHaveLength(7);
    expect(await count()).toBe(7);
  });
  it("room returns after the oldest events are purged", async () => {
    const id = await lead(1);
    for (let i = 0; i < 3; i++) await recordKitEventCapped(DB, id, "view", T0, 3);
    expect(await recordKitEventCapped(DB, id, "view", T0, 3)).toBe(false);
    await DB.prepare("DELETE FROM kit_events").run();
    expect(await recordKitEventCapped(DB, id, "view", T0, 3)).toBe(true);
  });
  it("floors the time to the hour like recordKitEvent", async () => {
    const id = await lead(1);
    await recordKitEventCapped(DB, id, "view", T0 + 12345, 3);
    const row = await DB.prepare("SELECT ts FROM kit_events").first<{ ts: number }>();
    expect(row!.ts % HOUR).toBe(0);
  });
  it.each([0, -1, 1.5, NaN, Infinity, 500_001, "3" as unknown as number])("rejects cap %s", async (cap) => {
    const id = await lead(1);
    await expect(recordKitEventCapped(DB, id, "view", T0, cap)).rejects.toThrow(RangeError);
    expect(await count()).toBe(0);
  });
  it("rejects an unknown kind and a non-finite time", async () => {
    const id = await lead(1);
    await expect(recordKitEventCapped(DB, id, "x" as never, T0, 3)).rejects.toThrow(RangeError);
    await expect(recordKitEventCapped(DB, id, "view", NaN, 3)).rejects.toThrow(RangeError);
  });
});

describe("lead cap at the boundary (existing upsertLead, new limits)", () => {
  it("MAX_LEADS 5 with 20 concurrent distinct addresses leaves exactly five rows", async () => {
    const r = await Promise.all(
      Array.from({ length: 20 }, (_, i) => upsertLead(DB, `c${i}@example.com`, "gate", null, T0, loadKitLimits({ MAX_LEADS: "5" } as never).maxLeads)),
    );
    expect(r.filter((x) => x !== null)).toHaveLength(5);
  });
});
