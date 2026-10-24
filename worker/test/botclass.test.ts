// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import {
  BOT_REASONS,
  classifyBot,
  uaSignals,
  type BotClass,
  type BotReason,
  type BotSignals,
} from "../src/botclass";
import { matchUaToken } from "../src/botdata";

/** A plain browser with every healthy signal. Cases override single fields. */
const BASE: BotSignals = {
  uaDeclaredBot: null,
  uaHeadless: false,
  uaMissingOrEmpty: false,
  uaTooLong: false,
  acceptLanguagePresent: true,
  secFetchPresent: true,
  networkClass: "residential",
  jsExecuted: true,
  clickWithoutView: false,
  rateFlag: false,
};

const CLASSES: BotClass[] = ["human-likely", "verified-crawler", "automated", "suspicious"];

type Row = [name: string, over: Partial<BotSignals>, cls: BotClass, reason: BotReason];

const TABLE: Row[] = [
  // Healthy and single light signals.
  ["clean browser", {}, "human-likely", "none"],
  ["mobile network", { networkClass: "mobile" }, "human-likely", "none"],
  ["unknown network", { networkClass: "unknown" }, "human-likely", "none"],
  ["vpn-proxy alone is not a signal", { networkClass: "vpn-proxy" }, "human-likely", "none"],
  ["tor alone is not enough", { networkClass: "tor" }, "human-likely", "none"],
  ["no-lang alone is not enough", { acceptLanguagePresent: false }, "human-likely", "none"],
  ["no-sec-fetch alone is not enough", { secFetchPresent: false }, "human-likely", "none"],
  ["no-js alone is not enough", { jsExecuted: false }, "human-likely", "none"],
  ["click-without-view alone is not enough", { clickWithoutView: true }, "human-likely", "none"],
  // Single heavy signals.
  ["datacenter alone", { networkClass: "hosting-datacenter" }, "suspicious", "datacenter"],
  ["rate alone", { rateFlag: true }, "suspicious", "rate"],
  ["too long alone", { uaTooLong: true }, "suspicious", "ua-too-long"],
  // Light pairs reach the threshold; reason is the first in WEAK_ORDER.
  ["no-lang + no-sec-fetch", { acceptLanguagePresent: false, secFetchPresent: false }, "suspicious", "no-sec-fetch"],
  ["no-lang + tor", { acceptLanguagePresent: false, networkClass: "tor" }, "suspicious", "no-lang"],
  ["no-lang + no-js", { acceptLanguagePresent: false, jsExecuted: false }, "suspicious", "no-lang"],
  ["click-without-view outranks no-js", { jsExecuted: false, clickWithoutView: true }, "suspicious", "click-without-view"],
  ["no-lang + click-without-view", { acceptLanguagePresent: false, clickWithoutView: true }, "suspicious", "no-lang"],
  ["no-sec-fetch + tor", { secFetchPresent: false, networkClass: "tor" }, "suspicious", "no-sec-fetch"],
  ["no-sec-fetch + no-js", { secFetchPresent: false, jsExecuted: false }, "suspicious", "no-sec-fetch"],
  ["tor + no-js", { networkClass: "tor", jsExecuted: false }, "suspicious", "tor"],
  ["tor + click-without-view", { networkClass: "tor", clickWithoutView: true }, "suspicious", "tor"],
    // Heavy beats light for the reason.
  ["datacenter + no-lang", { networkClass: "hosting-datacenter", acceptLanguagePresent: false }, "suspicious", "datacenter"],
  ["rate + datacenter (order)", { rateFlag: true, networkClass: "hosting-datacenter" }, "suspicious", "rate"],
  ["datacenter + too long (order)", { networkClass: "hosting-datacenter", uaTooLong: true }, "suspicious", "datacenter"],
  ["too long + no-sec-fetch", { uaTooLong: true, secFetchPresent: false }, "suspicious", "ua-too-long"],
  ["rate + no-lang + no-js", { rateFlag: true, acceptLanguagePresent: false, jsExecuted: false }, "suspicious", "rate"],
  ["everything weak", { rateFlag: true, networkClass: "hosting-datacenter", uaTooLong: true, secFetchPresent: false, acceptLanguagePresent: false, jsExecuted: false, clickWithoutView: true }, "suspicious", "rate"],
  // Hard signals.
  ["headless", { uaHeadless: true }, "automated", "ua-headless"],
  ["declared automated", { uaDeclaredBot: "automated" }, "automated", "ua-declared-bot"],
  ["declared crawler (claims to be)", { uaDeclaredBot: "verified-crawler" }, "verified-crawler", "verified-crawler-ua"],
  ["missing ua", { uaMissingOrEmpty: true }, "automated", "ua-missing"],
  // Precedence among hard signals.
  ["headless beats declared automated", { uaHeadless: true, uaDeclaredBot: "automated" }, "automated", "ua-headless"],
  ["headless beats declared crawler", { uaHeadless: true, uaDeclaredBot: "verified-crawler" }, "automated", "ua-headless"],
  ["declared automated beats missing ua", { uaDeclaredBot: "automated", uaMissingOrEmpty: true }, "automated", "ua-declared-bot"],
  ["declared crawler beats missing", { uaDeclaredBot: "verified-crawler", uaMissingOrEmpty: true }, "verified-crawler", "verified-crawler-ua"],
  ["headless beats missing", { uaHeadless: true, uaMissingOrEmpty: true }, "automated", "ua-headless"],
  // Hard signals beat weak ones.
  ["crawler from datacenter stays crawler", { uaDeclaredBot: "verified-crawler", networkClass: "hosting-datacenter", rateFlag: true }, "verified-crawler", "verified-crawler-ua"],
  ["declared bot with weak signals", { uaDeclaredBot: "automated", rateFlag: true }, "automated", "ua-declared-bot"],
  ["missing ua with weak signals", { uaMissingOrEmpty: true, rateFlag: true, jsExecuted: false }, "automated", "ua-missing"],
  // Paid fields: ignored unless the flag is on.
  ["paid low, flag absent", { paidBotScore: 1 }, "human-likely", "none"],
  ["paid low, flag false", { paidBotScore: 1, usePaidFields: false }, "human-likely", "none"],
  ["paid low, flag on", { paidBotScore: 1, usePaidFields: true }, "suspicious", "paid-score"],
  ["paid high, flag on", { paidBotScore: 95, usePaidFields: true }, "human-likely", "none"],
  ["paid at boundary 30, flag on", { paidBotScore: 30, usePaidFields: true }, "human-likely", "none"],
  ["paid 29, flag on", { paidBotScore: 29, usePaidFields: true }, "suspicious", "paid-score"],
  ["paid NaN, flag on", { paidBotScore: Number.NaN, usePaidFields: true }, "human-likely", "none"],
  ["paid flag on, no score", { usePaidFields: true }, "human-likely", "none"],
  ["paid low + datacenter (order)", { paidBotScore: 5, usePaidFields: true, networkClass: "hosting-datacenter" }, "suspicious", "paid-score"],
];

describe("classifyBot truth table", () => {
  it("has at least 40 combinations", () => {
    expect(TABLE.length).toBeGreaterThanOrEqual(40);
  });

  for (const [name, over, cls, reason] of TABLE) {
    it(`${name} -> ${cls}/${reason}`, () => {
      expect(classifyBot({ ...BASE, ...over })).toEqual({ class: cls, reason });
    });
  }
});

describe("classifyBot properties", () => {
  it("weak_weights_light_signals_never_trip_alone (threshold)", () => {
    for (const over of [
      { acceptLanguagePresent: false },
      { secFetchPresent: false },
      { jsExecuted: false },
      { clickWithoutView: true },
      { networkClass: "tor" as const },
    ]) {
      expect(classifyBot({ ...BASE, ...over }).class).toBe("human-likely");
    }
  });

  it("enum_closure: every output pairs a valid class with a listed reason", () => {
    const rnd = mulberry(1234);
    const pick = <T,>(xs: readonly T[]): T => xs[Math.floor(rnd() * xs.length)];
    let seen = 0;
    const reasons = new Set<string>();
    for (let i = 0; i < 20000; i++) {
      const input = {
        uaDeclaredBot: pick([null, "automated", "verified-crawler"] as const),
        uaHeadless: rnd() < 0.1,
        uaMissingOrEmpty: rnd() < 0.1,
        uaTooLong: rnd() < 0.2,
        acceptLanguagePresent: rnd() < 0.7,
        secFetchPresent: rnd() < 0.7,
        networkClass: pick(["residential", "mobile", "vpn-proxy", "hosting-datacenter", "tor", "unknown"] as const),
        jsExecuted: rnd() < 0.7,
        clickWithoutView: rnd() < 0.2,
        rateFlag: rnd() < 0.2,
        paidBotScore: pick([undefined, 1, 29, 30, 99]),
        usePaidFields: rnd() < 0.5,
      } satisfies BotSignals;
      const out = classifyBot(input);
      seen++;
      expect(CLASSES).toContain(out.class);
      expect(BOT_REASONS as readonly string[]).toContain(out.reason);
      expect(Object.keys(out).sort()).toEqual(["class", "reason"]);
      reasons.add(out.reason);
    }
    expect(seen).toBe(20000);
    // Every reason is reachable except "no-js", which by design only adds
    // weight and never leads (see WEAK_ORDER).
    expect([...reasons].sort()).toEqual(BOT_REASONS.filter((r) => r !== "no-js").sort());
  });

  it("reasons_are_unique_and_none_is_present", () => {
    expect(new Set(BOT_REASONS).size).toBe(BOT_REASONS.length);
    expect(BOT_REASONS).toContain("none");
  });

  it("deterministic_ordering: same input, same output; key order does not matter", () => {
    const a: BotSignals = { ...BASE, rateFlag: true, networkClass: "hosting-datacenter", jsExecuted: false };
    const reversed = Object.fromEntries(Object.entries(a).reverse()) as unknown as BotSignals;
    const first = classifyBot(a);
    for (let i = 0; i < 50; i++) expect(classifyBot(a)).toEqual(first);
    expect(classifyBot(reversed)).toEqual(first);
  });

  it("does_not_mutate_its_input", () => {
    const a: BotSignals = { ...BASE, rateFlag: true };
    const copy = JSON.stringify(a);
    classifyBot(a);
    expect(JSON.stringify(a)).toBe(copy);
  });

  it("paid_fields_ignored_when_the_flag_is_off: a score never changes the result", () => {
    for (const score of [undefined, -5, 0, 1, 29, 30, 99, Number.NaN, Number.POSITIVE_INFINITY]) {
      for (const over of [{}, { rateFlag: true }, { networkClass: "hosting-datacenter" as const }]) {
        const off = classifyBot({ ...BASE, ...over });
        expect(classifyBot({ ...BASE, ...over, paidBotScore: score })).toEqual(off);
        expect(classifyBot({ ...BASE, ...over, paidBotScore: score, usePaidFields: false })).toEqual(off);
      }
    }
  });

  it("never_throws on random and hostile objects", () => {
    const rnd = mulberry(99);
    const junk = (): unknown => {
      const r = rnd();
      if (r < 0.1) return null;
      if (r < 0.2) return undefined;
      if (r < 0.3) return Math.floor(rnd() * 200) - 100;
      if (r < 0.4) return "x".repeat(Math.floor(rnd() * 600));
      if (r < 0.5) return rnd() < 0.5;
      if (r < 0.6) return [rnd(), {}];
      if (r < 0.7) return { nested: { deep: [1, 2, 3] } };
      if (r < 0.8) return Number.NaN;
      if (r < 0.9) return Symbol("s");
      return () => 1;
    };
    const keys = [
      "uaDeclaredBot", "uaHeadless", "uaMissingOrEmpty", "uaTooLong", "acceptLanguagePresent",
      "secFetchPresent", "networkClass", "jsExecuted", "clickWithoutView", "rateFlag",
      "paidBotScore", "usePaidFields", "extra", "__proto__", "constructor",
    ];
    for (let i = 0; i < 5000; i++) {
      const o: Record<string, unknown> = {};
      for (const k of keys) if (rnd() < 0.6) Object.defineProperty(o, k, { value: junk(), enumerable: true });
      const out = classifyBot(o as unknown as BotSignals);
      expect(CLASSES).toContain(out.class);
      expect(BOT_REASONS as readonly string[]).toContain(out.reason);
    }
    for (const bad of [null, undefined, 0, "str", [], () => 1]) {
      expect(classifyBot(bad as unknown as BotSignals)).toEqual({ class: "human-likely", reason: "none" });
    }
  });
});

describe("matchUaToken", () => {
  const cases: [string, ReturnType<typeof matchUaToken>][] = [
    ["Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "verified-crawler"],
    ["facebookexternalhit/1.1", "verified-crawler"],
    ["Slackbot-LinkExpanding 1.0", "verified-crawler"],
    ["curl/8.5.0", "automated"],
    ["python-requests/2.31", "automated"],
    ["Lighthouse", "automated"],
    ["UptimeRobot/2.0", "automated"],
    ["Go-http-client/1.1", "automated"],
    ["got (https://github.com/sindresorhus/got)", "automated"],
    ["Mozilla/5.0 HeadlessChrome/120.0.0.0 Safari/537.36", "headless"],
    ["Mozilla/5.0 (X11; Linux) Playwright", "headless"],
    ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", null],
    ["Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15", null],
    ["Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1", null],
    ["Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0", null],
    ["Mozilla/5.0 Gotham Browser", null],
    ["", null],
  ];
  for (const [ua, want] of cases) {
    it(`${ua.slice(0, 40) || "(empty)"} -> ${want}`, () => {
      expect(matchUaToken(ua)).toBe(want);
    });
  }

  it("is case-insensitive", () => {
    expect(matchUaToken("GOOGLEBOT")).toBe("verified-crawler");
    expect(matchUaToken("CuRl/1")).toBe("automated");
  });

  it("only looks at the first 512 characters", () => {
    expect(matchUaToken("a".repeat(512) + "googlebot")).toBeNull();
    expect(matchUaToken("a".repeat(500) + "googlebot")).toBe("verified-crawler");
  });

  it("redos: 100k-character hostile inputs finish under 50 ms", () => {
    const inputs = [
      "a".repeat(100_000),
      "got".repeat(34_000),
      "(".repeat(100_000),
      "bo".repeat(50_000),
      " got ".repeat(20_000),
    ];
    const t0 = performance.now();
    for (const s of inputs) matchUaToken(s);
    const ms = performance.now() - t0;
    expect(ms).toBeLessThan(50);
  });

  it("whole-word token needs word boundaries", () => {
    expect(matchUaToken("gotham")).toBeNull();
    expect(matchUaToken("xgot")).toBeNull();
    expect(matchUaToken("got/12")).toBe("automated");
    expect(matchUaToken("gotgot got")).toBe("automated");
  });
});

describe("uaSignals", () => {
  it("maps a ua string to the four ua signals without returning the string", () => {
    expect(uaSignals(undefined)).toEqual({ uaDeclaredBot: null, uaHeadless: false, uaMissingOrEmpty: true, uaTooLong: false });
    expect(uaSignals("   ").uaMissingOrEmpty).toBe(true);
    expect(uaSignals("curl/8").uaDeclaredBot).toBe("automated");
    expect(uaSignals("Googlebot/2.1").uaDeclaredBot).toBe("verified-crawler");
    expect(uaSignals("HeadlessChrome/1").uaHeadless).toBe(true);
    expect(uaSignals("x".repeat(513)).uaTooLong).toBe(true);
    expect(uaSignals("x".repeat(512)).uaTooLong).toBe(false);
    expect(Object.values(uaSignals("curl/8")).every((v) => typeof v !== "string" || v === "automated")).toBe(true);
  });
});

/** Small seeded PRNG so failures reproduce. */
function mulberry(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
