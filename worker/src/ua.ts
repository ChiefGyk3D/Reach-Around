// SPDX-License-Identifier: Apache-2.0
//
// Coarse User-Agent and Accept-Language classification.
//
// Privacy contract: the raw header values are read transiently by these pure
// functions and are never returned, thrown, logged or embedded in an error.
// Only members of the closed enums below leave this file. No dependencies, no
// network, no state.

export type Browser =
  | "chrome"
  | "edge"
  | "firefox"
  | "safari"
  | "opera"
  | "samsung"
  | "brave"
  | "other";
export type Os = "windows" | "macos" | "linux" | "android" | "ios" | "chromeos" | "other";
export type Device = "mobile" | "tablet" | "desktop" | "other";

export interface UaClass {
  browser: Browser;
  /** Exact major version for a recognized browser (0..999), else null. */
  browserMajor: number | null;
  os: Os;
  device: Device;
  /** True when a web engine is present but no known browser could be named. */
  engineOnly: boolean;
}

export const MAX_UA_LENGTH = 512;
export const MAX_ACCEPT_LANGUAGE_LENGTH = 256;
export const MAX_MAJOR = 999;

function unknown(): UaClass {
  return { browser: "other", browserMajor: null, os: "other", device: "other", engineOnly: false };
}

// Declared crawlers, previewers, scripted clients and headless browsers. These
// get "other" everywhere so they never inflate human browser counts.
const BOT =
  /bot\b|crawl|spider|slurp|facebookexternalhit|embedly|preview|whatsapp|headless|phantomjs|puppeteer|playwright|selenium|lighthouse|curl\/|wget|libwww|python|go-http-client|java\/|okhttp|apache-httpclient|axios|node-fetch|undici|httpie|postman|monitor|uptime|scrapy/i;

// Browser rules, in priority order. Edge, Opera, Samsung Internet and Brave all
// contain "Chrome", and the iOS wrappers (CriOS, FxiOS, EdgiOS) all contain
// "Safari", so every specific token is tested before plain Chrome, and Chrome
// before Safari.
const BROWSER_RULES: ReadonlyArray<readonly [Browser, RegExp]> = [
  ["edge", /(?:EdgiOS|EdgA|Edge|Edg)\/(\d{1,4})/],
  ["opera", /(?:OPR|OPiOS|OPT|Opera)\/(\d{1,4})/],
  ["samsung", /SamsungBrowser\/(\d{1,4})/],
  ["brave", /Brave\/(\d{1,4})/],
  ["firefox", /(?:FxiOS|Firefox)\/(\d{1,4})/],
  ["chrome", /(?:CriOS|Chrome)\/(\d{1,4})/],
  ["safari", /Version\/(\d{1,4})[^]{0,80}?Safari\//],
];

function detectBrowser(ua: string): { browser: Browser; major: number | null } {
  // Android WebView: an engine inside an app, not a browser we can name.
  if (/; wv\)/.test(ua)) return { browser: "other", major: null };
  for (const [browser, re] of BROWSER_RULES) {
    const m = re.exec(ua);
    if (m) return { browser, major: Math.min(Number.parseInt(m[1] as string, 10), MAX_MAJOR) };
  }
  return { browser: "other", major: null };
}

function detectOs(ua: string): Os {
  // iOS first: its UA also says "like Mac OS X". Android and CrOS before
  // Linux, because both also contain "Linux" / "X11".
  if (/iPhone|iPad|iPod/.test(ua)) return "ios";
  if (/Android/.test(ua)) return "android";
  if (/CrOS/.test(ua)) return "chromeos";
  if (/Windows NT/.test(ua)) return "windows";
  // iPadOS 13+ desktop-mode Safari sends a Macintosh UA that cannot be told
  // apart from a Mac; it is counted as macos.
  if (/Macintosh|Mac OS X/.test(ua)) return "macos";
  if (/Linux|X11/.test(ua)) return "linux";
  return "other";
}

function detectDevice(ua: string, os: Os): Device {
  switch (os) {
    case "ios":
      return /iPad/.test(ua) ? "tablet" : "mobile";
    case "android":
      // Android phones say "Mobile"; tablets omit it.
      return /Mobile/.test(ua) ? "mobile" : "tablet";
    case "windows":
    case "macos":
    case "linux":
    case "chromeos":
      return "desktop";
    default:
      return "other";
  }
}

export function classifyUserAgent(ua: string | null): UaClass {
  if (typeof ua !== "string" || ua.length === 0 || ua.length > MAX_UA_LENGTH) return unknown();
  if (BOT.test(ua)) return unknown();
  const { browser, major } = detectBrowser(ua);
  const os = detectOs(ua);
  const device = detectDevice(ua, os);
  const engineOnly = browser === "other" && /AppleWebKit|Gecko|Trident|Presto/.test(ua);
  return { browser, browserMajor: major, os, device, engineOnly };
}

const LANG_TAG = /^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$/;

/** Lowercase primary language subtag of the first Accept-Language tag, else "und". */
export function primaryLanguage(acceptLanguage: string | null): string {
  if (typeof acceptLanguage !== "string") return "und";
  if (acceptLanguage.length === 0 || acceptLanguage.length > MAX_ACCEPT_LANGUAGE_LENGTH) {
    return "und";
  }
  const first = (acceptLanguage.split(",", 1)[0] as string).split(";", 1)[0] as string;
  const tag = first.trim();
  if (!LANG_TAG.test(tag)) return "und";
  return (tag.split("-", 1)[0] as string).toLowerCase();
}
