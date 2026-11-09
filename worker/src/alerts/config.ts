// SPDX-License-Identifier: Apache-2.0
//
// The alerts section of the site config, as the Worker receives it: one JSON
// value in the plain (non-secret) variable ALERTS_CONFIG. Anything off, unknown
// or malformed means "no alerts" (fail closed, default off). Names only; no
// secret is ever part of it.

import type { VisitorEmailMode } from "./format";
import { CHANNEL_IDS, EVENT_KINDS, type AlertEventKind, type ChannelId } from "./types";

export interface AlertsConfig {
  channels: ReadonlySet<ChannelId>;
  events: ReadonlySet<AlertEventKind>;
  visitorEmail: VisitorEmailMode;
  /** Alerts one channel may send per minute before the rest are held back. */
  maxPerMinute: number;
}

export const DEFAULT_MAX_PER_MINUTE = 5;
export const MAX_PER_MINUTE_CEILING = 30;
const KEYS = new Set(["channels", "events", "visitor_email", "max_per_minute"]);

function closedList<T extends string>(v: unknown, allowed: readonly T[]): Set<T> | null {
  if (!Array.isArray(v) || v.length > allowed.length) return null;
  const out = new Set<T>();
  for (const item of v) {
    if (typeof item !== "string" || !(allowed as readonly string[]).includes(item)) return null;
    out.add(item as T);
  }
  return out;
}

/** Parse ALERTS_CONFIG. Returns null (alerts off) for unset, malformed, unknown-key or empty-effect config. */
export function parseAlertsConfig(raw: unknown): AlertsConfig | null {
  if (typeof raw !== "string" || raw === "" || raw.length > 1000) return null;
  let v: unknown;
  try {
    v = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof v !== "object" || v === null || Array.isArray(v)) return null;
  const o = v as Record<string, unknown>;
  if (Object.keys(o).some((k) => !KEYS.has(k))) return null;
  const channels = closedList(o.channels ?? [], CHANNEL_IDS);
  const events = closedList(o.events ?? [], EVENT_KINDS);
  if (channels === null || events === null || channels.size === 0 || events.size === 0) return null;
  const ve = o.visitor_email ?? "full";
  if (ve !== "full" && ve !== "masked" && ve !== "none") return null;
  const mpm = o.max_per_minute ?? DEFAULT_MAX_PER_MINUTE;
  if (typeof mpm !== "number" || !Number.isInteger(mpm) || mpm < 1 || mpm > MAX_PER_MINUTE_CEILING) return null;
  return { channels, events, visitorEmail: ve, maxPerMinute: mpm };
}
