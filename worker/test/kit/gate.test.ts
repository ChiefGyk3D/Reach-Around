// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import worker from "../../src/index";
import { handleGate, handleKit, handleView } from "../../src/kit/gate";
import { verifyToken, signToken } from "../../src/kit/token";
import {
  CANARY,
  DB,
  DISCORD,
  HOST,
  IP,
  KEY,
  NOW,
  OTHER_KEY,
  UA,
  captureLogs,
  cookieOf,
  ctxOf,
  fakeFetch,
  gatePost,
  makeEnv,
  newFakes,
  resetDb,
  settle,
  viewReq,
  type Fakes,
} from "./gatekit";

let f: Fakes;
beforeEach(async () => {
  await resetDb();
  f = newFakes();
});
afterEach(() => vi.unstubAllGlobals());

const deps = () => ({ fetchFn: fakeFetch(f), nowMs: NOW });
const gate = (req: Request, over: Record<string, unknown> = {}) => handleGate(req, makeEnv(f, over), ctxOf(f), deps());
const view = (req: Request, over: Record<string, unknown> = {}) => handleView(req, makeEnv(f, over), deps());

async function leadRows() {
  return (await DB.prepare("SELECT * FROM leads").all<Record<string, unknown>>()).results;
}

async function unlock(email = "partner@example.test"): Promise<string> {
  const res = await gate(gatePost({ fields: { email, "cf-turnstile-response": "ok", website: "" } }));
  const c = cookieOf(res);
  expect(c).not.toBeNull();
  return c!;
}

/** The one answer every gate failure gives. */
async function expectGeneric(res: Response) {
  expect(res.status).toBe(303);
  expect(res.headers.get("Location")).toBe("/kit/retry/");
  expect(res.headers.get("Set-Cookie")).toBeNull();
  expect(res.headers.get("Cache-Control")).toBe("private, no-store");
  expect(await res.text()).toBe("");
}

describe("the unlock", () => {
  it("a valid post returns 303 /k/view with the cookie, a lead row and an unlock event", async () => {
    const res = await gate(gatePost());
    expect(res.status).toBe(303);
    expect(res.headers.get("Location")).toBe("/k/view");
    expect(res.headers.get("Cache-Control")).toBe("private, no-store");
    const rows = await leadRows();
    expect(rows).toHaveLength(1);
    expect(rows[0]!.email).toBe("partner@example.test");
    expect(rows[0]!.source).toBe("gate");
    expect(rows[0]!.verified_ts).toBeNull();
    expect(rows[0]!.country).toBe("US");
    const ev = (await DB.prepare("SELECT kind, lead_id FROM kit_events").all<{ kind: string; lead_id: string }>()).results;
    expect(ev).toEqual([{ kind: "unlock", lead_id: rows[0]!.id }]);
  });

  it("the cookie is HttpOnly Secure SameSite=Lax with no Domain, its token verifies and lid is the lead id; no token in Location or body", async () => {
    const res = await gate(gatePost());
    const set = res.headers.get("Set-Cookie")!;
    expect(set).toMatch(/^__Host-ra_kit=[^;]+; HttpOnly; Secure; SameSite=Lax; Path=\/; Max-Age=86400$/);
    expect(set).not.toMatch(/Domain/i);
    const token = cookieOf(res)!;
    const claims = await verifyToken([KEY], token, Math.floor(NOW / 1000));
    expect(claims).not.toBeNull();
    expect(claims!.lid).toBe((await leadRows())[0]!.id);
    expect(claims!.cc).toBe("US");
    expect(res.headers.get("Location")).not.toContain(token);
    expect(await res.text()).toBe("");
  });

  it("the email is normalized (lowercased) before it is stored", async () => {
    await gate(gatePost({ fields: { email: "Partner@Example.TEST", "cf-turnstile-response": "ok" } }));
    expect((await leadRows())[0]!.email).toBe("partner@example.test");
  });

  it("a double submit stores one lead, issues the same lid twice and alerts once", async () => {
    const env = { ALERT_DISCORD_WEBHOOK: DISCORD };
    const a = await gate(gatePost(), env);
    const b = await gate(gatePost(), env);
    await settle(f);
    expect(await leadRows()).toHaveLength(1);
    const ca = await verifyToken([KEY], cookieOf(a)!, Math.floor(NOW / 1000));
    const cb = await verifyToken([KEY], cookieOf(b)!, Math.floor(NOW / 1000));
    expect(ca!.lid).toBe(cb!.lid);
    expect(f.fetchCalls.filter((c) => c.url.startsWith("https://discord.com/")).length).toBe(1);
  });

  it("country comes only from CF-IPCountry in the two-letter form; XX, lowercase and junk store null", async () => {
    for (const [i, c] of ["XX", "us", "USA", "U1", ""].entries()) {
      await gate(gatePost({ fields: { email: `p${i}@example.test`, "cf-turnstile-response": "ok" }, headers: { "CF-IPCountry": c } }));
    }
    const rows = await leadRows();
    expect(rows).toHaveLength(5);
    expect(rows.every((r) => r.country === null)).toBe(true);
  });

  it("client-supplied lead fields are refused, not applied (closed field set)", async () => {
    for (const extra of ["verified=1", "lid=zzz", "country=XX", "source=email"]) {
      const body = `email=partner%40example.test&cf-turnstile-response=ok&${extra}`;
      await expectGeneric(await gate(gatePost({ fields: body })));
    }
    expect(await leadRows()).toHaveLength(0);
  });
});

describe("privacy: nothing about the client is stored or echoed", () => {
  it("no IP, user agent or referrer appears in any table, and the limiter key is not the address", async () => {
    await gate(gatePost());
    const dump = JSON.stringify([
      (await DB.prepare("SELECT * FROM leads").all()).results,
      (await DB.prepare("SELECT * FROM kit_events").all()).results,
      (await DB.prepare("SELECT * FROM counters").all()).results,
    ]);
    for (const needle of [IP, "Firefox", "Gecko", "/kit/", HOST]) expect(dump).not.toContain(needle);
    expect(f.limiterKeys).toHaveLength(1);
    expect(f.limiterKeys[0]).toMatch(/^[0-9a-f]{32}$/);
  });

  it("a stored value never reaches a response: the gate and the view answer without the email", async () => {
    const email = "unique-sentinel-77@example.test";
    const res = await gate(gatePost({ fields: { email, "cf-turnstile-response": "ok" } }));
    const cookie = cookieOf(res)!;
    const v = await view(viewReq(cookie));
    for (const r of [res, v]) {
      const hdr = JSON.stringify([...r.headers.entries()]);
      expect(hdr).not.toContain("unique-sentinel-77");
      expect(hdr).not.toContain(IP);
      expect(await r.text()).not.toContain("unique-sentinel-77");
    }
  });

  it("log lines are closed codes only: no email, token, IP or user agent", async () => {
    const logs = captureLogs();
    try {
      const res = await gate(gatePost({ fields: { email: "sentinel-log@example.test", "cf-turnstile-response": "ok" } }));
      await gate(gatePost({ fields: { email: "sentinel-log@example.test", "cf-turnstile-response": "bad" } }));
      await view(viewReq(cookieOf(res)!));
      const all = logs.lines.join("\n");
      expect(logs.lines.length).toBeGreaterThan(0);
      for (const needle of ["sentinel-log", cookieOf(res)!, IP, "Firefox", "ts-secret-example"]) expect(all).not.toContain(needle);
      for (const line of logs.lines) expect(JSON.parse(line)).toEqual(expect.objectContaining({ e: expect.stringMatching(/^(gate|view|alert|kill|log)$/) }));
    } finally {
      logs.restore();
    }
  });
});

describe("every failure is the same generic answer", () => {
  const cases: Array<[string, () => Request, ("deny" | "throw")?]> = [
    ["missing Turnstile response", () => gatePost({ fields: { email: "partner@example.test" } })],
    ["bad Turnstile response", () => gatePost({ fields: { email: "partner@example.test", "cf-turnstile-response": "nope" } })],
    ["invalid email", () => gatePost({ fields: { email: "not-an-email", "cf-turnstile-response": "ok" } })],
    ["missing email", () => gatePost({ fields: { "cf-turnstile-response": "ok" } })],
    ["filled honeypot", () => gatePost({ fields: { email: "partner@example.test", "cf-turnstile-response": "ok", website: "http://spam.example.test" } })],
    ["header injection in the email", () => gatePost({ fields: { email: "a@example.test\r\nBcc: x@example.test", "cf-turnstile-response": "ok" } })],
    ["SQL in the email", () => gatePost({ fields: { email: "a'; DROP TABLE leads;--@example.test", "cf-turnstile-response": "ok" } })],
    ["non-ASCII email", () => gatePost({ fields: { email: "pärtner@example.test", "cf-turnstile-response": "ok" } })],
    ["NUL in the email", () => gatePost({ fields: { email: "a\u0000@example.test", "cf-turnstile-response": "ok" } })],
    ["huge email", () => gatePost({ fields: { email: `${"a".repeat(300)}@example.test`, "cf-turnstile-response": "ok" } })],
    ["repeated field", () => gatePost({ fields: "email=a%40example.test&email=b%40example.test&cf-turnstile-response=ok" })],
    ["bad percent encoding", () => gatePost({ fields: "email=a%ZZ%40example.test&cf-turnstile-response=ok" })],
    ["bot user agent", () => gatePost({ headers: { "User-Agent": "curl/8.0" } })],
    ["missing user agent", () => gatePost({ headers: { "User-Agent": null } })],
    ["oversize user agent", () => gatePost({ headers: { "User-Agent": "Mozilla/5.0 " + "a".repeat(600) } })],
    ["wrong origin", () => gatePost({ headers: { Origin: "https://evil.example.test" } })],
    ["missing origin", () => gatePost({ headers: { Origin: null } })],
    ["null origin", () => gatePost({ headers: { Origin: "null" } })],
    ["origin with a port", () => gatePost({ headers: { Origin: `https://${HOST}:8443` } })],
    ["JSON body", () => gatePost({ fields: '{"email":"partner@example.test"}', headers: { "Content-Type": "application/json" } })],
    ["content type with a charset suffix", () => gatePost({ headers: { "Content-Type": "application/x-www-form-urlencoded; charset=utf-8" } })],
    ["multipart body", () => gatePost({ headers: { "Content-Type": "multipart/form-data; boundary=x" } })],
    ["missing content length", () => gatePost({ headers: { "Content-Length": null } })],
    ["declared length over the cap", () => gatePost({ headers: { "Content-Length": "999999" } })],
    ["declared length not a number", () => gatePost({ headers: { "Content-Length": "12abc" } })],
    ["body over the cap with a lying small length", () => gatePost({ fields: "email=a%40example.test&cf-turnstile-response=ok&pad=" + "x".repeat(9000), headers: { "Content-Length": "40" } })],
    ["the limiter says no", () => gatePost(), "deny"],
    ["the limiter throws", () => gatePost(), "throw"],
  ];
  for (const [name, mk, lim] of cases) {
    it(name, async () => {
      if (lim !== undefined) f.limiter = lim;
      await expectGeneric(await gate(mk()));
      expect(await leadRows()).toHaveLength(0);
    });
  }

  it("Turnstile with the wrong hostname, the wrong action, or an outage fails closed", async () => {
    for (const t of ["wrong-host", "wrong-action", "throw"] as const) {
      f.turnstile = t;
      await expectGeneric(await gate(gatePost()));
    }
    expect(await leadRows()).toHaveLength(0);
  });

  it("a refusal before the budget makes zero database statements and zero outbound calls", async () => {
    const early = [
      gatePost({ headers: { Origin: "https://evil.example.test" } }),
      gatePost({ headers: { "User-Agent": "curl/8.0" } }),
      gatePost({ headers: { "Content-Type": "application/json" } }),
      gatePost({ headers: { "Content-Length": "999999" } }),
    ];
    for (const r of early) await expectGeneric(await gate(r));
    f.limiter = "deny";
    await expectGeneric(await gate(gatePost()));
    expect(f.statements).toEqual([]);
    expect(f.fetchCalls).toEqual([]);
  });

  it("a refusal before the body read never pulls the body stream", async () => {
    const body = new ReadableStream({
      pull(c) {
        c.enqueue(new Uint8Array(10));
        c.close();
      },
    });
    const req = new Request(`https://${HOST}/k/gate`, {
      method: "POST",
      body,
      duplex: "half",
      headers: { Origin: "https://evil.example.test", "User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded", "Content-Length": "10" },
    } as RequestInit);
    await expectGeneric(await gate(req));
    expect(req.bodyUsed).toBe(false);
    expect(req.body!.locked).toBe(false);
  });
});

describe("environment and bindings fail closed with the same answer and no statement", () => {
  const broken: Array<[string, Record<string, unknown>]> = [
    ["KIT binding missing", { KIT: undefined }],
    ["GATE_LIMITER binding missing", { GATE_LIMITER: undefined }],
    ["TOKEN_HMAC_KEY missing", { TOKEN_HMAC_KEY: undefined }],
    ["TOKEN_HMAC_KEY of 31 bytes", { TOKEN_HMAC_KEY: "k".repeat(31) }],
    ["TURNSTILE_SECRET missing", { TURNSTILE_SECRET: undefined }],
    ["TURNSTILE_SECRET empty", { TURNSTILE_SECRET: "" }],
    ["SITE_HOST missing", { SITE_HOST: undefined }],
    ["SITE_HOST not a host", { SITE_HOST: "not a host" }],
    ["KIT_HOST present but invalid", { KIT_HOST: "https://x" }],
    ["TOKEN_TTL_SECONDS invalid", { TOKEN_TTL_SECONDS: "12" }],
    ["KIT_KILL malformed", { KIT_KILL: "maybe" }],
  ];
  for (const [name, over] of broken) {
    it(name, async () => {
      await expectGeneric(await gate(gatePost(), over));
      expect(f.statements).toEqual([]);
      expect(f.fetchCalls).toEqual([]);
    });
  }

  it("KIT_HOST names the host when the gate lives apart from SITE_HOST", async () => {
    f.tsHost = "kit.example.test";
    const res = await gate(gatePost({ headers: { Origin: "https://kit.example.test" } }), { KIT_HOST: "kit.example.test" });
    expect(res.status).toBe(303);
    expect(res.headers.get("Location")).toBe("/k/view");
  });
});

describe("kill switch", () => {
  for (const v of ["1", "true", "TRUE", "on", "yes", "maybe", "2"]) {
    it(`KIT_KILL=${v} stops every /k/ route and method with the fixed 503 and no work`, async () => {
      const paths = ["/k/gate", "/k/view", "/k/anything", "/k/", "/k"];
      for (const p of paths) {
        for (const m of ["GET", "HEAD", "POST", "OPTIONS", "PUT"]) {
          const req = new Request(`https://${HOST}${p}`, { method: m, headers: { Origin: `https://${HOST}`, "User-Agent": UA } });
          const res = await handleKit(req, makeEnv(f, { KIT_KILL: v }), ctxOf(f), deps());
          expect(res.status).toBe(503);
          expect(res.headers.get("Retry-After")).toBe("3600");
          expect(res.headers.get("Cache-Control")).toBe("private, no-store");
          expect(await res.text()).toBe("<!doctype html><title>Unavailable</title><p>Temporarily unavailable.");
        }
      }
      expect(f.statements).toEqual([]);
      expect(f.fetchCalls).toEqual([]);
    });
  }

  it("a killed gate never unlocks even with a valid post; flipping it back restores service", async () => {
    const res = await handleKit(gatePost(), makeEnv(f, { KIT_KILL: "1" }), ctxOf(f), deps());
    expect(res.status).toBe(503);
    expect(res.headers.get("Set-Cookie")).toBeNull();
    expect(await leadRows()).toHaveLength(0);
    for (const off of ["0", "false", "off", "no", "", undefined]) {
      const ok = await handleKit(gatePost(), makeEnv(f, { KIT_KILL: off }), ctxOf(f), deps());
      expect(ok.status).toBe(303);
      expect(ok.headers.get("Location")).toBe("/k/view");
    }
  });

  it("a valid cookie gets nothing from /k/view while killed", async () => {
    const cookie = await unlock();
    const res = await handleKit(viewReq(cookie), makeEnv(f, { KIT_KILL: "1" }), ctxOf(f), deps());
    expect(res.status).toBe(503);
    expect(await res.text()).not.toContain(CANARY);
  });
});

describe("budgets", () => {
  it("ROUTE_BUDGET_GATE=0 refuses before Turnstile, with no lead", async () => {
    await expectGeneric(await gate(gatePost(), { ROUTE_BUDGET_GATE: "0" }));
    expect(f.fetchCalls).toEqual([]);
    expect(await leadRows()).toHaveLength(0);
  });

  it("the hourly route budget is spent at its limit and a refused request writes nothing more", async () => {
    const over = { ROUTE_BUDGET_GATE: "2" };
    for (let i = 0; i < 2; i++) {
      const r = await gate(gatePost({ fields: { email: `p${i}@example.test`, "cf-turnstile-response": "ok" } }), over);
      expect(r.headers.get("Location")).toBe("/k/view");
    }
    const q = "SELECT key, day, count FROM counters WHERE key = 'route-gate'";
    const before = (await DB.prepare(q).all()).results;
    await expectGeneric(await gate(gatePost({ fields: { email: "p9@example.test", "cf-turnstile-response": "ok" } }), over));
    expect((await DB.prepare(q).all()).results).toEqual(before);
    expect(await leadRows()).toHaveLength(2);
  });

  it("an unusable limit refuses (names are never echoed)", async () => {
    for (const bad of [{ ROUTE_BUDGET_GATE: "abc" }, { MAX_LEADS: "-1" }, { DAILY_ALERT_BUDGET: "1e3" }]) {
      await expectGeneric(await gate(gatePost(), bad));
    }
    expect(await leadRows()).toHaveLength(0);
  });

  it("a configuration whose combined daily D1 writes pass the quota refuses before any write", async () => {
    await expectGeneric(await gate(gatePost(), { D1_DAILY_WRITE_QUOTA: "1000" }));
    expect(f.statements).toEqual([]);
    expect(await leadRows()).toHaveLength(0);
  });

  it("MAX_LEADS stops a new address and still refreshes a known one", async () => {
    const over = { MAX_LEADS: "1" };
    expect((await gate(gatePost(), over)).headers.get("Location")).toBe("/k/view");
    await expectGeneric(await gate(gatePost({ fields: { email: "second@example.test", "cf-turnstile-response": "ok" } }), over));
    expect((await gate(gatePost(), over)).headers.get("Location")).toBe("/k/view");
    expect(await leadRows()).toHaveLength(1);
  });
});

describe("storage failures", () => {
  it("a lead insert failure is the generic answer: no cookie, no event", async () => {
    f.failSql = /INSERT INTO leads/;
    await expectGeneric(await gate(gatePost()));
    expect((await DB.prepare("SELECT COUNT(*) AS n FROM kit_events").first<{ n: number }>())!.n).toBe(0);
  });

  it("a route budget write failure is the generic answer", async () => {
    f.failSql = /INSERT INTO counters/;
    await expectGeneric(await gate(gatePost()));
    expect(await leadRows()).toHaveLength(0);
  });

  it("a failing unlock event does not fail a valid unlock", async () => {
    f.failSql = /INSERT INTO kit_events/;
    const res = await gate(gatePost());
    expect(res.headers.get("Location")).toBe("/k/view");
    expect(await leadRows()).toHaveLength(1);
  });
});

describe("GET /k/view", () => {
  it("without a cookie: 303 /kit/, empty body, no canary", async () => {
    const res = await view(viewReq(null));
    expect(res.status).toBe(303);
    expect(res.headers.get("Location")).toBe("/kit/");
    expect(await res.text()).toBe("");
  });

  it("with a valid cookie: 200 with the page and the hardened headers", async () => {
    const res = await view(viewReq(await unlock()));
    expect(res.status).toBe(200);
    expect(await res.text()).toContain(CANARY);
    expect(res.headers.get("Cache-Control")).toBe("private, no-store");
    expect(res.headers.get("X-Robots-Tag")).toBe("noindex");
    expect(res.headers.get("Referrer-Policy")).toBe("no-referrer");
    expect(res.headers.get("X-Content-Type-Options")).toBe("nosniff");
    expect(res.headers.get("Content-Security-Policy")).toContain("default-src 'none'");
    expect(res.headers.get("Content-Security-Policy")).toContain("frame-ancestors 'none'");
    expect(res.headers.get("Set-Cookie")).toBeNull();
  });

  it("ignores a valid token in the query, an Authorization header, a custom header and a wrong or duplicated cookie", async () => {
    const t = await unlock();
    const reqs = [
      new Request(`https://${HOST}/k/view?t=${t}&token=${t}`),
      new Request(`https://${HOST}/k/view`, { headers: { Authorization: `Bearer ${t}` } }),
      new Request(`https://${HOST}/k/view`, { headers: { "X-Kit-Token": t } }),
      new Request(`https://${HOST}/k/view`, { headers: { Cookie: `ra_kit=${t}` } }),
      new Request(`https://${HOST}/k/view`, { headers: { Cookie: `__Host-ra_pend=${t}` } }),
      new Request(`https://${HOST}/k/view`, { headers: { Cookie: `__Host-ra_kit=${t}; __Host-ra_kit=${t}` } }),
    ];
    for (const r of reqs) {
      const res = await view(r);
      expect(res.status).toBe(303);
      expect(await res.text()).not.toContain(CANARY);
    }
  });

  it("an expired, tampered or foreign-key cookie gets a 303", async () => {
    const t = await unlock();
    const later = await handleView(viewReq(t), makeEnv(f), { nowMs: NOW + 25 * 3600 * 1000 });
    expect(later.status).toBe(303);
    const parts = t.split(".");
    const tampered = `${parts[0]}.${parts[1]}.${parts[2]!.slice(0, -2)}AA`;
    expect((await view(viewReq(tampered))).status).toBe(303);
    const lid = (await leadRows())[0]!.id as string;
    const foreign = await signToken(OTHER_KEY, { lid, iat: Math.floor(NOW / 1000), exp: Math.floor(NOW / 1000) + 3600, cc: "" });
    expect((await view(viewReq(foreign))).status).toBe(303);
  });

  it("a cookie for a deleted lead gets a 303", async () => {
    const t = await unlock();
    await DB.prepare("DELETE FROM leads").run();
    expect((await view(viewReq(t))).status).toBe(303);
  });

  it("a token issued before the lead's session floor gets a 303", async () => {
    const t = await unlock();
    await DB.prepare("UPDATE leads SET session_floor_ts = ?").bind(NOW + 1000).run();
    expect((await view(viewReq(t))).status).toBe(303);
  });

  it("the view budget caps D1 writes: at 0 a valid cookie gets a 303 and no page", async () => {
    const t = await unlock();
    const res = await view(viewReq(t), { ROUTE_BUDGET_VIEW: "0" });
    expect(res.status).toBe(303);
    expect(await res.text()).not.toContain(CANARY);
  });

  it("an unauthenticated request makes no D1 statement", async () => {
    await view(viewReq(null));
    await view(viewReq("v1.AAAA.BBBB"));
    expect(f.statements).toEqual([]);
  });

  it("an empty or failing KV is a 404 with no body", async () => {
    const t = await unlock();
    f.kv.clear();
    const a = await view(viewReq(t));
    expect(a.status).toBe(404);
    expect(await a.text()).toBe("");
    f.kv.set("kit/v1", "x");
    f.kvThrows = true;
    expect((await view(viewReq(t))).status).toBe(404);
  });

  it("HEAD returns the headers and no body", async () => {
    const t = await unlock();
    const res = await handleKit(viewReq(t, {}, "/k/view", "HEAD"), makeEnv(f), ctxOf(f), deps());
    expect(res.status).toBe(200);
    expect(await res.text()).toBe("");
    expect(res.headers.get("Cache-Control")).toBe("private, no-store");
  });

  it("the canary appears only in an authorized /k/view response", async () => {
    const t = await unlock();
    const env = makeEnv(f);
    const probes: Array<[string, string]> = [
      ["/k/content", "GET"],
      ["/k/kit/v1", "GET"],
      ["/k/view/", "GET"],
      ["/kit/v1", "GET"],
      ["/k/view", "POST"],
      ["/k/view", "PUT"],
      ["/k/gate", "GET"],
      ["/k/v/abc", "GET"],
      ["/k/send", "POST"],
      ["/k/%2e%2e/view", "GET"],
    ];
    for (const [p, m] of probes) {
      for (const cookie of [null, t]) {
        const res = await handleKit(viewReq(cookie, {}, p, m), env, ctxOf(f), deps());
        expect(await res.text(), `${m} ${p}`).not.toContain(CANARY);
      }
    }
  });
});

describe("routing and the other-routes invariant", () => {
  it("unknown /k/ paths and wrong methods are an empty no-store 404", async () => {
    for (const [p, m] of [["/k/nope", "GET"], ["/k/gate", "GET"], ["/k/view", "DELETE"], ["/k", "GET"], ["/k/", "POST"]] as const) {
      const res = await handleKit(new Request(`https://${HOST}${p}`, { method: m }), makeEnv(f), ctxOf(f), deps());
      expect(res.status).toBe(404);
      expect(res.headers.get("Cache-Control")).toBe("private, no-store");
      expect(await res.text()).toBe("");
    }
  });

  it("every response from the gate and view routes carries Cache-Control private, no-store and never public", async () => {
    const t = await unlock();
    const rs = [
      await gate(gatePost()),
      await gate(gatePost({ headers: { Origin: "https://evil.example.test" } })),
      await view(viewReq(t)),
      await view(viewReq(null)),
      await handleKit(new Request(`https://${HOST}/k/zzz`), makeEnv(f), ctxOf(f), deps()),
      await handleKit(viewReq(t), makeEnv(f, { KIT_KILL: "1" }), ctxOf(f), deps()),
    ];
    for (const r of rs) {
      const cc = r.headers.get("Cache-Control")!;
      expect(cc).toContain("no-store");
      expect(cc).not.toMatch(/public|s-maxage/);
    }
  });

  it("through worker.fetch: the unlock works end to end with the global fetch", async () => {
    vi.stubGlobal("fetch", fakeFetch(f));
    const res = await worker.fetch(gatePost(), makeEnv(f) as never, ctxOf(f) as never);
    expect(res.status).toBe(303);
    const t = cookieOf(res)!;
    const v = await worker.fetch(viewReq(t), makeEnv(f) as never, ctxOf(f) as never);
    expect(v.status).toBe(200);
    expect(await v.text()).toContain(CANARY);
  });

  it("every path outside /k/ still gets the empty 204, kill switch or not", async () => {
    const paths = ["/", "/kit/", "/kit/retry/", "/kx", "/kk/gate", "/e", "/e/x", "/e/c/nope", "/e/v/nope", "/e/live/x", "/index.html", "/.env", "//k/gate"];
    for (const kill of [undefined, "1"]) {
      for (const p of paths) {
        for (const m of ["GET", "POST", "HEAD", "PUT"]) {
          const body = m === "POST" || m === "PUT" ? "x" : undefined;
          const res = await worker.fetch(new Request(`https://${HOST}${p}`, { method: m, body }), makeEnv(f, { KIT_KILL: kill }) as never, ctxOf(f) as never);
          expect(res.status, `${m} ${p}`).toBe(204);
          expect(await res.text()).toBe("");
        }
      }
    }
    expect(f.fetchCalls).toEqual([]);
  });

  it("a /k/ lookalike with an oversize URL is not treated as a kit route", async () => {
    const res = await worker.fetch(new Request(`https://${HOST}/k/${"a".repeat(3000)}`), makeEnv(f) as never, ctxOf(f) as never);
    expect(res.status).toBe(204);
  });
});
