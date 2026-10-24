// SPDX-License-Identifier: Apache-2.0
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import type { Env } from "../src/click";
import { purgeOldEvents } from "../src/retention";

const DAY = 24 * 60 * 60 * 1000;
const NOW = Date.UTC(2027, 0, 1);

function makeEnv(): Env {
  return {
    DB: testEnv.DB,
    SITE_HOST: "links.example.test",
    LINK_IDS: "[]",
    CLICK_LIMITER: { limit: async () => ({ success: true }) },
  };
}

async function insert(ts: number, linkId = "example-one") {
  await testEnv.DB.prepare("INSERT INTO click_events (ts, link_id) VALUES (?, ?)")
    .bind(ts, linkId)
    .run();
}

async function remaining(): Promise<number[]> {
  const res = await testEnv.DB.prepare(
    "SELECT ts FROM click_events ORDER BY ts",
  ).all<{ ts: number }>();
  return res.results.map((r) => r.ts);
}

beforeEach(async () => {
  await testEnv.DB.prepare("DELETE FROM click_events").run();
});

describe("purgeOldEvents", () => {
  it("deletes events older than 90 days and keeps newer", async () => {
    await insert(NOW - 91 * DAY);
    await insert(NOW - 200 * DAY);
    await insert(NOW - 89 * DAY);
    await insert(NOW - 1000);
    await purgeOldEvents(makeEnv(), NOW);
    expect(await remaining()).toEqual([NOW - 89 * DAY, NOW - 1000]);
  });

  it("keepDays is configurable", async () => {
    await insert(NOW - 10 * DAY);
    await insert(NOW - 3 * DAY);
    await purgeOldEvents(makeEnv(), NOW, 7);
    expect(await remaining()).toEqual([NOW - 3 * DAY]);
  });

  it("a zero or negative keepDays is refused", async () => {
    await insert(NOW - 10 * DAY);
    await insert(NOW - 1000);
    for (const bad of [0, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
      await expect(purgeOldEvents(makeEnv(), NOW, bad)).rejects.toThrow();
    }
    expect(await remaining()).toHaveLength(2);
  });

  it("returns the number of rows deleted", async () => {
    await insert(NOW - 100 * DAY);
    await insert(NOW - 95 * DAY);
    await insert(NOW - 91 * DAY);
    await insert(NOW - DAY);
    expect(await purgeOldEvents(makeEnv(), NOW)).toBe(3);
    expect(await purgeOldEvents(makeEnv(), NOW)).toBe(0);
  });
});
