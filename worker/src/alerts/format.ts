// SPDX-License-Identifier: Apache-2.0
//
// Alert text. Fixed wording plus: a UTC time, a count (an integer clamped to a
// range), and at most one visitor field, the address, which is neutralized and
// shown according to the owner's visitor_email setting. Never an IP, user
// agent, cookie, token, country or hash: the event type has no field for them.

import { capMessage, MAX_FIELD_CHARS, neutralize, type TextMode } from "./sanitize";
import { HEALTH_CONDITIONS, type AlertEvent, type AlertMessage, type HealthCondition } from "./types";

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

// Only a normalized address is shown (lowercase ASCII, the gate's grammar in short form). Anything else,
// including free text that merely contains an "@", is shown as "a visitor".
const EMAIL_RE = /^[a-z0-9_+-]+(?:\.[a-z0-9_+-]+)*@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;

function who(email: unknown, mode: VisitorEmailMode, text: TextMode): string {
  if (mode === "none" || typeof email !== "string" || email.length > 254 || !EMAIL_RE.test(email)) return "a visitor";
  const shown = mode === "masked" ? maskEmail(email) : email;
  const clean = neutralize(shown, MAX_FIELD_CHARS, text);
  return clean === "" ? "a visitor" : clean;
}

function clampCount(n: unknown): number {
  return typeof n === "number" && Number.isFinite(n) ? Math.min(Math.max(Math.trunc(n), 0), 99_999) : 0;
}

/** Fixed wording for each health condition: [firing phrase, normal phrase]. The count (hours or days) is appended only where shown. */
const HEALTH_WORDS: Record<HealthCondition, { firing: string; ok: string; unit: string }> = {
  rollup_overdue: {
    firing: "the daily rollup has not finished in over 3 hours",
    ok: "the daily rollup is running again",
    unit: "hours",
  },
  rollup_stale: {
    firing: "the rollup checkpoint is more than 2 days behind",
    ok: "the rollup checkpoint has caught up",
    unit: "days",
  },
  zero_views: {
    firing: "no page views were counted in the last 24 hours on a site that had views the day before",
    ok: "page views are being counted again",
    unit: "",
  },
  view_budget_hit: {
    firing: "the hourly page-view budget was reached and further views were dropped",
    ok: "the page-view budget has room again",
    unit: "",
  },
  alert_budget_near: {
    firing: "today's alert budget is almost used up",
    ok: "the alert budget has room again",
    unit: "",
  },
};

function formatHealth(event: Extract<AlertEvent, { kind: "analytics_health" }>): AlertMessage {
  // Closed vocabulary: anything else is shown as a fixed word, never echoed.
  const known = (HEALTH_CONDITIONS as readonly unknown[]).includes(event.condition);
  const words = known ? HEALTH_WORDS[event.condition] : undefined;
  const when = utcStamp(event.atMs);
  if (words === undefined) {
    return {
      subject: "Reach Around: analytics health",
      text: capMessage(`Reach Around health: an unknown condition changed (as of ${when}).`),
    };
  }
  if (event.state === "cleared") {
    return {
      subject: "Reach Around: analytics health recovered",
      text: capMessage(`Reach Around health: ${words.ok} (as of ${when}).`),
    };
  }
  const n = clampCount(event.count);
  const size = words.unit !== "" && n > 0 ? ` (about ${n} ${words.unit})` : "";
  return {
    subject: "Reach Around: analytics health",
    text: capMessage(`Reach Around health: ${words.firing}${size} (as of ${when}). See the failure-mode runbook.`),
  };
}

export function formatAlert(event: AlertEvent, visitorEmail: VisitorEmailMode, text: TextMode): AlertMessage {
  switch (event.kind) {
    case "analytics_health":
      return formatHealth(event);
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
