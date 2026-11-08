// SPDX-License-Identifier: Apache-2.0
import { env as testEnv } from "cloudflare:workers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import worker, { LEAD_FIRST_MIN_BUDGET } from "../../src/index";
import { QueryMeter, meteredDb } from "../../src/d1budget";
import { LIVE_CRON } from "../../src/live";
import { PURGE_RESERVE } from "../../src/kit/budgets";
import {
  DEFAULT_LEAD_RETENTION_DAYS,
  LEAD_PURGE_BATCH,
  LEAD_PURGE_DAILY_WRITES,
  LEAD_PURGE_EVENT_BATCH,
  LEAD_PURGE_QUERIES,
  LEAD_PURGE_RUN_WRITES,
  MAX_LEAD_RETENTION_DAYS,
  leadRetentionDays,
  purgeExpiredLeads,
} from "../../src/kit/leadretention";
import { DEFAULT_MAX_LEADS, countLeads, recordKitEvent, upsertLead } from "../../src/kit/leads";
import retentionSource from "../../src/kit/leadretention.ts?raw";

const DB = testEnv.DB;
const DAY = 86_400_000;
const NOW = Date.UTC(2027, 0, 1, 12, 0, 0, 0);
const env = (extra: Record<string, string> = {}) => ({ DB, ...extra });

async function seed(i: number | string, createdTs: number): Promise<string> {
  const r = await upsertLead(DB, `lead${i}@example.test`, "gate", "US", createdTs, DEFAULT_MAX_LEADS);
  return r!.id;
}
async function seedMany(n: number, createdTs: number, from = 0): Promise<void> {
  const stmts = [];
  for (let i = from; i < from + n; i++) {
    stmts.push(
      DB.prepare("INSERT INTO leads (id, email, created_ts, last_seen_ts, source) VALUES (?, ?, ?, ?, 'gate')").bind(
        i.toString(16).padStart(32, "0"),
        `bulk${i}@example.test`,
        createdTs,
        createdTs,
      ),
    );
  }
  await DB.batch(stmts);
}
async function ids(): Promise<string[]> {
  return (await DB.prepare("SELECT id FROM leads ORDER BY created_ts, id").all<{ id: string }>()).results.map((r) => r.id);
}

beforeEach(async () => {
  await DB.prepare("DELETE FROM kit_events").run();
  await DB.prepare("DELETE FROM leads").run();
  await DB.prepare("DELETE FROM kit_meta").run();
});
afterEach(() => vi.restoreAllMocks());

describe("retention config", () => {
  it("defaults conservatively and documents a hard maximum", () => {
    expect(DEFAULT_LEAD_RETENTION_DAYS).toBe(30);
    expect(MAX_LEAD_RETENTION_DAYS).toBe(365);
    expect(leadRetentionDays(undefined)).toEqual({ days: 30, valid: true });
    expect(leadRetentionDays("")).toEqual({ days: 30, valid: true });
  });

  it("accepts plain digits from 1 to the maximum", () => {
    expect(leadRetentionDays("1")).toEqual({ days: 1, valid: true });
    expect(leadRetentionDays("90")).toEqual({ days: 90, valid: true });
    expect(leadRetentionDays("365")).toEqual({ days: 365, valid: true });
  });

  it("anything else is invalid and falls back to the default, never to a longer keep or a wipe", () => {
    for (const bad of ["0", "366", "1000000", "-1", "+5", "1e2", " 30", "30 ", "0x10", "3.5", "abc", "999999999999", 30 as never, null as never]) {
      expect(leadRetentionDays(bad), String(bad)).toEqual({ days: 30, valid: false });
    }
  });
});

describe("purgeExpiredLeads", () => {
  it("keeps a lead exactly at the boundary and deletes one second older, events included", async () => {
    const cutoff = NOW - 30 * DAY;
    const old = await seed("old", cutoff - 1000);
    const edge = await seed("edge", cutoff);
    const fresh = await seed("fresh", cutoff + 1000);
    await recordKitEvent(DB, old, "unlock", cutoff);
    await recordKitEvent(DB, edge, "unlock", cutoff);
    expect(await purgeExpiredLeads(env(), NOW)).toBe(1);
    expect(await ids()).toEqual([edge, fresh]);
    const ev = await DB.prepare("SELECT lead_id FROM kit_events").all<{ lead_id: string }>();
    expect(ev.results.map((r) => r.lead_id)).toEqual([edge]);
  });

  it("is idempotent", async () => {
    await seedMany(3, NOW - 40 * DAY);
    await seed("fresh", NOW);
    expect(await purgeExpiredLeads(env(), NOW)).toBe(3);
    expect(await purgeExpiredLeads(env(), NOW)).toBe(0);
    expect(await countLeads(DB)).toBe(1);
  });

  it("purges verified leads too: one retention for every lead", async () => {
    const id = await seed("v", NOW - 40 * DAY);
    await DB.prepare("UPDATE leads SET verified_ts = ? WHERE id = ?").bind(NOW - 39 * DAY, id).run();
    expect(await purgeExpiredLeads(env(), NOW)).toBe(1);
  });

  it("honors a configured retention", async () => {
    await seed("a", NOW - 10 * DAY);
    await seed("b", NOW - 3 * DAY);
    expect(await purgeExpiredLeads(env({ LEAD_RETENTION_DAYS: "7" }), NOW)).toBe(1);
    expect(await countLeads(DB)).toBe(1);
  });

  it("a bad retention value uses the 30 day default: it neither wipes everything nor keeps forever", async () => {
    await seed("old", NOW - 40 * DAY);
    await seed("mid", NOW - 20 * DAY);
    for (const bad of ["0", "999999", "abc", "-5"]) {
      await purgeExpiredLeads(env({ LEAD_RETENTION_DAYS: bad }), NOW);
      expect((await ids()).length, bad).toBe(1);
    }
  });

  it("deletes at most the per-run cap and the next run continues", async () => {
    const total = LEAD_PURGE_BATCH * 2 + 7;
    await seedMany(total, NOW - 60 * DAY);
    expect(await purgeExpiredLeads(env(), NOW)).toBe(LEAD_PURGE_BATCH);
    expect(await countLeads(DB)).toBe(total - LEAD_PURGE_BATCH);
    expect(await purgeExpiredLeads(env(), NOW)).toBe(LEAD_PURGE_BATCH);
    expect(await purgeExpiredLeads(env(), NOW)).toBe(7);
    expect(await countLeads(DB)).toBe(0);
  });

  it("caller options can only lower the batches", async () => {
    await seedMany(LEAD_PURGE_BATCH + 20, NOW - 60 * DAY);
    expect(await purgeExpiredLeads(env(), NOW, { leadBatch: 1_000_000, eventBatch: 1_000_000 })).toBe(LEAD_PURGE_BATCH);
    expect(await purgeExpiredLeads(env(), NOW, { leadBatch: 3 })).toBe(3);
    expect(await purgeExpiredLeads(env(), NOW, { leadBatch: 0 as never })).toBe(LEAD_PURGE_BATCH > 17 ? 17 : LEAD_PURGE_BATCH);
  });

  it("takes exactly one metered query per run, and none when the meter is empty", async () => {
    await seedMany(LEAD_PURGE_BATCH * 2, NOW - 60 * DAY);
    expect(LEAD_PURGE_QUERIES).toBe(1);
    const one = new QueryMeter(1);
    expect(await purgeExpiredLeads({ DB: meteredDb(DB, one) }, NOW)).toBe(LEAD_PURGE_BATCH);
    expect(one.used).toBe(1);
    const none = new QueryMeter(0);
    expect(await purgeExpiredLeads({ DB: meteredDb(DB, none) }, NOW)).toBe(0);
    expect(none.used).toBe(0);
    expect(await countLeads(DB)).toBe(LEAD_PURGE_BATCH);
    // A nearly spent parent also stops it.
    const parent = new QueryMeter(1);
    parent.take();
    expect(await purgeExpiredLeads({ DB: meteredDb(DB, parent.slice(5)) }, NOW)).toBe(0);
  });

  async function addEvents(leadId: string, n: number): Promise<void> {
    const stmts = [];
    for (let i = 0; i < n; i++) {
      stmts.push(DB.prepare("INSERT INTO kit_events (ts, lead_id, kind) VALUES (?, ?, 'view')").bind((i % 1000) * 3_600_000, leadId));
    }
    await DB.batch(stmts);
  }
  const eventCount = async () => (await DB.prepare("SELECT COUNT(*) AS n FROM kit_events").first<{ n: number }>())!.n;

  it("a lead that owns many events is purged incrementally: bounded event deletes per run, the lead goes when its events are gone", async () => {
    const heavy = await seed("heavy", NOW - 60 * DAY);
    const light = await seed("light", NOW - 59 * DAY);
    const total = LEAD_PURGE_EVENT_BATCH * 3 + 10;
    await addEvents(heavy, total);
    await addEvents(light, 2);
    let before = await eventCount();
    let runs = 0;
    while ((await countLeads(DB)) > 0 && runs < 10) {
      await purgeExpiredLeads(env(), NOW);
      runs += 1;
      const after = await eventCount();
      // The cascade must never be the thing that deletes events in bulk.
      expect(before - after).toBeLessThanOrEqual(LEAD_PURGE_EVENT_BATCH);
      before = after;
    }
    expect(runs).toBe(4);
    expect(await countLeads(DB)).toBe(0);
    expect(await eventCount()).toBe(0);
  });

  it("while a lead still has events it is not deleted", async () => {
    const heavy = await seed("heavy", NOW - 60 * DAY);
    await addEvents(heavy, LEAD_PURGE_EVENT_BATCH + 5);
    expect(await purgeExpiredLeads(env(), NOW)).toBe(0);
    expect(await ids()).toEqual([heavy]);
    expect(await eventCount()).toBe(5);
    expect(await purgeExpiredLeads(env(), NOW)).toBe(1);
  });

  it("never deletes the events of a current lead", async () => {
    const fresh = await seed("fresh", NOW - DAY);
    await addEvents(fresh, 5);
    const old = await seed("old", NOW - 40 * DAY);
    await addEvents(old, 5);
    await purgeExpiredLeads(env(), NOW);
    expect(await ids()).toEqual([fresh]);
    expect(await eventCount()).toBe(5);
  });

  it("the quota model is true: per-run writes follow from the batch sizes and 24 runs fit the kit purge reserve", () => {
    expect(LEAD_PURGE_RUN_WRITES).toBe(LEAD_PURGE_BATCH * 4 + LEAD_PURGE_EVENT_BATCH * 3 + 1);
    expect(LEAD_PURGE_DAILY_WRITES).toBe(24 * LEAD_PURGE_RUN_WRITES);
    // At most 80 percent of the reserve; the rest is left for the counter purge.
    expect(LEAD_PURGE_DAILY_WRITES).toBeLessThanOrEqual(PURGE_RESERVE * 0.8);
  });

  it("records a heartbeat without any lead data", async () => {
    await seed("x", NOW - 40 * DAY);
    await purgeExpiredLeads(env(), NOW);
    const rows = await DB.prepare("SELECT key, value FROM kit_meta").all<{ key: string; value: number }>();
    expect(rows.results).toEqual([{ key: "last_purge_ts", value: NOW }]);
  });

  it("refuses a time that is not a finite number", async () => {
    await seed("keep", NOW - 400 * DAY);
    for (const bad of [Number.NaN, Number.POSITIVE_INFINITY, "1" as never]) {
      await expect(purgeExpiredLeads(env(), bad)).rejects.toThrow(RangeError);
    }
    expect(await countLeads(DB)).toBe(1);
  });

  it("logs fixed strings and a count only: no address, hash or IP", async () => {
    const lines: string[] = [];
    vi.spyOn(console, "log").mockImplementation((s: unknown) => void lines.push(String(s)));
    vi.spyOn(console, "error").mockImplementation((s: unknown) => void lines.push(String(s)));
    vi.spyOn(console, "warn").mockImplementation((s: unknown) => void lines.push(String(s)));
    await seed("secretname", NOW - 40 * DAY);
    await purgeExpiredLeads(env({ LEAD_RETENTION_DAYS: "bogus-example.test" }), NOW);
    expect(lines.length).toBeGreaterThan(0);
    const all = lines.join("\n");
    expect(all).not.toContain("secretname");
    expect(all).not.toContain("example.test");
    for (const l of lines) expect(JSON.parse(l)).toMatchObject({ e: "purge" });
  });
});

describe("scheduled wiring", () => {
  function ctx() {
    const waits: Promise<unknown>[] = [];
    return { c: { waitUntil: (p: Promise<unknown>) => void waits.push(p), passThroughOnException() {} } as unknown as ExecutionContext, waits };
  }
  const limiter = { limit: async () => ({ success: true }) };
  const full = (extra: object = {}) => ({ DB, SITE_HOST: "x.example.test", LINK_IDS: "[]", CLICK_LIMITER: limiter, ...extra }) as never;

  it("the hourly cron purges expired leads and keeps current ones", async () => {
    const now = Date.now();
    await seed("old", now - 40 * DAY);
    const fresh = await seed("fresh", now - DAY);
    const { c, waits } = ctx();
    await worker.scheduled({ cron: "5 * * * *" } as ScheduledController, full(), c);
    await Promise.all(waits);
    expect(await ids()).toEqual([fresh]);
  });

  it("a controller with no cron string also runs the hourly jobs", async () => {
    await seed("old", Date.now() - 40 * DAY);
    const { c, waits } = ctx();
    await worker.scheduled({} as ScheduledController, full(), c);
    await Promise.all(waits);
    expect(await countLeads(DB)).toBe(0);
  });

  it("the live-poller cron never touches leads", async () => {
    await seed("old", Date.now() - 400 * DAY);
    const db = { prepare: vi.fn(() => { throw new Error("DB must not be touched"); }), batch: vi.fn() };
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => Response.json({ data: [] }));
    const { c, waits } = ctx();
    await worker.scheduled({ cron: LIVE_CRON } as ScheduledController, full({ DB: db }), c);
    await Promise.all(waits);
    expect(db.prepare).not.toHaveBeenCalled();
    expect(await countLeads(DB)).toBe(1);
  });

  it("a failing lead purge does not stop the other jobs and nothing throws", async () => {
    await DB.prepare("DELETE FROM click_events").run();
    await DB.prepare("INSERT INTO click_events (ts, link_id) VALUES (?, 'example-one')").bind(Date.now() - 200 * DAY).run();
    const failing = new Proxy(DB, {
      get(t, p) {
        if (p === "prepare") {
          return (q: string) => {
            if (/leads|kit_meta/.test(q)) throw new Error("leads unavailable");
            return t.prepare(q);
          };
        }
        const v = Reflect.get(t, p, t);
        return typeof v === "function" ? v.bind(t) : v;
      },
    });
    const { c, waits } = ctx();
    await worker.scheduled({} as ScheduledController, full({ DB: failing }), c);
    await expect(Promise.all(waits)).resolves.toBeDefined();
    const clicks = await DB.prepare("SELECT COUNT(*) AS n FROM click_events").first<{ n: number }>();
    expect(clicks?.n).toBe(0);
  });

  function counted(): { db: D1Database; stats: { queries: number; first: string } } {
    const stats = { queries: 0, first: "" };
    const texts = new WeakMap<object, string>();
    const hit = (q: string) => {
      stats.queries++;
      if (stats.first === "") stats.first = q;
    };
    const db = new Proxy(DB, {
      get(t, p) {
        if (p === "prepare") {
          return (q: string) => {
            const wrap = (st: D1PreparedStatement): D1PreparedStatement => {
              const proxy: D1PreparedStatement = new Proxy(st, {
                get(x, k) {
                  if (k === "bind") return (...a: unknown[]) => wrap(x.bind(...a));
                  if (k === "run" || k === "all" || k === "first" || k === "raw") {
                    return (...a: unknown[]) => { hit(q); return (x as never as Record<string, (...b: unknown[]) => unknown>)[k as string]!(...a); };
                  }
                  const v = Reflect.get(x, k, x);
                  return typeof v === "function" ? v.bind(x) : v;
                },
              });
              texts.set(proxy, q);
              return proxy;
            };
            return wrap(t.prepare(q));
          };
        }
        if (p === "batch") {
          return async (s: D1PreparedStatement[]) => { hit(s.map((x) => texts.get(x) ?? "").join(" | ")); return t.batch(s); };
        }
        const v = Reflect.get(t, p, t);
        return typeof v === "function" ? v.bind(t) : v;
      },
    });
    return { db: db as D1Database, stats };
  }

  it("a tiny QUERY_BUDGET never throws or overruns the meter", async () => {
    await seedMany(LEAD_PURGE_BATCH, Date.now() - 60 * DAY);
    const c = counted();
    const { c: cx, waits } = ctx();
    await worker.scheduled({} as ScheduledController, full({ DB: c.db, QUERY_BUDGET: "5" }), cx);
    await expect(Promise.all(waits)).resolves.toBeDefined();
    expect(c.stats.queries).toBeLessThanOrEqual(5);
  });

  it("at QUERY_BUDGET 9 or more the lead purge gets its query even when the other jobs have plenty to do", async () => {
    expect(LEAD_FIRST_MIN_BUDGET).toBe(9);
    const now = Date.now();
    for (const budget of ["9", "10", "20", "40", "900"]) {
      await DB.prepare("DELETE FROM leads").run();
      await DB.prepare("DELETE FROM click_events").run();
      await DB.prepare("DELETE FROM view_events").run();
      await DB.prepare("DELETE FROM view_rollup_log").run();
      await seed("old", now - 40 * DAY);
      // A backlog the click and view purges (and the rollup) would each spend a tiny meter on.
      const stmts = [];
      for (let i = 0; i < 1500; i++) {
        stmts.push(DB.prepare("INSERT INTO click_events (ts, link_id) VALUES (?, 'example-one')").bind(now - 200 * DAY - i));
        stmts.push(
          DB.prepare("INSERT INTO view_events (ts, page, src_kind, bot_class, reason) VALUES (?, 'home', 'd', 'h', 'none')").bind(
            now - 40 * DAY - i,
          ),
        );
      }
      await DB.batch(stmts);
      const { c, waits } = ctx();
      await worker.scheduled({} as ScheduledController, full({ QUERY_BUDGET: budget }), c);
      await Promise.all(waits);
      expect(await countLeads(DB), `budget ${budget}`).toBe(0);
    }
  });

  it("below 9 the rollup wins its queries and the lead purge runs last on what is left (no guarantee)", async () => {
    await seed("old", Date.now() - 40 * DAY);
    const c = counted();
    const { c: cx, waits } = ctx();
    await worker.scheduled({} as ScheduledController, full({ DB: c.db, QUERY_BUDGET: "8" }), cx);
    await Promise.all(waits);
    expect(c.stats.first).not.toContain("DELETE FROM leads");
    expect(c.stats.queries).toBeLessThanOrEqual(8);
    expect(await countLeads(DB)).toBe(0); // idle system: the leftover query is enough
  });

  it("the lead purge runs before every other job and takes one query", async () => {
    await seed("old", Date.now() - 40 * DAY);
    const c = counted();
    const { c: cx, waits } = ctx();
    await worker.scheduled({} as ScheduledController, full({ DB: c.db }), cx);
    await Promise.all(waits);
    expect(c.stats.first).toContain("DELETE FROM leads");
    expect(c.stats.first).toContain("kit_events");
    expect(await countLeads(DB)).toBe(0);
  });

  it("with the default budget a backlog drains one bounded batch per run", async () => {
    await seedMany(LEAD_PURGE_BATCH * 2, Date.now() - 60 * DAY);
    const { c, waits } = ctx();
    await worker.scheduled({} as ScheduledController, full(), c);
    await Promise.all(waits);
    expect(await countLeads(DB)).toBe(LEAD_PURGE_BATCH);
  });
});

describe("source hygiene", () => {
  it("no SQL is built by interpolation, with a negative case", () => {
    const flag = (src: string) =>
      (src.match(/`[^`]*`/g) ?? []).filter((l) => /SELECT|INSERT|UPDATE|DELETE/i.test(l) && l.includes("${"));
    expect(flag(retentionSource)).toEqual([]);
    expect(flag("const q = `DELETE FROM leads WHERE email = '${e}'`;")).toHaveLength(1);
    expect(retentionSource).toContain(".bind(");
    expect(retentionSource).not.toMatch(/console\./);
  });
});
