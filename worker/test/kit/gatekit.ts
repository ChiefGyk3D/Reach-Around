// SPDX-License-Identifier: Apache-2.0
// Shared test kit for the gate and alert tests: a fake edge around the real
// handlers with a real (in-memory) D1, a fake KV, limiter, Turnstile and
// alert sinks. Everything uses example.test addresses.
import { env as testEnv } from "cloudflare:workers";
import { vi } from "vitest";
import type { GateEnv } from "../../src/kit/gate";

export const HOST = "links.example.test";
export const NOW = Date.UTC(2027, 0, 1, 12, 0, 0);
export const KEY = "k".repeat(40);
export const OTHER_KEY = "o".repeat(40);
export const CANARY = "CANARY-GATED-7f3a";
export const IP = "203.0.113.9";
export const UA = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0";
export const DISCORD = "https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz_-ABCDEF";
export const MATRIX = {
  ALERT_MATRIX_HOMESERVER: "https://matrix.example.test",
  ALERT_MATRIX_ROOM_ID: "!room1:matrix.example.test",
  ALERT_MATRIX_ACCESS_TOKEN: "syt_exampletokenvalue",
};

export const DB = testEnv.DB;

export async function resetDb(): Promise<void> {
  await DB.prepare("DELETE FROM kit_events").run();
  await DB.prepare("DELETE FROM leads").run();
  await DB.prepare("DELETE FROM counters").run();
}

export interface Fakes {
  statements: string[];
  limiterKeys: string[];
  fetchCalls: Array<{ url: string; init: RequestInit }>;
  waiting: Promise<unknown>[];
  failSql?: RegExp;
  limiter: "allow" | "deny" | "throw";
  kv: Map<string, string>;
  kvThrows: boolean;
  turnstile: "ok" | "fail" | "throw" | "wrong-host" | "wrong-action";
  alert: "ok" | "500" | "throw" | "hang" | "flood";
  /** Hostname the fake Turnstile reports. */
  tsHost: string;
}

export function newFakes(): Fakes {
  return {
    statements: [],
    limiterKeys: [],
    fetchCalls: [],
    waiting: [],
    limiter: "allow",
    kv: new Map([["kit/v1", `<!doctype html><title>Kit</title><p>${CANARY}`]]),
    kvThrows: false,
    turnstile: "ok",
    alert: "ok",
    tsHost: HOST,
  };
}

export function ctxOf(f: Fakes) {
  return { waitUntil: (p: Promise<unknown>) => void f.waiting.push(p) };
}

export async function settle(f: Fakes): Promise<void> {
  await Promise.allSettled(f.waiting);
}

function spyDb(f: Fakes): D1Database {
  return new Proxy(DB, {
    get(target, prop) {
      if (prop === "prepare") {
        return (sql: string) => {
          f.statements.push(sql);
          if (f.failSql !== undefined && f.failSql.test(sql)) throw new Error("injected database failure");
          return target.prepare(sql);
        };
      }
      const v = Reflect.get(target, prop) as unknown;
      return typeof v === "function" ? (v as (...a: unknown[]) => unknown).bind(target) : v;
    },
  });
}

export function makeEnv(f: Fakes, over: Record<string, unknown> = {}): GateEnv {
  const base: Record<string, unknown> = {
    DB: spyDb(f),
    SITE_HOST: HOST,
    LINK_IDS: '["example-one"]',
    KIT: {
      get: async (k: string) => {
        if (f.kvThrows) throw new Error("kv down");
        return f.kv.get(k) ?? null;
      },
    },
    GATE_LIMITER: {
      limit: async ({ key }: { key: string }) => {
        f.limiterKeys.push(key);
        if (f.limiter === "throw") throw new Error("limiter down");
        return { success: f.limiter === "allow" };
      },
    },
    CLICK_LIMITER: { limit: async () => ({ success: true }) },
    TOKEN_HMAC_KEY: KEY,
    TURNSTILE_SECRET: "ts-secret-example",
    ...over,
  };
  for (const k of Object.keys(base)) if (base[k] === undefined) delete base[k];
  return base as unknown as GateEnv;
}

export function fakeFetch(f: Fakes): typeof fetch {
  return (async (input: unknown, init?: RequestInit) => {
    const url = String(input);
    f.fetchCalls.push({ url, init: init ?? {} });
    if (url.startsWith("https://challenges.cloudflare.com/")) {
      if (f.turnstile === "throw") throw new Error("network");
      const body = String(init?.body);
      const ok = f.turnstile !== "fail" && new URLSearchParams(body).get("response") === "ok";
      return new Response(
        JSON.stringify({
          success: ok,
          action: f.turnstile === "wrong-action" ? "send" : "gate",
          hostname: f.turnstile === "wrong-host" ? "evil.example.test" : f.tsHost,
          "error-codes": ok ? [] : ["invalid-input-response"],
        }),
        { status: 200 },
      );
    }
    if (f.alert === "throw") throw new Error("alert sink down");
    if (f.alert === "hang") return new Promise<Response>(() => undefined);
    if (f.alert === "500") return new Response("upstream says: secret details", { status: 500 });
    if (f.alert === "flood") {
      const chunk = new Uint8Array(65536).fill(65);
      return new Response(
        new ReadableStream({
          pull(c) {
            c.enqueue(chunk);
          },
        }),
        { status: 200 },
      );
    }
    return new Response(null, { status: 204 });
  }) as typeof fetch;
}

export function alertCalls(f: Fakes) {
  return f.fetchCalls.filter((c) => !c.url.startsWith("https://challenges.cloudflare.com/"));
}

export interface PostOpts {
  fields?: Record<string, string> | string;
  headers?: Record<string, string | null>;
  method?: string;
  path?: string;
}

/** A gate post with sensible defaults; a header set to null is removed. */
export function gatePost(o: PostOpts = {}): Request {
  const fields = o.fields ?? { email: "partner@example.test", "cf-turnstile-response": "ok", website: "" };
  const body = typeof fields === "string" ? fields : new URLSearchParams(fields).toString();
  const h: Record<string, string | null> = {
    Origin: `https://${HOST}`,
    "User-Agent": UA,
    "Content-Type": "application/x-www-form-urlencoded",
    "Content-Length": String(new TextEncoder().encode(body).length),
    "CF-Connecting-IP": IP,
    "CF-IPCountry": "US",
    Referer: `https://${HOST}/kit/`,
    ...o.headers,
  };
  const headers = new Headers();
  for (const [k, v] of Object.entries(h)) if (v !== null) headers.set(k, v);
  const method = o.method ?? "POST";
  return new Request(`https://${HOST}${o.path ?? "/k/gate"}`, {
    method,
    headers,
    body: method === "GET" || method === "HEAD" ? undefined : body,
  });
}

export function cookieOf(res: Response): string | null {
  const c = res.headers.get("Set-Cookie");
  if (c === null) return null;
  const m = /^__Host-ra_kit=([^;]+);/.exec(c);
  return m === null ? null : m[1]!;
}

export function viewReq(cookie: string | null, extra: Record<string, string> = {}, path = "/k/view", method = "GET"): Request {
  const headers = new Headers(extra);
  if (cookie !== null) headers.set("Cookie", `__Host-ra_kit=${cookie}`);
  return new Request(`https://${HOST}${path}`, { method, headers });
}

/** Capture the kit's log lines (the only console writer is log.ts). */
export function captureLogs(): { lines: string[]; restore(): void } {
  const lines: string[] = [];
  const spy = vi.spyOn(console, "log").mockImplementation((...a: unknown[]) => void lines.push(a.map(String).join(" ")));
  return { lines, restore: () => spy.mockRestore() };
}
