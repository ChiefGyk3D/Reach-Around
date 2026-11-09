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
// - Rate limit, daily budget and dedup are enforced in shared D1 state
//   (quota.ts, migration 0011), each one conditional statement, so isolates
//   cannot overshoot. Per channel, at most maxPerMinute messages a minute: the
//   last slot is the held-back notice, so the notice counts inside the limit.
//   The daily budget (DAILY_ALERT_BUDGET, default 20) counts alerts. The
//   in-isolate counters are only a cheap first filter in front of D1. If D1
//   fails, nothing is sent (fail closed, like every other kit budget).
//   The summary of held-back alerts rides on the next minute's first message and
//   is kept in isolate memory (best effort).
// - Identical events (same kind and address) inside DEDUP_MS send once.
// - Secrets come from env bindings by name (docs/alerts.md) at call time.

import { loadKitLimits, type KitLimitsEnv } from "../kit/budgets";
import { forRequest } from "../kit/log";
import { parseAlertsConfig, type AlertsConfig } from "./config";
import { createDiscordChannel } from "./discord";
import { createEmailChannel, EMAIL_PROVIDERS } from "./email";
import { formatAlert } from "./format";
import { DEFAULT_TIMEOUT_MS } from "./http";
import { createMatrixChannel } from "./matrix";
import { claimDedup, eventHash, MINUTE_MS, reserveQuota, siteKey, windowStart } from "./quota";
import { capMessage } from "./sanitize";
import type { AlertEvent, AlertMessage, Channel, ChannelId, FailureClass } from "./types";

/** Bindings the alerts read. SECRET ones are marked; set them in the secret manager, never in the repo. */
export interface AlertsEnv extends KitLimitsEnv {
  /** D1 binding that holds the alert quota and dedup tables (migration 0011). */
  DB: D1Database;
  /** The site host, used as the quota key. */
  SITE_HOST?: string;
  /** Plain variable: the alerts section of the site config as JSON. */
  ALERTS_CONFIG?: string;
  /** SECRET. https://discord.com/api/webhooks/<id>/<token>. */
  ALERT_DISCORD_WEBHOOK?: string;
  /**
   * SECRET. Matrix homeserver origin, https only. The homeserver and the access
   * token are ONE unit: whoever controls this name receives the token. It must
   * also be listed in alerts.matrix.allowed_hosts.
   */
  ALERT_MATRIX_SERVER?: string;
  /** DEPRECATED plain variable, read only when ALERT_MATRIX_SERVER is unset. Same allowed_hosts rule. */
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

export const WINDOW_MS = MINUTE_MS;
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
  /** Refused by the daily alert budget. */
  dailyRefused: number;
  /** Not sent because the shared quota state could not be used (fail closed). */
  quotaErrors: number;
  /** Matrix homeserver read from the deprecated plain variable. */
  deprecatedHomeserver: number;
}

interface Window {
  minute: number; // floor(now / 60000)
  attempts: number; // cheap local filter only; D1 decides
  held: number; // held back in the current window
  carry: number; // held back in earlier windows, to report on the next message
}

export function buildChannels(
  env: AlertsEnv,
  enabled: ReadonlySet<ChannelId>,
  matrixAllowedHosts: readonly string[],
): { channels: Channel[]; unconfigured: number; deprecated: boolean } {
  const channels: Channel[] = [];
  let unconfigured = 0;
  let deprecated = false;
  const add = (id: ChannelId, ch: Channel | null): void => {
    if (!enabled.has(id)) return;
    if (ch === null) unconfigured++;
    else channels.push(ch);
  };
  add("discord", createDiscordChannel(env.ALERT_DISCORD_WEBHOOK));
  const secretServer = typeof env.ALERT_MATRIX_SERVER === "string" && env.ALERT_MATRIX_SERVER !== "";
  const server = secretServer ? env.ALERT_MATRIX_SERVER : env.ALERT_MATRIX_HOMESERVER;
  const mx = createMatrixChannel(server, env.ALERT_MATRIX_ROOM_ID, env.ALERT_MATRIX_ACCESS_TOKEN, matrixAllowedHosts);
  if (mx !== null && !secretServer && enabled.has("matrix")) deprecated = true;
  add("matrix", mx);
  const name = typeof env.ALERT_EMAIL_PROVIDER === "string" ? env.ALERT_EMAIL_PROVIDER : "none";
  const factory = Object.prototype.hasOwnProperty.call(EMAIL_PROVIDERS, name) ? EMAIL_PROVIDERS[name] : undefined;
  const provider = factory ? factory(env as unknown as Record<string, unknown>) : null;
  add("email", createEmailChannel(provider, env.ALERT_EMAIL_TO));
  return { channels, unconfigured, deprecated };
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
  const status: AlertStatus = {
    channels: {},
    deduped: 0,
    unconfigured: 0,
    dailyRefused: 0,
    quotaErrors: 0,
    deprecatedHomeserver: 0,
  };

  const chStatus = (id: ChannelId): ChannelStatus => (status.channels[id] ??= { sent: 0, failed: {}, suppressed: 0 });

  function who(event: AlertEvent): string {
    return event.kind !== "gate_failed_repeatedly" && typeof event.email === "string" ? event.email : "";
  }

  /** First filter only: a repeat seen by THIS isolate. Never evicts; when full, D1 alone decides. */
  function localDuplicate(key: string, now: number): boolean {
    for (const [k, t] of seen) if (now - t >= DEDUP_MS || now < t) seen.delete(k);
    if (seen.has(key)) return true;
    if (seen.size < DEDUP_MAX_ENTRIES) seen.set(key, now);
    return false;
  }

  function localWindow(id: ChannelId, now: number): Window {
    const minute = Math.floor(now / MINUTE_MS);
    let w = windows.get(id);
    if (w === undefined || w.minute !== minute) {
      w = { minute, attempts: 0, held: 0, carry: (w?.held ?? 0) + (w?.carry ?? 0) };
      windows.set(id, w);
    }
    return w;
  }

  async function notify(env: AlertsEnv, event: AlertEvent, deps: DispatchDeps = {}): Promise<void> {
    const log = forRequest();
    try {
      const cfg = parseAlertsConfig(env?.ALERTS_CONFIG);
      if (cfg === null || !cfg.events.has(event?.kind)) return;
      const now = deps.nowMs ?? Date.now();
      const site = siteKey(env.SITE_HOST);
      const hash = await eventHash(site, event.kind, who(event));
      if (localDuplicate(hash, now)) {
        status.deduped++;
        return;
      }

      // Shared state: dedup claim, then the daily budget. Any failure sends nothing.
      let dailyBudget: number;
      try {
        dailyBudget = loadKitLimits(env).dailyAlertBudget;
        if (!(await claimDedup(env.DB, site, hash, now, DEDUP_MS))) {
          status.deduped++;
          return;
        }
        if ((await reserveQuota(env.DB, site, "all", "day", windowStart("day", now), dailyBudget)) === null) {
          status.dailyRefused++;
          log.log("alert", "over_budget", "route_budget");
          return;
        }
      } catch {
        status.quotaErrors++;
        log.log("alert", "failed", "other");
        return;
      }

      const { channels, unconfigured, deprecated } = buildChannels(env, cfg.channels, cfg.matrixAllowedHosts);
      status.unconfigured += unconfigured;
      if (deprecated) status.deprecatedHomeserver++;
      const sendDeps = { fetchFn: deps.fetchFn ?? fetch, timeoutMs: deps.timeoutMs ?? DEFAULT_TIMEOUT_MS };

      await Promise.all(
        channels.map(async (ch) => {
          try {
            const w = localWindow(ch.id, now);
            const limit = cfg.maxPerMinute;
            // Cheap filter: this isolate already used the whole minute.
            if (w.attempts >= limit) {
              w.held++;
              chStatus(ch.id).suppressed++;
              log.log("alert", "throttled", "route_budget");
              return;
            }
            w.attempts++;
            let count: number | null;
            try {
              count = await reserveQuota(env.DB, site, ch.id, "minute", windowStart("minute", now), limit);
            } catch {
              status.quotaErrors++;
              log.log("alert", "failed", "other");
              return;
            }
            if (count === null) {
              w.held++;
              chStatus(ch.id).suppressed++;
              log.log("alert", "throttled", "route_budget");
              return;
            }
            // The last slot of the minute carries the held-back notice instead of the event (limit 1: the event).
            const notice = limit > 1 && count >= limit;
            const base = formatAlert(event, cfg.visitorEmail, ch.textMode);
            let summary = "";
            if (!notice && w.carry > 0) {
              summary = ` (${w.carry} earlier alert${w.carry === 1 ? " was" : "s were"} held back by the rate limit.)`;
              w.carry = 0;
            }
            if (notice) {
              w.held++;
              chStatus(ch.id).suppressed++;
            }
            const message = withinCap(
              notice
                ? {
                    subject: base.subject,
                    text: "Reach Around: more alerts are arriving than the per-minute limit; further ones are held back and counted.",
                  }
                : { subject: base.subject, text: base.text + summary },
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
