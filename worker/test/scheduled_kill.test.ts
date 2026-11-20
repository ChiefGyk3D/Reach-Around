// SPDX-License-Identifier: Apache-2.0
import { env as testEnv } from "cloudflare:workers";
import { describe, expect, it } from "vitest";
import worker from "../src/index";

const limiter = { limit: async () => ({ success: true }) };

function ctx() {
  const waits: Promise<unknown>[] = [];
  return {
    c: { waitUntil: (p: Promise<unknown>) => void waits.push(p), passThroughOnException() {} } as unknown as ExecutionContext,
    waits,
  };
}

/** A database that records every statement and every batch, and answers like an empty one. */
function spyDb() {
  const calls: string[] = [];
  const stmt = (sql: string) => {
    const s = {
      bind: () => s,
      run: async () => ({ success: true, meta: { changes: 0 }, results: [] }),
      all: async () => ({ success: true, results: [], meta: {} }),
      first: async () => null,
      raw: async () => [],
    };
    return s;
  };
  const db = {
    prepare: (sql: string) => {
      calls.push(sql);
      return stmt(sql);
    },
    batch: async (list: unknown[]) => {
      calls.push("batch");
      return list.map(() => ({ success: true, results: [], meta: { changes: 0 } }));
    },
    exec: async (sql: string) => {
      calls.push(sql);
      return { count: 0, duration: 0 };
    },
  };
  return { db, calls };
}

function env(extra: object, db: unknown) {
  return { ...testEnv, DB: db, SITE_HOST: "x.example.test", LINK_IDS: "[]", CLICK_LIMITER: limiter, ...extra } as never;
}

describe("SCHEDULED_KILL maintenance switch", () => {
  it.each(["1", "true", "TRUE"])("SCHEDULED_KILL=%s: the hourly handler runs no D1 statement", async (v) => {
    const { db, calls } = spyDb();
    const { c, waits } = ctx();
    await worker.scheduled({ cron: "5 * * * *" } as ScheduledController, env({ SCHEDULED_KILL: v }, db), c);
    await Promise.all(waits);
    expect(calls).toEqual([]);
  });

  it("SCHEDULED_KILL=1 also stops the other cron without touching D1", async () => {
    const { db, calls } = spyDb();
    const { c, waits } = ctx();
    await worker.scheduled({ cron: "*/2 * * * *" } as ScheduledController, env({ SCHEDULED_KILL: "1" }, db), c);
    await Promise.all(waits);
    expect(calls).toEqual([]);
  });

  it.each([undefined, "0", "false", "", "yes"])("SCHEDULED_KILL=%s leaves the hourly handler running", async (v) => {
    const { db, calls } = spyDb();
    const { c, waits } = ctx();
    await worker.scheduled(
      { cron: "5 * * * *" } as ScheduledController,
      env(v === undefined ? {} : { SCHEDULED_KILL: v }, db),
      c,
    );
    await Promise.all(waits);
    expect(calls.length).toBeGreaterThan(0);
  });
});
