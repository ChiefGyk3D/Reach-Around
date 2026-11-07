// SPDX-License-Identifier: Apache-2.0
//
// Bounded read of a byte stream for the gate form and the Turnstile answer.
// Same semantics as readBoundedBody in worker/src/kit/body.ts (PR #284): empty
// chunks are dropped and capped, the number of non-empty chunks is capped, the
// whole read races a deadline, cancellation is fire and forget, and the reader
// lock is released on every exit (after the pending read settles when the exit
// was a timeout).
// TODO: replace this module with the shared readBoundedBody once #284 lands, so
// the guard exists once.

/** More consecutive zero-length chunks than this is a hostile stream. */
export const MAX_EMPTY_CHUNKS = 8;
/** More non-empty chunks than this is refused, whatever the byte cap. */
export const MAX_CHUNKS = 256;

/** Cancel without waiting: a cancel that never settles must not hold up the answer. */
function discard(reader: ReadableStreamDefaultReader<Uint8Array>): void {
  try {
    reader.cancel().catch(() => undefined);
  } catch {
    // The stream is being refused anyway.
  }
}

/**
 * Read at most `max` bytes. Null if the body is longer, the stream is hostile,
 * errors, or does not finish within `deadlineMs`. Never throws.
 */
export async function readCappedBytes(body: ReadableStream<Uint8Array> | null, max: number, deadlineMs: number): Promise<Uint8Array | null> {
  if (body === null) return new Uint8Array(0);
  let reader: ReadableStreamDefaultReader<Uint8Array>;
  try {
    reader = body.getReader();
  } catch {
    return null;
  }
  const release = (): void => {
    try {
      reader.releaseLock();
    } catch {
      // Already released.
    }
  };
  const chunks: Uint8Array[] = [];
  let total = 0;
  let empties = 0;
  let count = 0;
  let timedOut = false;
  let pending: Promise<unknown> | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const expired = new Promise<"timeout">((resolve) => {
    timer = setTimeout(() => resolve("timeout"), Math.max(1, deadlineMs));
  });
  try {
    for (;;) {
      const rp = reader.read();
      pending = rp;
      const step = await Promise.race([rp, expired]);
      if (step === "timeout") {
        timedOut = true;
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
    if (timedOut && pending !== null) pending.then(release, release);
    else release();
  }
  const out = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) {
    out.set(c, off);
    off += c.byteLength;
  }
  return out;
}
