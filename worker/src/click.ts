// SPDX-License-Identifier: Apache-2.0
import { normalizeAddress } from "./address";
import { limiterKeyOrIsolate } from "./ratekey";
import { clickCounterStatements } from "./clickfunnel";
import { killedSites, loadSites, siteForRequest } from "./sites";

/** Workers Rate Limiting binding (the subset we use). */
export interface RateLimiter {
  limit(options: { key: string }): Promise<{ success: boolean }>;
}

export interface Env {
  DB: D1Database;
  /**
   * The sites this collector serves (docs/analytics-design.md section 4). When
   * unset, SITE_HOST and LINK_IDS describe the one site `links`.
   */
  SITES?: string;
  /** Shorthand for one site `links`: its host, the only origin whose clicks are counted. */
  SITE_HOST?: string;
  /** Shorthand for one site `links`: JSON array of link ids from the build manifest. */
  LINK_IDS?: string;
  /** Shorthand for one site `links`: JSON array of tab ids (read by the view route). */
  PAGE_IDS?: string;
  /** Comma list of site ids whose click route is off, read per request. */
  CLICKS_KILL_SITES?: string;
  CLICK_LIMITER: RateLimiter;
  /**
   * Optional. When set, the limiter key is a stable keyed hash of the client
   * address under it; when absent, a keyed hash under a random per-isolate key.
   * The raw address never reaches the limiter, either way.
   */
  VIEW_HASH_SECRET?: string;
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
/** A source label as the page sends it: judged raw, before any lowercasing. */
const SRC_RAW = /^[A-Za-z0-9-]{1,30}$/;
const SRC = /^[a-z0-9-]{1,30}$/;
const HOUR_MS = 3_600_000;
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

/**
 * The closed-set source label of a click (issue #329). Read only for a site
 * that lists sources (an empty list is still a closed list: every valid label
 * is "other"). One `s` value, judged raw (the Kelvin sign and dotless i
 * lowercase into ASCII, so they are refused first); a label not on the site's
 * list is stored as the fixed "other", and its text is never kept. A missing,
 * repeated or malformed `s` is null (no source). Nothing else on the URL is read.
 */
export function clickSource(url: URL, sources: readonly string[] | undefined): string | null {
  if (sources === undefined) return null;
  const all = url.searchParams.getAll("s");
  if (all.length !== 1 || !SRC_RAW.test(all[0])) return null;
  const label = all[0].toLowerCase();
  if (!SRC.test(label)) return null;
  return sources.includes(label) ? label : "other";
}

function countryOf(request: Request): string | null {
  const c = request.headers.get("CF-IPCountry");
  return c !== null && COUNTRY.test(c) && c !== "XX" ? c : null;
}

/** Count one counter failure: a number only, never content. */
async function countFailure(db: D1Database): Promise<void> {
  try {
    await db
      .prepare(
        "INSERT INTO view_meta (key, value) VALUES ('click_counter_err', '1') " +
          "ON CONFLICT (key) DO UPDATE SET value = CAST(value AS INTEGER) + 1",
      )
      .run();
  } catch {
    // Nothing more can be done.
  }
}

/**
 * Handle `POST /e/c/:linkId`. Check order: kill switch, method, URL/path/id length caps, site (from
 * Origin / Ping-From), per-site kill switch, that site's id allowlist, body (never read),
 * user agent, rate limit, insert. Always returns an empty 204.
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

    // 2. The site is the one whose host matches Ping-From or Origin (never a
    // client claim; a noise filter only, threat A1), then the id must be on
    // THAT site's list (a Map of exact strings: no prototype lookups). An id
    // listed for another site records nothing.
    const site = siteForRequest(loadSites(env).sites, request); // CONTROL:click-site
    if (site === null) return noContent();
    if (killedSites(env.CLICKS_KILL_SITES).has(site.id)) return noContent(); // CONTROL:click-kill-site
    if (!site.linkIds.has(linkId)) return noContent();

    // 2b. Body: never read; ignored when it cannot be shown small.
    if (!(await bodyAcceptable(request))) return noContent();

    // 3. User agent: missing, empty or bot-like records nothing.
    const ua = request.headers.get("User-Agent");
    if (!ua || ua.length > MAX_UA_LENGTH || BOT_UA.test(ua)) return noContent();

    // 5. Rate limit per client. The key is a keyed hash, never the address,
    // and is never stored.
    const key = await limiterKeyOrIsolate(
      env.VIEW_HASH_SECRET,
      "clk",
      // Canonical form, so equivalent spellings share one allowance. An absent
      // or malformed address becomes the literal "unknown" (one shared bucket).
      normalizeAddress(request.headers.get("CF-Connecting-IP")) ?? "unknown",
    ); // CONTROL:click-limiter-key
    const { success } = await env.CLICK_LIMITER.limit({ key });
    if (!success) return noContent();

    // 6. Insert: hour-rounded timestamp, link id, optional country and optional
    // closed-set source label only.
    // A raw row carries at most a source label, never a country: country lives only
    // in the aggregate counter below, so the two stores cannot be differenced (#408).
    const now = Date.now();
    const src = clickSource(new URL(request.url), site.sources);
    const country = countryOf(request);
    await env.DB.prepare("INSERT INTO click_events (ts, link_id, country, site, src) VALUES (?, ?, ?, ?, ?)")
      .bind(now - (now % HOUR_MS), linkId, null, site.id, src) // country lives only in the aggregate counter
      .run();
    // The marginal funnel counters (#331): a count per (day, link, label), no row per click. They run
    // apart from the raw row, so a missing table or a failure here never drops the raw click; a failure
    // is only counted (a number in view_meta, no content).
    try {
      await env.DB.batch(
        clickCounterStatements(env.DB, {
          site: site.id,
          ts: now,
          linkId,
          src,
          listsSources: site.sources !== undefined,
          country,
        }),
      );
    } catch {
      await countFailure(env.DB);
    }
  } catch {
    // A failure must look the same as success from the outside.
  }
  return noContent();
}
