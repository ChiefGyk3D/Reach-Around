// SPDX-License-Identifier: Apache-2.0
import { afterEach, describe, expect, it, vi } from "vitest";
import { parseAlertsConfig } from "../../src/alerts/config";
import { createDispatcher, MAX_PAYLOAD_BYTES, scheduleAlert, type AlertsEnv } from "../../src/alerts/dispatcher";
import { createEmailChannel, disabledEmailProvider } from "../../src/alerts/email";
import { formatAlert } from "../../src/alerts/format";
import { capMessage, MAX_MESSAGE_CHARS, neutralize } from "../../src/alerts/sanitize";
import type { AlertEvent } from "../../src/alerts/types";
import { validateDiscordWebhook, validateHomeserver, validateRoomId } from "../../src/alerts/validate";

const HOOK_TOKEN = "SECRETtokenSECRETtokenSECRET0123456789";
const DISCORD = `https://discord.com/api/webhooks/123456789012345678/${HOOK_TOKEN}`;
const MX_TOKEN = "syt_MATRIXSECRET_0123456789";
const BASE: AlertsEnv = {
  ALERTS_CONFIG: JSON.stringify({ channels: ["discord", "matrix"], events: ["kit_unlocked", "lead_captured", "gate_failed_repeatedly"] }),
  ALERT_DISCORD_WEBHOOK: DISCORD,
  ALERT_MATRIX_HOMESERVER: "https://matrix.example.org",
  ALERT_MATRIX_ROOM_ID: "!room123:example.org",
  ALERT_MATRIX_ACCESS_TOKEN: MX_TOKEN,
};
const T0 = Date.UTC(2026, 10, 9, 14, 3, 30);
const unlock = (email = "partner@example.com", atMs = T0): AlertEvent => ({ kind: "kit_unlocked", atMs, email });

type Call = { url: string; init: RequestInit };
function mockFetch(handler: (url: string) => Response | Promise<Response> | never = () => new Response(null, { status: 204 })) {
  const calls: Call[] = [];
  const fn = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init: init ?? {} });
    return handler(String(url));
  });
  return { fn: fn as unknown as typeof fetch, calls };
}

afterEach(() => vi.restoreAllMocks());

/** True when the text holds a control, bidi, zero-width or separator character. */
function hasControl(t: string): boolean {
  for (const ch of t) {
    const c = ch.codePointAt(0)!;
    if (c < 0x20 || (c >= 0x7f && c <= 0x9f) || (c >= 0x200b && c <= 0x200f) || (c >= 0x2028 && c <= 0x202e) || c === 0xfeff) return true;
  }
  return false;
}

describe("config (default off, fail closed)", () => {
  it("is off when unset, empty, malformed, unknown-keyed or has no channel or no event", () => {
    for (const v of [undefined, "", "nope", "[]", "null", '{"channels":["discord"]}', '{"events":["kit_unlocked"]}',
      '{"channels":["slack"],"events":["kit_unlocked"]}', '{"channels":["discord"],"events":["x"]}',
      '{"channels":["discord"],"events":["kit_unlocked"],"extra":1}',
      '{"channels":["discord"],"events":["kit_unlocked"],"max_per_minute":0}',
      '{"channels":["discord"],"events":["kit_unlocked"],"max_per_minute":"5"}',
      '{"channels":["discord"],"events":["kit_unlocked"],"visitor_email":"yes"}']) {
      expect(parseAlertsConfig(v)).toBeNull();
    }
  });
  it("parses a good value with defaults", () => {
    const c = parseAlertsConfig('{"channels":["discord"],"events":["kit_unlocked"]}');
    expect(c?.visitorEmail).toBe("full");
    expect(c?.maxPerMinute).toBe(5);
  });
  it("sends nothing and never calls fetch when off or when the event is not enabled", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    await d.notify({ ...BASE, ALERTS_CONFIG: undefined }, unlock(), { fetchFn: m.fn, nowMs: T0 });
    await d.notify({ ...BASE, ALERTS_CONFIG: '{"channels":["discord"],"events":["lead_captured"]}' }, unlock(), { fetchFn: m.fn, nowMs: T0 });
    expect(m.calls).toHaveLength(0);
  });
  it("counts an enabled channel that has no valid binding as unconfigured and sends nothing there", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    await d.notify({ ALERTS_CONFIG: BASE.ALERTS_CONFIG }, unlock(), { fetchFn: m.fn, nowMs: T0 });
    expect(m.calls).toHaveLength(0);
    expect(d.status().unconfigured).toBe(2);
  });
});

describe("Discord adapter", () => {
  it("POSTs JSON with a plain-text summary, no mentions, and no redirects", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    await d.notify({ ...BASE, ALERTS_CONFIG: '{"channels":["discord"],"events":["kit_unlocked"]}' }, unlock(), { fetchFn: m.fn, nowMs: T0 });
    expect(m.calls).toHaveLength(1);
    const { url, init } = m.calls[0]!;
    expect(url).toBe(DISCORD);
    expect(init.method).toBe("POST");
    expect(init.redirect).toBe("error");
    const body = JSON.parse(String(init.body));
    expect(body.allowed_mentions).toEqual({ parse: [] });
    expect(body.content).toContain("2026-11-09 14:03 UTC");
    expect(body.content).toContain("partner(at)example.com");
    expect(d.status().channels.discord?.sent).toBe(1);
  });
});

describe("Matrix adapter", () => {
  it("PUTs an m.room.message with a transaction id, bearer token, plain body", async () => {
    const m = mockFetch(() => new Response("{}", { status: 200 }));
    const d = createDispatcher();
    await d.notify({ ...BASE, ALERTS_CONFIG: '{"channels":["matrix"],"events":["kit_unlocked"]}' }, unlock(), { fetchFn: m.fn, nowMs: T0 });
    const { url, init } = m.calls[0]!;
    expect(url).toMatch(/^https:\/\/matrix\.example\.org\/_matrix\/client\/v3\/rooms\/!room123%3Aexample\.org\/send\/m\.room\.message\/[0-9a-f]{32}$/);
    expect(init.method).toBe("PUT");
    expect(init.redirect).toBe("error");
    expect((init.headers as Record<string, string>).Authorization).toBe(`Bearer ${MX_TOKEN}`);
    const body = JSON.parse(String(init.body));
    expect(body.msgtype).toBe("m.text");
    expect(body).not.toHaveProperty("formatted_body");
    expect(body.body).toContain("partner(at)example.com");
  });
  it("uses a new transaction id for each send", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    const env = { ...BASE, ALERTS_CONFIG: '{"channels":["matrix"],"events":["kit_unlocked"]}' };
    await d.notify(env, unlock("a@example.com"), { fetchFn: m.fn, nowMs: T0 });
    await d.notify(env, unlock("b@example.com"), { fetchFn: m.fn, nowMs: T0 });
    expect(new Set(m.calls.map((c) => c.url)).size).toBe(2);
  });
});

describe("email", () => {
  it("is never configured today: nothing is sent and it is counted as unconfigured", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    await d.notify(
      { ALERTS_CONFIG: '{"channels":["email"],"events":["kit_unlocked"]}', ALERT_EMAIL_TO: "owner@example.com", ALERT_EMAIL_PROVIDER: "none" },
      unlock(), { fetchFn: m.fn, nowMs: T0 });
    expect(m.calls).toHaveLength(0);
    expect(d.status().unconfigured).toBe(1);
  });
  it("the disabled provider reports unavailable and never throws", async () => {
    const ch = createEmailChannel(disabledEmailProvider, "owner@example.com");
    expect(ch).not.toBeNull();
    const r = await ch!.send({ text: "x", subject: "y" }, { fetchFn: fetch, timeoutMs: 100 });
    expect(r).toEqual({ ok: false, failure: "unavailable" });
    expect(createEmailChannel(null, "owner@example.com")).toBeNull();
    expect(createEmailChannel(disabledEmailProvider, "not an address")).toBeNull();
  });
  it("an own-property lookup: __proto__ and constructor are not providers", async () => {
    const d = createDispatcher();
    for (const p of ["__proto__", "constructor", "toString"]) {
      await d.notify({ ALERTS_CONFIG: '{"channels":["email"],"events":["kit_unlocked"]}', ALERT_EMAIL_TO: "o@example.com", ALERT_EMAIL_PROVIDER: p },
        unlock(p + "@example.com"), { fetchFn: mockFetch().fn, nowMs: T0 });
    }
    expect(d.status().unconfigured).toBe(3);
  });
});

describe("failure handling and isolation", () => {
  const cases: Array<[string, () => Response | Promise<Response>, string]> = [
    ["4xx", () => new Response(null, { status: 404 }), "rejected"],
    ["5xx", () => new Response(null, { status: 503 }), "server"],
    ["3xx (redirect refused)", () => new Response(null, { status: 302, headers: { Location: "https://evil.example.net/" } }), "redirect"],
    ["network error", () => { throw new TypeError("boom"); }, "network"],
  ];
  for (const [name, h, cls] of cases) {
    it(`${name} is counted as ${cls}, never thrown`, async () => {
      const m = mockFetch(h);
      const d = createDispatcher();
      await expect(d.notify({ ...BASE, ALERTS_CONFIG: '{"channels":["discord"],"events":["kit_unlocked"]}' }, unlock(), { fetchFn: m.fn, nowMs: T0 })).resolves.toBeUndefined();
      expect(d.status().channels.discord?.failed[cls as "rejected"]).toBe(1);
      expect(d.status().channels.discord?.sent).toBe(0);
    });
  }
  it("a timeout is counted and returns promptly", async () => {
    const m = mockFetch(() => new Promise<Response>(() => undefined));
    const d = createDispatcher();
    const t = Date.now();
    await d.notify({ ...BASE, ALERTS_CONFIG: '{"channels":["discord"],"events":["kit_unlocked"]}' }, unlock(), { fetchFn: m.fn, nowMs: T0, timeoutMs: 50 });
    expect(Date.now() - t).toBeLessThan(1000);
    expect(d.status().channels.discord?.failed.timeout).toBe(1);
  });
  it("a hanging or failing channel never blocks or stops the other", async () => {
    const m = mockFetch((url) => (url.startsWith("https://discord.com") ? new Response(null, { status: 500 }) : new Response("{}", { status: 200 })));
    const d = createDispatcher();
    await d.notify(BASE, unlock(), { fetchFn: m.fn, nowMs: T0 });
    expect(m.calls).toHaveLength(2);
    expect(d.status().channels.discord?.failed.server).toBe(1);
    expect(d.status().channels.matrix?.sent).toBe(1);
  });
  it("a channel that throws is contained", async () => {
    const m = mockFetch((url) => { if (url.includes("discord")) throw new Error("x"); return new Response(null, { status: 200 }); });
    const d = createDispatcher();
    await d.notify(BASE, unlock(), { fetchFn: m.fn, nowMs: T0 });
    expect(d.status().channels.matrix?.sent).toBe(1);
  });
  it("scheduleAlert uses waitUntil and never throws without a context", () => {
    const m = mockFetch();
    const p: Promise<unknown>[] = [];
    scheduleAlert(BASE, { waitUntil: (x) => void p.push(x) }, unlock("sched@example.com", T0 + 1), { fetchFn: m.fn });
    expect(p).toHaveLength(1);
    expect(() => scheduleAlert(BASE, undefined, unlock())).not.toThrow();
    expect(() => scheduleAlert(BASE, { waitUntil: () => { throw new Error("x"); } }, unlock())).not.toThrow();
  });
});

describe("no secret in logs, status or messages", () => {
  it("keeps the webhook, token and addresses out of every log line, the status and the payload text", async () => {
    const lines: string[] = [];
    vi.spyOn(console, "log").mockImplementation((...a: unknown[]) => void lines.push(a.join(" ")));
    vi.spyOn(console, "error").mockImplementation((...a: unknown[]) => void lines.push(a.join(" ")));
    vi.spyOn(console, "warn").mockImplementation((...a: unknown[]) => void lines.push(a.join(" ")));
    const m = mockFetch((url) => { if (url.includes("discord")) throw new Error(`leak ${DISCORD}`); return new Response(`${MX_TOKEN}`, { status: 500 }); });
    const d = createDispatcher();
    await d.notify(BASE, unlock("victim@example.com"), { fetchFn: m.fn, nowMs: T0 });
    const all = lines.join("\n") + JSON.stringify(d.status());
    for (const s of [HOOK_TOKEN, MX_TOKEN, "victim@example.com", "123456789012345678"]) expect(all).not.toContain(s);
    for (const c of m.calls) {
      const text = String(c.init.body);
      expect(text).not.toContain(HOOK_TOKEN);
      expect(text).not.toContain(MX_TOKEN);
    }
  });
});

describe("redaction and neutralization", () => {
  const hostile = [
    "@everyone @here <@123456789012345678> <#1234> <:emoji:123>",
    "**bold** __u__ ~~s~~ `code` ```fence``` > quote | spoiler || [click](https://evil.example.net) # head",
    "https://evil.example.net/x?a=1",
    "line1 line2 line3" + String.fromCharCode(13, 10, 0, 7, 27, 0x202e, 0x200b, 0x2028, 0xfeff, 0x9f) + "[31m",
    "@room @user:matrix.org",
    "A".repeat(10_000),
    "e\ud83d",
  ];
  it("neutralizes mentions, markdown, links, controls and length", () => {
    for (const h of hostile) {
      for (const mode of ["markdown", "plain"] as const) {
        const out = neutralize(h, 120, mode);
        expect(out).not.toMatch(/@/);
        expect(out).not.toMatch(/[<>]/);
        expect(hasControl(out)).toBe(false);
        expect(out).not.toContain("://");
        expect(out.length).toBeLessThanOrEqual(123);
      }
    }
    const md = neutralize("**x** [a](b) `c`", 120, "markdown");
    expect(md).toBe("\\*\\*x\\*\\* \\[a\\](b) \\`c\\`");
  });
  it("non-strings become empty", () => {
    for (const v of [undefined, null, 5, {}, ["a"]]) expect(neutralize(v)).toBe("");
  });
  it("a hostile address cannot add a line, ping, or link to the message", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    await d.notify(BASE, unlock("x@y.com @everyone\n<@1234567890> [a](https://evil.example.net)"), { fetchFn: m.fn, nowMs: T0 });
    for (const c of m.calls) {
      const body = JSON.parse(String(c.init.body)) as { content?: string; body?: string };
      const text = body.content ?? body.body ?? "";
      expect(text).not.toMatch(/[@<>\n]/);
      expect(text).not.toContain("://");
    }
  });
  it("shows the address per visitor_email: full, masked, none", () => {
    expect(formatAlert(unlock(), "full", "plain").text).toContain("partner(at)example.com");
    const masked = formatAlert(unlock(), "masked", "plain").text;
    expect(masked).toContain("p***(at)example.com");
    expect(masked).not.toContain("partner");
    const none = formatAlert(unlock(), "none", "plain").text;
    expect(none).toContain("a visitor");
    expect(none).not.toContain("example.com");
  });
  it("states the UTC time, and the event type has no field for an IP, agent, cookie or token", () => {
    const m = formatAlert(unlock(), "full", "plain");
    expect(m.text).toMatch(/2026-11-09 14:03 UTC/);
    const polluted = { ...unlock(), ip: "203.0.113.9", ua: "Agent/1", cookie: "c=1", token: "tok" } as unknown as AlertEvent;
    const t = formatAlert(polluted, "full", "plain").text;
    for (const s of ["203.0.113.9", "Agent/1", "c=1", "tok"]) expect(t).not.toContain(s);
  });
  it("a bad time or count cannot break the text", () => {
    expect(formatAlert({ kind: "kit_unlocked", atMs: Number.NaN, email: "a@b.co" }, "full", "plain").text).toContain("an unknown time");
    const g = formatAlert({ kind: "gate_failed_repeatedly", atMs: T0, count: 1e99 }, "full", "plain").text;
    expect(g).toContain("99999 times");
  });
  it("caps a message at the hard limit and a payload at its byte cap", () => {
    expect(capMessage("x".repeat(5000)).length).toBeLessThanOrEqual(MAX_MESSAGE_CHARS);
    expect(MAX_PAYLOAD_BYTES).toBeLessThanOrEqual(2000);
  });
  it("sends a bounded body even for a very long address", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    await d.notify(BASE, unlock("a".repeat(5000) + "@example.com"), { fetchFn: m.fn, nowMs: T0 });
    for (const c of m.calls) expect(new TextEncoder().encode(String(c.init.body)).length).toBeLessThanOrEqual(MAX_PAYLOAD_BYTES);
  });
});

describe("rate limit and dedup", () => {
  const env = { ...BASE, ALERTS_CONFIG: '{"channels":["discord"],"events":["kit_unlocked"],"max_per_minute":3}' };
  it("sends at most N per minute per channel, then one notice, then holds the rest", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    for (let i = 0; i < 50; i++) await d.notify(env, unlock(`u${i}@example.com`), { fetchFn: m.fn, nowMs: T0 + i });
    expect(m.calls).toHaveLength(4); // 3 alerts + 1 notice
    expect(String(m.calls[3]!.init.body)).toContain("held back");
    expect(d.status().channels.discord?.suppressed).toBe(47);
  });
  it("the next window carries a summary of what was held back", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    for (let i = 0; i < 10; i++) await d.notify(env, unlock(`u${i}@example.com`), { fetchFn: m.fn, nowMs: T0 + i });
    await d.notify(env, unlock("later@example.com"), { fetchFn: m.fn, nowMs: T0 + 61_000 });
    const last = String(m.calls[m.calls.length - 1]!.init.body);
    expect(last).toContain("7 earlier alerts were held back");
  });
  it("limits each channel on its own", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    const both = { ...BASE, ALERTS_CONFIG: '{"channels":["discord","matrix"],"events":["kit_unlocked"],"max_per_minute":1}' };
    await d.notify(both, unlock("a@example.com"), { fetchFn: m.fn, nowMs: T0 });
    expect(m.calls).toHaveLength(2);
  });
  it("drops an identical event inside the dedup window, allows it after", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    const e = { ...BASE, ALERTS_CONFIG: '{"channels":["discord"],"events":["kit_unlocked"]}' };
    await d.notify(e, unlock("same@example.com"), { fetchFn: m.fn, nowMs: T0 });
    await d.notify(e, unlock("SAME@example.com"), { fetchFn: m.fn, nowMs: T0 + 1000 });
    expect(m.calls).toHaveLength(1);
    expect(d.status().deduped).toBe(1);
    await d.notify(e, unlock("same@example.com"), { fetchFn: m.fn, nowMs: T0 + 11 * 60_000 });
    expect(m.calls).toHaveLength(2);
  });
});

describe("destination (SSRF) rules", () => {
  it("accepts only Discord webhook URLs of the right shape", () => {
    expect(validateDiscordWebhook(DISCORD)).toBe(DISCORD);
    expect(validateDiscordWebhook(`https://ptb.discord.com/api/v10/webhooks/123456789012345678/${HOOK_TOKEN}`)).not.toBeNull();
    for (const bad of [
      `http://discord.com/api/webhooks/123456789012345678/${HOOK_TOKEN}`,
      `https://discord.com.evil.example.net/api/webhooks/123456789012345678/${HOOK_TOKEN}`,
      `https://evil.example.net/api/webhooks/123456789012345678/${HOOK_TOKEN}`,
      `https://user@discord.com/api/webhooks/123456789012345678/${HOOK_TOKEN}`,
      `https://discord.com:8443/api/webhooks/123456789012345678/${HOOK_TOKEN}`,
      `https://discord.com/api/webhooks/123456789012345678/${HOOK_TOKEN}?x=1`,
      `https://discord.com/api/webhooks/123456789012345678/${HOOK_TOKEN}/`,
      `https://discord.com/api/webhooks/abc/${HOOK_TOKEN}`,
      "https://127.0.0.1/api/webhooks/123456789012345678/" + HOOK_TOKEN,
      "", undefined, 5, null,
    ]) expect(validateDiscordWebhook(bad)).toBeNull();
  });
  it("accepts a public https Matrix homeserver name only", () => {
    expect(validateHomeserver("https://matrix.example.org")).toBe("matrix.example.org");
    expect(validateHomeserver("https://Matrix.Example.org/")).toBe("matrix.example.org");
    for (const bad of [
      "http://matrix.example.org", "https://127.0.0.1", "https://10.0.0.5", "https://192.168.1.1", "https://169.254.169.254",
      "https://[::1]", "https://[fe80::1]", "https://2130706433", "https://0x7f.1", "https://0177.0.0.1", "https://localhost",
      "https://printer.local", "https://matrix.internal", "https://host.lan", "https://x.home.arpa", "https://nas", "https://svc.corp",
      "https://matrix.example.org:8448", "https://matrix.example.org/path", "https://matrix.example.org?x=1",
      "https://user@matrix.example.org", "https://matrix.example.org.", "https://-bad.example.org", "https://exa mple.org",
      "https://matrix.example.org\\@evil.example.net", "", undefined, 7,
    ]) expect(validateHomeserver(bad)).toBeNull();
  });
  it("accepts a room id of the right shape", () => {
    expect(validateRoomId("!abc:example.org")).not.toBeNull();
    for (const bad of ["#alias:example.org", "!a b", "!a/b:example.org", "!abc:exa mple.org", undefined]) expect(validateRoomId(bad)).toBeNull();
  });
  it("a channel with a bad destination is not configured and no request is made", async () => {
    const m = mockFetch();
    const d = createDispatcher();
    await d.notify({ ...BASE, ALERT_DISCORD_WEBHOOK: "https://evil.example.net/x", ALERT_MATRIX_HOMESERVER: "https://127.0.0.1" }, unlock(), { fetchFn: m.fn, nowMs: T0 });
    expect(m.calls).toHaveLength(0);
    expect(d.status().unconfigured).toBe(2);
  });
});
