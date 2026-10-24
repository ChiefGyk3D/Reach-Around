// SPDX-License-Identifier: Apache-2.0
//
// The write-only page-view route: POST /e/v/:pageId (Plan 4 Task A4).
//
// Every outcome is the same empty 204. Nothing is ever returned.
//
// PRIVACY CONTRACT
//   - The connection address, the raw User-Agent and Accept-Language are read
//     transiently in memory and reduced to closed categories before the first
//     database call. They are never stored, logged, thrown or echoed.
//   - The only things taken from the request: referrer host, utm label and a
//     window-size letter from a tiny form body; Cloudflare's country, region
//     and city (never coordinates, postal code, organization or ASN); derived
//     UA classes; a bot class; and a daily keyed hash of the address.
//   - The hash is HMAC-SHA-256 under a key derived from a secret and the UTC
//     date, cut to UV_BITS bits (default 32). It lives only in visitor_seen,
//     which has no column in common with view_events, and the daily job
//     deletes it. The same address hashes differently every day.
//   - Nothing here logs. There is no console call in this file.
//
// BEACON CONTRACT (as Plan 4 defines it, not invented here)
//   The click route never reads a body; this route must, because the plan
//   sends `r`, `u` and `w` in a tiny form-encoded body from
//   navigator.sendBeacon. The read is strictly bounded: Content-Length must be
//   present, plain digits, at most MAX_BODY (256) and no Transfer-Encoding;
//   the bytes are counted while reading and the read aborts past the cap. A
//   request refused before the body step has its stream cancelled unread.
//
// DEFAULTS RELIED ON (open decisions in the plan, none decided by anyone)
//   P-3 raw retention 7 days (applies to the rollup job, not this file)
//   P-5 hash width 32 bits, day boundary UTC
//   P-6 write budget 120 human views per hour and 30 bot views per hour,
//       counted in view_meta (an atomic upsert) because Plan 3's reserveRoute
//       is not on main yet. VIEWS_PER_HOUR / BOT_VIEWS_PER_HOUR override.
//   GEO_LEVEL city, BOTS_MODE count, network class "unknown" for now.

import { BOT_REASONS, classifyBot, uaSignals, type BotClass } from "./botclass";
import { MAX_PATH_LENGTH, MAX_URL_LENGTH, originMatches } from "./click";
import type { RateLimiter } from "./click";
import { classifyUserAgent, primaryLanguage } from "./ua";

export interface ViewEnv {
  DB: D1Database;
  SITE_HOST: string;
  /** JSON array of page (tab) ids from the build manifest. */
  PAGE_IDS?: string;
  /** "1" or "true" (any case) turns the route off, read per request. */
  VIEWS_KILL?: string;
  VIEW_LIMITER?: RateLimiter;
  /** Optional second limiter that only feeds the `rate` bot signal. */
  VIEW_SUSPECT_LIMITER?: RateLimiter;
  /** Secret, never in a committed file. Base64, at least 32 bytes decoded. */
  VIEW_HASH_SECRET?: string;
  /** "0" or "false" turns unique-visitor hashing off even with a secret. */
  UV_ENABLED?: string;
  UV_BITS?: string;
  GEO_LEVEL?: string;
  BOTS_MODE?: string;
  VIEWS_PER_HOUR?: string;
  BOT_VIEWS_PER_HOUR?: string;
  /** Per-client hourly cap (default 60, ceiling 1000). Needs VIEW_HASH_SECRET. */
  CLIENT_VIEWS_PER_HOUR?: string;
}

export const MAX_BODY = 256;
const MAX_ID_LENGTH = 40;
const MAX_HOST_LENGTH = 80;
const MAX_CITY_LENGTH = 60;
const MAX_CITY_INPUT = 200;
const DEFAULT_HUMAN_PER_HOUR = 120;
const DEFAULT_BOT_PER_HOUR = 30;
/**
 * One client may spend at most this many accepted views an hour, so a single
 * address cannot drain the global budget. The client is identified only by a
 * keyed hash (see capKey), never the address. CLIENT_VIEWS_PER_HOUR overrides
 * it up to the ceiling; a larger or malformed value falls back to the default.
 */
export const DEFAULT_CLIENT_VIEWS_PER_HOUR = 60;
export const MAX_CLIENT_VIEWS_PER_HOUR = 1000;
const DEFAULT_HASH_BITS = 32;
const MIN_SECRET_BYTES = 32;
const HOUR_MS = 3_600_000;
const MINUTE_MS = 60_000;

const ROUTE = /^\/e\/v\/([a-z0-9][a-z0-9-]{0,39})$/;
const UTM = /^[a-z0-9-]{1,30}$/;
const LABEL = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;
const REGION = /^[A-Z0-9]{1,3}$/;
const COUNTRY = /^[A-Z]{2}$/;
const CITY = /^[\p{L}\p{M} .'-]+$/u;

export type SrcKind = "d" | "i" | "r" | "u";
export type Viewport = "s" | "m" | "l" | "x" | "";
export type GeoLevel = "country" | "region" | "city";

export interface Beacon {
  kind: SrcKind;
  src: string;
  vp: Viewport;
}

/** Closed value sets for the enumerated columns. */
export const BROWSERS = ["chrome", "edge", "firefox", "safari", "opera", "samsung", "brave", "other"];
export const OSES = ["windows", "macos", "linux", "android", "ios", "chromeos", "other"];
export const DEVICES = ["mobile", "tablet", "desktop", "other"];
export const NETS = ["residential", "mobile", "vpn-proxy", "hosting-datacenter", "tor", "unknown"];
const CLASS_LETTER: Record<BotClass, "h" | "v" | "a" | "s"> = {
  "human-likely": "h",
  "verified-crawler": "v",
  automated: "a",
  suspicious: "s",
};

// ---------------------------------------------------------------- helpers

/** The one response. Same status, same headers, no body, whatever happened. */
function noContent(): Response {
  return new Response(null, { status: 204 });
}

export function flagOn(v: string | undefined): boolean {
  return typeof v === "string" && (v.toLowerCase() === "1" || v.toLowerCase() === "true");
}

function idSet(raw: string | undefined): Set<string> {
  try {
    const parsed: unknown = JSON.parse(raw ?? "");
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((v): v is string => typeof v === "string" && v !== ""));
  } catch {
    return new Set();
  }
}

function oneOf(v: string, allowed: readonly string[], fallback: string): string {
  return allowed.includes(v) ? v : fallback;
}

async function discard(body: ReadableStream<Uint8Array> | null): Promise<void> {
  try {
    await body?.cancel();
  } catch {
    // The request is being ignored anyway.
  }
}

function positiveInt(v: string | undefined, fallback: number, max: number): number {
  if (typeof v !== "string" || !/^\d{1,9}$/.test(v)) return fallback;
  const n = Number(v);
  return n <= max ? n : fallback;
}

// ----------------------------------------------------------------- fields

/**
 * A referrer host the beacon may carry: lowercase, at most 80 characters,
 * dot-separated labels of [a-z0-9-] that neither start nor end with "-", at
 * least two labels, a final label with a letter (so IP literals, "localhost",
 * ports, userinfo, paths, queries and fragments all fail). A leading "www."
 * is removed. Returns null when it does not pass.
 */
export function parseHost(raw: string): string | null {
  if (raw.length === 0 || raw.length > MAX_HOST_LENGTH) return null; // CONTROL:pv-parse-host
  let host = raw.toLowerCase();
  if (host.startsWith("www.")) host = host.slice(4);
  const labels = host.split(".");
  if (labels.length < 2) return null;
  for (const label of labels) if (!LABEL.test(label)) return null;
  if (!/[a-z]/.test(labels[labels.length - 1] as string)) return null;
  return host;
}

/** A utm label: [a-z0-9-]{1,30} after lowercasing. */
export function parseUtm(raw: string): string | null {
  const v = raw.toLowerCase();
  return UTM.test(v) ? v : null;
}

/**
 * The form body: only keys r, u, w are read, the first value of each wins,
 * unknown keys are ignored. A key that is present but invalid drops the whole
 * beacon (null). `u` wins over `r`; a host equal to the site's own is kind "i".
 */
export function parseBeacon(text: string, siteHost: string): Beacon | null {
  if (text.length > MAX_BODY) return null;
  let params: URLSearchParams;
  try {
    params = new URLSearchParams(text);
  } catch {
    return null;
  }
  const r = params.get("r");
  const u = params.get("u");
  const w = params.get("w");

  let vp: Viewport = "";
  if (w !== null) {
    if (w !== "s" && w !== "m" && w !== "l" && w !== "x") return null;
    vp = w;
  }

  let host: string | null = null;
  if (r !== null) {
    host = parseHost(r);
    if (host === null) return null;
  }
  let utm: string | null = null;
  if (u !== null) {
    utm = parseUtm(u);
    if (utm === null) return null;
  }

  if (utm !== null) return { kind: "u", src: utm, vp };
  if (host !== null) {
    if (host === siteHost.toLowerCase()) return { kind: "i", src: "", vp };
    return { kind: "r", src: host, vp };
  }
  return { kind: "d", src: "", vp };
}

// -------------------------------------------------------------------- geo

export interface Geo {
  country: string;
  region: string;
  city: string;
}

function readCountry(v: unknown): string {
  return typeof v === "string" && COUNTRY.test(v) && v !== "XX" && v !== "T1" ? v : "";
}

/**
 * Country always; region only at level region or city; city only at level
 * city. Reads only `CF-IPCountry`, `cf.country`, `cf.regionCode` and
 * `cf.city`. Nothing finer exists in this function.
 */
export function geoOf(request: Request, level: GeoLevel): Geo {
  const cf = (request as { cf?: Record<string, unknown> }).cf;
  let country = readCountry(request.headers.get("CF-IPCountry"));
  if (country === "" && cf) country = readCountry(cf.country);
  let region = "";
  let city = "";
  if (cf && (level === "region" || level === "city")) {
    const rc = cf.regionCode;
    if (typeof rc === "string" && REGION.test(rc)) region = rc;
  }
  if (cf && level === "city") {
    const c = cf.city;
    if (typeof c === "string" && c.length > 0 && c.length <= MAX_CITY_INPUT) {
      const n = c.normalize("NFC");
      if (n.length <= MAX_CITY_LENGTH && CITY.test(n)) city = n;
    }
  }
  return { country, region, city };
}

// ---------------------------------------------------------------- visitor

function parseV4(s: string): number[] | null {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(s);
  if (!m) return null;
  const parts = [Number(m[1]), Number(m[2]), Number(m[3]), Number(m[4])];
  return parts.every((p) => p <= 255) ? parts : null;
}

/**
 * IPv4 as canonical dotted text; IPv6 cut to its first 64 bits (so one
 * household's privacy addresses count once); an IPv4-mapped IPv6 address
 * becomes its IPv4 form; anything else is null.
 */
export function normalizeAddress(ip: string | null): string | null {
  if (typeof ip !== "string" || ip.length === 0 || ip.length > 45) return null;
  const v4 = parseV4(ip);
  if (v4) return v4.join(".");
  if (!/^[0-9a-fA-F:.]+$/.test(ip) || !ip.includes(":")) return null;

  let text = ip;
  const lastColon = text.lastIndexOf(":");
  const tail = text.slice(lastColon + 1);
  if (tail.includes(".")) {
    const t = parseV4(tail);
    if (!t) return null;
    const hi = ((t[0] as number) << 8) | (t[1] as number);
    const lo = ((t[2] as number) << 8) | (t[3] as number);
    text = `${text.slice(0, lastColon + 1)}${hi.toString(16)}:${lo.toString(16)}`;
  }
  const halves = text.split("::");
  if (halves.length > 2) return null;
  const toGroups = (s: string): string[] | null => {
    if (s === "") return [];
    const g = s.split(":");
    return g.every((x) => /^[0-9a-fA-F]{1,4}$/.test(x)) ? g : null;
  };
  const head = toGroups(halves[0] as string);
  if (head === null) return null;
  let groups: string[];
  if (halves.length === 2) {
    const rest = toGroups(halves[1] as string);
    if (rest === null || head.length + rest.length > 7) return null;
    groups = [...head, ...Array<string>(8 - head.length - rest.length).fill("0"), ...rest];
  } else {
    if (head.length !== 8) return null;
    groups = head;
  }
  const n = groups.map((g) => Number.parseInt(g, 16));
  if (n.slice(0, 5).every((x) => x === 0) && n[5] === 0xffff) {
    const a = n[6] as number;
    const b = n[7] as number;
    return [a >> 8, a & 255, b >> 8, b & 255].join(".");
  }
  return `v6:${n
    .slice(0, 4)
    .map((x) => x.toString(16).padStart(4, "0"))
    .join("")}`;
}

function utcDate(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

function secretBytes(secret: string | undefined): Uint8Array | null {
  if (typeof secret !== "string" || secret.length === 0 || secret.length > 4096) return null;
  try {
    const bin = atob(secret);
    if (bin.length < MIN_SECRET_BYTES) return null;
    return Uint8Array.from(bin, (c) => c.charCodeAt(0));
  } catch {
    return null;
  }
}

let keyCache: { secret: string; date: string; key: CryptoKey } | null = null;

/** HMAC key for one UTC day: HMAC(secret, "RA-UV1|" + date), cached per date. */
export async function dayKey(secret: Uint8Array, secretText: string, date: string): Promise<CryptoKey> {
  if (keyCache && keyCache.secret === secretText && keyCache.date === date) return keyCache.key;
  const enc = new TextEncoder();
  const root = await crypto.subtle.importKey(
    "raw",
    secret,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const derived = await crypto.subtle.sign("HMAC", root, enc.encode(`RA-UV1|${date}`));
  const key = await crypto.subtle.importKey(
    "raw",
    derived,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  keyCache = { secret: secretText, date, key };
  return key;
}

/** The first `bits` bits of HMAC(dayKey, normalized address), as lowercase hex. */
export async function visitorHash(key: CryptoKey, normalized: string, bits: number): Promise<string> {
  const sig = new Uint8Array(
    await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(normalized)),
  );
  const nbytes = Math.ceil(bits / 8);
  const out = sig.slice(0, nbytes);
  const extra = nbytes * 8 - bits;
  if (extra > 0) out[nbytes - 1] = (out[nbytes - 1] as number) & (0xff << extra) & 0xff;
  let hex = "";
  for (const b of out) hex += b.toString(16).padStart(2, "0");
  return hex.slice(0, Math.ceil(bits / 4));
}

/**
 * Keyed hashes for this request, or nulls when there is no usable secret or
 * the address does not parse. `uv` is the daily unique-visitor hash (only if
 * uniques are on); `cap` is a separate 32-bit hash under a different label,
 * used only as the per-client budget key (bots and humans on one address have
 * separate caps), so it cannot be matched to a visitor_seen row. The raw address is a local here and in normalizeAddress.
 */
async function hashesFor(
  request: Request,
  env: ViewEnv,
  now: number,
  bot: boolean,
): Promise<{ uv: string | null; cap: string | null }> {
  const secret = secretBytes(env.VIEW_HASH_SECRET);
  if (secret === null || typeof env.VIEW_HASH_SECRET !== "string") return { uv: null, cap: null };
  const addr = normalizeAddress(request.headers.get("CF-Connecting-IP")); // CONTROL:pv-no-raw
  if (addr === null) return { uv: null, cap: null };
  const key = await dayKey(secret, env.VIEW_HASH_SECRET, utcDate(now)); // CONTROL:pv-uv-key
  const cap = await visitorHash(key, `cap|${bot ? "bot" : "human"}|${addr}`, 32);
  const flag = env.UV_ENABLED;
  if (typeof flag === "string" && !flagOn(flag)) return { uv: null, cap };
  const bits = positiveInt(env.UV_BITS, DEFAULT_HASH_BITS, 64);
  return { uv: await visitorHash(key, addr, bits >= 16 ? bits : DEFAULT_HASH_BITS), cap };
}

// ----------------------------------------------------------------- derive

interface Derived {
  cls: "h" | "v" | "a" | "s";
  reason: string;
  browser: string;
  bmajor: number;
  os: string;
  device: string;
  lang: string;
}

/** Reduce the user agent and language header to enums. Raw text stays local. */
async function derive(request: Request, env: ViewEnv): Promise<Derived> {
  const ua = request.headers.get("User-Agent"); // CONTROL:pv-no-raw
  const acceptLanguage = request.headers.get("Accept-Language");
  const uaClass = classifyUserAgent(ua);
  let rate = false;
  if (env.VIEW_SUSPECT_LIMITER) {
    try {
      const key = request.headers.get("CF-Connecting-IP") ?? "unknown";
      rate = !(await env.VIEW_SUSPECT_LIMITER.limit({ key })).success;
    } catch {
      rate = false;
    }
  }
  const result = classifyBot({
    ...uaSignals(ua),
    acceptLanguagePresent: typeof acceptLanguage === "string" && acceptLanguage.length > 0,
    secFetchPresent: request.headers.has("Sec-Fetch-Site"),
    networkClass: "unknown",
    jsExecuted: true,
    clickWithoutView: false,
    rateFlag: rate,
  });
  const lang = primaryLanguage(acceptLanguage);
  return {
    cls: CLASS_LETTER[result.class],
    reason: oneOf(result.reason, BOT_REASONS, "none"),
    browser: oneOf(uaClass.browser, BROWSERS, "other"),
    bmajor: Math.min(Math.max(Math.trunc(uaClass.browserMajor ?? 0), 0), 999),
    os: oneOf(uaClass.os, OSES, "other"),
    device: oneOf(uaClass.device, DEVICES, "other"),
    lang: lang === "und" || !/^[a-z]{2,3}$/.test(lang) ? "" : lang,
  };
}

// ------------------------------------------------------------------- body

/** Read at most `max` bytes, counting as we go. Null when the cap is passed. */
async function readBounded(body: ReadableStream<Uint8Array> | null, max: number): Promise<string | null> {
  if (body === null) return "";
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > max) {
      await reader.cancel().catch(() => undefined);
      return null;
    }
    chunks.push(value);
  }
  const all = new Uint8Array(total);
  let at = 0;
  for (const c of chunks) {
    all.set(c, at);
    at += c.byteLength;
  }
  return new TextDecoder("utf-8", { fatal: false, ignoreBOM: false }).decode(all);
}

// ----------------------------------------------------------------- budget

/**
 * Spend one unit of this hour's write budget for `route`, atomically. The
 * single upsert only raises the counter while it is under the limit, so
 * `changes` is 1 when a unit was granted and 0 when the budget is spent.
 * This is the first write of an accepted view.
 */
async function reserve(env: ViewEnv, key: string, limit: number): Promise<boolean> {
  if (limit <= 0) return false;
  const res = await env.DB.prepare(
    "INSERT INTO view_meta (key, value) VALUES (?1, '1') " +
      "ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) " +
      "WHERE CAST(value AS INTEGER) < ?2",
  )
    .bind(key, limit)
    .run(); // CONTROL:pv-budget
  return res.meta.changes === 1;
}

// ---------------------------------------------------------------- handler

/**
 * Handle `POST /e/v/:pageId`. Check order, cheapest first, nothing writes
 * before the budget step:
 *   kill switch, method, URL/path/id caps, id allowlist, Sec-GPC / DNT,
 *   Origin (or Ping-From) with the 256 header cap, Content-Length, rate
 *   limit, bounded body read and parse, derive, classify, bots mode,
 *   budget, visitor_seen, view_events.
 * Always returns an empty 204.
 */
export async function handleView(
  request: Request,
  env: ViewEnv,
  now: number = Date.now(),
): Promise<Response> {
  let bodyTouched = false;
  try {
    if (flagOn(env.VIEWS_KILL)) return noContent(); // CONTROL:pv-kill

    if (request.method !== "POST") return noContent();
    if (request.url.length > MAX_URL_LENGTH) return noContent();
    const pathname = new URL(request.url).pathname;
    if (pathname.length > MAX_PATH_LENGTH) return noContent();
    const m = ROUTE.exec(pathname);
    if (!m) return noContent();
    const page = m[1] as string;
    if (page.length > MAX_ID_LENGTH || !idSet(env.PAGE_IDS).has(page)) return noContent();

    if (request.headers.get("Sec-GPC")?.trim() === "1") return noContent(); // CONTROL:pv-gpc
    if (request.headers.get("DNT")?.trim() === "1") return noContent(); // CONTROL:pv-gpc

    if (!env.SITE_HOST || !originMatches(request, env.SITE_HOST)) return noContent(); // CONTROL:pv-origin

    const declared = request.headers.get("Content-Length");
    if (declared === null || !/^\d{1,10}$/.test(declared) || Number(declared) > MAX_BODY) {
      return noContent(); // CONTROL:pv-cl
    }
    if (request.headers.get("Transfer-Encoding") !== null) return noContent();

    if (!env.VIEW_LIMITER) return noContent();
    const limiterKey = request.headers.get("CF-Connecting-IP") ?? "unknown";
    if (!(await env.VIEW_LIMITER.limit({ key: limiterKey })).success) return noContent(); // CONTROL:pv-limiter

    bodyTouched = true;
    const text = await readBounded(request.body, MAX_BODY);
    if (text === null) return noContent();
    const beacon = parseBeacon(text, env.SITE_HOST);
    if (beacon === null) return noContent();

    const level = oneOf(env.GEO_LEVEL ?? "", ["country", "region", "city"], "city") as GeoLevel;
    const geo = geoOf(request, level);
    const d = await derive(request, env);

    const bot = d.cls !== "h";
    if (env.BOTS_MODE === "drop" && (d.cls === "a" || d.cls === "s")) return noContent();

    const limit = bot
      ? positiveInt(env.BOT_VIEWS_PER_HOUR, DEFAULT_BOT_PER_HOUR, 100000)
      : positiveInt(env.VIEWS_PER_HOUR, DEFAULT_HUMAN_PER_HOUR, 100000);
    const hour = Math.floor(now / HOUR_MS);

    // Per-client cap first, so one client cannot spend the global budget.
    const { uv, cap } = await hashesFor(request, env, now, bot);
    if (cap !== null) {
      const perClient = positiveInt(env.CLIENT_VIEWS_PER_HOUR, DEFAULT_CLIENT_VIEWS_PER_HOUR, MAX_CLIENT_VIEWS_PER_HOUR);
      if (!(await reserve(env, `cap:${cap}:${hour}`, perClient))) return noContent(); // CONTROL:pv-client-cap
    }
    if (!(await reserve(env, `bud:${bot ? "pvbot" : "pv"}:${hour}`, limit))) return noContent();

    let first = 0;
    if (!bot) {
      const h = uv;
      if (h !== null) {
        const seen = await env.DB.prepare("INSERT OR IGNORE INTO visitor_seen (day, h) VALUES (?, ?)")
          .bind(utcDate(now), h)
          .run();
        first = seen.meta.changes === 1 ? 1 : 0;
      }
    }

    await env.DB.prepare(
      "INSERT INTO view_events (ts, page, src_kind, src, country, region, city, browser, bmajor, " +
        "os, device, lang, vp, net, bot_class, reason, first) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    )
      .bind(
        Math.floor(now / MINUTE_MS) * MINUTE_MS,
        page,
        beacon.kind,
        beacon.src,
        geo.country,
        geo.region,
        geo.city,
        d.browser,
        d.bmajor,
        d.os,
        d.device,
        d.lang,
        beacon.vp,
        "unknown", // network class: the dataset task fills this in later
        d.cls,
        d.reason,
        first,
      )
      .run();
  } catch {
    // A failure looks the same as success from the outside. Nothing is logged.
  } finally {
    if (!bodyTouched) await discard(request.body);
  }
  return noContent();
}
