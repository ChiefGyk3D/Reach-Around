// SPDX-License-Identifier: Apache-2.0
import { MAX_UA_LENGTH, matchUaToken } from "./botdata";

/**
 * Bot classifier: a pure, deterministic function over already-derived facts.
 *
 * Class meanings. All are "likely", none is proof:
 * - human-likely: nothing suspicious was found.
 * - verified-crawler: the user agent CLAIMS to be a known search engine or
 *   link-preview fetcher. Nothing is verified (no address-range or reverse
 *   DNS check), so read it as "claims to be a known crawler".
 * - automated: declares itself a bot, runs headless, or sends no user agent.
 * - suspicious: looks like a browser but enough weak signals add up.
 *
 * Decision order, first match wins:
 *   1. uaHeadless                           -> automated / ua-headless
 *   2. uaDeclaredBot === "automated"        -> automated / ua-declared-bot
 *   3. uaDeclaredBot === "verified-crawler" -> verified-crawler / verified-crawler-ua
 *   4. uaMissingOrEmpty                     -> automated / ua-missing
 *   5. weak signals: sum the weights in WEAK_WEIGHTS; if the sum is at least
 *      SUSPICIOUS_THRESHOLD the class is suspicious and the reason is the
 *      heaviest present signal, ties broken by the order of WEAK_ORDER.
 *   6. otherwise                            -> human-likely / none
 *
 * A signal that is missing or not exactly the expected value counts as
 * "unknown" and contributes nothing, so a partial object cannot look
 * suspicious by accident. A VPN or proxy address is never a signal; a Tor
 * address is a weak one that matters only together with another.
 *
 * The input is plain derived facts. No raw user agent, address or header text
 * goes in or comes out.
 */

export type BotClass = "human-likely" | "verified-crawler" | "automated" | "suspicious";

export const BOT_REASONS = [
  "ua-declared-bot",
  "ua-headless",
  "ua-missing",
  "ua-too-long",
  "no-lang",
  "no-sec-fetch",
  "datacenter",
  "tor",
  "no-js",
  "click-without-view",
  "rate",
  "paid-score",
  "verified-crawler-ua",
  "none",
] as const;

export type BotReason = (typeof BOT_REASONS)[number];

export type NetworkClass =
  | "residential"
  | "mobile"
  | "vpn-proxy"
  | "hosting-datacenter"
  | "tor"
  | "unknown";

/**
 * Structural input. A future ua.ts "UaClass" plugs in by supplying the four ua*
 * fields (see uaSignals for a ready-made bridge from a bounded UA string).
 */
export interface BotSignals {
  /** Token class from matchUaToken, headless excluded (that is uaHeadless). */
  uaDeclaredBot: "verified-crawler" | "automated" | null;
  uaHeadless: boolean;
  uaMissingOrEmpty: boolean;
  uaTooLong: boolean;
  acceptLanguagePresent: boolean;
  secFetchPresent: boolean;
  networkClass: NetworkClass;
  /** True if the page-view beacon fired. */
  jsExecuted: boolean;
  clickWithoutView: boolean;
  rateFlag: boolean;
  /** Cloudflare bot score (1 to 99, low is bot-like). Ignored unless usePaidFields. */
  paidBotScore?: number;
  /** Must be true for paidBotScore to be read. Off by default. */
  usePaidFields?: boolean;
}

export interface BotResult {
  class: BotClass;
  reason: BotReason;
}

type WeakKey = Exclude<
  BotReason,
  "ua-declared-bot" | "ua-headless" | "ua-missing" | "verified-crawler-ua" | "none"
>;

/** Weight of each weak signal. Changing one changes which combinations trip. */
export const WEAK_WEIGHTS: Readonly<Record<WeakKey, number>> = {
  "paid-score": 2,
  rate: 2,
  datacenter: 2,
  "ua-too-long": 2,
  "no-sec-fetch": 1,
  "no-lang": 1,
  tor: 1,
  "click-without-view": 1,
  "no-js": 1,
};

/**
 * Reason tie-break order (also the order signals are evaluated). Among equal
 * weights the earlier entry leads. By construction the last entry ("no-js",
 * the weakest evidence: script blockers and JS-off visitors are legitimate)
 * can only ever be a partner that adds weight, never the reported reason.
 */
export const WEAK_ORDER: readonly WeakKey[] = [
  "paid-score",
  "rate",
  "datacenter",
  "ua-too-long",
  "no-sec-fetch",
  "no-lang",
  "tor",
  "click-without-view",
  "no-js",
];

/** Sum of weak weights at or above this is "suspicious". One light signal is not. */
export const SUSPICIOUS_THRESHOLD = 2;

/** Paid score strictly below this counts as bot-like (only when usePaidFields). */
export const PAID_SCORE_LOW = 30;

function weakPresent(s: Record<string, unknown>, key: WeakKey): boolean {
  switch (key) {
    case "paid-score": {
      const v = s.paidBotScore;
      return (
        s.usePaidFields === true && typeof v === "number" && Number.isFinite(v) && v < PAID_SCORE_LOW
      );
    }
    case "rate":
      return s.rateFlag === true;
    case "datacenter":
      return s.networkClass === "hosting-datacenter";
    case "ua-too-long":
      return s.uaTooLong === true;
    case "no-sec-fetch":
      return s.secFetchPresent === false;
    case "no-lang":
      return s.acceptLanguagePresent === false;
    case "tor":
      return s.networkClass === "tor";
    case "no-js":
      return s.jsExecuted === false;
    case "click-without-view":
      return s.clickWithoutView === true;
  }
}

export function classifyBot(input: BotSignals): BotResult {
  const s: Record<string, unknown> =
    typeof input === "object" && input !== null
      ? (input as unknown as Record<string, unknown>)
      : {};

  if (s.uaHeadless === true) return { class: "automated", reason: "ua-headless" };
  if (s.uaDeclaredBot === "automated") return { class: "automated", reason: "ua-declared-bot" };
  if (s.uaDeclaredBot === "verified-crawler") {
    return { class: "verified-crawler", reason: "verified-crawler-ua" };
  }
  if (s.uaMissingOrEmpty === true) return { class: "automated", reason: "ua-missing" };

  let total = 0;
  let best: WeakKey | null = null;
  for (const key of WEAK_ORDER) {
    if (!weakPresent(s, key)) continue;
    total += WEAK_WEIGHTS[key];
    if (best === null || WEAK_WEIGHTS[key] > WEAK_WEIGHTS[best]) best = key;
  }
  if (best !== null && total >= SUSPICIOUS_THRESHOLD) {
    return { class: "suspicious", reason: best };
  }
  return { class: "human-likely", reason: "none" };
}

/**
 * Bridge from a raw user agent to the four ua* signals. The string is bounded
 * and used only for pattern matching; it is not returned.
 */
export function uaSignals(
  ua: string | null | undefined,
): Pick<BotSignals, "uaDeclaredBot" | "uaHeadless" | "uaMissingOrEmpty" | "uaTooLong"> {
  if (typeof ua !== "string" || ua.trim().length === 0) {
    return { uaDeclaredBot: null, uaHeadless: false, uaMissingOrEmpty: true, uaTooLong: false };
  }
  const m = matchUaToken(ua);
  return {
    uaDeclaredBot: m === "verified-crawler" || m === "automated" ? m : null,
    uaHeadless: m === "headless",
    uaMissingOrEmpty: false,
    uaTooLong: ua.length > MAX_UA_LENGTH,
  };
}
