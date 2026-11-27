// SPDX-License-Identifier: Apache-2.0
//
// AB-4: forged, tampered, expired, replayed and revoked unlock cookies, end to end on GET /k/view
// (and on the inquiry form, which shares the session check). A cookie that is refused must get the
// one fixed redirect, never the page, and must not spend the shared view budget.
import { env as testEnv } from "cloudflare:workers";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { handleGate, VIEW_ROUTE } from "../../src/kit/gate";
import { setSessionFloor, setUnstoredSessionFloor } from "../../src/kit/leads";
import { COOKIE_NAME, MAX_TOKEN_AGE_SECONDS, signToken } from "../../src/kit/token";
import { mintUnstoredId } from "../../src/kit/unstored";
import {
  CANARY,
  KEY,
  LEAD_ID,
  OTHER_KEY,
  count,
  everything,
  forgeToken,
  goodClaims,
  inquiryReq,
  insertLead,
  installNetwork,
  makeEnv,
  nowSec,
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
  await insertLead();
});
afterEach(() => net.restore());

const OTHER_LEAD = "b".repeat(32);

async function viewUnits(): Promise<number> {
  const row = await testEnv.DB.prepare("SELECT COALESCE(SUM(count), 0) AS n FROM counters WHERE key = 'route-view'").first<{ n: number }>();
  return row!.n;
}
async function refused(cookie: string, why: string): Promise<void> {
  const before = await viewUnits();
  const res = await send(viewReq({ headers: { cookie } }));
  expect(res.status, why).toBe(303);
  expect(res.headers.get("location"), why).toBe("/kit/");
  expect(await everything(res), why).not.toContain(CANARY);
  expect(await viewUnits(), `${why}: spent view budget`).toBe(before);
}
async function opens(cookie: string, why: string): Promise<void> {
  const res = await send(viewReq({ headers: { cookie } }));
  expect(res.status, why).toBe(200);
  expect(await res.text(), why).toContain(CANARY);
}

describe("AB-4: tampering with a real token", () => {
  it("control: an untouched token opens the page", async () => {
    await opens(await signedCookie(), "untouched");
  });

  it("an altered payload keeps the old signature and is refused (lead id, expiry, country, issue time)", async () => {
    await insertLead(OTHER_LEAD, "other@example.com");
    const [v, payload, sig] = (await signedCookie()).slice(COOKIE_NAME.length + 1).split(".") as [string, string, string];
    const claims = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/"))) as Record<string, unknown>;
    const edits: Record<string, unknown>[] = [
      { ...claims, lid: OTHER_LEAD },
      { ...claims, exp: (claims.exp as number) + 100_000 },
      { ...claims, iat: (claims.iat as number) + 1 },
      { ...claims, cc: "FR" },
    ];
    for (const e of edits) {
      const p = btoa(JSON.stringify(e)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
      await refused(`${COOKIE_NAME}=${v}.${p}.${sig}`, JSON.stringify(e).slice(0, 40));
    }
  });

  it("a flipped bit anywhere in the signature is refused", async () => {
    const token = (await signedCookie()).slice(COOKIE_NAME.length + 1);
    const i = token.lastIndexOf(".") + 1;
    for (const at of [i, i + 10, token.length - 2]) {
      const flipped = token.slice(0, at) + (token[at] === "A" ? "B" : "A") + token.slice(at + 1);
      await refused(`${COOKIE_NAME}=${flipped}`, `flip at ${at - i}`);
    }
  });

  it("truncated, empty, extra and missing segments are refused", async () => {
    const token = (await signedCookie()).slice(COOKIE_NAME.length + 1);
    const [v, p, s] = token.split(".") as [string, string, string];
    const shapes = [`${v}.${p}.`, `${v}.${p}`, `${v}..${s}`, `.${p}.${s}`, `${v}.${p}.${s}.x`, `${v}.${p}.${s.slice(0, -4)}`, `${p}.${s}`, v, `${v}.${p}.${s}=`];
    for (const t of shapes) await refused(`${COOKIE_NAME}=${t}`, t.slice(0, 30));
  });

  it("another version prefix is refused even when its signature is valid for that text", async () => {
    const t = await forgeToken(JSON.stringify(goodClaims()), KEY, "v2");
    await refused(`${COOKIE_NAME}=${t}`, "v2");
    const none = await forgeToken(JSON.stringify(goodClaims()), KEY, "none");
    await refused(`${COOKIE_NAME}=${none}`, "alg none style");
  });

  it("a token signed by another key, or by a key too short to be a key, is refused", async () => {
    await refused(await signedCookie({ key: OTHER_KEY }), "other key");
    await refused(`${COOKIE_NAME}=${await forgeToken(JSON.stringify(goodClaims()), "short")}`, "short key");
  });

  it("a non-canonical base64 spelling of a valid token is refused", async () => {
    let token = "";
    for (let i = 0; i < 200 && !/[-_]/.test(token.split(".")[2] ?? ""); i++) token = (await signedCookie()).slice(COOKIE_NAME.length + 1);
    const [v, p, s] = token.split(".") as [string, string, string];
    expect(s).toMatch(/[-_]/); // the loop found a signature that differs between the two alphabets
    await refused(`${COOKIE_NAME}=${v}.${p}.${s.replace(/-/g, "+").replace(/_/g, "/")}`, "standard alphabet");
    await refused(`${COOKIE_NAME}=${v}.${p}=.${s}`, "padded payload");
    // a final character that carries unused bits is a second spelling of the same bytes
    const last = s[s.length - 1]!;
    const alt = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    const sibling = alt[(alt.indexOf(last) + 1) % 64]!;
    if (sibling !== last) await refused(`${COOKIE_NAME}=${v}.${p}.${s.slice(0, -1)}${sibling}`, "sibling spelling");
  });
});

describe("AB-4: validly signed tokens with claims no signer would write", () => {
  const T = nowSec();
  const CASES: [string, () => string][] = [
    ["expiry before issue", () => JSON.stringify(goodClaims({ iat: T - 100, exp: T - 200 }))],
    ["expiry equal to issue", () => JSON.stringify(goodClaims({ iat: T - 100, exp: T - 100 }))],
    ["lifetime past the verifier cap", () => JSON.stringify(goodClaims({ iat: T - 30, exp: T - 30 + MAX_TOKEN_AGE_SECONDS + 1 }))],
    ["lifetime of ten years", () => JSON.stringify(goodClaims({ iat: T - 30, exp: T + 315_360_000 }))],
    ["fractional issue time", () => JSON.stringify(goodClaims({ iat: T - 30.5 }))],
    ["string issue time", () => JSON.stringify(goodClaims({ iat: String(T - 30) }))],
    ["unsafe integer expiry", () => JSON.stringify(goodClaims({ exp: 2 ** 60 }))],
    ["lead id with a path", () => JSON.stringify(goodClaims({ lid: "../../etc" }))],
    ["lead id with SQL", () => JSON.stringify(goodClaims({ lid: "x' OR '1'='1" }))],
    ["lead id of 65 characters", () => JSON.stringify(goodClaims({ lid: "a".repeat(65) }))],
    ["empty lead id", () => JSON.stringify(goodClaims({ lid: "" }))],
    ["numeric lead id", () => JSON.stringify(goodClaims({ lid: 123 }))],
    ["lowercase country", () => JSON.stringify(goodClaims({ cc: "us" }))],
    ["three letter country", () => JSON.stringify(goodClaims({ cc: "USA" }))],
    ["missing country", () => JSON.stringify((({ cc: _cc, ...rest }) => rest)(goodClaims()))],
    ["missing token id", () => JSON.stringify((({ jti: _j, ...rest }) => rest)(goodClaims()))],
    ["short token id", () => JSON.stringify(goodClaims({ jti: "abc" }))],
    ["token id with a space", () => JSON.stringify(goodClaims({ jti: "a".repeat(21) + " " }))],
    ["payload is an array", () => JSON.stringify([goodClaims()])],
    ["payload is null", () => "null"],
    ["payload is not JSON", () => "not json at all"],
    ["payload is empty", () => ""],
    ["payload with invalid UTF-8", () => "{\"lid\":\"\udc80\"}"],
  ];
  for (const [name, make] of CASES) {
    it(`refuses ${name}`, async () => {
      await refused(`${COOKIE_NAME}=${await forgeToken(make())}`, name);
    });
  }

  it("control: well-formed claims with a valid signature open the page", async () => {
    await opens(`${COOKIE_NAME}=${await forgeToken(JSON.stringify(goodClaims()))}`, "control");
  });
});

describe("AB-4: expiry, issue time and replay", () => {
  it("a token one second past its expiry, and one exactly at it, are refused; one second before opens", async () => {
    const now = nowSec();
    await refused(await signedCookie({ iat: now - 3601, exp: now - 1 }), "1s past");
    await refused(await signedCookie({ iat: now - 3600, exp: now }), "at expiry");
    await opens(await signedCookie({ iat: now - 3600, exp: now + 30 }), "30s left");
  });

  it("a token issued in the future beyond the skew allowance is refused; a few seconds ahead is tolerated", async () => {
    const now = nowSec();
    await refused(await signedCookie({ iat: now + 600, exp: now + 4200 }), "10 minutes ahead");
    await refused(await signedCookie({ iat: now + 3600, exp: now + 7200 }), "1 hour ahead");
    await refused(await signedCookie({ iat: now + 120, exp: now + 3720 }), "2 minutes ahead");
    await opens(await signedCookie({ iat: now + 5, exp: now + 3605 }), "5s ahead");
  });

  it("the gate's own cookie stops working at its lifetime to the second (clock supplied by the test)", async () => {
    const T0 = Date.UTC(2026, 10, 9, 14, 3, 30);
    const env = makeEnv();
    const gateRes = await handleGate(
      new Request("https://links.example.test/k/gate", {
        method: "POST",
        headers: {
          "content-type": "application/x-www-form-urlencoded",
          origin: "https://links.example.test",
          "cf-connecting-ip": "203.0.113.50",
          "user-agent": "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
        },
        body: new URLSearchParams({ email: "late@example.com", "cf-turnstile-response": "ok-late" }).toString(),
      }),
      env as never,
      { nowMs: T0, fetchFn: (async () => new Response(JSON.stringify({ success: true, hostname: "links.example.test", action: "gate", challenge_ts: new Date(T0).toISOString(), "error-codes": [] }), { status: 200 })) as typeof fetch },
    );
    const cookie = gateRes.headers.get("set-cookie")!.split(";")[0]!;
    const seeView = async (atMs: number): Promise<number> =>
      (await handleGate(new Request(`https://links.example.test${VIEW_ROUTE}`, { headers: { cookie, "cf-connecting-ip": "203.0.113.50" } }), makeEnv() as never, { nowMs: atMs })).status;
    expect(await seeView(T0 + 1000)).toBe(200);
    expect(await seeView(T0 + 86_400_000 - 1000)).toBe(200);
    expect(await seeView(T0 + 86_400_000)).toBe(303);
    expect(await seeView(T0 + 86_400_000 + 5000)).toBe(303);
  });

  it("replaying a valid cookie many times keeps working until it expires, and each replay is a separate view", async () => {
    const cookie = await signedCookie();
    for (let i = 0; i < 5; i++) await opens(cookie, `replay ${i}`);
    expect(await count("counters", "WHERE key = 'route-view'")).toBe(1); // one hourly bucket row
  });
});

describe("AB-4: revocation and deleted leads", () => {
  it("a cookie for a deleted lead is refused, and so is one for a lead that never existed", async () => {
    const cookie = await signedCookie();
    await opens(cookie, "before delete");
    await testEnv.LEADS_DB.prepare("DELETE FROM leads WHERE id = ?1").bind(LEAD_ID).run();
    await refused(cookie, "after delete");
    await refused(await signedCookie({ lid: "c".repeat(32) }), "never existed");
  });

  it("a lead's session floor ends older cookies, including one issued in the floor's own second, and not newer ones", async () => {
    const t = nowSec();
    const old = await signedCookie({ iat: t - 100, exp: t + 3000 });
    const same = await signedCookie({ iat: t - 10, exp: t + 3000 });
    const fresh = await signedCookie({ iat: t - 5, exp: t + 3000 });
    await setSessionFloor(testEnv.LEADS_DB, LEAD_ID, (t - 10) * 1000 + 500);
    await refused(old, "older than the floor");
    await refused(same, "same second as the floor");
    await opens(fresh, "issued after the floor");
  });

  it("a stored floor that is not a plain non-negative integer fails closed", async () => {
    // STRICT tables refuse text, so the shapes that can occur are tested at the session layer (session.test.ts);
    // here the end-to-end part: a revoked lead that passes the gate again gets a working new cookie.
    const t = nowSec();
    await setSessionFloor(testEnv.LEADS_DB, LEAD_ID, (t + 1) * 1000);
    await refused(await signedCookie({ iat: t - 5, exp: t + 3000 }), "floor in the future of the cookie");
  });

  it("an unstored unlock is ended by the kit-wide floor and not by a lead lookup", async () => {
    const unstored = await mintUnstoredId(KEY);
    const t = nowSec();
    const cookie = await signedCookie({ lid: unstored, iat: t - 20, exp: t + 3000 });
    await opens(cookie, "unstored before the floor");
    await setUnstoredSessionFloor(testEnv.LEADS_DB, (t - 20) * 1000);
    await refused(cookie, "unstored at the floor");
    await opens(await signedCookie({ lid: unstored, iat: t - 5, exp: t + 3000 }), "unstored after the floor");
  });

  it("an id that merely looks like an unstored id (32 hex, wrong tag) is looked up as a lead and refused", async () => {
    const t = nowSec();
    await refused(await signedCookie({ lid: "0123456789abcdef".repeat(2), iat: t - 5, exp: t + 3000 }), "forged unstored id");
    const real = await mintUnstoredId(KEY);
    const forged = real.slice(0, 16) + (real[16] === "0" ? "1" : "0") + real.slice(17);
    await refused(await signedCookie({ lid: forged, iat: t - 5, exp: t + 3000 }), "one nibble off");
  });

  it("the inquiry form refuses the same revoked, deleted and forged cookies", async () => {
    const t = nowSec();
    const attempts: [string, string][] = [
      ["forged signature", await signedCookie({ key: OTHER_KEY })],
      ["no lead", await signedCookie({ lid: "d".repeat(32) })],
      ["expired", await signedCookie({ iat: t - 7200, exp: t - 3600 })],
    ];
    await setSessionFloor(testEnv.LEADS_DB, LEAD_ID, (t + 10) * 1000);
    attempts.push(["revoked", await signedCookie({ iat: t - 5, exp: t + 3000 })]);
    for (const [why, cookie] of attempts) {
      const res = await send(inquiryReq({ cookie }));
      expect(res.status, why).toBe(303);
      expect(res.headers.get("location"), why).toBe("/kit/retry/");
      expect(net.sink(), why).toHaveLength(0);
    }
  });
});

describe("AB-4: key rotation", () => {
  it("a cookie under the previous key works while that key is configured and stops when it is dropped", async () => {
    const cookie = await signedCookie({ key: OTHER_KEY });
    await refused(cookie, "no previous key configured");
    const res = await send(viewReq({ headers: { cookie } }), makeEnv({ TOKEN_HMAC_KEY_PREVIOUS: OTHER_KEY }));
    expect(res.status).toBe(200);
  });

  it("new unlocks are signed with the current key only, so the previous key alone never verifies them", async () => {
    const cookie = await unlock(makeEnv({ TOKEN_HMAC_KEY_PREVIOUS: OTHER_KEY }));
    const res = await send(viewReq({ headers: { cookie } }), makeEnv({ TOKEN_HMAC_KEY: OTHER_KEY, TOKEN_HMAC_KEY_PREVIOUS: undefined }));
    expect(res.status).toBe(303);
  });

  it("a configured previous key that is too short closes the route for everyone (nobody is let in on one weak key)", async () => {
    const cookie = await signedCookie();
    const res = await send(viewReq({ headers: { cookie } }), makeEnv({ TOKEN_HMAC_KEY_PREVIOUS: "tiny" }));
    expect(res.status).toBe(303);
    expect(await everything(res)).not.toContain(CANARY);
  });

  it("a signing key under 32 bytes refuses to sign at all", async () => {
    await expect(signToken("k".repeat(31), { lid: LEAD_ID, iat: 1, exp: 100, cc: "" })).rejects.toThrow();
  });
});
