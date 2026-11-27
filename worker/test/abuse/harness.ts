// SPDX-License-Identifier: Apache-2.0
//
// Shared helpers for the abuse suite (issue #270). Everything here is hermetic: the vendor and the
// alert sink are fakes installed over the global fetch, the database is the test D1, the clock is the
// real one (requests go through `worker.fetch`, which has no clock seam), and every address, host and
// secret is a documentation placeholder.
import { env as testEnv } from "cloudflare:workers";
import { vi } from "vitest";
import { resetEventCapCache } from "../../src/kit/budgets";
import { COOKIE_NAME, signToken } from "../../src/kit/token";
import worker from "../../src/index";

export const HOST = "links.example.test";
export const ORIGIN = `https://${HOST}`;
export const KEY = "k".repeat(40);
export const OTHER_KEY = "z".repeat(40);
export const CANARY = "CANARY-GATED-abuse-4c1e";
export const EMAIL = "pat@example.com";
export const CLIENT_IP = "203.0.113.9";
export const FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0";
export const HOOK = "https://discord.com/api/webhooks/123456789012345678/SECRETtokenSECRETtokenSECRET0123456789";
export const LEAD_ID = "a".repeat(32);

export interface Limiter {
  keys: string[];
  limit(o: { key: string }): Promise<{ success: boolean }>;
}
export function limiter(success = true): Limiter {
  const keys: string[] = [];
  return {
    keys,
    async limit(o) {
      keys.push(o.key);
      return { success };
    },
  };
}
/** A limiter that admits the first `n` calls and refuses the rest, like a per-minute allowance. */
export function allowance(n: number): Limiter {
  const keys: string[] = [];
  return {
    keys,
    async limit(o) {
      keys.push(o.key);
      return { success: keys.length <= n };
    },
  };
}

export interface Net {
  /** Every call the Worker made to the network: the vendor check and the alert sink. */
  calls: { url: string; body: string }[];
  siteverify: () => number;
  sink: () => { url: string; body: string }[];
  restore: () => void;
}

/**
 * Fake network over the global fetch. siteverify approves a token that starts with "ok" (every time,
 * like the vendor's eventually consistent replay) and reports a challenge time of now. Any other
 * host is the alert sink and answers 204.
 */
export function installNetwork(opts: { siteverify?: "ok" | "throw" | "500" | "wrong-host" | "wrong-action"; sink?: number } = {}): Net {
  const calls: Net["calls"] = [];
  const spy = vi.spyOn(globalThis, "fetch").mockImplementation((async (url: string | URL | Request, init?: RequestInit) => {
    const u = String(url);
    const body = String(init?.body ?? "");
    calls.push({ url: u, body });
    if (u.includes("siteverify")) {
      const mode = opts.siteverify ?? "ok";
      if (mode === "throw") throw new Error("vendor-secret-detail-9137");
      if (mode === "500") return new Response("{}", { status: 500 });
      const good = new URLSearchParams(body).get("response")?.startsWith("ok") === true;
      return new Response(
        JSON.stringify({
          success: good,
          hostname: mode === "wrong-host" ? "evil.example" : HOST,
          action: mode === "wrong-action" ? "send" : "gate",
          challenge_ts: new Date().toISOString(),
          "error-codes": good ? [] : ["invalid-input-response"],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    return new Response(null, { status: opts.sink ?? 204 });
  }) as typeof fetch);
  return {
    calls,
    siteverify: () => calls.filter((c) => c.url.includes("siteverify")).length,
    sink: () => calls.filter((c) => c.url.startsWith("https://discord.com/")),
    restore: () => spy.mockRestore(),
  };
}

export function kv(page: string | null = `<!doctype html><title>kit</title><p>${CANARY}`): { get: ReturnType<typeof vi.fn> } {
  return { get: vi.fn(async (k: string) => (k === "kit/v1" ? page : null)) };
}

export type AbuseEnv = Record<string, unknown> & { DB: D1Database; KIT: { get: ReturnType<typeof vi.fn> } };

export function makeEnv(over: Record<string, unknown> = {}): AbuseEnv {
  return {
    DB: testEnv.DB,
    LEADS_DB: testEnv.LEADS_DB, // leads and kit events live in their own database (issue #772)
    SITE_HOST: HOST,
    KIT: kv(),
    GATE_LIMITER: limiter(),
    KIT_VIEW_LIMITER: limiter(),
    GATE_CHECK_LIMITER: limiter(),
    INQUIRY_LIMITER: limiter(),
    TOKEN_HMAC_KEY: KEY,
    TURNSTILE_SECRET: "1x0000000000000000000000000000000AA",
    VIEW_HASH_SECRET: btoa("s".repeat(40)),
    ALERT_DEDUP_KEY: "d".repeat(40),
    ALERTS_CONFIG: JSON.stringify({ channels: ["discord"], events: ["kit_unlocked", "inquiry_received", "analytics_health"] }),
    ALERT_DISCORD_WEBHOOK: HOOK,
    ...over,
  } as AbuseEnv;
}

export interface ReqOpts {
  fields?: Record<string, string> | string;
  headers?: Record<string, string | null>;
  method?: string;
  path?: string;
  cookie?: string | null;
  body?: BodyInit | null;
}

export function gateReq(o: ReqOpts = {}): Request {
  const fields = o.fields ?? { email: EMAIL, "cf-turnstile-response": "ok" };
  const text = typeof fields === "string" ? fields : new URLSearchParams(fields).toString();
  return build(o, text, {
    "content-type": "application/x-www-form-urlencoded",
    origin: ORIGIN,
    "cf-connecting-ip": CLIENT_IP,
    "cf-ipcountry": "US",
    "user-agent": FIREFOX,
  });
}

export function build(o: ReqOpts, text: string, base: Record<string, string>): Request {
  const headers: Record<string, string> = { ...base };
  if (o.cookie) headers.cookie = o.cookie;
  for (const [k, v] of Object.entries(o.headers ?? {})) {
    if (v === null) delete headers[k];
    else headers[k] = v;
  }
  const method = o.method ?? "POST";
  const hasBody = method !== "GET" && method !== "HEAD";
  const body = o.body !== undefined ? o.body : hasBody ? text : undefined;
  const init: RequestInit & { duplex?: string } = { method, headers, body: hasBody ? body : undefined };
  if (body instanceof ReadableStream) init.duplex = "half";
  return new Request(`${ORIGIN}${o.path ?? "/k/gate"}`, init);
}

export function viewReq(o: ReqOpts = {}): Request {
  return build({ method: "GET", path: "/k/view", ...o }, "", {
    origin: ORIGIN,
    "cf-connecting-ip": CLIENT_IP,
    "cf-ipcountry": "US",
    "user-agent": FIREFOX,
  });
}

export const INQUIRY_GOOD = { name: "Pat Example", email: EMAIL, brand: "Example Brand", budget: "5000 USD", details: "A sponsored stream." };

export function inquiryReq(o: ReqOpts = {}): Request {
  const fields = o.fields ?? INQUIRY_GOOD;
  const text = typeof fields === "string" ? fields : new URLSearchParams(fields).toString();
  return build({ path: "/k/inquiry", ...o }, text, {
    "content-type": "application/x-www-form-urlencoded",
    origin: ORIGIN,
    "cf-connecting-ip": CLIENT_IP,
  });
}

const pending: Promise<unknown>[] = [];
/** Send a request through the real Worker entry point and let background work (alerts) finish. */
export async function send(request: Request, env: AbuseEnv = makeEnv()): Promise<Response> {
  const ctx = { waitUntil: (p: Promise<unknown>) => void pending.push(p), passThroughOnException: () => undefined };
  const res = await worker.fetch(request, env as never, ctx as never);
  await Promise.all(pending.splice(0));
  return res;
}

export async function resetState(): Promise<void> {
  resetEventCapCache();
  await testEnv.LEADS_DB.batch([
    testEnv.LEADS_DB.prepare("DELETE FROM kit_events"),
    testEnv.LEADS_DB.prepare("DELETE FROM leads"),
    testEnv.LEADS_DB.prepare("DELETE FROM kit_meta"),
  ]);
  await testEnv.DB.batch([
    testEnv.DB.prepare("DELETE FROM counters"),
    testEnv.DB.prepare("DELETE FROM kit_meta"),
    testEnv.DB.prepare("DELETE FROM alert_quota"),
    testEnv.DB.prepare("DELETE FROM alert_dedup"),
  ]);
}

export async function count(table: string, where = ""): Promise<number> {
  const db = table === "leads" || table === "kit_events" ? testEnv.LEADS_DB : testEnv.DB;
  return (await db.prepare(`SELECT COUNT(*) AS n FROM ${table} ${where}`).first<{ n: number }>())!.n;
}

export function setCookieOf(res: Response): string | null {
  return res.headers.get("set-cookie");
}
/** `name=value` of the unlock cookie a response set (for sending back). */
export function cookiePair(res: Response): string {
  return setCookieOf(res)!.split(";")[0]!;
}
export function tokenOf(res: Response): string {
  return cookiePair(res).slice(COOKIE_NAME.length + 1);
}

/** Unlock through the real gate and return the cookie pair. */
let tokenSeq = 0;
export async function unlock(env: AbuseEnv = makeEnv(), email = EMAIL, token = `ok-${++tokenSeq}`): Promise<string> {
  const res = await send(gateReq({ fields: { email, "cf-turnstile-response": token } }), env);
  if (setCookieOf(res) === null) throw new Error("harness: the gate did not unlock");
  return cookiePair(res);
}

export function nowSec(): number {
  return Math.floor(Date.now() / 1000);
}

/** A signed cookie pair for `lid` (a lead row must exist for it to open anything). */
export async function signedCookie(over: { lid?: string; iat?: number; exp?: number; key?: string; cc?: string } = {}): Promise<string> {
  const iat = over.iat ?? nowSec() - 30;
  const exp = over.exp ?? iat + 3600;
  return `${COOKIE_NAME}=${await signToken(over.key ?? KEY, { lid: over.lid ?? LEAD_ID, iat, exp, cc: over.cc ?? "" })}`;
}

const enc = new TextEncoder();
function b64url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
/**
 * A token with a VALID signature over an arbitrary payload text, so a test can send claims that
 * signToken itself would refuse to write (the verifier must still refuse them).
 */
export async function forgeToken(payloadText: string, key = KEY, version = "v1"): Promise<string> {
  const body = `${version}.${b64url(enc.encode(payloadText))}`;
  const k = await crypto.subtle.importKey("raw", enc.encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", k, enc.encode(body)));
  return `${body}.${b64url(mac)}`;
}
export function jti(): string {
  return b64url(crypto.getRandomValues(new Uint8Array(16)));
}
/** Claims that pass every check; a test overrides one field. */
export function goodClaims(over: Record<string, unknown> = {}): Record<string, unknown> {
  const iat = nowSec() - 30;
  return { lid: LEAD_ID, iat, exp: iat + 3600, cc: "", jti: jti(), ...over };
}

export async function insertLead(id = LEAD_ID, email = EMAIL, sessionFloorMs: number | null = null): Promise<void> {
  const t = Date.now() - 86_400_000;
  await testEnv.LEADS_DB.prepare("INSERT INTO leads (id, email, created_ts, last_seen_ts, source, session_floor_ts) VALUES (?1, ?2, ?3, ?3, 'gate', ?4)")
    .bind(id, email, t, sessionFloorMs)
    .run();
}

/** DB handles that count every statement prepared through them (to prove a refusal touched nothing):
 *  the analytics database and the leads database share one log. */
export function countingDb(): { db: D1Database; leads: D1Database; sql: string[] } {
  const sql: string[] = [];
  const wrap = (real: D1Database): D1Database =>
    new Proxy(real, {
      get(target, prop) {
        if (prop === "prepare") {
          return (q: string) => {
            sql.push(q);
            return target.prepare(q);
          };
        }
        if (prop === "batch") {
          return (stmts: D1PreparedStatement[]) => {
            sql.push(`batch:${stmts.length}`);
            return target.batch(stmts);
          };
        }
        const v = Reflect.get(target, prop) as unknown;
        return typeof v === "function" ? (v as (...a: unknown[]) => unknown).bind(target) : v;
      },
    }) as D1Database;
  return { db: wrap(testEnv.DB), leads: wrap(testEnv.LEADS_DB), sql };
}

/** Capture everything written to the console while `fn` runs. */
export async function captureConsole<T>(fn: () => Promise<T>): Promise<{ out: string; value: T }> {
  const spies = (["log", "info", "warn", "error", "debug"] as const).map((m) => vi.spyOn(console, m).mockImplementation(() => undefined));
  try {
    const value = await fn();
    return { out: spies.flatMap((s) => s.mock.calls).flat().map(String).join("\n"), value };
  } finally {
    for (const s of spies) s.mockRestore();
  }
}

/** Every header and the body of a response as one string, to search for leaked text. */
export async function everything(res: Response): Promise<string> {
  const h = [...res.headers].map(([k, v]) => `${k}: ${v}`).join("\n");
  return `${res.status}\n${h}\n${await res.clone().text()}`;
}
