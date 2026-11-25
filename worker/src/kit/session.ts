// SPDX-License-Identifier: Apache-2.0
//
// The one session check for the gated kit routes (GET /k/view and POST /k/inquiry).
//
// A cookie that verifies (signature, expiry) is not enough: the session must also still be current.
// A stored lead must still exist and the cookie must have been issued after the lead's floor; an
// unstored unlock is held to the one kit-wide floor. The floors are in MILLISECONDS, the token's iat
// is whole seconds, so both are compared in whole seconds and the check fails closed: only a cookie
// issued in a second strictly after the floor's second passes.

import { getLead, getUnstoredSessionFloor } from "./leads";
import { isUnstoredId } from "./unstored";
import type { TokenClaims } from "./token";

export type SessionVerdict = "ok" | "expired" | "error";

/**
 * Decide whether verified token claims still open the gated routes. "expired" is a lead that is gone
 * or a cookie at or below the applicable floor; "error" is a storage read that failed or a stored floor that is malformed (callers refuse).
 */
export async function authorizeSession(db: D1Database, keys: readonly string[], claims: TokenClaims): Promise<SessionVerdict> {
  let floorMs: number | null;
  try {
    if (await isUnstoredId(keys, claims.lid)) {
      floorMs = await getUnstoredSessionFloor(db); // CONTROL:unstored-floor
    } else {
      const lead = await getLead(db, claims.lid); // CONTROL:lead-exists
      if (lead === null) return "expired";
      floorMs = lead.session_floor_ts;
    }
  } catch {
    return "error";
  }
  if (floorMs === null) return "ok";
  // The columns are STRICT integers with a CHECK, but a value that is not a non-negative safe integer
  // (text, NaN, a fraction, a negative) must never open the session: NaN makes every comparison false.
  // Fail closed as a storage error (callers refuse).
  if (typeof floorMs !== "number" || !Number.isSafeInteger(floorMs) || floorMs < 0) return "error"; // CONTROL:session-floor-shape
  if (claims.iat <= Math.floor(floorMs / 1000)) return "expired"; // CONTROL:session-floor
  return "ok";
}
