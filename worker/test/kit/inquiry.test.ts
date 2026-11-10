// SPDX-License-Identifier: Apache-2.0
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createDispatcher } from "../../src/alerts/dispatcher";
import { formatAlert } from "../../src/alerts/format";
import {
  INQUIRY_ERROR_ROUTE,
  INQUIRY_LIMITS,
  INQUIRY_ROUTE,
  INQUIRY_SENT_ROUTE,
  handleInquiry,
  type InquiryEnv,
} from "../../src/kit/inquiry";
import { COOKIE_NAME, signToken } from "../../src/kit/token";

const HOST = "links.example.test";
const ORIGIN = `https://${HOST}`;
const KEY = "k".repeat(40);
const HOOK = "https://discord.com/api/webhooks/123456789012345678/SECRETtokenSECRETtokenSECRET0123456789";
const T0 = Date.UTC(2026, 10, 9, 14, 3, 30);
const NOW_SEC = Math.floor(T0 / 1000);

type Call = { url: string; body: string };
function mockFetch(status = 204) {
  const calls: Call[] = [];
  const fn = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), body: String(init?.body ?? "") });
    return new Response(null, { status });
  });
  return { fn: fn as unknown as typeof fetch, calls };
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

function baseEnv(over: Partial<InquiryEnv> = {}): InquiryEnv {
  return {
    DB: testEnv.DB,
    SITE_HOST: HOST,
    TOKEN_HMAC_KEY: KEY,
    INQUIRY_LIMITER: limiter(),
    ALERT_DEDUP_KEY: "d".repeat(40),
    ALERTS_CONFIG: JSON.stringify({ channels: ["discord"], events: ["inquiry_received"] }),
    ALERT_DISCORD_WEBHOOK: HOOK,
    ...over,
  } as InquiryEnv;
}

async function cookie(over: { iat?: number; exp?: number; key?: string } = {}): Promise<string> {
  const iat = over.iat ?? NOW_SEC - 60;
  const exp = over.exp ?? iat + 3600;
  return `${COOKIE_NAME}=${await signToken(over.key ?? KEY, { lid: "lead1", iat, exp, cc: "" })}`;
}

const GOOD = { name: "Pat Example", email: "pat@example.com", brand: "Example Brand", budget: "5000 USD", details: "A sponsored stream." };

interface Opts {
  fields?: Record<string, string> | string;
  headers?: Record<string, string | null>;
  method?: string;
  path?: string;
  cookie?: string | null;
}
async function req(o: Opts = {}): Promise<Request> {
  const body = typeof o.fields === "string" ? o.fields : new URLSearchParams(o.fields ?? GOOD).toString();
  const headers: Record<string, string> = {
    "content-type": "application/x-www-form-urlencoded",
    origin: ORIGIN,
    "cf-connecting-ip": "203.0.113.9",
  };
  const ck = o.cookie === undefined ? await cookie() : o.cookie;
  if (ck !== null) headers.cookie = ck;
  for (const [k, v] of Object.entries(o.headers ?? {})) {
    if (v === null) delete headers[k];
    else headers[k] = v;
  }
  const method = o.method ?? "POST";
  return new Request(`${ORIGIN}${o.path ?? INQUIRY_ROUTE}`, { method, headers, body: method === "POST" ? body : undefined });
}

async function post(o: Opts = {}, e: InquiryEnv = baseEnv(), f = mockFetch()) {
  const res = await handleInquiry(await req(o), e, { fetchFn: f.fn, nowMs: T0, notify: createDispatcher().notify });
  return { res, f };
}

const BIDI = String.fromCharCode(0x202e);
const ZWSP = String.fromCharCode(0x200b);
const LSEP = String.fromCharCode(0x2028);
const NUL = String.fromCharCode(0);
const BEL = String.fromCharCode(7);
const A_UML = String.fromCharCode(0xe4);
const EMOJI = String.fromCodePoint(0x1f600);

beforeEach(async () => {
  await testEnv.DB.batch([
    testEnv.DB.prepare("DELETE FROM alert_quota"),
    testEnv.DB.prepare("DELETE FROM alert_dedup"),
    testEnv.DB.prepare("DELETE FROM counters"),
  ]);
});

describe("gating (only a visitor who passed the gate can post)", () => {
  it("sends a visitor with no unlock cookie to the gate and delivers nothing", async () => {
    const lim = limiter();
    const { res, f } = await post({ cookie: null }, baseEnv({ INQUIRY_LIMITER: lim }));
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("/kit/retry/");
    expect(f.calls).toHaveLength(0);
    expect(lim.keys).toHaveLength(0);
  });
  it("refuses a forged, expired, wrong-key or duplicated cookie", async () => {
    const good = await cookie();
    const bad = [
      `${COOKIE_NAME}=v1.abc.def`,
      await cookie({ key: "z".repeat(40) }),
      await cookie({ iat: NOW_SEC - 7200, exp: NOW_SEC - 3600 }),
      `${good}; ${good}`,
      "other=1",
    ];
    for (const c of bad) {
      const { res, f } = await post({ cookie: c });
      expect(res.headers.get("location")).toBe("/kit/retry/");
      expect(f.calls).toHaveLength(0);
    }
  });
  it("accepts a cookie signed with the previous key during a rotation", async () => {
    const e = baseEnv({ TOKEN_HMAC_KEY: "n".repeat(40), TOKEN_HMAC_KEY_PREVIOUS: KEY });
    const { res } = await post({}, e);
    expect(res.headers.get("location")).toBe(INQUIRY_SENT_ROUTE);
  });
  it("fails closed when the signing key or the limiter binding is missing", async () => {
    for (const over of [{ TOKEN_HMAC_KEY: "" }, { INQUIRY_LIMITER: undefined }, { TOKEN_HMAC_KEY: "short" }]) {
      const { res, f } = await post({}, baseEnv(over as Partial<InquiryEnv>));
      expect(res.status).toBe(503);
      expect(f.calls).toHaveLength(0);
    }
  });
});

describe("request shape", () => {
  it("answers 503 for every request while the kit kill switch is on", async () => {
    for (const value of ["1", "true", "garbage"]) {
      const { res } = await post({}, baseEnv({ KIT_KILL: value }));
      expect(res.status).toBe(503);
    }
  });
  it("accepts POST for the form and GET only for the two fixed result pages", async () => {
    const r1 = await handleInquiry(await req({ method: "GET" }), baseEnv(), {});
    expect(r1.status).toBe(405);
    const r2 = await handleInquiry(await req({ method: "PUT", path: INQUIRY_SENT_ROUTE }), baseEnv(), {});
    expect(r2.status).toBe(405);
  });
  it("refuses a missing, null or foreign Origin", async () => {
    for (const origin of [null, "null", "https://evil.example", `${ORIGIN}.evil.example`, "http://" + HOST]) {
      const { res, f } = await post({ headers: { origin } });
      expect(res.status).toBe(403);
      expect(f.calls).toHaveLength(0);
    }
  });
  it("accepts only the urlencoded content type", async () => {
    for (const ct of ["application/json", "multipart/form-data; boundary=x", "text/plain", "application/x-www-form-urlencoded; charset=utf-8"]) {
      const { res } = await post({ headers: { "content-type": ct } });
      expect(res.status).toBe(415);
    }
  });
  it("refuses a body over the cap, declared or streamed", async () => {
    const big = `name=a&details=${"x".repeat(INQUIRY_LIMITS.maxBodyBytes + 1)}`;
    const { res, f } = await post({ fields: big });
    expect(res.status).toBe(413);
    expect(f.calls).toHaveLength(0);
  });
});

describe("fields", () => {
  const sent = (r: Response) => r.headers.get("location") === INQUIRY_SENT_ROUTE;
  const failed = (r: Response) => r.headers.get("location") === INQUIRY_ERROR_ROUTE;

  it("delivers a good inquiry once and redirects to the sent page", async () => {
    const { res, f } = await post();
    expect(res.status).toBe(303);
    expect(sent(res)).toBe(true);
    expect(f.calls).toHaveLength(1);
    expect(f.calls[0]!.url).toBe(HOOK);
  });
  it("requires a name", async () => {
    for (const name of ["", "   ", undefined]) {
      const fields: Record<string, string> = { ...GOOD };
      if (name === undefined) delete fields.name;
      else fields.name = name;
      const { res, f } = await post({ fields });
      expect(failed(res)).toBe(true);
      expect(f.calls).toHaveLength(0);
    }
  });
  it("accepts a name alone: email, brand, budget and details are optional", async () => {
    const { res, f } = await post({ fields: { name: "Pat" } });
    expect(sent(res)).toBe(true);
    expect(f.calls).toHaveLength(1);
  });
  it("enforces every size cap by characters", async () => {
    const L = INQUIRY_LIMITS;
    const cases: Array<[string, number]> = [
      ["name", L.name],
      ["brand", L.brand],
      ["budget", L.budget],
      ["details", L.details],
    ];
    for (const [field, max] of cases) {
      const ok = await post({ fields: { ...GOOD, [field]: "a".repeat(max) } });
      expect(sent(ok.res)).toBe(true);
      const over = await post({ fields: { ...GOOD, [field]: "a".repeat(max + 1) } });
      expect(failed(over.res)).toBe(true);
      expect(over.f.calls).toHaveLength(0);
    }
    // Counted in characters, not bytes: a full-length name of 4-byte characters is fine.
    const wide = await post({ fields: { ...GOOD, name: EMOJI.repeat(L.name) } });
    expect(sent(wide.res)).toBe(true);
  });
  it("validates the email with the gate's strict grammar and keeps it optional", async () => {
    const bad = ["not-an-email", "a@b", "a b@example.com", "pat@example.com\r\nBcc: x@example.com", "pat@@example.com", `p${A_UML}t@example.com`, "<pat@example.com>"];
    for (const email of bad) {
      const { res, f } = await post({ fields: { ...GOOD, email } });
      expect(failed(res)).toBe(true);
      expect(f.calls).toHaveLength(0);
    }
    const none = await post({ fields: { ...GOOD, email: "" } });
    expect(sent(none.res)).toBe(true);
  });
  it("refuses line breaks and control or hidden characters in the one-line fields (header injection)", async () => {
    for (const field of ["name", "brand", "budget"]) {
      for (const bad of ["a\r\nBcc: x@example.com", "a\nb", `a${NUL}b`, `a${BIDI}b`, `a${ZWSP}b`, `a${LSEP}b`]) {
        const { res, f } = await post({ fields: { ...GOOD, [field]: bad } });
        expect(failed(res)).toBe(true);
        expect(f.calls).toHaveLength(0);
      }
    }
  });
  it("allows line breaks in the details but refuses other controls and bidi marks", async () => {
    const ok = await post({ fields: { ...GOOD, details: "line one\r\nline two\tend" } });
    expect(sent(ok.res)).toBe(true);
    for (const bad of [`a${NUL}b`, `a${BIDI}b`, `a${BEL}b`]) {
      const { res } = await post({ fields: { ...GOOD, details: bad } });
      expect(failed(res)).toBe(true);
    }
  });
  it("refuses unknown fields, repeated fields and bad percent escapes", async () => {
    for (const body of ["name=a&extra=1", "name=a&name=b", "name=a%zz", "name=a&cf-turnstile-response=x", "NAME=a"]) {
      const { res, f } = await post({ fields: body });
      expect(failed(res)).toBe(true);
      expect(f.calls).toHaveLength(0);
    }
  });
  it("refuses invalid UTF-8", async () => {
    const { res } = await post({ fields: "name=%ff%fe" });
    expect(failed(res) || res.status === 400).toBe(true);
  });
});

describe("honeypot", () => {
  it("tells a bot it worked and delivers nothing and spends nothing", async () => {
    const { res, f } = await post({ fields: { ...GOOD, website: "http://spam.example" } });
    expect(res.headers.get("location")).toBe(INQUIRY_SENT_ROUTE);
    expect(f.calls).toHaveLength(0);
    const row = await testEnv.DB.prepare("SELECT COUNT(*) AS n FROM counters WHERE key = 'inquiry'").first<{ n: number }>();
    expect(row!.n).toBe(0);
  });
  it("lets an empty honeypot through", async () => {
    const { res } = await post({ fields: { ...GOOD, website: "" } });
    expect(res.headers.get("location")).toBe(INQUIRY_SENT_ROUTE);
  });
});

describe("rate limiting and budget", () => {
  it("answers 429 and delivers nothing when the per-client limiter refuses", async () => {
    const { res, f } = await post({}, baseEnv({ INQUIRY_LIMITER: limiter(false) }));
    expect(res.status).toBe(429);
    expect(res.headers.get("retry-after")).toBe("60");
    expect(f.calls).toHaveLength(0);
  });
  it("fails closed when the limiter throws or the client address is missing", async () => {
    const boom = {
      limit: async () => {
        throw new Error("down");
      },
    };
    expect((await post({}, baseEnv({ INQUIRY_LIMITER: boom }))).res.status).toBe(429);
    expect((await post({ headers: { "cf-connecting-ip": null } })).res.status).toBe(429);
  });
  it("never gives the raw client address to the limiter", async () => {
    const lim = limiter();
    await post({}, baseEnv({ INQUIRY_LIMITER: lim }));
    expect(lim.keys).toHaveLength(1);
    expect(lim.keys[0]).not.toContain("203.0.113");
    expect(lim.keys[0]).toMatch(/^[0-9a-f]{32}$/);
  });
  it("stops accepting once the daily budget is spent, and a refusal delivers nothing", async () => {
    const e = baseEnv({ DAILY_ALERT_BUDGET: "2" });
    const f = mockFetch();
    const results: number[] = [];
    for (let i = 0; i < 4; i++) {
      const r = await handleInquiry(await req({ fields: { ...GOOD, name: `Pat ${i}` } }), e, { fetchFn: f.fn, nowMs: T0, notify: createDispatcher().notify });
      results.push(r.status);
    }
    expect(results).toEqual([303, 303, 503, 503]);
    expect(f.calls.length).toBeLessThanOrEqual(2);
  });
  it("spends nothing for a post that fails validation", async () => {
    await post({ fields: { name: "" } });
    const row = await testEnv.DB.prepare("SELECT COUNT(*) AS n FROM counters WHERE key = 'inquiry'").first<{ n: number }>();
    expect(row!.n).toBe(0);
  });
});

describe("delivery", () => {
  it("shows the not-sent page when no channel took the message", async () => {
    const down = await post({}, baseEnv(), mockFetch(500));
    expect(down.res.headers.get("location")).toBe(INQUIRY_ERROR_ROUTE);
    const off = await post({}, baseEnv({ ALERTS_CONFIG: undefined }));
    expect(off.res.headers.get("location")).toBe(INQUIRY_ERROR_ROUTE);
    const notEnabled = await post({}, baseEnv({ ALERTS_CONFIG: JSON.stringify({ channels: ["discord"], events: ["kit_unlocked"] }) }));
    expect(notEnabled.res.headers.get("location")).toBe(INQUIRY_ERROR_ROUTE);
  });
  it("sends one plain message with the fields and no address, agent or cookie", async () => {
    const { f } = await post();
    const sentBody = JSON.parse(f.calls[0]!.body) as { content: string; allowed_mentions: { parse: string[] } };
    expect(sentBody.allowed_mentions.parse).toEqual([]);
    expect(sentBody.content).toContain("Pat Example");
    expect(sentBody.content).toContain("Example Brand");
    expect(sentBody.content).not.toContain("203.0.113");
    expect(sentBody.content).not.toContain(COOKIE_NAME);
  });
});

describe("escaping (nothing a visitor types becomes markup, a mention, a link or a header)", () => {
  it("neutralizes html, mentions and links in the delivered text", async () => {
    const fields = {
      name: "<b>Pat</b>",
      email: "pat@example.com",
      brand: "@everyone <@12345> [x](http://evil.example)",
      budget: "javascript:alert(1)",
      details: "<script>alert(1)</script>\nsee https://evil.example/phish `code` **bold**",
    };
    const { f } = await post({ fields });
    const text = (JSON.parse(f.calls[0]!.body) as { content: string }).content;
    expect(text).not.toMatch(/[<>]/);
    expect(text).not.toContain("@everyone");
    expect(text).not.toContain("<@");
    expect(text).not.toContain("http://");
    expect(text).not.toContain("https://");
    expect(text).not.toContain("javascript:");
    expect(text).not.toMatch(/[\r\n]/);
  });
  it("keeps every visitor value out of the response, success or failure", async () => {
    const evil = "<img src=x onerror=alert(1)>";
    for (const fields of [{ ...GOOD, brand: evil }, { name: evil + "\n" }]) {
      const { res } = await post({ fields });
      expect(await res.text()).not.toContain("img");
      for (const [, v] of res.headers) expect(v).not.toContain("img");
    }
  });
  it("formats the alert with only the fixed shape, whatever the event holds", () => {
    const text = formatAlert(
      { kind: "inquiry_received", atMs: T0, name: "n\r\nSubject: x", email: null, brand: "b", budget: "c", details: `d${BIDI}` },
      "full",
      "plain",
    ).text;
    expect(text).not.toMatch(/[\r\n]/);
    expect(text).not.toContain(BIDI);
    expect(text).toContain("not given");
  });
  it("applies the visitor_email setting to the inquiry address", async () => {
    const masked = baseEnv({ ALERTS_CONFIG: JSON.stringify({ channels: ["discord"], events: ["inquiry_received"], visitor_email: "masked" }) });
    const { f } = await post({}, masked);
    const text = (JSON.parse(f.calls[0]!.body) as { content: string }).content;
    expect(text).toContain("p\\*\\*\\*");
    expect(text).not.toContain("pat(at)example");
  });
});

describe("result pages", () => {
  for (const path of [INQUIRY_SENT_ROUTE, INQUIRY_ERROR_ROUTE]) {
    it(`serves ${path} as fixed text with no script, no form and a closed policy`, async () => {
      const res = await handleInquiry(await req({ method: "GET", path, cookie: null }), baseEnv(), {});
      expect(res.status).toBe(200);
      const html = await res.text();
      expect(html).not.toMatch(/<script|<form|<input|<style| on\w+=/i);
      expect(res.headers.get("content-type")).toContain("text/html");
      expect(res.headers.get("cache-control")).toBe("private, no-store");
      expect(res.headers.get("content-security-policy")).toContain("default-src 'none'");
      expect(res.headers.get("x-content-type-options")).toBe("nosniff");
      expect(html).toContain('href="/kit/"');
    });
  }
  it("names no cause on the not-sent page", async () => {
    const res = await handleInquiry(await req({ method: "GET", path: INQUIRY_ERROR_ROUTE }), baseEnv(), {});
    const html = await res.text();
    expect(html).toContain("not sent");
    expect(html).not.toMatch(/rate|budget|honeypot|limit|cookie|token/i);
  });
});

describe("dispatcher dedup identity", () => {
  it("treats an identical repeat inside the window as one", async () => {
    const d = createDispatcher();
    const e = baseEnv();
    const f = mockFetch();
    const ev = { kind: "inquiry_received" as const, atMs: T0, name: "Pat", email: "pat@example.com", brand: "", budget: "", details: "" };
    await d.notify(e, ev, { fetchFn: f.fn, nowMs: T0 });
    await d.notify(e, ev, { fetchFn: f.fn, nowMs: T0 + 1000 });
    expect(f.calls).toHaveLength(1);
  });
});
