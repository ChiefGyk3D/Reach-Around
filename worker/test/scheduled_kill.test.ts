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

  it("SCHEDULED_KILL=1 also stops the Live poller: no KV read or write and no outbound fetch", async () => {
    const { db, calls } = spyDb();
    const kvCalls: string[] = [];
    const kv = {
      get: async (k: string) => (kvCalls.push(`get ${k}`), null),
      put: async (k: string) => void kvCalls.push(`put ${k}`),
    };
    const fetched: string[] = [];
    const realFetch = globalThis.fetch;
    globalThis.fetch = (async (input: unknown) => {
      fetched.push(String(input));
      return new Response("{}", { status: 500 });
    }) as typeof fetch;
    const live = {
      LIVE_KV: kv,
      LIVE_KICK_HANDLE: "example",
      KICK_CLIENT_ID: "id",
      KICK_CLIENT_SECRET: "secret",
      LIVE_TWITCH_HANDLE: "example",
      TWITCH_CLIENT_ID: "id",
      TWITCH_CLIENT_SECRET: "secret",
    };
    try {
      // Control: without the switch the poller does reach the KV, so the spies can see it.
      const control = ctx();
      await worker.scheduled({ cron: "*/2 * * * *" } as ScheduledController, env(live, db), control.c);
      await Promise.all(control.waits);
      expect(kvCalls.length + fetched.length).toBeGreaterThan(0);
      kvCalls.length = 0;
      fetched.length = 0;

      const { c, waits } = ctx();
      await worker.scheduled(
        { cron: "*/2 * * * *" } as ScheduledController,
        env({ ...live, SCHEDULED_KILL: "1" }, db),
        c,
      );
      await Promise.all(waits);
      expect(kvCalls).toEqual([]);
      expect(fetched).toEqual([]);
      expect(calls).toEqual([]);
    } finally {
      globalThis.fetch = realFetch;
    }
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
