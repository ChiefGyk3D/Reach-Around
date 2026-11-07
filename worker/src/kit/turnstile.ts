// SPDX-License-Identifier: Apache-2.0
//
// Server-side Turnstile check (Cloudflare "siteverify"). Request and response
// shape per https://developers.cloudflare.com/turnstile/get-started/server-side-validation/
// (read 2026-11-06): POST form fields `secret` and `response`, JSON answer with
// `success`, `hostname`, `action`, `challenge_ts`, `error-codes`, `cdata`.
//
// Fails closed: every error, timeout, odd status or odd body is `false`. The
// function never throws, never logs, and never puts the secret anywhere but
// the request body. Only the two documented fields are sent; the visitor's
// address is not among them, so no address leaves the Worker.
//
// Route wiring (the gate and send handlers) must pass the page host that the
// widget is rendered on as `opts.hostname`. That host can differ from
// `SITE_HOST`, which names the links site. Use `expectedHostname` to check the
// configuration once at load; an unset or blank host makes every check fail.

import type { KitEnv } from "./env";
import { dnsLabelOk } from "./punycode";
import { readBoundedBody } from "./body";

export const SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";
export const TURNSTILE_TIMEOUT_MS = 3000;
const MAX_TOKEN_LENGTH = 2048;
const MAX_RESPONSE_BYTES = 16 * 1024;
const MAX_JSON_DEPTH = 32;
const TOKEN_RE = /^[\x21-\x7e]{1,2048}$/;

export type TurnstileAction = "gate" | "send";

export interface TurnstileOptions {
  /** Expected hostname of the page that hosts the widget. Defaults to `env.SITE_HOST`. */
  hostname?: string;
  /** Test seam. Default 3000. */
  timeoutMs?: number;
}

/**
 * The hostname a siteverify answer must carry: `opts.hostname` when the option
 * is present, else `env.SITE_HOST`. Trimmed and lowercased. Null when the value
 * is missing, blank, not a string, or not a plain hostname (no port, path or
 * non-ASCII). A null here means verification can never succeed.
 */
export function expectedHostname(env: { SITE_HOST?: unknown } | undefined, opts: { hostname?: unknown } = {}): string | null {
  const raw = opts.hostname !== undefined ? opts.hostname : env?.SITE_HOST;
  if (typeof raw !== "string") return null;
  const h = raw.trim().toLowerCase();
  if (h.length > 253) return null;
  const labels = h.split(".");
  // At least two labels: a kit page is never served from a bare single-label host.
  if (labels.length < 2) return null;
  for (const l of labels) {
    if (!dnsLabelOk(l)) return null;
  }
  return h;
}

/** True when every UTF-16 surrogate in `s` is half of a valid pair. */
function wellFormed(s: string): boolean {
  for (let n = 0; n < s.length; n++) {
    const c = s.charCodeAt(n);
    if (c >= 0xd800 && c <= 0xdbff) {
      const d = s.charCodeAt(n + 1);
      if (!(d >= 0xdc00 && d <= 0xdfff)) return false;
      n++;
    } else if (c >= 0xdc00 && c <= 0xdfff) {
      return false;
    }
  }
  return true;
}

const NUMBER_RE = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/;
const HEX4_RE = /^[0-9a-fA-F]{4}$/;

/**
 * Strict JSON parser. Throws on malformed input, trailing content, nesting past
 * 32 levels, a `__proto__` key, or a duplicate key at any level. Keys are
 * compared AFTER decoding (escapes and surrogate pairs), so two spellings of one
 * key are a duplicate. Objects are built without a prototype.
 */
export function parseStrictJson(text: string): unknown {
  let i = 0;
  const fail = (): never => {
    throw new Error("json");
  };
  const ws = (): void => {
    while (i < text.length) {
      const c = text.charCodeAt(i);
      if (c === 0x20 || c === 0x09 || c === 0x0a || c === 0x0d) i++;
      else break;
    }
  };
  const str = (): string => {
    if (text[i] !== '"') return fail();
    i++;
    let out = "";
    for (;;) {
      if (i >= text.length) return fail();
      const ch = text[i]!;
      const c = text.charCodeAt(i);
      if (ch === '"') {
        i++;
        return wellFormed(out) ? out : fail();
      }
      if (c < 0x20) return fail();
      if (ch !== "\\") {
        out += ch;
        i++;
        continue;
      }
      const e = text[i + 1];
      i += 2;
      switch (e) {
        case '"': out += '"'; break;
        case "\\": out += "\\"; break;
        case "/": out += "/"; break;
        case "b": out += "\b"; break;
        case "f": out += "\f"; break;
        case "n": out += "\n"; break;
        case "r": out += "\r"; break;
        case "t": out += "\t"; break;
        case "u": {
          const hex = text.slice(i, i + 4);
          if (!HEX4_RE.test(hex)) return fail();
          out += String.fromCharCode(parseInt(hex, 16));
          i += 4;
          break;
        }
        default:
          return fail();
      }
    }
  };
  const value = (depth: number): unknown => {
    if (depth > MAX_JSON_DEPTH) return fail();
    ws();
    const ch = text[i];
    if (ch === "{") {
      i++;
      const obj: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
      ws();
      if (text[i] === "}") {
        i++;
        return obj;
      }
      for (;;) {
        ws();
        const k = str();
        if (k === "__proto__" || Object.prototype.hasOwnProperty.call(obj, k)) return fail();
        ws();
        if (text[i] !== ":") return fail();
        i++;
        obj[k] = value(depth + 1);
        ws();
        if (text[i] === ",") {
          i++;
          continue;
        }
        if (text[i] === "}") {
          i++;
          return obj;
        }
        return fail();
      }
    }
    if (ch === "[") {
      i++;
      const arr: unknown[] = [];
      ws();
      if (text[i] === "]") {
        i++;
        return arr;
      }
      for (;;) {
        arr.push(value(depth + 1));
        ws();
        if (text[i] === ",") {
          i++;
          continue;
        }
        if (text[i] === "]") {
          i++;
          return arr;
        }
        return fail();
      }
    }
    if (ch === '"') return str();
    if (text.startsWith("true", i)) {
      i += 4;
      return true;
    }
    if (text.startsWith("false", i)) {
      i += 5;
      return false;
    }
    if (text.startsWith("null", i)) {
      i += 4;
      return null;
    }
    const m = NUMBER_RE.exec(text.slice(i, i + 64));
    if (m === null) return fail();
    i += m[0].length;
    return Number(m[0]);
  };
  const result = value(0);
  ws();
  if (i !== text.length) return fail();
  return result;
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

    // CONTROL:turnstile-hostname
    const host = expectedHostname(env, opts);
    if (host === null) return false;

    const form = new URLSearchParams();
    form.set("secret", secret);
    form.set("response", token);

    const timeoutMs = opts.timeoutMs ?? TURNSTILE_TIMEOUT_MS;
    const started = Date.now();
    let fetchTimer: ReturnType<typeof setTimeout> | undefined;
    const fetchExpired = new Promise<"timeout">((resolve) => {
      fetchTimer = setTimeout(() => resolve("timeout"), timeoutMs);
    });
    let res: Response | "timeout";
    try {
      res = await Promise.race([
        fetchFn(SITEVERIFY_URL, {
          method: "POST",
          body: form,
          redirect: "error",
          signal: AbortSignal.timeout(timeoutMs),
        }),
        fetchExpired,
      ]);
    } finally {
      clearTimeout(fetchTimer);
    }
    if (res === "timeout" || res.status !== 200) return false;
    // One deadline covers the fetch and the body read.
    // The upstream length header is not trusted: re-wrap the stream so only the byte cap applies.
    const wrapped = new Request(SITEVERIFY_URL, { method: "POST", body: res.body, duplex: "half" } as RequestInit);
    const read = await readBoundedBody(wrapped, MAX_RESPONSE_BYTES, { deadlineMs: timeoutMs - (Date.now() - started) });
    if (!read.ok) return false;
    const text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(read.bytes);

    const parsed = parseStrictJson(text);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return false;
    const r = parsed as Record<string, unknown>;
    if (r.success !== true) return false;
    const codes = r["error-codes"];
    if (codes !== undefined && (!Array.isArray(codes) || codes.length > 0)) return false;
    if (r.action !== action) return false;
    if (typeof r.hostname !== "string" || r.hostname.toLowerCase() !== host) return false;
    return true;
  } catch {
    return false;
  }
}
