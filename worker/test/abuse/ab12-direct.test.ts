// SPDX-License-Identifier: Apache-2.0
//
// Abuse cases that end-to-end requests cannot reach (a type-closed argument, a hook that throws, a
// key shape the environment loader would already refuse), checked at the function the route calls.
// Each one closes a mutation the first mutation run left alive.
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import { claimGateUnit, reserveRoute } from "../../src/kit/budgets";
import { guardRequest, originAllowList } from "../../src/kit/guard";
import { normalizeEmail } from "../../src/kit/input";
import { verifyToken } from "../../src/kit/token";
import {
  CANARY,
  INQUIRY_GOOD,
  KEY,
  forgeToken,
  gateReq,
  goodClaims,
  inquiryReq,
  insertLead,
  installNetwork,
  makeEnv,
  resetState,
  send,
  signedCookie,
  unlock,
  viewReq,
} from "./harness";

beforeEach(async () => {
  await resetState();
});

describe("budgets: arguments no route passes", () => {
  it("a claim key that is not ts- and 40 hex digits is refused, however it is spelled", async () => {
    const env = { DB: testEnv.DB } as never;
    for (const bad of ["", "ts-", `ts-${"a".repeat(39)}`, `ts-${"a".repeat(41)}`, `ts-${"G".repeat(40)}`, `TS-${"a".repeat(40)}`, `x-${"a".repeat(40)}`, `ts-${"a".repeat(40)}\n`, "route-gate"]) {
      await expect(claimGateUnit(env, bad, "2026-11-09", Date.UTC(2026, 10, 9, 12)), JSON.stringify(bad)).rejects.toThrow(RangeError);
    }
    expect(await testEnv.DB.prepare("SELECT COUNT(*) AS n FROM counters").first<{ n: number }>()).toEqual({ n: 0 });
  });

  it("an unknown route name gets no budget and writes nothing", async () => {
    const env = { DB: testEnv.DB } as never;
    for (const name of ["send", "link", "", "VIEW", "view ", "__proto__"]) {
      expect(await reserveRoute(env, name as never, Date.UTC(2026, 10, 9, 12)), name).toBe(false);
    }
    expect(await testEnv.DB.prepare("SELECT COUNT(*) AS n FROM counters").first<{ n: number }>()).toEqual({ n: 0 });
  });
});

describe("guard: a broken origin hook never lets a state-changing request in", () => {
  const post = (): Request => new Request("https://links.example.test/k/x", { method: "POST", headers: { origin: "https://links.example.test" } });
  const env = { KIT_KILL: undefined };

  it("a hook that throws, returns a non-true value or is missing refuses a POST with 403", () => {
    const hooks: unknown[] = [
      () => {
        throw new Error("boom");
      },
      () => "yes",
      () => 1,
      () => undefined,
      undefined,
    ];
    for (const originCheck of hooks) {
      const d = guardRequest(post(), env, { methods: ["POST"], originCheck: originCheck as never });
      expect(d.allow).toBe(false);
      if (!d.allow) expect(d.response.status).toBe(403);
    }
  });

  it("the allow-list passes the exact origin and nothing that only starts with it", () => {
    const check = originAllowList(["https://links.example.test"]);
    expect(check(post())).toBe(true);
    expect(check(new Request("https://links.example.test/", { method: "POST", headers: { origin: "https://links.example.test.evil.example" } }))).toBe(false);
    expect(check(new Request("https://links.example.test/", { method: "POST" }))).toBe(false);
  });

  it("a method outside the allow-list is 405 with an Allow header", () => {
    const d = guardRequest(new Request("https://links.example.test/k/x", { method: "DELETE" }), env, { methods: ["GET", "POST"], originCheck: () => true });
    expect(d.allow).toBe(false);
    if (!d.allow) {
      expect(d.response.status).toBe(405);
      expect(d.response.headers.get("allow")).toBe("GET, POST");
    }
  });
});

describe("addresses: the grammar limits that no gate fixture reaches", () => {
  const label = (n: number): string => "a".repeat(n);

  it("an address over 254 characters is refused even when each part is within its own limit", () => {
    const local = label(64);
    const domain = [label(60), label(60), label(60), label(8), "com"].join(".");
    const addr = `${local}@${domain}`;
    expect(addr.length).toBeGreaterThan(254);
    expect(normalizeEmail(addr)).toBeNull();
    expect(normalizeEmail(`${local}@${[label(60), label(60), label(40), "com"].join(".")}`)).not.toBeNull();
  });

  it("a domain with one label is refused (a bare host name is not an address)", () => {
    for (const a of ["pat@localhost", "pat@intranet", "pat@com"]) expect(normalizeEmail(a), a).toBeNull();
    expect(normalizeEmail("pat@example.com")).toBe("pat@example.com");
  });
});

describe("tokens: a key that is too short never verifies anything", () => {
  it("verifyToken ignores a short key in the list, and a list of only short keys verifies nothing", async () => {
    const short = "short-key-0123456789";
    const t = await forgeToken(JSON.stringify(goodClaims()), short);
    const now = Math.floor(Date.now() / 1000);
    expect(await verifyToken([short], t, now)).toBeNull();
    expect(await verifyToken([KEY, short], t, now)).toBeNull();
    expect(await verifyToken(short, t, now)).toBeNull();
    const ok = await forgeToken(JSON.stringify(goodClaims()), KEY);
    expect(await verifyToken([short, KEY], ok, now)).not.toBeNull();
  });
});

describe("the page and the form: methods and bodies", () => {
  it("HEAD with a valid cookie is 200 with the page's headers and no body", async () => {
    const net = installNetwork();
    const cookie = await unlock().finally(() => net.restore());
    const head = await send(viewReq({ method: "HEAD", cookie }));
    const get = await send(viewReq({ cookie }));
    expect(head.status).toBe(200);
    expect(await head.text()).toBe("");
    expect(await get.text()).toContain(CANARY);
    for (const h of ["cache-control", "content-security-policy", "x-robots-tag", "content-type"]) {
      expect(head.headers.get(h), h).toBe(get.headers.get(h));
    }
  });

  it("POST to the two result pages is 405 and sends nothing", async () => {
    const net = installNetwork();
    try {
      await insertLead();
      for (const path of ["/k/inquiry/sent", "/k/inquiry/error"]) {
        const res = await send(inquiryReq({ path, cookie: await signedCookie() }));
        expect(res.status, path).toBe(405);
        expect(res.headers.get("allow")).toBe("GET, HEAD");
      }
      expect(net.calls).toEqual([]);
    } finally {
      net.restore();
    }
  });

  it("an inquiry that declares one byte over the 16 KiB cap is 413 before its body is read", async () => {
    await insertLead();
    const res = await send(inquiryReq({ cookie: await signedCookie(), headers: { "content-length": String(16 * 1024 + 1) }, fields: INQUIRY_GOOD }));
    expect(res.status).toBe(413);
    const lenient = await send(inquiryReq({ cookie: await signedCookie(), headers: { "content-length": "not-a-number" } }));
    expect(lenient.status).toBe(400);
  });

  it("the unlock page and the form share one environment: both are closed by the same kill switch", async () => {
    const env = makeEnv({ KIT_KILL: "on" });
    expect((await send(inquiryReq({ method: "GET", path: "/k/inquiry/sent" }), env)).status).toBe(503);
  });

  it("every killed inquiry route gives the one complete killed answer: fixed page, Retry-After, no-store, CSP with frame protection, content type", async () => {
    const env = makeEnv({ KIT_KILL: "1" });
    const requests = [
      inquiryReq({ cookie: await signedCookie() }),
      inquiryReq({ method: "GET", path: "/k/inquiry/sent" }),
      inquiryReq({ method: "GET", path: "/k/inquiry/error" }),
      inquiryReq({ method: "HEAD", path: "/k/inquiry/sent" }),
      gateReq(),
      viewReq({ cookie: await signedCookie() }),
    ];
    for (const request of requests) {
      const label = `${request.method} ${new URL(request.url).pathname}`;
      const res = await send(request, env);
      expect(res.status, label).toBe(503);
      expect(res.headers.get("retry-after"), label).toBe("3600");
      expect(res.headers.get("cache-control"), label).toBe("private, no-store");
      expect(res.headers.get("content-type"), label).toBe("text/html; charset=utf-8");
      expect(res.headers.get("content-security-policy"), label).toBe("default-src 'none'; frame-ancestors 'none'");
      expect(res.headers.get("x-content-type-options"), label).toBe("nosniff");
      if (request.method !== "HEAD") expect(await res.text(), label).toBe("<!doctype html><title>Unavailable</title><p>Temporarily unavailable.");
    }
  });
});
