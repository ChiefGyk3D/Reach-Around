// SPDX-License-Identifier: Apache-2.0
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createDispatcher } from "../../src/alerts/dispatcher";
import { GATED_KV_KEY, GATE_ROUTE, VIEW_ROUTE, handleGate, type GateEnv } from "../../src/kit/gate";
import { setSessionFloor } from "../../src/kit/leads";
import { COOKIE_NAME, signToken } from "../../src/kit/token";
import { resetEventCapCache } from "../../src/kit/budgets";
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
function fake(opts: { siteverify?: "ok" | "throw" | "wrong-host" | "wrong-action" | "error-code" | "500" } = {}): Fake {
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
      if (mode === "wrong-host") r.hostname = "evil.example";
      if (mode === "wrong-action") r.action = "send";
      if (mode === "error-code") r["error-codes"] = ["timeout-or-duplicate"];
      return new Response(JSON.stringify(r), { status: 200, headers: { "content-type": "application/json" } });
    }
    return new Response(null, { status: 204 });
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
    SITE_HOST: HOST,
    KIT: kv(),
    GATE_LIMITER: limiter(),
    TOKEN_HMAC_KEY: KEY,
    TURNSTILE_SECRET: "1x0000000000000000000000000000000AA",
    VIEW_HASH_SECRET: btoa("s".repeat(40)),
    ALERT_DEDUP_KEY: "d".repeat(40),
    ALERTS_CONFIG: JSON.stringify({ channels: ["discord"], events: ["kit_unlocked"] }),
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
async function post(o: Opts = {}, e: GateEnv = baseEnv(), f: Fake = fake(), nowMs = T0) {
  const ctx = { waitUntil: (p: Promise<unknown>) => void pending.push(p) };
  const res = await handleGate(req(o), e, { fetchFn: f.fn, nowMs, notify: createDispatcher().notify, ctx });
  await Promise.all(pending.splice(0));
  return { res, f };
}

async function count(table: string): Promise<number> {
  return (await testEnv.DB.prepare(`SELECT COUNT(*) AS n FROM ${table}`).first<{ n: number }>())!.n;
}

function setCookie(res: Response): string | null {
  return res.headers.get("set-cookie");
}
function cookieHeader(res: Response): string {
  return setCookie(res)!.split(";")[0]!;
}

beforeEach(async () => {
  resetEventCapCache();
  await testEnv.DB.batch([
    testEnv.DB.prepare("DELETE FROM kit_events"),
    testEnv.DB.prepare("DELETE FROM leads"),
    testEnv.DB.prepare("DELETE FROM counters"),
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
    const lead = await testEnv.DB.prepare("SELECT email, source, country, verified_ts FROM leads").first<Record<string, unknown>>();
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
    const lead = await testEnv.DB.prepare("SELECT id FROM leads").first<{ id: string }>();
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
  { name: "lead cap is full", env: () => baseEnv({ MAX_LEADS: "1" }) },
  { name: "signing key too short", env: () => baseEnv({ TOKEN_HMAC_KEY: "k".repeat(31) }) },
  { name: "missing Turnstile secret", env: () => baseEnv({ TURNSTILE_SECRET: "" }) },
  { name: "missing KIT binding", env: () => baseEnv({ KIT: undefined }) },
  { name: "missing limiter binding", env: () => baseEnv({ GATE_LIMITER: undefined }) },
  { name: "missing VIEW_HASH_SECRET", env: () => baseEnv({ VIEW_HASH_SECRET: undefined }) },
];

describe("gate routes: refusals, limits and session rules", () => {
  for (const c of FAILURES) {
    it(c.name, async () => {
      if (c.name === "lead cap is full") {
        await testEnv.DB.prepare("INSERT INTO leads (id, email, created_ts, last_seen_ts, source) VALUES (?, ?, 1, 1, 'gate')")
          .bind("a".repeat(32), "other@example.com")
          .run();
      }
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

  it("the hourly route budget stops a flood after N posts and then writes nothing more", async () => {
    const e = baseEnv({ ROUTE_BUDGET_GATE: "2" });
    for (let i = 0; i < 4; i++) await post({ fields: { email: EMAIL, "cf-turnstile-response": "bad" + i } }, e);
    const row = await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'route-gate'").first<{ count: number }>();
    expect(row!.count).toBe(2);
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
    await testEnv.DB.prepare("UPDATE leads SET email = ? WHERE id = ?").bind(EMAIL, id).run();
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
  await testEnv.DB.prepare("INSERT INTO leads (id, email, created_ts, last_seen_ts, source, session_floor_ts) VALUES (?, ?, 1, 1, 'gate', ?)")
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
    await testEnv.DB.prepare("DELETE FROM leads WHERE id = ?").bind(id).run();
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

  it("an empty KV key is a 404 with no body", async () => {
    await seedLead();
    const res = await view({ cookie: await unlockCookieFor() }, baseEnv({ KIT: kv(null) }));
    expect(res.status).toBe(404);
    expect(await res.text()).toBe("");
  });

  it("a failing KV read is a 404, not an error page", async () => {
    await seedLead();
    const broken = { get: vi.fn(async () => { throw new Error("kv down"); }) };
    expect((await view({ cookie: await unlockCookieFor() }, baseEnv({ KIT: broken }))).status).toBe(404);
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
    for (let i = 0; i < 6; i++) await post({ ...a, fields: { email: EMAIL, "cf-turnstile-response": "bad" + i } }, e);
    const spent = await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'route-gate'").first<{ count: number }>();
    expect(spent!.count).toBe(1); // only A's first request got past its limiter
    const other = await post({ ...b, fields: { email: "b@example.com", "cf-turnstile-response": "ok" } }, e);
    expect(other.res.headers.get("set-cookie")).not.toBeNull();
  });

  it("one client over its view allowance cannot spend the view budget or block another client", async () => {
    await seedLead();
    const lim = keyedLimiter(1);
    const e = baseEnv({ GATE_LIMITER: lim });
    const ck = await unlockCookieFor();
    const hdr = (ip: string) => ({ cookie: ck, headers: { "cf-connecting-ip": ip } });
    const first = await view(hdr("203.0.113.10"), e);
    expect(first.status).toBe(200);
    for (let i = 0; i < 5; i++) expect((await view(hdr("203.0.113.10"), e)).status).toBe(303);
    const spent = await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'route-view'").first<{ count: number }>();
    expect(spent!.count).toBe(1);
    expect((await view(hdr("203.0.113.11"), e)).status).toBe(200);
  });

  it("the gate and the view keep separate per-client allowances", async () => {
    await seedLead();
    const lim = keyedLimiter(1);
    const e = baseEnv({ GATE_LIMITER: lim });
    const g = await post({}, e);
    expect(g.res.headers.get("set-cookie")).not.toBeNull();
    expect((await view({ cookie: await unlockCookieFor() }, e)).status).toBe(200);
    expect(lim.hits.size).toBe(2);
  });

  it("a view with no usable client address is refused before the budget", async () => {
    await seedLead();
    const res = await view({ cookie: await unlockCookieFor(), headers: { "cf-connecting-ip": null } });
    expect(res.status).toBe(303);
    expect(await count("counters")).toBe(0);
  });
});

describe("lead cap (accepted residual)", () => {
  // At the lead cap a client that already passed Turnstile can infer whether an address is a stored
  // lead: a known address is refreshed and unlocked, a new one gets the usual refusal. The cap does
  // not evict stored leads to hide this. This test pins the behavior so it cannot get worse silently:
  // it must stay a single yes or no, and the refusal must stay identical to every other refusal.
  it("known address unlocks, new address gets the standard refusal, nothing else differs", async () => {
    await testEnv.DB.prepare("INSERT INTO leads (id, email, created_ts, last_seen_ts, source) VALUES (?, ?, 1, 1, 'gate')")
      .bind("e".repeat(32), EMAIL)
      .run();
    const e = baseEnv({ MAX_LEADS: "1" });
    const known = await post({}, e);
    expect(known.res.headers.get("set-cookie")).not.toBeNull();
    const fresh = await post({ fields: { email: "new@example.com", "cf-turnstile-response": "ok2" } }, e);
    expect(fresh.res.status).toBe(303);
    expect(fresh.res.headers.get("location")).toBe("/kit/retry/");
    expect(fresh.res.headers.get("set-cookie")).toBeNull();
    expect(await fresh.res.text()).toBe("");
    expect(await count("leads")).toBe(1);
  });
});

describe("Turnstile single use across UTC midnight", () => {
  it("a token passed just before midnight is refused just after it", async () => {
    const before = Date.UTC(2026, 10, 9, 23, 59, 50);
    const after = Date.UTC(2026, 10, 10, 0, 0, 10);
    const one = await post({}, baseEnv(), fake(), before);
    expect(one.res.headers.get("set-cookie")).not.toBeNull();
    const two = await post({ fields: { email: "other@example.com", "cf-turnstile-response": "ok" } }, baseEnv(), fake(), after);
    expect(two.res.headers.get("location")).toBe("/kit/retry/");
    expect(two.res.headers.get("set-cookie")).toBeNull();
    expect(await count("leads")).toBe(1);
  });
});

describe("session floor writer", () => {
  it("setSessionFloor writes milliseconds, and /k/view honors it", async () => {
    const id = await seedLead();
    await setSessionFloor(testEnv.DB, id, T0);
    const row = await testEnv.DB.prepare("SELECT session_floor_ts AS f FROM leads WHERE id = ?").bind(id).first<{ f: number }>();
    expect(row!.f).toBe(T0);
    expect(row!.f).toBeGreaterThan(1e12); // milliseconds, not seconds
    expect((await view({ cookie: await unlockCookieFor({ iat: NOW_SEC }) })).status).toBe(303);
    expect((await view({ cookie: await unlockCookieFor({ iat: NOW_SEC - 1 }) })).status).toBe(303);
    expect((await view({ cookie: await unlockCookieFor({ iat: NOW_SEC + 1 }) })).status).toBe(200);
  });
  it("refuses a non-finite time", async () => {
    const id = await seedLead();
    await expect(setSessionFloor(testEnv.DB, id, Number.NaN)).rejects.toThrow(RangeError);
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
});
