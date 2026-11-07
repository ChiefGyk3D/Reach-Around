// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it, vi } from "vitest";
import { createLogger, logEvent } from "../../src/kit/log";

const SENTINELS = ["victim@example.test", "tok_SENTINEL_9f8e7d", "203.0.113.77", "SentinelBrowser/1.0"];

function make(over: { max?: number; perRequest?: number } = {}) {
  const lines: string[] = [];
  let t = 1_000_000;
  const logger = createLogger({
    sink: (s) => lines.push(s),
    now: () => t,
    maxPerMinute: over.max,
    maxPerRequest: over.perRequest,
  });
  return { logger, lines, advance: (ms: number) => (t += ms) };
}

describe("line shape", () => {
  it("is the JSON of exactly e, o, r (and n for purge/log), at most 120 bytes", () => {
    const { logger, lines } = make();
    logger.log("gate", "rejected", "origin");
    logger.log("purge", "ok", undefined, 42);
    expect(lines.length).toBe(2);
    expect(JSON.parse(lines[0])).toEqual({ e: "gate", o: "rejected", r: "origin" });
    expect(JSON.parse(lines[1])).toEqual({ e: "purge", o: "ok", n: 42 });
    for (const l of lines) expect(new TextEncoder().encode(l).length).toBeLessThanOrEqual(120);
  });
  it("unknown values become other and never appear verbatim", () => {
    const { logger, lines } = make();
    const hostile = [SENTINELS[0], "x".repeat(10_240), "__proto__", { toString: () => SENTINELS[1] }, 7, null, undefined];
    for (const h of hostile) logger.log(h as never, h as never, h as never, h as never);
    const all = lines.join("\n");
    for (const s of SENTINELS) expect(all).not.toContain(s);
    expect(all).not.toContain("__proto__");
    expect(all.length).toBeLessThan(2000);
    for (const l of lines) {
      const o = JSON.parse(l);
      expect(o.e).toBe("log");
      expect(o.o).toBe("failed");
    }
  });
  it("n is kept only for purge and log, and only as a small non-negative integer", () => {
    const { logger, lines } = make({ max: 600 });
    logger.log("gate", "ok", undefined, 5);
    for (const n of [-1, 1.5, Number.NaN, Infinity, "7", 10_000_000_000, null]) logger.log("purge", "ok", undefined, n as never);
    logger.log("purge", "ok", undefined, 0);
    logger.log("purge", "ok", undefined, 9);
    const parsed = lines.map((l) => JSON.parse(l));
    expect(parsed[0]).not.toHaveProperty("n");
    expect(parsed.slice(1, 8).every((p) => !("n" in p))).toBe(true);
    expect(parsed[8].n).toBe(0);
    expect(parsed[9].n).toBe(9);
  });
  it("prototype pollution style keys do not become enum values", () => {
    const { logger, lines } = make();
    logger.log("constructor" as never, "toString" as never, "hasOwnProperty" as never);
    expect(lines.join()).not.toMatch(/constructor|toString|hasOwnProperty/);
  });
});

describe("caps", () => {
  it("a flood of hostile calls emits at most the per-minute cap, from a small distinct set", () => {
    const { logger, lines } = make({ max: 60 });
    for (let i = 0; i < 10_000; i++) logger.log(`${SENTINELS[0]}${i}` as never, `${i}` as never, `${i}x` as never, i as never);
    expect(lines.length).toBeLessThanOrEqual(60);
    expect(new Set(lines).size).toBeLessThanOrEqual(64);
  });
  it("after the minute rolls, one dropped summary with the exact count, then normal lines resume", () => {
    const { logger, lines, advance } = make({ max: 5 });
    for (let i = 0; i < 20; i++) logger.log("gate", "ok");
    expect(lines.length).toBe(5);
    advance(60_001);
    logger.log("gate", "ok");
    expect(JSON.parse(lines[5])).toEqual({ e: "log", o: "dropped", n: 15 });
    expect(JSON.parse(lines[6])).toEqual({ e: "gate", o: "ok" });
    expect(lines.length).toBe(7);
    advance(60_001);
    logger.log("gate", "ok");
    expect(lines.length).toBe(8);
  });
  it("the cap is clamped into 1..600", () => {
    const lo = make({ max: 0 });
    for (let i = 0; i < 10; i++) lo.logger.log("gate", "ok");
    expect(lo.lines.length).toBe(1);
    const hi = make({ max: 100_000 });
    for (let i = 0; i < 1000; i++) hi.logger.log("gate", "ok");
    expect(hi.lines.length).toBe(600);
    const bad = make({ max: Number.NaN });
    for (let i = 0; i < 1000; i++) bad.logger.log("gate", "ok");
    expect(bad.lines.length).toBe(60);
  });
  it("budget and kill events are emitted at most once per minute each", () => {
    const { logger, lines, advance } = make({ max: 600 });
    for (let i = 0; i < 100; i++) {
      logger.log("budget", "over_budget", "route_budget");
      logger.log("kill", "closed", "kill");
    }
    expect(lines.length).toBe(2);
    advance(60_001);
    logger.log("budget", "over_budget", "route_budget");
    expect(lines.length).toBe(3);
  });
  it("a request logger stops after its own small line cap", () => {
    const { logger, lines } = make({ max: 600, perRequest: 3 });
    const r = logger.forRequest();
    for (let i = 0; i < 50; i++) r.log("gate", "rejected", "origin");
    expect(lines.length).toBe(3);
    const r2 = logger.forRequest();
    r2.log("send", "ok");
    expect(lines.length).toBe(4);
  });
  it("request-cap lines suppressed are not counted as dropped noise beyond the minute cap", () => {
    const { logger, lines } = make({ max: 600, perRequest: 1 });
    const r = logger.forRequest();
    r.log("gate", "ok");
    r.log("gate", "ok");
    expect(lines.length).toBe(1);
  });
});

describe("suppressed calls do no work", () => {
  it("after the minute cap, no validation lookup or serialization happens", () => {
    const { logger } = make({ max: 2 });
    logger.log("gate", "ok");
    logger.log("gate", "ok");
    const has = vi.spyOn(Set.prototype, "has");
    const str = vi.spyOn(JSON, "stringify");
    try {
      for (let i = 0; i < 1000; i++) logger.log("x".repeat(10_000) as never, "y" as never, "z" as never, i);
      expect(has.mock.calls.filter((c) => typeof c[0] === "string" && c[0].length > 16)).toEqual([]);
      expect(str).not.toHaveBeenCalled();
    } finally {
      has.mockRestore();
      str.mockRestore();
    }
  });
  it("after the per-request cap, no validation lookup happens", () => {
    const { logger } = make({ max: 600, perRequest: 1 });
    const r = logger.forRequest();
    r.log("gate", "ok");
    const has = vi.spyOn(Set.prototype, "has");
    try {
      for (let i = 0; i < 1000; i++) r.log("gate", "ok");
      expect(has).not.toHaveBeenCalled();
    } finally {
      has.mockRestore();
    }
  });
  it("an over-long string is rejected by length before any set lookup", () => {
    const { logger, lines } = make();
    const has = vi.spyOn(Set.prototype, "has");
    try {
      logger.log("q".repeat(100_000) as never, "q".repeat(100_000) as never, "q".repeat(100_000) as never);
      expect(has.mock.calls.filter((c) => typeof c[0] === "string" && c[0].length > 16)).toEqual([]);
    } finally {
      has.mockRestore();
    }
    expect(lines.length).toBe(1);
  });
  it("the shared logEvent stops after its per-minute cap without further sink calls", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(Date.now() + 3_600_000);
    const log = vi.spyOn(console, "log").mockImplementation(() => {});
    try {
      for (let i = 0; i < 5000; i++) logEvent("gate", "rejected", "origin");
      expect(log.mock.calls.length).toBeLessThanOrEqual(61);
    } finally {
      vi.useRealTimers();
      log.mockRestore();
    }
  });
});

describe("no personal data reaches the sink", () => {
  it("an Error carrying an address, token, IP and user agent produces no such text", () => {
    const { logger, lines } = make();
    const err = new Error(`${SENTINELS.join(" ")}`);
    try {
      throw err;
    } catch (e) {
      logger.log("gate", "failed", "other", e as never);
      logger.log("gate", e as never, e as never);
      logger.logError("send", e);
      logger.forRequest().logError("view", e);
    }
    const all = lines.join("\n");
    for (const s of SENTINELS) expect(all).not.toContain(s);
    expect(all).not.toContain("@");
    expect(lines.length).toBeGreaterThan(0);
  });
  it("the default logger writes only through console.log, never error or warn", () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => {});
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(Date.now() + 7_200_000);
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      logEvent("gate", "rejected", "origin");
      logEvent(SENTINELS[0] as never, SENTINELS[1] as never, SENTINELS[2] as never);
      expect(log).toHaveBeenCalled();
      expect(err).not.toHaveBeenCalled();
      expect(warn).not.toHaveBeenCalled();
      for (const c of log.mock.calls) for (const s of SENTINELS) expect(String(c[0])).not.toContain(s);
    } finally {
      vi.useRealTimers();
      log.mockRestore();
      err.mockRestore();
      warn.mockRestore();
    }
  });
});
