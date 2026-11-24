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
// limiter, bounded body read and parse, the per-coarse-network check allowance (a binding),
// Turnstile and its single-use claim, the small business route budget, lead storage, unlock
// event, token and cookie, owner alert. Nothing before a passed Turnstile check
// writes to D1.
//
// Failures never name their cause: the response is the same fixed redirect with
// no body and no Set-Cookie, so a visitor learns nothing about which check
// refused. Lead storage does not change the answer either: at the lead cap (or
// the per-day new-lead cap) a new address is unlocked with the same response as a
// known one, only not stored (see the REVOCATION DESIGN and LEAD STORAGE notes below).
//
// Exceptions to the one refusal answer: the kill switch gives the fixed 503 page; GET /k/gate and any
// method but GET or HEAD on /k/view give an empty 404; a bad configuration closes the route with the
// usual redirect. Every other path under /k/ never reaches this file: the Worker's existing
// every-path-204 answer applies (index.ts), where the plan's table said 404. That is intentional.
//
// KNOWN GAPS, stated plainly: nothing in this change writes the revocation floors yet
// (setSessionFloor, setUnstoredSessionFloor are written by the later revoke task), so a leaked cookie
// lives for its TTL (24 hours by default). Counter rows (single-use claims) are written before the
// business budget is charged; the hourly purge deletes at most 500 an hour; growth is bounded by the
// per-network allowance in front of it. An attacker who passes Turnstile can spend the new-lead cap
// with plus-tag addresses: accepted, covered by the `lead_cap_reached` alert, and the unlock alert
// still carries each address.
//
// Nothing here logs request data. The only logging is the kit logger's closed
// vocabulary. The address and the token never leave this file except in the
// owner alert (the address, by contract of `kit_unlocked`) and the cookie.

import { notifyOwner, type AlertsEnv, type DispatchDeps } from "../alerts/dispatcher";
import { normalizeAddress } from "../address";
import { limiterKey, secretBytes } from "../ratekey";
import { claimGateUnit, loadKitLimits, recordKitEventCapped, reserveRoute, type KitLimitsEnv } from "./budgets";
import { isKitKilled, loadKitEnv, type KitEnv } from "./env";
import { killedResponse, originAllowList } from "./guard";
import { assertLeadsSeparated } from "./separation";
import { MAX_FORM_BYTES, readForm } from "./input";
import { isBotUserAgent } from "../click";
import { getLead, getLeadByEmail, getUnstoredSessionFloor, upsertLead } from "./leads";
import { coarseNetwork } from "./network";
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
/** The one key every request shares in GATE_WATCH_LIMITER (a per-location monitoring hint, never a refusal). */
const WATCH_KEY = "gate-watch";
/** Counter keys (table `counters`, one row a day each): new leads stored, and the once-a-day alert claims. */
const NEW_LEAD_KEY = "newlead";
const CAP_ALERT_KEY = "leadcap-alert";
const BUSY_ALERT_KEY = "gatebusy-alert";
/**
 * New leads stored per UTC day. Past it (or past MAX_LEADS) a new address is unlocked but not stored,
 * exactly like a known one from the visitor's side. A code constant, not a setting.
 */
export const NEW_LEADS_PER_DAY = 100;

export interface GateEnv
  extends AlertsEnv,
    Pick<KitEnv, "KIT" | "LEADS_DB" | "GATE_LIMITER" | "KIT_KILL" | "TOKEN_HMAC_KEY" | "TOKEN_HMAC_KEY_PREVIOUS" | "TURNSTILE_SECRET" | "TOKEN_TTL_SECONDS"> {
  /** Keys the limiter key hash, the same secret the click, view and inquiry routes use. */
  VIEW_HASH_SECRET?: string;
  /**
   * Workers Rate Limiting binding for requests about to reach Turnstile, PARTITIONED by a keyed hash of
   * the coarse network (a /24 or /48, label "gnet"), so one network can use up only its own share and
   * cannot starve another. It caps vendor calls and claim writes; no D1 write. Required; without it the
   * gate refuses.
   */
  GATE_CHECK_LIMITER?: MinuteLimiter;
  /**
   * Workers Rate Limiting binding for GET /k/view, per client (keyed hash, label "view"). Its own
   * binding, with a higher allowance than the gate's (4 per 60 s documented), so a visitor can reload.
   * Keep limit x 60 below ROUTE_BUDGET_VIEW so one client cannot spend an hour's budget alone.
   * (Not named VIEW_LIMITER: that is the page-view beacon route's binding.) Required for /k/view.
   */
  KIT_VIEW_LIMITER?: MinuteLimiter;
  /**
   * Optional Workers Rate Limiting binding, ONE shared key, set far above expected peak. It is a
   * PER-LOCATION busy signal: Cloudflare counts a rate limit separately in each of its locations, so it
   * is not an account-wide total. Traffic spread across locations can exceed the number without raising
   * it, and one regional spike can raise it. It is a MONITORING hint only: when it reports over its
   * limit the request still proceeds, and the owner gets one closed health alert a day (`gate_busy`).
   * It is never the protection and never refuses anyone.
   */
  GATE_WATCH_LIMITER?: MinuteLimiter;
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
 * nothing, and only together with the hourly gate unit (claimGateUnit, one atomic
 * batch: the claim insert is conditional on the unit having been taken), so claim
 * writes cannot exceed ROUTE_BUDGET_GATE. A replayed token is found by a read and
 * spends no unit. Rows are purged after two days by the hourly cron
 * (purgeOldCounters, src/index.ts). Every failure (timeout, internal-error, any
 * error code) is one "failed"; the codes are never shown to the visitor.
 *
 * Not done here on purpose: `remoteip` is not sent (the existing check keeps
 * the address inside the Worker), and there is no retry (fail closed).
 * ASSUMED, not verifiable from the vendor pages alone: the gate widget is
 * rendered with data-action="gate" and its hostname is SITE_HOST.
 */
export async function challengePassed(
  env: Pick<GateEnv, "TURNSTILE_SECRET" | "SITE_HOST" | "DB"> & KitLimitsEnv,
  token: string,
  fetchFn: typeof fetch,
  nowMs: number,
): Promise<"ok" | "failed" | "budget"> {
  let challengeMs: number | null = null;
  const passed = await verifyTurnstile(env, token, "gate", fetchFn, {
    onVerified: (r) => {
      challengeMs = r.challengeMs;
    },
  }); // CONTROL:turnstile
  if (!passed || challengeMs === null) return "failed";
  const at: number = challengeMs;
  if (Math.abs(nowMs - at) > CHALLENGE_SKEW_MS) return "failed"; // CONTROL:turnstile-fresh
  try {
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token)));
    let hex = "";
    for (const b of digest.slice(0, 20)) hex += b.toString(16).padStart(2, "0");
    // ONE atomic step: the single-use claim row is written only if the hourly gate unit was taken.
    const claim = await claimGateUnit(env, `ts-${hex}`, utcDay(at), nowMs);
    return claim === "ok" ? "ok" : claim === "budget" ? "budget" : "failed";
  } catch {
    return "failed";
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

/**
 * One closed health alert per UTC day for `key`. The day's claim is taken first (so concurrent requests
 * send one), the alert is sent after the response, and when no channel accepted it the claim is released,
 * so a later request retries. Never throws.
 */
async function healthAlertOnce(
  env: GateEnv,
  deps: GateDeps,
  key: string,
  condition: "lead_cap_reached" | "gate_busy",
  nowMs: number,
  dispatch: DispatchDeps,
): Promise<void> {
  if (deps.ctx === undefined) return;
  const day = utcDay(nowMs);
  try {
    if (!(await reserveBudget(env.DB, key, day, 1))) return;
  } catch {
    return;
  }
  const send = deps.notify ?? notifyOwner;
  const release = async (): Promise<void> => {
    try {
      await env.DB.prepare("DELETE FROM counters WHERE key = ?1 AND day = ?2").bind(key, day).run();
    } catch {
      // The claim then holds for the rest of the day: one missed alert, never a repeat.
    }
  };
  deps.ctx.waitUntil(
    (async () => {
      try {
        const receipt = await send(env, { kind: "analytics_health", atMs: nowMs, condition, state: "firing", count: 0 }, dispatch);
        if (!receipt.some((r) => r.ok)) await release();
      } catch {
        await release();
      }
    })(),
  );
}

/** Give back one daily new-lead unit (never below zero). */
async function refundNewLead(db: D1Database, day: string): Promise<void> {
  try {
    await db.prepare("UPDATE counters SET count = count - 1 WHERE key = ?1 AND day = ?2 AND count > 0").bind(NEW_LEAD_KEY, day).run();
  } catch {
    // A unit that could not be returned only makes the day's cap slightly stricter.
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
    // The shape check above is cheap; this proves the two databases are different by what each states
    // it is, and that analytics holds no lead rows (separation.ts). Before anything is spent.
    await assertLeadsSeparated(env);
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

  // TWO BUDGETS. This one is an allowance PER COARSE NETWORK (a /24 or /48, keyed hash with its own
  // label) on requests about to call Turnstile (a Workers Rate Limiting binding, no D1 write): it caps
  // vendor calls and claim writes without letting one network use up another's share. The small business
  // budget (ROUTE_BUDGET_GATE) is charged only after the body parsed AND Turnstile passed, so junk posts
  // cannot spend it. An edge rate rule (issue #613) is another layer in front, not needed for correctness.
  const network = coarseNetwork(address);
  const netKey = network === null ? null : await limiterKey(limiterSecret, secretText, "gnet", network);
  if (!(await checkIpMinute(checkLimiter, netKey))) return fail("route_budget"); // CONTROL:gate-check-ceiling

  // Per-location busy signal, a monitoring hint only (not an account-wide count): over its (very high)
  // limit nothing is refused; the owner is told.
  let busy = false;
  if (hasLimiter(env.GATE_WATCH_LIMITER)) {
    try {
      busy = (await env.GATE_WATCH_LIMITER.limit({ key: WATCH_KEY }))?.success === false;
    } catch {
      busy = false;
    }
  }

  const verdict = await challengePassed({ ...env, DB: env.DB }, form.turnstile, fetchFn, now);
  if (verdict !== "ok") return fail(verdict === "budget" ? "route_budget" : "turnstile"); // CONTROL:gate-route-budget

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
    // Leads and kit events are in their own database (issue #772). loadKitEnv has already refused a
    // missing LEADS_DB or one that is the analytics database; the counters stay in DB.
    const known = await getLeadByEmail(env.LEADS_DB, form.email);
    let lead: { id: string } | null = null;
    if (known !== null) {
      lead = await upsertLead(env.LEADS_DB, form.email, "gate", countryOf(request), now, limits.maxLeads);
    } else {
      // Admission and creation: the daily unit is taken atomically (one conditional upsert), the row is
      // created atomically (one statement that says whether it created it), and the unit is given back
      // only after an UNAMBIGUOUS result that shows no row was created (the cap refused, or a concurrent
      // request for the same address created it first). If the call throws, the outcome is unknown (the
      // row may have been committed), so the unit is KEPT: admission errs on the stricter side.
      // Guaranteed: the day's counter is never below the rows created, so the daily cap cannot be
      // exceeded; it can be above them after an ambiguous failure (a lost unit, never an extra row).
      const day = utcDay(now);
      if (await reserveBudget(env.DB, NEW_LEAD_KEY, day, deps.newLeadsPerDay ?? NEW_LEADS_PER_DAY)) {
        const made = await upsertLead(env.LEADS_DB, form.email, "gate", countryOf(request), now, limits.maxLeads);
        if (made === null || !made.created) await refundNewLead(env.DB, day);
        lead = made;
      }
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
      await recordKitEventCapped(env.LEADS_DB, leadId, "unlock", now, limits.maxKitEvents);
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
      // Closed health words, each at most once per UTC day and retried while no channel takes them: that
      // new addresses are not being stored, and that the gate is far busier than usual.
      if (!stored) await healthAlertOnce(env, deps, CAP_ALERT_KEY, "lead_cap_reached", now, dispatch);
      if (busy) await healthAlertOnce(env, deps, BUSY_ALERT_KEY, "gate_busy", now, dispatch);
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
    await assertLeadsSeparated(env); // same proof as the gate (separation.ts)
  } catch {
    log.log("link", "closed", "binding");
    return redirect(VIEW_FAIL_PAGE);
  }

  // The cookie is the only way in: not the query string, not a header. Free to check, so first.
  const raw = readUnlockCookie(request.headers.get("cookie"));
  const claims = raw === null ? null : await verifyToken(cfg.keys, raw, Math.floor(now / 1000)); // CONTROL:view-token
  if (claims === null) return back("expired");

  // Per-client minute limiter on its OWN binding (KIT_VIEW_LIMITER, 4 per 60 s documented), with
  // the same keyed hash as the gate but its own purpose label. A visitor can reload, use the back button
  // or return from the inquiry form; one client cannot spend another's allowance, and (4 x 60 = 240, below
  // ROUTE_BUDGET_VIEW 300) cannot spend an hour's view budget alone. Two or more clients can.
  const limiterSecret = secretBytes(env.VIEW_HASH_SECRET);
  if (limiterSecret === null || typeof env.VIEW_HASH_SECRET !== "string" || !hasLimiter(env.KIT_VIEW_LIMITER)) {
    log.log("link", "closed", "binding");
    return redirect(VIEW_FAIL_PAGE);
  }
  const address = normalizeAddress(request.headers.get("cf-connecting-ip"));
  const key = address === null ? null : await limiterKey(limiterSecret, env.VIEW_HASH_SECRET, "view", address);
  if (!(await checkIpMinute(env.KIT_VIEW_LIMITER, key))) return back("ip_minute"); // CONTROL:view-ip-minute

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
      const lead = await getLead(env.LEADS_DB, claims.lid); // CONTROL:lead-exists
      if (lead === null) return back("expired");
      floorMs = lead.session_floor_ts;
    }
  } catch {
    return back("other");
  }
  if (floorMs !== null && claims.iat <= Math.floor(floorMs / 1000)) return back("expired"); // CONTROL:session-floor

  // The shared view budget is charged only for a cookie that can still read the page: lead existence and
  // the applicable floor are checked first, so a revoked or orphaned cookie cannot spend it. The
  // per-client limiter above stays early (it has no D1 write).
  if (!(await reserveRoute({ ...env, DB: env.DB }, "view", now))) return back("route_budget"); // CONTROL:view-route-budget

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
