// SPDX-License-Identifier: Apache-2.0
//
// Server-side Turnstile check (Cloudflare "siteverify"). Request and response
// shape per https://developers.cloudflare.com/turnstile/get-started/server-side-validation/
// (read 2026-11-06): POST form fields `secret` and `response` (optional
// `remoteip` and `idempotency_key`), JSON answer with `success`, `hostname`,
// `action`, `challenge_ts`, `error-codes`, `cdata`.
//
// Fails closed: every error, timeout, odd status or odd body is `false`. The
// function never throws, never logs, and never puts the secret anywhere but
// the request body. The visitor's address is never sent (`remoteip` is
// optional in the API), so no address leaves the Worker.

import type { KitEnv } from "./env";

export const SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";
export const TURNSTILE_TIMEOUT_MS = 3000;
const MAX_TOKEN_LENGTH = 2048;
const MAX_RESPONSE_BYTES = 16 * 1024;
const TOKEN_RE = /^[\x21-\x7e]{1,2048}$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export type TurnstileAction = "gate" | "send";

export interface TurnstileOptions {
  /** Expected hostname. Defaults to `env.SITE_HOST`. When neither is set, hostname is not checked. */
  hostname?: string;
  /** A UUID, to make a retry safe. Anything else fails the check. */
  idempotencyKey?: string;
  /** Test seam. Default 3000. */
  timeoutMs?: number;
}

/** Read a response body as text, at most `max` bytes; null when longer or unreadable. */
async function readText(res: Response, max: number): Promise<string | null> {
  if (res.body === null) return "";
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > max) {
      await reader.cancel().catch(() => undefined);
      return null;
    }
    chunks.push(value);
  }
  const all = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) {
    all.set(c, off);
    off += c.byteLength;
  }
  return new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(all);
}

/** True when a decision key appears more than once (JSON.parse would keep the last). */
function hasRepeatedKey(text: string): boolean {
  for (const k of ["success", "hostname", "action", "error-codes"]) {
    const m = text.match(new RegExp(`"${k}"\\s*:`, "g"));
    if (m !== null && m.length > 1) return true;
  }
  return false;
}

export async function verifyTurnstile(
  env: Pick<KitEnv, "TURNSTILE_SECRET"> & { SITE_HOST?: string },
  token: unknown,
  action: TurnstileAction,
  fetchFn: typeof fetch = fetch,
  opts: TurnstileOptions = {},
): Promise<boolean> {
  try {
    if (typeof token !== "string" || token.length > MAX_TOKEN_LENGTH || !TOKEN_RE.test(token)) return false;
    if (action !== "gate" && action !== "send") return false;
    const secret = (env as { TURNSTILE_SECRET?: unknown } | undefined)?.TURNSTILE_SECRET;
    if (typeof secret !== "string" || secret === "") return false;
    if (opts.idempotencyKey !== undefined && !UUID_RE.test(opts.idempotencyKey)) return false;

    const rawHost = opts.hostname ?? env.SITE_HOST;
    const expectedHost = typeof rawHost === "string" ? rawHost.trim().toLowerCase() : "";

    const form = new URLSearchParams();
    form.set("secret", secret);
    form.set("response", token);
    if (opts.idempotencyKey !== undefined) form.set("idempotency_key", opts.idempotencyKey);

    const res = await fetchFn(SITEVERIFY_URL, {
      method: "POST",
      body: form,
      redirect: "error",
      signal: AbortSignal.timeout(opts.timeoutMs ?? TURNSTILE_TIMEOUT_MS),
    });
    if (res.status !== 200) return false;
    const text = await readText(res, MAX_RESPONSE_BYTES);
    if (text === null || hasRepeatedKey(text)) return false;

    const parsed: unknown = JSON.parse(text);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return false;
    const r = parsed as Record<string, unknown>;
    if (r.success !== true) return false;
    const codes = r["error-codes"];
    if (codes !== undefined && (!Array.isArray(codes) || codes.length > 0)) return false;
    if (r.action !== action) return false;
    if (expectedHost !== "") {
      if (typeof r.hostname !== "string" || r.hostname.toLowerCase() !== expectedHost) return false;
    }
    return true;
  } catch {
    return false;
  }
}
