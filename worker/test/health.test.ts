// SPDX-License-Identifier: Apache-2.0
// Analytics health and collection coverage (issues #401, #402). Mocked fetch only: no network, no secret.
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createDispatcher } from "../src/alerts/dispatcher";
import { formatAlert } from "../src/alerts/format";
import { HEALTH_CONDITIONS } from "../src/alerts/types";
import {
  coverageLetter,
  HOUR_MS,
  MAX_NOTICES_PER_TICK,
  runHealth,
  type HealthEnv,
} from "../src/health";
import { padHour } from "../src/viewretention";

const DB = testEnv.DB;
const NOW = Date.UTC(2026, 10, 9, 14, 5, 0); // 2026-11-09 14:05 UTC
const HOUR = Math.floor(NOW / HOUR_MS);
const DISCORD = "https://discord.com/api/webhooks/123456789012345678/AbCdEfGhIjKlMnOpQrStUvWxYz0123456789";
const KEY = "k".repeat(40);

function env(over: Partial<HealthEnv> = {}): HealthEnv {
  return {
    DB,
    SITE_HOST: "links.example.test",
    ALERTS_CONFIG: JSON.stringify({ channels: ["discord"], events: ["analytics_health"], max_per_minute: 30 }),
    ALERT_DISCORD_WEBHOOK: DISCORD,
    ALERT_DEDUP_KEY: KEY,
    DAILY_ALERT_BUDGET: "50",
    ...over,
  };
}

function mockFetch(status = 204) {
  const bodies: string[] = [];
  const fn = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
    bodies.push(String(init?.body ?? ""));
    return new Response(null, { status });
  });
  return { fn: fn as unknown as typeof fetch, bodies, calls: fn };
}

async function meta(key: string, value: string) {
  await DB.prepare("INSERT INTO view_meta (key, value) VALUES (?1, ?2) ON CONFLICT(key) DO UPDATE SET value = excluded.value")
    .bind(key, value)
    .run();
}
const bud = (hour: number, n: number) => meta(`bud:${padHour(hour)}:_all:pv`, String(n));
const stateOf = async (c: string) =>
  (await DB.prepare("SELECT value FROM view_meta WHERE key = ?1").bind(`health:${c}`).first<{ value: string }>())?.value ?? null;
const coverage = async (day: string) =>
  (await DB.prepare("SELECT hours FROM analytics_coverage WHERE day = ?1").bind(day).first<{ hours: string }>())?.hours ?? null;

/** A healthy baseline: heartbeat fresh, checkpoint yesterday-ish, views flowing. */
async function healthy() {
  await meta("rollup_last_ok", String(NOW - 20 * 60_000));
  await meta("rolled_through:links", "2026-11-08");
  await bud(HOUR - 1, 10);
  await bud(HOUR - 30, 10);
}

async function tick(e: HealthEnv, d = createDispatcher(), f = mockFetch(), now = NOW) {
  const res = await runHealth(e, DB, now, { dispatcher: d, fetchFn: f.fn });
  return { res, f, d };
}

beforeEach(async () => {
  await DB.batch([
    DB.prepare("DELETE FROM view_meta"),
    DB.prepare("DELETE FROM analytics_coverage"),
    DB.prepare("DELETE FROM alert_quota"),
    DB.prepare("DELETE FROM alert_dedup"),
  ]);
});

describe("quiet by default", () => {
  it("a healthy site sends nothing and records no state", async () => {
    await healthy();
    const { res, f } = await tick(env());
    expect(res.active).toEqual([]);
    expect(f.calls).not.toHaveBeenCalled();
    for (const c of HEALTH_CONDITIONS) expect(await stateOf(c)).toBeNull();
  });

  it("alerts off (no config, or the event not enabled) sends nothing but still records coverage", async () => {
    await meta("rollup_last_ok", String(NOW - 9 * HOUR_MS));
    for (const cfg of [undefined, JSON.stringify({ channels: ["discord"], events: ["kit_unlocked"] }), "not json"]) {
      const { res, f } = await tick(env({ ALERTS_CONFIG: cfg }));
      expect(f.calls).not.toHaveBeenCalled();
      expect(res.coverage).toBe("o");
    }
    expect(await coverage("2026-11-09")).toBe("-".repeat(13) + "o" + "-".repeat(10));
  });

  it("a fresh install (no heartbeat, no counters) is unknown, not a failure", async () => {
    const { res, f } = await tick(env());
    expect(res.active).toEqual([]);
    expect(f.calls).not.toHaveBeenCalled();
  });
});

describe("each condition fires once and clears", () => {
  it("rollup_overdue", async () => {
    await healthy();
    await meta("rollup_last_ok", String(NOW - 5 * HOUR_MS));
    const d = createDispatcher();
    const a = await tick(env(), d);
    expect(a.res.active).toContain("rollup_overdue");
    expect(a.res.notified).toBe(1);
    expect(a.f.bodies[0]).toContain("the daily rollup has not finished in over 3 hours (about 5 hours)");
    expect(await stateOf("rollup_overdue")).toBe(String(NOW));
    // Still failing next hour: quiet.
    const b = await tick(env(), d, mockFetch(), NOW + HOUR_MS);
    expect(b.f.calls).not.toHaveBeenCalled();
    // Recovers: one cleared notice, state removed.
    await meta("rollup_last_ok", String(NOW + HOUR_MS * 2 - 60_000));
    const c = await tick(env(), d, mockFetch(), NOW + 2 * HOUR_MS);
    expect(c.res.notified).toBe(1);
    expect(c.f.bodies[0]).toContain("the daily rollup is running again");
    expect(await stateOf("rollup_overdue")).toBeNull();
    const e2 = await tick(env(), d, mockFetch(), NOW + 3 * HOUR_MS);
    expect(e2.f.calls).not.toHaveBeenCalled();
  });

  it("rollup_stale", async () => {
    await healthy();
    await meta("rolled_through:links", "2026-11-05"); // 4 days behind
    const d = createDispatcher();
    const a = await tick(env(), d);
    expect(a.res.active).toContain("rollup_stale");
    expect(a.f.bodies[0]).toContain("the rollup checkpoint is more than 2 days behind (about 4 days)");
    await meta("rolled_through:links", "2026-11-08");
    const c = await tick(env(), d, mockFetch(), NOW + HOUR_MS);
    expect(c.f.bodies[0]).toContain("the rollup checkpoint has caught up");
    expect(await stateOf("rollup_stale")).toBeNull();
  });

  it("rollup_stale is not raised for the normal two-day lag just after midnight", async () => {
    await meta("rolled_through:links", "2026-11-07");
    const { res } = await tick(env()); // today is 11-09: lag 2, normal
    expect(res.active).not.toContain("rollup_stale");
  });

  it("zero_views needs earlier traffic, and a kill switch is not an alert", async () => {
    await meta("rollup_last_ok", String(NOW - 600_000));
    await bud(HOUR - 30, 12); // views 24 to 48 hours ago, none since
    const quiet = await tick(env({ VIEWS_KILL: "1" }));
    expect(quiet.res.active).not.toContain("zero_views");
    expect(quiet.res.coverage).toBe("k");
    const d = createDispatcher();
    const a = await tick(env(), d);
    expect(a.res.active).toContain("zero_views");
    expect(a.f.bodies[0]).toContain("no page views were counted in the last 24 hours");
    await bud(HOUR, 3);
    const c = await tick(env(), d, mockFetch(), NOW + 60_000);
    // Dedup window: the cleared notice is a different event (state differs), so it sends.
    expect(c.f.bodies[0]).toContain("page views are being counted again");
    expect(await stateOf("zero_views")).toBeNull();
  });

  it("zero_views stays quiet for a site that never had views", async () => {
    await meta("rollup_last_ok", String(NOW - 600_000));
    const { res } = await tick(env());
    expect(res.active).not.toContain("zero_views");
  });

  it("view_budget_hit", async () => {
    await healthy();
    await bud(HOUR - 1, 480); // the default account ceiling
    const d = createDispatcher();
    const a = await tick(env(), d);
    expect(a.res.active).toContain("view_budget_hit");
    expect(a.res.coverage).toBe("q");
    expect(a.f.bodies[0]).toContain("the hourly page-view budget was reached");
    // Four hours later the window has moved on.
    const c = await tick(env({}), d, mockFetch(), NOW + 4 * HOUR_MS);
    expect(c.f.bodies.join("")).toContain("the page-view budget has room again");
    expect(await stateOf("view_budget_hit")).toBeNull();
  });

  it("alert_budget_near", async () => {
    await healthy();
    const day = Date.UTC(2026, 10, 9);
    await DB.prepare("INSERT INTO alert_quota (site, channel, scope, window_start, count) VALUES ('links.example.test','all','day',?1,?2)")
      .bind(day, 8)
      .run();
    const e = env({ DAILY_ALERT_BUDGET: "10" });
    const d = createDispatcher();
    const a = await tick(e, d);
    expect(a.res.active).toContain("alert_budget_near");
    expect(a.f.bodies[0]).toContain("today's alert budget is almost used up");
    // Next UTC day the counter row is for another window: it clears.
    await meta("rollup_last_ok", String(NOW + 12 * HOUR_MS - 60_000));
    await meta("rolled_through:links", "2026-11-09");
    await bud(HOUR + 11, 10);
    const c = await tick(e, d, mockFetch(), NOW + 12 * HOUR_MS);
    expect(c.f.bodies.join("")).toContain("the alert budget has room again");
  });
});

describe("dedup, quota and failure", () => {
  it("a lost state write does not repeat the message inside the dedup window", async () => {
    await healthy();
    await meta("rollup_last_ok", String(NOW - 5 * HOUR_MS));
    const d = createDispatcher();
    const a = await tick(env(), d);
    expect(a.res.notified).toBe(1);
    await DB.prepare("DELETE FROM view_meta WHERE key LIKE 'health:%'").run(); // pretend the state write was lost
    const b = await tick(env(), d, mockFetch(), NOW + 60_000);
    expect(b.f.calls).not.toHaveBeenCalled();
    expect(d.status().deduped).toBeGreaterThan(0);
  });

  it("the daily alert budget limits messages; a refused one is not marked as sent and is retried later", async () => {
    await healthy();
    await meta("rollup_last_ok", String(NOW - 5 * HOUR_MS));
    await meta("rolled_through:links", "2026-11-01");
    const d = createDispatcher();
    const e = env({ DAILY_ALERT_BUDGET: "1" });
    const a = await tick(e, d);
    expect(a.res.notified).toBe(1);
    expect(d.status().dailyRefused).toBe(1);
    const states = [await stateOf("rollup_overdue"), await stateOf("rollup_stale")];
    expect(states.filter((s) => s !== null)).toHaveLength(1);
    // Next UTC day the budget is fresh and the other one goes out.
    const tomorrow = NOW + 24 * HOUR_MS;
    await meta("rollup_last_ok", String(tomorrow - 5 * HOUR_MS));
    const b = await tick(e, d, mockFetch(), tomorrow);
    expect(b.res.notified).toBe(1);
  });

  it("at most MAX_NOTICES_PER_TICK messages in one tick", async () => {
    await meta("rollup_last_ok", String(NOW - 9 * HOUR_MS));
    await meta("rolled_through:links", "2026-10-01");
    await bud(HOUR - 30, 5);
    await DB.prepare("INSERT INTO alert_quota (site, channel, scope, window_start, count) VALUES ('links.example.test','all','day',?1,45)")
      .bind(Date.UTC(2026, 10, 9))
      .run();
    // overdue, stale, zero_views and alert_budget_near are active; only three are sent in one tick.
    const { res, f } = await tick(env({ DAILY_ALERT_BUDGET: "50" }));
    expect(res.active.length).toBe(4);
    expect(f.calls.mock.calls.length).toBe(MAX_NOTICES_PER_TICK);
  });

  it("a channel failure never throws and leaves the condition to be retried", async () => {
    await healthy();
    await meta("rollup_last_ok", String(NOW - 5 * HOUR_MS));
    const d = createDispatcher();
    const a = await tick(env(), d, mockFetch(500));
    expect(a.res.notified).toBe(0);
    expect(await stateOf("rollup_overdue")).toBeNull();
    expect(d.status().channels.discord?.failed.server).toBe(1);
    // Coverage was still written.
    expect(await coverage("2026-11-09")).not.toBeNull();
  });

  it("a database that fails writes nothing, sends nothing and does not throw", async () => {
    const broken = new Proxy(DB, {
      get(t, p) {
        if (p === "batch" || p === "prepare") {
          return () => {
            throw new Error("down");
          };
        }
        return Reflect.get(t, p, t);
      },
    }) as D1Database;
    const f = mockFetch();
    const res = await runHealth(env(), broken, NOW, { dispatcher: createDispatcher(), fetchFn: f.fn });
    expect(res).toEqual({ coverage: "", active: [], notified: 0 });
    expect(f.calls).not.toHaveBeenCalled();
  });

  it("garbage in the counters is unknown, not an alert", async () => {
    await meta("rollup_last_ok", "not-a-number");
    await meta("rolled_through:links", "yesterday");
    await meta(`bud:${padHour(HOUR)}:_all:pv`, "x");
    const { res, f } = await tick(env());
    expect(res.active).toEqual([]);
    expect(f.calls).not.toHaveBeenCalled();
  });
});

describe("no content in alerts", () => {
  it("every message is fixed words, a UTC time and one count; site ids and labels never appear", async () => {
    await meta("rollup_last_ok", String(NOW - 9 * HOUR_MS));
    await meta("rolled_through:secretsite", "2026-10-01");
    await bud(HOUR - 30, 5);
    await meta("rollup_err", "https://leak.example.test/path?token=abc");
    const f = mockFetch();
    await tick(env({ SITE_HOST: "links.example.test" }), createDispatcher(), f);
    expect(f.bodies.length).toBeGreaterThan(0);
    for (const b of f.bodies) {
      const text = String((JSON.parse(b) as { content: string }).content);
      expect(text).not.toMatch(/secretsite|leak|token|https?:|links\.example|@|\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/);
      expect(text).toMatch(/^Reach Around health: [a-z0-9 ,'()-]+ \(as of \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC\)\./);
    }
  });

  it("an unknown condition or hostile fields are never echoed", () => {
    const m = formatAlert(
      { kind: "analytics_health", atMs: NOW, condition: "https://x.test/<b>" as never, state: "firing", count: 1e99 },
      "full",
      "plain",
    );
    expect(m.text).toBe("Reach Around health: an unknown condition changed (as of 2026-11-09 14:05 UTC).");
    const n = formatAlert({ kind: "analytics_health", atMs: NOW, condition: "rollup_overdue", state: "firing", count: 1e99 }, "full", "plain");
    expect(n.text).toContain("(about 99999 hours)");
  });
});

describe("coverage (gaps)", () => {
  it("records one letter per hour, without overwriting other hours", async () => {
    await runHealth(env({ ALERTS_CONFIG: undefined }), DB, NOW);
    await runHealth(env({ ALERTS_CONFIG: undefined, VIEWS_KILL: "true" }), DB, NOW + HOUR_MS);
    await bud(HOUR + 1, 480);
    await runHealth(env({ ALERTS_CONFIG: undefined }), DB, NOW + 2 * HOUR_MS);
    expect(await coverage("2026-11-09")).toBe("-".repeat(13) + "okq" + "-".repeat(8));
  });

  it("an hour with no tick stays '-' (an outage), a quiet hour is 'o' (a zero, not a gap)", async () => {
    await runHealth(env({ ALERTS_CONFIG: undefined }), DB, NOW);
    await runHealth(env({ ALERTS_CONFIG: undefined }), DB, NOW + 3 * HOUR_MS);
    expect(await coverage("2026-11-09")).toBe("-".repeat(13) + "o--o" + "-".repeat(7));
  });

  it("the first tick of a UTC day fills the last hour of the day before", async () => {
    await runHealth(env({ ALERTS_CONFIG: undefined }), DB, Date.UTC(2026, 10, 10, 0, 5));
    expect(await coverage("2026-11-09")).toBe("-".repeat(23) + "o");
  });

  it("rows older than the keep window are deleted", async () => {
    await DB.prepare("INSERT INTO analytics_coverage (day, hours) VALUES ('2025-01-01', ?1)").bind("o".repeat(24)).run();
    await runHealth(env({ ALERTS_CONFIG: undefined }), DB, NOW);
    expect(await coverage("2025-01-01")).toBeNull();
  });

  it("the table refuses a bad row", async () => {
    await expect(DB.prepare("INSERT INTO analytics_coverage (day, hours) VALUES ('x', ?1)").bind("o".repeat(24)).run()).rejects.toThrow();
    await expect(DB.prepare("INSERT INTO analytics_coverage (day, hours) VALUES ('2026-11-09', 'ooo')").run()).rejects.toThrow();
    await expect(DB.prepare("INSERT INTO analytics_coverage (day, hours) VALUES ('2026-11-09', ?1)").bind("z".repeat(24)).run()).rejects.toThrow();
  });

  it("coverageLetter prefers the kill switch, then the budget", () => {
    const inp = { meta: new Map(), views: new Map([[HOUR - 1, 480]]), alertToday: 0 };
    expect(coverageLetter(inp, HOUR, { killed: true, accountLimit: 480 })).toBe("k");
    expect(coverageLetter(inp, HOUR, { killed: false, accountLimit: 480 })).toBe("q");
    expect(coverageLetter(inp, HOUR, { killed: false, accountLimit: 0 })).toBe("o");
  });
});
