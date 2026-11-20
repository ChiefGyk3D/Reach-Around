// SPDX-License-Identifier: Apache-2.0
//
// Ids for an unlock that stored no lead (the lead cap or the daily new-lead cap was reached).
//
// The cookie must look the same whether or not the address was stored, and the token's payload is
// readable by the visitor, so the id of an unstored unlock is 32 lowercase hex characters like a real
// lead id. What marks it is a keyed tag: the second half is the first 8 bytes of
// HMAC-SHA-256(key, "unstored:" + first half). Only the Worker can compute it, and a real lead id
// (128 random bits) matches by chance with probability 2^-64.

const enc = new TextEncoder();
const HEX32 = /^[0-9a-f]{32}$/;

function hex(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += b.toString(16).padStart(2, "0");
  return s;
}

async function tag(key: string, half: string): Promise<string> {
  const k = await crypto.subtle.importKey("raw", enc.encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", k, enc.encode(`unstored:${half}`)));
  return hex(mac.slice(0, 8));
}

/** A fresh id for an unlock that stored nothing. */
export async function mintUnstoredId(key: string): Promise<string> {
  const half = hex(crypto.getRandomValues(new Uint8Array(8)));
  return half + (await tag(key, half));
}

/** True when `id` was minted by mintUnstoredId under one of `keys` (current first, then previous). */
export async function isUnstoredId(keys: readonly string[], id: string): Promise<boolean> {
  if (!HEX32.test(id)) return false;
  const half = id.slice(0, 16);
  const want = id.slice(16);
  let found = false;
  for (const k of keys) {
    if ((await tag(k, half)) === want) found = true; // every key is tried, so the work does not depend on which matched
  }
  return found;
}
