// SPDX-License-Identifier: Apache-2.0
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createDispatcher } from "../../src/alerts/dispatcher";
import { GATED_KV_KEY, GATE_ROUTE, VIEW_ROUTE, handleGate, type GateEnv } from "../../src/kit/gate";
import { getUnstoredSessionFloor, setSessionFloor, setUnstoredSessionFloor } from "../../src/kit/leads";
import { mintUnstoredId } from "../../src/kit/unstored";
import { COOKIE_NAME, signToken } from "../../src/kit/token";
import { resetEventCapCache } from "../../src/kit/budgets";
import { resetSeparationCache } from "../../src/kit/separation";
import worker from "../../src/index";

const HOST = "links.example.test";
const ORIGIN = `https://${HOST}`;
const KEY = "k".repeat(40);
const HOOK = "https://discord.com/api/webhooks/123456789012345678/SECRETtokenSECRETtokenSECRET0123456789";
const T0 = Date.UTC(2026, 10, 9, 14, 3, 30);
const NOW_SEC = Math.floor(T0 / 1000);
const CANARY = "CANARY-GATED-7f3a";
const EMAIL = "pat@example.com";

interface Fake {
  calls: { url: string; body: string }[];
  fn: typeof fetch;
}
/** Fake network: siteverify approves only the token "ok" (every time, like an eventually consistent replay); Discord takes anything. */
function fake(
  opts: {
    siteverify?: "ok" | "throw" | "wrong-host" | "wrong-action" | "error-code" | "500";
    /** The vendor's challenge time in ms (default T0); "missing" omits it, "garbage" sends text that is not a time. */
    challenge?: number | "missing" | "garbage";
    /** The alert webhook's answer status (default 204). */
    discord?: number;
  } = {},
): Fake {
  const calls: Fake["calls"] = [];
  const fn = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    const u = String(url);
    const body = String(init?.body ?? "");
    calls.push({ url: u, body });
    if (u.includes("siteverify")) {
      const mode = opts.siteverify ?? "ok";
      if (mode === "throw") throw new Error("network");
      if (mode === "500") return new Response("{}", { status: 500 });
      const good = new URLSearchParams(body).get("response")?.startsWith("ok") === true;
      const r: Record<string, unknown> = { success: good, hostname: HOST, action: "gate", "error-codes": good ? [] : ["invalid-input-response"] };
      const ch = opts.challenge ?? T0;
      if (ch === "garbage") r.challenge_ts = "not a time";
      else if (ch !== "missing") r.challenge_ts = new Date(ch).toISOString();
      if (mode === "wrong-host") r.hostname = "evil.example";
      if (mode === "wrong-action") r.action = "send";
      if (mode === "error-code") r["error-codes"] = ["timeout-or-duplicate"];
      return new Response(JSON.stringify(r), { status: 200, headers: { "content-type": "application/json" } });
    }
    return new Response(null, { status: opts.discord ?? 204 });
  });
  return { calls, fn: fn as unknown as typeof fetch };
}

interface Limiter {
  keys: string[];
  limit(o: { key: string }): Promise<{ success: boolean }>;
}
function limiter(success = true): Limiter {
  const keys: string[] = [];
  return {
    keys,
    async limit(o) {
      keys.push(o.key);
      return { success };
    },
  };
}

function kv(page: string | null = `<!doctype html><title>kit</title><p>${CANARY}`): { get: ReturnType<typeof vi.fn> } {
  return { get: vi.fn(async (k: string) => (k === GATED_KV_KEY ? page : null)) };
}

function baseEnv(over: Record<string, unknown> = {}): GateEnv {
  return {
    DB: testEnv.DB,
    LEADS_DB: testEnv.LEADS_DB,
    SITE_HOST: HOST,
    KIT: kv(),
    GATE_LIMITER: limiter(),
    KIT_VIEW_LIMITER: limiter(),
    GATE_CHECK_LIMITER: limiter(),
    TOKEN_HMAC_KEY: KEY,
    TURNSTILE_SECRET: "1x0000000000000000000000000000000AA",
    VIEW_HASH_SECRET: btoa("s".repeat(40)),
    ALERT_DEDUP_KEY: "d".repeat(40),
    ALERTS_CONFIG: JSON.stringify({ channels: ["discord"], events: ["kit_unlocked", "analytics_health"] }),
    ALERT_DISCORD_WEBHOOK: HOOK,
    ...over,
  } as unknown as GateEnv;
}

interface Opts {
  fields?: Record<string, string> | string;
  headers?: Record<string, string | null>;
  method?: string;
  path?: string;
  cookie?: string | null;
}
function req(o: Opts = {}): Request {
  const fields = o.fields ?? { email: EMAIL, "cf-turnstile-response": "ok" };
  const body = typeof fields === "string" ? fields : new URLSearchParams(fields).toString();
  const headers: Record<string, string> = {
    "content-type": "application/x-www-form-urlencoded",
    origin: ORIGIN,
    "cf-connecting-ip": "203.0.113.9",
    "cf-ipcountry": "US",
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
  };
  if (o.cookie) headers.cookie = o.cookie;
  for (const [k, v] of Object.entries(o.headers ?? {})) {
    if (v === null) delete headers[k];
    else headers[k] = v;
  }
  const method = o.method ?? "POST";
  return new Request(`${ORIGIN}${o.path ?? GATE_ROUTE}`, { method, headers, body: method === "POST" ? body : undefined });
}

const pending: Promise<unknown>[] = [];
async function post(o: Opts = {}, e: GateEnv = baseEnv(), f: Fake = fake(), nowMs = T0, extra: { newLeadsPerDay?: number } = {}) {
  const ctx = { waitUntil: (p: Promise<unknown>) => void pending.push(p) };
  const res = await handleGate(req(o), e, { fetchFn: f.fn, nowMs, notify: createDispatcher().notify, ctx, ...extra });
  await Promise.all(pending.splice(0));
  return { res, f };
}

/** Row count of a table in the database that owns it: leads and kit_events are in LEADS_DB (issue #772). */
async function count(table: string): Promise<number> {
  const db = table === "leads" || table === "kit_events" ? testEnv.LEADS_DB : testEnv.DB;
  return (await db.prepare(`SELECT COUNT(*) AS n FROM ${table}`).first<{ n: number }>())!.n;
}

function setCookie(res: Response): string | null {
  return res.headers.get("set-cookie");
}
function cookieHeader(res: Response): string {
  return setCookie(res)!.split(";")[0]!;
}

beforeEach(async () => {
  resetEventCapCache();
  resetSeparationCache();
  await testEnv.LEADS_DB.batch([
    testEnv.LEADS_DB.prepare("DELETE FROM kit_events"),
    testEnv.LEADS_DB.prepare("DELETE FROM leads"),
    testEnv.LEADS_DB.prepare("DELETE FROM kit_meta"),
  ]);
  await testEnv.DB.batch([
    testEnv.DB.prepare("DELETE FROM kit_events"),
    testEnv.DB.prepare("DELETE FROM leads"),
    testEnv.DB.prepare("DELETE FROM counters"),
    testEnv.DB.prepare("DELETE FROM kit_meta"),
    testEnv.DB.prepare("DELETE FROM alert_quota"),
    testEnv.DB.prepare("DELETE FROM alert_dedup"),
  ]);
});

describe("POST /k/gate success", () => {
  it("stores one lead and an unlock event, sets the __Host- cookie, redirects to /k/view with no token in the URL or body", async () => {
    const { res } = await post();
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("/k/view");
    expect(res.headers.get("cache-control")).toBe("private, no-store");
    const c = setCookie(res)!;
    expect(c).toMatch(new RegExp(`^${COOKIE_NAME}=v1\\.[A-Za-z0-9_.-]+; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=86400$`));
    expect(c).not.toMatch(/Domain=/i);
    const token = c.split("=")[1]!.split(";")[0]!;
    expect(res.headers.get("location")).not.toContain(token);
    expect(await res.text()).toBe("");
    const lead = await testEnv.LEADS_DB.prepare("SELECT email, source, country, verified_ts FROM leads").first<Record<string, unknown>>();
    expect(lead).toEqual({ email: EMAIL, source: "gate", country: "US", verified_ts: null });
    expect(await count("kit_events")).toBe(1);
  });

  it("alerts the owner once with the kit unlock event, and a failing sink does not fail the unlock", async () => {
    const f = fake();
    const { res } = await post({}, baseEnv(), f);
    expect(res.status).toBe(303);
    expect(res.headers.get("set-cookie")).not.toBeNull();
    const hooks = f.calls.filter((c) => c.url.startsWith("https://discord.com/"));
    expect(hooks).toHaveLength(1);

    const bad = vi.fn(async () => {
      throw new Error("sink down");
    }) as unknown as typeof import("../../src/alerts/dispatcher").notifyOwner;
    const ctx = { waitUntil: (p: Promise<unknown>) => void pending.push(p) };
    const r2 = await handleGate(req({ fields: { email: "two@example.com", "cf-turnstile-response": "ok2" } }), baseEnv(), {
      fetchFn: fake().fn,
      nowMs: T0,
      notify: bad,
      ctx,
    });
    await Promise.all(pending.splice(0));
    expect(r2.status).toBe(303);
    expect(r2.headers.get("set-cookie")).not.toBeNull();
  });

  it("ignores client-supplied lead fields: extra fields are refused, country comes from the edge header", async () => {
    const { res } = await post({ fields: { email: EMAIL, "cf-turnstile-response": "ok", verified: "1", source: "email", country: "XX", lid: "zzz" } });
    expect(res.headers.get("location")).toBe("/kit/retry/");
    expect(await count("leads")).toBe(0);
  });

  it("a returning address refreshes one lead row, never a second", async () => {
    await post({ fields: { email: EMAIL, "cf-turnstile-response": "ok" } });
    // A fresh siteverify token for the same address, spelled differently.
    const again = await post({ fields: { email: "PAT@Example.com", "cf-turnstile-response": "ok2" } });
    expect(again.res.headers.get("set-cookie")).not.toBeNull();
    expect(await count("leads")).toBe(1);
  });

  it("token claims: lead id, ttl from TOKEN_TTL_SECONDS, country", async () => {
    const { res } = await post({}, baseEnv({ TOKEN_TTL_SECONDS: "7200" }));
    expect(setCookie(res)).toContain("Max-Age=7200");
    const lead = await testEnv.LEADS_DB.prepare("SELECT id FROM leads").first<{ id: string }>();
    const v = await handleGate(req({ path: VIEW_ROUTE, method: "GET", cookie: cookieHeader(res) }), baseEnv(), { nowMs: T0 + 1000 });
    expect(v.status).toBe(200);
    expect(lead!.id).toHaveLength(32);
  });
});

// Every cause answers the SAME thing: 303 /kit/retry/, no body, no Set-Cookie, no lead.
const FAILURES: { name: string; opts?: Opts; env?: () => GateEnv; f?: () => Fake }[] = [
  { name: "wrong origin", opts: { headers: { origin: "https://evil.example" } } },
  { name: "missing origin", opts: { headers: { origin: null } } },
  { name: "null origin", opts: { headers: { origin: "null" } } },
  { name: "wrong content type", opts: { headers: { "content-type": "application/json" } } },
  { name: "missing user agent", opts: { headers: { "user-agent": null } } },
  { name: "empty user agent", opts: { headers: { "user-agent": "" } } },
  { name: "tool user agent", opts: { headers: { "user-agent": "curl/8.5.0" } } },
  { name: "crawler user agent", opts: { headers: { "user-agent": "Mozilla/5.0 (compatible; Googlebot/2.1)" } } },
  { name: "oversize user agent", opts: { headers: { "user-agent": "M".repeat(600) } } },
  { name: "oversize declared length", opts: { headers: { "content-length": "999999" } } },
  { name: "oversize body", opts: { fields: `email=${EMAIL}&cf-turnstile-response=ok&website=${"a".repeat(5000)}` } },
  { name: "honeypot filled", opts: { fields: { email: EMAIL, "cf-turnstile-response": "ok", website: "http://spam.example" } } },
  { name: "unknown field", opts: { fields: { email: EMAIL, "cf-turnstile-response": "ok", extra: "1" } } },
  { name: "repeated field", opts: { fields: `email=${EMAIL}&email=${EMAIL}&cf-turnstile-response=ok` } },
  { name: "bad percent escape", opts: { fields: `email=${EMAIL}%zz&cf-turnstile-response=ok` } },
  { name: "invalid email", opts: { fields: { email: "not-an-email", "cf-turnstile-response": "ok" } } },
  { name: "non-ASCII email", opts: { fields: { email: "pät@example.com", "cf-turnstile-response": "ok" } } },
  { name: "missing email", opts: { fields: { "cf-turnstile-response": "ok" } } },
  { name: "missing turnstile token", opts: { fields: { email: EMAIL } } },
  { name: "bad turnstile token", opts: { fields: { email: EMAIL, "cf-turnstile-response": "nope" } } },
  { name: "turnstile hostname mismatch", f: () => fake({ siteverify: "wrong-host" }) },
  { name: "turnstile action mismatch", f: () => fake({ siteverify: "wrong-action" }) },
  { name: "turnstile error code on a success body", f: () => fake({ siteverify: "error-code" }) },
  { name: "turnstile outage (throws)", f: () => fake({ siteverify: "throw" }) },
  { name: "turnstile internal error (500)", f: () => fake({ siteverify: "500" }) },
  { name: "client limiter says no", env: () => baseEnv({ GATE_LIMITER: limiter(false) }) },
  { name: "no client address", opts: { headers: { "cf-connecting-ip": null } } },
  { name: "route budget is zero", env: () => baseEnv({ ROUTE_BUDGET_GATE: "0" }) },
  { name: "invalid budget configuration", env: () => baseEnv({ ROUTE_BUDGET_GATE: "lots" }) },
  { name: "check ceiling says no", env: () => baseEnv({ GATE_CHECK_LIMITER: limiter(false) }) },
  { name: "missing check limiter binding", env: () => baseEnv({ GATE_CHECK_LIMITER: undefined }) },
  { name: "challenge time missing", f: () => fake({ challenge: "missing" }) },
  { name: "challenge time garbage", f: () => fake({ challenge: "garbage" }) },
  { name: "challenge time far in the past", f: () => fake({ challenge: T0 - 3_600_000 }) },
  { name: "challenge time far in the future", f: () => fake({ challenge: T0 + 3_600_000 }) },
  { name: "signing key too short", env: () => baseEnv({ TOKEN_HMAC_KEY: "k".repeat(31) }) },
  { name: "missing Turnstile secret", env: () => baseEnv({ TURNSTILE_SECRET: "" }) },
  { name: "missing KIT binding", env: () => baseEnv({ KIT: undefined }) },
  { name: "missing limiter binding", env: () => baseEnv({ GATE_LIMITER: undefined }) },
  { name: "missing VIEW_HASH_SECRET", env: () => baseEnv({ VIEW_HASH_SECRET: undefined }) },
  { name: "missing LEADS_DB binding", env: () => baseEnv({ LEADS_DB: undefined }) },
  { name: "LEADS_DB is not a database", env: () => baseEnv({ LEADS_DB: "reach-around-leads" }) },
  { name: "LEADS_DB is the analytics database", env: () => baseEnv({ LEADS_DB: testEnv.DB }) },
];

/**
 * Counts separation proofs: every proof prepares the identity read once on LEADS_DB, so the number of
 * those prepares is the number of proofs a request paid for (each proof is at most SEPARATION_QUERIES
 * D1 queries).
 */
function proofCounter(): { env: Partial<GateEnv>; proofs: () => number } {
  let n = 0;
  const leads = new Proxy(testEnv.LEADS_DB, {
    get(t, key) {
      if (key === "prepare") {
        return (q: string) => {
          if (q.includes("FROM db_identity")) n++;
          return t.prepare(q);
        };
      }
      const v = Reflect.get(t, key, t);
      return typeof v === "function" ? v.bind(t) : v;
    },
  });
  return { env: { LEADS_DB: leads } as unknown as Partial<GateEnv>, proofs: () => n };
}

describe("D1 query bound of the separation proof on the public routes (issue #772, round 3)", () => {
  it("a new lead through the gate pays for at most 2 proofs, and only after the limiters", async () => {
    const c = proofCounter();
    const { res } = await post({}, baseEnv(c.env));
    expect(res.headers.get("set-cookie")).not.toBeNull();
    expect(c.proofs()).toBe(2); // after the limiters; right before the lead write
  });

  it("a returning lead through the gate pays for the early proof and the write proof", async () => {
    const id = await seedLead({ id: "c".repeat(32) });
    await testEnv.LEADS_DB.prepare("UPDATE leads SET email = ? WHERE id = ?").bind(EMAIL, id).run();
    const c = proofCounter();
    const { res } = await post({}, baseEnv(c.env));
    expect(res.headers.get("set-cookie")).not.toBeNull();
    expect(c.proofs()).toBe(2); // after the limiters, and right before the refresh write
  });

  it("a gate request stopped by a limiter or a bad form pays for no proof", async () => {
    const c = proofCounter();
    await post({}, baseEnv({ ...c.env, GATE_LIMITER: limiter(false) }));
    expect(c.proofs()).toBe(0);
    const bad = await post({ fields: "not a form" }, baseEnv(c.env));
    expect(bad.res.status).toBe(303);
    expect(c.proofs()).toBe(0);
  });

  it("the view route pays for 1 proof, right before the lead read, and 0 for an unstored token", async () => {
    await seedLead();
    const c = proofCounter();
    const ok = await view({ cookie: await unlockCookieFor() }, baseEnv(c.env));
    expect(ok.status).toBe(200);
    expect(c.proofs()).toBe(1);
    const none = proofCounter();
    const unstored = await view({ cookie: await unlockCookieFor({ lid: await mintUnstoredId(KEY) }) }, baseEnv(none.env));
    expect(unstored.status).toBe(200);
    expect(none.proofs()).toBe(0);
  });

  it("the view route stopped by its limiter makes no proof", async () => {
    await seedLead();
    const c = proofCounter();
    const res = await view({ cookie: await unlockCookieFor() }, baseEnv({ ...c.env, KIT_VIEW_LIMITER: limiter(false) }));
    expect(res.status).toBe(303);
    expect(c.proofs()).toBe(0);
  });
});

describe("the leads database is separate (issue #772)", () => {
  const analyticsLeads = async () =>
    (await testEnv.DB.prepare("SELECT (SELECT COUNT(*) FROM leads) + (SELECT COUNT(*) FROM kit_events) AS n").first<{ n: number }>())!.n;

  it("a stored lead and its unlock event are in LEADS_DB and nothing about them reaches the analytics database", async () => {
    const { res } = await post();
    expect(res.headers.get("set-cookie")).not.toBeNull();
    expect(await count("leads")).toBe(1);
    expect(await count("kit_events")).toBe(1);
    expect(await analyticsLeads()).toBe(0);
    const analytics = await testEnv.DB.prepare("SELECT COUNT(*) AS n FROM counters WHERE key = 'newlead'").first<{ n: number }>();
    expect(analytics!.n).toBe(1); // the daily new-lead unit is a counter, not lead data
  });

  it.each([
    ["missing", undefined],
    ["the analytics database", testEnv.DB],
  ])("with LEADS_DB %s the gate stores nothing anywhere and answers the usual retry redirect", async (_n, v) => {
    const f = fake();
    const { res } = await post({}, baseEnv({ LEADS_DB: v }), f);
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("/kit/retry/");
    expect(res.headers.get("set-cookie")).toBeNull();
    expect(await analyticsLeads()).toBe(0);
    expect(await count("leads")).toBe(0);
    expect(f.calls.filter((x) => x.url.includes("turnstile"))).toHaveLength(0); // refused before the vendor call
  });

  it("with LEADS_DB missing the view route refuses a cookie that was valid, and reads nothing from the analytics database", async () => {
    const { res } = await post();
    const ck = cookieHeader(res);
    const lead = await testEnv.LEADS_DB.prepare("SELECT id FROM leads").first<{ id: string }>();
    // The same lead row also sits in the analytics database (the wrong place): it must not be read.
    await testEnv.DB.prepare("INSERT INTO leads (id, email, created_ts, last_seen_ts, source) VALUES (?, 'legacy@example.com', 1, 1, 'gate')")
      .bind(lead!.id)
      .run();
    const v = await handleGate(req({ path: VIEW_ROUTE, method: "GET", cookie: ck }), baseEnv({ LEADS_DB: undefined }), { nowMs: T0 + 1000 });
    expect(v.status).toBe(303);
    expect(v.headers.get("location")).toBe("/kit/");
    // A legacy row left in analytics now closes the route on its own (separation.ts), so the row is
    // removed before the cookie is expected to work again (this line and the reset were added with that check).
    await testEnv.DB.prepare("DELETE FROM leads").run();
    resetSeparationCache();
    const ok = await handleGate(req({ path: VIEW_ROUTE, method: "GET", cookie: ck }), baseEnv(), { nowMs: T0 + 1000 });
    expect(ok.status).toBe(200);
    await testEnv.DB.prepare("DELETE FROM leads").run();
  });

  it("a lead row left in the analytics database closes the view route for a cookie that was working", async () => {
    const { res } = await post();
    const ck = cookieHeader(res);
    expect((await view({ cookie: ck })).status).toBe(200);
    await testEnv.DB.prepare("INSERT INTO leads (id, email, created_ts, last_seen_ts, source) VALUES (?, 'legacy@example.com', 1, 1, 'gate')")
      .bind("e".repeat(32))
      .run();
    resetSeparationCache();
    const closed = await view({ cookie: ck });
    expect(closed.status).toBe(303);
    expect(await closed.text()).not.toContain(CANARY);
    await testEnv.DB.prepare("DELETE FROM leads").run();
    resetSeparationCache();
    expect((await view({ cookie: ck })).status).toBe(200);
  });

  // Since the separation proof, a lead row in analytics refuses the route before any lookup; the answer
  // (303) is the same as when the lead is simply not found in LEADS_DB.
  it("a lead that exists only in the analytics database does not unlock the view route", async () => {
    const id = "d".repeat(32);
    await testEnv.DB.prepare("INSERT INTO leads (id, email, created_ts, last_seen_ts, source) VALUES (?, 'legacy@example.com', 1, 1, 'gate')")
      .bind(id)
      .run();
    const res = await view({ cookie: await unlockCookieFor({ lid: id }) });
    expect(res.status).toBe(303);
    await testEnv.DB.prepare("DELETE FROM leads").run();
  });
});

describe("gate routes: refusals, limits and session rules", () => {
  for (const c of FAILURES) {
    it(c.name, async () => {
      const before = await count("leads");
      const f = c.f?.() ?? fake();
      const { res } = await post(c.opts ?? {}, c.env?.() ?? baseEnv(), f);
      expect(res.status).toBe(303);
      expect(res.headers.get("location")).toBe("/kit/retry/");
      expect(res.headers.get("set-cookie")).toBeNull();
      expect(res.headers.get("cache-control")).toBe("private, no-store");
      expect(await res.text()).toBe("");
      expect(await count("leads")).toBe(before);
      expect(await count("kit_events")).toBe(0);
      expect(f.calls.filter((x) => x.url.startsWith("https://discord.com/"))).toHaveLength(0);
    });
  }

  it("failure responses are byte-identical across causes", async () => {
    const shapes = new Set<string>();
    for (const c of FAILURES) {
      const { res } = await post(c.opts ?? {}, c.env?.() ?? baseEnv(), c.f?.() ?? fake());
      if (res.headers.get("location") !== "/kit/retry/") continue;
      shapes.add(JSON.stringify([res.status, [...res.headers].sort(), await res.text()]));
    }
    expect(shapes.size).toBe(1);
  });

  it("a rate limited client never reaches the database or Turnstile", async () => {
    const f = fake();
    const { res } = await post({}, baseEnv({ GATE_LIMITER: limiter(false) }), f);
    expect(res.status).toBe(303);
    expect(f.calls).toHaveLength(0);
    expect(await count("counters")).toBe(0);
  });

  it("a wrong content type or an oversize declared length is refused before any limiter is spent (#927)", async () => {
    const cases: Record<string, string>[] = [{ "content-type": "text/plain" }, { "content-length": "999999" }];
    for (const headers of cases) {
      const client = limiter();
      const net = limiter();
      const { res } = await post({ headers }, baseEnv({ GATE_LIMITER: client, GATE_CHECK_LIMITER: net }));
      expect(res.headers.get("location")).toBe("/kit/retry/");
      expect(client.keys).toHaveLength(0);
      expect(net.keys).toHaveLength(0);
      expect(await count("counters")).toBe(0);
    }
  });

  it("the limiter key is a keyed hash, never the address", async () => {
    const lim = limiter();
    await post({}, baseEnv({ GATE_LIMITER: lim }));
    expect(lim.keys).toHaveLength(1);
    expect(lim.keys[0]).not.toContain("203.0.113.9");
    expect(lim.keys[0]).toMatch(/^[0-9a-f]{32}$/);
  });

  it("a replayed Turnstile token is refused even when the vendor still accepts it", async () => {
    const first = await post();
    expect(first.res.headers.get("set-cookie")).not.toBeNull();
    const second = await post({ fields: { email: "other@example.com", "cf-turnstile-response": "ok" } });
    expect(second.res.headers.get("location")).toBe("/kit/retry/");
    expect(second.res.headers.get("set-cookie")).toBeNull();
    expect(await count("leads")).toBe(1);
  });

  // Two budgets: a global ceiling before Turnstile (binding, no D1 write) and the small business budget
  // charged only after the body parsed and Turnstile passed.
  const fromIp = (n: number) => ({ "cf-connecting-ip": `203.0.113.${n}` });
  const junk = (i: number): Opts => ({ headers: fromIp(20 + i), fields: { email: `j${i}@example.com`, "cf-turnstile-response": "bad" + i } });

  it("solved challenges over a spent hourly budget write no counter rows", async () => {
    const e = baseEnv({ ROUTE_BUDGET_GATE: "1" });
    const first = await post({ headers: fromIp(50), fields: { email: "a@example.com", "cf-turnstile-response": "ok-a" } }, e);
    expect(first.res.headers.get("set-cookie")).not.toBeNull(); // spends the whole budget
    const before = await count("counters");
    for (let i = 0; i < 5; i++) {
      const { res } = await post({ headers: fromIp(60 + i), fields: { email: `b${i}@example.com`, "cf-turnstile-response": `ok-b${i}` } }, e);
      expect(res.headers.get("location")).toBe("/kit/retry/");
      expect(res.headers.get("set-cookie")).toBeNull();
    }
    expect(await count("counters")).toBe(before); // no claim rows, no budget change
    const claims = await testEnv.DB.prepare("SELECT count(*) AS n FROM counters WHERE key LIKE 'ts-%'").first<{ n: number }>();
    expect(claims!.n).toBe(1);
  });

  const claimRows = async () =>
    (await testEnv.DB.prepare("SELECT count(*) AS n FROM counters WHERE key LIKE 'ts-%'").first<{ n: number }>())!.n;
  const gateSpent = async () =>
    (await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'route-gate'").first<{ count: number }>())?.count ?? 0;

  it("concurrent solved challenges cannot write more claims than the hourly budget", async () => {
    const e = baseEnv({ ROUTE_BUDGET_GATE: "1" });
    const results = await Promise.all(
      Array.from({ length: 8 }, (_, i) =>
        post({ headers: fromIp(100 + i * 2), fields: { email: `r${i}@example.com`, "cf-turnstile-response": `ok-r${i}` } }, e),
      ),
    );
    expect(results.filter((r) => r.res.headers.get("set-cookie") !== null).length).toBe(1);
    expect(await claimRows()).toBe(1);
    expect(await gateSpent()).toBe(1);
  });

  it("concurrent solved challenges under a larger budget write exactly one claim and one unit each", async () => {
    const e = baseEnv({ ROUTE_BUDGET_GATE: "3" });
    const results = await Promise.all(
      Array.from({ length: 8 }, (_, i) =>
        post({ headers: fromIp(130 + i * 2), fields: { email: `q${i}@example.com`, "cf-turnstile-response": `ok-q${i}` } }, e),
      ),
    );
    expect(results.filter((r) => r.res.headers.get("set-cookie") !== null).length).toBe(3);
    expect(await claimRows()).toBe(3);
    expect(await gateSpent()).toBe(3);
  });

  it("a replayed token does not spend the route budget, sequentially or concurrently", async () => {
    const e = baseEnv({ ROUTE_BUDGET_GATE: "5" });
    const same = (i: number): Opts => ({ headers: fromIp(170 + i * 2), fields: { email: `p${i}@example.com`, "cf-turnstile-response": "ok-same" } });
    expect((await post(same(0), e)).res.headers.get("set-cookie")).not.toBeNull();
    expect(await gateSpent()).toBe(1);
    for (let i = 1; i < 4; i++) expect((await post(same(i), e)).res.headers.get("set-cookie")).toBeNull();
    expect(await gateSpent()).toBe(1);
    const burst = await Promise.all([4, 5, 6, 7].map((i) => post({ ...same(i), fields: { email: `p${i}@example.com`, "cf-turnstile-response": "ok-same2" } }, e)));
    expect(burst.filter((r) => r.res.headers.get("set-cookie") !== null).length).toBe(1);
    expect(await claimRows()).toBe(2);
    expect(await gateSpent()).toBe(2);
  });

  it("under the budget each solved challenge still writes its claim and unlocks", async () => {
    const e = baseEnv({ ROUTE_BUDGET_GATE: "3" });
    for (let i = 0; i < 3; i++) {
      const { res } = await post({ headers: fromIp(70 + i), fields: { email: `c${i}@example.com`, "cf-turnstile-response": `ok-c${i}` } }, e);
      expect(res.headers.get("set-cookie")).not.toBeNull();
    }
    const claims = await testEnv.DB.prepare("SELECT count(*) AS n FROM counters WHERE key LIKE 'ts-%'").first<{ n: number }>();
    expect(claims!.n).toBe(3);
    const row = await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'route-gate'").first<{ count: number }>();
    expect(row!.count).toBe(3);
  });

  it("junk posts from many addresses cannot spend the business budget; a real visitor still unlocks", async () => {
    const e = baseEnv({ ROUTE_BUDGET_GATE: "2" });
    for (let i = 0; i < 8; i++) {
      const { res } = await post(junk(i), e);
      expect(res.headers.get("set-cookie")).toBeNull();
    }
    const spent = await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'route-gate'").first<{ count: number }>();
    expect(spent).toBeNull();
    const real = await post({ headers: fromIp(99), fields: { email: "real@example.com", "cf-turnstile-response": "ok1" } }, e);
    expect(real.res.headers.get("set-cookie")).not.toBeNull();
    const row = await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'route-gate'").first<{ count: number }>();
    expect(row!.count).toBe(1);
  });

  it("verified visitors do spend the business budget, and over it they are refused", async () => {
    const e = baseEnv({ ROUTE_BUDGET_GATE: "2" });
    const ok = (i: number): Opts => ({ headers: fromIp(40 + i), fields: { email: `v${i}@example.com`, "cf-turnstile-response": "ok" + i } });
    expect((await post(ok(1), e)).res.headers.get("set-cookie")).not.toBeNull();
    expect((await post(ok(2), e)).res.headers.get("set-cookie")).not.toBeNull();
    const third = await post(ok(3), e);
    expect(third.res.headers.get("location")).toBe("/kit/retry/");
    expect(third.res.headers.get("set-cookie")).toBeNull();
    expect(await count("leads")).toBe(2);
  });

  // The pre-Turnstile allowance is partitioned by a keyed hash of the coarse network (a /24 for IPv4, a
  // /48 for IPv6), so one network can use up only its own share.
  const vendorCalls = (f: Fake) => f.calls.filter((c) => c.url.includes("siteverify")).length;

  it("one network cannot starve another: each network has its own allowance before Turnstile", async () => {
    const lim = keyedLimiter(3);
    const f = fake();
    const e = baseEnv({ GATE_CHECK_LIMITER: lim });
    // Network A: ten different hosts of one /24, all well-formed, none with a valid token.
    for (let i = 0; i < 10; i++) await post({ headers: { "cf-connecting-ip": `203.0.113.${100 + i}` }, fields: { email: `a${i}@example.com`, "cf-turnstile-response": "bad" + i } }, e, f);
    expect(vendorCalls(f)).toBe(3);
    // Network B (another /24) is untouched and unlocks.
    const real = await post({ headers: { "cf-connecting-ip": "198.51.100.7" }, fields: { email: "real@example.com", "cf-turnstile-response": "ok1" } }, e, f);
    expect(real.res.headers.get("set-cookie")).not.toBeNull();
    expect(await count("counters")).toBeGreaterThan(0); // only the verified visitor wrote rows
    expect(lim.hits.size).toBe(2); // one key per network, never one shared key
  });

  it("many distinct networks together cannot starve any one visitor", async () => {
    const lim = keyedLimiter(1);
    const e = baseEnv({ GATE_CHECK_LIMITER: lim });
    for (let i = 0; i < 40; i++) await post({ headers: { "cf-connecting-ip": `10.${i}.0.1` }, fields: { email: `m${i}@example.com`, "cf-turnstile-response": "bad" + i } }, e);
    const real = await post({ headers: { "cf-connecting-ip": "198.51.100.7" }, fields: { email: "real@example.com", "cf-turnstile-response": "ok1" } }, e);
    expect(real.res.headers.get("set-cookie")).not.toBeNull();
  });

  it("the network key groups a /24 and a /48, hides the address, and differs by network", async () => {
    const lim = limiter();
    const e = baseEnv({ GATE_CHECK_LIMITER: lim });
    const ips = ["203.0.113.1", "203.0.113.200", "203.0.114.1", "2001:db8:abcd:1::1", "2001:db8:abcd:ffff::9", "2001:db8:abce::1"];
    for (const [i, ip] of ips.entries()) {
      await post({ headers: { "cf-connecting-ip": ip }, fields: { email: `k${i}@example.com`, "cf-turnstile-response": "bad" + i } }, e);
    }
    const [a1, a2, b, c1, c2, d] = lim.keys;
    expect(a1).toBe(a2);
    expect(a1).not.toBe(b);
    expect(c1).toBe(c2);
    expect(c1).not.toBe(d);
    expect(a1).not.toBe(c1);
    for (const k of lim.keys) expect(k).toMatch(/^[0-9a-f]{32}$/);
  });

  it("the network key is not the per-client key (distinct domain labels)", async () => {
    const client = limiter();
    const net = limiter();
    await post({}, baseEnv({ GATE_LIMITER: client, GATE_CHECK_LIMITER: net }));
    expect(client.keys[0]).not.toBe(net.keys[0]);
  });

  // The per-location busy signal is a monitoring hint only: it never refuses, it raises one closed health word a day.
  describe("per-location busy signal", () => {
    it("never refuses a visitor, and tells the owner once a day in closed words", async () => {
      const f = fake();
      const e = baseEnv({ GATE_WATCH_LIMITER: limiter(false) });
      const one = await post({ fields: { email: "w1@example.com", "cf-turnstile-response": "ok1" } }, e, f);
      const two = await post({ headers: { "cf-connecting-ip": "198.51.100.9" }, fields: { email: "w2@example.com", "cf-turnstile-response": "ok2" } }, e, f);
      expect(one.res.headers.get("set-cookie")).not.toBeNull();
      expect(two.res.headers.get("set-cookie")).not.toBeNull();
      const busy = f.calls.filter((c) => c.url.startsWith("https://discord.com/") && c.body.includes("far more requests"));
      expect(busy).toHaveLength(1);
      expect(busy[0]!.body).not.toContain("example.com");
    });

    it("a quiet signal raises nothing, and a missing binding is fine", async () => {
      const f = fake();
      await post({}, baseEnv({ GATE_WATCH_LIMITER: limiter(true) }), f);
      await post({ fields: { email: "w3@example.com", "cf-turnstile-response": "ok3" } }, baseEnv(), f);
      expect(f.calls.filter((c) => c.body.includes("far more requests"))).toHaveLength(0);
    });

    it("an unavailable sink releases the day's claim, so a later request retries", async () => {
      const e = baseEnv({ GATE_WATCH_LIMITER: limiter(false) });
      const down = fake({ discord: 500 });
      await post({ fields: { email: "w4@example.com", "cf-turnstile-response": "ok4" } }, e, down);
      expect(await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'gatebusy-alert'").first()).toBeNull();
      // The dispatcher's own 10 minute dedup window (a failed send still holds it) must pass first.
      const later = T0 + 11 * 60_000;
      const up = fake({ challenge: later });
      await post({ headers: { "cf-connecting-ip": "198.51.100.9" }, fields: { email: "w5@example.com", "cf-turnstile-response": "ok5" } }, e, up, later);
      expect(up.calls.filter((c) => c.body.includes("far more requests"))).toHaveLength(1);
      await post({ headers: { "cf-connecting-ip": "198.51.100.10" }, fields: { email: "w6@example.com", "cf-turnstile-response": "ok6" } }, e, up, later);
      expect(up.calls.filter((c) => c.body.includes("far more requests"))).toHaveLength(1);
    });
  });

  it("a request refused before Turnstile (bad body) does not use the check ceiling", async () => {
    const ceiling = limiter();
    await post({ fields: { email: "not-an-email", "cf-turnstile-response": "ok" } }, baseEnv({ GATE_CHECK_LIMITER: ceiling }));
    await post({ fields: { email: EMAIL } }, baseEnv({ GATE_CHECK_LIMITER: ceiling }));
    expect(ceiling.keys).toHaveLength(0);
  });

  it("a browser-shaped submission (all three fields, honeypot empty) reaches the unlock path", async () => {
    // The built form posts email and an empty website field; the Turnstile script adds cf-turnstile-response.
    const body = `email=${encodeURIComponent(EMAIL)}&website=&cf-turnstile-response=ok`;
    const { res } = await post({ fields: body });
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("/k/view");
    expect(res.headers.get("set-cookie")).not.toBeNull();
    expect(await count("leads")).toBe(1);
  });

  // Design (plan 3, Task 15): a session floor only invalidates cookies issued before it. A fresh
  // gate pass for the same lead issues a cookie that works. There is no blocked flag in the schema.
  // The floor is in milliseconds. Fail closed: only a cookie issued in a whole second strictly after
  // the floor's second works, so a cookie from the floor's own second is refused.
  const S = NOW_SEC - 60;
  const FLOORS: { name: string; floorMs: number | null; iat: number; status: number }[] = [
    { name: "no floor", floorMs: null, iat: S, status: 200 },
    { name: "floor well before the cookie", floorMs: (S - 60) * 1000, iat: S, status: 200 },
    { name: "floor after the cookie second", floorMs: (S + 1) * 1000, iat: S, status: 303 },
    { name: "floor later in the cookie's own second", floorMs: S * 1000 + 500, iat: S, status: 303 },
    { name: "floor at the start of the cookie's own second", floorMs: S * 1000, iat: S, status: 303 },
    { name: "cookie from the next second after the floor", floorMs: S * 1000 + 500, iat: S + 1, status: 200 },
  ];
  for (const c of FLOORS) {
    it(`view with ${c.name}: ${c.status}`, async () => {
      const id = await seedLead({ id: "c".repeat(32), floor: c.floorMs });
      const res = await view({ cookie: await unlockCookieFor({ lid: id, iat: c.iat }) });
      expect(res.status).toBe(c.status);
      if (c.status === 303) expect(await res.text()).not.toContain(CANARY);
    });
  }

  it("an existing lead that passes the gate again after its floor gets a working new cookie", async () => {
    const id = await seedLead({ id: "c".repeat(32), floor: T0 - 5000 });
    await testEnv.LEADS_DB.prepare("UPDATE leads SET email = ? WHERE id = ?").bind(EMAIL, id).run();
    const { res } = await post();
    expect(res.headers.get("set-cookie")).not.toBeNull();
    expect((await view({ cookie: cookieHeader(res) })).status).toBe(200);
  });

  it("kill switch answers 503 before anything else, for every method and route", async () => {
    for (const [method, path] of [["POST", GATE_ROUTE], ["GET", GATE_ROUTE], ["GET", VIEW_ROUTE]] as const) {
      const f = fake();
      const res = await handleGate(req({ method, path, headers: { origin: "https://evil.example" } }), baseEnv({ KIT_KILL: "1" }), { fetchFn: f.fn });
      expect(res.status).toBe(503);
      expect(f.calls).toHaveLength(0);
    }
  });

  it("GET /k/gate and other methods are 404 with no body", async () => {
    for (const method of ["GET", "PUT", "DELETE", "HEAD"]) {
      const res = await handleGate(req({ method }), baseEnv(), {});
      expect(res.status).toBe(404);
      expect(await res.text()).toBe("");
    }
  });
});

async function unlockCookieFor(over: { lid?: string; iat?: number; exp?: number; key?: string } = {}): Promise<string> {
  const iat = over.iat ?? NOW_SEC - 60;
  return `${COOKIE_NAME}=${await signToken(over.key ?? KEY, { lid: over.lid ?? "a".repeat(32), iat, exp: over.exp ?? iat + 3600, cc: "" })}`;
}
async function seedLead(over: { id?: string; floor?: number | null } = {}): Promise<string> {
  const id = over.id ?? "a".repeat(32);
  await testEnv.LEADS_DB.prepare("INSERT INTO leads (id, email, created_ts, last_seen_ts, source, session_floor_ts) VALUES (?, ?, 1, 1, 'gate', ?)")
    .bind(id, "seed@example.com", over.floor ?? null)
    .run();
  return id;
}
async function view(o: Opts = {}, e: GateEnv = baseEnv()) {
  return handleGate(req({ method: "GET", path: VIEW_ROUTE, ...o }), e, { nowMs: T0 });
}

describe("GET /k/view (gated content)", () => {
  it("serves the page to a valid cookie with no-store, noindex, no-referrer and a locked-down CSP", async () => {
    await seedLead();
    const res = await view({ cookie: await unlockCookieFor() });
    expect(res.status).toBe(200);
    expect(await res.text()).toContain(CANARY);
    expect(res.headers.get("cache-control")).toBe("private, no-store");
    expect(res.headers.get("x-robots-tag")).toBe("noindex");
    expect(res.headers.get("referrer-policy")).toBe("no-referrer");
    expect(res.headers.get("content-security-policy")).toContain("default-src 'none'");
    expect(res.headers.get("content-type")).toContain("text/html");
  });

  const BAD_COOKIES: { name: string; cookie: () => Promise<string | null> }[] = [
    { name: "no cookie", cookie: async () => null },
    { name: "garbage", cookie: async () => `${COOKIE_NAME}=v1.abc.def` },
    { name: "forged (wrong key)", cookie: () => unlockCookieFor({ key: "z".repeat(40) }) },
    { name: "expired", cookie: () => unlockCookieFor({ iat: NOW_SEC - 7200, exp: NOW_SEC - 3600 }) },
    { name: "duplicated", cookie: async () => { const c = await unlockCookieFor(); return `${c}; ${c}`; } },
    { name: "wrong cookie name (no __Host- prefix)", cookie: async () => (await unlockCookieFor()).replace("__Host-", "") },
    { name: "lead that does not exist", cookie: () => unlockCookieFor({ lid: "b".repeat(32) }) },
  ];
  for (const c of BAD_COOKIES) {
    it(`refuses ${c.name} with a redirect and none of the page`, async () => {
      await seedLead();
      const kvp = kv();
      const res = await view({ cookie: await c.cookie() }, baseEnv({ KIT: kvp }));
      expect(res.status).toBe(303);
      expect(res.headers.get("location")).toBe("/kit/");
      expect(await res.text()).not.toContain(CANARY);
      expect(res.headers.get("cache-control")).toBe("private, no-store");
      expect(kvp.get).not.toHaveBeenCalled();
    });
  }

  it("a cookie issued before a session revoke is refused", async () => {
    await seedLead({ floor: (NOW_SEC - 30) * 1000 });
    const res = await view({ cookie: await unlockCookieFor({ iat: NOW_SEC - 60 }) });
    expect(res.status).toBe(303);
  });

  it("a cookie for a lead deleted after issue is refused", async () => {
    const id = await seedLead();
    const ck = await unlockCookieFor({ lid: id });
    await testEnv.LEADS_DB.prepare("DELETE FROM leads WHERE id = ?").bind(id).run();
    expect((await view({ cookie: ck })).status).toBe(303);
  });

  it("ignores a valid token in the query string, a header or a form body", async () => {
    await seedLead();
    const ck = await unlockCookieFor();
    const token = ck.split("=")[1]!;
    const r1 = await handleGate(new Request(`${ORIGIN}${VIEW_ROUTE}?t=${token}`), baseEnv(), { nowMs: T0 });
    const r2 = await handleGate(new Request(`${ORIGIN}${VIEW_ROUTE}`, { headers: { authorization: `Bearer ${token}`, "x-token": token } }), baseEnv(), { nowMs: T0 });
    for (const r of [r1, r2]) {
      expect(r.status).toBe(303);
      expect(await r.text()).not.toContain(CANARY);
    }
  });

  // A missing or failing page answers exactly like every other refusal, so an outage cannot tell a
  // visitor whether a cookie was valid. Only the closed-vocabulary log tells the cause.
  it("an empty KV key gets the same fixed redirect as an invalid cookie", async () => {
    await seedLead();
    const missing = await view({ cookie: await unlockCookieFor() }, baseEnv({ KIT: kv(null) }));
    const invalid = await view({ cookie: null });
    expect(missing.status).toBe(303);
    expect(missing.headers.get("location")).toBe("/kit/");
    expect(await shape(missing)).toBe(await shape(invalid));
  });

  it("a failing KV read gets the same fixed redirect, and the log names the class only", async () => {
    await seedLead();
    const spy = vi.spyOn(console, "log").mockImplementation(() => undefined);
    // The shared logger caps lines per minute; move the clock past the window so this line is not dropped.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(Date.now() + 10 * 60_000);
    try {
      const broken = { get: vi.fn(async () => { throw new Error("kv down"); }) };
      const res = await view({ cookie: await unlockCookieFor() }, baseEnv({ KIT: broken }));
      expect(res.status).toBe(303);
      expect(await shape(res)).toBe(await shape(await view({ cookie: null })));
      const lines = spy.mock.calls.flat().map(String);
      expect(lines.some((l) => l.includes('"r":"provider"'))).toBe(true);
      expect(lines.join("\n")).not.toContain("kv down");
    } finally {
      vi.useRealTimers();
      spy.mockRestore();
    }
  });

  it("the view budget refuses when spent, and a refusal still gives the same redirect", async () => {
    await seedLead();
    const res = await view({ cookie: await unlockCookieFor() }, baseEnv({ ROUTE_BUDGET_VIEW: "0" }));
    expect(res.status).toBe(303);
    expect(await res.text()).not.toContain(CANARY);
  });

  it("the canary appears only from GET or HEAD /k/view with a cookie", async () => {
    await seedLead();
    const ck = await unlockCookieFor();
    const probes: [string, string][] = [["POST", VIEW_ROUTE], ["GET", "/k/content"], ["GET", "/k/kit/v1"], ["GET", "/k/view/"], ["GET", "/kit/v1"]];
    for (const [method, path] of probes) {
      const res = await handleGate(req({ method, path, cookie: ck }), baseEnv(), { nowMs: T0 });
      expect(await res.text()).not.toContain(CANARY);
    }
    const head = await view({ cookie: null, method: "HEAD" });
    expect(head.status).toBe(303);
  });

  it("the unlock cookie is scoped __Host-: Secure, Path=/, HttpOnly, no Domain", async () => {
    const { res } = await post();
    const c = setCookie(res)!;
    expect(c.startsWith("__Host-")).toBe(true);
    for (const attr of ["Secure", "Path=/", "HttpOnly", "SameSite=Lax"]) expect(c).toContain(attr);
    expect(c).not.toMatch(/Domain/i);
  });
});

// Per-key quota, like the real binding: each client key has its own allowance.
function keyedLimiter(perKey: number): Limiter & { hits: Map<string, number> } {
  const hits = new Map<string, number>();
  return {
    hits,
    keys: [],
    async limit(o) {
      const n = (hits.get(o.key) ?? 0) + 1;
      hits.set(o.key, n);
      return { success: n <= perKey };
    },
  };
}

describe("early refusals write nothing", () => {
  const EARLY: { name: string; opts: Opts; env?: () => GateEnv }[] = [
    { name: "wrong origin", opts: { headers: { origin: "https://evil.example" } } },
    { name: "missing origin", opts: { headers: { origin: null } } },
    { name: "wrong content type", opts: { headers: { "content-type": "application/json" } } },
    { name: "missing content type", opts: { headers: { "content-type": null } } },
    { name: "declared size over the cap", opts: { headers: { "content-length": "999999" } } },
    { name: "declared size not a number", opts: { headers: { "content-length": "12abc" } } },
    { name: "no client address", opts: { headers: { "cf-connecting-ip": null } } },
    { name: "missing user agent", opts: { headers: { "user-agent": null } } },
    { name: "tool user agent", opts: { headers: { "user-agent": "python-requests/2.31" } } },
    { name: "limiter says no", opts: {}, env: () => baseEnv({ GATE_LIMITER: limiter(false) }) },
  ];
  for (const c of EARLY) {
    it(`${c.name}: no counters row, no lead, no Turnstile call`, async () => {
      const f = fake();
      const { res } = await post(c.opts, c.env?.() ?? baseEnv(), f);
      expect(res.headers.get("location")).toBe("/kit/retry/");
      expect(await count("counters")).toBe(0);
      expect(await count("leads")).toBe(0);
      expect(f.calls).toHaveLength(0);
    });
  }
});

describe("per-client limits do not spend shared budgets", () => {
  it("one client over its gate allowance cannot spend the gate budget or block another client", async () => {
    const lim = keyedLimiter(1);
    const e = baseEnv({ GATE_LIMITER: lim });
    const a = { headers: { "cf-connecting-ip": "203.0.113.10" } };
    const b = { headers: { "cf-connecting-ip": "203.0.113.11" } };
    for (let i = 0; i < 6; i++) await post({ ...a, fields: { email: `a${i}@example.com`, "cf-turnstile-response": "ok" + i } }, e);
    const spent = await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'route-gate'").first<{ count: number }>();
    expect(spent!.count).toBe(1); // only A's first request got past its limiter
    const other = await post({ ...b, fields: { email: "b@example.com", "cf-turnstile-response": "ok" } }, e);
    expect(other.res.headers.get("set-cookie")).not.toBeNull();
  });

  it("one client over its view allowance cannot spend the view budget or block another client", async () => {
    await seedLead();
    const lim = keyedLimiter(3);
    const e = baseEnv({ KIT_VIEW_LIMITER: lim });
    const ck = await unlockCookieFor();
    const hdr = (ip: string) => ({ cookie: ck, headers: { "cf-connecting-ip": ip } });
    for (let i = 0; i < 3; i++) expect((await view(hdr("203.0.113.10"), e)).status).toBe(200);
    for (let i = 0; i < 5; i++) expect((await view(hdr("203.0.113.10"), e)).status).toBe(303);
    const spent = await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'route-view'").first<{ count: number }>();
    expect(spent!.count).toBe(3);
    expect((await view(hdr("203.0.113.11"), e)).status).toBe(200);
  });

  const viewSpent = async () =>
    (await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'route-view'").first<{ count: number }>())?.count ?? 0;

  it("a cookie below its lead's session floor never changes the shared view counter", async () => {
    const id = await seedLead();
    await setSessionFloor(testEnv.LEADS_DB, id, T0);
    expect((await view({ cookie: await unlockCookieFor({ iat: NOW_SEC - 1 }) })).status).toBe(303);
    expect(await viewSpent()).toBe(0);
  });

  it("a cookie below the unstored session floor never changes the shared view counter", async () => {
    await seedLead();
    const e = baseEnv({ MAX_LEADS: "1" }); // the seeded lead fills the cap, so the next unlock is unstored
    const fresh = await post({ fields: { email: "new@example.com", "cf-turnstile-response": "ok2" } }, e);
    await setUnstoredSessionFloor(testEnv.DB, T0);
    expect((await view({ cookie: cookieHeader(fresh.res) }, e)).status).toBe(303);
    expect(await viewSpent()).toBe(0);
  });

  it("a cookie whose lead was deleted never changes the shared view counter", async () => {
    const id = await seedLead();
    await testEnv.LEADS_DB.prepare("DELETE FROM leads WHERE id = ?").bind(id).run();
    expect((await view({ cookie: await unlockCookieFor({ iat: NOW_SEC + 1 }) })).status).toBe(303);
    expect(await viewSpent()).toBe(0);
  });

  it("a valid cookie still spends exactly one view unit", async () => {
    await seedLead();
    expect((await view({ cookie: await unlockCookieFor({ iat: NOW_SEC + 1 }) })).status).toBe(200);
    expect(await viewSpent()).toBe(1);
  });

  it("a visitor can reload the gated page: the view allowance is higher than the gate's", async () => {
    await seedLead();
    // The documented values: 1 per 60 s per client for the gate, 4 per 60 s for the view
    // (tests/test_deploy_limits_doc.py pins that 4 x 60 stays below ROUTE_BUDGET_VIEW).
    const gateLim = keyedLimiter(1);
    const viewLim = keyedLimiter(4);
    const e = baseEnv({ GATE_LIMITER: gateLim, KIT_VIEW_LIMITER: viewLim });
    const ck = await unlockCookieFor();
    for (let i = 0; i < 4; i++) expect((await view({ cookie: ck }, e)).status).toBe(200); // reloads, back button, a preview HEAD
    expect((await view({ cookie: ck }, e)).status).toBe(303); // the fifth in the same minute is refused
    expect(gateLim.hits.size).toBe(0); // viewing never touches the gate's allowance
  });

  it("the gate and the view keep separate per-client allowances (separate bindings)", async () => {
    await seedLead();
    const gateLim = keyedLimiter(1);
    const viewLim = keyedLimiter(1);
    const e = baseEnv({ GATE_LIMITER: gateLim, KIT_VIEW_LIMITER: viewLim });
    const g = await post({}, e);
    expect(g.res.headers.get("set-cookie")).not.toBeNull();
    expect((await view({ cookie: await unlockCookieFor() }, e)).status).toBe(200);
    expect(gateLim.hits.size).toBe(1);
    expect(viewLim.hits.size).toBe(1);
  });

  it("a missing view limiter binding refuses the view and writes nothing", async () => {
    await seedLead();
    const res = await view({ cookie: await unlockCookieFor() }, baseEnv({ KIT_VIEW_LIMITER: undefined }));
    expect(res.status).toBe(303);
    expect(await count("counters")).toBe(0);
  });

  for (const [name, cookie] of [
    ["a missing cookie", null],
    ["a malformed cookie", `${COOKIE_NAME}=v1.AAAA.BBBB`],
  ] as const) {
    it(`${name} is refused before any view allowance or counter is spent (#927)`, async () => {
      await seedLead();
      const viewLim = keyedLimiter(4);
      const counters = async () => (await testEnv.DB.prepare("SELECT key, day, count FROM counters ORDER BY key, day").all()).results;
      const before = await counters();
      const res = await view({ cookie }, baseEnv({ KIT_VIEW_LIMITER: viewLim }));
      expect(res.status).toBe(303);
      expect(viewLim.hits.size).toBe(0);
      expect(await counters()).toEqual(before);
    });
  }

  it("a view with no usable client address is refused before the budget", async () => {
    await seedLead();
    const res = await view({ cookie: await unlockCookieFor(), headers: { "cf-connecting-ip": null } });
    expect(res.status).toBe(303);
    expect(await count("counters")).toBe(0);
  });
});

/** The response with the cookie's token masked: what two visitors could compare byte for byte. */
async function shape(res: Response): Promise<string> {
  const headers = [...res.headers].map(([k, v]) => [k, k === "set-cookie" ? v.replace(/=[^;]+/, "=<token>") : v]).sort();
  return JSON.stringify([res.status, headers, await res.text()]);
}
function payloadOf(res: Response): { lid: string } {
  const token = setCookie(res)!.split("=")[1]!.split(";")[0]!;
  const b64 = token.split(".")[1]!.replace(/-/g, "+").replace(/_/g, "/");
  return JSON.parse(atob(b64)) as { lid: string };
}

describe("lead cap: the answer does not depend on whether the address is stored", () => {
  async function atCap(): Promise<GateEnv> {
    await testEnv.LEADS_DB.prepare("INSERT INTO leads (id, email, created_ts, last_seen_ts, source) VALUES (?, ?, 1, 1, 'gate')")
      .bind("e".repeat(32), EMAIL)
      .run();
    return baseEnv({ MAX_LEADS: "1" });
  }

  it("known and unknown addresses get a byte-identical response, and only the known one is stored", async () => {
    const e = await atCap();
    const known = await post({}, e);
    const fresh = await post({ fields: { email: "new@example.com", "cf-turnstile-response": "ok2" } }, e);
    expect(await shape(fresh.res)).toBe(await shape(known.res));
    expect(fresh.res.headers.get("location")).toBe("/k/view");
    expect(await count("leads")).toBe(1);
    expect(await count("kit_events")).toBe(1); // only the stored lead has an unlock event
    // the visitor-readable id has the same form either way
    expect(payloadOf(known.res).lid).toMatch(/^[0-9a-f]{32}$/);
    expect(payloadOf(fresh.res).lid).toMatch(/^[0-9a-f]{32}$/);
  });

  it("an unstored unlock opens /k/view like any other", async () => {
    const e = await atCap();
    const fresh = await post({ fields: { email: "new@example.com", "cf-turnstile-response": "ok2" } }, e);
    const res = await view({ cookie: cookieHeader(fresh.res) }, e);
    expect(res.status).toBe(200);
    expect(await res.text()).toContain(CANARY);
  });

  it("the unstored session floor ends earlier unstored cookies and a later unlock works", async () => {
    const e = await atCap();
    const fresh = await post({ fields: { email: "new@example.com", "cf-turnstile-response": "ok2" } }, e);
    await setUnstoredSessionFloor(testEnv.DB, T0);
    expect((await view({ cookie: cookieHeader(fresh.res) }, e)).status).toBe(303);
    const later = await post({ fields: { email: "new@example.com", "cf-turnstile-response": "ok3" } }, e, fake(), T0 + 5000);
    const res = await handleGate(req({ method: "GET", path: VIEW_ROUTE, cookie: cookieHeader(later.res) }), e, { nowMs: T0 + 5000 });
    expect(res.status).toBe(200);
  });

  it("an id that merely looks like a lead id, with no lead row and no valid tag, is refused", async () => {
    const e = await atCap();
    expect((await view({ cookie: await unlockCookieFor({ lid: "f".repeat(32) }) }, e)).status).toBe(303);
  });

  it("the daily new-lead cap behaves the same way: unlocked, not stored, known leads still refreshed", async () => {
    const e = baseEnv();
    const extra = { newLeadsPerDay: 2 };
    const ok = (i: number): Opts => ({ headers: { "cf-connecting-ip": `203.0.113.${60 + i}` }, fields: { email: `n${i}@example.com`, "cf-turnstile-response": "ok" + i } });
    const a = await post(ok(1), e, fake(), T0, extra);
    const b = await post(ok(2), e, fake(), T0, extra);
    const c = await post(ok(3), e, fake(), T0, extra);
    expect(await count("leads")).toBe(2);
    expect(await shape(c.res)).toBe(await shape(a.res));
    expect(await shape(b.res)).toBe(await shape(a.res));
    // a known address is refreshed even after the daily cap, and the next day stores again
    const again = await post({ ...ok(1), fields: { email: "n1@example.com", "cf-turnstile-response": "ok4" } }, e, fake(), T0, extra);
    expect(again.res.headers.get("set-cookie")).not.toBeNull();
    expect(await count("leads")).toBe(2);
    const nextDay = T0 + 86_400_000;
    await post(ok(5), e, fake({ challenge: nextDay }), nextDay, extra);
    expect(await count("leads")).toBe(3);
  });

  it("tells the owner once a day, in closed words, that the cap was reached; the address is not stored", async () => {
    const e = await atCap();
    const f = fake();
    await post({ headers: { "cf-connecting-ip": "203.0.113.70" }, fields: { email: "x1@example.com", "cf-turnstile-response": "ok7" } }, e, f);
    await post({ headers: { "cf-connecting-ip": "203.0.113.71" }, fields: { email: "x2@example.com", "cf-turnstile-response": "ok8" } }, e, f);
    const hooks = f.calls.filter((c) => c.url.startsWith("https://discord.com/"));
    const capped = hooks.filter((c) => c.body.includes("lead limit"));
    expect(capped).toHaveLength(1);
    expect(capped[0]!.body).not.toContain("x1@");
    expect(await count("leads")).toBe(1);
  });

  it("an unavailable sink does not use up the day's cap alert: it is released and a later unlock retries", async () => {
    const e = await atCap();
    const unstored = (n: number, f: Fake, at = T0) =>
      post({ headers: { "cf-connecting-ip": `203.0.113.${80 + n}` }, fields: { email: `u${n}@example.com`, "cf-turnstile-response": "ok" + (10 + n) } }, e, f, at);
    const capped = (f: Fake) => f.calls.filter((c) => c.url.startsWith("https://discord.com/") && c.body.includes("lead limit"));
    const down = fake({ discord: 500 });
    await unstored(1, down);
    expect(capped(down).length).toBeGreaterThan(0); // it tried
    expect(await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'leadcap-alert'").first()).toBeNull(); // and let go of the claim
    // After the dispatcher's own 10 minute dedup window (a failed send still holds it).
    const later = T0 + 11 * 60_000;
    const up = fake({ challenge: later });
    await unstored(2, up, later);
    expect(capped(up)).toHaveLength(1);
    await unstored(3, up, later);
    expect(capped(up)).toHaveLength(1); // delivered once: now taken for the day
  });

  // Admission and creation: the daily unit is given back whenever no row was created, so the counter
  // equals the rows created, whatever the interleaving.
  it("concurrent first-time posts for one address take one daily unit and create one row", async () => {
    const e = baseEnv();
    const run = (i: number) =>
      handleGate(req({ headers: { "cf-connecting-ip": `203.0.113.${100 + i}` }, fields: { email: "same@example.com", "cf-turnstile-response": "ok" + (20 + i) } }), e, {
        fetchFn: fake().fn,
        nowMs: T0,
        notify: createDispatcher().notify,
        newLeadsPerDay: 3,
      });
    const results = await Promise.all([0, 1, 2, 3, 4, 5].map(run));
    expect(results.every((r) => r.headers.get("set-cookie") !== null)).toBe(true);
    expect(await count("leads")).toBe(1);
    const row = await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'newlead'").first<{ count: number }>();
    expect(row!.count).toBe(1);
  });

  it("concurrent posts for distinct addresses never store more than the daily cap, and the counter equals the rows", async () => {
    const e = baseEnv();
    const run = (i: number) =>
      handleGate(req({ headers: { "cf-connecting-ip": `203.0.113.${120 + i}` }, fields: { email: `d${i}@example.com`, "cf-turnstile-response": "ok" + (40 + i) } }), e, {
        fetchFn: fake().fn,
        nowMs: T0,
        notify: createDispatcher().notify,
        newLeadsPerDay: 3,
      });
    const results = await Promise.all([0, 1, 2, 3, 4, 5, 6, 7].map(run));
    expect(results.every((r) => r.headers.get("set-cookie") !== null)).toBe(true);
    expect(await count("leads")).toBe(3);
    const row = await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'newlead'").first<{ count: number }>();
    expect(row!.count).toBe(3);
  });

  it("a unit taken for a lead the cap then refuses is given back too", async () => {
    const e = await atCap(); // MAX_LEADS 1, one lead stored
    await post({ fields: { email: "new@example.com", "cf-turnstile-response": "ok2" } }, e, fake(), T0, { newLeadsPerDay: 5 });
    const row = await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'newlead'").first<{ count: number }>();
    expect(row === null || row.count === 0).toBe(true);
  });

  // An ambiguous failure: the database commits the insert and then the call throws. The unit must be
  // KEPT (stricter admission), so the day's counter is never below the rows created.
  it("when the insert commits but the call throws, the daily unit is kept and the cap cannot be exceeded", async () => {
    const real = testEnv.LEADS_DB;
    const faulty = new Proxy(real, {
      get(t, p) {
        if (p === "prepare") {
          return (q: string) => {
            const st = t.prepare(q);
            if (!q.includes("INSERT INTO leads")) return st;
            const wrap = (s: D1PreparedStatement): D1PreparedStatement =>
              new Proxy(s, {
                get(x, k) {
                  if (k === "bind") return (...a: unknown[]) => wrap(x.bind(...a));
                  if (k === "first") {
                    return async (...a: unknown[]) => {
                      await (x as unknown as Record<string, (...b: unknown[]) => Promise<unknown>>).first!(...a); // commits
                      throw new Error("connection lost after commit");
                    };
                  }
                  const v = Reflect.get(x, k, x);
                  return typeof v === "function" ? v.bind(x) : v;
                },
              });
            return wrap(st);
          };
        }
        const v = Reflect.get(t, p, t);
        return typeof v === "function" ? v.bind(t) : v;
      },
    });
    const e = baseEnv({ LEADS_DB: faulty });
    const extra = { newLeadsPerDay: 3 };
    for (let round = 0; round < 2; round++) {
      for (let i = 0; i < 6; i++) {
        await post(
          { headers: { "cf-connecting-ip": `203.0.113.${140 + i}` }, fields: { email: `f${i}@example.com`, "cf-turnstile-response": `ok${60 + round * 10 + i}` } },
          e,
          fake(),
          T0,
          extra,
        );
      }
    }
    const rows = await count("leads");
    const unit = await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'newlead'").first<{ count: number }>();
    expect(rows).toBeGreaterThan(0);
    expect(rows).toBeLessThanOrEqual(3); // never past the daily cap
    expect(unit!.count).toBeGreaterThanOrEqual(rows); // never fewer units than rows
    expect(unit!.count).toBeLessThanOrEqual(3);
  });

  it("a stored lead keeps its behavior: a known address under the cap is stored once and refreshed", async () => {
    const e = baseEnv();
    await post({}, e);
    await post({ fields: { email: EMAIL, "cf-turnstile-response": "ok2" } }, e);
    expect(await count("leads")).toBe(1);
  });
});

describe("Turnstile single use across UTC midnight", () => {
  // The claim bucket is the UTC day of the vendor's challenge time, the same for every answer about one
  // token, so the clock at the moment of the replay does not matter.
  const solved = Date.UTC(2026, 10, 9, 23, 59, 40);
  const before = Date.UTC(2026, 10, 9, 23, 59, 50);
  const after = Date.UTC(2026, 10, 10, 0, 0, 10);

  it("a token passed just before midnight is refused just after it", async () => {
    const one = await post({}, baseEnv(), fake({ challenge: solved }), before);
    expect(one.res.headers.get("set-cookie")).not.toBeNull();
    const two = await post({ fields: { email: "other@example.com", "cf-turnstile-response": "ok" } }, baseEnv(), fake({ challenge: solved }), after);
    expect(two.res.headers.get("location")).toBe("/kit/retry/");
    expect(two.res.headers.get("set-cookie")).toBeNull();
    expect(await count("leads")).toBe(1);
  });

  it("two concurrent requests with one token, one on each side of midnight, unlock exactly one", async () => {
    const run = (email: string, ip: string, at: number) =>
      handleGate(req({ headers: { "cf-connecting-ip": ip }, fields: { email, "cf-turnstile-response": "ok" } }), baseEnv(), {
        fetchFn: fake({ challenge: solved }).fn,
        nowMs: at,
        notify: createDispatcher().notify,
      });
    const results = await Promise.all([
      run("a@example.com", "203.0.113.31", Date.UTC(2026, 10, 9, 23, 59, 59, 900)),
      run("b@example.com", "203.0.113.32", Date.UTC(2026, 10, 10, 0, 0, 0, 100)),
    ]);
    expect(results.filter((r) => r.headers.get("set-cookie") !== null)).toHaveLength(1);
    expect(await count("leads")).toBe(1);
  });

  it("the claim is one row, in the bucket of the challenge day", async () => {
    await post({}, baseEnv(), fake({ challenge: solved }), before);
    const rows = await testEnv.DB.prepare("SELECT day FROM counters WHERE key LIKE 'ts-%'").all<{ day: string }>();
    expect(rows.results.map((r) => r.day)).toEqual(["2026-11-09"]);
  });
});

describe("session floor writer", () => {
  it("setSessionFloor writes milliseconds, and /k/view honors it", async () => {
    const id = await seedLead();
    await setSessionFloor(testEnv.LEADS_DB, id, T0);
    const row = await testEnv.LEADS_DB.prepare("SELECT session_floor_ts AS f FROM leads WHERE id = ?").bind(id).first<{ f: number }>();
    expect(row!.f).toBe(T0);
    expect(row!.f).toBeGreaterThan(1e12); // milliseconds, not seconds
    expect((await view({ cookie: await unlockCookieFor({ iat: NOW_SEC }) })).status).toBe(303);
    expect((await view({ cookie: await unlockCookieFor({ iat: NOW_SEC - 1 }) })).status).toBe(303);
    expect((await view({ cookie: await unlockCookieFor({ iat: NOW_SEC + 1 }) })).status).toBe(200);
  });
  it("refuses a non-finite time", async () => {
    const id = await seedLead();
    await expect(setSessionFloor(testEnv.LEADS_DB, id, Number.NaN)).rejects.toThrow(RangeError);
  });
  it("setSessionFloor only moves forward: an older value never lowers the floor", async () => {
    const id = await seedLead();
    await setSessionFloor(testEnv.LEADS_DB, id, T0);
    await setSessionFloor(testEnv.LEADS_DB, id, T0 - 60_000);
    const row = await testEnv.LEADS_DB.prepare("SELECT session_floor_ts AS f FROM leads WHERE id = ?").bind(id).first<{ f: number }>();
    expect(row!.f).toBe(T0);
    await setSessionFloor(testEnv.LEADS_DB, id, T0 + 5_000); // a newer one still raises it (the guard is not a freeze)
    const up = await testEnv.LEADS_DB.prepare("SELECT session_floor_ts AS f FROM leads WHERE id = ?").bind(id).first<{ f: number }>();
    expect(up!.f).toBe(T0 + 5_000);
  });
  it("setUnstoredSessionFloor only moves forward: an older value never lowers the floor", async () => {
    await setUnstoredSessionFloor(testEnv.DB, T0);
    await setUnstoredSessionFloor(testEnv.DB, T0 - 60_000);
    expect(await getUnstoredSessionFloor(testEnv.DB)).toBe(T0);
    await setUnstoredSessionFloor(testEnv.DB, T0 + 5_000);
    expect(await getUnstoredSessionFloor(testEnv.DB)).toBe(T0 + 5_000);
  });
});

/** A D1 whose statements matching `bad` throw when run: a read error on one table. */
function failingOn(bad: RegExp, base: D1Database = testEnv.DB): D1Database {
  return new Proxy(base, {
    get(t, p) {
      if (p === "prepare") {
        return (q: string) => {
          if (bad.test(q)) throw new Error("database unavailable");
          return t.prepare(q);
        };
      }
      const v = Reflect.get(t, p, t);
      return typeof v === "function" ? v.bind(t) : v;
    },
  });
}

describe("/k/view fails closed when the database cannot be read", () => {
  it("a failing lead lookup gives the fixed redirect and no page", async () => {
    await seedLead();
    const e = baseEnv({ LEADS_DB: failingOn(/FROM leads/, testEnv.LEADS_DB) });
    const res = await view({ cookie: await unlockCookieFor() }, e);
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("/kit/");
    expect(await res.text()).not.toContain(CANARY);
  });

  it("a failing unstored-floor read gives the fixed redirect and no page", async () => {
    const lid = await mintUnstoredId(KEY);
    const e = baseEnv({ DB: failingOn(/FROM kit_meta/) });
    const res = await view({ cookie: await unlockCookieFor({ lid }) }, e);
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("/kit/");
    expect(await res.text()).not.toContain(CANARY);
    // and the same cookie opens the page when the read works, so the refusal is the failure's doing
    expect((await view({ cookie: await unlockCookieFor({ lid }) })).status).toBe(200);
  });
});

describe("challenge time freshness", () => {
  // A challenge solved 5 minutes ago is still valid at the vendor, so the bound must be wider than that.
  const MIN = 60_000;
  const cases: [string, number, boolean][] = [
    ["4.5 minutes old", -4.5 * MIN, true],
    ["9 minutes old", -9 * MIN, true],
    ["11 minutes old", -11 * MIN, false],
    ["9 minutes in the future", 9 * MIN, true],
    ["11 minutes in the future", 11 * MIN, false],
  ];
  for (const [name, offset, ok] of cases) {
    it(`${ok ? "accepts" : "refuses"} a challenge ${name}`, async () => {
      const { res } = await post({}, baseEnv(), fake({ challenge: T0 + offset }));
      expect(res.headers.get("set-cookie") !== null).toBe(ok);
    });
  }
});

describe("alerts and key rotation", () => {
  it("a notify that throws releases the day's claim", async () => {
    await testEnv.LEADS_DB.prepare("INSERT INTO leads (id, email, created_ts, last_seen_ts, source) VALUES (?, ?, 1, 1, 'gate')")
      .bind("e".repeat(32), EMAIL)
      .run();
    const e = baseEnv({ MAX_LEADS: "1" });
    const boom = (async () => {
      throw new Error("sink exploded");
    }) as unknown as typeof import("../../src/alerts/dispatcher").notifyOwner;
    const ctx = { waitUntil: (p: Promise<unknown>) => void pending.push(p) };
    const res = await handleGate(req({ fields: { email: "new@example.com", "cf-turnstile-response": "ok2" } }), e, {
      fetchFn: fake().fn,
      nowMs: T0,
      notify: boom,
      ctx,
    });
    await Promise.all(pending.splice(0));
    expect(res.headers.get("set-cookie")).not.toBeNull(); // the unlock itself is unaffected
    expect(await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'leadcap-alert'").first()).toBeNull();
  });

  it("an unstored cookie minted under the previous key is still recognized after rotation", async () => {
    const OLD = "o".repeat(40);
    const lid = await mintUnstoredId(OLD);
    const rotated = baseEnv({ TOKEN_HMAC_KEY: KEY, TOKEN_HMAC_KEY_PREVIOUS: OLD });
    const cookie = await unlockCookieFor({ lid, key: OLD });
    expect((await view({ cookie }, rotated)).status).toBe(200);
    // the unstored floor applies to it (it was recognized as unstored, not looked up as a lead)
    await setUnstoredSessionFloor(testEnv.DB, T0);
    expect((await view({ cookie }, rotated)).status).toBe(303);
    // without the previous key the token itself no longer verifies
    expect((await view({ cookie }, baseEnv())).status).toBe(303);
  });
});

describe("no personal data in logs", () => {
  it("never logs the address or the token", async () => {
    const spy = vi.spyOn(console, "log").mockImplementation(() => undefined);
    const err = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      const { res } = await post();
      await view({ cookie: cookieHeader(res) });
      await post({ fields: { email: "who@example.com", "cf-turnstile-response": "nope" } });
      const out = [...spy.mock.calls, ...err.mock.calls].flat().join("\n");
      expect(out).not.toContain("example.com");
      expect(out).not.toContain(cookieHeader(res).split("=")[1]!);
      expect(out).not.toContain("203.0.113.9");
    } finally {
      spy.mockRestore();
      err.mockRestore();
    }
  });
});

describe("worker.fetch wiring", () => {
  it("routes /k/gate and /k/view to the gate, and leaves other /k/ paths and /e/ routes alone", async () => {
    const gate = await worker.fetch(req({ headers: { origin: "https://evil.example" } }), baseEnv() as never, undefined);
    expect(gate.status).toBe(303);
    expect(gate.headers.get("location")).toBe("/kit/retry/");
    const v = await worker.fetch(req({ method: "GET", path: VIEW_ROUTE }), baseEnv() as never, undefined);
    expect(v.headers.get("location")).toBe("/kit/");
    const other = await worker.fetch(req({ method: "GET", path: "/k/other" }), baseEnv() as never, undefined);
    expect(other.status).toBe(204);
  });
  it("routes /k/gate and /k/view to the gate whatever the query length", async () => {
    const q = `?${"a=1&".repeat(600)}`; // the whole URL is well over the analytics URL cap
    const gate = await worker.fetch(
      req({ headers: { origin: "https://evil.example" }, path: `${GATE_ROUTE}${q}` }),
      baseEnv() as never,
      undefined,
    );
    expect(gate.status).toBe(303);
    expect(gate.headers.get("location")).toBe("/kit/retry/");
    const v = await worker.fetch(req({ method: "GET", path: `${VIEW_ROUTE}${q}` }), baseEnv() as never, undefined);
    expect(v.status).toBe(303);
    expect(v.headers.get("location")).toBe("/kit/");
    // an oversize URL on any other /k/ path still gets the click route's empty 204
    const other = await worker.fetch(req({ method: "GET", path: `/k/other${q}` }), baseEnv() as never, undefined);
    expect(other.status).toBe(204);
  });
});
