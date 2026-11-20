// SPDX-License-Identifier: Apache-2.0
//
// The media kit gate (issue #263, Plan 3 Task 8, slice 1).
//
//   POST /k/gate   form: email, cf-turnstile-response (and the hidden honeypot `website`)
//                  success: 303 /k/view plus the __Host- unlock cookie
//                  failure: 303 /kit/retry/, IDENTICAL for every cause (no oracle)
//   GET  /k/view   the gated page from KV (key kit/v1), only with a valid unlock cookie
//                  failure: 303 /kit/
//
// ORDER (cheap and free first, anything that writes last): kill switch, method,
// origin, content type, user agent, configuration, declared length, per-client
// limiter, hourly route budget, bounded body read and parse, Turnstile (and its
// single-use claim), lead upsert (lead cap), unlock event, token and cookie,
// owner alert. Nothing before the route budget writes to D1.
//
// Failures never name their cause: the response is the same fixed redirect with
// no body and no Set-Cookie, so a visitor learns nothing about which check
// refused. ONE ACCEPTED RESIDUAL: at the lead cap, a client that has already
// passed Turnstile can infer whether an address is a stored lead (a known
// address is refreshed and unlocked, a new one gets the usual refusal). Stored
// leads are not evicted to hide that; a test pins the behavior.
//
// Every other path under /k/ keeps the Worker's existing every-path-204 answer
// (index.ts); the plan's table said 404 for them. That is intentional.
//
// Nothing here logs request data. The only logging is the kit logger's closed
// vocabulary. The address and the token never leave this file except in the
// owner alert (the address, by contract of `kit_unlocked`) and the cookie.

import { notifyOwner, type AlertsEnv, type DispatchDeps } from "../alerts/dispatcher";
import { normalizeAddress } from "../address";
import { limiterKey, secretBytes } from "../ratekey";
import { loadKitLimits, recordKitEventCapped, reserveRoute } from "./budgets";
import { isKitKilled, loadKitEnv, type KitEnv } from "./env";
import { killedResponse, originAllowList } from "./guard";
import { MAX_FORM_BYTES, readForm } from "./input";
import { isBotUserAgent } from "../click";
import { getLead, upsertLead } from "./leads";
import { checkIpMinute, reserveBudget, utcDay, type MinuteLimiter } from "./limits";
import { forRequest, type Reason } from "./log";
import { readUnlockCookie, signToken, unlockCookie, verifyToken } from "./token";
import { verifyTurnstile } from "./turnstile";

export const GATE_ROUTE = "/k/gate";
export const VIEW_ROUTE = "/k/view";
/** Where every gate failure goes (the page that holds the gate form). */
export const GATE_FAIL_PAGE = "/kit/retry/";
/** Where a view without a valid unlock goes (the public kit page). */
export const VIEW_FAIL_PAGE = "/kit/";
/** The KV key that holds the gated page (Plan 3 storage contract; deploy uploads it). */
export const GATED_KV_KEY = "kit/v1";

const FORM_CONTENT_TYPE = "application/x-www-form-urlencoded";
const BODY_DEADLINE_MS = 5000;
const COUNTRY_RE = /^[A-Z]{2}$/;

export interface GateEnv
  extends AlertsEnv,
    Pick<KitEnv, "KIT" | "GATE_LIMITER" | "KIT_KILL" | "TOKEN_HMAC_KEY" | "TOKEN_HMAC_KEY_PREVIOUS" | "TURNSTILE_SECRET" | "TOKEN_TTL_SECONDS"> {
  /** Keys the limiter key hash, the same secret the click, view and inquiry routes use. */
  VIEW_HASH_SECRET?: string;
}

export interface GateDeps {
  fetchFn?: typeof fetch;
  nowMs?: number;
  /** The alert dispatcher; tests pass a fresh one so its dedup memory starts empty. */
  notify?: typeof notifyOwner;
  /** Execution context: the owner alert runs in `waitUntil`, so the visitor never waits for it. */
  ctx?: { waitUntil(p: Promise<unknown>): void };
}

const BASE_HEADERS = {
  "Cache-Control": "private, no-store",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
} as const;

function redirect(path: string, extra: Record<string, string> = {}): Response {
  return new Response(null, { status: 303, headers: { ...BASE_HEADERS, Location: path, ...extra } });
}

function empty(status: number, extra: Record<string, string> = {}): Response {
  return new Response(null, { status, headers: { ...BASE_HEADERS, ...extra } });
}

/** The one failure answer. Identical for every cause; nothing from the request is in it. */
function fail(reason: Reason): Response {
  forRequest().log("gate", "rejected", reason);
  return redirect(GATE_FAIL_PAGE);
}

/**
 * TURNSTILE: the ONLY place the gate touches Turnstile. It delegates to the
 * existing server-side check (turnstile.ts: siteverify POST of `secret` and
 * `response`, then success, empty error-codes, action and hostname must match).
 *
 * Vendor facts (developers.cloudflare.com server-side validation, read
 * 2026-11-19 by the coordinator's researcher): a token is valid 300 seconds and
 * single use, but redemption is eventually consistent, so a replay can briefly
 * succeed. Turnstile alone is therefore NOT replay protection: after a pass the
 * token's SHA-256 is claimed in the D1 counters table (one atomic conditional
 * upsert, limit 1), and a second use of the same token is refused here. The
 * claim is written only for tokens that passed, so a flood of junk tokens
 * writes nothing; rows are bounded by the gate route budget and are purged
 * after two days by the hourly cron (purgeOldCounters, src/index.ts). The claim
 * is looked up in today's and yesterday's row, so a replay that straddles UTC
 * midnight is still refused (a token lives 300 seconds, far less than a day).
 * Every failure (timeout, internal-error, any error code) is one `false`; the
 * codes are never shown to the visitor.
 *
 * Not done here on purpose: `remoteip` is not sent (the existing check keeps
 * the address inside the Worker), and there is no retry (fail closed).
 * ASSUMED, not verifiable from the vendor pages alone: the gate widget is
 * rendered with data-action="gate" and its hostname is SITE_HOST.
 */
export async function challengePassed(
  env: Pick<GateEnv, "TURNSTILE_SECRET" | "SITE_HOST" | "DB">,
  token: string,
  fetchFn: typeof fetch,
  nowMs: number,
): Promise<boolean> {
  if (!(await verifyTurnstile(env, token, "gate", fetchFn))) return false; // CONTROL:turnstile
  try {
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token)));
    let hex = "";
    for (const b of digest.slice(0, 20)) hex += b.toString(16).padStart(2, "0");
    const claim = `ts-${hex}`;
    const earlier = await env.DB.prepare("SELECT 1 AS n FROM counters WHERE key = ?1 AND day = ?2")
      .bind(claim, utcDay(Math.max(0, nowMs - 86_400_000)))
      .first();
    if (earlier !== null) return false; // CONTROL:turnstile-single-use-previous-day
    return await reserveBudget(env.DB, claim, utcDay(nowMs), 1); // CONTROL:turnstile-single-use
  } catch {
    return false;
  }
}

function hasLimiter(l: unknown): l is MinuteLimiter {
  return typeof l === "object" && l !== null && typeof (l as { limit?: unknown }).limit === "function";
}

/** Two uppercase letters from the edge, else null. "XX" (unknown) and "T1" (Tor) are not countries. */
function countryOf(request: Request): string | null {
  const c = request.headers.get("cf-ipcountry");
  return c !== null && COUNTRY_RE.test(c) && c !== "XX" ? c : null;
}

async function gate(request: Request, env: GateEnv, deps: GateDeps): Promise<Response> {
  const log = forRequest();
  const now = deps.nowMs ?? Date.now();
  const fetchFn = deps.fetchFn ?? fetch;

  if (request.headers.get("content-type") !== FORM_CONTENT_TYPE) return fail("other"); // CONTROL:gate-content-type
  // Missing, oversize or tool-like user agents end here, before anything is spent (shared with the click route).
  if (isBotUserAgent(request.headers.get("user-agent"))) return fail("bot_ua"); // CONTROL:gate-bot-ua

  // Configuration: any missing or malformed binding closes the route (same answer, nothing spent).
  let cfg: ReturnType<typeof loadKitEnv>;
  try {
    cfg = loadKitEnv(env as unknown as KitEnv);
  } catch {
    log.log("gate", "closed", "binding");
    return redirect(GATE_FAIL_PAGE);
  }
  const limiterSecret = secretBytes(env.VIEW_HASH_SECRET);
  if (limiterSecret === null || typeof env.VIEW_HASH_SECRET !== "string" || !hasLimiter(env.GATE_LIMITER)) {
    log.log("gate", "closed", "binding");
    return redirect(GATE_FAIL_PAGE);
  }
  const secretText = env.VIEW_HASH_SECRET;

  // Declared length, header only: a body that says it is too big is never read.
  const declared = request.headers.get("content-length");
  if (declared !== null && (!/^[0-9]{1,9}$/.test(declared) || Number(declared) > MAX_FORM_BYTES)) return fail("size"); // CONTROL:gate-declared-length

  // Per-client minute limiter, keyed by a keyed hash (the raw address never reaches the binding).
  const address = normalizeAddress(request.headers.get("cf-connecting-ip"));
  const key = address === null ? null : await limiterKey(limiterSecret, secretText, "gate", address);
  if (!(await checkIpMinute(env.GATE_LIMITER, key))) return fail("ip_minute"); // CONTROL:gate-ip-minute

  // Hourly route budget: the first D1 write, bounded by the budget itself.
  if (!(await reserveRoute({ ...env, DB: env.DB }, "gate", now))) return fail("route_budget"); // CONTROL:gate-route-budget

  // Bounded body read and parse. A repeated or unknown field, a filled honeypot, a bad
  // email or a malformed token all end the same way as any other failure.
  const form = await readForm(request, { deadlineMs: BODY_DEADLINE_MS }); // CONTROL:gate-body-cap
  if (form === null) return fail("size");
  if (form.email === null) return fail("email");
  if (form.turnstile === null) return fail("turnstile");

  if (!(await challengePassed(env, form.turnstile, fetchFn, now))) return fail("turnstile");

  // REVOCATION DESIGN (plan 3, Task 15 and decision D2): `leads.session_floor_ts` ends the cookies
  // that were issued before it; it is not a block list, and the schema has no blocked flag. The kit
  // is open to any visitor who passes Turnstile, so a lead that passes the gate again is issued a new
  // cookie, whose iat is not below the floor. Deleting the lead is the way to remove a lead entirely.
  // Lead storage (30 day retention is the scheduled purge, leadretention.ts). The visitor's
  // fields never choose the source, the verified flag or the country.
  let lead: { id: string; created: boolean } | null;
  let limits: ReturnType<typeof loadKitLimits>;
  try {
    limits = loadKitLimits(env);
    lead = await upsertLead(env.DB, form.email, "gate", countryOf(request), now, limits.maxLeads);
  } catch {
    return fail("other");
  }
  if (lead === null) return fail("lead_cap");

  try {
    await recordKitEventCapped(env.DB, lead.id, "unlock", now, limits.maxKitEvents);
  } catch {
    // The unlock event is a convenience for the owner; a full or failing table never blocks the visitor.
  }

  const nowSec = Math.floor(now / 1000);
  let cookie: string;
  try {
    const token = await signToken(cfg.keys[0]!, { lid: lead.id, iat: nowSec, exp: nowSec + cfg.tokenTtlSeconds, cc: countryOf(request) ?? "" });
    cookie = unlockCookie(token, cfg.tokenTtlSeconds);
  } catch {
    return fail("other");
  }

  // Owner alert: fire and forget. A failing channel never fails the unlock.
  if (deps.ctx !== undefined) {
    const send = deps.notify ?? notifyOwner;
    const dispatch: DispatchDeps = { nowMs: now };
    if (deps.fetchFn !== undefined) dispatch.fetchFn = deps.fetchFn;
    try {
      deps.ctx.waitUntil(send(env, { kind: "kit_unlocked", atMs: now, email: form.email }, dispatch).catch(() => undefined));
    } catch {
      // Never fails the request.
    }
  }

  log.log("gate", "ok");
  return redirect(VIEW_ROUTE, { "Set-Cookie": cookie });
}

async function view(request: Request, env: GateEnv, deps: GateDeps): Promise<Response> {
  const log = forRequest();
  const now = deps.nowMs ?? Date.now();
  const back = (reason: Reason): Response => {
    log.log("link", "rejected", reason);
    return redirect(VIEW_FAIL_PAGE);
  };

  let cfg: ReturnType<typeof loadKitEnv>;
  try {
    cfg = loadKitEnv(env as unknown as KitEnv);
  } catch {
    log.log("link", "closed", "binding");
    return redirect(VIEW_FAIL_PAGE);
  }

  // The cookie is the only way in: not the query string, not a header. Free to check, so first.
  const raw = readUnlockCookie(request.headers.get("cookie"));
  const claims = raw === null ? null : await verifyToken(cfg.keys, raw, Math.floor(now / 1000)); // CONTROL:view-token
  if (claims === null) return back("expired");

  // Per-client minute limiter, the same keyed hash as the gate but its own purpose label, so a
  // client's gate and view allowances are separate and one client cannot spend another's.
  const limiterSecret = secretBytes(env.VIEW_HASH_SECRET);
  if (limiterSecret === null || typeof env.VIEW_HASH_SECRET !== "string" || !hasLimiter(env.GATE_LIMITER)) {
    log.log("link", "closed", "binding");
    return redirect(VIEW_FAIL_PAGE);
  }
  const address = normalizeAddress(request.headers.get("cf-connecting-ip"));
  const key = address === null ? null : await limiterKey(limiterSecret, env.VIEW_HASH_SECRET, "view", address);
  if (!(await checkIpMinute(env.GATE_LIMITER, key))) return back("ip_minute"); // CONTROL:view-ip-minute

  if (!(await reserveRoute({ ...env, DB: env.DB }, "view", now))) return back("route_budget"); // CONTROL:view-route-budget

  // The lead must still exist (a deleted lead's cookie is dead) and the token must not predate a revoke.
  let lead: Awaited<ReturnType<typeof getLead>>;
  try {
    lead = await getLead(env.DB, claims.lid); // CONTROL:lead-exists
  } catch {
    return back("other");
  }
  if (lead === null) return back("expired");
  // The floor is in MILLISECONDS (whoever writes `session_floor_ts` must write milliseconds). The
  // token's iat is whole seconds, so the two are compared in whole seconds and the check fails closed:
  // only a cookie issued in a second strictly after the floor's second works. Trade-off: a fresh
  // unlock issued in the very same second as the floor is refused once and works on the next try.
  if (lead.session_floor_ts !== null && claims.iat <= Math.floor(lead.session_floor_ts / 1000)) return back("expired"); // CONTROL:session-floor

  let page: string | null;
  try {
    page = await env.KIT.get(GATED_KV_KEY);
  } catch {
    return empty(404);
  }
  if (page === null) return empty(404);

  if (request.method.toUpperCase() === "HEAD") {
    return new Response(null, { status: 200, headers: viewHeaders() });
  }
  return new Response(page, { status: 200, headers: viewHeaders() });
}

function viewHeaders(): Record<string, string> {
  return {
    ...BASE_HEADERS,
    "Content-Type": "text/html; charset=utf-8",
    "X-Robots-Tag": "noindex",
    // ASSUMED: the gated page is one HTML file with its own inline style and
    // same-origin images and fonts, and posts only to this origin (the inquiry
    // form). kit.py refuses a gated page that requests another host.
    "Content-Security-Policy":
      "default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
  };
}

/** True for the two paths this module owns. */
export function isGateRoute(pathname: string): boolean {
  return pathname === GATE_ROUTE || pathname === VIEW_ROUTE;
}

export async function handleGate(request: Request, env: GateEnv, deps: GateDeps = {}): Promise<Response> {
  if (isKitKilled(env)) return killedResponse(); // CONTROL:kill-switch
  let path: string;
  try {
    path = new URL(request.url).pathname;
  } catch {
    return empty(404);
  }
  const method = request.method.toUpperCase();
  if (path === VIEW_ROUTE) return method === "GET" || method === "HEAD" ? view(request, env, deps) : empty(404);
  if (path !== GATE_ROUTE || method !== "POST") return empty(404);

  let origins: string[] = [];
  if (typeof env.SITE_HOST === "string" && env.SITE_HOST !== "") origins = [`https://${env.SITE_HOST}`];
  let ok = false;
  try {
    ok = originAllowList(origins)(request); // CONTROL:gate-origin
  } catch {
    ok = false;
  }
  if (!ok) return fail("origin");
  return gate(request, env, deps);
}
