// SPDX-License-Identifier: Apache-2.0
//
// The coarse network a request came from, for partitioning an allowance: a /24 for IPv4 and a /48 for
// IPv6, taken from the canonical text `normalizeAddress` gives. The same cut the beacon token work uses
// (worker/src/beacon.ts on the beacon branch); kept here so the kit does not depend on that branch.
// The result is only ever fed to a keyed hash (ratekey.ts `limiterKey`, with its own domain-separation
// label), never stored or logged.

/** `v4:a.b.c` or `v6:<12 hex>`; null for anything else (including a missing address). */
export function coarseNetwork(normalized: string | null): string | null {
  if (normalized === null) return null;
  if (normalized.startsWith("v6:")) {
    const hex = normalized.slice(3);
    return /^[0-9a-f]{16}$/.test(hex) ? `v6:${hex.slice(0, 12)}` : null;
  }
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.\d{1,3}$/.exec(normalized);
  return m ? `v4:${m[1]}.${m[2]}.${m[3]}` : null;
}
