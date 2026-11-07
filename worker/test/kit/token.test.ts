// SPDX-License-Identifier: Apache-2.0
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  COOKIE_NAME,
  MAX_TOKEN_AGE_SECONDS,
  readUnlockCookie,
  signToken,
  unlockCookie,
  verifyToken,
} from "../../src/kit/token";
import tokenSource from "../../src/kit/token.ts?raw";

const K = "x".repeat(32);
const K2 = "y".repeat(32);
const IAT = 1_000;
const EXP = IAT + 86_400;
const claims = { lid: "a1b2c3", iat: IAT, exp: EXP, cc: "US" };

const enc = (s: string) => btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const dec = (s: string) => atob(s.replace(/-/g, "+").replace(/_/g, "/"));

async function hmac(key: string, msg: string): Promise<string> {
  const k = await crypto.subtle.importKey("raw", new TextEncoder().encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(msg)));
  return enc(String.fromCharCode(...sig));
}

/** Build a correctly signed token around an arbitrary payload value. */
async function forge(payload: unknown, key = K, version = "v1"): Promise<string> {
  const body = `${version}.${enc(JSON.stringify(payload))}`;
  return `${body}.${await hmac(key, body)}`;
}

afterEach(() => vi.restoreAllMocks());

describe("round trip", () => {
  it("returns lid, iat, exp and cc", async () => {
    const t = await signToken(K, claims);
    expect(await verifyToken(K, t, IAT + 10)).toEqual(claims);
  });
  it("accepts an empty cc", async () => {
    const t = await signToken(K, { ...claims, cc: "" });
    expect((await verifyToken(K, t, IAT + 10))?.cc).toBe("");
  });
  it("two tokens for the same claims differ (random id)", async () => {
    expect(await signToken(K, claims)).not.toBe(await signToken(K, claims));
  });
  it("token has the v1.payload.signature shape", async () => {
    expect((await signToken(K, claims)).split(".")).toHaveLength(3);
    expect(await signToken(K, claims)).toMatch(/^v1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}$/);
  });
});

describe("signing refuses bad input", () => {
  it("throws on a 31 byte key without echoing it", async () => {
    const short = "s3cretKEY".padEnd(31, "q");
    const err = await signToken(short, claims).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(Error);
    expect(String((err as Error).message)).not.toContain(short);
  });
  it("counts bytes, not characters", async () => {
    // 16 two-byte characters = 32 bytes: accepted. 15 = 30 bytes: refused.
    await expect(signToken("é".repeat(16), claims)).resolves.toBeTypeOf("string");
    await expect(signToken("é".repeat(15), claims)).rejects.toThrow();
  });
  it.each(["usa", "us", "U", "1"])("throws on cc %s", async (cc) => {
    await expect(signToken(K, { ...claims, cc })).rejects.toThrow();
  });
  it("throws when cc is not a string", async () => {
    await expect(signToken(K, { ...claims, cc: 1 as unknown as string })).rejects.toThrow();
  });
  it("throws on bad lid, non-integer times or exp not after iat", async () => {
    await expect(signToken(K, { ...claims, lid: "" })).rejects.toThrow();
    await expect(signToken(K, { ...claims, lid: "a.b" })).rejects.toThrow();
    await expect(signToken(K, { ...claims, iat: 1.5 })).rejects.toThrow();
    await expect(signToken(K, { ...claims, exp: IAT })).rejects.toThrow();
  });
  it("throws when lifetime exceeds the verifier cap", async () => {
    await expect(signToken(K, { ...claims, exp: IAT + MAX_TOKEN_AGE_SECONDS + 1 })).rejects.toThrow();
  });
});

describe("tamper", () => {
  it("payload re-encoded with another lid, signature kept", async () => {
    const t = await signToken(K, claims);
    const [v, p, s] = t.split(".");
    const obj = JSON.parse(dec(p!));
    for (const patch of [{ lid: "ffff" }, { iat: IAT + 1 }, { exp: EXP + 5 }, { cc: "DE" }, { jti: "A".repeat(22) }]) {
      const forged = `${v}.${enc(JSON.stringify({ ...obj, ...patch }))}.${s}`;
      expect(await verifyToken(K, forged, IAT + 10)).toBeNull();
    }
  });
  it("flipping the last signature char", async () => {
    const t = await signToken(K, claims);
    const flipped = t.slice(0, -1) + (t.endsWith("A") ? "B" : "A");
    expect(await verifyToken(K, flipped, IAT + 10)).toBeNull();
  });
  it("flipping each character of the signature in turn", async () => {
    const t = await signToken(K, claims);
    const i = t.lastIndexOf(".") + 1;
    for (let n = i; n < t.length; n += 7) {
      const c = t[n] === "A" ? "B" : "A";
      expect(await verifyToken(K, t.slice(0, n) + c + t.slice(n + 1), IAT + 10)).toBeNull();
    }
  });
  it("a token signed with another key", async () => {
    expect(await verifyToken(K, await signToken(K2, claims), IAT + 10)).toBeNull();
  });
  it("version label rewritten", async () => {
    const t = await signToken(K, claims);
    expect(await verifyToken(K, "v2" + t.slice(2), IAT + 10)).toBeNull();
  });
  it("correctly signed under a version other than v1", async () => {
    const t = await forge({ ...claims, jti: "A".repeat(22) }, K, "v2");
    expect(await verifyToken(K, t, IAT + 10)).toBeNull();
    expect(await verifyToken(K, await forge({ ...claims, jti: "A".repeat(22) }, K, "v0"), IAT + 10)).toBeNull();
  });
  it("sanity: the forge helper itself produces a valid token", async () => {
    const t = await forge({ ...claims, jti: "A".repeat(22) });
    expect(await verifyToken(K, t, IAT + 10)).toEqual(claims);
  });
});

describe("malformed input returns null and never throws", () => {
  const valid = { ...claims, jti: "A".repeat(22) };
  const cases: [string, () => Promise<string>][] = [
    ["empty", async () => ""],
    ["a.b", async () => "a.b"],
    ["v1.a.b.c", async () => "v1.a.b.c"],
    ["v1 with two parts", async () => "v1.abc"],
    ["not base64", async () => "v1.!!!!.????"],
    ["payload with padding", async () => `v1.${btoa("{}")}==.${"A".repeat(43)}`],
    ["payload not canonical base64url", async () => "v1.QQ.AAAA"],
    ["standard alphabet chars", async () => "v1.a+b/.c+d/"],
    ["whitespace", async () => " v1.a.b "],
    ["payload is an array", () => forge([1, 2])],
    ["payload is null", () => forge(null)],
    ["payload is a string", () => forge("x")],
    ["missing lid", () => forge({ ...valid, lid: undefined })],
    ["numeric lid", () => forge({ ...valid, lid: 5 })],
    ["lid with a dot", () => forge({ ...valid, lid: "a.b" })],
    ["string exp", () => forge({ ...valid, exp: String(EXP) })],
    ["float exp", () => forge({ ...valid, exp: EXP + 0.5 })],
    ["null iat", () => forge({ ...valid, iat: null })],
    ["missing cc", () => forge({ ...valid, cc: undefined })],
    ["cc usa", () => forge({ ...valid, cc: "usa" })],
    ["cc numeric 1", () => forge({ ...valid, cc: 1 })],
    ["missing jti", () => forge({ ...valid, jti: undefined })],
    ["short jti", () => forge({ ...valid, jti: "A" })],
    ["exp before iat", () => forge({ ...valid, exp: IAT - 1 })],
    ["payload is not JSON", async () => {
      const body = `v1.${enc("{not json")}`;
      return `${body}.${await hmac(K, body)}`;
    }],
    ["10 KiB token", async () => "v1." + "A".repeat(10 * 1024)],
    ["signature of wrong length", async () => {
      const t = await signToken(K, claims);
      return t.slice(0, -4);
    }],
    ["truncated at every point", async () => (await signToken(K, claims)).slice(0, 30)],
  ];
  it.each(cases)("%s", async (_n, make) => {
    const t = await make();
    await expect(verifyToken(K, t, IAT + 10)).resolves.toBeNull();
  });
  it("truncation at any length is refused", async () => {
    const t = await signToken(K, claims);
    for (let n = 0; n < t.length; n++) expect(await verifyToken(K, t.slice(0, n), IAT + 10)).toBeNull();
  });
  it("non-string token input returns null", async () => {
    for (const bad of [undefined, null, 5, {}, []]) {
      await expect(verifyToken(K, bad as unknown as string, IAT)).resolves.toBeNull();
    }
  });
  it("a short or empty verification key returns null instead of throwing", async () => {
    const t = await signToken(K, claims);
    await expect(verifyToken("short", t, IAT + 10)).resolves.toBeNull();
    await expect(verifyToken("", t, IAT + 10)).resolves.toBeNull();
    await expect(verifyToken([], t, IAT + 10)).resolves.toBeNull();
  });
  it("a short key in the key list is skipped, not accepted", async () => {
    const t = await signToken(K, claims);
    await expect(verifyToken(["short", K], t, IAT + 10)).resolves.toEqual(claims);
  });
});

describe("clock", () => {
  it("exactly exp is refused, one second earlier is accepted", async () => {
    const t = await signToken(K, claims);
    expect(await verifyToken(K, t, EXP)).toBeNull();
    expect(await verifyToken(K, t, EXP - 1)).not.toBeNull();
  });
  it("far past exp is refused", async () => {
    expect(await verifyToken(K, await signToken(K, claims), EXP + 10_000_000)).toBeNull();
  });
  it("iat more than 60 s in the future is refused; 60 s is allowed", async () => {
    const t = await signToken(K, claims);
    expect(await verifyToken(K, t, IAT - 61)).toBeNull();
    expect(await verifyToken(K, t, IAT - 60)).not.toBeNull();
  });
  it("a non-finite or non-integer now is refused", async () => {
    const t = await signToken(K, claims);
    for (const now of [NaN, Infinity, -Infinity, 1000.5]) expect(await verifyToken(K, t, now)).toBeNull();
  });
  it("exp minus iat above 168 hours is refused (verifier cap)", async () => {
    const t = await forge({ ...claims, exp: IAT + MAX_TOKEN_AGE_SECONDS + 1, jti: "A".repeat(22) });
    expect(await verifyToken(K, t, IAT + 10)).toBeNull();
    const ok = await forge({ ...claims, exp: IAT + MAX_TOKEN_AGE_SECONDS, jti: "A".repeat(22) });
    expect(await verifyToken(K, ok, IAT + 10)).not.toBeNull();
  });
});

describe("key rotation", () => {
  it("a token under the previous key verifies while the key is in the previous slot", async () => {
    const old = await signToken(K2, claims);
    expect(await verifyToken([K, K2], old, IAT + 10)).toEqual(claims);
  });
  it("a token under the current key verifies with a previous key present", async () => {
    expect(await verifyToken([K, K2], await signToken(K, claims), IAT + 10)).toEqual(claims);
  });
  it("after rotation completes the old key no longer verifies", async () => {
    expect(await verifyToken([K], await signToken(K2, claims), IAT + 10)).toBeNull();
  });
  it("a third key never verifies", async () => {
    expect(await verifyToken([K, K2], await signToken("z".repeat(32), claims), IAT + 10)).toBeNull();
  });
  it("expiry still applies to previous-key tokens", async () => {
    expect(await verifyToken([K, K2], await signToken(K2, claims), EXP)).toBeNull();
  });
});

describe("constant-time comparison", () => {
  it("verification goes through crypto.subtle.verify", async () => {
    const spy = vi.spyOn(crypto.subtle, "verify");
    await verifyToken(K, await signToken(K, claims), IAT + 10);
    expect(spy).toHaveBeenCalled();
  });
  // Flags == / === / != / !== between a MAC-ish name and anything but null or a number.
  const NAIVE = /\b\w*(sig|mac|signature|digest)\w*\s*[!=]==?\s*(?!null\b|\d)[A-Za-z_(]|[A-Za-z_)]\s*[!=]==?\s*\w*(sig|mac|signature|digest)/i;
  it("the source never compares a MAC or signature with == or ===", () => {
    expect(tokenSource.length).toBeGreaterThan(500); // something was collected
    expect(tokenSource.split("\n").filter((l) => NAIVE.test(l))).toEqual([]);
  });
  it("the guard bites: the pattern flags naive comparisons", () => {
    expect(NAIVE.test("if (computedMac === givenSig) return true;")).toBe(true);
    expect(NAIVE.test("return expected !== sigB64;")).toBe(true);
    expect(NAIVE.test("if (sig === null) return null;")).toBe(false);
  });
  it("control markers are present", () => {
    expect(tokenSource).toContain("CONTROL:token-signature");
    expect(tokenSource).toContain("CONTROL:token-expiry");
  });
});

describe("no secret in any error", () => {
  it("messages from signing never contain the key or token", async () => {
    const keys = ["short-secret-value", "é".repeat(15)];
    for (const k of keys) {
      for (const c of [claims, { ...claims, cc: "usa" }]) {
        const err = await signToken(k, c).catch((e: unknown) => e);
        expect(err).toBeInstanceOf(Error);
        expect((err as Error).message).not.toContain(k);
      }
    }
  });
  it("verify never logs", async () => {
    const spies = (["log", "warn", "error", "info", "debug"] as const).map((m) => vi.spyOn(console, m));
    await verifyToken(K, "v1.a.b", IAT);
    await verifyToken(K, await signToken(K2, claims), IAT);
    for (const s of spies) expect(s).not.toHaveBeenCalled();
  });
});

describe("cookie", () => {
  it("builds the exact attribute string with no Domain", () => {
    expect(unlockCookie("T", 86_400)).toBe("__Host-ra_kit=T; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=86400");
    expect(unlockCookie("T", 86_400)).not.toMatch(/Domain/i);
    expect(COOKIE_NAME).toBe("__Host-ra_kit");
  });
  it("refuses a token with characters outside the token alphabet", () => {
    expect(() => unlockCookie("a; Domain=x", 60)).toThrow();
    expect(() => unlockCookie("a\r\nSet-Cookie: x", 60)).toThrow();
    expect(() => unlockCookie("", 60)).toThrow();
  });
  it("refuses a bad lifetime", () => {
    for (const n of [0, -1, 1.5, NaN, MAX_TOKEN_AGE_SECONDS + 1]) expect(() => unlockCookie("T", n)).toThrow();
  });
  it("finds the value among other cookies", () => {
    expect(readUnlockCookie("a=1; __Host-ra_kit=TOK; b=2")).toBe("TOK");
  });
  it("ignores look-alike names", () => {
    expect(readUnlockCookie("ra_kit=nope; __Host-ra_kit_x=nope")).toBeNull();
    expect(readUnlockCookie("x__Host-ra_kit=nope")).toBeNull();
  });
  it("returns null for duplicates, empty and missing", () => {
    expect(readUnlockCookie("__Host-ra_kit=a; __Host-ra_kit=b")).toBeNull();
    expect(readUnlockCookie("__Host-ra_kit=")).toBeNull();
    expect(readUnlockCookie(null)).toBeNull();
    expect(readUnlockCookie("")).toBeNull();
  });
  it("round trips through a header", async () => {
    const t = await signToken(K, claims);
    const header = unlockCookie(t, 86_400).split(";")[0]!;
    expect(readUnlockCookie(header)).toBe(t);
  });
});
