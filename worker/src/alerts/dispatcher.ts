// SPDX-License-Identifier: Apache-2.0
//
// Owner alerts (issue #252): fan one AlertEvent out to the channels the site
// config enables.
//
// RULES.
// - Default off. No ALERTS_CONFIG, a bad one, or an event the owner did not
//   enable sends nothing and touches no network.
// - Nothing here throws into the caller. A channel that fails or times out
//   never blocks the others; the visitor never waits (scheduleAlert uses
//   ctx.waitUntil).
// - Status counters hold counts by channel and closed failure class, never
//   message content, addresses, URLs or secrets. Logging goes through the kit
//   logger (closed enums only).
// - Per channel, at most maxPerMinute messages a minute. The first message
//   held back in a window sends one notice; the next window's first message
//   carries the count. The window state lives in the Worker isolate's memory,
//   so the limit is per isolate: a best-effort flood brake, not an exact quota.
//   (The kit's D1 DAILY_ALERT_BUDGET, kit/budgets.ts, is the daily ceiling.)
// - Identical events (same kind and address) inside DEDUP_MS send once.
// - Secrets come from env bindings by name (docs/alerts.md) at call time.

import { forRequest } from "../kit/log";
import { parseAlertsConfig, type AlertsConfig } from "./config";
import { createDiscordChannel } from "./discord";
import { createEmailChannel, EMAIL_PROVIDERS } from "./email";
import { formatAlert } from "./format";
import { DEFAULT_TIMEOUT_MS } from "./http";
import { createMatrixChannel } from "./matrix";
import { capMessage } from "./sanitize";
import type { AlertEvent, AlertMessage, Channel, ChannelId, FailureClass } from "./types";

/** Bindings the alerts read. SECRET ones are marked; set them in the secret manager, never in the repo. */
export interface AlertsEnv {
  /** Plain variable: the alerts section of the site config as JSON. */
  ALERTS_CONFIG?: string;
  /** SECRET. https://discord.com/api/webhooks/<id>/<token>. */
  ALERT_DISCORD_WEBHOOK?: string;
  /** Plain variable. Matrix homeserver origin, https only. */
  ALERT_MATRIX_HOMESERVER?: string;
  /** Plain variable. Matrix room id. */
  ALERT_MATRIX_ROOM_ID?: string;
  /** SECRET. Access token of the account that posts. */
  ALERT_MATRIX_ACCESS_TOKEN?: string;
  /** Plain variable. Recipient of the email channel. */
  ALERT_EMAIL_TO?: string;
  /** Plain variable. Email provider name; unset or "none" disables email. */
  ALERT_EMAIL_PROVIDER?: string;
}

export interface DispatchDeps {
  fetchFn?: typeof fetch;
  nowMs?: number;
  timeoutMs?: number;
}

export const WINDOW_MS = 60_000;
export const DEDUP_MS = 10 * 60_000;
const DEDUP_MAX_ENTRIES = 200;
/** Hard ceiling on the bytes of one outgoing message body. */
export const MAX_PAYLOAD_BYTES = 2000;

export interface ChannelStatus {
  sent: number;
  failed: Partial<Record<FailureClass, number>>;
  /** Held back by the per-minute limit. */
  suppressed: number;
}

export interface AlertStatus {
  channels: Partial<Record<ChannelId, ChannelStatus>>;
  /** Dropped as a repeat of a recent identical event. */
  deduped: number;
  /** Enabled in config but not fully configured (a missing or invalid binding). */
  unconfigured: number;
}

interface Window {
  start: number;
  count: number;
  held: number; // held back in the current window
  carry: number; // held back in earlier windows, to report on the next message
  noticed: boolean;
}

export function buildChannels(
  env: AlertsEnv,
  enabled: ReadonlySet<ChannelId>,
): { channels: Channel[]; unconfigured: number } {
  const channels: Channel[] = [];
  let unconfigured = 0;
  const add = (id: ChannelId, ch: Channel | null): void => {
    if (!enabled.has(id)) return;
    if (ch === null) unconfigured++;
    else channels.push(ch);
  };
  add("discord", createDiscordChannel(env.ALERT_DISCORD_WEBHOOK));
  add(
    "matrix",
    createMatrixChannel(env.ALERT_MATRIX_HOMESERVER, env.ALERT_MATRIX_ROOM_ID, env.ALERT_MATRIX_ACCESS_TOKEN),
  );
  const name = typeof env.ALERT_EMAIL_PROVIDER === "string" ? env.ALERT_EMAIL_PROVIDER : "none";
  const factory = Object.prototype.hasOwnProperty.call(EMAIL_PROVIDERS, name) ? EMAIL_PROVIDERS[name] : undefined;
  const provider = factory ? factory(env as unknown as Record<string, unknown>) : null;
  add("email", createEmailChannel(provider, env.ALERT_EMAIL_TO));
  return { channels, unconfigured };
}

function bodyBytes(m: AlertMessage): number {
  return new TextEncoder().encode(JSON.stringify(m)).length;
}

function withinCap(message: AlertMessage): AlertMessage {
  let m = message;
  while (bodyBytes(m) > MAX_PAYLOAD_BYTES && m.text.length > 20) {
    m = { ...m, text: capMessage(m.text, Math.floor(m.text.length / 2)) };
  }
  return bodyBytes(m) > MAX_PAYLOAD_BYTES
    ? { subject: "Reach Around alert", text: "Reach Around: an alert was held back (too large)." }
    : m;
}

export function createDispatcher() {
  const windows = new Map<ChannelId, Window>();
  const seen = new Map<string, number>();
  const status: AlertStatus = { channels: {}, deduped: 0, unconfigured: 0 };

  const chStatus = (id: ChannelId): ChannelStatus => (status.channels[id] ??= { sent: 0, failed: {}, suppressed: 0 });

  function isDuplicate(event: AlertEvent, now: number): boolean {
    const who =
      event.kind !== "gate_failed_repeatedly" && typeof event.email === "string"
        ? event.email.toLowerCase().slice(0, 254)
        : "";
    const key = `${event.kind}|${who}`;
    for (const [k, t] of seen) if (now - t >= DEDUP_MS || now < t) seen.delete(k);
    if (seen.has(key)) return true;
    if (seen.size >= DEDUP_MAX_ENTRIES) seen.delete(seen.keys().next().value as string);
    seen.set(key, now);
    return false;
  }

  /** May this channel send now? `notice` means send the one held-back notice instead of the event. */
  function admit(id: ChannelId, cfg: AlertsConfig, now: number): { go: boolean; summary: string; notice: boolean } {
    let w = windows.get(id);
    if (w === undefined || now - w.start >= WINDOW_MS || now < w.start) {
      const carry = (w?.held ?? 0) + (w?.carry ?? 0);
      w = { start: now, count: 0, held: 0, carry, noticed: false };
      windows.set(id, w);
    }
    if (w.count < cfg.maxPerMinute) {
      w.count++;
      let summary = "";
      if (w.carry > 0) {
        summary = ` (${w.carry} earlier alert${w.carry === 1 ? " was" : "s were"} held back by the rate limit.)`;
        w.carry = 0;
      }
      return { go: true, summary, notice: false };
    }
    w.held++;
    chStatus(id).suppressed++;
    if (!w.noticed) {
      w.noticed = true;
      return { go: true, summary: "", notice: true };
    }
    return { go: false, summary: "", notice: false };
  }

  async function notify(env: AlertsEnv, event: AlertEvent, deps: DispatchDeps = {}): Promise<void> {
    const log = forRequest();
    try {
      const cfg = parseAlertsConfig(env?.ALERTS_CONFIG);
      if (cfg === null || !cfg.events.has(event?.kind)) return;
      const now = deps.nowMs ?? Date.now();
      if (isDuplicate(event, now)) {
        status.deduped++;
        return;
      }
      const { channels, unconfigured } = buildChannels(env, cfg.channels);
      status.unconfigured += unconfigured;
      const sendDeps = { fetchFn: deps.fetchFn ?? fetch, timeoutMs: deps.timeoutMs ?? DEFAULT_TIMEOUT_MS };

      await Promise.all(
        channels.map(async (ch) => {
          try {
            const gate = admit(ch.id, cfg, now);
            if (!gate.go) {
              log.log("alert", "throttled", "route_budget");
              return;
            }
            const base = formatAlert(event, cfg.visitorEmail, ch.textMode);
            const message = withinCap(
              gate.notice
                ? {
                    subject: base.subject,
                    text: "Reach Around: more alerts are arriving than the per-minute limit; further ones are held back and counted.",
                  }
                : { subject: base.subject, text: base.text + gate.summary },
            );
            const res = await ch.send(message, sendDeps);
            const s = chStatus(ch.id);
            if (res.ok) {
              s.sent++;
              log.log("alert", "ok");
            } else {
              s.failed[res.failure] = (s.failed[res.failure] ?? 0) + 1;
              log.log("alert", "failed", "provider");
            }
          } catch {
            const s = chStatus(ch.id);
            s.failed.network = (s.failed.network ?? 0) + 1;
            log.log("alert", "failed", "other");
          }
        }),
      );
    } catch {
      log.log("alert", "failed", "other");
    }
  }

  return {
    notify,
    /** A copy of the counters. Counts only. */
    status: (): AlertStatus => JSON.parse(JSON.stringify(status)) as AlertStatus,
  };
}

const shared = createDispatcher();

/** Send an owner alert now (await it in tests). Never throws. */
export const notifyOwner = shared.notify;
export const alertStatus = shared.status;

/**
 * Fire and forget: the visitor's response never waits for, or learns about,
 * an alert. Without an execution context nothing is sent (an un-awaited
 * promise can be cut off when the response returns).
 */
export function scheduleAlert(
  env: AlertsEnv,
  ctx: { waitUntil(p: Promise<unknown>): void } | undefined,
  event: AlertEvent,
  deps: DispatchDeps = {},
): void {
  try {
    if (ctx === undefined || typeof ctx.waitUntil !== "function") return;
    ctx.waitUntil(shared.notify(env, event, deps));
  } catch {
    // An alert must never fail the request.
  }
}
