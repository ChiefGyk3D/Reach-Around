// SPDX-License-Identifier: Apache-2.0
//
// Signed unlock token for the media-kit gate: `v1.<payload>.<signature>`, both
// parts base64url without padding. The signature is HMAC-SHA256 over the ASCII
// text `v1.<payload>` and is checked with crypto.subtle.verify, which compares
// in constant time. Nothing here logs, and no error text carries a key, a
// token or a claim value.

export const COOKIE_NAME = "__Host-ra_kit";
export const MIN_KEY_BYTES = 32;
/** Longest lifetime the verifier accepts, whatever a signer put in the token. */
export const MAX_TOKEN_AGE_SECONDS = 168 * 3600;
/** How far an issued-at may sit in the future before the token is refused. */
export const CLOCK_SKEW_SECONDS = 60;

const VERSION = "v1";
const MAX_TOKEN_LENGTH = 1024;
const HASH_LEN = 32;
const ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const JTI_RE = /^[A-Za-z0-9_-]{22}$/;
const CC_RE = /^([A-Z]{2})?$/;
const B64URL_RE = /^[A-Za-z0-9_-]*$/;
const COOKIE_VALUE_RE = /^[A-Za-z0-9._-]{1,1024}$/;

export interface TokenClaims {
  lid: string;
  iat: number;
  exp: number;
  cc: string;
}

const textEncoder = new TextEncoder();

function toB64url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** Strict decode: alphabet only, no padding, canonical (re-encodes identically). */
function fromB64url(s: string): Uint8Array | null {
  if (!B64URL_RE.test(s) || s.length % 4 === 1) return null;
  try {
    const std = s.replace(/-/g, "+").replace(/_/g, "/");
    const bin = atob(std + "=".repeat((4 - (std.length % 4)) % 4));
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return toB64url(out) === s ? out : null;
  } catch {
    return null;
  }
}

function keyUsable(key: unknown): key is string {
  return typeof key === "string" && textEncoder.encode(key).length >= MIN_KEY_BYTES;
}

async function importKey(key: string, usage: "sign" | "verify"): Promise<CryptoKey> {
  return crypto.subtle.importKey("raw", textEncoder.encode(key), { name: "HMAC", hash: "SHA-256" }, false, [usage]);
}

function isInt(n: unknown): n is number {
  return typeof n === "number" && Number.isSafeInteger(n);
}

function claimsShapeOk(c: { lid?: unknown; iat?: unknown; exp?: unknown; cc?: unknown }): boolean {
  return (
    typeof c.lid === "string" &&
    ID_RE.test(c.lid) &&
    isInt(c.iat) &&
    isInt(c.exp) &&
    c.exp > c.iat &&
    c.exp - c.iat <= MAX_TOKEN_AGE_SECONDS &&
    typeof c.cc === "string" &&
    CC_RE.test(c.cc)
  );
}

/** Sign claims with `key`. Throws a generic error on a short key or bad claims. */
export async function signToken(key: string, claims: TokenClaims): Promise<string> {
  if (!keyUsable(key)) throw new Error("token signing key is missing or too short");
  if (!claimsShapeOk(claims)) throw new Error("token claims are invalid");
  const jti = toB64url(crypto.getRandomValues(new Uint8Array(16)));
  const payload = toB64url(
    textEncoder.encode(JSON.stringify({ lid: claims.lid, iat: claims.iat, exp: claims.exp, cc: claims.cc, jti })),
  );
  const body = `${VERSION}.${payload}`;
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", await importKey(key, "sign"), textEncoder.encode(body)));
  return `${body}.${toB64url(mac)}`;
}

/**
 * Verify a token. `keys` is the current key, or `[current, previous]` during a
 * rotation. Returns the claims, or null for anything that is not a currently
 * valid token. Never throws.
 */
export async function verifyToken(
  keys: string | readonly string[],
  token: string,
  nowSec: number,
): Promise<TokenClaims | null> {
  try {
    if (typeof token !== "string" || token.length === 0 || token.length > MAX_TOKEN_LENGTH) return null;
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

    const body = textEncoder.encode(`${VERSION}.${payloadB64}`);
    // Check every key, so the work does not depend on which one matched.
    let valid = false;
    for (const k of list) {
      const ok = await crypto.subtle.verify("HMAC", await importKey(k, "verify"), sig, body); // CONTROL:token-signature
      if (ok) valid = true;
    }
    if (!valid) return null;

    const parsed: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(payloadBytes));
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    const c = parsed as { lid?: unknown; iat?: unknown; exp?: unknown; cc?: unknown; jti?: unknown };
    if (typeof c.jti !== "string" || !JTI_RE.test(c.jti)) return null;
    if (!claimsShapeOk(c)) return null;
    const out = c as TokenClaims;
    if (nowSec >= out.exp) return null; // CONTROL:token-expiry
    if (out.iat > nowSec + CLOCK_SKEW_SECONDS) return null;
    return { lid: out.lid, iat: out.iat, exp: out.exp, cc: out.cc };
  } catch {
    return null;
  }
}

/** The Set-Cookie value for an unlock. No Domain attribute, as `__Host-` requires. */
export function unlockCookie(token: string, ttlSeconds: number): string {
  if (typeof token !== "string" || !COOKIE_VALUE_RE.test(token)) throw new Error("cookie value is not a token");
  if (!isInt(ttlSeconds) || ttlSeconds < 1 || ttlSeconds > MAX_TOKEN_AGE_SECONDS) throw new Error("cookie lifetime is out of range");
  return `${COOKIE_NAME}=${token}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=${ttlSeconds}`;
}

/** The unlock cookie's value from a Cookie header; null if absent, empty or duplicated. */
export function readUnlockCookie(header: string | null): string | null {
  if (typeof header !== "string" || header === "") return null;
  let found: string | null = null;
  let count = 0;
  for (const part of header.split(";")) {
    const p = part.trim();
    const i = p.indexOf("=");
    if (i <= 0 || p.slice(0, i) !== COOKIE_NAME) continue;
    count++;
    found = p.slice(i + 1);
  }
  return count === 1 && found !== null && found !== "" ? found : null;
}
