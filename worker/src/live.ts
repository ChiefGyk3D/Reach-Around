// SPDX-License-Identifier: Apache-2.0
//
// Live status for the Live tab (issue #297, slice 4). NOT deployed by default:
// nothing here runs until the owner adds the KV binding, the 2-minute cron and
// the platform credentials (all commented placeholders in wrangler.toml).
//
// Two halves:
//   * `GET /e/live` serves one tiny cached JSON document: booleans and
//     timestamps ONLY (no titles, viewer counts, thumbnails or platform text).
//     It is the single deliberate exception to "this Worker returns no stored
//     data". It takes no parameters; the query string is ignored.
//   * `runLivePoller` (the 2-minute cron) asks Twitch and Kick whether the
//     configured channels are live and writes the answer to KV, but only when
//     something changed, plus a heartbeat every 10 minutes.
//
// Rules that hold everywhere in this file:
//   * No console output at all. Platform responses (the Kick body carries a
//     stream key) are parsed by an explicit allowlist and the rest is dropped.
//   * Handles come from env, validated by the same patterns as the config
//     model, never from a request.
//   * Secrets are read from env bindings by name and never printed or stored
//     anywhere but the Authorization / form body of the call that needs them.

/** The slice of a KV namespace this module uses. */
export interface LiveKV {
  get(key: string): Promise<string | null>;
  put(key: string, value: string, options?: { expirationTtl?: number }): Promise<void>;
  delete(key: string): Promise<void>;
}

export interface LiveEnv {
  /** KV namespace binding (placeholder in wrangler.toml). Absent: the route serves the degraded body. */
  LIVE_KV?: LiveKV;
  /** Kill switch: exact true/false forms; anything else fails closed to the degraded body. */
  LIVE_KILL?: string;
  /** Twitch login (config model pattern). Unset or invalid: Twitch is never polled. */
  LIVE_TWITCH_HANDLE?: string;
  /** Kick slug (config model pattern). Unset or invalid: Kick is never polled. */
  LIVE_KICK_HANDLE?: string;
  /** Secrets, by name only. */
  TWITCH_CLIENT_ID?: string;
  TWITCH_CLIENT_SECRET?: string;
  KICK_CLIENT_ID?: string;
  KICK_CLIENT_SECRET?: string;
}

/** The cron expression that runs ONLY the poller (must match wrangler.toml). */
export const LIVE_CRON = "*/2 * * * *";
export const LIVE_ROUTE = "/e/live";
export const POLL_INTERVAL_SECONDS = 120;
/** Max-Age of the served document and TTL field, seconds. */
export const SERVE_TTL_SECONDS = 30;
/** A KV write is forced at least this often while the daily cap allows. */
export const HEARTBEAT_SECONDS = 600;
/**
 * as_of older than this is served as unknown. Three heartbeats, not three poll
 * intervals: with writes only on change the stored as_of moves every 10
 * minutes, so 3 x 120 s would flap to unknown between heartbeats.
 */
export const STALE_AFTER_SECONDS = 3 * HEARTBEAT_SECONDS;
/** Past this many state writes in a UTC day the heartbeat is skipped (data goes stale: an outage, not a bill). */
export const DAILY_WRITE_CAP = 800;

const STATE_KEY = "live:state";
const TOKEN_KEY = { twitch: "live:token:twitch", kick: "live:token:kick" } as const;
/** Refresh a cached app token this long before it expires. */
const TOKEN_MARGIN_SECONDS = 300;
const FETCH_TIMEOUT_MS = 5000;
const MAX_BODY_CHARS = 65536;

// Same patterns as src/reach_around/config/blocks.py, anchored.
const TWITCH_HANDLE = /^[A-Za-z0-9_]{1,25}$/;
const KICK_HANDLE = /^[A-Za-z0-9_-]{1,40}$/;

export function validTwitchHandle(v: unknown): v is string {
  return typeof v === "string" && TWITCH_HANDLE.test(v);
}
export function validKickHandle(v: unknown): v is string {
  return typeof v === "string" && KICK_HANDLE.test(v);
}

/** live: null means unknown. since: unix seconds, only when live. */
export interface Platform {
  live: boolean | null;
  since: number | null;
}

interface StoredState {
  v: 1;
  as_of: number;
  kick: Platform;
  twitch: Platform;
  day: string;
  writes: number;
}

const UNKNOWN: Platform = { live: null, since: null };

// ---------------------------------------------------------------- kill switch

const KILL_ON = new Set(["1", "true", "on", "yes"]);
const KILL_OFF = new Set(["0", "false", "off", "no"]);

/** Same strict parse as KIT_KILL: unset or "" is off; an exact form is on/off; anything else is invalid. */
function killState(v: unknown): "on" | "off" | "invalid" {
  if (v === undefined || v === "") return "off";
  if (typeof v !== "string") return "invalid";
  const s = v.toLowerCase();
  if (KILL_ON.has(s)) return "on";
  if (KILL_OFF.has(s)) return "off";
  return "invalid";
}

/** True when the route must serve the degraded body. Fails closed. */
export function isLiveKilled(env: Pick<LiveEnv, "LIVE_KILL">): boolean {
  return killState(env?.LIVE_KILL) !== "off";
}

// ------------------------------------------------------------------- serving

function body(asOf: number | null, kick: Platform, twitch: Platform): string {
  // Built field by field: stored extras can never reach the response.
  return JSON.stringify({
    v: 1,
    as_of: asOf,
    ttl: SERVE_TTL_SECONDS,
    kick: { live: kick.live, since: kick.since },
    twitch: { live: twitch.live, since: twitch.since },
  });
}

function jsonResponse(text: string, maxAge: number = SERVE_TTL_SECONDS): Response {
  return new Response(text, {
    status: 200,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": `public, max-age=${maxAge}`,
      "X-Content-Type-Options": "nosniff",
    },
  });
}

function degraded(): Response {
  return jsonResponse(body(null, UNKNOWN, UNKNOWN));
}

function emptyNoContent(): Response {
  return new Response(null, { status: 204 });
}

function platformOrNull(v: unknown): Platform | null {
  if (typeof v !== "object" || v === null) return null;
  const o = v as Record<string, unknown>;
  const live = o.live;
  const since = o.since;
  if (live !== true && live !== false && live !== null) return null;
  if (since !== null && !(typeof since === "number" && Number.isFinite(since))) return null;
  return { live, since: live === true ? (since as number | null) : null };
}

function parseState(raw: string | null): StoredState | null {
  if (raw === null) return null;
  try {
    const o = JSON.parse(raw) as Record<string, unknown>;
    const kick = platformOrNull(o.kick);
    const twitch = platformOrNull(o.twitch);
    if (o.v !== 1 || !kick || !twitch) return null;
    if (typeof o.as_of !== "number" || !Number.isFinite(o.as_of)) return null;
    return {
      v: 1,
      as_of: o.as_of,
      kick,
      twitch,
      day: typeof o.day === "string" ? o.day : "",
      writes: typeof o.writes === "number" && Number.isFinite(o.writes) ? o.writes : 0,
    };
  } catch {
    return null;
  }
}

/** The body for the current stored state: stale or missing data is unknown. */
export function bodyFromState(state: StoredState | null, nowSeconds: number): string {
  if (state === null) return body(null, UNKNOWN, UNKNOWN);
  if (nowSeconds - state.as_of > STALE_AFTER_SECONDS) {
    return body(state.as_of, UNKNOWN, UNKNOWN);
  }
  return body(state.as_of, state.kick, state.twitch);
}

function cachedAsOf(text: string): number | null {
  try {
    const a = (JSON.parse(text) as { as_of?: unknown }).as_of;
    return typeof a === "number" && Number.isFinite(a) ? a : null;
  } catch {
    return null;
  }
}

export interface LiveDeps {
  cache?: Pick<Cache, "match" | "put">;
  now?: () => number;
}

/** Handle `GET /e/live`. Other methods get the Worker's usual empty 204. */
export async function handleLive(
  request: Request,
  env: LiveEnv,
  deps: LiveDeps = {},
): Promise<Response> {
  if (request.method !== "GET") return emptyNoContent();
  try {
    // Kill switch first, every request, before any cache or KV read.
    if (isLiveKilled(env)) return degraded();

    const url = new URL(request.url);
    url.search = ""; // the query string is ignored, and never part of the cache key
    url.hash = "";
    const key = new Request(url.toString(), { method: "GET" });
    const cache = deps.cache ?? caches.default;

    const nowSeconds = Math.floor((deps.now ? deps.now() : Date.now()) / 1000);
    const hit = await cache.match(key);
    if (hit) {
      // A cached copy never outlives the freshness cutoff: re-check the as_of
      // it carries before serving it.
      const text = await hit.clone().text();
      const asOf = cachedAsOf(text);
      if (asOf !== null && nowSeconds - asOf > STALE_AFTER_SECONDS) {
        return jsonResponse(body(asOf, UNKNOWN, UNKNOWN));
      }
      return hit;
    }

    let state: StoredState | null = null;
    try {
      state = env.LIVE_KV ? parseState(await env.LIVE_KV.get(STATE_KEY)) : null;
    } catch {
      state = null;
    }
    const fresh = state !== null && nowSeconds - state.as_of <= STALE_AFTER_SECONDS;
    // A fresh state may be cached only until its cutoff.
    const maxAge = fresh
      ? Math.min(SERVE_TTL_SECONDS, Math.max(0, state!.as_of + STALE_AFTER_SECONDS - nowSeconds))
      : SERVE_TTL_SECONDS;
    const res = jsonResponse(bodyFromState(state, nowSeconds), maxAge);
    try {
      await cache.put(key, res.clone());
    } catch {
      // Serving without the edge copy is fine.
    }
    return res;
  } catch {
    return degraded();
  }
}

// -------------------------------------------------------------------- poller

type FetchFn = (input: string, init?: RequestInit) => Promise<Response>;

export interface PollDeps {
  fetch?: FetchFn;
}

async function readLimited(res: Response): Promise<unknown> {
  const text = await res.text();
  if (text.length > MAX_BODY_CHARS) throw new Error("body too large");
  return JSON.parse(text);
}

function toSeconds(iso: unknown): number | null {
  if (typeof iso !== "string") return null;
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
}

function asObject(v: unknown): Record<string, unknown> | null {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

// ---- Replaceable request builders and parsers. Each is small on purpose.

/** verify against the current Twitch docs before deploy: token endpoint, method, form fields. */
export function twitchTokenRequest(id: string, secret: string): { url: string; init: RequestInit } {
  return {
    url: "https://id.twitch.tv/oauth2/token",
    init: {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: id,
        client_secret: secret,
        grant_type: "client_credentials",
      }).toString(),
    },
  };
}

/** verify against the current Twitch docs before deploy: Get Streams URL, user_login parameter, headers. */
export function twitchStreamsRequest(
  handle: string,
  id: string,
  token: string,
): { url: string; init: RequestInit } {
  return {
    url: `https://api.twitch.tv/helix/streams?user_login=${encodeURIComponent(handle)}`,
    init: { method: "GET", headers: { Authorization: `Bearer ${token}`, "Client-Id": id } },
  };
}

/** verify against the current Twitch docs before deploy: response shape (data[], type, started_at). Allowlist only. */
export function parseTwitchStreams(json: unknown): Platform | null {
  const root = asObject(json);
  if (!root || !Array.isArray(root.data)) return null;
  const first = root.data.length > 0 ? asObject(root.data[0]) : null;
  if (first === null) return { live: false, since: null };
  if (first.type !== "live") return { live: false, since: null };
  return { live: true, since: toSeconds(first.started_at) };
}

/** verify against the current Kick developer docs before deploy: token endpoint, method, form fields. */
export function kickTokenRequest(id: string, secret: string): { url: string; init: RequestInit } {
  return {
    url: "https://id.kick.com/oauth/token",
    init: {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: id,
        client_secret: secret,
        grant_type: "client_credentials",
      }).toString(),
    },
  };
}

/** verify against the current Kick developer docs before deploy: Channels URL and slug parameter. */
export function kickChannelRequest(handle: string, token: string): { url: string; init: RequestInit } {
  return {
    url: `https://api.kick.com/public/v1/channels?slug=${encodeURIComponent(handle)}`,
    init: { method: "GET", headers: { Authorization: `Bearer ${token}` } },
  };
}

/**
 * verify against the current Kick developer docs before deploy: response shape.
 * The channel's stream object carries a stream KEY. Only is_live and start_time
 * are read; every other field is discarded unread and never logged or stored.
 */
export function parseKickChannel(json: unknown): Platform | null {
  const root = asObject(json);
  if (!root || !Array.isArray(root.data) || root.data.length === 0) return null;
  const channel = asObject(root.data[0]);
  const stream = channel ? asObject(channel.stream) : null;
  if (stream === null) return null;
  const isLive = stream.is_live;
  if (isLive !== true && isLive !== false) return null;
  return isLive ? { live: true, since: toSeconds(stream.start_time) } : { live: false, since: null };
}

// ---- Token cache (KV, until near expiry)

async function appToken(
  kv: LiveKV,
  which: "twitch" | "kick",
  request: { url: string; init: RequestInit },
  nowSeconds: number,
  f: FetchFn,
): Promise<string | null> {
  try {
    const cached = JSON.parse((await kv.get(TOKEN_KEY[which])) ?? "null") as Record<string, unknown> | null;
    if (
      cached &&
      typeof cached.t === "string" &&
      typeof cached.exp === "number" &&
      cached.exp - TOKEN_MARGIN_SECONDS > nowSeconds
    ) {
      return cached.t;
    }
  } catch {
    // Unreadable cache entry: fetch a fresh token.
  }
  const res = await f(request.url, { ...request.init, signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) });
  if (!res.ok) return null;
  const json = asObject(await readLimited(res));
  const token = json?.access_token;
  const ttl = json?.expires_in;
  if (typeof token !== "string" || token.length === 0 || typeof ttl !== "number" || !(ttl > 0)) return null;
  const exp = nowSeconds + Math.floor(ttl);
  // KV needs at least 60 s of TTL.
  await kv.put(TOKEN_KEY[which], JSON.stringify({ t: token, exp }), {
    expirationTtl: Math.max(60, Math.floor(ttl)),
  });
  return token;
}

async function pollTwitch(env: LiveEnv, kv: LiveKV, nowSeconds: number, f: FetchFn): Promise<Platform> {
  const handle = env.LIVE_TWITCH_HANDLE;
  const id = env.TWITCH_CLIENT_ID;
  const secret = env.TWITCH_CLIENT_SECRET;
  if (!validTwitchHandle(handle) || !id || !secret) return UNKNOWN;
  try {
    const token = await appToken(kv, "twitch", twitchTokenRequest(id, secret), nowSeconds, f);
    if (token === null) return UNKNOWN;
    const req = twitchStreamsRequest(handle, id, token);
    const res = await f(req.url, { ...req.init, signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) });
    if (res.status === 401) {
      await kv.delete(TOKEN_KEY.twitch);
      return UNKNOWN;
    }
    if (!res.ok) return UNKNOWN;
    return parseTwitchStreams(await readLimited(res)) ?? UNKNOWN;
  } catch {
    return UNKNOWN;
  }
}

async function pollKick(env: LiveEnv, kv: LiveKV, nowSeconds: number, f: FetchFn): Promise<Platform> {
  const handle = env.LIVE_KICK_HANDLE;
  const id = env.KICK_CLIENT_ID;
  const secret = env.KICK_CLIENT_SECRET;
  if (!validKickHandle(handle) || !id || !secret) return UNKNOWN;
  try {
    const token = await appToken(kv, "kick", kickTokenRequest(id, secret), nowSeconds, f);
    if (token === null) return UNKNOWN;
    const req = kickChannelRequest(handle, token);
    const res = await f(req.url, { ...req.init, signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) });
    if (res.status === 401) {
      await kv.delete(TOKEN_KEY.kick);
      return UNKNOWN;
    }
    if (!res.ok) return UNKNOWN;
    return parseKickChannel(await readLimited(res)) ?? UNKNOWN;
  } catch {
    return UNKNOWN;
  }
}

function same(a: Platform, b: Platform): boolean {
  return a.live === b.live && a.since === b.since;
}

/**
 * One poll. Writes the state to KV only when a platform's state changed, or as
 * a heartbeat when 10 minutes have passed and today's write count is under the
 * cap. A failed lookup counts as "unknown" (written once, on the change). Never
 * throws and never logs.
 */
export async function runLivePoller(
  env: LiveEnv,
  nowMs: number,
  deps: PollDeps = {},
): Promise<void> {
  try {
    const kv = env.LIVE_KV;
    if (!kv || isLiveKilled(env)) return;
    const f: FetchFn = deps.fetch ?? ((input, init) => fetch(input, init));
    const nowSeconds = Math.floor(nowMs / 1000);

    let prev: StoredState | null = null;
    try {
      prev = parseState(await kv.get(STATE_KEY));
    } catch {
      prev = null;
    }

    const [kick, twitch] = await Promise.all([
      pollKick(env, kv, nowSeconds, f),
      pollTwitch(env, kv, nowSeconds, f),
    ]);

    const day = new Date(nowMs).toISOString().slice(0, 10);
    const writesToday = prev !== null && prev.day === day ? prev.writes : 0;
    const changed = prev === null || !same(prev.kick, kick) || !same(prev.twitch, twitch);
    const heartbeatDue =
      prev !== null && nowSeconds - prev.as_of >= HEARTBEAT_SECONDS && writesToday < DAILY_WRITE_CAP;
    if (!changed && !heartbeatDue) return;

    const next: StoredState = { v: 1, as_of: nowSeconds, kick, twitch, day, writes: writesToday + 1 };
    await kv.put(STATE_KEY, JSON.stringify(next));
  } catch {
    // Next run retries; nothing here is safe to report.
  }
}
