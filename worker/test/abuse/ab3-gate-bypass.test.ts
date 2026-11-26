// SPDX-License-Identifier: Apache-2.0
//
// AB-3 (gate bypass) and method confusion, end to end through worker.fetch: no route, method, path
// spelling or credential placement shows the gated page without a valid unlock cookie.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { COOKIE_NAME } from "../../src/kit/token";
import {
  CANARY,
  EMAIL,
  ORIGIN,
  count,
  countingDb,
  cookiePair,
  everything,
  gateReq,
  insertLead,
  installNetwork,
  kv,
  limiter,
  makeEnv,
  resetState,
  send,
  signedCookie,
  unlock,
  viewReq,
  type Net,
} from "./harness";

let net: Net;
beforeEach(async () => {
  await resetState();
  net = installNetwork();
});
afterEach(() => net.restore());

async function noCanary(res: Response): Promise<void> {
  expect(await everything(res)).not.toContain(CANARY);
}

describe("AB-3: the gated page needs the unlock cookie and nothing else", () => {
  it("control: a real unlock opens the page, and the page carries the lockdown headers", async () => {
    const cookie = await unlock();
    const res = await send(viewReq({ cookie }));
    expect(res.status).toBe(200);
    expect(await res.text()).toContain(CANARY);
    expect(res.headers.get("cache-control")).toBe("private, no-store");
    expect(res.headers.get("x-content-type-options")).toBe("nosniff");
    expect(res.headers.get("x-robots-tag")).toBe("noindex");
    expect(res.headers.get("referrer-policy")).toBe("no-referrer");
    const csp = res.headers.get("content-security-policy") ?? "";
    expect(csp).toContain("default-src 'none'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("form-action 'self'");
    expect(csp).toContain("base-uri 'none'");
  });

  for (const method of ["GET", "HEAD"]) {
    it(`${method} /k/view with no cookie is the fixed redirect and never the page`, async () => {
      const res = await send(viewReq({ method }));
      expect(res.status).toBe(303);
      expect(res.headers.get("location")).toBe("/kit/");
      expect(res.headers.get("set-cookie")).toBeNull();
      await noCanary(res);
    });
  }

  for (const method of ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"]) {
    it(`${method} /k/view is an empty 404 even with a valid cookie`, async () => {
      const cookie = await unlock();
      const res = await send(viewReq({ method, cookie, body: method === "OPTIONS" ? undefined : "x=1" }));
      expect(res.status).toBe(404);
      expect(await res.text()).toBe("");
      await noCanary(res);
    });
  }

  for (const method of ["GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"]) {
    it(`${method} /k/gate is an empty 404 and unlocks nothing`, async () => {
      const res = await send(gateReq({ method, body: method === "PUT" || method === "PATCH" ? "email=x" : undefined }));
      expect(res.status).toBe(404);
      expect(await res.text()).toBe("");
      expect(res.headers.get("set-cookie")).toBeNull();
      expect(net.calls).toHaveLength(0);
    });
  }

  // The page must never be served from a path that merely looks like it.
  const SPELLINGS = [
    "/k/view/",
    "/k/view/.",
    "/k//view",
    "//k/view",
    "/K/view",
    "/k/VIEW",
    "/k/view%2f",
    "/k/view%00",
    "/k/view;x=1",
    "/k/view/../view/x",
    "/k/./view/y",
    "/k/gate/../view/z",
    "/k/%76iew/q",
    "/kit/view",
    "/k/view.html",
  ];
  for (const path of SPELLINGS) {
    it(`path spelling ${JSON.stringify(path)} never serves the page, even with a valid cookie`, async () => {
      const cookie = await unlock();
      const res = await send(viewReq({ path, cookie }));
      await noCanary(res);
      expect([200, 204, 303, 404]).toContain(res.status);
      if (res.status === 200) throw new Error("a non-route path answered 200");
    });
  }

  it("a token in the query string, a header or a form body is ignored", async () => {
    const cookie = await unlock();
    const token = cookie.slice(COOKIE_NAME.length + 1);
    const asHeaders: Record<string, string>[] = [
      { authorization: `Bearer ${token}` },
      { "x-kit-token": token },
      { "x-token": token },
      { "x-forwarded-cookie": cookie },
      { "set-cookie": cookie },
      { cookie: `ra_kit=${token}` }, // wrong name: no __Host- prefix
      { cookie: `__host-ra_kit=${token}` }, // wrong case
      { cookie: `x${COOKIE_NAME}=${token}` }, // name that merely ends with the real one
      { cookie: `${COOKIE_NAME}x=${token}` }, // name that merely starts with it
      { cookie: `${COOKIE_NAME}="${token}"` }, // quoted value
      { cookie: `${COOKIE_NAME}=${token}x` }, // value with a changed tail
      { cookie: `${COOKIE_NAME}= ${token}` }, // a space inside the value
    ];
    for (const h of asHeaders) {
      const res = await send(viewReq({ headers: h }));
      expect(res.status, JSON.stringify(Object.keys(h))).toBe(303);
      await noCanary(res);
    }
    for (const q of [`?${COOKIE_NAME}=${token}`, `?token=${token}`, `?t=${token}`, `?cookie=${encodeURIComponent(cookie)}`]) {
      const res = await send(viewReq({ path: `/k/view${q}` }));
      expect(res.status).toBe(303);
      await noCanary(res);
    }
    const res = await send(viewReq({ method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" }, body: `token=${token}` }));
    expect(res.status).toBe(404);
    await noCanary(res);
  });

  it("two unlock cookies in one request are refused, whatever their order", async () => {
    const good = await unlock();
    const bad = `${good.slice(0, -2)}xx`;
    for (const cookie of [`${good}; ${bad}`, `${bad}; ${good}`, `${good}; ${good}`]) {
      const res = await send(viewReq({ headers: { cookie } }));
      expect(res.status, cookie.slice(0, 30)).toBe(303);
      await noCanary(res);
    }
  });

  it("the cookie opens the page when it sits among other cookies (positive control for the table above)", async () => {
    const cookie = await unlock();
    const res = await send(viewReq({ headers: { cookie: `a=1; ${cookie}; b=2` } }));
    expect(res.status).toBe(200);
  });

  it("an oversized cookie header is refused without being parsed into the page", async () => {
    const res = await send(viewReq({ headers: { cookie: `${COOKIE_NAME}=${"A".repeat(5000)}` } }));
    expect(res.status).toBe(303);
    await noCanary(res);
  });

  it("other paths under /k/ get the Worker's empty answer and never the page", async () => {
    for (const path of ["/k/", "/k/send", "/k/v/abc", "/k/v/", "/k/admin", "/k/kit/v1", "/k/view/kit/v1"]) {
      for (const method of ["GET", "POST"]) {
        const res = await send(viewReq({ path, method, cookie: await unlock() }));
        expect(res.status, `${method} ${path}`).toBe(204);
        expect(await res.text()).toBe("");
      }
    }
  });
});

describe("AB-3: every gate and view response is private and uncacheable", () => {
  const BAD_CACHE = /public|s-maxage|max-age=[1-9]/i;

  it("success, every refusal, the kill page and the 404s", async () => {
    const good = await unlock();
    const cases: [string, Request, ReturnType<typeof makeEnv>?][] = [
      ["gate success", gateReq({ fields: { email: "two@example.com", "cf-turnstile-response": "ok2" } })],
      ["gate refusal", gateReq({ fields: { email: EMAIL, "cf-turnstile-response": "nope" } })],
      ["gate wrong origin", gateReq({ headers: { origin: "https://evil.example" } })],
      ["gate wrong method", gateReq({ method: "GET" })],
      ["view page", viewReq({ cookie: good })],
      ["view head", viewReq({ cookie: good, method: "HEAD" })],
      ["view refusal", viewReq()],
      ["view wrong method", viewReq({ method: "DELETE" })],
      ["gate killed", gateReq(), makeEnv({ KIT_KILL: "1" })],
      ["view killed", viewReq({ cookie: good }), makeEnv({ KIT_KILL: "1" })],
      ["gate misconfigured", gateReq(), makeEnv({ TOKEN_HMAC_KEY: "short" })],
      ["view misconfigured", viewReq({ cookie: good }), makeEnv({ TOKEN_HMAC_KEY: "short" })],
    ];
    for (const [name, request, env] of cases) {
      const res = await send(request, env ?? makeEnv());
      const cc = res.headers.get("cache-control") ?? "";
      expect(cc, name).toContain("no-store");
      expect(cc, name).not.toMatch(BAD_CACHE);
    }
  });
});

describe("AB-3: the kill switch closes everything, per request, for free", () => {
  const ENGAGED = ["1", "true", "TRUE", "on", "yes", "Yes", "maybe", "2", " 1", "1 ", "enabled"];
  for (const value of ENGAGED) {
    it(`KIT_KILL=${JSON.stringify(value)} answers 503 on the gate and the view, with no database, KV, vendor or alert call`, async () => {
      const cookie = await unlock();
      const { db, sql } = countingDb();
      const page = kv();
      const env = makeEnv({ KIT_KILL: value, DB: db, KIT: page });
      const before = net.calls.length;
      for (const request of [gateReq(), viewReq({ cookie }), viewReq({ method: "HEAD", cookie }), gateReq({ method: "GET" })]) {
        const res = await send(request, env);
        expect(res.status).toBe(503);
        expect(res.headers.get("retry-after")).toBe("3600");
        expect(res.headers.get("cache-control")).toBe("private, no-store");
        const body = await res.text();
        expect(body).not.toContain(CANARY);
        expect(body).not.toContain("KIT_KILL");
        expect(body.length).toBeLessThan(100);
      }
      expect(sql).toEqual([]);
      expect(page.get).not.toHaveBeenCalled();
      expect(net.calls.length).toBe(before);
    });
  }

  it("flips on the next request in both directions (nothing is cached at load)", async () => {
    const cookie = await unlock();
    const env = makeEnv();
    expect((await send(viewReq({ cookie }), env)).status).toBe(200);
    env.KIT_KILL = "1";
    expect((await send(viewReq({ cookie }), env)).status).toBe(503);
    env.KIT_KILL = "0";
    expect((await send(viewReq({ cookie }), env)).status).toBe(200);
    env.KIT_KILL = "off";
    expect((await send(viewReq({ cookie }), env)).status).toBe(200);
    delete env.KIT_KILL;
    expect((await send(viewReq({ cookie }), env)).status).toBe(200);
  });
});

describe("AB-3: origin on the gate POST is an exact match", () => {
  const BAD = [
    "https://evil.example",
    "null",
    "",
    `${ORIGIN}.evil.example`,
    `${ORIGIN}/`,
    `${ORIGIN}:8443`,
    `http://links.example.test`,
    `https://LINKS.example.test`,
    `https://x.links.example.test`,
    `${ORIGIN} https://evil.example`,
  ];
  for (const origin of BAD) {
    it(`Origin ${JSON.stringify(origin)} gets the one refusal and writes nothing`, async () => {
      const { db, sql } = countingDb();
      const res = await send(gateReq({ headers: { origin } }), makeEnv({ DB: db }));
      expect(res.status).toBe(303);
      expect(res.headers.get("location")).toBe("/kit/retry/");
      expect(res.headers.get("set-cookie")).toBeNull();
      expect(sql).toEqual([]);
      expect(net.siteverify()).toBe(0);
    });
  }

  it("a request with no Origin header at all is refused", async () => {
    const res = await send(gateReq({ headers: { origin: null } }));
    expect(res.headers.get("location")).toBe("/kit/retry/");
    expect(res.headers.get("set-cookie")).toBeNull();
  });

  it("no SITE_HOST configured means no origin can match: the gate refuses everyone", async () => {
    for (const host of [undefined, ""]) {
      const res = await send(gateReq(), makeEnv({ SITE_HOST: host }));
      expect(res.headers.get("location")).toBe("/kit/retry/");
      expect(res.headers.get("set-cookie")).toBeNull();
    }
  });
});

describe("AB-3: a valid-looking cookie for nobody opens nothing", () => {
  it("a validly signed cookie whose lead does not exist is refused and spends no view budget", async () => {
    const cookie = await signedCookie();
    const res = await send(viewReq({ cookie }));
    expect(res.status).toBe(303);
    await noCanary(res);
    expect(await count("counters", "WHERE key LIKE 'route-view%'")).toBe(0);
  });

  it("the same cookie opens once the lead exists (control)", async () => {
    await insertLead();
    const res = await send(viewReq({ cookie: await signedCookie() }));
    expect(res.status).toBe(200);
  });

  it("a cookie pair taken from a response is never itself the page and is not echoed back", async () => {
    const res = await send(gateReq());
    const pair = cookiePair(res);
    const body = await everything(res);
    expect(body).not.toContain(CANARY);
    expect(res.headers.get("location")).not.toContain(pair.slice(COOKIE_NAME.length + 1));
  });
});

describe("AB-3: limiter bindings of the view route", () => {
  it("a client the view limiter refuses never sees the page, and the answer is the usual redirect", async () => {
    const cookie = await unlock();
    const res = await send(viewReq({ cookie }), makeEnv({ KIT_VIEW_LIMITER: limiter(false) }));
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("/kit/");
    await noCanary(res);
  });
});
