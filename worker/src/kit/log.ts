// SPDX-License-Identifier: Apache-2.0
/**
 * Bounded structured logging for the kit. A line is the JSON of closed-enum
 * fields and at most one small counter. Nothing derived from a request (an
 * address, token, IP, user agent, path, or error message) can reach a line:
 * every value is looked up in a fixed set, and anything else becomes a fixed
 * fallback. Volume is capped per request and per minute.
 *
 * This is the only module in the kit allowed to call `console`.
 */

export type LogEvent = "gate" | "send" | "link" | "view" | "alert" | "purge" | "budget" | "kill" | "log";
export type Outcome = "ok" | "rejected" | "throttled" | "over_budget" | "closed" | "failed" | "dropped";
export type Reason =
  | "origin"
  | "bot_ua"
  | "size"
  | "turnstile"
  | "email"
  | "ip_minute"
  | "route_budget"
  | "address_day"
  | "mail_budget"
  | "binding"
  | "single_use"
  | "expired"
  | "lead_cap"
  | "kill"
  | "provider"
  | "other";

const EVENTS: ReadonlySet<string> = new Set<LogEvent>(["gate", "send", "link", "view", "alert", "purge", "budget", "kill", "log"]);
const OUTCOMES: ReadonlySet<string> = new Set<Outcome>(["ok", "rejected", "throttled", "over_budget", "closed", "failed", "dropped"]);
const REASONS: ReadonlySet<string> = new Set<Reason>([
  "origin",
  "bot_ua",
  "size",
  "turnstile",
  "email",
  "ip_minute",
  "route_budget",
  "address_day",
  "mail_budget",
  "binding",
  "single_use",
  "expired",
  "lead_cap",
  "kill",
  "provider",
  "other",
]);
/** Events that may carry the counter `n`. */
const COUNTED: ReadonlySet<string> = new Set(["purge", "log"]);
/** Events emitted at most once per minute, however often they are logged. */
const ONCE_PER_MINUTE: ReadonlySet<string> = new Set(["budget", "kill"]);

export const DEFAULT_LOG_MAX_PER_MINUTE = 60;
export const DEFAULT_LOG_MAX_PER_REQUEST = 4;
const MAX_PER_MINUTE_CEILING = 600;
const WINDOW_MS = 60_000;
const MAX_COUNTER = 1_000_000_000;

function clamp(v: unknown, lo: number, hi: number, fallback: number): number {
  if (typeof v !== "number" || !Number.isFinite(v)) return fallback;
  return Math.min(hi, Math.max(lo, Math.floor(v)));
}

/** No enum value is longer than this; longer strings are rejected before any lookup. */
const MAX_VALUE_LENGTH = 16;

function pick<T extends string>(set: ReadonlySet<string>, v: unknown, fallback: T): T {
  return typeof v === "string" && v.length <= MAX_VALUE_LENGTH && set.has(v) ? (v as T) : fallback;
}

export interface LoggerOptions {
  /** Where finished lines go. Defaults to `console.log`. */
  sink?: (line: string) => void;
  now?: () => number;
  /** Clamped to 1..600; default 60. */
  maxPerMinute?: number;
  /** Lines one request may emit; clamped to 1..16; default 4. */
  maxPerRequest?: number;
}

export interface RequestLogger {
  log(event: LogEvent, outcome: Outcome, reason?: Reason, n?: number): void;
  /** Log a failure. The error itself is never read: only the stable code is kept. */
  logError(event: LogEvent, err?: unknown): void;
}

export interface Logger extends RequestLogger {
  /** A logger for one request, with its own small line budget. */
  forRequest(): RequestLogger;
}

export function createLogger(opts: LoggerOptions = {}): Logger {
  const sink = opts.sink ?? ((s: string) => console.log(s));
  const now = opts.now ?? (() => Date.now());
  const cap = clamp(opts.maxPerMinute, 1, MAX_PER_MINUTE_CEILING, DEFAULT_LOG_MAX_PER_MINUTE);
  const perRequest = clamp(opts.maxPerRequest, 1, 16, DEFAULT_LOG_MAX_PER_REQUEST);

  let windowStart = now();
  let emitted = 0;
  let dropped = 0;
  const onceSeen = new Set<string>();

  function emit(fields: Record<string, unknown>): void {
    try {
      sink(JSON.stringify(fields));
    } catch {
      // Logging must never break a request.
    }
  }

  function roll(): void {
    const t = now();
    if (t - windowStart < WINDOW_MS && t >= windowStart) return;
    windowStart = t;
    emitted = 0;
    onceSeen.clear();
    if (dropped > 0) {
      const n = Math.min(dropped, MAX_COUNTER);
      dropped = 0;
      emitted++;
      emit({ e: "log", o: "dropped", n });
    }
  }

  function write(event: unknown, outcome: unknown, reason: unknown, n: unknown): void {
    // Cheap early exit first: once the minute cap is reached a call does no
    // validation, lookup or string building.
    roll();
    if (emitted >= cap) {
      dropped++; // CONTROL:log-cap
      return;
    }
    const e = pick<LogEvent>(EVENTS, event, "log");
    const o = pick<Outcome>(OUTCOMES, outcome, "failed");
    const line: Record<string, unknown> = { e, o };
    if (reason !== undefined && reason !== null) line.r = pick<Reason>(REASONS, reason, "other");
    if (COUNTED.has(e) && typeof n === "number" && Number.isSafeInteger(n) && n >= 0 && n <= MAX_COUNTER) line.n = n;
    if (ONCE_PER_MINUTE.has(e)) {
      if (onceSeen.has(e)) return;
      onceSeen.add(e);
    }
    emitted++;
    emit(line);
  }

  function requestLogger(limit: number): RequestLogger {
    let used = 0;
    return {
      log(event, outcome, reason, n) {
        if (used >= limit) return;
        used++;
        write(event, outcome, reason, n);
      },
      logError(event) {
        this.log(event, "failed", "other");
      },
    };
  }

  const root = requestLogger(Number.POSITIVE_INFINITY);
  return {
    log: root.log,
    logError: root.logError,
    forRequest: () => requestLogger(perRequest),
  };
}

/** The per-isolate logger. */
const shared = createLogger();

export function logEvent(event: LogEvent, outcome: Outcome, reason?: Reason, n?: number): void {
  shared.log(event, outcome, reason, n);
}

export function forRequest(): RequestLogger {
  return shared.forRequest();
}
