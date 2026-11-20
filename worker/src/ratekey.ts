// SPDX-License-Identifier: Apache-2.0
//
// Keys for the Workers Rate Limiting bindings. The raw client address never
// reaches a binding.
//
// A limiter key must NOT rotate at midnight UTC: a daily-rotating key hands
// every client a fresh allowance at the boundary. So these keys come from a
// STABLE key: HMAC(VIEW_HASH_SECRET, "RA-LIM1") with no date in it. A purpose
// label ("lim", "sus", "clk") is mixed in per use, so the values cannot be
// matched to one another. The stable key is a different derivation from the
// daily unique-visitor key ("RA-UV1|<date>"), so a limiter key never equals a
// stored value (the visitor hash, the per-client cap hash).
//
// There is no key without a usable secret: both routes refuse the request
// instead (a random per-isolate key would make the allowance per isolate,
// not per client).

export const MIN_SECRET_BYTES = 32;

export function secretBytes(secret: string | undefined): Uint8Array | null {
  if (typeof secret !== "string" || secret.length === 0 || secret.length > 4096) return null;
  try {
    const bin = atob(secret);
    if (bin.length < MIN_SECRET_BYTES) return null;
    return Uint8Array.from(bin, (c) => c.charCodeAt(0));
  } catch {
    return null;
  }
}

const HMAC = { name: "HMAC", hash: "SHA-256" } as const;

let stable: { secret: string; key: CryptoKey } | null = null;

async function stableKey(secret: Uint8Array, secretText: string): Promise<CryptoKey> {
  if (stable && stable.secret === secretText) return stable.key;
  const root = await crypto.subtle.importKey("raw", secret, HMAC, false, ["sign"]);
  const derived = await crypto.subtle.sign("HMAC", root, new TextEncoder().encode("RA-LIM1"));
  const key = await crypto.subtle.importKey("raw", derived, HMAC, false, ["sign"]);
  stable = { secret: secretText, key };
  return key;
}

async function tag(key: CryptoKey, purpose: string, address: string): Promise<string> {
  const sig = new Uint8Array(
    await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${purpose}|${address}`)),
  );
  let hex = "";
  for (const b of sig.slice(0, 16)) hex += b.toString(16).padStart(2, "0");
  return hex;
}

/** 128-bit hex limiter key from the stable secret-derived key (no day component). */
export async function limiterKey(
  secret: Uint8Array,
  secretText: string,
  purpose: string,
  address: string,
): Promise<string> {
  return tag(await stableKey(secret, secretText), purpose, address);
}
