// SPDX-License-Identifier: Apache-2.0
// Shared test helpers for the page-view route. Synthetic data only.
import { env as testEnv } from "cloudflare:workers";
import { vi } from "vitest";
import type { ViewEnv } from "../../src/view";

export const SITE = "links.example.test";
export const IP4 = "198.51.100.7";
export const IP6 = "2001:db8::7";
export const CHROME_UA =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.6613.84 Safari/537.36";
export const SECRET = btoa("a".repeat(32));
export const SECRET_2 = btoa("b".repeat(32));
/** 2027-03-14T12:00:30Z: an instant with a non-zero seconds part. */
export const NOW = Date.parse("2027-03-14T12:00:30Z");

export const DB = testEnv.DB;

/** Test double for the Workers Rate Limiting binding. */
export function fakeLimiter(limit = 100000) {
  const counts = new Map<string, number>();
  return {
    calls: 0,
    keys: [] as string[],
    async limit({ key }: { key: string }) {
      this.calls += 1;
      this.keys.push(key);
      const n = (counts.get(key) ?? 0) + 1;
      counts.set(key, n);
      return { success: n <= limit };
    },
  };
}

export function makeEnv(over: Partial<ViewEnv> = {}): ViewEnv {
  return {
    DB,
    SITE_HOST: SITE,
    PAGE_IDS: JSON.stringify(["home", "support"]),
    VIEW_LIMITER: fakeLimiter(),
    VIEW_HASH_SECRET: SECRET,
    ...over,
  };
}

export interface BeaconOpts {
  path?: string;
  method?: string;
  body?: string;
  /** Header overrides; null removes the header. */
  headers?: Record<string, string | null>;
  cf?: Record<string, unknown>;
  /** Do not set Content-Length. */
  noLength?: boolean;
  stream?: ReadableStream<Uint8Array>;
}

/** A valid beacon unless overridden. */
export function beacon(o: BeaconOpts = {}): Request {
  const body = o.body ?? "r=news.example.test&u=bluesky&w=m";
  const method = o.method ?? "POST";
  const h = new Headers();
  const all: Record<string, string | null> = {
    "User-Agent": CHROME_UA,
    Origin: `https://${SITE}`,
    "CF-Connecting-IP": IP4,
    "CF-IPCountry": "US",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Site": "same-origin",
    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
    ...(o.noLength || o.stream ? {} : { "Content-Length": String(new TextEncoder().encode(body).length) }),
    ...o.headers,
  };
  for (const [k, v] of Object.entries(all)) if (v !== null) h.set(k, v);
  const hasBody = method !== "GET" && method !== "HEAD";
  const init: Record<string, unknown> = { method, headers: h };
  if (hasBody) {
    init.body = o.stream ?? body;
    if (o.stream) init.duplex = "half";
  }
  if (o.cf) init.cf = o.cf;
  return new Request(`https://worker.example.test${o.path ?? "/e/v/home"}`, init as RequestInit);
}

export type Row = {
  id: number;
  ts: number;
  page: string;
  src_kind: string;
  src: string;
  country: string;
  region: string;
  city: string;
  browser: string;
  bmajor: number;
  os: string;
  device: string;
  lang: string;
  vp: string;
  net: string;
  bot_class: string;
  reason: string;
  first: number;
};

export async function rows(): Promise<Row[]> {
  return (await DB.prepare("SELECT * FROM view_events ORDER BY id").all<Row>()).results;
}

export async function seen(): Promise<Array<{ day: string; h: string }>> {
  return (await DB.prepare("SELECT day, h FROM visitor_seen ORDER BY day, h").all<{
    day: string;
    h: string;
  }>()).results;
}

export async function budgets(): Promise<Array<{ key: string; value: string }>> {
  return (await DB.prepare("SELECT key, value FROM view_meta ORDER BY key").all<{
    key: string;
    value: string;
  }>()).results;
}

export async function clearAll(): Promise<void> {
  for (const t of ["view_events", "visitor_seen", "view_meta", "view_daily", "view_dim_daily", "view_geo_daily", "view_rollup_log"]) {
    await DB.prepare(`DELETE FROM ${t}`).run();
  }
}

/** Every table dumped as one JSON string, for sentinel scans. */
export async function dumpDatabase(): Promise<string> {
  const tables = await DB.prepare(
    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE '_cf_%'",
  ).all<{ name: string }>();
  const out: Record<string, unknown[]> = {};
  for (const t of tables.results) {
    out[t.name] = (await DB.prepare(`SELECT * FROM ${t.name}`).all()).results;
  }
  return JSON.stringify(out);
}

/** A DB that counts prepare() and batch() calls and sums rows_written of every result. */
export function spyDb(inject?: (stmts: D1PreparedStatement[]) => D1PreparedStatement[]) {
  const calls = { prepare: 0, batch: 0, rowsWritten: 0 };
  const proxy = new Proxy(DB, {
    get(t, prop) {
      if (prop === "prepare") {
        return (q: string) => {
          calls.prepare += 1;
          return t.prepare(q);
        };
      }
      if (prop === "batch") {
        return async (stmts: D1PreparedStatement[]) => {
          calls.batch += 1;
          const res = await t.batch(inject ? inject(stmts) : stmts);
          for (const r of res) calls.rowsWritten += (r.meta as { rows_written?: number }).rows_written ?? 0;
          return res;
        };
      }
      const v = Reflect.get(t, prop, t);
      return typeof v === "function" ? v.bind(t) : v;
    },
  });
  return { DB: proxy as D1Database, calls };
}

/** Every console method spied; returns all text that was printed. */
export function spyConsole() {
  const spies = (["log", "info", "warn", "error", "debug", "trace"] as const).map((m) =>
    vi.spyOn(console, m).mockImplementation(() => undefined),
  );
  return {
    text(): string {
      return spies.map((s) => JSON.stringify(s.mock.calls)).join("\n");
    },
    count(): number {
      return spies.reduce((n, s) => n + s.mock.calls.length, 0);
    },
    restore() {
      for (const s of spies) s.mockRestore();
    },
  };
}

/** A body stream that counts pulls; highWaterMark 0 so it only pulls on a read. */
export function countingStream(bytes: number) {
  const state = { pulls: 0, cancelled: false };
  let sent = false;
  const stream = new ReadableStream<Uint8Array>(
    {
      pull(c) {
        state.pulls += 1;
        if (sent) {
          c.close();
          return;
        }
        sent = true;
        c.enqueue(new Uint8Array(bytes).fill(97));
      },
      cancel() {
        state.cancelled = true;
      },
    },
    { highWaterMark: 0 },
  );
  return { stream, state };
}
