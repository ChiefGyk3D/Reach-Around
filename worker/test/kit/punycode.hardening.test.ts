// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { isValidALabel, punycodeDecode, punycodeEncode } from "../../src/kit/punycode";

const enc = (u: string) => "xn--" + punycodeEncode(u)!;

describe("isValidALabel on its own", () => {
  it("enforces the 63 character limit itself", () => {
    const long = enc("é".repeat(58));
    expect(long.length).toBe(64);
    expect(isValidALabel(long)).toBe(false);
    const ok = enc("é".repeat(20));
    expect(ok.length).toBeLessThanOrEqual(63);
    expect(isValidALabel(ok)).toBe(true);
  });
  it("rejects decomposed and uppercase Unicode labels", () => {
    expect(isValidALabel(enc("éa"))).toBe(false); // NFD
    expect(isValidALabel(enc("éa"))).toBe(true); // the NFC form of the same text
    expect(isValidALabel(enc("Éa"))).toBe(false); // uppercase
  });
  it("rejects right-to-left scripts, mixed direction and leading marks", () => {
    for (const u of ["abcאב", "אב", "ار1", "ا١", "ܐa", "ހa", "ߊ", "a\u{10800}", "̀a", "a‏b", "a‮b", "a⁧b"]) {
      expect(isValidALabel(enc(u)), JSON.stringify(u)).toBe(false);
    }
    for (const l of ["xn--abc-spef", "xn--a-ubb", "xn--1-ymc2p"]) expect(isValidALabel(l), l).toBe(false);
  });
  it("still accepts left-to-right scripts", () => {
    for (const u of ["рф", "中文", "über", "αβ", "こんにちは"]) {
      expect(isValidALabel(enc(u)), u).toBe(true);
    }
  });
});

describe("punycodeEncode overflow", () => {
  it("returns null instead of truncating when the arithmetic leaves 32 bits", () => {
    expect(punycodeEncode("a".repeat(2000) + "\u0080\u{10ffff}")).toBeNull();
  });
  it("still encodes large but safe input", () => {
    expect(punycodeEncode("a".repeat(50) + "é")).toMatch(/^a{50}-/);
  });
});

describe("punycodeDecode on hostile input", () => {
  it("never throws and returns null over the length cap", () => {
    expect(punycodeDecode("a".repeat(125_000) + "-")).toBeNull();
    expect(punycodeDecode("a".repeat(257))).toBeNull();
  });
  it("stays fast on a long input within the cap", () => {
    const t0 = performance.now();
    for (let i = 0; i < 20; i++) punycodeDecode("a".repeat(100) + "-" + "9a".repeat(75));
    expect(performance.now() - t0).toBeLessThan(500);
  });
  it("does not throw for malformed input of any shape", () => {
    for (const s of ["-", "--", "9", "zzzz", "a-b-c-", "é", "ab\u0000", "999999999999999999999999"]) {
      expect(() => punycodeDecode(s), s).not.toThrow();
    }
  });
});
