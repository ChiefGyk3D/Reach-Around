// SPDX-License-Identifier: Apache-2.0
//
// The owner-alert contract (issue #252). Everything the Worker can tell the
// owner is an AlertEvent; every place it can tell them is a Channel. Callers
// (the kit gate, the brand inquiry form, health checks) build an event and hand
// it to the dispatcher; they never see a channel, a URL or a secret.

/** Events the site config can switch on. Closed set. */
export type AlertEventKind = "kit_unlocked" | "lead_captured" | "gate_failed_repeatedly";

export const EVENT_KINDS: readonly AlertEventKind[] = ["kit_unlocked", "lead_captured", "gate_failed_repeatedly"];

/** Channels the site config can switch on. A new channel (Slack, Teams) is one id here plus one adapter. */
export type ChannelId = "discord" | "matrix" | "email";

export const CHANNEL_IDS: readonly ChannelId[] = ["discord", "matrix", "email"];

/**
 * The event contract. `email` is the visitor's normalized address and is the
 * ONLY visitor-derived field an event may carry. Nothing else about a visitor
 * (IP, user agent, cookie, token, country) has a field here, so it cannot be
 * passed by accident.
 */
export type AlertEvent =
  | { kind: "kit_unlocked"; atMs: number; email: string }
  | { kind: "lead_captured"; atMs: number; email: string }
  | { kind: "gate_failed_repeatedly"; atMs: number; count: number };

/** What a channel is asked to deliver: one short plain-text message. */
export interface AlertMessage {
  /** Already sanitized, single line or a few lines, under the hard cap. */
  text: string;
  /** Short subject for channels that have one (email). */
  subject: string;
}

/** Closed failure classes. Counted, logged as a class only, never with content. */
export type FailureClass =
  | "invalid_config"
  | "rejected" // 4xx
  | "server" // 5xx
  | "timeout"
  | "redirect"
  | "network"
  | "unavailable"; // provider disabled / no-op

export type ChannelResult = { ok: true } | { ok: false; failure: FailureClass };

export interface SendDeps {
  fetchFn: typeof fetch;
  timeoutMs: number;
}

/**
 * One delivery route. `send` returns a result and MUST NOT throw into the
 * request path; the dispatcher wraps it anyway.
 */
export interface Channel {
  readonly id: ChannelId;
  /** "markdown" when the destination renders markdown (Discord), else "plain". */
  readonly textMode: "markdown" | "plain";
  send(message: AlertMessage, deps: SendDeps): Promise<ChannelResult>;
}
