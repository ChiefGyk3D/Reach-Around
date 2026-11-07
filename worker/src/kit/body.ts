// SPDX-License-Identifier: Apache-2.0
/**
 * Bounded request bodies. The cap is enforced twice: on the declared
 * Content-Length (header only, nothing read) and again while streaming, so a
 * lying or missing length cannot get more than `maxBytes` buffered. Hostile
 * input produces a typed refusal, never an exception, and a refusal never
 * carries request-derived text.
 */

export type BodyErrorCode = "too_large" | "bad_length" | "length_required" | "length_mismatch" | "aborted" | "bad_utf8" | "bad_stream" | "timeout";

export type BodyFailure = { ok: false; code: BodyErrorCode; status: 400 | 408 | 411 | 413 };
export type BodyResult = { ok: true; bytes: Uint8Array } | BodyFailure;
export type TextResult = { ok: true; text: string } | BodyFailure;

export interface BodyOptions {
  /** Refuse with 411 when no Content-Length is sent. Default false: a streamed body is still capped. */
  requireLength?: boolean;
  /** Deadline for the whole read in milliseconds. Default 10000, clamped to 10..60000. */
  deadlineMs?: number;
}

const DEFAULT_DEADLINE_MS = 10_000;
/** More consecutive zero-length chunks than this is a hostile stream. */
const MAX_EMPTY_CHUNKS = 8;
/** More non-empty chunks than this is refused, whatever the byte cap. */
const MAX_CHUNKS = 4096;
const INITIAL_BUFFER = 4096;
/** Largest single BYOB read. */
const BYOB_READ_SIZE = 16 * 1024;

const STATUS: Record<BodyErrorCode, 400 | 408 | 411 | 413> = {
  too_large: 413,
  bad_length: 400,
  length_required: 411,
  length_mismatch: 400,
  aborted: 400,
  bad_utf8: 400,
  bad_stream: 400,
  timeout: 408,
};

function fail(code: BodyErrorCode): BodyFailure {
  return { ok: false, code, status: STATUS[code] };
}

/** Cancel without waiting: a cancel that never settles must not hold up the answer. */
function discard(body: { cancel(): Promise<void> } | null): void {
  try {
    body?.cancel().catch(() => undefined);
  } catch {
    // The request is being refused anyway.
  }
}

function clampDeadline(v: unknown): number {
  if (typeof v !== "number" || !Number.isFinite(v)) return DEFAULT_DEADLINE_MS;
  return Math.min(60_000, Math.max(10, Math.floor(v)));
}

export async function readBoundedBody(request: Request, maxBytes: number, opts: BodyOptions = {}): Promise<BodyResult> {
  let body: ReadableStream<Uint8Array> | null = null;
  try {
    const cap = typeof maxBytes === "number" && Number.isFinite(maxBytes) && maxBytes > 0 ? Math.floor(maxBytes) : 0;
    body = request.body;
    const declaredRaw = request.headers.get("Content-Length");

    let declared: number | null = null;
    if (declaredRaw !== null) {
      if (!/^\d{1,10}$/.test(declaredRaw)) {
        discard(body);
        return fail("bad_length");
      }
      declared = Number(declaredRaw);
      if (declared > cap) {
        discard(body);
        return fail("too_large");
      }
    } else if (opts.requireLength === true) {
      discard(body);
      return fail("length_required");
    }

    if (body === null) {
      return declared !== null && declared !== 0 ? fail("length_mismatch") : { ok: true, bytes: new Uint8Array(0) };
    }

    // Prefer a BYOB reader (a byte-stream body, which is what the Workers runtime
    // gives a real request): no single read can then return more than
    // `readSize` bytes, whatever the sender offers. Bodies that are not byte
    // streams fall back to the default reader, where the platform's own
    // chunking and the post-read cap apply.
    let byob: ReadableStreamBYOBReader | null = null;
    try {
      byob = body.getReader({ mode: "byob" });
    } catch {
      byob = null;
    }
    const reader: { cancel(): Promise<void>; releaseLock(): void } = byob ?? body.getReader();
    const defaultReader = byob === null ? (reader as ReadableStreamDefaultReader<Uint8Array>) : null;
    let spare = new ArrayBuffer(Math.min(cap + 1, BYOB_READ_SIZE));
    const readNext = (): Promise<ReadableStreamReadResult<Uint8Array>> =>
      byob !== null ? byob.read(new Uint8Array(spare)) : defaultReader!.read();
    const release = (): void => {
      try {
        reader.releaseLock();
      } catch {
        // Already released, or still settling: nothing more to do.
      }
    };
    let pendingRead: Promise<unknown> | null = null;
    let timedOut = false;
    const deadlineMs = clampDeadline(opts.deadlineMs);
    let timer: ReturnType<typeof setTimeout> | undefined;
    const expired = new Promise<"timeout">((resolve) => {
      timer = setTimeout(() => resolve("timeout"), deadlineMs);
    });
    // One growing buffer: no per-chunk objects are retained.
    let buf = new Uint8Array(Math.min(cap, declared !== null ? declared : INITIAL_BUFFER));
    let total = 0;
    let empties = 0;
    let chunks = 0;
    try {
      for (;;) {
        const rp = readNext();
        pendingRead = rp;
        const step = await Promise.race([rp, expired]);
        if (step === "timeout") {
          timedOut = true;
          discard(reader);
          return fail("timeout");
        }
        const { done, value } = step;
        if (done) break;
        if (byob !== null && value instanceof Uint8Array) spare = value.buffer as ArrayBuffer;
        if (!(value instanceof Uint8Array)) {
          discard(reader);
          return fail("aborted");
        }
        if (value.byteLength === 0) {
          if (++empties > MAX_EMPTY_CHUNKS) {
            discard(reader);
            return fail("bad_stream");
          }
          continue;
        }
        empties = 0;
        total += value.byteLength;
        if (total > cap) {
          discard(reader); // CONTROL:body-cap
          return fail("too_large");
        }
        if (++chunks > MAX_CHUNKS) {
          discard(reader);
          return fail("bad_stream");
        }
        if (total > buf.length) {
          const grown = new Uint8Array(Math.min(cap, Math.max(total, buf.length * 2)));
          grown.set(buf.subarray(0, total - value.byteLength));
          buf = grown;
        }
        buf.set(value, total - value.byteLength);
      }
    } catch {
      discard(reader);
      return fail("aborted");
    } finally {
      clearTimeout(timer);
      // Release the lock once no read is pending. After a timeout the read is
      // still in flight (the cancel above settles it), so release when it does;
      // the response is never held up for that.
      if (timedOut && pendingRead !== null) pendingRead.then(release, release);
      else release();
    }

    if (declared !== null && declared !== total) return fail("length_mismatch");

    const bytes = buf.length === total ? buf : buf.slice(0, total);
    return { ok: true, bytes };
  } catch {
    discard(body);
    return fail("aborted");
  }
}

/** As `readBoundedBody`, then a strict UTF-8 decode. */
export async function readBoundedText(request: Request, maxBytes: number, opts: BodyOptions = {}): Promise<TextResult> {
  const r = await readBoundedBody(request, maxBytes, opts);
  if (!r.ok) return r;
  try {
    return { ok: true, text: new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(r.bytes) };
  } catch {
    return fail("bad_utf8");
  }
}
