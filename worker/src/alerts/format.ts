// SPDX-License-Identifier: Apache-2.0
//
// Alert text. Fixed wording plus: a UTC time, a count (an integer clamped to a
// range), and at most one visitor field, the address, which is neutralized and
// shown according to the owner's visitor_email setting. Never an IP, user
// agent, cookie, token, country or hash: the event type has no field for them.

import { capMessage, MAX_FIELD_CHARS, neutralize, type TextMode } from "./sanitize";
import type { AlertEvent, AlertMessage } from "./types";

export type VisitorEmailMode = "full" | "masked" | "none";

function utcStamp(ms: number): string {
  const d = new Date(Number.isFinite(ms) ? ms : Number.NaN);
  if (Number.isNaN(d.getTime())) return "an unknown time";
  return `${d.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

/** "partner@example.com" -> "p***@example.com". Masking runs on the raw value, then neutralizes. */
function maskEmail(email: string): string {
  const at = email.lastIndexOf("@");
  if (at < 1) return "***";
  return `${email[0]}***${email.slice(at)}`;
}

function who(email: unknown, mode: VisitorEmailMode, text: TextMode): string {
  if (mode === "none" || typeof email !== "string") return "a visitor";
  const shown = mode === "masked" ? maskEmail(email) : email;
  const clean = neutralize(shown, MAX_FIELD_CHARS, text);
  return clean === "" ? "a visitor" : clean;
}

function clampCount(n: unknown): number {
  return typeof n === "number" && Number.isFinite(n) ? Math.min(Math.max(Math.trunc(n), 0), 99_999) : 0;
}

export function formatAlert(event: AlertEvent, visitorEmail: VisitorEmailMode, text: TextMode): AlertMessage {
  switch (event.kind) {
    case "kit_unlocked":
      return {
        subject: "Reach Around: media kit unlocked",
        text: capMessage(
          `Reach Around: the media kit was unlocked by ${who(event.email, visitorEmail, text)} at ${utcStamp(event.atMs)}.`,
        ),
      };
    case "lead_captured":
      return {
        subject: "Reach Around: new media kit lead",
        text: capMessage(
          `Reach Around: new media kit lead ${who(event.email, visitorEmail, text)} at ${utcStamp(event.atMs)}.`,
        ),
      };
    case "gate_failed_repeatedly":
      return {
        subject: "Reach Around: media kit gate failing",
        text: capMessage(
          `Reach Around: the media kit gate failed ${clampCount(event.count)} times in a short span (as of ${utcStamp(event.atMs)}). Check the gate.`,
        ),
      };
  }
}
