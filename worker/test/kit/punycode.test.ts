// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { dnsLabelOk, punycodeDecode, punycodeEncode } from "../../src/kit/punycode";

describe("punycode (RFC 3492)", () => {
  const pairs: Array<[string, string]> = [
    ["bücher", "bcher-kva"],
    ["рф", "p1ai"],
    ["中文", "fiq228c"],
    ["münchen", "mnchen-3ya"],
  ];
  it.each(pairs)("round-trips %s", (u, a) => {
    expect(punycodeEncode(u)).toBe(a);
    expect(punycodeDecode(a)).toBe(u);
  });
  it("returns null on bad input", () => {
    expect(punycodeDecode("a-é")).toBeNull();
    expect(punycodeDecode("zzzzzzzzzzzzzzzzzzzzzzzz")).toBeNull();
  });
});

describe("dnsLabelOk", () => {
  it("accepts plain labels and valid A-labels", () => {
    for (const l of ["a", "abc", "a-b", "a1", "xn--bcher-kva", "xn--p1ai", "xn--fiq228c"]) expect(dnsLabelOk(l), l).toBe(true);
  });
  it("rejects bad shapes, reserved prefixes and invalid A-labels", () => {
    for (const l of ["", "-a", "a-", "a_b", "A", "a".repeat(64), "xn--", "xn--a", "xn--abc-", "xn--abc", "xn--bcher-kva9", "ab--cd", "xn--ASCII-", "xn--ab-"]) {
      expect(dnsLabelOk(l), l).toBe(false);
    }
  });
});
