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
}

const ROUTE = /^\/e\/c\/([^/]+)$/;
const MAX_ID_LENGTH = 100;
const MAX_UA_LENGTH = 512;
const COUNTRY = /^[A-Z]{2}$/;
const BOT_UA =
  /bot|crawl|spider|slurp|curl|wget|python|httpclient|java\/|go-http|libwww|headless|lighthouse|preview|monitor|scan|fetch|check/i;

/**
 * Every outcome, accepted or ignored, is this exact response: an empty 204.
 * The caller learns nothing about which check, if any, rejected it.
 */
function noContent(): Response {
  return new Response(null, { status: 204 });
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
    return id.length > 0 && id.length <= MAX_ID_LENGTH ? id : null;
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

function originMatches(request: Request, siteHost: string): boolean {
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
 * Handle `POST /e/c/:linkId`. Check order: route and method, id allowlist,
 * user agent, origin, rate limit, insert. Always returns an empty 204.
 * No IP address is stored; the client address is used only as a rate-limit
 * key and is never written anywhere.
 */
export async function handleClick(request: Request, env: Env): Promise<Response> {
  try {
    // 1. Route and method.
    if (request.method !== "POST") return noContent();
    const linkId = linkIdFromPath(new URL(request.url).pathname);
    if (linkId === null) return noContent();

    // 2. Id allowlist (a Set of exact strings: no prototype lookups).
    if (!allowedIds(env.LINK_IDS).has(linkId)) return noContent();

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
