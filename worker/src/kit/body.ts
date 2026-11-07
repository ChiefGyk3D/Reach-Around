// SPDX-License-Identifier: Apache-2.0
/**
 * Bounded request bodies. The cap is enforced twice: on the declared
 * Content-Length (header only, nothing read) and again while streaming, so a
 * lying or missing length cannot get more than `maxBytes` buffered. Hostile
 * input produces a typed refusal, never an exception, and a refusal never
 * carries request-derived text.
 */

export type BodyErrorCode = "too_large" | "bad_length" | "length_required" | "length_mismatch" | "aborted" | "bad_utf8";

export type BodyFailure = { ok: false; code: BodyErrorCode; status: 400 | 411 | 413 };
export type BodyResult = { ok: true; bytes: Uint8Array } | BodyFailure;
export type TextResult = { ok: true; text: string } | BodyFailure;

export interface BodyOptions {
  /** Refuse with 411 when no Content-Length is sent. Default false: a streamed body is still capped. */
  requireLength?: boolean;
}

const STATUS: Record<BodyErrorCode, 400 | 411 | 413> = {
  too_large: 413,
  bad_length: 400,
  length_required: 411,
  length_mismatch: 400,
  aborted: 400,
  bad_utf8: 400,
};

function fail(code: BodyErrorCode): BodyFailure {
  return { ok: false, code, status: STATUS[code] };
}

async function discard(body: ReadableStream<Uint8Array> | null): Promise<void> {
  try {
    await body?.cancel();
  } catch {
    // The request is being refused anyway.
  }
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
        await discard(body);
        return fail("bad_length");
      }
      declared = Number(declaredRaw);
      if (declared > cap) {
        await discard(body);
        return fail("too_large");
      }
    } else if (opts.requireLength === true) {
      await discard(body);
      return fail("length_required");
    }

    if (body === null) {
      return declared !== null && declared !== 0 ? fail("length_mismatch") : { ok: true, bytes: new Uint8Array(0) };
    }

    const reader = body.getReader();
    const chunks: Uint8Array[] = [];
    let total = 0;
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        if (!(value instanceof Uint8Array)) {
          await reader.cancel().catch(() => undefined);
          return fail("aborted");
        }
        total += value.byteLength;
        if (total > cap) {
          await reader.cancel().catch(() => undefined); // CONTROL:body-cap
          return fail("too_large");
        }
        chunks.push(value);
      }
    } catch {
      await reader.cancel().catch(() => undefined);
      return fail("aborted");
    }

    if (declared !== null && declared !== total) return fail("length_mismatch");

    const bytes = new Uint8Array(total);
    let at = 0;
    for (const c of chunks) {
      bytes.set(c, at);
      at += c.byteLength;
    }
    return { ok: true, bytes };
  } catch {
    await discard(body);
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
