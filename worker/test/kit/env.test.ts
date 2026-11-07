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
  it("accepts TOKEN_TTL_SECONDS inside 60..168 h", () => {
    expect(load(env({ TOKEN_TTL_SECONDS: "3600" })).tokenTtlSeconds).toBe(3600);
  });
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
  it("is off by default and for unrecognized values", () => {
    for (const v of [undefined, "", "0", "false", "yes", "2", " "]) expect(isKitKilled({ KIT_KILL: v } as never)).toBe(false);
    expect(isKitKilled({} as never)).toBe(false);
  });
  it('engages on "1" or "true" in any case', () => {
    for (const v of ["1", "true", "TRUE", "True"]) expect(isKitKilled({ KIT_KILL: v } as never)).toBe(true);
  });
  it("is reported by loadKitEnv and does not stop it loading", () => {
    expect(load(env({ KIT_KILL: "1" })).killed).toBe(true);
  });
  it("is read on every call, not cached", () => {
    const e = { KIT_KILL: "0" } as { KIT_KILL: string };
    expect(isKitKilled(e as never)).toBe(false);
    e.KIT_KILL = "1";
    expect(isKitKilled(e as never)).toBe(true);
  });
});
