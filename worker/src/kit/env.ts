// SPDX-License-Identifier: Apache-2.0
import type { Env, RateLimiter } from "../click";
import { MAX_TOKEN_AGE_SECONDS, MIN_KEY_BYTES } from "./token";

/**
 * What the kit adds to the Worker environment. `DB`, `SITE_HOST` and the click
 * bindings stay declared once, in `click.ts`.
 */
export interface KitEnv extends Env {
  KIT: KVNamespace;
  GATE_LIMITER: RateLimiter;
  /** "1" or "true" (any case) stops the kit routes. Read on every request. */
  KIT_KILL?: string;
  /** SECRET. Signs unlock tokens. At least 32 bytes. */
  TOKEN_HMAC_KEY: string;
  /** SECRET, optional. The key being rotated out; still verifies, never signs. */
  TOKEN_HMAC_KEY_PREVIOUS?: string;
  /** SECRET. Turnstile verification. */
  TURNSTILE_SECRET: string;
  /** Unlock cookie lifetime in seconds. Default 86400, at most 168 hours. */
  TOKEN_TTL_SECONDS?: string;
}

export const DEFAULT_TOKEN_TTL_SECONDS = 86_400;
const MIN_TTL_SECONDS = 60;

/** The kit configuration is unusable. Carries field names only, never values. */
export class KitEnvError extends Error {
  readonly fields: readonly string[];
  constructor(fields: readonly string[]) {
    super(`kit environment invalid: ${fields.join(", ")}`);
    this.name = "KitEnvError";
    this.fields = fields;
  }
}

export interface KitConfig {
  /** Verification keys, current first. Sign with `keys[0]`. */
  keys: readonly string[];
  tokenTtlSeconds: number;
  killed: boolean;
}

/** Same truthy rule as the other kill switches. Cheap, so call it per request. */
export function isKitKilled(env: Pick<KitEnv, "KIT_KILL">): boolean {
  const v = env?.KIT_KILL;
  if (typeof v !== "string") return false;
  const s = v.trim().toLowerCase();
  return s === "1" || s === "true";
}

function keyOk(v: unknown): v is string {
  return typeof v === "string" && new TextEncoder().encode(v).length >= MIN_KEY_BYTES;
}

/**
 * Validate every binding and secret the kit needs. Fails closed: any problem
 * throws a KitEnvError that lists the offending variable NAMES. A value is
 * never read into a message.
 */
export function loadKitEnv(env: KitEnv): KitConfig {
  const bad: string[] = [];
  const e = (env ?? {}) as unknown as Record<string, unknown>;

  if (e.KIT === undefined || e.KIT === null || typeof e.KIT !== "object") bad.push("KIT");
  const lim = e.GATE_LIMITER as { limit?: unknown } | undefined;
  if (lim === undefined || lim === null || typeof lim.limit !== "function") bad.push("GATE_LIMITER");

  const keys: string[] = [];
  if (keyOk(e.TOKEN_HMAC_KEY)) keys.push(e.TOKEN_HMAC_KEY);
  else bad.push("TOKEN_HMAC_KEY");

  const prev = e.TOKEN_HMAC_KEY_PREVIOUS;
  if (prev !== undefined && prev !== null && prev !== "") {
    if (keyOk(prev)) {
      if (prev !== keys[0]) keys.push(prev);
    } else bad.push("TOKEN_HMAC_KEY_PREVIOUS");
  }

  if (typeof e.TURNSTILE_SECRET !== "string" || e.TURNSTILE_SECRET === "") bad.push("TURNSTILE_SECRET");

  let ttl = DEFAULT_TOKEN_TTL_SECONDS;
  const rawTtl = e.TOKEN_TTL_SECONDS;
  if (rawTtl !== undefined && rawTtl !== null && rawTtl !== "") {
    if (typeof rawTtl === "string" && /^[0-9]{1,9}$/.test(rawTtl)) {
      const n = Number(rawTtl);
      if (n >= MIN_TTL_SECONDS && n <= MAX_TOKEN_AGE_SECONDS) ttl = n;
      else bad.push("TOKEN_TTL_SECONDS");
    } else bad.push("TOKEN_TTL_SECONDS");
  }

  if (bad.length > 0) throw new KitEnvError(bad);
  return { keys, tokenTtlSeconds: ttl, killed: isKitKilled(env) };
}
