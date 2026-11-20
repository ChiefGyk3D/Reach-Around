// SPDX-License-Identifier: Apache-2.0
// The short-lived signed beacon token (Refs #610): format, claims, window, key rotation, and that it is
// kept apart from the media-kit token. Synthetic keys and addresses only.
import { describe, expect, it } from "vitest";
import {
  BEACON_CLOCK_SKEW_SECONDS,
  BEACON_TTL_SECONDS,
  MAX_BEACON_TOKEN_LENGTH,
  clientHashFor,
  coarsePrefix,
  parseBeaconMode,
  signBeacon,
  verifyBeacon,
} from "../src/beacon";
import { signToken, verifyToken } from "../src/kit/token";
import { normalizeAddress } from "../src/address";

const K = "k".repeat(32);
const K_OLD = "o".repeat(32);
const K_OTHER = "z".repeat(32);
const IAT = 2_000_000_000;
const EXP = IAT + BEACON_TTL_SECONDS;
const CH = "0a1b2c3d";
const base = { sid: "links", ch: CH, iat: IAT, exp: EXP };

const enc = (s: string) => btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
async function mac(key: string, msg: string): Promise<string> {
  const k = await crypto.subtle.importKey("raw", new TextEncoder().encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(msg)));
  return enc(String.fromCharCode(...sig));
}
/** A correctly signed token around any payload value, to reach the claim checks behind the signature. */
async function forge(payload: unknown, key = K, version = "b1"): Promise<string> {
  const body = `${version}.${enc(JSON.stringify(payload))}`;
  return `${body}.${await mac(key, body)}`;
}
const good = (over: Record<string, unknown> = {}) => ({ ...base, jti: "A".repeat(22), ...over });

describe("round trip", () => {
  it("returns the site, the window and the client hash", async () => {
    const t = await signBeacon(K, base);
    const out = await verifyBeacon(K, t, IAT + 5);
    expect(out).toMatchObject(base);
    expect(t.startsWith("b1.")).toBe(true);
    expect(t.length).toBeLessThanOrEqual(MAX_BEACON_TOKEN_LENGTH);
  });
  it("gives every token its own random id", async () => {
    expect(await signBeacon(K, base)).not.toBe(await signBeacon(K, base));
  });
  it("refuses to sign with a short key or bad claims", async () => {
    await expect(signBeacon("short", base)).rejects.toThrow();
    await expect(signBeacon(K, { ...base, sid: "Bad Site" })).rejects.toThrow();
    await expect(signBeacon(K, { ...base, ch: "xyz" })).rejects.toThrow();
    await expect(signBeacon(K, { ...base, exp: IAT + BEACON_TTL_SECONDS + 1 })).rejects.toThrow();
  });
});

describe("the time window (table)", () => {
  const cases: [string, number, boolean][] = [
    ["at issue", IAT, true],
    ["one second before expiry", EXP - 1, true],
    ["at expiry", EXP, false],
    ["after expiry", EXP + 1, false],
    ["issued 60 s in the future (skew allowed)", IAT - BEACON_CLOCK_SKEW_SECONDS, true],
    ["issued 61 s in the future", IAT - BEACON_CLOCK_SKEW_SECONDS - 1, false],
  ];
  for (const [name, now, ok] of cases) {
    it(name, async () => {
      const out = await verifyBeacon(K, await signBeacon(K, base), now);
      expect(out !== null).toBe(ok);
    });
  }
  it("is not a number it can be fooled with", async () => {
    const t = await signBeacon(K, base);
    for (const bad of [Number.NaN, 1.5, Infinity]) expect(await verifyBeacon(K, t, bad)).toBeNull();
  });
});

describe("claims behind a valid signature (table)", () => {
  const cases: [string, unknown][] = [
    ["lifetime over the cap", good({ exp: IAT + BEACON_TTL_SECONDS + 1 })],
    ["exp not after iat", good({ exp: IAT })],
    ["site id with capitals", good({ sid: "Links" })],
    ["site id too long", good({ sid: "a".repeat(21) })],
    ["client hash wrong length", good({ ch: "abc" })],
    ["client hash not hex", good({ ch: "ZZZZZZZZ" })],
    ["no id", { sid: "links", ch: CH, iat: IAT, exp: EXP }],
    ["bad id", good({ jti: "short" })],
    ["extra claim", good({ extra: 1 })],
    ["iat as text", good({ iat: String(IAT) })],
    ["a list", [good()]],
    ["null", null],
  ];
  for (const [name, payload] of cases) {
    it(`refuses ${name}`, async () => {
      expect(await verifyBeacon(K, await forge(payload), IAT + 1)).toBeNull();
    });
  }
  it("accepts the same claims when they are well formed (the table's control)", async () => {
    expect(await verifyBeacon(K, await forge(good()), IAT + 1)).not.toBeNull();
  });
});

describe("malformed and hostile tokens (table)", () => {
  it("each is refused without throwing", async () => {
    const t = await signBeacon(K, base);
    const [v, p, s] = t.split(".") as [string, string, string];
    const cases: [string, string][] = [
      ["empty", ""],
      ["one part", "b1"],
      ["two parts", `b1.${p}`],
      ["four parts", `${t}.x`],
      ["duplicate dots", `b1..${s}`],
      ["empty payload", `b1..${s}`],
      ["empty signature", `b1.${p}.`],
      ["wrong version", `v1.${p}.${s}`],
      ["upper-case version", `B1.${p}.${s}`],
      ["padded payload", `b1.${p}=.${s}`],
      ["non-canonical signature", `b1.${p}.${s.slice(0, -1)}${s.endsWith("A") ? "B" : "A"}`],
      ["flipped payload", `b1.${p.slice(0, -2)}${p.endsWith("AA") ? "BB" : "AA"}.${s}`],
      ["tampered signature", `b1.${p}.${s.slice(1)}${s[0] === "A" ? "B" : "A"}`],
      ["standard base64 characters", `b1.${p.replace(/-/g, "+")}+/.${s}`],
      ["spaces", ` ${t}`],
      ["newline", `${t}\n`],
      ["version only differs by case in signature input", `${v.toUpperCase()}.${p}.${s}`],
    ];
    for (const [name, token] of cases) expect(await verifyBeacon(K, token, IAT + 1), name).toBeNull();
  });
  it("an oversized token is refused before any work, even if it would verify", async () => {
    const big = await forge(good({ pad: "x".repeat(2000) }));
    expect(big.length).toBeGreaterThan(MAX_BEACON_TOKEN_LENGTH);
    expect(await verifyBeacon(K, big, IAT + 1)).toBeNull();
    expect(await verifyBeacon(K, "b1." + "A".repeat(100_000), IAT + 1)).toBeNull();
  });
  it("non-string input is refused", async () => {
    expect(await verifyBeacon(K, undefined as unknown as string, IAT)).toBeNull();
    expect(await verifyBeacon(K, 5 as unknown as string, IAT)).toBeNull();
  });
});

describe("key rotation (table)", () => {
  it("accepts the current and the previous key, refuses an unknown one", async () => {
    const withOld = await signBeacon(K_OLD, base);
    const withCurrent = await signBeacon(K, base);
    const withOther = await signBeacon(K_OTHER, base);
    expect(await verifyBeacon([K, K_OLD], withOld, IAT + 1)).not.toBeNull();
    expect(await verifyBeacon([K, K_OLD], withCurrent, IAT + 1)).not.toBeNull();
    expect(await verifyBeacon([K, K_OLD], withOther, IAT + 1)).toBeNull();
    expect(await verifyBeacon(K, withOld, IAT + 1)).toBeNull();
  });
  it("tells the caller which key matched, so the client hash is recomputed under it", async () => {
    const withOld = await signBeacon(K_OLD, base);
    expect((await verifyBeacon([K, K_OLD], withOld, IAT + 1))?.key).toBe(K_OLD);
  });
  it("ignores unusable keys and refuses when none is usable", async () => {
    const t = await signBeacon(K, base);
    expect(await verifyBeacon(["short", K], t, IAT + 1)).not.toBeNull();
    expect(await verifyBeacon(["short", ""], t, IAT + 1)).toBeNull();
    expect(await verifyBeacon([], t, IAT + 1)).toBeNull();
  });
});

describe("kept apart from the media-kit token", () => {
  it("a kit token is not a beacon token, with the same key text", async () => {
    const kit = await signToken(K, { lid: "a1b2c3", iat: IAT, exp: EXP, cc: "US" });
    expect(await verifyBeacon(K, kit, IAT + 1)).toBeNull();
  });
  it("a beacon token is not a kit token, with the same key text", async () => {
    const beacon = await signBeacon(K, base);
    expect(await verifyToken(K, beacon, IAT + 1)).toBeNull();
  });
  it("even a kit-shaped token whose version is relabeled does not verify (the signature covers the version)", async () => {
    const kit = await signToken(K, { lid: "a1b2c3", iat: IAT, exp: EXP, cc: "US" });
    expect(await verifyBeacon(K, `b1.${kit.slice(3)}`, IAT + 1)).toBeNull();
  });
});

describe("the coarse client hash", () => {
  const prefix = (ip: string) => coarsePrefix(normalizeAddress(ip));
  it("cuts IPv4 to a /24 and IPv6 to a /48", () => {
    expect(prefix("198.51.100.7")).toBe(prefix("198.51.100.200"));
    expect(prefix("198.51.100.7")).not.toBe(prefix("198.51.101.7"));
    expect(prefix("2001:db8:1:1::7")).toBe(prefix("2001:db8:1:ffff::9"));
    expect(prefix("2001:db8:1::7")).not.toBe(prefix("2001:db8:2::7"));
    expect(prefix("198.51.100.7")).toBe(prefix("::ffff:198.51.100.9"));
  });
  it("is null with no usable address", () => {
    expect(coarsePrefix(null)).toBeNull();
    expect(coarsePrefix("unknown")).toBeNull();
  });
  it("is 8 hex digits, depends on the key and the prefix, and is null without a prefix", async () => {
    const a = await clientHashFor(K, normalizeAddress("198.51.100.7"));
    expect(a).toMatch(/^[0-9a-f]{8}$/);
    expect(await clientHashFor(K, normalizeAddress("198.51.100.99"))).toBe(a);
    expect(await clientHashFor(K, normalizeAddress("198.51.101.7"))).not.toBe(a);
    expect(await clientHashFor(K_OTHER, normalizeAddress("198.51.100.7"))).not.toBe(a);
    expect(await clientHashFor(K, null)).toBeNull();
  });
});

describe("BEACON_MODE", () => {
  it("unset and empty are off; the three words are exact; anything else is unusable", () => {
    expect(parseBeaconMode(undefined)).toBe("off");
    expect(parseBeaconMode("")).toBe("off");
    expect(parseBeaconMode("off")).toBe("off");
    expect(parseBeaconMode("observe")).toBe("observe");
    expect(parseBeaconMode("enforce")).toBe("enforce");
    for (const bad of ["Observe", "ENFORCE", " observe", "observe ", "on", "1", "true", "enforced", 1, null])
      expect(parseBeaconMode(bad), String(bad)).toBeNull();
  });
});
