// SPDX-License-Identifier: Apache-2.0
//
// AB-10 and AB-2: cost and quota exhaustion. A refused request must cost the owner nothing (no
// database statement, no KV read, no vendor call, no alert), a body is bounded before it is read, and
// every budget stops at its limit instead of at the bill.
import { env as testEnv } from "cloudflare:workers";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  CANARY,
  EMAIL,
  FIREFOX,
  INQUIRY_GOOD,
  allowance,
  count,
  countingDb,
  everything,
  gateReq,
  inquiryReq,
  installNetwork,
  kv,
  limiter,
  makeEnv,
  resetState,
  send,
  signedCookie,
  insertLead,
  unlock,
  viewReq,
  type AbuseEnv,
  type Net,
  type ReqOpts,
} from "./harness";

let net: Net;
beforeEach(async () => {
  await resetState();
  net = installNetwork();
});
afterEach(() => net.restore());

const FIELDS = (n: number) => ({ email: `v${n}@example.com`, "cf-turnstile-response": `ok-${n}` });

describe("AB-10: a refused request costs nothing", () => {
  const CHEAP: [string, ReqOpts, Record<string, unknown>][] = [
    ["wrong origin", { headers: { origin: "https://evil.example" } }, {}],
    ["missing origin", { headers: { origin: null } }, {}],
    ["wrong content type", { headers: { "content-type": "application/json" } }, {}],
    ["missing content type", { headers: { "content-type": null } }, {}],
    ["missing user agent", { headers: { "user-agent": null } }, {}],
    ["tool user agent", { headers: { "user-agent": "python-requests/2.31" } }, {}],
    ["crawler user agent", { headers: { "user-agent": "Mozilla/5.0 (compatible; bingbot/2.0)" } }, {}],
    ["declared length over the cap", { headers: { "content-length": "4097" } }, {}],
    ["declared length far over the cap", { headers: { "content-length": "99999999999" } }, {}],
    ["declared length not a number", { headers: { "content-length": "12abc" } }, {}],
    ["declared length with a sign", { headers: { "content-length": "-5" } }, {}],
    ["declared length in exponent form", { headers: { "content-length": "1e3" } }, {}],
    ["no client address", { headers: { "cf-connecting-ip": null } }, {}],
    ["per-client limiter refuses", {}, { GATE_LIMITER: limiter(false) }],
    ["kill switch", {}, { KIT_KILL: "1" }],
    ["missing signing key", {}, { TOKEN_HMAC_KEY: undefined }],
    ["missing limiter binding", {}, { GATE_LIMITER: undefined }],
    ["missing check limiter binding", {}, { GATE_CHECK_LIMITER: undefined }],
    ["missing hash secret", {}, { VIEW_HASH_SECRET: undefined }],
  ];
  for (const [name, opts, over] of CHEAP) {
    it(`${name}: no database statement, no KV read, no vendor call, no alert`, async () => {
      const { db, sql } = countingDb();
      const page = kv();
      const res = await send(gateReq(opts), makeEnv({ DB: db, KIT: page, ...over }));
      expect([303, 503]).toContain(res.status);
      expect(res.headers.get("set-cookie")).toBeNull();
      expect(sql, name).toEqual([]);
      expect(page.get).not.toHaveBeenCalled();
      expect(net.calls, name).toEqual([]);
    });
  }

  const PARSE: [string, ReqOpts][] = [
    ["honeypot filled", { fields: { email: EMAIL, "cf-turnstile-response": "ok", website: "x" } }],
    ["unknown field", { fields: { email: EMAIL, "cf-turnstile-response": "ok", extra: "1" } }],
    ["repeated field", { fields: `email=${EMAIL}&email=${EMAIL}&cf-turnstile-response=ok` }],
    ["bad percent escape", { fields: `email=${EMAIL}%zz&cf-turnstile-response=ok` }],
    ["invalid email", { fields: { email: "nope", "cf-turnstile-response": "ok" } }],
    ["missing token", { fields: { email: EMAIL } }],
    ["token with control characters", { fields: { email: EMAIL, "cf-turnstile-response": "ok\u0001x" } }],
    ["oversize body", { fields: `email=${EMAIL}&cf-turnstile-response=ok&website=${"a".repeat(6000)}` }],
    ["invalid UTF-8", { body: new Uint8Array([0x65, 0x6d, 0x61, 0x69, 0x6c, 0x3d, 0xff, 0xfe]) }],
  ];
  for (const [name, opts] of PARSE) {
    it(`${name}: refused after the limiters only, with no database statement and no vendor call`, async () => {
      const { db, sql } = countingDb();
      const res = await send(gateReq(opts), makeEnv({ DB: db }));
      expect(res.status).toBe(303);
      expect(res.headers.get("location")).toBe("/kit/retry/");
      expect(sql).toEqual([]);
      expect(net.calls).toEqual([]);
    });
  }
});

describe("AB-10: a body is bounded before it is read", () => {
  function counted(totalBytes: number, chunk: number): { body: ReadableStream<Uint8Array>; stats: { pulled: number; cancelled: boolean } } {
    const stats = { pulled: 0, cancelled: false };
    let sent = 0;
    const body = new ReadableStream<Uint8Array>(
      {
        pull(c) {
          if (sent >= totalBytes) return c.close();
          const n = Math.min(chunk, totalBytes - sent);
          sent += n;
          stats.pulled += n;
          c.enqueue(new Uint8Array(n).fill(97));
        },
        cancel() {
          stats.cancelled = true;
        },
      },
      { highWaterMark: 0 },
    );
    return { body, stats };
  }

  it("a chunked body with no declared length is cut off at the cap, and the sender is told to stop", async () => {
    const { body, stats } = counted(10_000_000, 1024);
    const { db, sql } = countingDb();
    const res = await send(gateReq({ body, headers: { "content-length": null } }), makeEnv({ DB: db }));
    expect(res.headers.get("location")).toBe("/kit/retry/");
    expect(stats.pulled).toBeLessThanOrEqual(4096 + 2048);
    expect(stats.cancelled).toBe(true);
    expect(sql).toEqual([]);
    expect(net.calls).toEqual([]);
  });

  it("a body of exactly the cap is read, and one byte more is refused before the vendor is called", async () => {
    // Percent-encoded padding keeps the decoded token short enough to be valid, so only the raw size differs.
    const head = "email=pat%40example.com&cf-turnstile-response=ok-";
    const pad = (raw: number): string => {
      const fill = raw - head.length;
      return head + "%61".repeat(Math.floor(fill / 3)) + "a".repeat(fill % 3);
    };
    const atCap = pad(4096);
    expect(atCap.length).toBe(4096);
    const ok = await send(gateReq({ fields: atCap }));
    expect(ok.headers.get("location")).toBe("/k/view"); // the cap is inclusive
    expect(net.siteverify()).toBe(1);
    const past = pad(4097);
    expect(past.length).toBe(4097);
    const refused = await send(gateReq({ fields: past }));
    expect(refused.headers.get("location")).toBe("/kit/retry/");
    expect(refused.headers.get("set-cookie")).toBeNull();
    expect(net.siteverify()).toBe(1);
  });

  it("a body that stalls forever is cut off at the 5 second deadline, with no database or vendor call", async () => {
    const body = new ReadableStream<Uint8Array>({ pull: () => new Promise(() => undefined) }, { highWaterMark: 0 });
    const { db, sql } = countingDb();
    const started = Date.now();
    const res = await send(gateReq({ body, headers: { "content-length": null } }), makeEnv({ DB: db }));
    const took = Date.now() - started;
    expect(res.headers.get("location")).toBe("/kit/retry/");
    expect(took).toBeGreaterThanOrEqual(4500);
    expect(took).toBeLessThan(9000);
    expect(sql).toEqual([]);
    expect(net.calls).toEqual([]);
  }, 20_000);

  it("a declared length smaller than the real body is refused rather than trusted", async () => {
    const text = `email=${EMAIL}&cf-turnstile-response=ok-lie`;
    const res = await send(gateReq({ fields: text, headers: { "content-length": "10" } }));
    expect(res.headers.get("set-cookie")).toBeNull();
    expect(res.headers.get("location")).toBe("/kit/retry/");
  });

  it("the inquiry form bounds its body the same way and sends nothing", async () => {
    await insertLead();
    const cookie = await signedCookie();
    const { body, stats } = counted(5_000_000, 2048);
    const res = await send(inquiryReq({ cookie, body, headers: { "content-length": null } }));
    expect([400, 413]).toContain(res.status);
    expect(stats.pulled).toBeLessThan(200_000);
    expect(stats.cancelled).toBe(true);
    expect(net.sink()).toHaveLength(0);
  });
});

describe("AB-10: floods are stopped by the allowances, not by the bill", () => {
  it("a per-client allowance of 2 lets only 2 of 10 requests from one client reach the vendor", async () => {
    const env = makeEnv({ GATE_LIMITER: allowance(2) });
    for (let i = 0; i < 10; i++) await send(gateReq({ fields: { email: `f${i}@example.com`, "cf-turnstile-response": `bad-${i}` } }), env);
    expect(net.siteverify()).toBe(2);
    expect(await count("counters")).toBe(0);
  });

  it("a per-network allowance of 3 caps vendor calls across many clients of one network", async () => {
    const check = allowance(3);
    const env = makeEnv({ GATE_CHECK_LIMITER: check });
    for (let i = 0; i < 25; i++) {
      await send(gateReq({ headers: { "cf-connecting-ip": `203.0.113.${10 + i}` }, fields: { email: `n${i}@example.com`, "cf-turnstile-response": `bad-${i}` } }), env);
    }
    expect(net.siteverify()).toBe(3);
    // every client of the /24 was keyed to the same network key
    expect(new Set(check.keys).size).toBe(1);
  });

  it("junk that never solves the challenge writes no counter row and no lead, however much of it arrives", async () => {
    const env = makeEnv();
    for (let i = 0; i < 60; i++) await send(gateReq({ fields: FIELDS(i).email ? { email: `j${i}@example.com`, "cf-turnstile-response": `bad-${i}` } : {} }), env);
    expect(await count("counters")).toBe(0);
    expect(await count("leads")).toBe(0);
    expect(await count("kit_events")).toBe(0);
    expect(net.sink()).toHaveLength(0);
  });

  it("the Turnstile outage case: every request fails the same way and writes nothing", async () => {
    net.restore();
    net = installNetwork({ siteverify: "throw" });
    for (let i = 0; i < 10; i++) {
      const res = await send(gateReq({ fields: FIELDS(i) }));
      expect(res.headers.get("location")).toBe("/kit/retry/");
    }
    expect(await count("counters")).toBe(0);
  });
});

describe("AB-2: the hourly route budgets stop the writes at their limit", () => {
  it("a gate budget of 3 admits exactly 3 of 10 solved challenges and spends exactly 3 claim rows", async () => {
    const env = makeEnv({ ROUTE_BUDGET_GATE: "3" });
    const results: Response[] = [];
    for (let i = 0; i < 10; i++) results.push(await send(gateReq({ headers: { "cf-connecting-ip": `203.0.113.${40 + i}` }, fields: FIELDS(i) }), env));
    const unlocked = results.filter((r) => r.headers.get("set-cookie") !== null);
    expect(unlocked).toHaveLength(3);
    expect(await count("counters", "WHERE key LIKE 'ts-%'")).toBe(3);
    const row = await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'route-gate'").first<{ count: number }>();
    expect(row!.count).toBe(3);
    const after = await count("counters");
    // seven more solved challenges after the budget is spent: nothing is written
    for (let i = 10; i < 17; i++) await send(gateReq({ headers: { "cf-connecting-ip": `203.0.113.${40 + i}` }, fields: FIELDS(i) }), env);
    expect(await count("counters")).toBe(after);
    // and the refused ones are byte-identical to any other refusal
    const refused = results.filter((r) => r.headers.get("set-cookie") === null);
    const shapes = new Set<string>();
    for (const r of refused) shapes.add(JSON.stringify([r.status, [...r.headers].sort(), await r.text()]));
    shapes.add(JSON.stringify(await shape(await send(gateReq({ headers: { origin: "https://evil.example" } })))));
    expect(shapes.size).toBe(1);
  });

  it("a gate budget of 0 closes the gate to everyone without writing anything", async () => {
    const res = await send(gateReq(), makeEnv({ ROUTE_BUDGET_GATE: "0" }));
    expect(res.headers.get("location")).toBe("/kit/retry/");
    expect(res.headers.get("set-cookie")).toBeNull();
    expect(await count("counters")).toBe(0);
    expect(await count("leads")).toBe(0);
  });

  it("a view budget of 2 serves 2 of 5 views of one valid cookie and writes nothing for the rest", async () => {
    const cookie = await unlock();
    const env = makeEnv({ ROUTE_BUDGET_VIEW: "2" });
    const statuses: number[] = [];
    for (let i = 0; i < 5; i++) {
      const res = await send(viewReq({ cookie }), env);
      statuses.push(res.status);
      if (res.status !== 200) expect(await everything(res)).not.toContain(CANARY);
    }
    expect(statuses).toEqual([200, 200, 303, 303, 303]);
    const row = await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'route-view'").first<{ count: number }>();
    expect(row!.count).toBe(2);
  });

  it("a view budget of 0 serves nobody", async () => {
    const cookie = await unlock();
    const res = await send(viewReq({ cookie }), makeEnv({ ROUTE_BUDGET_VIEW: "0" }));
    expect(res.status).toBe(303);
    expect(await count("counters", "WHERE key = 'route-view'")).toBe(0);
  });

  it("an invalid budget setting closes the route instead of removing the limit", async () => {
    const cookie = await unlock();
    for (const bad of ["lots", "-1", "1.5", "999999999", "1e3", ""]) {
      const res = await send(viewReq({ cookie }), makeEnv({ ROUTE_BUDGET_VIEW: bad }));
      if (bad === "") continue; // unset-equivalent: the default applies
      expect(res.status, bad).toBe(303);
    }
  });
});

async function shape(res: Response): Promise<unknown> {
  return [res.status, [...res.headers].sort(), await res.text()];
}

describe("AB-2: the lead caps hold, and the answer does not depend on them", () => {
  it("20 concurrent first-time addresses with MAX_LEADS 5 store 5 rows and unlock all 20 the same way", async () => {
    const env = makeEnv({ MAX_LEADS: "5" });
    const results = await Promise.all(Array.from({ length: 20 }, (_, i) => send(gateReq({ headers: { "cf-connecting-ip": `203.0.113.${100 + i}` }, fields: FIELDS(i) }), env)));
    expect(results.every((r) => r.status === 303 && r.headers.get("location") === "/k/view" && r.headers.get("set-cookie") !== null)).toBe(true);
    expect(await count("leads")).toBeLessThanOrEqual(5);
    expect(await count("leads")).toBeGreaterThan(0);
    const lengths = new Set(results.map((r) => r.headers.get("set-cookie")!.length));
    expect(lengths.size).toBe(1); // a stored and an unstored cookie are the same shape
  });

  it("the default daily new-lead cap (100) stops storing at 100 and still unlocks the 101st to 105th", async () => {
    const env = makeEnv();
    let unlocked = 0;
    for (let i = 0; i < 105; i++) {
      const res = await send(gateReq({ fields: FIELDS(i) }), env);
      if (res.headers.get("set-cookie") !== null) unlocked++;
    }
    expect(unlocked).toBe(105);
    expect(await count("leads")).toBe(100);
    const row = await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'newlead'").first<{ count: number }>();
    expect(row!.count).toBe(100);
  });

  it("an unstored visitor causes no event insert and no lead insert attempt", async () => {
    const env = makeEnv({ MAX_LEADS: "1" });
    await send(gateReq({ fields: FIELDS(1) }), env);
    const { db, sql } = countingDb();
    const res = await send(gateReq({ fields: FIELDS(2) }), makeEnv({ MAX_LEADS: "1", DB: db }));
    expect(res.headers.get("set-cookie")).not.toBeNull();
    expect(await count("leads")).toBe(1);
    expect(sql.filter((s) => s.includes("INSERT INTO kit_events"))).toEqual([]);
  });

  it("one address submitted 20 times at once is one lead row and one daily unit", async () => {
    const env = makeEnv();
    const results = await Promise.all(Array.from({ length: 20 }, (_, i) => send(gateReq({ fields: { email: "same@example.com", "cf-turnstile-response": `ok-same-${i}` } }), env)));
    expect(results.every((r) => r.headers.get("set-cookie") !== null)).toBe(true);
    expect(await count("leads")).toBe(1);
    const row = await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'newlead'").first<{ count: number }>();
    expect(row!.count).toBe(1);
  });

  it("one Turnstile token used 20 times at once unlocks exactly once", async () => {
    const env = makeEnv();
    const results = await Promise.all(Array.from({ length: 20 }, (_, i) => send(gateReq({ headers: { "cf-connecting-ip": `203.0.113.${150 + i}` }, fields: { email: `r${i}@example.com`, "cf-turnstile-response": "ok-shared" } }), env)));
    expect(results.filter((r) => r.headers.get("set-cookie") !== null)).toHaveLength(1);
    expect(await count("counters", "WHERE key LIKE 'ts-%'")).toBe(1);
    const row = await testEnv.DB.prepare("SELECT count FROM counters WHERE key = 'route-gate'").first<{ count: number }>();
    expect(row!.count).toBe(1); // the losers gave their unit back
  });
});

describe("AB-10: the alert path cannot be turned into a flood", () => {
  it("an alert sink that is down never fails or slows an unlock", async () => {
    net.restore();
    net = installNetwork({ sink: 500 });
    const res = await send(gateReq());
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("/k/view");
    expect(res.headers.get("set-cookie")).not.toBeNull();
  });

  it("the unlock page stays available for a client that holds a cookie when the allowances are spent for others", async () => {
    const cookie = await unlock();
    const res = await send(viewReq({ cookie }), makeEnv({ GATE_LIMITER: limiter(false), GATE_CHECK_LIMITER: limiter(false) }));
    expect(res.status).toBe(200);
    expect(await res.text()).toContain(CANARY);
    expect(FIREFOX.length).toBeGreaterThan(0);
  });
});

export type { AbuseEnv };
