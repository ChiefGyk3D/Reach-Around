// SPDX-License-Identifier: Apache-2.0
import { afterEach, describe, expect, it, vi } from "vitest";
import { readCappedBytes } from "../../src/kit/streamread";

afterEach(() => vi.useRealTimers());

const mk = (src: UnderlyingSource<Uint8Array>) => new ReadableStream<Uint8Array>(src);
const bytes = (n: number) => new Uint8Array(n).fill(0x61);

describe("readCappedBytes", () => {
  it("reads a normal body", async () => {
    let sent = false;
    const out = await readCappedBytes(
      mk({
        pull(c) {
          if (sent) c.close();
          else {
            sent = true;
            c.enqueue(bytes(10));
          }
        },
      }),
      100,
      1000,
    );
    expect(out?.byteLength).toBe(10);
  });
  it("survives a cancel() that rejects, with no unhandled rejection", async () => {
    const body = mk({
      start(c) {
        c.enqueue(bytes(200));
      },
      cancel: () => Promise.reject(new Error("cancel failed")),
    });
    expect(await readCappedBytes(body, 100, 1000)).toBeNull();
    await new Promise((r) => setTimeout(r, 30));
    expect(body.locked).toBe(false);
  });
  it("survives a read that rejects after the deadline, with no unhandled rejection", async () => {
    const body = mk({ pull: () => new Promise<void>((_res, rej) => setTimeout(() => rej(new Error("late")), 60)) });
    expect(await readCappedBytes(body, 100, 20)).toBeNull();
    await new Promise((r) => setTimeout(r, 120));
    expect(body.locked).toBe(false);
  });
  it("handles a reader whose pending read rejects after the deadline", async () => {
    let released = 0;
    const reader = {
      read: () => new Promise<never>((_res, rej) => setTimeout(() => rej(new Error("late")), 50)),
      cancel: async () => undefined,
      releaseLock: () => {
        released++;
      },
    };
    const body = { getReader: () => reader } as unknown as ReadableStream<Uint8Array>;
    expect(await readCappedBytes(body, 100, 10)).toBeNull();
    expect(released).toBe(0); // still waiting on the read
    await new Promise((r) => setTimeout(r, 120));
    expect(released).toBe(1);
  });
  it("clears its deadline timer on every exit", async () => {
    vi.useFakeTimers();
    let sent = false;
    const normal = mk({
      pull(c) {
        if (sent) c.close();
        else {
          sent = true;
          c.enqueue(bytes(5));
        }
      },
    });
    await readCappedBytes(normal, 100, 5000);
    expect(vi.getTimerCount()).toBe(0);
    await readCappedBytes(mk({ start: (c) => c.enqueue(bytes(500)) }), 100, 5000);
    expect(vi.getTimerCount()).toBe(0);
    await readCappedBytes(mk({ pull: (c) => c.enqueue(new Uint8Array(0)) }), 100, 5000);
    expect(vi.getTimerCount()).toBe(0);
    await readCappedBytes(mk({ pull: () => Promise.reject(new Error("x")) }), 100, 5000);
    expect(vi.getTimerCount()).toBe(0);
  });
});
