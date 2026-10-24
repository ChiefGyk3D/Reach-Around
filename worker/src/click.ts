// SPDX-License-Identifier: Apache-2.0

/** Workers Rate Limiting binding (the subset we use). */
export interface RateLimiter {
  limit(options: { key: string }): Promise<{ success: boolean }>;
}

export interface Env {
  DB: D1Database;
  /** Host of the static site; the only origin whose clicks are counted. */
  SITE_HOST: string;
  /** JSON array of link ids from the build manifest. */
  LINK_IDS: string;
  CLICK_LIMITER: RateLimiter;
  /**
   * Optional kill switch. "1" or "true" (any case) turns click recording off;
   * anything else, or unset, leaves it on. Read on every request.
   */
  CLICKS_KILL?: string;
}

/** Largest request body we will look at, in bytes. The route never needs one. */
export const MAX_BODY_BYTES = 1024;
/** Longest URL path we will decode or match, in characters. */
export const MAX_PATH_LENGTH = 128;
/** Longest link id, matching the slug limit in the build config. */
export const MAX_ID_LENGTH = 40;
/** Longest Origin or Ping-From header we will parse, in characters. */
export const MAX_ORIGIN_HEADER = 256;
/** Longest full request URL we will parse, in characters. */
export const MAX_URL_LENGTH = 2048;

const ROUTE = /^\/e\/c\/([^/]+)$/;
const MAX_UA_LENGTH = 512;
const COUNTRY = /^[A-Z]{2}$/;
/**
 * Known crawlers, link previewers, HTTP clients, headless browsers and
 * monitors, plus generic words (bot, crawler, spider, ...). Case-insensitive.
 *
 * This is a speed bump, not a guarantee. A user-agent string is whatever the
 * sender says it is, so this cannot stop automation that sends a browser user
 * agent. It only keeps honest tools out of the counts.
 */
const BOT_UA = new RegExp(
  [
    // Link-preview and social crawlers.
    "facebookexternalhit", "facebot", "twitterbot", "slackbot", "slack-imgproxy",
    "whatsapp", "telegrambot", "discordbot", "linkedinbot", "pinterest", "applebot",
    // Search and ad crawlers.
    "googlebot", "adsbot", "mediapartners", "bingbot", "yandexbot", "baiduspider",
    "duckduckbot", "ia_archiver",
    // HTTP clients and scripts.
    "curl", "wget", "python", "aiohttp", "httpx", "axios", "node-fetch", "undici",
    "\\bgot\\b", "go-http", "okhttp", "java/", "libwww", "httpclient",
    // Headless browsers and monitors.
    "headless", "phantomjs", "lighthouse", "pingdom", "uptimerobot", "statuscake",
    // Generic words.
    "bot", "crawl", "spider", "slurp", "preview", "monitor", "scan", "fetch", "check",
  ].join("|"),
  "i",
);

/**
 * Every outcome, accepted or ignored, is this exact response: an empty 204.
 * The caller learns nothing about which check, if any, rejected it.
 */
function noContent(): Response {
  return new Response(null, { status: 204 });
}

/** Kill switch: read per request, never cached at module load. */
function killed(env: Env): boolean {
  const v = env.CLICKS_KILL;
  if (typeof v !== "string") return false;
  const lower = v.toLowerCase();
  return lower === "1" || lower === "true";
}

/**
 * The click route never needs a body, so a body is never read. Returns false
 * (ignore the request) when a body is present and cannot be shown small from
 * headers alone: Content-Length missing, not plain digits, over the cap, or
 * Transfer-Encoding set. A body that passes is cancelled unread.
 */
async function bodyAcceptable(request: Request): Promise<boolean> {
  const body = request.body;
  const declared = request.headers.get("Content-Length");
  if (declared !== null && (!/^\d{1,10}$/.test(declared) || Number(declared) > MAX_BODY_BYTES)) {
    await discard(body);
    return false; // CONTROL:body-cap
  }
  if (body === null) return true;
  await discard(body);
  return declared !== null && request.headers.get("Transfer-Encoding") === null;
}

async function discard(body: ReadableStream<Uint8Array> | null): Promise<void> {
  try {
    await body?.cancel();
  } catch {
    // Nothing to do: the request is being ignored anyway.
  }
}

function allowedIds(raw: string): Set<string> {
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((v): v is string => typeof v === "string" && v !== ""));
  } catch {
    return new Set();
  }
}

function linkIdFromPath(pathname: string): string | null {
  const m = ROUTE.exec(pathname);
  if (!m) return null;
  try {
    const id = decodeURIComponent(m[1]);
    return id.length > 0 ? id : null;
  } catch {
    return null;
  }
}

function hostOf(value: string | null): string | null {
  if (!value) return null;
  try {
    return new URL(value).host.toLowerCase();
  } catch {
    return null;
  }
}

export function originMatches(request: Request, siteHost: string): boolean {
  // Length guard first: an oversize header is never handed to the URL parser.
  for (const name of ["Ping-From", "Origin"]) {
    const v = request.headers.get(name);
    if (v !== null && v.length > MAX_ORIGIN_HEADER) return false; // CONTROL:header-cap
  }
  const from = hostOf(request.headers.get("Ping-From"));
  const origin = hostOf(request.headers.get("Origin"));
  const want = siteHost.toLowerCase();
  return from === want || origin === want;
}

function countryOf(request: Request): string | null {
  const c = request.headers.get("CF-IPCountry");
  return c !== null && COUNTRY.test(c) && c !== "XX" ? c : null;
}

/**
 * Handle `POST /e/c/:linkId`. Check order: kill switch, method, URL/path/id length caps, id allowlist,
 * body (never read),
 * user agent, origin, rate limit, insert. Always returns an empty 204.
 * No IP address is stored; the client address is used only as a rate-limit
 * key and is never written anywhere.
 */
export async function handleClick(request: Request, env: Env): Promise<Response> {
  try {
    // 0. Cost guards, cheapest first, before anything is parsed or read.
    if (killed(env)) return noContent(); // CONTROL:kill-switch

    // 1. Method, then URL, path and id length caps, all before decoding.
    if (request.method !== "POST") return noContent();
    if (request.url.length > MAX_URL_LENGTH) return noContent();
    const pathname = new URL(request.url).pathname;
    if (pathname.length > MAX_PATH_LENGTH) return noContent();
    const linkId = linkIdFromPath(pathname);
    if (linkId === null || linkId.length > MAX_ID_LENGTH) return noContent();

    // 2. Id allowlist (a Set of exact strings: no prototype lookups).
    if (!allowedIds(env.LINK_IDS).has(linkId)) return noContent();

    // 2b. Body: never read; ignored when it cannot be shown small.
    if (!(await bodyAcceptable(request))) return noContent();

    // 3. User agent: missing, empty or bot-like records nothing.
    const ua = request.headers.get("User-Agent");
    if (!ua || ua.length > MAX_UA_LENGTH || BOT_UA.test(ua)) return noContent();

    // 4. Origin: Ping-From or Origin must be the configured site host.
    if (!env.SITE_HOST || !originMatches(request, env.SITE_HOST)) return noContent();

    // 5. Rate limit per client. The key is never stored.
    const key = request.headers.get("CF-Connecting-IP") ?? "unknown";
    const { success } = await env.CLICK_LIMITER.limit({ key });
    if (!success) return noContent();

    // 6. Insert: timestamp, link id and optional country only.
    await env.DB.prepare("INSERT INTO click_events (ts, link_id, country) VALUES (?, ?, ?)")
      .bind(Date.now(), linkId, countryOf(request))
      .run();
  } catch {
    // A failure must look the same as success from the outside.
  }
  return noContent();
}
