// SPDX-License-Identifier: Apache-2.0
//
// BEACON_MODE, kept in a file of its own so the deploy-time budget check can read it without the routes.

/**
 * Whether BEACON_MODE may be `enforce`. It is NOT yet: a token proves only that the page asked the Worker
 * for one, and nothing yet limits who may ask or how often one token may be used. Enforce is switched on
 * only after the issuance gate and the per-token limit have landed (later slices of #610). Until then
 * `enforce` is an unusable value: clicks and views are refused (the same empty 204) and the health flag
 * is set, exactly as for any other unusable setting. Observe only records the lane; it changes nothing.
 */
export const ENFORCE_AVAILABLE = false;

export type BeaconMode = "off" | "observe" | "enforce";

/** BEACON_MODE: unset or empty is off; the exact words off and observe are usable; anything else is null (unusable). */
export function parseBeaconMode(v: unknown): BeaconMode | null {
  if (v === undefined || v === "") return "off";
  if (v === "off" || v === "observe") return v;
  return v === "enforce" && ENFORCE_AVAILABLE ? v : null;
}
