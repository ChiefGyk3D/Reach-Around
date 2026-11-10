// SPDX-License-Identifier: Apache-2.0
//
// The business inquiry form behind the media kit (issues #492 and #429).
//
//   POST /k/inquiry         the form on the gated kit page (no client script)
//   GET  /k/inquiry/sent    fixed result page
//   GET  /k/inquiry/error   fixed result page
//
// A visitor can post only with a valid unlock cookie from the gate, so the form
// is as private as the gated page. Nothing is stored: the inquiry is delivered
// once through the owner alert channels (src/alerts, event `inquiry_received`)
// and forgotten. Every refusal is an answer with no request-derived text in it.
//
// ORDER (each step is cheaper than the next): kill switch, method, origin,
// content type, configuration, unlock cookie, per-client limiter, bounded body,
// field rules, honeypot, daily budget, delivery.
//
// Nothing here logs request data; the only logging is the kit logger's closed
// vocabulary.

import { notifyOwner, type AlertsEnv, type DispatchDeps } from "../alerts/dispatcher";
import { normalizeAddress } from "../address";
import { limiterKeyOrIsolate } from "../ratekey";
import { readBoundedText } from "./body";
import { loadKitLimits } from "./budgets";
import { isKitKilled, type KitEnv } from "./env";
import { guardRequest, originAllowList } from "./guard";
import { normalizeEmail } from "./input";
import { reserveBudget, checkIpMinute, utcDay, type MinuteLimiter } from "./limits";
import { forRequest } from "./log";
import { MIN_KEY_BYTES, readUnlockCookie, verifyToken } from "./token";

export const INQUIRY_ROUTE = "/k/inquiry";
export const INQUIRY_SENT_ROUTE = "/k/inquiry/sent";
export const INQUIRY_ERROR_ROUTE = "/k/inquiry/error";
/** A visitor without a valid unlock goes back to the gate (the page that holds the gate form). */
export const GATE_PAGE = "/kit/retry/";
/** Where the two result pages send the visitor back to. */
const KIT_PAGE = "/kit/";

/** Maximum lengths are in characters (code points), not bytes. */
export const INQUIRY_LIMITS = {
  name: 80,
  brand: 80,
  budget: 40,
  details: 800,
  /** A fully escaped body of the longest allowed fields, with room to spare. */
  maxBodyBytes: 16 * 1024,
} as const;

const FIELDS = new Set(["name", "email", "brand", "budget", "details", "website"]);
const CONTENT_TYPE = "application/x-www-form-urlencoded";
const BAD_PERCENT = /%(?![0-9a-fA-F]{2})/;
const BODY_DEADLINE_MS = 5000;
const BUDGET_KEY = "inquiry";

// One-line fields: controls, format (zero-width, bidi), line and paragraph separators.
const ONE_LINE_BAD = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u;
// Details: tab and line feed are allowed (removed before this check); no other control, separator or bidi mark.
const DETAILS_BAD = /[\p{Cc}\p{Zl}\p{Zp}\p{Bidi_Control}]/u;
const REPLACEMENT = "�";

export interface InquiryEnv extends AlertsEnv, Pick<KitEnv, "KIT_KILL" | "TOKEN_HMAC_KEY" | "TOKEN_HMAC_KEY_PREVIOUS"> {
  /**
   * Workers Rate Limiting binding for the form: a few posts per client per
   * minute. Required; without it the form refuses (fail closed).
   */
  INQUIRY_LIMITER?: MinuteLimiter;
  /** Keys the limiter key hash, the same secret the click and view routes use. */
  VIEW_HASH_SECRET?: string;
}

export interface InquiryDeps {
  fetchFn?: typeof fetch;
  nowMs?: number;
  /** The alert dispatcher; tests pass a fresh one so its dedup memory starts empty. */
  notify?: typeof notifyOwner;
}

const BASE_HEADERS = {
  "Cache-Control": "private, no-store",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
} as const;

function redirect(path: string): Response {
  return new Response(null, { status: 303, headers: { ...BASE_HEADERS, Location: path } });
}

function plain(status: number, extra: Record<string, string> = {}): Response {
  return new Response(null, { status, headers: { ...BASE_HEADERS, ...extra } });
}

function page(title: string, line: string): Response {
  // Fixed text only. Nothing from the request is ever placed in it.
  const html =
    `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">` +
    `<meta name="color-scheme" content="dark"><meta name="robots" content="noindex"><title>${title}</title>` +
    `<main><h1>${title}</h1><p>${line}</p><p><a href="${KIT_PAGE}">Back to the media kit</a></p></main>`;
  return new Response(html, {
    status: 200,
    headers: {
      ...BASE_HEADERS,
      "Content-Type": "text/html; charset=utf-8",
      "Content-Security-Policy": "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    },
  });
}

const length = (s: string): number => [...s].length;
// No lone surrogates (String.isWellFormed is newer than this project's TypeScript library setting).
const LONE_SURROGATE = /[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/;
const wellFormed = (s: string): boolean => !LONE_SURROGATE.test(s);

function oneLine(raw: string | undefined, max: number, required: boolean): string | null {
  const v = (raw ?? "").trim();
  if (v === "") return required ? null : "";
  if (length(v) > max || ONE_LINE_BAD.test(v) || !wellFormed(v) || v.includes(REPLACEMENT)) return null;
  return v;
}

export interface InquiryFields {
  name: string;
  email: string | null;
  brand: string;
  budget: string;
  details: string;
}

type Parsed = { ok: true; honeypot: boolean; fields: InquiryFields } | { ok: false };

/** Parse and validate the body. Anything outside the rules is refused, never repaired. */
export function parseInquiry(text: string): Parsed {
  if (BAD_PERCENT.test(text)) return { ok: false };
  const seen = new Map<string, string>();
  for (const [k, v] of new URLSearchParams(text)) {
    if (!FIELDS.has(k) || seen.has(k)) return { ok: false };
    seen.set(k, v);
  }
  if ((seen.get("website") ?? "") !== "") {
    // A bot filled the field no person sees. The caller answers as if it worked.
    return { ok: true, honeypot: true, fields: { name: "", email: null, brand: "", budget: "", details: "" } };
  }
  const name = oneLine(seen.get("name"), INQUIRY_LIMITS.name, true);
  const brand = oneLine(seen.get("brand"), INQUIRY_LIMITS.brand, false);
  const budget = oneLine(seen.get("budget"), INQUIRY_LIMITS.budget, false);
  if (name === null || brand === null || budget === null) return { ok: false };

  const rawEmail = seen.get("email") ?? "";
  let email: string | null = null;
  if (rawEmail !== "") {
    email = normalizeEmail(rawEmail);
    if (email === null) return { ok: false };
  }

  const details = (seen.get("details") ?? "").replace(/\r\n?/g, "\n").trim();
  if (
    length(details) > INQUIRY_LIMITS.details ||
    DETAILS_BAD.test(details.replace(/[\t\n]/g, "")) ||
    !wellFormed(details) ||
    details.includes(REPLACEMENT)
  ) {
    return { ok: false };
  }
  return { ok: true, honeypot: false, fields: { name, email, brand, budget, details } };
}

function signingKeys(env: InquiryEnv): string[] | null {
  const out: string[] = [];
  const enc = new TextEncoder();
  const cur = env.TOKEN_HMAC_KEY;
  if (typeof cur !== "string" || enc.encode(cur).length < MIN_KEY_BYTES) return null;
  out.push(cur);
  const prev = env.TOKEN_HMAC_KEY_PREVIOUS;
  if (typeof prev === "string" && prev !== "" && prev !== cur) {
    if (enc.encode(prev).length < MIN_KEY_BYTES) return null;
    out.push(prev);
  }
  return out;
}

function hasLimiter(l: unknown): l is MinuteLimiter {
  return typeof l === "object" && l !== null && typeof (l as { limit?: unknown }).limit === "function";
}

async function submit(request: Request, env: InquiryEnv, deps: InquiryDeps): Promise<Response> {
  const log = forRequest();
  const now = deps.nowMs ?? Date.now();

  const type = request.headers.get("content-type");
  if (type !== CONTENT_TYPE) return plain(415);

  const keys = signingKeys(env);
  if (keys === null || !hasLimiter(env.INQUIRY_LIMITER)) {
    log.log("send", "closed", "binding");
    return plain(503);
  }

  // Gate: a valid unlock cookie from the kit gate, and nothing else, opens the form.
  const token = readUnlockCookie(request.headers.get("cookie"));
  const claims = token === null ? null : await verifyToken(keys, token, Math.floor(now / 1000)); // CONTROL:inquiry-gate
  if (claims === null) {
    log.log("send", "rejected", "expired");
    return redirect(GATE_PAGE);
  }

  // Per-client limiter, keyed by a keyed hash of the address (the raw address never reaches the binding).
  const address = normalizeAddress(request.headers.get("cf-connecting-ip"));
  const key = address === null ? null : await limiterKeyOrIsolate(env.VIEW_HASH_SECRET, "inq", address);
  if (!(await checkIpMinute(env.INQUIRY_LIMITER, key))) {
    // CONTROL:inquiry-ip-minute
    log.log("send", "throttled", "ip_minute");
    return plain(429, { "Retry-After": "60" });
  }

  const read = await readBoundedText(request, INQUIRY_LIMITS.maxBodyBytes, { deadlineMs: BODY_DEADLINE_MS }); // CONTROL:inquiry-size
  if (!read.ok) {
    log.log("send", "rejected", "size");
    return plain(read.status === 413 ? 413 : 400);
  }
  const parsed = parseInquiry(read.text);
  if (!parsed.ok) {
    log.log("send", "rejected", "email");
    return redirect(INQUIRY_ERROR_ROUTE);
  }
  if (parsed.honeypot) {
    log.log("send", "dropped", "other");
    return redirect(INQUIRY_SENT_ROUTE); // CONTROL:inquiry-honeypot
  }

  // Daily budget: only a valid inquiry spends it. It is the owner alert budget, so the form can never
  // send the owner more messages than the owner agreed to receive. A refusal is an outage, not a bill.
  let allowed = false;
  try {
    allowed = await reserveBudget(env.DB, BUDGET_KEY, utcDay(now), loadKitLimits(env).dailyAlertBudget); // CONTROL:inquiry-budget
  } catch {
    allowed = false;
  }
  if (!allowed) {
    log.log("send", "over_budget", "route_budget");
    return plain(503, { "Retry-After": "3600" });
  }

  const send = deps.notify ?? notifyOwner;
  const dispatch: DispatchDeps = { nowMs: now };
  if (deps.fetchFn !== undefined) dispatch.fetchFn = deps.fetchFn;
  const receipt = await send(env, { kind: "inquiry_received", atMs: now, ...parsed.fields }, dispatch);
  if (!receipt.some((r) => r.ok)) {
    log.log("send", "failed", "provider");
    return redirect(INQUIRY_ERROR_ROUTE);
  }
  log.log("send", "ok");
  return redirect(INQUIRY_SENT_ROUTE);
}

/** True for the three paths this module owns. */
export function isInquiryRoute(pathname: string): boolean {
  return pathname === INQUIRY_ROUTE || pathname === INQUIRY_SENT_ROUTE || pathname === INQUIRY_ERROR_ROUTE;
}

export async function handleInquiry(request: Request, env: InquiryEnv, deps: InquiryDeps = {}): Promise<Response> {
  let origins: string[] = [];
  if (typeof env.SITE_HOST === "string" && env.SITE_HOST !== "") origins = [`https://${env.SITE_HOST}`];
  const decision = guardRequest(request, env, { methods: ["GET", "HEAD", "POST"], originCheck: originAllowList(origins) });
  if (!decision.allow) return decision.response;
  if (isKitKilled(env)) return plain(503);

  let path: string;
  try {
    path = new URL(request.url).pathname;
  } catch {
    return plain(404);
  }
  const method = request.method.toUpperCase();
  if (path === INQUIRY_ROUTE) return method === "POST" ? submit(request, env, deps) : plain(405, { Allow: "POST" });
  if (method === "POST") return plain(405, { Allow: "GET, HEAD" });
  if (path === INQUIRY_SENT_ROUTE) return page("Inquiry sent", "Your inquiry was sent. The owner will reply by email if you gave an address.");
  if (path === INQUIRY_ERROR_ROUTE) {
    return page("Inquiry not sent", "Your inquiry was not sent. Check the form and try again, or use the email address on the media kit page.");
  }
  return plain(404);
}
