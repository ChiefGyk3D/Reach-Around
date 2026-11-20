// SPDX-License-Identifier: Apache-2.0
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import worker from "../../src/index";
import { COUNTER_PURGE_BATCH, purgeOldCounters, reserveBudget, utcDay, utcHour } from "../../src/kit/limits";

const DB = testEnv.DB;
const DAY = 86_400_000;

async function rows(): Promise<string[]> {
  const r = await DB.prepare("SELECT key || '@' || day AS k FROM counters ORDER BY key, day").all<{ k: string }>();
  return r.results.map((x) => x.k);
}

beforeEach(async () => {
  await DB.prepare("DELETE FROM counters").run();
});

describe("counter purge", () => {
  it("is bounded: one run deletes at most the batch size", async () => {
    const now = Date.UTC(2027, 2, 14, 12);
    for (let i = 0; i < 5; i++) await reserveBudget(DB, `ts-${"a".repeat(39)}${i}`, "2027-03-01", 1);
    expect(await purgeOldCounters(DB, now, 2)).toBe(2);
    expect(await purgeOldCounters(DB, now, 2)).toBe(2);
    expect(await purgeOldCounters(DB, now, 2)).toBe(1);
    expect(await purgeOldCounters(DB, now, 2)).toBe(0);
  });
  it("the default batch is a positive bound", () => {
    expect(COUNTER_PURGE_BATCH).toBeGreaterThan(0);
    expect(COUNTER_PURGE_BATCH).toBeLessThanOrEqual(1000);
  });
});

describe("scheduled wiring", () => {
  function ctx() {
    const waits: Promise<unknown>[] = [];
    return { c: { waitUntil: (p: Promise<unknown>) => void waits.push(p), passThroughOnException() {} } as unknown as ExecutionContext, waits };
  }
  const limiter = { limit: async () => ({ success: true }) };
  const full = (extra: object = {}) => ({ DB, SITE_HOST: "x.example.test", LINK_IDS: "[]", CLICK_LIMITER: limiter, ...extra }) as never;

  it("the hourly cron purges single-use claims and route counters older than two days and keeps newer ones", async () => {
    const now = Date.now();
    const old = now - 5 * DAY;
    await reserveBudget(DB, `ts-${"b".repeat(40)}`, utcDay(old), 1);
    await reserveBudget(DB, "route-gate", utcHour(old), 10);
    await reserveBudget(DB, "route-view", utcHour(old), 10);
    await reserveBudget(DB, `ts-${"c".repeat(40)}`, utcDay(now), 1);
    await reserveBudget(DB, "route-gate", utcHour(now), 10);
    const { c, waits } = ctx();
    await worker.scheduled({ cron: "5 * * * *" } as ScheduledController, full(), c);
    await Promise.all(waits);
    const left = await rows();
    expect(left).toContain(`ts-${"c".repeat(40)}@${utcDay(now)}`);
    expect(left).toContain(`route-gate@${utcHour(now)}`);
    expect(left.some((k) => k.includes(utcDay(old)))).toBe(false);
  });

  it("a tiny QUERY_BUDGET never throws", async () => {
    await reserveBudget(DB, "route-gate", utcHour(Date.now() - 5 * DAY), 10);
    const { c, waits } = ctx();
    await worker.scheduled({ cron: "5 * * * *" } as ScheduledController, full({ QUERY_BUDGET: "5" }), c);
    await expect(Promise.all(waits)).resolves.toBeDefined();
  });

  it("the live-poller cron never touches counters", async () => {
    await reserveBudget(DB, "route-gate", utcHour(Date.now() - 5 * DAY), 10);
    const { c, waits } = ctx();
    const db = { prepare: () => { throw new Error("DB must not be touched"); }, batch: () => { throw new Error("DB must not be touched"); } };
    await worker.scheduled({ cron: "*/2 * * * *" } as ScheduledController, full({ DB: db }), c);
    await Promise.all(waits);
    expect((await rows()).length).toBe(1);
  });
});
