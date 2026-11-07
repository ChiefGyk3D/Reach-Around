// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { readBoundedBody, readBoundedText } from "../../src/kit/body";

interface Spy {
  pulls: number;
  cancelled: boolean;
}

/** A stream of `total` bytes in chunks, counting pulls and cancellation. */
function stream(total: number, chunk = 512): { body: ReadableStream<Uint8Array>; spy: Spy } {
  const spy: Spy = { pulls: 0, cancelled: false };
  let sent = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(c) {
      spy.pulls++;
      if (sent >= total) return c.close();
      const n = Math.min(chunk, total - sent);
      sent += n;
      c.enqueue(new Uint8Array(n).fill(97));
    },
    cancel() {
      spy.cancelled = true;
    },
  }, { highWaterMark: 0 }); // no prefetch: a pull means a reader asked
  return { body, spy };
}

function req(body: BodyInit | null, headers: Record<string, string> = {}): Request {
  const init: RequestInit & { duplex?: string } = { method: "POST", headers, body };
  if (body instanceof ReadableStream) init.duplex = "half";
  return new Request("https://example.test/k/gate", init);
}

describe("readBoundedBody: header checks read nothing", () => {
  it("a declared length above the cap is 413 and the stream is never pulled", async () => {
    const { body, spy } = stream(5000);
    const r = await readBoundedBody(req(body, { "Content-Length": "5000" }), 1024);
    expect(r).toMatchObject({ ok: false, code: "too_large", status: 413 });
    expect(spy.pulls).toBe(0);
  });
  it.each(["abc", "-1", "1.5", "5, 5", "0x10", "99999999999999999999"])(
    "malformed length %j is 400 bad_length",
    async (v) => {
      const { body, spy } = stream(5);
      const r = await readBoundedBody(req(body, { "Content-Length": v }), 1024);
      expect(r).toMatchObject({ ok: false, code: "bad_length", status: 400 });
      expect(spy.pulls).toBe(0);
    },
  );
  it("a missing length is 411 only when requireLength is set, and nothing is pulled", async () => {
    const { body, spy } = stream(5);
    const r = await readBoundedBody(req(body), 1024, { requireLength: true });
    expect(r).toMatchObject({ ok: false, code: "length_required", status: 411 });
    expect(spy.pulls).toBe(0);
  });
});

describe("readBoundedBody: the stream is capped too", () => {
  it("a lying Content-Length (says 10, sends 5000) is refused near the cap and cancelled", async () => {
    const { body, spy } = stream(5000);
    const r = await readBoundedBody(req(body, { "Content-Length": "10" }), 1024);
    expect(r).toMatchObject({ ok: false, status: 413 });
    expect(spy.cancelled).toBe(true);
    // cap + one 512 byte chunk, plus the pull that follows the enqueue
    expect(spy.pulls).toBeLessThanOrEqual(5);
  });
  it("a missing length with a large streamed body is refused near the cap and cancelled", async () => {
    const { body, spy } = stream(1_000_000);
    const r = await readBoundedBody(req(body), 1024);
    expect(r).toMatchObject({ ok: false, code: "too_large", status: 413 });
    expect(spy.cancelled).toBe(true);
    expect(spy.pulls).toBeLessThanOrEqual(5);
  });
  it("a length that is within the cap but does not match what arrives is 400", async () => {
    const { body } = stream(50);
    const r = await readBoundedBody(req(body, { "Content-Length": "10" }), 1024);
    expect(r).toMatchObject({ ok: false, code: "length_mismatch", status: 400 });
  });
  it("exactly the cap is returned", async () => {
    const { body } = stream(1024);
    const r = await readBoundedBody(req(body, { "Content-Length": "1024" }), 1024);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.bytes.length).toBe(1024);
  });
  it("cap + 1 is refused, with and without a declared length", async () => {
    const a = stream(1025);
    expect(await readBoundedBody(req(a.body, { "Content-Length": "1025" }), 1024)).toMatchObject({ ok: false, status: 413 });
    const b = stream(1025);
    expect(await readBoundedBody(req(b.body), 1024)).toMatchObject({ ok: false, status: 413 });
  });
  it("an empty body is ok and empty", async () => {
    const r = await readBoundedBody(req(null), 1024);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.bytes.length).toBe(0);
    const z = await readBoundedBody(req(new Uint8Array(0), { "Content-Length": "0" }), 1024);
    expect(z.ok && z.bytes.length).toBe(0);
  });
  it("an aborted stream is 400 aborted and never throws", async () => {
    let n = 0;
    const body = new ReadableStream<Uint8Array>({
      pull(c) {
        if (n++ === 0) c.enqueue(new Uint8Array(10));
        else c.error(new Error("a@b.example connection reset"));
      },
    });
    const r = await readBoundedBody(req(body), 1024);
    expect(r).toMatchObject({ ok: false, code: "aborted", status: 400 });
    expect(JSON.stringify(r)).not.toContain("@");
  });
  it("a cap of zero, negative or NaN accepts only an empty body", async () => {
    for (const cap of [0, -5, Number.NaN]) {
      const { body } = stream(1);
      expect(await readBoundedBody(req(body), cap)).toMatchObject({ ok: false, status: 413 });
      expect((await readBoundedBody(req(null), cap)).ok).toBe(true);
    }
  });
});

describe("readBoundedText", () => {
  it("decodes UTF-8", async () => {
    const r = await readBoundedText(req("héllo", { "Content-Length": String(new TextEncoder().encode("héllo").length) }), 1024);
    expect(r).toEqual({ ok: true, text: "héllo" });
  });
  it("invalid UTF-8 is 400 bad_utf8", async () => {
    const r = await readBoundedText(req(new Uint8Array([0xff, 0xfe, 0xfd]), { "Content-Length": "3" }), 1024);
    expect(r).toMatchObject({ ok: false, code: "bad_utf8", status: 400 });
  });
  it("passes the cap failures through", async () => {
    const { body } = stream(5000);
    expect(await readBoundedText(req(body), 1024)).toMatchObject({ ok: false, code: "too_large" });
  });
});

describe("readBoundedBody: stalls and empty chunks", () => {
  it("many zero-length chunks are refused, not accumulated", async () => {
    let n = 0;
    const body = new ReadableStream<Uint8Array>({
      pull(c) {
        if (n++ < 100_000) c.enqueue(new Uint8Array(0));
        else c.close();
      },
    });
    const r = await readBoundedBody(req(body), 1024);
    expect(r).toMatchObject({ ok: false, code: "bad_stream", status: 400 });
    expect(n).toBeLessThan(100);
  });
  it("a few empty chunks between data are tolerated and dropped", async () => {
    const parts = [new Uint8Array(0), new Uint8Array([1, 2]), new Uint8Array(0), new Uint8Array([3])];
    const body = new ReadableStream<Uint8Array>({
      pull(c) {
        const p = parts.shift();
        if (p) c.enqueue(p);
        else c.close();
      },
    });
    const r = await readBoundedBody(req(body), 1024);
    expect(r.ok && Array.from(r.bytes)).toEqual([1, 2, 3]);
  });
  it("a pull that never settles returns timeout at the deadline", async () => {
    const body = new ReadableStream<Uint8Array>({ pull: () => new Promise(() => undefined) }, { highWaterMark: 0 });
    const r = await readBoundedBody(req(body), 1024, { deadlineMs: 30 });
    expect(r).toMatchObject({ ok: false, code: "timeout", status: 408 });
  });
  it("a cancel that never settles does not hold up the refusal", async () => {
    let sent = false;
    const body = new ReadableStream<Uint8Array>({
      pull(c) {
        if (!sent) {
          sent = true;
          c.enqueue(new Uint8Array(2000));
        }
      },
      cancel: () => new Promise(() => undefined),
    });
    const r = await readBoundedBody(req(body), 1024, { deadlineMs: 5000 });
    expect(r).toMatchObject({ ok: false, code: "too_large", status: 413 });
  });
  it("a header refusal with a never-settling cancel still returns", async () => {
    const body = new ReadableStream<Uint8Array>({ cancel: () => new Promise(() => undefined) });
    const r = await readBoundedBody(req(body, { "Content-Length": "999999" }), 1024);
    expect(r).toMatchObject({ ok: false, code: "too_large" });
  });
});

describe("readBoundedBody: chunk count is bounded", () => {
  it("thousands of one-byte chunks inside the byte cap are refused as a bad stream and cancelled", async () => {
    let cancelled = false;
    let n = 0;
    const body = new ReadableStream<Uint8Array>({
      pull(c) {
        n++;
        c.enqueue(new Uint8Array([97]));
      },
      cancel() {
        cancelled = true;
      },
    }, { highWaterMark: 0 });
    const r = await readBoundedBody(req(body), 65_536);
    expect(r).toMatchObject({ ok: false, code: "bad_stream", status: 400 });
    expect(cancelled).toBe(true);
    expect(n).toBeLessThanOrEqual(4100);
  });
  it("a body of many small chunks under the chunk limit is returned intact", async () => {
    let sent = 0;
    const body = new ReadableStream<Uint8Array>({
      pull(c) {
        if (sent >= 1000) return c.close();
        c.enqueue(new Uint8Array([sent++ % 256]));
      },
    }, { highWaterMark: 0 });
    const r = await readBoundedBody(req(body), 65_536);
    expect(r.ok && r.bytes.length).toBe(1000);
    if (r.ok) expect(r.bytes[257]).toBe(1);
  });
});

describe("readBoundedBody: cancellation happens on every refusal path", () => {
  function watched(make: (c: ReadableStreamDefaultController<Uint8Array>) => void): { body: ReadableStream<Uint8Array>; spy: { cancelled: number } } {
    const spy = { cancelled: 0 };
    const body = new ReadableStream<Uint8Array>({
      pull: make,
      cancel() {
        spy.cancelled++;
      },
    }, { highWaterMark: 0 });
    return { body, spy };
  }
  const chunk = (n: number) => (c: ReadableStreamDefaultController<Uint8Array>) => c.enqueue(new Uint8Array(n).fill(1));

  it("declared length over the cap", async () => {
    const { body, spy } = watched(chunk(1));
    await readBoundedBody(req(body, { "Content-Length": "5000" }), 1024);
    expect(spy.cancelled).toBe(1);
  });
  it("malformed declared length", async () => {
    const { body, spy } = watched(chunk(1));
    await readBoundedBody(req(body, { "Content-Length": "abc" }), 1024);
    expect(spy.cancelled).toBe(1);
  });
  it("missing length when required", async () => {
    const { body, spy } = watched(chunk(1));
    await readBoundedBody(req(body), 1024, { requireLength: true });
    expect(spy.cancelled).toBe(1);
  });
  it("streamed bytes over the cap", async () => {
    const { body, spy } = watched(chunk(512));
    await readBoundedBody(req(body), 1024);
    expect(spy.cancelled).toBe(1);
  });
  it("stalled stream at the deadline", async () => {
    const { body, spy } = watched(() => new Promise(() => undefined) as never);
    const r = await readBoundedBody(req(body), 1024, { deadlineMs: 20 });
    expect(r).toMatchObject({ code: "timeout" });
    expect(spy.cancelled).toBe(1);
  });
  it("too many empty chunks", async () => {
    const { body, spy } = watched((c) => c.enqueue(new Uint8Array(0)));
    await readBoundedBody(req(body), 1024);
    expect(spy.cancelled).toBe(1);
  });
  it("a chunk that is not bytes", async () => {
    const spy = { cancelled: 0 };
    const body = new ReadableStream<unknown>({
      pull(c) {
        c.enqueue("text");
      },
      cancel() {
        spy.cancelled++;
      },
    }, { highWaterMark: 0 }) as unknown as ReadableStream<Uint8Array>;
    const r = await readBoundedBody(req(body), 1024);
    expect(r).toMatchObject({ code: "aborted" });
    expect(spy.cancelled).toBe(1);
  });
});
