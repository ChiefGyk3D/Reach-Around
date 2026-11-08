// SPDX-License-Identifier: Apache-2.0
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import { checkIpMinute, purgeOldCounters, reserveBudget, utcDay, utcHour } from "../../src/kit/limits";

const DB = testEnv.DB;

beforeEach(async () => {
  await DB.prepare("DELETE FROM counters").run();
});

describe("utcDay and utcHour", () => {
  it("roll over at the UTC boundary", () => {
    expect(utcDay(Date.UTC(2027, 2, 14, 23, 59, 59))).toBe("2027-03-14");
    expect(utcDay(Date.UTC(2027, 2, 14, 23, 59, 59) + 1000)).toBe("2027-03-15");
    expect(utcHour(Date.UTC(2027, 2, 14, 5, 59, 59))).toBe("2027-03-14T05");
    expect(utcHour(Date.UTC(2027, 2, 14, 5, 59, 59) + 1000)).toBe("2027-03-14T06");
  });
  it.each([NaN, Infinity, -Infinity, -1, "5" as unknown as number, 8.64e15 + 1])("refuses a non-time %s", (v) => {
    expect(() => utcDay(v)).toThrow(RangeError);
    expect(() => utcHour(v)).toThrow(RangeError);
  });
});

describe("reserveBudget", () => {
  const D = "2027-03-14";
  it("grants exactly the limit, then refuses", async () => {
    const r: boolean[] = [];
    for (let i = 0; i < 5; i++) r.push(await reserveBudget(DB, "mail", D, 3));
    expect(r).toEqual([true, true, true, false, false]);
  });
  it("ten concurrent reservations with limit 3 grant exactly three", async () => {
    const r = await Promise.all(Array.from({ length: 10 }, () => reserveBudget(DB, "mail", D, 3)));
    expect(r.filter(Boolean)).toHaveLength(3);
    const row = await DB.prepare("SELECT count FROM counters WHERE key='mail'").first<{ count: number }>();
    expect(row!.count).toBe(3);
  });
  it("fifty concurrent hourly reservations with limit 5 grant exactly five", async () => {
    const r = await Promise.all(Array.from({ length: 50 }, () => reserveBudget(DB, "route-gate", "2027-03-14T05", 5)));
    expect(r.filter(Boolean)).toHaveLength(5);
  });
  it("limit 0 always refuses and stores no row", async () => {
    expect(await reserveBudget(DB, "mail", D, 0)).toBe(false);
    expect(await reserveBudget(DB, "mail", D, 0)).toBe(false);
    const n = await DB.prepare("SELECT COUNT(*) AS n FROM counters").first<{ n: number }>();
    expect(n!.n).toBe(0);
  });
  it("resets on the next day and keys do not share a count", async () => {
    expect(await reserveBudget(DB, "mail", D, 1)).toBe(true);
    expect(await reserveBudget(DB, "mail", D, 1)).toBe(false);
    expect(await reserveBudget(DB, "mail", "2027-03-15", 1)).toBe(true);
    expect(await reserveBudget(DB, "alert", D, 1)).toBe(true);
  });
  it("a refused reservation writes nothing", async () => {
    await reserveBudget(DB, "mail", D, 1);
    const res = await DB.prepare(
      `INSERT INTO counters (key, day, count) SELECT ?1, ?2, 1 WHERE ?3 > 0
       ON CONFLICT (key, day) DO UPDATE SET count = count + 1 WHERE count < ?3`,
    )
      .bind("mail", D, 1)
      .run();
    expect(res.meta.changes).toBe(0);
    expect(res.meta.rows_written).toBe(0);
  });
  it("never lets the stored count pass the limit under a hostile mix", async () => {
    const r = await Promise.all(Array.from({ length: 40 }, (_, i) => reserveBudget(DB, "mail", D, i % 2 === 0 ? 7 : 7)));
    expect(r.filter(Boolean)).toHaveLength(7);
  });
  it.each([-1, 1.5, NaN, Infinity, 100_001, "3" as unknown as number, null as unknown as number])(
    "rejects limit %s",
    async (limit) => {
      await expect(reserveBudget(DB, "mail", D, limit)).rejects.toThrow(RangeError);
    },
  );
  it.each(["", "UP", "a:b", "x".repeat(65), "a b", "a'b"])("rejects key %j", async (key) => {
    await expect(reserveBudget(DB, key, D, 1)).rejects.toThrow(RangeError);
  });
  it.each(["2028-02-29", "2027-02-28", "2027-04-30", "2027-01-31", "2000-02-29", "2028-02-29T23"])(
    "accepts the real calendar date %s",
    async (day) => {
      expect(await reserveBudget(DB, "mail", day, 1)).toBe(true);
    },
  );
  it.each(["2027-02-29", "2027-02-30", "2027-04-31", "2027-06-31", "2027-09-31", "2027-11-31", "1900-02-29", "2100-02-29", "2027-02-29T05", "0000-01-01"])(
    "rejects the impossible date %s and stores nothing",
    async (day) => {
      await expect(reserveBudget(DB, "mail", day, 1)).rejects.toThrow(RangeError);
      const n = await DB.prepare("SELECT COUNT(*) AS n FROM counters").first<{ n: number }>();
      expect(n!.n).toBe(0);
    },
  );
  it.each(["2027-3-14", "2027-13-01", "2027-03-14T5", "'; DROP", "2027-03-14x"])("rejects day %j", async (day) => {
    await expect(reserveBudget(DB, "mail", day, 1)).rejects.toThrow(RangeError);
  });
});

describe("checkIpMinute", () => {
  it("returns the limiter's verdict", async () => {
    const seen: string[] = [];
    const yes = { limit: async (o: { key: string }) => (seen.push(o.key), { success: true }) };
    const no = { limit: async () => ({ success: false }) };
    expect(await checkIpMinute(yes, "abc")).toBe(true);
    expect(seen).toEqual(["abc"]);
    expect(await checkIpMinute(no, "abc")).toBe(false);
  });
  it("refuses a missing key without calling the limiter, and fails closed when it throws", async () => {
    let calls = 0;
    const l = { limit: async () => (calls++, { success: true }) };
    expect(await checkIpMinute(l, null)).toBe(false);
    expect(await checkIpMinute(l, "")).toBe(false);
    expect(calls).toBe(0);
    expect(
      await checkIpMinute(
        {
          limit: async () => {
            throw new Error("down");
          },
        },
        "k",
      ),
    ).toBe(false);
  });
  it("fails closed on a malformed verdict", async () => {
    expect(await checkIpMinute({ limit: async () => ({ success: "yes" as unknown as boolean }) }, "k")).toBe(false);
    expect(await checkIpMinute({ limit: async () => undefined as never }, "k")).toBe(false);
  });
});

describe("purgeOldCounters", () => {
  it("deletes days and hours older than two days, keeps the rest", async () => {
    const now = Date.UTC(2027, 2, 14, 12);
    for (const d of ["2027-03-11", "2027-03-11T23", "2027-03-12", "2027-03-12T00", "2027-03-14T05"]) {
      await reserveBudget(DB, "mail", d, 5);
    }
    const n = await purgeOldCounters(DB, now);
    expect(n).toBe(2);
    const left = await DB.prepare("SELECT day FROM counters ORDER BY day").all<{ day: string }>();
    expect(left.results.map((r) => r.day)).toEqual(["2027-03-12", "2027-03-12T00", "2027-03-14T05"]);
  });
});
