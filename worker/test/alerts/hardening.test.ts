// SPDX-License-Identifier: Apache-2.0
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { parseAlertsConfig } from "../../src/alerts/config";
import { createDispatcher, type AlertsEnv } from "../../src/alerts/dispatcher";
import { formatAlert } from "../../src/alerts/format";
import { claimDedup, DEDUP_ROW_CAP, eventHash, reserveQuota } from "../../src/alerts/quota";
import { neutralize } from "../../src/alerts/sanitize";
import type { AlertEvent } from "../../src/alerts/types";
import { embedsIpLiteral, validateHomeserver } from "../../src/alerts/validate";

const DB = testEnv.DB;
const KEY = "k".repeat(40);
const KEY_BYTES = new TextEncoder().encode(KEY);
const T0 = Date.UTC(2026, 10, 9, 14, 3, 30);
const MX_TOKEN = "syt_MATRIXSECRET_0123456789";
const DISCORD = "https://discord.com/api/webhooks/123456789012345678/AbCdEfGhIjKlMnOpQrStUvWxYz0123456789";
const cfg = (over: Record<string, unknown> = {}) =>
  JSON.stringify({ channels: ["discord"], events: ["kit_unlocked"], ...over });
const unlock = (email: string, atMs = T0): AlertEvent => ({ kind: "kit_unlocked", atMs, email });
function mockFetch() {
  const calls: string[] = [];
  const fn = vi.fn(async (url: string | URL | Request) => {
    calls.push(String(url));
    return new Response(null, { status: 204 });
  });
  return { fn: fn as unknown as typeof fetch, calls };
}

beforeEach(async () => {
  await DB.batch([DB.prepare("DELETE FROM alert_quota"), DB.prepare("DELETE FROM alert_dedup")]);
});

describe("Matrix homeserver hardening", () => {
  const allow = ["matrix.example.org"];
  it("rejects names that embed an IP in any label form", () => {
    for (const h of [
      "127.0.0.1.nip.io", "169-254-169-254.sslip.io", "10.0.0.1.example.org", "10-0-0-1.example.org", "7f000001.example.org",
      "2130706433.example.org", "0x7f.example.org", "0177.0.0.1.example.org", "a.192.168.0.1.example.org", "1.2.3.4",
    ]) {
      expect(embedsIpLiteral(h)).toBe(true);
      expect(validateHomeserver(`https://${h}`, [h])).toBeNull();
    }
    expect(embedsIpLiteral("matrix.example.org")).toBe(false);
    expect(embedsIpLiteral("matrix-1.example.org")).toBe(false);
  });
  it("rejects wildcard-DNS and rebinding suffixes even when listed", () => {
    for (const h of ["foo.nip.io", "foo.sslip.io", "foo.xip.io", "foo.traefik.me", "foo.localtest.me", "foo.lvh.me", "nip.io", "a.b.local.gd"]) {
      expect(validateHomeserver(`https://${h}`, [h])).toBeNull();
    }
  });
  it("requires an exact allowed_hosts match", () => {
    expect(validateHomeserver("https://matrix.example.org", allow)).toBe("matrix.example.org");
    expect(validateHomeserver("https://matrix.example.org", [])).toBeNull();
    expect(validateHomeserver("https://sub.matrix.example.org", allow)).toBeNull();
    expect(validateHomeserver("https://matrix.example.org.evil.example.net", allow)).toBeNull();
    expect(validateHomeserver("https://example.org", allow)).toBeNull();
  });
  it("config: allowed_hosts must be lowercase valid names, at most four, no unknown keys", () => {
    const base = { channels: ["matrix"], events: ["kit_unlocked"] };
    const ok1 = parseAlertsConfig(JSON.stringify({ ...base, matrix: { allowed_hosts: ["matrix.example.org"] } }));
    expect(ok1?.matrixAllowedHosts).toEqual(["matrix.example.org"]);
    for (const bad of [
      { allowed_hosts: ["Matrix.Example.org"] }, { allowed_hosts: ["127.0.0.1"] }, { allowed_hosts: ["a.nip.io"] },
      { allowed_hosts: ["https://matrix.example.org"] }, { allowed_hosts: "matrix.example.org" },
      { allowed_hosts: ["a.example.org", "b.example.org", "c.example.org", "d.example.org", "e.example.org"] },
      { allowed_hosts: [], extra: 1 },
    ]) {
      expect(parseAlertsConfig(JSON.stringify({ ...base, matrix: bad }))).toBeNull();
    }
  });
  const base = {
    DB,
    ALERTS_CONFIG: cfg({ channels: ["matrix"], matrix: { allowed_hosts: ["matrix.example.org"] } }),
    ALERT_MATRIX_ROOM_ID: "!room123:example.org",
    ALERT_MATRIX_ACCESS_TOKEN: MX_TOKEN,
  } satisfies AlertsEnv;
  it("reads the homeserver from the secret binding", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    await d.notify({ ...base, ALERT_MATRIX_SERVER: "https://matrix.example.org" }, unlock("a@example.com"), { fetchFn: m.fn, nowMs: T0 });
    expect(m.calls).toHaveLength(1);
    expect(d.status().deprecatedHomeserver).toBe(0);
  });
  it("the deprecated plain variable still works but is counted, and a host not in allowed_hosts gets no token", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    await d.notify({ ...base, ALERT_MATRIX_HOMESERVER: "https://matrix.example.org" }, unlock("a@example.com"), { fetchFn: m.fn, nowMs: T0 });
    expect(m.calls).toHaveLength(1);
    expect(d.status().deprecatedHomeserver).toBe(1);
    await d.notify({ ...base, ALERT_MATRIX_SERVER: "https://other.example.org" }, unlock("b@example.com"), { fetchFn: m.fn, nowMs: T0 + 1 });
    expect(m.calls).toHaveLength(1);
    expect(d.status().unconfigured).toBe(1);
  });
  it("the plain variable is refused when allowed_hosts is empty", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    await d.notify(
      { ...base, ALERTS_CONFIG: cfg({ channels: ["matrix"] }), ALERT_MATRIX_HOMESERVER: "https://matrix.example.org" },
      unlock("a@example.com"),
      { fetchFn: m.fn, nowMs: T0 },
    );
    expect(m.calls).toHaveLength(0);
  });
});

describe("shared quota (D1)", () => {
  it("is atomic under parallel calls: exactly `limit` grants", async () => {
    const results = await Promise.all(Array.from({ length: 60 }, () => reserveQuota(DB, "default", "discord", "minute", T0, 30)));
    const granted = results.filter((r) => r !== null);
    expect(granted).toHaveLength(30);
    expect(new Set(granted).size).toBe(30); // each grant saw a distinct new count
    expect(Math.max(...(granted as number[]))).toBe(30);
  });
  it("limit 0 refuses and writes no row; a new window starts a new count", async () => {
    expect(await reserveQuota(DB, "default", "discord", "day", T0, 0)).toBeNull();
    expect((await DB.prepare("SELECT COUNT(*) AS n FROM alert_quota").first<{ n: number }>())?.n).toBe(0);
    expect(await reserveQuota(DB, "default", "discord", "minute", T0, 1)).toBe(1);
    expect(await reserveQuota(DB, "default", "discord", "minute", T0, 1)).toBeNull();
    expect(await reserveQuota(DB, "default", "discord", "minute", T0 + 60_000, 1)).toBe(1);
  });
  it("rejects bad arguments", async () => {
    await expect(reserveQuota(DB, "Bad Site", "discord", "minute", T0, 1)).rejects.toThrow(RangeError);
    await expect(reserveQuota(DB, "default", "x y", "minute", T0, 1)).rejects.toThrow(RangeError);
    await expect(reserveQuota(DB, "default", "discord", "hour" as never, T0, 1)).rejects.toThrow(RangeError);
  });
  it("two dispatchers (two isolates) share one per-minute limit: N sends in all, the last slot is the notice", async () => {
    const env: AlertsEnv = { DB, ALERT_DEDUP_KEY: KEY, ALERT_DISCORD_WEBHOOK: DISCORD, ALERTS_CONFIG: cfg({ max_per_minute: 3 }) };
    const m = mockFetch();
    const a = createDispatcher();
    const b = createDispatcher();
    await Promise.all(
      Array.from({ length: 20 }, (_, i) => (i % 2 ? a : b).notify(env, unlock(`u${i}@example.com`, T0), { fetchFn: m.fn, nowMs: T0 + i })),
    );
    expect(m.calls).toHaveLength(3); // 2 alerts + 1 notice: the notice counts inside the limit
  });
  it("never sends more than the limit in a minute (no off-by-one with the notice)", async () => {
    const env: AlertsEnv = { DB, ALERT_DEDUP_KEY: KEY, ALERT_DISCORD_WEBHOOK: DISCORD, ALERTS_CONFIG: cfg({ max_per_minute: 30 }), DAILY_ALERT_BUDGET: "1000" };
    const m = mockFetch();
    const d = createDispatcher();
    for (let i = 0; i < 100; i++) await d.notify(env, unlock(`u${i}@example.com`), { fetchFn: m.fn, nowMs: T0 + i });
    expect(m.calls).toHaveLength(30);
  });
  it("enforces DAILY_ALERT_BUDGET across dispatchers, and resets the next UTC day", async () => {
    const env: AlertsEnv = { DB, ALERT_DEDUP_KEY: KEY, ALERT_DISCORD_WEBHOOK: DISCORD, ALERTS_CONFIG: cfg({ max_per_minute: 30 }), DAILY_ALERT_BUDGET: "4" };
    const m = mockFetch();
    for (let i = 0; i < 10; i++) await createDispatcher().notify(env, unlock(`u${i}@example.com`), { fetchFn: m.fn, nowMs: T0 + i * 1000 });
    expect(m.calls).toHaveLength(4);
    await createDispatcher().notify(env, unlock("late@example.com"), { fetchFn: m.fn, nowMs: T0 + 86_400_000 });
    expect(m.calls).toHaveLength(5);
    const zero = createDispatcher();
    await zero.notify({ ...env, DAILY_ALERT_BUDGET: "0" }, unlock("z@example.com"), { fetchFn: m.fn, nowMs: T0 + 2 * 86_400_000 });
    expect(m.calls).toHaveLength(5);
    expect(zero.status().dailyRefused).toBe(1);
  });
  it("fails closed when the shared state is unusable", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    const down = (): never => {
      throw new Error("db down");
    };
    const broken = { prepare: down, batch: down } as unknown as D1Database;
    await d.notify({ DB: broken, ALERT_DISCORD_WEBHOOK: DISCORD, ALERTS_CONFIG: cfg() }, unlock("a@example.com"), { fetchFn: m.fn, nowMs: T0 });
    expect(m.calls).toHaveLength(0);
    expect(d.status().quotaErrors).toBe(1);
  });
});

describe("shared dedup (D1)", () => {
  it("a second isolate does not resend an identical event; it is allowed after expiry", async () => {
    const env: AlertsEnv = { DB, ALERT_DEDUP_KEY: KEY, ALERT_DISCORD_WEBHOOK: DISCORD, ALERTS_CONFIG: cfg({ max_per_minute: 30 }), DAILY_ALERT_BUDGET: "1000" };
    const m = mockFetch();
    await createDispatcher().notify(env, unlock("same@example.com"), { fetchFn: m.fn, nowMs: T0 });
    await createDispatcher().notify(env, unlock("same@example.com"), { fetchFn: m.fn, nowMs: T0 + 1000 });
    expect(m.calls).toHaveLength(1);
    await createDispatcher().notify(env, unlock("same@example.com"), { fetchFn: m.fn, nowMs: T0 + 11 * 60_000 });
    expect(m.calls).toHaveLength(2);
  });
  it("holds beyond 200 distinct events: an old identical event is still a duplicate", async () => {
    const env: AlertsEnv = { DB, ALERT_DEDUP_KEY: KEY, ALERT_DISCORD_WEBHOOK: DISCORD, ALERTS_CONFIG: cfg({ max_per_minute: 30 }), DAILY_ALERT_BUDGET: "100000" };
    const m = mockFetch();
    const d = createDispatcher();
    await d.notify(env, unlock("first@example.com"), { fetchFn: m.fn, nowMs: T0 });
    for (let i = 0; i < 250; i++) await d.notify(env, unlock(`n${i}@example.com`), { fetchFn: m.fn, nowMs: T0 + 1000 + i });
    const before = m.calls.length;
    await createDispatcher().notify(env, unlock("first@example.com"), { fetchFn: m.fn, nowMs: T0 + 5000 });
    await d.notify(env, unlock("first@example.com"), { fetchFn: m.fn, nowMs: T0 + 6000 });
    expect(m.calls).toHaveLength(before);
  });
  it("is bounded: a hard row cap, and expired rows are pruned on write", async () => {
    const h = (i: number) => eventHash(KEY_BYTES, "default", "kit_unlocked", `v${i}`);
    for (let i = 0; i < DEDUP_ROW_CAP + 20; i++) await claimDedup(DB, "default", await h(i), T0, 600_000);
    const n = (await DB.prepare("SELECT COUNT(*) AS n FROM alert_dedup").first<{ n: number }>())?.n;
    expect(n).toBe(DEDUP_ROW_CAP);
    expect(await claimDedup(DB, "default", await h(DEDUP_ROW_CAP + 5), T0, 600_000)).toBe(false);
    expect(await claimDedup(DB, "default", await h(0), T0 + 700_000, 600_000)).toBe(true);
    expect((await DB.prepare("SELECT COUNT(*) AS n FROM alert_dedup").first<{ n: number }>())?.n).toBe(1);
  });
  it("stores no address, only a hash", async () => {
    const env: AlertsEnv = { DB, ALERT_DEDUP_KEY: KEY, ALERT_DISCORD_WEBHOOK: DISCORD, ALERTS_CONFIG: cfg() };
    await createDispatcher().notify(env, unlock("privacy@example.com"), { fetchFn: mockFetch().fn, nowMs: T0 });
    const rows = await DB.prepare("SELECT * FROM alert_dedup").all();
    expect(JSON.stringify(rows.results)).not.toContain("privacy");
  });
});

describe("link and hostname neutralization", () => {
  const forms = [
    "www.example", "example.com", "http://evil.example.net/x", "mailto:x@y.com", "javascript:alert(1)", "tel:+15555550100",
    "//evil.example.net", "evil.example.net:8080/path", "ftp://h/x", "1.2.3.4", "user@host.example", "data:text/html,hi", "xn--e1afmkfd.example",
  ];
  for (const mode of ["plain", "markdown"] as const) {
    it(`leaves nothing linkable in ${mode} text`, () => {
      for (const f of forms) {
        const out = neutralize(`see ${f} now`, 120, mode);
        expect(out).not.toMatch(/[A-Za-z0-9]\.[A-Za-z0-9]/);
        expect(out).not.toMatch(/[A-Za-z][A-Za-z0-9+.-]*:/);
        expect(out).not.toContain("//");
        expect(out).not.toContain("@");
      }
    });
  }
  it("shows only a normalized address; free text in the address slot is not rendered", () => {
    expect(formatAlert(unlock("partner@example.com"), "full", "plain").text).toContain("partner(at)example(dot)com");
    for (const bad of ["see www.example.com now", "mailto:x@y.com", "Partner@Example.com", "a@b", "x@y.com @everyone", ""]) {
      const t = formatAlert(unlock(bad), "full", "plain").text;
      expect(t).toContain("by a visitor");
      expect(t).not.toMatch(/www|mailto|everyone/);
    }
  });
});

// A D1 stand-in that fails chosen statements, to prove a failed admission step leaves no claim and no charge.
function flakyDb(failWhen: (sql: string) => boolean): D1Database {
  return new Proxy(DB, {
    get(target, prop) {
      if (prop === "prepare") {
        return (sql: string) => {
          if (failWhen(sql)) throw new Error("db down");
          return target.prepare(sql);
        };
      }
      const v = (target as never)[prop];
      return typeof v === "function" ? (v as (...a: unknown[]) => unknown).bind(target) : v;
    },
  }) as D1Database;
}
const rows = async (t: string) => (await DB.prepare(`SELECT COUNT(*) AS n FROM ${t}`).first<{ n: number }>())?.n;

describe("admission is all or nothing", () => {
  const env = (db: D1Database = DB): AlertsEnv => ({
    DB: db, ALERT_DEDUP_KEY: KEY, ALERT_DISCORD_WEBHOOK: DISCORD, ALERTS_CONFIG: cfg({ max_per_minute: 30 }), DAILY_ALERT_BUDGET: "50",
  });
  it("a failing daily reserve leaves no dedup claim, and a retry after recovery sends", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    const down = flakyDb((sql) => sql.includes("'day'") === false && sql.includes("alert_quota") && sql.includes("RETURNING") && dayCall());
    let n = 0;
    function dayCall(): boolean {
      n++;
      return n === 2; // the 2nd reserve (the daily one) fails
    }
    await d.notify(env(down), unlock("retry@example.com"), { fetchFn: m.fn, nowMs: T0 });
    expect(m.calls).toHaveLength(0);
    expect(await rows("alert_dedup")).toBe(0);
    expect(d.status().quotaErrors).toBe(1);
    await d.notify(env(), unlock("retry@example.com"), { fetchFn: m.fn, nowMs: T0 + 1000 });
    expect(m.calls).toHaveLength(1);
  });
  it("a failure inside claimDedup does not mark the local cache", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    await d.notify(env(flakyDb((sql) => sql.includes("alert_dedup"))), unlock("c@example.com"), { fetchFn: m.fn, nowMs: T0 });
    expect(m.calls).toHaveLength(0);
    await d.notify(env(), unlock("c@example.com"), { fetchFn: m.fn, nowMs: T0 + 1000 });
    expect(m.calls).toHaveLength(1);
    expect(d.status().deduped).toBe(0);
  });
  it("an exhausted daily budget releases the claim and refunds the minute slot", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    await d.notify({ ...env(), DAILY_ALERT_BUDGET: "0" }, unlock("z@example.com"), { fetchFn: m.fn, nowMs: T0 });
    expect(await rows("alert_dedup")).toBe(0);
    const minute = await DB.prepare("SELECT COALESCE(SUM(count), 0) AS n FROM alert_quota WHERE scope = 'minute'").first<{ n: number }>();
    expect(minute?.n).toBe(0);
  });
});

describe("daily budget is charged only for attempted sends", () => {
  it("an enabled channel with missing or invalid bindings consumes no daily budget across 100 events", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    for (const hook of [undefined, "https://evil.example.net/x"]) {
      const e: AlertsEnv = { DB, ALERT_DEDUP_KEY: KEY, ALERT_DISCORD_WEBHOOK: hook, ALERTS_CONFIG: cfg(), DAILY_ALERT_BUDGET: "20" };
      for (let i = 0; i < 50; i++) await d.notify(e, unlock(`u${i}@example.com`), { fetchFn: m.fn, nowMs: T0 + i });
    }
    expect(m.calls).toHaveLength(0);
    expect(await rows("alert_quota")).toBe(0);
    expect(await rows("alert_dedup")).toBe(0);
    // the budget is still whole afterwards
    await d.notify({ DB, ALERT_DEDUP_KEY: KEY, ALERT_DISCORD_WEBHOOK: DISCORD, ALERTS_CONFIG: cfg(), DAILY_ALERT_BUDGET: "20" }, unlock("ok@example.com"), { fetchFn: m.fn, nowMs: T0 + 99 });
    expect(m.calls).toHaveLength(1);
  });
  it("events refused by every channel's minute limit consume no extra daily budget", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    const e: AlertsEnv = { DB, ALERT_DEDUP_KEY: KEY, ALERT_DISCORD_WEBHOOK: DISCORD, ALERTS_CONFIG: cfg({ max_per_minute: 1 }), DAILY_ALERT_BUDGET: "20" };
    for (let i = 0; i < 40; i++) await createDispatcher().notify(e, unlock(`u${i}@example.com`), { fetchFn: m.fn, nowMs: T0 + i });
    expect(m.calls).toHaveLength(1);
    const day = await DB.prepare("SELECT count FROM alert_quota WHERE scope = 'day'").first<{ count: number }>();
    expect(day?.count).toBe(1);
    expect(await rows("alert_dedup")).toBe(1);
  });
});

describe("keyed dedup hash", () => {
  it("differs per key, is stable per key, and never holds an address", async () => {
    const a = await eventHash(KEY_BYTES, "default", "kit_unlocked", "Same@Example.com");
    const b = await eventHash(new TextEncoder().encode("j".repeat(40)), "default", "kit_unlocked", "same@example.com");
    expect(a).toMatch(/^[0-9a-f]{32}$/);
    expect(a).not.toBe(b);
    expect(a).toBe(await eventHash(KEY_BYTES, "default", "kit_unlocked", "same@example.com"));
    await createDispatcher().notify(
      { DB, ALERT_DEDUP_KEY: KEY, ALERT_DISCORD_WEBHOOK: DISCORD, ALERTS_CONFIG: cfg() }, unlock("visible@example.com"),
      { fetchFn: mockFetch().fn, nowMs: T0 },
    );
    expect(JSON.stringify((await DB.prepare("SELECT * FROM alert_dedup").all()).results)).not.toContain("visible");
  });
  it("without a usable key dedup is off and counted, never a constant key; alerts still go out", async () => {
    for (const k of [undefined, "", "short"]) {
      const m = mockFetch();
      const d = createDispatcher();
      const e: AlertsEnv = { DB, ALERT_DEDUP_KEY: k, ALERT_DISCORD_WEBHOOK: DISCORD, ALERTS_CONFIG: cfg({ max_per_minute: 30 }), DAILY_ALERT_BUDGET: "50" };
      await d.notify(e, unlock("same@example.com"), { fetchFn: m.fn, nowMs: T0 });
      await d.notify(e, unlock("same@example.com"), { fetchFn: m.fn, nowMs: T0 + 1000 });
      expect(m.calls).toHaveLength(2);
      expect(d.status().dedupDisabled).toBe(2);
      expect(await rows("alert_dedup")).toBe(0);
      await DB.prepare("DELETE FROM alert_quota").run();
    }
  });
});

describe("oversized input is cheap and capped", () => {
  it("a 10 MB address is capped before any normalization and shown as a visitor", async () => {
    const huge = "A".repeat(10_000_000) + "@example.com";
    const m = mockFetch();
    const t = Date.now();
    await createDispatcher().notify(
      { DB, ALERT_DEDUP_KEY: KEY, ALERT_DISCORD_WEBHOOK: DISCORD, ALERTS_CONFIG: cfg() }, unlock(huge), { fetchFn: m.fn, nowMs: T0 },
    );
    expect(Date.now() - t).toBeLessThan(1500);
    expect(m.calls).toHaveLength(1);
    const h1 = await eventHash(KEY_BYTES, "default", "kit_unlocked", huge);
    const h2 = await eventHash(KEY_BYTES, "default", "kit_unlocked", huge.slice(0, 254));
    expect(h1).toBe(h2);
  });
  it("ignores malformed events without throwing", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    for (const bad of [null, undefined, 5, "x", { kind: "nope" }, { kind: "kit_unlocked", email: { toString() { throw new Error("x"); } } }]) {
      await expect(d.notify({ DB, ALERT_DISCORD_WEBHOOK: DISCORD, ALERTS_CONFIG: cfg() }, bad as never, { fetchFn: m.fn, nowMs: T0 })).resolves.toBeUndefined();
    }
  });
});
