// SPDX-License-Identifier: Apache-2.0
//
// The media kit gate (issue #263): `POST /k/gate` and `GET /k/view`. Everything
// else under `/k/` is an empty 404. Nothing else in the Worker changes: every
// other path keeps its empty 204 (index.ts).
//
// FLOW. A visitor on the static `/kit/` page posts the form (email, honeypot,
// Turnstile response) to `/k/gate`. After the guards below, the address is
// stored as a lead and the Worker answers `303 /k/view` with the signed
// `__Host-ra_kit` cookie. `GET /k/view` checks the cookie and serves the gated
// page from the KV key `kit/v1` (uploaded by the deploy, never part of the
// Pages deploy). The proof that reaches the "static page" is therefore the
// cookie alone: it is HttpOnly, set on the same host, and read only here.
//
// GUARD ORDER for the gate (cheap and free first, anything that writes last):
//   1 kill switch   2 route/method   3 env and bindings   4 origin   5 bot UA
//   6 content type and declared length   7 per-client minute limiter
//   8 combined D1 write budget (limits load), then the hourly route budget
//   9 bounded body read, honeypot, email normalization
//  10 Turnstile   11 lead insert (lead cap)   12 unlock event   13 alert   14 cookie
// Steps 1 to 7 make no D1 statement.
//
// FAIL CLOSED, ONE ANSWER. Every failure of the gate is the same response: an
// empty `303 /kit/retry/` with `Cache-Control: private, no-store` and no
// cookie. A missing secret or binding, a bad limit, a Turnstile outage, a full
// lead table and a database error all look the same, so the response reveals
// nothing about which check failed. Failures log one closed code (log.ts).
//
// RESIDUAL (v1, stated in docs/security/threat-model.md): the address is not
// verified. Anyone who passes the challenge unlocks with any address. The gated
// page therefore holds only material the owner shows to anyone.
//
// PRIVACY. No IP address, user agent or referrer is stored; the IP is used only
// as a limiter key through an HMAC (ratekey.ts). The country is the two-letter
// edge code only. No stored value is ever echoed in a response.

import { BOT_UA, MAX_UA_LENGTH } from "../click";
import { normalizeAddress } from "../address";
import { limiterKeyOrIsolate } from "../ratekey";
import { scheduleAlert, type AlertEnv } from "./alert";
import { loadKitLimits, recordKitEventCapped, reserveRoute, type KitLimitsEnv } from "./budgets";
import { isKitKilled, loadKitEnv, type KitConfig, type KitEnv } from "./env";
import { killedResponse } from "./guard";
import { MAX_FORM_BYTES, readForm } from "./input";
import { getLead, upsertLead } from "./leads";
import { checkIpMinute } from "./limits";
import { forRequest, type Reason } from "./log";
import { readUnlockCookie, signToken, unlockCookie, verifyToken } from "./token";
import { expectedHostname, verifyTurnstile } from "./turnstile";

export const GATE_PATH = "/k/gate";
export const VIEW_PATH = "/k/view";
export const RETRY_PATH = "/kit/retry/";
export const KIT_PAGE_PATH = "/kit/";
/** KV key of the gated page. */
export const GATED_KEY = "kit/v1";
const MAX_URL_LENGTH = 2048;
const FORM_CONTENT_TYPE = "application/x-www-form-urlencoded";
const COUNTRY = /^[A-Z]{2}$/;

/** Everything the kit routes read from the Worker environment. */
export type GateEnv = KitEnv &
  KitLimitsEnv &
  AlertEnv & {
    /** Host of the page that carries the gate form, when it differs from SITE_HOST. */
    KIT_HOST?: string;
    /** Optional keyed-hash secret for the limiter key (shared with the click route). */
    VIEW_HASH_SECRET?: string;
  };

export interface GateDeps {
  fetchFn?: typeof fetch;
  nowMs?: number;
}

type Ctx = { waitUntil(p: Promise<unknown>): void } | undefined;

const NO_STORE = "private, no-store";

/** Every gate failure. Fixed bytes: nothing from the request or from storage. */
function retryResponse(): Response {
  return new Response(null, { status: 303, headers: { Location: RETRY_PATH, "Cache-Control": NO_STORE } });
}

/** Every view failure (no or bad cookie). */
function kitPageResponse(): Response {
  return new Response(null, { status: 303, headers: { Location: KIT_PAGE_PATH, "Cache-Control": NO_STORE } });
}

function notFound(): Response {
  return new Response(null, { status: 404, headers: { "Cache-Control": NO_STORE } });
}

/** True for `/k` and anything under `/k/`. An oversize URL is never parsed here. */
export function isKitRoute(request: Request): boolean {
  if (request.url.length > MAX_URL_LENGTH) return false;
  try {
    const p = new URL(request.url).pathname;
    return p === "/k" || p.startsWith("/k/");
  } catch {
    return false;
  }
}

/** The host the gate form is served from: KIT_HOST when set, else SITE_HOST. A bad value is null. */
function kitHost(env: GateEnv): string | null {
  const configured = env.KIT_HOST;
  return expectedHostname({ SITE_HOST: configured !== undefined && configured !== "" ? configured : env.SITE_HOST });
}

function countryOf(request: Request): string | null {
  const c = request.headers.get("CF-IPCountry");
  return c !== null && COUNTRY.test(c) && c !== "XX" ? c : null;
}

/** `POST /k/gate`. Always a 303 to the retry page unless the visitor is unlocked. */
export async function handleGate(request: Request, env: GateEnv, ctx: Ctx, deps: GateDeps = {}): Promise<Response> {
  const log = forRequest();
  const fail = (reason: Reason): Response => {
    log.log("gate", "rejected", reason);
    return retryResponse();
  };
  try {
    // 3. Environment and bindings: any problem is the one generic answer.
    let cfg: KitConfig;
    try {
      cfg = loadKitEnv(env);
    } catch {
      return fail("binding");
    }
    const host = kitHost(env);
    if (host === null) return fail("binding");
    if (cfg.killed) return fail("kill");

    // 4. Origin: exactly the kit host; a missing or "null" Origin is refused.
    if (request.headers.get("Origin") !== `https://${host}`) return fail("origin"); // CONTROL:origin

    // 5. User agent: missing, oversize or bot-like.
    const ua = request.headers.get("User-Agent");
    if (!ua || ua.length > MAX_UA_LENGTH || BOT_UA.test(ua)) return fail("bot_ua"); // CONTROL:bot-ua

    // 6. Exact content type and a declared length inside the cap (header only).
    if (request.headers.get("Content-Type") !== FORM_CONTENT_TYPE) return fail("size");
    const declared = request.headers.get("Content-Length");
    if (declared === null || !/^[0-9]{1,9}$/.test(declared) || Number(declared) > MAX_FORM_BYTES) return fail("size"); // CONTROL:body-cap

    // 7. Per-client burst limit. The key is a keyed hash, never the address.
    const key = await limiterKeyOrIsolate(env.VIEW_HASH_SECRET, "gate", normalizeAddress(request.headers.get("CF-Connecting-IP")) ?? "unknown");
    if (!(await checkIpMinute(env.GATE_LIMITER, key))) return fail("ip_minute"); // CONTROL:ip-minute

    // 8. The combined daily D1 write budget (loadKitLimits refuses a
    // configuration over the quota or with an unusable value), then the hourly
    // route budget: the first D1 write.
    const now = deps.nowMs ?? Date.now();
    let limits;
    try {
      limits = loadKitLimits(env);
    } catch {
      return fail("route_budget");
    }
    if (!(await reserveRoute(env, "gate", now))) return fail("route_budget"); // CONTROL:route-budget

    // 9. Body, honeypot, address.
    const form = await readForm(request);
    if (form === null) return fail("size"); // CONTROL:honeypot (a filled honeypot is a null form)
    if (form.email === null) return fail("email");
    if (form.turnstile === null) return fail("turnstile");

    // 10. Turnstile, against the kit host.
    if (!(await verifyTurnstile(env, form.turnstile, "gate", deps.fetchFn ?? fetch, { hostname: host }))) return fail("turnstile"); // CONTROL:turnstile

    // 11. The lead. The country is the edge's two-letter code or nothing.
    const country = countryOf(request);
    let lead: { id: string; created: boolean } | null;
    try {
      lead = await upsertLead(env.DB, form.email, "gate", country, now, limits.maxLeads);
    } catch {
      return fail("other");
    }
    if (lead === null) return fail("lead_cap"); // CONTROL:lead-cap

    // 12. The unlock event. An analytics row never blocks a valid unlock.
    try {
      await recordKitEventCapped(env.DB, lead.id, "unlock", now, limits.maxKitEvents);
    } catch {
      // Same: the lead exists and the visitor passed the challenge.
    }

    // 14. The cookie. A signing failure is the generic answer: no unlock.
    const iat = Math.floor(now / 1000);
    let token: string;
    let cookie: string;
    try {
      token = await signToken(cfg.keys[0]!, { lid: lead.id, iat, exp: iat + cfg.tokenTtlSeconds, cc: country ?? "" });
      cookie = unlockCookie(token, cfg.tokenTtlSeconds);
    } catch {
      return fail("other");
    }

    // 13. Owner alert for a NEW address only (a repeat submit is not news).
    if (lead.created) scheduleAlert(env, ctx, "lead_created", { fetchFn: deps.fetchFn, nowMs: now });

    log.log("gate", "ok");
    return new Response(null, {
      status: 303,
      headers: { Location: VIEW_PATH, "Cache-Control": NO_STORE, "Set-Cookie": cookie },
    });
  } catch {
    return fail("other");
  }
}

const VIEW_HEADERS: Record<string, string> = {
  "Content-Type": "text/html; charset=utf-8",
  "Cache-Control": NO_STORE,
  "X-Robots-Tag": "noindex",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "Content-Security-Policy":
    "default-src 'none'; style-src 'self'; img-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
};

/** `GET /k/view` (and HEAD). The gated page for a valid cookie, else a 303 to the public kit page. */
export async function handleView(request: Request, env: GateEnv, deps: GateDeps = {}): Promise<Response> {
  const log = forRequest();
  const deny = (reason: Reason): Response => {
    log.log("view", "rejected", reason);
    return kitPageResponse();
  };
  try {
    let cfg: KitConfig;
    try {
      cfg = loadKitEnv(env);
    } catch {
      return deny("binding");
    }
    if (cfg.killed) return deny("kill");

    // The cookie only: a token in the query, a header or a form is never read. CONTROL:cookie-only
    const raw = readUnlockCookie(request.headers.get("Cookie"));
    if (raw === null) return deny("other");
    const now = deps.nowMs ?? Date.now();
    const claims = await verifyToken(cfg.keys, raw, Math.floor(now / 1000));
    if (claims === null) return deny("expired");

    // The lead must still exist (a deleted lead's cookie stops working), and a
    // token issued before the lead's session floor is refused.
    const lead = await getLead(env.DB, claims.lid); // CONTROL:lead-exists
    if (lead === null) return deny("other");
    if (lead.session_floor_ts !== null && claims.iat * 1000 < lead.session_floor_ts) return deny("expired");

    // Only a valid cookie reaches a D1 write: the hourly view budget.
    if (!(await reserveRoute(env, "view", now))) return deny("route_budget");

    let page: string | null;
    try {
      page = await env.KIT.get(GATED_KEY, "text");
    } catch {
      page = null;
    }
    if (page === null || page === "") {
      log.log("view", "failed", "other");
      return notFound();
    }
    log.log("view", "ok");
    return new Response(request.method === "HEAD" ? null : page, { status: 200, headers: VIEW_HEADERS });
  } catch {
    return deny("other");
  }
}

/** Router for every `/k/` request. The kill switch comes first and answers every route and method alike. */
export async function handleKit(request: Request, env: GateEnv, ctx: Ctx, deps: GateDeps = {}): Promise<Response> {
  try {
    if (isKitKilled(env)) {
      forRequest().log("kill", "closed", "kill");
      return killedResponse(); // CONTROL:kill-switch
    }
    const path = new URL(request.url).pathname;
    const method = request.method.toUpperCase();
    if (path === GATE_PATH && method === "POST") return await handleGate(request, env, ctx, deps);
    if (path === VIEW_PATH && (method === "GET" || method === "HEAD")) return await handleView(request, env, deps);
    return notFound();
  } catch {
    return notFound();
  }
}
