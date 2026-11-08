// SPDX-License-Identifier: Apache-2.0
//
// Owner alerts (issue #252): a short notice to a Discord webhook and/or a
// Matrix room when something the owner wants to know about happens. Email
// (Mailgun) is added later behind the same entry point.
//
// RULES.
// - An alert NEVER blocks or fails the visitor. `sendAlert` never throws and
//   `scheduleAlert` hands it to `ctx.waitUntil`, so the response does not wait.
// - Every alert is one unit against DAILY_ALERT_BUDGET (budgets.ts), reserved
//   before anything is sent. With no sink configured nothing is reserved, sent
//   or written. A refused reservation sends nothing.
// - The text is a fixed string, plus at most one number (the daily limit in the
//   budget warning). It never carries an address, a lead id, a hash, an IP, a
//   token, a country or any text that came from a request or from an upstream
//   response.
// - Secrets are read from env by NAME and used only as the request URL or the
//   Authorization header. They are never logged, stored or put in a payload.
//   Names: ALERT_DISCORD_WEBHOOK, ALERT_MATRIX_HOMESERVER,
//   ALERT_MATRIX_ROOM_ID, ALERT_MATRIX_ACCESS_TOKEN (docs/deploy.md).
// - Outgoing requests use redirect: "error" (a redirect would carry a secret to
//   a new place), a hard timeout, and a bounded read of the answer. The answer
//   is never parsed or logged; only the status class decides success.
// - A refused or failed alert logs a closed code through log.ts and nothing else.
//
// INFERRED (not verified against the vendors' pages in this change; check the
// first real delivery in the smoke test):
// - Discord: POST <webhook url> with a JSON body {"content": "...",
//   "allowed_mentions": {"parse": []}} answers 204 (or 200 with ?wait=true).
// - Matrix: PUT <homeserver>/_matrix/client/v3/rooms/<roomId>/send/m.room.message/<txnId>
//   with `Authorization: Bearer <token>` and {"msgtype": "m.text", "body": "..."}
//   answers 200 with an event id.

import { readBoundedBody } from "./body";
import { forRequest } from "./log";
import { reserveAlert, loadKitLimits, type KitLimitsEnv } from "./budgets";
import { reserveBudget, utcDay } from "./limits";
import { expectedHostname } from "./turnstile";

export type AlertKind = "lead_created" | "budget_warning";

export interface AlertEnv extends KitLimitsEnv {
  DB: D1Database;
  /** SECRET. https://discord.com/api/webhooks/<id>/<token>. */
  ALERT_DISCORD_WEBHOOK?: string;
  /** Matrix homeserver origin, https only, no port or path (a name, not a secret). */
  ALERT_MATRIX_HOMESERVER?: string;
  /** Matrix room id, `!opaque:server`. */
  ALERT_MATRIX_ROOM_ID?: string;
  /** SECRET. Access token of the account that posts. */
  ALERT_MATRIX_ACCESS_TOKEN?: string;
}

export interface AlertDeps {
  fetchFn?: typeof fetch;
  nowMs?: number;
  /** Test seam. Default 3000. */
  timeoutMs?: number;
}

export const ALERT_TIMEOUT_MS = 3000;
export const MAX_ALERT_RESPONSE_BYTES = 4096;
/** Counter key for the once-a-day budget warning. */
export const WARN_COUNTER_KEY = "alert-warn";

/** The only texts an alert can carry. */
export const ALERT_TEXT = {
  lead_created: "Reach Around: a new media kit lead was created.",
  budget_warning: (limit: number): string =>
    `Reach Around: the daily alert limit (${limit}) was reached. Further alerts are dropped until the next UTC day.`,
} as const;

const DISCORD_RE = /^https:\/\/(?:discord|discordapp)\.com\/api\/webhooks\/[0-9]{15,25}\/[A-Za-z0-9_-]{20,100}$/;
const MATRIX_ROOM_RE = /^![A-Za-z0-9._=+/-]{1,128}:[a-z0-9.-]{1,253}$/;
const MATRIX_TOKEN_RE = /^[A-Za-z0-9._~+/=-]{8,512}$/;

interface Sink {
  url: string;
  init: RequestInit;
}

function discordSink(env: AlertEnv, text: string): Sink | null {
  const url = env.ALERT_DISCORD_WEBHOOK;
  if (typeof url !== "string" || !DISCORD_RE.test(url)) return null;
  return {
    url,
    init: {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // allowed_mentions: no ping can come from this text, whatever it holds.
      body: JSON.stringify({ content: text, allowed_mentions: { parse: [] } }),
    },
  };
}

function matrixSink(env: AlertEnv, text: string): Sink | null {
  const hs = env.ALERT_MATRIX_HOMESERVER;
  const room = env.ALERT_MATRIX_ROOM_ID;
  const token = env.ALERT_MATRIX_ACCESS_TOKEN;
  if (typeof hs !== "string" || typeof room !== "string" || typeof token !== "string") return null;
  const m = /^https:\/\/([^/:?#@\s]+)$/.exec(hs);
  if (m === null) return null;
  const host = expectedHostname({}, { hostname: m[1] });
  if (host === null || !MATRIX_ROOM_RE.test(room) || !MATRIX_TOKEN_RE.test(token)) return null;
  const txn = Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
  return {
    url: `https://${host}/_matrix/client/v3/rooms/${encodeURIComponent(room)}/send/m.room.message/${txn}`,
    init: {
      method: "PUT",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ msgtype: "m.text", body: text }),
    },
  };
}

/** True when at least one sink is fully and validly configured. */
export function alertConfigured(env: AlertEnv): boolean {
  return discordSink(env, "x") !== null || matrixSink(env, "x") !== null;
}

/** One bounded POST or PUT. Resolves true on a 2xx; never throws; never reads the answer's content. */
async function deliver(sink: Sink, fetchFn: typeof fetch, timeoutMs: number): Promise<boolean> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const started = Date.now();
    const expired = new Promise<"timeout">((resolve) => {
      timer = setTimeout(() => resolve("timeout"), timeoutMs);
    });
    const res = await Promise.race([
      fetchFn(sink.url, { ...sink.init, redirect: "error", signal: AbortSignal.timeout(timeoutMs) }),
      expired,
    ]);
    if (res === "timeout") return false;
    const ok = res.status >= 200 && res.status < 300;
    // Bounded read so a hostile answer cannot hold memory; the content is dropped.
    if (res.body !== null) {
      const wrapped = new Request(sink.url, { method: "POST", body: res.body, duplex: "half" } as RequestInit);
      await readBoundedBody(wrapped, MAX_ALERT_RESPONSE_BYTES, { deadlineMs: Math.max(10, timeoutMs - (Date.now() - started)) });
    }
    return ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

async function dispatch(env: AlertEnv, text: string, deps: AlertDeps): Promise<boolean> {
  const fetchFn = deps.fetchFn ?? fetch;
  const timeoutMs = deps.timeoutMs ?? ALERT_TIMEOUT_MS;
  const sinks = [discordSink(env, text), matrixSink(env, text)].filter((s): s is Sink => s !== null);
  const results = await Promise.all(sinks.map((s) => deliver(s, fetchFn, timeoutMs)));
  return results.some((r) => r);
}

/**
 * Send one owner alert. Never throws. See the header for the rules.
 *
 * `lead_created` takes one unit of DAILY_ALERT_BUDGET. When the budget is
 * spent, the first refused alert of the UTC day instead sends ONE
 * `budget_warning` (its own once-a-day counter, so it costs one more D1 write
 * a day at most and is not itself limited by the exhausted budget); every later
 * alert that day is dropped silently.
 */
export async function sendAlert(env: AlertEnv, kind: AlertKind, deps: AlertDeps = {}): Promise<void> {
  const log = forRequest();
  try {
    if (kind !== "lead_created" && kind !== "budget_warning") return;
    if (!alertConfigured(env)) return;
    const now = deps.nowMs ?? Date.now();

    let text: string;
    if (kind === "lead_created") {
      if (await reserveAlert(env, now)) { // CONTROL:alert-budget (reserveAlert)
        text = ALERT_TEXT.lead_created;
      } else {
        // Budget spent (or unusable). One warning a day, then silence.
        let limit: number;
        try {
          limit = loadKitLimits(env).dailyAlertBudget;
        } catch {
          log.log("alert", "over_budget", "route_budget");
          return;
        }
        let first = false;
        try {
          first = await reserveBudget(env.DB, WARN_COUNTER_KEY, utcDay(now), 1);
        } catch {
          first = false;
        }
        if (!first) {
          log.log("alert", "over_budget", "route_budget");
          return;
        }
        text = ALERT_TEXT.budget_warning(limit);
      }
    } else {
      // A direct budget_warning call still spends the once-a-day counter.
      let limit: number;
      try {
        limit = loadKitLimits(env).dailyAlertBudget;
        if (!(await reserveBudget(env.DB, WARN_COUNTER_KEY, utcDay(now), 1))) {
          log.log("alert", "over_budget", "route_budget");
          return;
        }
      } catch {
        log.log("alert", "failed", "other");
        return;
      }
      text = ALERT_TEXT.budget_warning(limit);
    }

    const sent = await dispatch(env, text, deps);
    if (sent) log.log("alert", "ok");
    else log.log("alert", "failed", "provider");
  } catch {
    log.log("alert", "failed", "other");
  }
}

/**
 * Fire and forget: the visitor's response never waits for, or learns about,
 * an alert. Without an execution context nothing is sent (an un-awaited promise
 * can be cut off when the response returns).
 */
export function scheduleAlert(
  env: AlertEnv,
  ctx: { waitUntil(p: Promise<unknown>): void } | undefined,
  kind: AlertKind,
  deps: AlertDeps = {},
): void {
  try {
    if (ctx === undefined || typeof ctx.waitUntil !== "function") return;
    ctx.waitUntil(sendAlert(env, kind, deps));
  } catch {
    // An alert must never fail the request.
  }
}
