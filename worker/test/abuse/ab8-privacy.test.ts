// SPDX-License-Identifier: Apache-2.0
//
// AB-8: personal data and error leakage. No client address or user agent is stored anywhere, the
// only table that holds an email is `leads`, nothing the visitor sent or the platform reported comes
// back in a response or a log line, and an internal failure looks exactly like any other refusal.
import { env as testEnv } from "cloudflare:workers";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createLogger } from "../../src/kit/log";
import {
  CLIENT_IP,
  EMAIL,
  FIREFOX,
  INQUIRY_GOOD,
  KEY,
  captureConsole,
  cookiePair,
  everything,
  gateReq,
  inquiryReq,
  insertLead,
  installNetwork,
  kv,
  makeEnv,
  resetState,
  send,
  setCookieOf,
  signedCookie,
  tokenOf,
  unlock,
  viewReq,
  type AbuseEnv,
  type Net,
} from "./harness";

let net: Net;
beforeEach(async () => {
  await resetState();
  net = installNetwork();
});
afterEach(() => net.restore());

const SECRETS = [KEY, btoa("s".repeat(40)), "1x0000000000000000000000000000000AA", "d".repeat(40), "SECRETtokenSECRETtokenSECRET0123456789"];
const NAMES = ["TOKEN_HMAC_KEY", "TURNSTILE_SECRET", "VIEW_HASH_SECRET", "ALERT_DEDUP_KEY", "ALERT_DISCORD_WEBHOOK", "KIT_KILL"];

async function dumpAllTables(): Promise<{ table: string; text: string }[]> {
  const names = await testEnv.DB.prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '_cf_%' AND name != 'd1_migrations'").all<{ name: string }>();
  const out: { table: string; text: string }[] = [];
  for (const { name } of names.results) {
    const rows = await testEnv.DB.prepare(`SELECT * FROM "${name}"`).all();
    out.push({ table: name, text: JSON.stringify(rows.results) });
  }
  return out;
}

describe("AB-8: what is stored", () => {
  it("after unlocks, views, refusals and inquiries no table holds an address, a user agent, a token or a cookie, and only leads holds an email", async () => {
    const cookies: string[] = [];
    for (let i = 0; i < 4; i++) {
      const res = await send(gateReq({ fields: { email: `person${i}@example.com`, "cf-turnstile-response": `ok-store-${i}`, }, headers: { "cf-connecting-ip": `203.0.113.${60 + i}`, "user-agent": `${FIREFOX} marker${i}` } }));
      cookies.push(cookiePair(res));
    }
    for (const c of cookies) await send(viewReq({ cookie: c }));
    await send(gateReq({ fields: { email: "refused@example.com", "cf-turnstile-response": "nope" } }));
    await send(gateReq({ headers: { origin: "https://evil.example" } }));
    await insertLead("e".repeat(32), "inquirer@example.com");
    await send(inquiryReq({ cookie: await signedCookie({ lid: "e".repeat(32) }) }));

    const tables = await dumpAllTables();
    expect(tables.length).toBeGreaterThan(4);
    for (const { table, text } of tables) {
      expect(text, table).not.toContain("203.0.113.");
      expect(text, table).not.toContain("Mozilla");
      expect(text, table).not.toContain("Firefox");
      expect(text, table).not.toContain("marker");
      expect(text, table).not.toContain("ok-store");
      expect(text, table).not.toContain("v1.");
      for (const c of cookies) expect(text, table).not.toContain(c.slice(15, 60));
      for (const s of SECRETS) expect(text, table).not.toContain(s);
      if (table !== "leads") {
        expect(text, table).not.toContain("@example.com");
        expect(text, table).not.toContain("person");
      }
    }
    const leads = tables.find((t) => t.table === "leads")!.text;
    expect(leads).toContain("person0@example.com");
  });

  it("the lead row carries only the address, timestamps, a fixed source and the edge country (no network or browser data)", async () => {
    await send(gateReq());
    const cols = await testEnv.DB.prepare("SELECT name FROM pragma_table_info('leads')").all<{ name: string }>();
    const names = cols.results.map((c) => c.name).sort();
    // A closed list on purpose: a new column (an address, a browser string) must be added here on review.
    expect(names).toEqual([
      "country",
      "created_ts",
      "email",
      "id",
      "last_cc_alert_ts",
      "last_seen_ts",
      "last_view_alert_ts",
      "last_view_event_ts",
      "session_floor_ts",
      "source",
      "verified_ts",
    ]);
    const row = await testEnv.DB.prepare("SELECT * FROM leads").first<Record<string, unknown>>();
    expect(JSON.stringify(row)).not.toContain(CLIENT_IP);
    expect(row!.source).toBe("gate");
  });

  it("a client cannot choose what is stored: extra fields refuse the request, and the country comes only from the edge header", async () => {
    for (const extra of [{ source: "email" }, { verified: "1" }, { country: "FR" }, { lid: "x" }, { id: "x" }, { created_ts: "0" }]) {
      const res = await send(gateReq({ fields: { email: EMAIL, "cf-turnstile-response": "ok-x", ...extra } }));
      expect(res.headers.get("location")).toBe("/kit/retry/");
    }
    for (const bad of ["XX", "T1", "us", "USA", "", "1"]) {
      await resetState();
      const res = await send(gateReq({ headers: { "cf-ipcountry": bad }, fields: { email: EMAIL, "cf-turnstile-response": `ok-c-${bad}` } }));
      expect(res.headers.get("set-cookie")).not.toBeNull();
      const row = await testEnv.DB.prepare("SELECT country FROM leads").first<{ country: string | null }>();
      expect(row!.country, bad).toBeNull();
    }
  });
});

describe("AB-8: what is logged", () => {
  it("every flow writes only closed-vocabulary lines, with no address, token, cookie, user agent or secret", async () => {
    const { out } = await captureConsole(async () => {
      const cookie = await unlock(makeEnv(), "logged.person@example.com");
      await send(viewReq({ cookie }));
      await send(viewReq());
      await send(gateReq({ fields: { email: "other.person@example.com", "cf-turnstile-response": "nope" } }));
      await send(gateReq({ headers: { origin: "https://evil.example" } }));
      await send(gateReq({ headers: { "user-agent": "curl/8.5.0" } }));
      await send(gateReq({ fields: { email: EMAIL } }));
      await insertLead();
      await send(inquiryReq({ cookie: await signedCookie() }));
      await send(inquiryReq({ cookie: await signedCookie(), fields: { ...INQUIRY_GOOD, email: "not an address" } }));
      return cookie;
    });
    expect(out.length).toBeGreaterThan(0);
    for (const needle of ["example.com", "person", CLIENT_IP, "203.0.113", "Mozilla", "Firefox", "curl", "__Host-ra_kit", "v1.", "ok-", "nope", ...SECRETS, ...NAMES]) {
      expect(out, needle).not.toContain(needle);
    }
    for (const line of out.split("\n").filter((l) => l.trim() !== "")) {
      const parsed = JSON.parse(line) as Record<string, unknown>;
      expect(Object.keys(parsed).every((k) => ["e", "o", "r", "n"].includes(k)), line).toBe(true);
      for (const v of Object.values(parsed)) expect(String(v), line).toMatch(/^[a-z_]{1,16}$|^\d{1,10}$/);
    }
  });

  it("the logger writes a value it was not given words for as a fixed word, never as itself", () => {
    const lines: string[] = [];
    const log = createLogger({ sink: (l) => lines.push(l) });
    log.log("pat@example.com" as never, "203.0.113.9" as never, "v1.abc.def" as never);
    log.log("gate", "<script>" as never, "x".repeat(500) as never);
    log.log("gate", "rejected", { toString: () => "secret" } as never);
    const all = lines.join("\n");
    for (const needle of ["pat", "example", "203", "v1.", "script", "xxxx", "secret"]) expect(all).not.toContain(needle);
    expect(lines).toHaveLength(3);
  });

  it("a flood is cut to the per-minute allowance and the rest is only counted", () => {
    let t = 1_000_000;
    const lines: string[] = [];
    const log = createLogger({ sink: (l) => lines.push(l), now: () => t, maxPerMinute: 5 });
    for (let i = 0; i < 200; i++) log.log("gate", "rejected", "origin");
    expect(lines).toHaveLength(5);
    t += 61_000;
    log.log("gate", "rejected", "origin");
    expect(lines).toHaveLength(7); // one summary of what was dropped, then the new line
    expect(JSON.parse(lines[5]!)).toEqual({ e: "log", o: "dropped", n: 195 });
  });

  it("one request cannot write more than its own line budget", () => {
    const lines: string[] = [];
    const log = createLogger({ sink: (l) => lines.push(l), maxPerRequest: 3 });
    const one = log.forRequest();
    for (let i = 0; i < 50; i++) one.log("gate", "rejected", "origin");
    expect(lines).toHaveLength(3);
    const two = log.forRequest();
    two.log("gate", "rejected", "origin");
    expect(lines).toHaveLength(4);
  });
});

describe("AB-8: what a response says", () => {
  const SCENARIOS: [string, () => Promise<Response>][] = [
    ["unlock success", () => send(gateReq({ fields: { email: "reply.check@example.com", "cf-turnstile-response": "ok-resp" } }))],
    ["bad token", () => send(gateReq({ fields: { email: "reply.check@example.com", "cf-turnstile-response": "nope" } }))],
    ["bad email", () => send(gateReq({ fields: { email: "reply.check@@example.com", "cf-turnstile-response": "ok" } }))],
    ["bad origin", () => send(gateReq({ headers: { origin: "https://evil.example" } }))],
    ["killed gate", () => send(gateReq(), makeEnv({ KIT_KILL: "1" }))],
    ["misconfigured gate", () => send(gateReq(), makeEnv({ TOKEN_HMAC_KEY: "short" }))],
    ["view without a cookie", () => send(viewReq())],
    ["view with a garbage cookie", () => send(viewReq({ headers: { cookie: "__Host-ra_kit=v1.AAAA.BBBB" } }))],
    ["wrong method", () => send(gateReq({ method: "GET" }))],
    ["inquiry without a cookie", () => send(inquiryReq({ cookie: null }))],
    ["inquiry with a bad address", async () => { await insertLead(); return send(inquiryReq({ cookie: await signedCookie(), fields: { ...INQUIRY_GOOD, email: "reply.check@@example.com" } })); }],
  ];
  for (const [name, run] of SCENARIOS) {
    it(`${name}: nothing the visitor sent, no secret, no setting name and no stack trace comes back`, async () => {
      const res = await run();
      const text = await everything(res);
      for (const needle of ["reply.check", "example.com", CLIENT_IP, "Firefox", "ok-resp", "nope", ...SECRETS, ...NAMES, "Error:", "    at ", ".ts:", "node_modules", "workerd", "stack"]) {
        expect(text, needle).not.toContain(needle);
      }
      const loc = res.headers.get("location") ?? "";
      expect(loc.length).toBeLessThan(40);
      expect(loc).not.toMatch(/[?#=@]/);
    });
  }

  it("the success redirect carries no token or address in the URL, and the body is empty", async () => {
    const res = await send(gateReq());
    expect(res.headers.get("location")).toBe("/k/view");
    expect(await res.text()).toBe("");
    expect(res.headers.get("location")).not.toContain(tokenOf(res));
    expect(setCookieOf(res)).not.toContain(EMAIL);
  });
});

describe("AB-8: an internal failure looks like any other refusal", () => {
  const THROWING_DB = {
    prepare() {
      throw new Error("d1-secret-detail-5521");
    },
    batch() {
      throw new Error("d1-secret-detail-5521");
    },
  };
  const THROWING_KV = {
    get: async () => {
      throw new Error("kv-secret-detail-8842");
    },
  };
  const DETAILS = ["d1-secret-detail-5521", "kv-secret-detail-8842", "vendor-secret-detail-9137"];

  async function plain(res: Response): Promise<string> {
    return JSON.stringify([res.status, [...res.headers].sort(), await res.clone().text()]);
  }

  it("the gate: a database that throws, a vendor that throws and a vendor 500 all answer with the one fixed redirect", async () => {
    const reference = await plain(await send(gateReq({ fields: { email: EMAIL, "cf-turnstile-response": "nope" } })));
    const cases: [string, AbuseEnv, ReturnType<typeof installNetwork>?][] = [
      ["database throws", makeEnv({ DB: THROWING_DB }) as AbuseEnv],
      ["bad vendor answer", makeEnv()],
    ];
    for (const [name, env] of cases) {
      const res = await send(gateReq({ fields: { email: EMAIL, "cf-turnstile-response": name === "bad vendor answer" ? "nope" : "ok-t" } }), env);
      const text = await everything(res);
      for (const d of DETAILS) expect(text, name).not.toContain(d);
      expect(await plain(res), name).toBe(reference);
    }
    for (const mode of ["throw", "500"] as const) {
      net.restore();
      net = installNetwork({ siteverify: mode });
      const res = await send(gateReq({ fields: { email: EMAIL, "cf-turnstile-response": `ok-${mode}` } }));
      for (const d of DETAILS) expect(await everything(res), mode).not.toContain(d);
      expect(await plain(res), mode).toBe(reference);
    }
  });

  it("the view: a database that throws and a KV that throws answer exactly like a missing cookie", async () => {
    const cookie = await unlock();
    const reference = await plain(await send(viewReq()));
    const dbDown = await send(viewReq({ cookie }), makeEnv({ DB: THROWING_DB }));
    const kvDown = await send(viewReq({ cookie }), makeEnv({ KIT: THROWING_KV }));
    const kvEmpty = await send(viewReq({ cookie }), makeEnv({ KIT: kv(null) }));
    for (const res of [dbDown, kvDown, kvEmpty]) {
      for (const d of DETAILS) expect(await everything(res)).not.toContain(d);
      expect(await plain(res)).toBe(reference);
    }
  });

  it("the inquiry form: a database that throws never shows the cause", async () => {
    await insertLead();
    const res = await send(inquiryReq({ cookie: await signedCookie() }), makeEnv({ DB: THROWING_DB }));
    for (const d of DETAILS) expect(await everything(res)).not.toContain(d);
    expect([303, 503]).toContain(res.status);
    expect(net.sink()).toHaveLength(0);
  });
});
