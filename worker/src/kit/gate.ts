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
// limiter, bounded body read and parse, the global check ceiling (a binding), Turnstile
// and its single-use claim, the small business route budget, lead storage, unlock
// event, token and cookie, owner alert. Nothing before a passed Turnstile check
// writes to D1.
//
// Failures never name their cause: the response is the same fixed redirect with
// no body and no Set-Cookie, so a visitor learns nothing about which check
// refused. Lead storage does not change the answer either: at the lead cap (or
// the per-day new-lead cap) a new address is unlocked with the same response as a
// known one, only not stored (see the REVOCATION DESIGN and LEAD STORAGE notes below).
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
import { getLead, getLeadByEmail, getUnstoredSessionFloor, upsertLead } from "./leads";
import { isUnstoredId, mintUnstoredId } from "./unstored";
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
/** How far the vendor's challenge time may sit from this Worker's clock. */
const CHALLENGE_SKEW_MS = 10 * 60_000;
/** The key every request shares in GATE_CHECK_LIMITER (one global allowance). */
const CHECK_KEY = "gate-check";
/** Counter keys (table `counters`, one row a day each): new leads stored, and the once-a-day cap alert. */
const NEW_LEAD_KEY = "newlead";
const CAP_ALERT_KEY = "leadcap-alert";
/**
 * New leads stored per UTC day. Past it (or past MAX_LEADS) a new address is unlocked but not stored,
 * exactly like a known one from the visitor's side. A code constant, not a setting.
 */
export const NEW_LEADS_PER_DAY = 100;

export interface GateEnv
  extends AlertsEnv,
    Pick<KitEnv, "KIT" | "GATE_LIMITER" | "KIT_KILL" | "TOKEN_HMAC_KEY" | "TOKEN_HMAC_KEY_PREVIOUS" | "TURNSTILE_SECRET" | "TOKEN_TTL_SECONDS"> {
  /** Keys the limiter key hash, the same secret the click, view and inquiry routes use. */
  VIEW_HASH_SECRET?: string;
  /**
   * Workers Rate Limiting binding: ONE global allowance for requests that are about to reach Turnstile
   * (so it caps vendor calls and the claim writes). Larger than the per-client limiter; no D1 write.
   * Required; without it the gate refuses.
   */
  GATE_CHECK_LIMITER?: MinuteLimiter;
}

export interface GateDeps {
  /** Test seam: new leads stored per UTC day (default NEW_LEADS_PER_DAY). */
  newLeadsPerDay?: number;
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
 * upsert, limit 1), and a second use of the same token is refused here.
 *
 * The claim lives in ONE bucket for the token's whole life: the UTC day of the
 * vendor's `challenge_ts`, which is the same on every answer for one token. A
 * replay therefore lands on the same row whatever the clock says, midnight
 * included, and two concurrent requests race on one primary key. A pass without
 * a parsable `challenge_ts`, or one more than CHALLENGE_SKEW_MS from this
 * Worker's clock, is refused (fail closed). ASSUMED: `challenge_ts` is an ISO 8601
 * text, as in the vendor's response shape; if it were not, the gate would refuse
 * everyone, which a staging run would show at once.
 *
 * The claim is written only for tokens that passed, so junk tokens write
 * nothing; rows are purged after two days by the hourly cron (purgeOldCounters,
 * src/index.ts). Every failure (timeout, internal-error, any error code) is one
 * `false`; the codes are never shown to the visitor.
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
  let challengeMs: number | null = null;
  const passed = await verifyTurnstile(env, token, "gate", fetchFn, {
    onVerified: (r) => {
      challengeMs = r.challengeMs;
    },
  }); // CONTROL:turnstile
  if (!passed || challengeMs === null) return false;
  const at: number = challengeMs;
  if (Math.abs(nowMs - at) > CHALLENGE_SKEW_MS) return false; // CONTROL:turnstile-fresh
  try {
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token)));
    let hex = "";
    for (const b of digest.slice(0, 20)) hex += b.toString(16).padStart(2, "0");
    return await reserveBudget(env.DB, `ts-${hex}`, utcDay(at), 1); // CONTROL:turnstile-single-use
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

/** True for the first unstored unlock of a UTC day: one health alert a day, never one per visitor. */
async function capAlertOnce(env: GateEnv, nowMs: number): Promise<boolean> {
  try {
    return await reserveBudget(env.DB, CAP_ALERT_KEY, utcDay(nowMs), 1);
  } catch {
    return false;
  }
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
  if (
    limiterSecret === null ||
    typeof env.VIEW_HASH_SECRET !== "string" ||
    !hasLimiter(env.GATE_LIMITER) ||
    !hasLimiter(env.GATE_CHECK_LIMITER)
  ) {
    log.log("gate", "closed", "binding");
    return redirect(GATE_FAIL_PAGE);
  }
  const secretText = env.VIEW_HASH_SECRET;
  const checkLimiter = env.GATE_CHECK_LIMITER;

  // Declared length, header only: a body that says it is too big is never read.
  const declared = request.headers.get("content-length");
  if (declared !== null && (!/^[0-9]{1,9}$/.test(declared) || Number(declared) > MAX_FORM_BYTES)) return fail("size"); // CONTROL:gate-declared-length

  // Per-client minute limiter, keyed by a keyed hash (the raw address never reaches the binding).
  const address = normalizeAddress(request.headers.get("cf-connecting-ip"));
  const key = address === null ? null : await limiterKey(limiterSecret, secretText, "gate", address);
  if (!(await checkIpMinute(env.GATE_LIMITER, key))) return fail("ip_minute"); // CONTROL:gate-ip-minute

  // Bounded body read and parse. A repeated or unknown field, a filled honeypot, a bad
  // email or a malformed token all end the same way as any other failure, and none of
  // them has cost anything beyond the per-client limiter above.
  const form = await readForm(request, { deadlineMs: BODY_DEADLINE_MS }); // CONTROL:gate-body-cap
  if (form === null) return fail("size");
  if (form.email === null) return fail("email");
  if (form.turnstile === null) return fail("turnstile");

  // TWO BUDGETS. This one is a larger, global ceiling on requests that are about to call Turnstile
  // (a Workers Rate Limiting binding, no D1 write): it caps vendor calls and claim writes. The small
  // business budget (ROUTE_BUDGET_GATE) is charged only after the body parsed AND Turnstile passed, so
  // junk posts cannot spend it. An edge rate rule (issue #613) is the other layer in front of both.
  if (!(await checkIpMinute(checkLimiter, CHECK_KEY))) return fail("route_budget"); // CONTROL:gate-check-ceiling

  if (!(await challengePassed(env, form.turnstile, fetchFn, now))) return fail("turnstile");

  if (!(await reserveRoute({ ...env, DB: env.DB }, "gate", now))) return fail("route_budget"); // CONTROL:gate-route-budget

  // REVOCATION DESIGN (plan 3, Task 15 and decision D2): `leads.session_floor_ts` ends the cookies
  // that were issued before it; it is not a block list, and the schema has no blocked flag. The kit
  // is open to any visitor who passes Turnstile, so a lead that passes the gate again is issued a new
  // cookie, whose iat is not below the floor. Deleting the lead is the way to remove a lead entirely.
  //
  // LEAD STORAGE (30 day retention is the scheduled purge, leadretention.ts). The visitor's fields
  // never choose the source, the verified flag or the country. A known address is refreshed. A new one
  // is stored only while MAX_LEADS and the per-day new-lead cap have room; otherwise the visitor is
  // unlocked all the same, with an id that marks an unstored unlock (unstored.ts), so the answer is
  // identical for known and unknown addresses at the cap. What the owner loses then: those addresses
  // are not stored (the unlock alert still reaches the alert sink) until old leads age out.
  let leadId: string;
  let stored = true;
  let limits: ReturnType<typeof loadKitLimits>;
  try {
    limits = loadKitLimits(env);
    const known = await getLeadByEmail(env.DB, form.email);
    let lead: { id: string } | null = null;
    if (known !== null) {
      lead = await upsertLead(env.DB, form.email, "gate", countryOf(request), now, limits.maxLeads);
    } else if (await reserveBudget(env.DB, NEW_LEAD_KEY, utcDay(now), deps.newLeadsPerDay ?? NEW_LEADS_PER_DAY)) {
      lead = await upsertLead(env.DB, form.email, "gate", countryOf(request), now, limits.maxLeads);
    }
    if (lead === null) {
      stored = false;
      leadId = await mintUnstoredId(cfg.keys[0]!);
    } else {
      leadId = lead.id;
    }
  } catch {
    return fail("other");
  }

  if (stored) {
    try {
      await recordKitEventCapped(env.DB, leadId, "unlock", now, limits.maxKitEvents);
    } catch {
      // The unlock event is a convenience for the owner; a full or failing table never blocks the visitor.
    }
  }

  const nowSec = Math.floor(now / 1000);
  let cookie: string;
  try {
    const token = await signToken(cfg.keys[0]!, { lid: leadId, iat: nowSec, exp: nowSec + cfg.tokenTtlSeconds, cc: countryOf(request) ?? "" });
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
      if (!stored && (await capAlertOnce(env, now))) {
        // Closed health word, once per UTC day: the owner learns that new addresses are not being stored.
        deps.ctx.waitUntil(
          send(env, { kind: "analytics_health", atMs: now, condition: "lead_cap_reached", state: "firing", count: 0 }, dispatch).catch(() => undefined),
        );
      }
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

  // The floor is in MILLISECONDS (whoever writes it must write milliseconds). The token's iat is whole
  // seconds, so the two are compared in whole seconds and the check fails closed: only a cookie issued
  // in a second strictly after the floor's second works. Trade-off: a fresh unlock issued in the very
  // same second as the floor is refused once and works on the next try.
  let floorMs: number | null;
  try {
    if (await isUnstoredId(cfg.keys, claims.lid)) {
      // An unlock that stored no lead has no lead row: the one kit-wide floor in kit_meta applies. A read
      // error refuses.
      floorMs = await getUnstoredSessionFloor(env.DB); // CONTROL:unstored-floor
    } else {
      // The lead must still exist (a deleted lead's cookie is dead).
      const lead = await getLead(env.DB, claims.lid); // CONTROL:lead-exists
      if (lead === null) return back("expired");
      floorMs = lead.session_floor_ts;
    }
  } catch {
    return back("other");
  }
  if (floorMs !== null && claims.iat <= Math.floor(floorMs / 1000)) return back("expired"); // CONTROL:session-floor

  // A missing or failing page gets the SAME answer as every other refusal, so an outage cannot tell a
  // visitor whether a cookie was valid. The cause goes to the closed-vocabulary log only.
  let page: string | null;
  try {
    page = await env.KIT.get(GATED_KV_KEY);
  } catch {
    return back("provider");
  }
  if (page === null) return back("other");

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
