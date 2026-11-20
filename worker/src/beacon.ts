// SPDX-License-Identifier: Apache-2.0
//
// The short-lived signed beacon token (Refs #610). The page asks the Worker for a token once per page
// load; the click and view routes read it and charge the event to the verified lane (a valid token) or the
// unverified lane (anything else). The token is stateless: nothing is stored, no cookie is set, and the
// page keeps it in memory only. Nothing here logs, and no error text carries a key, a token or a claim.
//
// FORMAT  `b1.<payload>.<signature>`, both parts base64url without padding. The signature is HMAC-SHA256
//   over the ASCII text `b1.<payload>` under BEACON_HMAC_KEY, its own secret (never the media-kit key),
//   with its own version label, so a token of one kind cannot be read as the other.
// CLAIMS  sid (the site the request came from), ch (a short keyed hash of the client's coarse network
//   prefix, a /24 for IPv4 and a /48 for IPv6), iat and exp (at most BEACON_TTL_SECONDS apart), jti (16
//   random bytes). `ch` is recomputed on every check and never stored.
// MODES   BEACON_MODE is off (the default: no token is minted or read), observe (tokens are minted and
//   read, the lane is recorded, and nothing is ever refused or counted differently) or enforce (the
//   unverified lane's cap binds, clickceiling.ts).
// A token proves only that the page asked the Worker for one, recently, from the same network and site.

import { normalizeAddress } from "./address";
import { bodyAcceptable, MAX_URL_LENGTH, type RateLimiter } from "./click";
import { markClickSetupInvalid, type ClickLane } from "./clickceiling";
import { fromB64url, keyUsable, toB64url } from "./kit/token";
import { limiterKey, secretBytes } from "./ratekey";
import { loadSites, siteForRequest, type SitesEnv } from "./sites";

/** How long a token lives: 15 minutes, whatever a signer put in it. */
export const BEACON_TTL_SECONDS = 900;
/** How far an issued-at may sit in the future before the token is refused. */
export const BEACON_CLOCK_SKEW_SECONDS = 60;
/** The longest token text the verifier will look at. A real token is about 190 characters. */
export const MAX_BEACON_TOKEN_LENGTH = 320;
/** The mint route: exactly this path. */
export const MINT_ROUTE = "/e/t";
/** The query parameter that carries the token on a click ping or a view beacon. */
export const TOKEN_PARAM = "t";

const VERSION = "b1";
const HASH_LEN = 32;
const SID_RE = /^[a-z0-9][a-z0-9-]{0,19}$/;
const CH_RE = /^[0-9a-f]{8}$/;
const JTI_RE = /^[A-Za-z0-9_-]{22}$/;
const CLAIM_KEYS = ["ch", "exp", "iat", "jti", "sid"];
const enc = new TextEncoder();

export type BeaconMode = "off" | "observe" | "enforce";

export interface BeaconEnv {
  /** "off" (default), "observe" or "enforce"; exact words only, read per request. Anything else is unusable. */
  BEACON_MODE?: string;
  /** Secret, at least 32 bytes. Signs and checks beacon tokens. Never the media-kit key. */
  BEACON_HMAC_KEY?: string;
  /** Optional: the previous key during a rotation, accepted for checking only. */
  BEACON_HMAC_KEY_PREVIOUS?: string;
  /** Rate limiter for the mint route (per client). Without it the mint issues nothing. */
  MINT_LIMITER?: RateLimiter;
}

export interface BeaconClaims {
  sid: string;
  ch: string;
  iat: number;
  exp: number;
}

/** BEACON_MODE: unset or empty is off; the three words are exact; anything else is null (unusable). */
export function parseBeaconMode(v: unknown): BeaconMode | null {
  if (v === undefined || v === "") return "off";
  return v === "off" || v === "observe" || v === "enforce" ? v : null;
}

/** The mode and the usable keys (current first), or null when the mode is unusable. */
export function beaconSetup(env: BeaconEnv): { mode: BeaconMode; keys: string[] } | null {
  const mode = parseBeaconMode(env.BEACON_MODE);
  if (mode === null) return null;
  return { mode, keys: [env.BEACON_HMAC_KEY, env.BEACON_HMAC_KEY_PREVIOUS].filter(keyUsable) };
}

// ---------------------------------------------------------- client hash

/** A /24 for IPv4 and a /48 for IPv6, from the canonical text normalizeAddress gives; null for anything else. */
export function coarsePrefix(normalized: string | null): string | null {
  if (normalized === null) return null;
  if (normalized.startsWith("v6:")) {
    const hex = normalized.slice(3);
    return /^[0-9a-f]{16}$/.test(hex) ? `v6:${hex.slice(0, 12)}` : null;
  }
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.\d{1,3}$/.exec(normalized);
  return m ? `v4:${m[1]}.${m[2]}.${m[3]}` : null;
}

async function hmac(key: string, usage: "sign" | "verify"): Promise<CryptoKey> {
  return crypto.subtle.importKey("raw", enc.encode(key), { name: "HMAC", hash: "SHA-256" }, false, [usage]);
}

/** The first 32 bits of HMAC(key, "bt1|" + prefix), as 8 hex digits; null when there is no usable prefix. */
export async function clientHashFor(key: string, normalizedAddress: string | null): Promise<string | null> {
  const prefix = coarsePrefix(normalizedAddress);
  if (prefix === null || !keyUsable(key)) return null;
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", await hmac(key, "sign"), enc.encode(`bt1|${prefix}`)));
  let out = "";
  for (const b of mac.slice(0, 4)) out += b.toString(16).padStart(2, "0");
  return out;
}

// --------------------------------------------------------------- tokens

function isInt(n: unknown): n is number {
  return typeof n === "number" && Number.isSafeInteger(n);
}

function claimsOk(c: { sid?: unknown; ch?: unknown; iat?: unknown; exp?: unknown }): boolean {
  return (
    typeof c.sid === "string" &&
    SID_RE.test(c.sid) &&
    typeof c.ch === "string" &&
    CH_RE.test(c.ch) &&
    isInt(c.iat) &&
    isInt(c.exp) &&
    c.exp > c.iat &&
    c.exp - c.iat <= BEACON_TTL_SECONDS
  );
}

/** Sign claims with `key`. Throws a generic error on a short key or bad claims. */
export async function signBeacon(key: string, claims: BeaconClaims): Promise<string> {
  if (!keyUsable(key)) throw new Error("beacon signing key is missing or too short");
  if (!claimsOk(claims)) throw new Error("beacon claims are invalid");
  const jti = toB64url(crypto.getRandomValues(new Uint8Array(16)));
  const payload = toB64url(
    enc.encode(JSON.stringify({ sid: claims.sid, ch: claims.ch, iat: claims.iat, exp: claims.exp, jti })),
  );
  const body = `${VERSION}.${payload}`;
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", await hmac(key, "sign"), enc.encode(body)));
  return `${body}.${toB64url(mac)}`;
}

/**
 * Verify a token. `keys` is the current key, or `[current, previous]` during a rotation. Returns the claims
 * and the key that matched (the client hash is recomputed under it), or null for anything that is not a
 * currently valid token. Never throws.
 */
export async function verifyBeacon(
  keys: string | readonly string[],
  token: string,
  nowSec: number,
): Promise<(BeaconClaims & { key: string }) | null> {
  try {
    if (typeof token !== "string" || token.length === 0 || token.length > MAX_BEACON_TOKEN_LENGTH) return null;
    if (!isInt(nowSec)) return null;
    const parts = token.split(".");
    if (parts.length !== 3 || parts[0] !== VERSION) return null;
    const [, payloadB64, sigB64] = parts as [string, string, string];
    const sig = fromB64url(sigB64);
    if (sig === null || sig.length !== HASH_LEN) return null;
    const payloadBytes = fromB64url(payloadB64);
    if (payloadBytes === null || payloadBytes.length === 0) return null;

    const list = (typeof keys === "string" ? [keys] : [...keys]).filter(keyUsable);
    if (list.length === 0) return null;
    const body = enc.encode(`${VERSION}.${payloadB64}`);
    // Check every key, so the work does not depend on which one matched.
    let matched: string | null = null;
    for (const k of list) {
      const ok = await crypto.subtle.verify("HMAC", await hmac(k, "verify"), sig, body); // CONTROL:beacon-signature
      if (ok && matched === null) matched = k;
    }
    if (matched === null) return null;

    const parsed: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(payloadBytes));
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    const c = parsed as { sid?: unknown; ch?: unknown; iat?: unknown; exp?: unknown; jti?: unknown };
    if (Object.keys(c).sort().join(",") !== CLAIM_KEYS.join(",")) return null;
    if (typeof c.jti !== "string" || !JTI_RE.test(c.jti)) return null;
    if (!claimsOk(c)) return null;
    const out = c as unknown as BeaconClaims;
    if (nowSec >= out.exp) return null; // CONTROL:beacon-expiry
    if (out.iat > nowSec + BEACON_CLOCK_SKEW_SECONDS) return null;
    return { sid: out.sid, ch: out.ch, iat: out.iat, exp: out.exp, key: matched };
  } catch {
    return null;
  }
}

// ----------------------------------------------------------------- lane

/** The token text on a request URL: exactly one `t` value, else null (a repeated or missing one earns nothing). */
function tokenOf(request: Request): string | null {
  if (request.url.length > MAX_URL_LENGTH) return null;
  const all = new URL(request.url).searchParams.getAll(TOKEN_PARAM);
  return all.length === 1 && all[0] !== undefined && all[0].length > 0 ? all[0] : null;
}

/**
 * The lane a request earns: verified (v) only for a currently valid token that was signed for THIS site and
 * minted for this client's network prefix; unverified (u) for everything else, always, with no error and no
 * difference in the response. The token is read from the URL only, never a header or a body.
 */
export async function beaconLane(
  request: Request,
  keys: readonly string[],
  siteId: string,
  nowMs: number,
): Promise<ClickLane> {
  try {
    const t = tokenOf(request);
    if (t === null || keys.length === 0) return "u";
    const claims = await verifyBeacon(keys, t, Math.floor(nowMs / 1000));
    if (claims === null || claims.sid !== siteId) return "u"; // CONTROL:beacon-site
    const ch = await clientHashFor(claims.key, normalizeAddress(request.headers.get("CF-Connecting-IP"))); // CONTROL:beacon-client
    return ch !== null && ch === claims.ch ? "v" : "u";
  } catch {
    return "u";
  }
}

// ----------------------------------------------------------------- mint

export interface MintEnv extends SitesEnv, BeaconEnv {
  DB: D1Database;
  /** Same secret the click and view routes hash with; the mint limiter key comes from it. */
  VIEW_HASH_SECRET?: string;
}

/** Every refusal is this exact response: an empty 204. */
function noContent(): Response {
  return new Response(null, { status: 204 });
}

/**
 * Handle `POST /e/t`: mint one token for the site whose host matches Origin or Ping-From, bound to the
 * caller's coarse network prefix. Check order: mode, method, URL, path, opt-out signals, site, body, keys,
 * client address, per-client limiter. Success is the only answer with a body (the token and its life in
 * seconds); every other outcome is the empty 204 the other routes give. Stores nothing, writes no D1 row
 * (except the once-a-day setup flag), sets no cookie.
 */
export async function handleMint(request: Request, env: MintEnv, nowMs: number = Date.now()): Promise<Response> {
  try {
    const setup = beaconSetup(env);
    if (setup === null) {
      await markClickSetupInvalid(env.DB, nowMs);
      return noContent();
    }
    if (setup.mode === "off") return noContent();
    if (request.method !== "POST") return noContent();
    if (request.url.length > MAX_URL_LENGTH) return noContent();
    if (new URL(request.url).pathname !== MINT_ROUTE) return noContent();
    // A visitor who signals Global Privacy Control or Do Not Track is issued nothing.
    if (request.headers.get("Sec-GPC")?.trim() === "1") return noContent();
    if (request.headers.get("DNT")?.trim() === "1") return noContent();
    const site = siteForRequest(loadSites(env).sites, request);
    if (site === null) return noContent();
    if (!(await bodyAcceptable(request))) return noContent();

    const key = env.BEACON_HMAC_KEY;
    if (!keyUsable(key)) {
      await markClickSetupInvalid(env.DB, nowMs);
      return noContent();
    }
    const secret = secretBytes(env.VIEW_HASH_SECRET);
    const addr = normalizeAddress(request.headers.get("CF-Connecting-IP"));
    if (secret === null || typeof env.VIEW_HASH_SECRET !== "string" || addr === null || !env.MINT_LIMITER) return noContent();
    const ch = await clientHashFor(key, addr);
    if (ch === null) return noContent();
    const limiter = await limiterKey(secret, env.VIEW_HASH_SECRET, "mnt", addr);
    if (!(await env.MINT_LIMITER.limit({ key: limiter })).success) return noContent(); // CONTROL:beacon-mint-limiter

    const iat = Math.floor(nowMs / 1000);
    const t = await signBeacon(key, { sid: site.id, ch, iat, exp: iat + BEACON_TTL_SECONDS });
    return new Response(JSON.stringify({ t, ttl: BEACON_TTL_SECONDS }), {
      status: 200,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    return noContent();
  }
}
