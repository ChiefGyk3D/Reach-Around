// SPDX-License-Identifier: Apache-2.0
//
// Input handling for the media-kit gate form. One narrow grammar for the
// address, one exact content type, a hard body cap, and a closed set of field
// names. Anything outside it is refused rather than repaired: the address ends
// up in a mail header and a recipient field, so there is no "cleaning".
//
// Nothing here logs.

export const FIELD_EMAIL = "email";
/** The name the Turnstile widget gives its hidden field. */
export const FIELD_TURNSTILE = "cf-turnstile-response";
/** A field real visitors never fill (hidden by CSS). A value means a script. */
export const FIELD_HONEYPOT = "website";

/** Body cap in bytes. Task 16 replaces the read with `readBounded`. */
export const MAX_FORM_BYTES = 4096;
export const MAX_EMAIL_LENGTH = 254;
export const MAX_TURNSTILE_TOKEN_LENGTH = 2048;

const FORM_CONTENT_TYPE = "application/x-www-form-urlencoded";
const MAX_LOCAL_LENGTH = 64;
const MAX_DOMAIN_LENGTH = 253;
const MAX_LABEL_LENGTH = 63;

// Printable ASCII without space: 0x21 to 0x7e. Checked on the RAW string, before
// any case mapping, so characters that lowercase into ASCII never get that far.
const PRINTABLE_ASCII = /^[\x21-\x7e]+$/;
// Local part: dot-separated atoms of letters, digits and `_+-`. No quoting,
// comments, percent routing or other specials.
const LOCAL_RE = /^[a-z0-9_+-]+(?:\.[a-z0-9_+-]+)*$/;
const LABEL_RE = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/;
// Final label: letters only, or a punycode label. Rules out IP literals and
// all-numeric hosts.
const TLD_RE = /^(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$/;
const TOKEN_RE = /^[\x21-\x7e]+$/;

/**
 * Normalize one email address, or return null. Accepts an ASCII-only address of
 * 3 to 254 characters with exactly one `@`, a dot-atom local part of at most 64
 * characters, and a domain of at least two labels. Lowercases; does no other
 * rewriting (plus tags are kept). Non-ASCII, whitespace, control characters,
 * quoting, comments, display names, lists and IP literals are all refused.
 */
export function normalizeEmail(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  if (raw.length < 3 || raw.length > MAX_EMAIL_LENGTH) return null;
  if (!PRINTABLE_ASCII.test(raw)) return null;
  const s = raw.toLowerCase();
  const at = s.indexOf("@");
  if (at < 1 || at !== s.lastIndexOf("@")) return null;
  const local = s.slice(0, at);
  const domain = s.slice(at + 1);
  if (local.length > MAX_LOCAL_LENGTH || !LOCAL_RE.test(local)) return null;
  if (domain.length > MAX_DOMAIN_LENGTH) return null;
  const labels = domain.split(".");
  if (labels.length < 2) return null;
  for (const l of labels) {
    if (l.length > MAX_LABEL_LENGTH || !LABEL_RE.test(l)) return null;
  }
  if (!TLD_RE.test(labels[labels.length - 1]!)) return null;
  return s;
}

/** A Turnstile token as the widget produces it, or null. */
function cleanToken(v: string): string | null {
  if (v.length < 1 || v.length > MAX_TURNSTILE_TOKEN_LENGTH) return null;
  return TOKEN_RE.test(v) ? v : null;
}

const BAD_PERCENT = /%(?![0-9a-fA-F]{2})/;
const DEFAULT_DEADLINE_MS = 5000;

function clampDeadline(v: unknown): number {
  if (typeof v !== "number" || !Number.isFinite(v)) return DEFAULT_DEADLINE_MS;
  return Math.min(30_000, Math.max(10, Math.floor(v)));
}
/** More consecutive zero-length chunks than this is a hostile stream. */
const MAX_EMPTY_CHUNKS = 8;
/** More non-empty chunks than this is refused, whatever the byte cap. */
const MAX_CHUNKS = 4096;

/** Cancel without waiting: a cancel that never settles must not hold up the answer. */
function discard(reader: ReadableStreamDefaultReader<Uint8Array>): void {
  try {
    reader.cancel().catch(() => undefined);
  } catch {
    // The request is being refused anyway.
  }
}

/**
 * Read at most `max` bytes. Null if the body is longer, the stream is hostile
 * (empty-chunk or tiny-chunk floods), errors, or does not finish by the
 * deadline. Cancellation is fire and forget.
 * TODO: switch to the shared readBoundedBody (worker/src/kit/body.ts) once that
 * module lands, so this guard is not implemented twice.
 */
async function readCapped(body: ReadableStream<Uint8Array> | null, max: number, deadlineMs: number): Promise<Uint8Array | null> {
  if (body === null) return new Uint8Array(0);
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  let empties = 0;
  let count = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const expired = new Promise<"timeout">((resolve) => {
    timer = setTimeout(() => resolve("timeout"), deadlineMs);
  });
  try {
    for (;;) {
      const step = await Promise.race([reader.read(), expired]);
      if (step === "timeout") {
        discard(reader);
        return null;
      }
      const { done, value } = step;
      if (done) break;
      if (!(value instanceof Uint8Array)) {
        discard(reader);
        return null;
      }
      if (value.byteLength === 0) {
        if (++empties > MAX_EMPTY_CHUNKS) {
          discard(reader);
          return null;
        }
        continue;
      }
      empties = 0;
      total += value.byteLength;
      if (total > max || ++count > MAX_CHUNKS) {
        discard(reader);
        return null;
      }
      chunks.push(value);
    }
  } catch {
    discard(reader);
    return null;
  } finally {
    clearTimeout(timer);
  }
  const out = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) {
    out.set(c, off);
    off += c.byteLength;
  }
  return out;
}

export interface GateForm {
  /** Normalized, or null when absent or invalid. */
  email: string | null;
  /** The token as sent, or null when absent or not shaped like one. */
  turnstile: string | null;
}

/**
 * Parse the gate form. Returns null (refuse the request) for: a method other
 * than POST, a content type other than exactly `application/x-www-form-urlencoded`,
 * a body over 4096 bytes or not valid UTF-8, a repeated or unknown field, or a
 * non-empty honeypot. An absent or invalid email or token comes back as null
 * inside the result, so the caller decides how to answer.
 */
export async function readForm(request: Request, opts: { deadlineMs?: number } = {}): Promise<GateForm | null> {
  if (request.method !== "POST") return null;
  if (request.headers.get("content-type") !== FORM_CONTENT_TYPE) return null;
  const declared = request.headers.get("content-length");
  if (declared !== null && (!/^[0-9]{1,9}$/.test(declared) || Number(declared) > MAX_FORM_BYTES)) return null;

  const bytes = await readCapped(request.body, MAX_FORM_BYTES, clampDeadline(opts.deadlineMs));
  if (bytes === null) return null;
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes);
  } catch {
    return null;
  }
  // URLSearchParams passes a stray percent sign through; refuse it instead.
  if (BAD_PERCENT.test(text)) return null;

  const seen = new Map<string, string>();
  for (const [k, v] of new URLSearchParams(text)) {
    if (k !== FIELD_EMAIL && k !== FIELD_TURNSTILE && k !== FIELD_HONEYPOT) return null;
    if (seen.has(k)) return null;
    seen.set(k, v);
  }
  if ((seen.get(FIELD_HONEYPOT) ?? "") !== "") return null;

  const email = seen.get(FIELD_EMAIL);
  const token = seen.get(FIELD_TURNSTILE);
  return {
    // CONTROL:email-validate
    email: email === undefined ? null : normalizeEmail(email),
    turnstile: token === undefined ? null : cleanToken(token),
  };
}
