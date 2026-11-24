// SPDX-License-Identifier: Apache-2.0
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import worker from "../../src/index";
import { QueryMeter, meteredDb } from "../../src/d1budget";
import { GATE_ROUTE, handleGate, type GateEnv } from "../../src/kit/gate";
import { purgeExpiredLeads } from "../../src/kit/leadretention";
import {
  LeadsSeparationError,
  SEPARATION_QUERIES,
  assertLeadsSeparated,
  resetSeparationCache,
} from "../../src/kit/separation";

// Separation of the leads database (issue #772, review round 1). Every database here is synthetic.
const ANALYTICS = testEnv.DB;
const LEADS = testEnv.LEADS_DB;
const DAY = 86_400_000;
const NOW = Date.UTC(2027, 0, 1, 12, 0, 0, 0);
const LEGACY_ID = "a".repeat(32);

/** A different JavaScript object over the same database: what a second binding name would look like. */
function alias(db: D1Database): D1Database {
  return new Proxy(db, {
    get(target, key) {
      const value = Reflect.get(target, key, target);
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
}

async function addLegacyLead(): Promise<void> {
  await ANALYTICS.prepare("INSERT INTO leads (id, email, created_ts, last_seen_ts, source) VALUES (?, 'legacy@example.test', 1, 1, 'gate')")
    .bind(LEGACY_ID)
    .run();
  resetSeparationCache();
}
async function clearLegacy(): Promise<void> {
  await ANALYTICS.batch([ANALYTICS.prepare("DELETE FROM kit_events"), ANALYTICS.prepare("DELETE FROM leads")]);
  resetSeparationCache();
}
const rejects = async (env: { DB?: unknown; LEADS_DB?: unknown }): Promise<unknown> => {
  try {
    await assertLeadsSeparated(env);
  } catch (e) {
    return e;
  }
  return undefined;
};

beforeEach(async () => {
  await clearLegacy();
  await LEADS.batch([LEADS.prepare("DELETE FROM kit_events"), LEADS.prepare("DELETE FROM leads")]);
});

describe("the separation proof", () => {
  it("passes for the two real databases", async () => {
    await expect(assertLeadsSeparated({ DB: ANALYTICS, LEADS_DB: LEADS })).resolves.toBeUndefined();
  });

  it("each database states what it is, and the migrations own that", async () => {
    expect((await LEADS.prepare("SELECT role FROM db_identity").all<{ role: string }>()).results).toEqual([{ role: "leads" }]);
    expect((await ANALYTICS.prepare("SELECT role FROM db_identity").all<{ role: string }>()).results).toEqual([{ role: "analytics" }]);
  });

  // HIGH 2. The old check compared object identity, so a second binding name or a wrapper passed.
  it("refuses a second binding to the analytics database: a different object, the same database", async () => {
    const e = await rejects({ DB: ANALYTICS, LEADS_DB: alias(ANALYTICS) });
    expect(e).toBeInstanceOf(LeadsSeparationError);
  });

  it("refuses the two bindings swapped", async () => {
    expect(await rejects({ DB: LEADS, LEADS_DB: ANALYTICS })).toBeInstanceOf(LeadsSeparationError);
  });

  it("refuses an alias of the leads database standing in for analytics", async () => {
    expect(await rejects({ DB: alias(LEADS), LEADS_DB: LEADS })).toBeInstanceOf(LeadsSeparationError);
  });

  it("refuses a database that never said what it is", async () => {
    const bare = {
      prepare: () => ({ all: async () => ({ results: [] }), first: async () => null, bind() { return this; } }),
      batch: async () => [{ results: [] }],
    };
    expect(await rejects({ DB: ANALYTICS, LEADS_DB: bare })).toBeInstanceOf(LeadsSeparationError);
    expect(await rejects({ DB: bare, LEADS_DB: LEADS })).toBeInstanceOf(LeadsSeparationError);
  });

  it("refuses when a read fails, and never leaks a database error", async () => {
    const broken = {
      prepare: () => {
        throw new Error("secret detail example.test");
      },
      batch: async () => {
        throw new Error("secret detail example.test");
      },
    };
    const e = (await rejects({ DB: ANALYTICS, LEADS_DB: broken })) as Error;
    expect(e).toBeInstanceOf(LeadsSeparationError);
    expect(e.message).not.toContain("secret");
  });

  it("probes through a metering wrapper's raw database and counts them only on the meter it is given", async () => {
    const parent = new QueryMeter(50);
    const slice = parent.slice(SEPARATION_QUERIES);
    const wrapped = meteredDb(LEADS, parent.slice(0)); // a wrapper with NO room: the probe must not use it
    await assertLeadsSeparated({ DB: ANALYTICS, LEADS_DB: wrapped }, { meter: slice });
    expect(slice.used).toBeGreaterThan(0);
    expect(slice.used).toBeLessThanOrEqual(SEPARATION_QUERIES);
  });

  it("does not probe when the meter has no room, and remembers nothing", async () => {
    const tiny = new QueryMeter(SEPARATION_QUERIES - 1);
    await expect(assertLeadsSeparated({ DB: ANALYTICS, LEADS_DB: LEADS }, { meter: tiny })).rejects.toThrow("budget");
    expect(tiny.used).toBe(0);
    await expect(assertLeadsSeparated({ DB: ANALYTICS, LEADS_DB: LEADS })).resolves.toBeUndefined();
  });

  it("remembers a verdict briefly, and a refusal is not served from a stale approval", async () => {
    await assertLeadsSeparated({ DB: ANALYTICS, LEADS_DB: LEADS }, { nowMs: 1_000 });
    await addLegacyLead(); // the reset inside forgets the approval: the next check sees the row
    expect(await rejects({ DB: ANALYTICS, LEADS_DB: LEADS })).toBeInstanceOf(LeadsSeparationError);
  });
});

// HIGH 1. Existing personal data stays readable through analytics until the one-time move empties it.
describe("lead rows left in the analytics database", () => {
  it("refuse the proof while a lead row is there, and pass once it is gone", async () => {
    await addLegacyLead();
    expect(await rejects({ DB: ANALYTICS, LEADS_DB: LEADS })).toBeInstanceOf(LeadsSeparationError);
    await clearLegacy();
    await expect(assertLeadsSeparated({ DB: ANALYTICS, LEADS_DB: LEADS })).resolves.toBeUndefined();
  });

  it("are not assumed gone: a database with no lead tables passes only when sqlite_master proves it", async () => {
    // Stand-in for analytics after the later contract migration: no lead tables, identity says analytics.
    const gone = {
      prepare: (q: string) => ({
        all: async () => ({ results: /db_identity/.test(q) ? [{ role: "analytics" }] : [] }),
        first: async () => ({ n: 0 }),
        bind() { return this; },
      }),
      batch: async () => {
        throw new Error("no such table: leads");
      },
    };
    await expect(assertLeadsSeparated({ DB: gone, LEADS_DB: LEADS })).resolves.toBeUndefined();
    resetSeparationCache();
    const lying = { ...gone, prepare: (q: string) => ({ ...gone.prepare(q), first: async () => ({ n: 1 }) }) };
    expect(await rejects({ DB: lying, LEADS_DB: LEADS })).toBeInstanceOf(LeadsSeparationError);
  });

  it("close the gate: nothing is stored and the vendor is never called", async () => {
    await addLegacyLead();
    let vendor = 0;
    const fetchFn = (async () => {
      vendor++;
      return new Response("{}");
    }) as typeof fetch;
    const env = gateEnv();
    const res = await handleGate(
      new Request(`https://links.example.test${GATE_ROUTE}`, {
        method: "POST",
        headers: {
          "content-type": "application/x-www-form-urlencoded",
          origin: "https://links.example.test",
          "user-agent": "Mozilla/5.0 (X11; Linux x86_64) Firefox/130.0",
          "cf-connecting-ip": "203.0.113.9",
        },
        body: new URLSearchParams({ email: "new@example.test", "cf-turnstile-response": "ok" }).toString(),
      }),
      env,
      { nowMs: NOW, fetchFn },
    );
    expect(res.status).toBe(303);
    expect(res.headers.get("set-cookie")).toBeNull();
    expect(vendor).toBe(0);
    expect((await LEADS.prepare("SELECT COUNT(*) AS n FROM leads").first<{ n: number }>())!.n).toBe(0);
  });

  it("stop the hourly job's lead purge (closed) and leave every row where it was", async () => {
    await addLegacyLead();
    await LEADS.prepare("INSERT INTO leads (id, email, created_ts, last_seen_ts, source) VALUES (?, 'old@example.test', 1, 1, 'gate')")
      .bind("b".repeat(32))
      .run();
    const waits: Promise<unknown>[] = [];
    const ctx = { waitUntil: (p: Promise<unknown>) => void waits.push(p), passThroughOnException() {} } as unknown as ExecutionContext;
    await worker.scheduled(
      {} as ScheduledController,
      { DB: ANALYTICS, LEADS_DB: LEADS, SITE_HOST: "x.example.test", LINK_IDS: "[]", CLICK_LIMITER: { limit: async () => ({ success: true }) } } as never,
      ctx,
    );
    await Promise.all(waits);
    expect((await LEADS.prepare("SELECT COUNT(*) AS n FROM leads").first<{ n: number }>())!.n).toBe(1);
    expect((await ANALYTICS.prepare("SELECT COUNT(*) AS n FROM leads").first<{ n: number }>())!.n).toBe(1);
  });
});

// MEDIUM 3. The exported purge is a path of its own; it proves the separation itself.
describe("the lead purge called directly", () => {
  async function seedOld(db: D1Database): Promise<void> {
    await db.prepare("INSERT INTO leads (id, email, created_ts, last_seen_ts, source) VALUES (?, 'old@example.test', 1, 1, 'gate')")
      .bind("c".repeat(32))
      .run();
  }

  it("refuses the analytics database handed in as LEADS_DB, and deletes nothing there", async () => {
    await seedOld(ANALYTICS);
    resetSeparationCache();
    await expect(purgeExpiredLeads({ DB: ANALYTICS, LEADS_DB: ANALYTICS }, NOW)).rejects.toThrow();
    await expect(purgeExpiredLeads({ DB: ANALYTICS, LEADS_DB: alias(ANALYTICS) }, NOW)).rejects.toBeInstanceOf(LeadsSeparationError);
    expect((await ANALYTICS.prepare("SELECT COUNT(*) AS n FROM leads").first<{ n: number }>())!.n).toBe(1);
    expect((await ANALYTICS.prepare("SELECT COUNT(*) AS n FROM kit_meta WHERE key = 'last_purge_ts'").first<{ n: number }>())!.n).toBe(0);
  });

  it("refuses a call that leaves the analytics binding out, as it cannot prove anything", async () => {
    await expect(purgeExpiredLeads({ LEADS_DB: ANALYTICS } as never, NOW)).rejects.toThrow(TypeError);
    await expect(purgeExpiredLeads({ LEADS_DB: LEADS } as never, NOW)).rejects.toThrow(TypeError);
  });

  it("still purges the real leads database", async () => {
    await seedOld(LEADS);
    expect(await purgeExpiredLeads({ DB: ANALYTICS, LEADS_DB: LEADS }, Date.now() + 40 * DAY)).toBe(1);
  });
});

function gateEnv(): GateEnv {
  const limiter = { limit: async () => ({ success: true }) };
  return {
    DB: ANALYTICS,
    LEADS_DB: LEADS,
    SITE_HOST: "links.example.test",
    KIT: { get: async () => "<p>kit" },
    GATE_LIMITER: limiter,
    KIT_VIEW_LIMITER: limiter,
    GATE_CHECK_LIMITER: limiter,
    TOKEN_HMAC_KEY: "k".repeat(40),
    TURNSTILE_SECRET: "1x0000000000000000000000000000000AA",
    VIEW_HASH_SECRET: btoa("s".repeat(40)),
  } as unknown as GateEnv;
}
