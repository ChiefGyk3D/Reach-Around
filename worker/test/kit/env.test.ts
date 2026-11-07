// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { DEFAULT_TOKEN_TTL_SECONDS, KitEnvError, isKitKilled, loadKitEnv } from "../../src/kit/env";

const KEY = "k".repeat(32);
const PREV = "p".repeat(32);
const stub = {} as unknown;

function env(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return { KIT: stub, GATE_LIMITER: { limit: async () => ({ success: true }) }, TOKEN_HMAC_KEY: KEY, TURNSTILE_SECRET: "t".repeat(16), ...extra };
}
const load = (e: Record<string, unknown>) => loadKitEnv(e as never);

describe("loadKitEnv", () => {
  it("returns defaults: 24 h lifetime, kill off, no previous key", () => {
    const c = load(env());
    expect(c.tokenTtlSeconds).toBe(DEFAULT_TOKEN_TTL_SECONDS);
    expect(DEFAULT_TOKEN_TTL_SECONDS).toBe(86_400);
    expect(c.killed).toBe(false);
    expect(c.keys).toEqual([KEY]);
  });
  it("puts the previous key second, after the current one", () => {
    expect(load(env({ TOKEN_HMAC_KEY_PREVIOUS: PREV })).keys).toEqual([KEY, PREV]);
  });
  it("treats an empty previous key as unset", () => {
    expect(load(env({ TOKEN_HMAC_KEY_PREVIOUS: "" })).keys).toEqual([KEY]);
  });
  it.each([["3600", 3600], ["86400", 86_400], ["604800", 604_800]])("accepts TOKEN_TTL_SECONDS %s (1 h to 168 h)", (v, n) => {
    expect(load(env({ TOKEN_TTL_SECONDS: v })).tokenTtlSeconds).toBe(n);
  });
  it.each(["3599", "60", "604801"])("rejects TOKEN_TTL_SECONDS %s (outside 1 h to 168 h)", (v) => {
    expect(() => load(env({ TOKEN_TTL_SECONDS: v }))).toThrow(KitEnvError);
  });
  it("SEND_LIMITER is optional in v1: absent loads fine", () => {
    expect(() => load(env())).not.toThrow();
    expect(() => load(env({ SEND_LIMITER: undefined }))).not.toThrow();
  });
  it("SEND_LIMITER with a valid shape loads", () => {
    expect(() => load(env({ SEND_LIMITER: { limit: async () => ({ success: true }) } }))).not.toThrow();
  });
  it.each([["empty object", {}], ["string", "x"], ["null", null], ["limit not a function", { limit: 1 }]])(
    "SEND_LIMITER present but malformed (%s) is rejected by name",
    (_n, v) => {
      let err: unknown;
      try {
        load(env({ SEND_LIMITER: v }));
      } catch (e) {
        err = e;
      }
      expect(err).toBeInstanceOf(KitEnvError);
      expect((err as KitEnvError).fields).toEqual(["SEND_LIMITER"]);
    },
  );
});

describe("fails closed", () => {
  const bad: [string, Record<string, unknown>][] = [
    ["missing key", { TOKEN_HMAC_KEY: undefined }],
    ["empty key", { TOKEN_HMAC_KEY: "" }],
    ["31 byte key", { TOKEN_HMAC_KEY: "k".repeat(31) }],
    ["non-string key", { TOKEN_HMAC_KEY: 12345 }],
    ["short previous key", { TOKEN_HMAC_KEY_PREVIOUS: "p".repeat(31) }],
    ["missing KIT binding", { KIT: undefined }],
    ["missing GATE_LIMITER", { GATE_LIMITER: undefined }],
    ["limiter without limit()", { GATE_LIMITER: {} }],
    ["missing Turnstile secret", { TURNSTILE_SECRET: undefined }],
    ["empty Turnstile secret", { TURNSTILE_SECRET: "" }],
    ["ttl not a number", { TOKEN_TTL_SECONDS: "soon" }],
    ["ttl zero", { TOKEN_TTL_SECONDS: "0" }],
    ["ttl negative", { TOKEN_TTL_SECONDS: "-5" }],
    ["ttl fractional", { TOKEN_TTL_SECONDS: "10.5" }],
    ["ttl over 168 h", { TOKEN_TTL_SECONDS: String(168 * 3600 + 1) }],
    ["ttl with trailing junk", { TOKEN_TTL_SECONDS: "60abc" }],
  ];
  it.each(bad)("%s", (_n, patch) => {
    expect(() => load(env(patch))).toThrow(KitEnvError);
  });
  it("a missing env object refuses to start", () => {
    expect(() => loadKitEnv(undefined as never)).toThrow(KitEnvError);
  });
  it("the error names fields only and never echoes a secret value", () => {
    const secrets = ["short-gate-key-123", "tsecret-VALUE-xyz", "prev-secret-VALUE"];
    let err: unknown;
    try {
      load(env({ TOKEN_HMAC_KEY: secrets[0], TURNSTILE_SECRET: secrets[1], TOKEN_HMAC_KEY_PREVIOUS: secrets[2], TOKEN_TTL_SECONDS: "junk-SECRET" }));
    } catch (e) {
      err = e;
    }
    expect(err).toBeInstanceOf(KitEnvError);
    const text = `${(err as Error).message} ${(err as KitEnvError).fields.join(",")} ${JSON.stringify(err)} ${(err as Error).stack}`;
    for (const s of [...secrets, "junk-SECRET"]) expect(text).not.toContain(s);
    expect((err as KitEnvError).fields).toContain("TOKEN_HMAC_KEY");
    expect((err as Error).message).toContain("TOKEN_HMAC_KEY");
  });
});

describe("kill switch", () => {
  // Policy: unset or "" is off. Otherwise the value must be an explicit form,
  // matched exactly (no trimming), any case. Anything else is invalid.
  const TRUE_FORMS = ["1", "true", "TRUE", "True", "on", "ON", "yes", "Yes"];
  const FALSE_FORMS = ["0", "false", "FALSE", "False", "off", "OFF", "no", "No"];
  const INVALID = ["treu", "True ", " true", "2", " ", "\t1", "1\n", "tru", "yes!", "enabled", "-1", "00", "null"];

  it("is off when unset or empty", () => {
    for (const v of [undefined, ""]) expect(isKitKilled({ KIT_KILL: v } as never)).toBe(false);
    expect(isKitKilled({} as never)).toBe(false);
    expect(load(env()).killed).toBe(false);
    expect(load(env({ KIT_KILL: "" })).killed).toBe(false);
  });
  it.each(TRUE_FORMS)("true form %j engages", (v) => {
    expect(isKitKilled({ KIT_KILL: v } as never)).toBe(true);
    expect(load(env({ KIT_KILL: v })).killed).toBe(true);
  });
  it.each(FALSE_FORMS)("false form %j is off", (v) => {
    expect(isKitKilled({ KIT_KILL: v } as never)).toBe(false);
    expect(load(env({ KIT_KILL: v })).killed).toBe(false);
  });
  it.each(INVALID)("malformed value %j: loadKitEnv rejects, isKitKilled fails closed", (v) => {
    expect(() => load(env({ KIT_KILL: v }))).toThrow(KitEnvError);
    expect(isKitKilled({ KIT_KILL: v } as never)).toBe(true);
  });
  it("non-string values are malformed too", () => {
    for (const v of [1, true, 0, null, {}, []]) {
      expect(() => load(env({ KIT_KILL: v }))).toThrow(KitEnvError);
      expect(isKitKilled({ KIT_KILL: v } as never)).toBe(true);
    }
  });
  it("the rejection names the variable and never echoes the value", () => {
    let err: unknown;
    try {
      load(env({ KIT_KILL: "treu-SECRETISH" }));
    } catch (e) {
      err = e;
    }
    expect((err as KitEnvError).fields).toEqual(["KIT_KILL"]);
    expect((err as Error).message).toContain("KIT_KILL");
    expect(`${(err as Error).message} ${JSON.stringify(err)}`).not.toContain("SECRETISH");
  });
  it("is read on every call, not cached", () => {
    const e = { KIT_KILL: "0" } as { KIT_KILL: string };
    expect(isKitKilled(e as never)).toBe(false);
    e.KIT_KILL = "1";
    expect(isKitKilled(e as never)).toBe(true);
  });
});
