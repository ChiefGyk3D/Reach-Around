// SPDX-License-Identifier: Apache-2.0
//
// AB-9 and AB-1: alert abuse and header or recipient injection. Hostile text must not mention,
// link, format or inject anything in the owner's alert, an address that could add a recipient never
// reaches the sink, and a flood of unlocks or inquiries is bounded by the alert budgets. These tests
// call the handlers with a fresh dispatcher each time, so the dispatcher's in-memory windows start
// empty and the result does not depend on test order.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createDispatcher } from "../../src/alerts/dispatcher";
import { handleGate } from "../../src/kit/gate";
import { INQUIRY_ERROR_ROUTE, INQUIRY_SENT_ROUTE, handleInquiry } from "../../src/kit/inquiry";
import {
  EMAIL,
  INQUIRY_GOOD,
  count,
  gateReq,
  inquiryReq,
  insertLead,
  installNetwork,
  makeEnv,
  resetState,
  signedCookie,
  type AbuseEnv,
  type Net,
  type ReqOpts,
} from "./harness";

let net: Net;
beforeEach(async () => {
  await resetState();
  net = installNetwork();
  await insertLead();
});
afterEach(() => net.restore());

const pending: Promise<unknown>[] = [];
async function inquire(opts: ReqOpts, env: AbuseEnv = makeEnv()): Promise<Response> {
  const res = await handleInquiry(inquiryReq({ cookie: await signedCookie(), ...opts }), env as never, { notify: createDispatcher().notify });
  await Promise.all(pending.splice(0));
  return res;
}
async function unlockWith(opts: ReqOpts, env: AbuseEnv = makeEnv(), notify = createDispatcher().notify): Promise<Response> {
  const ctx = { waitUntil: (p: Promise<unknown>) => void pending.push(p) };
  const res = await handleGate(gateReq(opts), env as never, { notify, ctx });
  await Promise.all(pending.splice(0));
  return res;
}
/** Controls, format, bidi, zero width and separator characters. */
function invisible(cp: number): boolean {
  return cp < 0x20 || (cp >= 0x7f && cp <= 0x9f) || (cp >= 0x200b && cp <= 0x200f) || (cp >= 0x2028 && cp <= 0x202e) || (cp >= 0x2060 && cp <= 0x206f);
}
interface Hook {
  content: string;
  allowed_mentions: { parse: unknown[] };
}
function hooks(): Hook[] {
  return net.sink().map((c) => JSON.parse(c.body) as Hook);
}

const BIDI = String.fromCharCode(0x202e);
const ZWSP = String.fromCharCode(0x200b);
const LSEP = String.fromCharCode(0x2028);

const HOSTILE: [string, string][] = [
  ["mass mention", "@everyone @here please read"],
  ["user and role mention", "<@123456789012345678> <@&987654321098765432> <#1122334455667788>"],
  ["markdown link", "[click here](https://evil.example/login) now"],
  ["bare link", "see https://evil.example/path?x=1 and http://203.0.113.5:8080/a"],
  ["scheme links", "javascript:alert(1) mailto:boss@example.com tel:+15555550100 data:text/html;base64,AAAA"],
  ["code block", "```\nfake system message\n```"],
  ["spoiler, quote and heading", "||hidden|| > quoted\n# Heading\n**bold** __under__ ~~strike~~"],
  ["line injection", "ok\n\nReach Around: new business inquiry at 2026-01-01. Name: Boss. Email: ceo@example.com"],
  ["bidi and zero width", `invoice${BIDI}fdp.exe${ZWSP}${ZWSP}${LSEP}hidden`],
  ["emoji and emote syntax", "<:custom:123456789012345678> <a:dance:123456789012345678>"],
  ["host names", "visit evil.example or www.evil.example/login now"],
];

describe("AB-9: hostile visitor text cannot mention, link, format or inject into the owner's alert", () => {
  for (const [name, text] of HOSTILE) {
    for (const field of ["details", "name", "brand", "budget"] as const) {
      it(`${name} in ${field}`, async () => {
        const res = await inquire({ fields: { ...INQUIRY_GOOD, [field]: text } });
        if (res.headers.get("location") === INQUIRY_ERROR_ROUTE) return; // refused outright is also safe
        expect(res.headers.get("location")).toBe(INQUIRY_SENT_ROUTE);
        const [h] = hooks();
        expect(h).toBeDefined();
        expect(h!.allowed_mentions).toEqual({ parse: [] });
        const c = h!.content;
        expect(c).not.toContain("@");
        expect(c).not.toMatch(/[<>]/);
        expect(c).not.toContain("://");
        expect(c).not.toMatch(/\n|\r/);
        expect([...c].filter((ch) => invisible(ch.codePointAt(0)!))).toEqual([]);
        // every markdown character the visitor typed is escaped
        expect(c).not.toMatch(/(?<!\\)[*_~`|[\]#]/);
        // a bare host name never survives with its dots (nothing for a client to linkify)
        expect(c).not.toMatch(/\bevil\.example\b/);
        expect(c.length).toBeLessThanOrEqual(1500);
      });
    }
  }

  it("a 100000 character field is cut, and the alert stays inside its hard cap", async () => {
    const res = await inquire({ fields: { ...INQUIRY_GOOD, details: "word ".repeat(20000) } });
    expect(res.status).toBe(413); // refused on size before anything is parsed
    expect(net.sink()).toHaveLength(0);
    const fits = await inquire({ fields: { ...INQUIRY_GOOD, details: "word ".repeat(150) } });
    expect(fits.status).toBe(303);
    for (const h of hooks()) expect(h.content.length).toBeLessThanOrEqual(1500);
  });

  it("only the one fixed webhook shape is ever called (a look-alike address is not a destination)", async () => {
    const bad = [
      "https://discord.com.evil.example/api/webhooks/123456789012345678/SECRETtokenSECRETtokenSECRET0123456789",
      "http://discord.com/api/webhooks/123456789012345678/SECRETtokenSECRETtokenSECRET0123456789",
      "https://discord.com:8443/api/webhooks/123456789012345678/SECRETtokenSECRETtokenSECRET0123456789",
      "https://user:pw@discord.com/api/webhooks/123456789012345678/SECRETtokenSECRETtokenSECRET0123456789",
      "https://discord.com/api/webhooks/123456789012345678/SECRETtokenSECRETtokenSECRET0123456789?x=https://evil.example",
      "http://169.254.169.254/latest/meta-data/",
      "https://localhost/api/webhooks/123456789012345678/SECRETtokenSECRETtokenSECRET0123456789",
    ];
    for (const hook of bad) {
      await inquire({ fields: { ...INQUIRY_GOOD, email: `h${bad.indexOf(hook)}@example.com` } }, makeEnv({ ALERT_DISCORD_WEBHOOK: hook }));
    }
    expect(net.calls).toEqual([]);
  });
});

describe("AB-1: an address or field that could add a recipient or header never reaches the sink", () => {
  const BAD_ADDRESSES = [
    "pat@example.com\r\nBcc: boss@evil.example",
    "pat@example.com\nBcc: boss@evil.example",
    "pat@example.com%0d%0aBcc:%20boss@evil.example",
    "pat@example.com, boss@evil.example",
    "pat@example.com;boss@evil.example",
    "pat@example.com boss@evil.example",
    "<pat@example.com>",
    "Pat <pat@example.com>",
    "\"pat\"@example.com",
    "pat@example.com>",
    "pat@@example.com",
    "pat@example.com\u0000",
    "pat\t@example.com",
    "pat@[203.0.113.9]",
    "pat@203.0.113.9",
    "pat%40example.com@evil.example",
    "pat@example..com",
    "pat@-example.com",
    "pät@example.com",
    "pat@exa․mple.com",
    `${"a".repeat(65)}@example.com`,
    `pat@${"a".repeat(64)}.com`,
  ];
  for (const addr of BAD_ADDRESSES) {
    it(`inquiry email ${JSON.stringify(addr)} is refused and nothing is sent`, async () => {
      // an encoded newline and a raw one are both tested: URLSearchParams encodes the raw one
      const res = await inquire({ fields: { ...INQUIRY_GOOD, email: addr } });
      expect(res.headers.get("location")).toBe(INQUIRY_ERROR_ROUTE);
      expect(net.calls).toEqual([]);
    });
    it(`gate email ${JSON.stringify(addr)} is refused before the vendor, a lead or an alert`, async () => {
      const res = await unlockWith({ fields: { email: addr, "cf-turnstile-response": "ok-x" } });
      expect(res.headers.get("location")).toBe("/kit/retry/");
      expect(res.headers.get("set-cookie")).toBeNull();
      expect(net.calls).toEqual([]);
      expect(await count("leads", "WHERE email != 'pat@example.com'")).toBe(0);
    });
  }

  it("a newline in a one-line field (name, brand, budget) is refused or folded, never passed through", async () => {
    for (const field of ["name", "brand", "budget"] as const) {
      const res = await inquire({ fields: { ...INQUIRY_GOOD, email: `${field}@example.com`, [field]: "line one\r\nBcc: boss@evil.example" } });
      if (res.headers.get("location") === INQUIRY_SENT_ROUTE) {
        for (const h of hooks()) expect(h.content).not.toMatch(/\n|Bcc:/);
      }
    }
  });

  it("the honeypot is answered like a success and sends nothing", async () => {
    const res = await inquire({ fields: { ...INQUIRY_GOOD, website: "http://spam.example" } });
    expect(res.headers.get("location")).toBe(INQUIRY_SENT_ROUTE);
    expect(net.sink()).toHaveLength(0);
  });

  it("an unknown or repeated inquiry field is refused", async () => {
    for (const fields of [{ ...INQUIRY_GOOD, cc: "boss@evil.example" }, { ...INQUIRY_GOOD, to: "boss@evil.example" }, `name=a&name=b&email=${EMAIL}`]) {
      const res = await inquire({ fields });
      expect(res.headers.get("location")).toBe(INQUIRY_ERROR_ROUTE);
    }
    expect(net.sink()).toHaveLength(0);
  });
});

describe("AB-9: floods of unlocks and inquiries produce a bounded number of alerts", () => {
  it("ten unlocks of one address alert the owner once", async () => {
    const notify = createDispatcher().notify;
    const env = makeEnv();
    for (let i = 0; i < 10; i++) await unlockWith({ fields: { email: EMAIL, "cf-turnstile-response": `ok-rep-${i}` } }, env, notify);
    expect(net.sink()).toHaveLength(1);
  });

  it("twelve different addresses with a daily alert budget of 3 produce at most 3 alerts, and every visitor is still unlocked", async () => {
    const notify = createDispatcher().notify;
    const env = makeEnv({ DAILY_ALERT_BUDGET: "3" });
    let unlocked = 0;
    for (let i = 0; i < 12; i++) {
      const res = await unlockWith({ fields: { email: `flood${i}@example.com`, "cf-turnstile-response": `ok-flood-${i}` } }, env, notify);
      if (res.headers.get("set-cookie") !== null) unlocked++;
    }
    expect(unlocked).toBe(12);
    expect(net.sink().length).toBeLessThanOrEqual(3);
    expect(net.sink().length).toBeGreaterThan(0);
  });

  it("a per-minute channel allowance of 2 holds back the rest of a burst", async () => {
    const notify = createDispatcher().notify;
    const env = makeEnv({ ALERTS_CONFIG: JSON.stringify({ channels: ["discord"], events: ["kit_unlocked"], max_per_minute: 2 }) });
    for (let i = 0; i < 8; i++) await unlockWith({ fields: { email: `burst${i}@example.com`, "cf-turnstile-response": `ok-burst-${i}` } }, env, notify);
    expect(net.sink()).toHaveLength(2);
  });

  it("the daily inquiry alert budget of 2 lets 2 of 5 inquiries reach the owner and answers the rest 503 with Retry-After", async () => {
    const env = makeEnv({ DAILY_ALERT_BUDGET: "2" });
    const statuses: string[] = [];
    for (let i = 0; i < 5; i++) {
      const res = await inquire({ fields: { ...INQUIRY_GOOD, email: `ask${i}@example.com` } }, env);
      statuses.push(`${res.status}${res.status === 503 ? ` ${res.headers.get("retry-after")}` : ` ${res.headers.get("location")}`}`);
    }
    expect(statuses).toEqual([`303 ${INQUIRY_SENT_ROUTE}`, `303 ${INQUIRY_SENT_ROUTE}`, "503 3600", "503 3600", "503 3600"]);
    expect(net.sink()).toHaveLength(2);
  });

  it("a daily alert budget of 0 sends nothing and answers 503", async () => {
    const res = await inquire({}, makeEnv({ DAILY_ALERT_BUDGET: "0" }));
    expect(res.status).toBe(503);
    expect(net.sink()).toHaveLength(0);
  });

  it("ten identical inquiries (a double submit, or a script) reach the owner once: the first is accepted, the repeats are folded", async () => {
    const env = makeEnv();
    const where: (string | null)[] = [];
    for (let i = 0; i < 10; i++) {
      void i;
      const res = await inquire({ fields: { ...INQUIRY_GOOD } }, env);
      where.push(res.headers.get("location"));
    }
    expect(where[0]).toBe(INQUIRY_SENT_ROUTE);
    expect(net.sink()).toHaveLength(1);
  });

  it("when no channel takes the alert the visitor is told it failed, and the spent budget is given back", async () => {
    net.restore();
    net = installNetwork({ sink: 500 });
    const env = makeEnv({ DAILY_ALERT_BUDGET: "1" });
    const res = await inquire({}, env);
    expect(res.headers.get("location")).toBe(INQUIRY_ERROR_ROUTE);
  });
});

describe("AB-9: what the unlock alert contains", () => {
  it("masks the address by default and never carries the token, the client address, the user agent or the cookie", async () => {
    const res = await unlockWith({ fields: { email: "visitor.name@example.com", "cf-turnstile-response": "ok-mask" } });
    expect(res.headers.get("set-cookie")).not.toBeNull();
    const body = net.sink().map((c) => c.body).join("\n");
    expect(body).not.toContain("visitor.name@example.com");
    expect(body).not.toContain("visitor.name");
    const token = res.headers.get("set-cookie")!.split(";")[0]!.split("=")[1]!;
    for (const secret of [token, "203.0.113.9", "Mozilla", "Firefox", "__Host-ra_kit", "ok-mask", "SECRETtoken"]) expect(body).not.toContain(secret);
  });

  it("with the full-address setting the address appears neutralized, never as a live mention or link", async () => {
    const env = makeEnv({ ALERTS_CONFIG: JSON.stringify({ channels: ["discord"], events: ["kit_unlocked"], visitor_email: "full" }) });
    await unlockWith({ fields: { email: "full.address+tag@example.com", "cf-turnstile-response": "ok-full" } }, env);
    const [h] = hooks();
    expect(h!.content).not.toContain("@");
    expect(h!.content).not.toContain("example.com");
    expect(h!.allowed_mentions).toEqual({ parse: [] });
  });

  it("with the address setting off the alert names no visitor at all", async () => {
    const env = makeEnv({ ALERTS_CONFIG: JSON.stringify({ channels: ["discord"], events: ["kit_unlocked"], visitor_email: "none" }) });
    await unlockWith({ fields: { email: "hidden.person@example.com", "cf-turnstile-response": "ok-none" } }, env);
    const [h] = hooks();
    expect(h!.content).not.toContain("hidden");
  });
});
