// SPDX-License-Identifier: Apache-2.0

/**
 * Shared user-agent token data for bot handling. This is the single source
 * for "what does a declared bot look like"; it extends the BOT_UA idea in
 * click.ts (which stays as it is until that file adopts this module).
 *
 * Everything here is lowercase and matched with plain substring checks. There
 * is no regular expression, so no input can make matching backtrack. A user
 * agent is whatever the sender says it is: a match means "claims to be", never
 * "is". Nothing here verifies a crawler.
 */

/** Longest user agent we look at; the rest is ignored. */
export const MAX_UA_LENGTH = 512;

export type UaTokenClass = "verified-crawler" | "automated" | "headless";

/**
 * Crawlers and link-preview fetchers that name themselves. "verified-crawler"
 * is the class name for "claims to be one of these"; no address range or
 * reverse-DNS check is done. Sources are each operator's own documentation:
 * - Google: developers.google.com/search/docs/crawling-indexing/overview-google-crawlers
 *   (googlebot, adsbot-google, mediapartners-google)
 * - Bing: bing.com/webmasters/help/which-crawlers-does-bing-use-8c184ec0 (bingbot)
 * - DuckDuckGo: duckduckgo.com/duckduckgo-help-pages/results/duckduckbot (duckduckbot)
 * - Yandex: yandex.com/support/webmaster/robot-workings/check-yandex-robots.html (yandexbot)
 * - Baidu: baidu.com/search/robots_english.html (baiduspider)
 * - Apple: support.apple.com/en-us/119829 (applebot)
 * - Meta: developers.facebook.com/docs/sharing/webmasters/web-crawlers
 *   (facebookexternalhit, facebot; WhatsApp link previews use the "whatsapp" token)
 * - X: developer.x.com/en/docs/x-for-websites/cards/guides/troubleshooting-cards (twitterbot)
 * - Slack: api.slack.com/robots (slackbot, slack-imgproxy)
 * - Discord: discord.com/developers/docs/reference (discordbot)
 * - LinkedIn: linkedin.com/help/linkedin/answer/a521928 (linkedinbot)
 * - Telegram: core.telegram.org/bots/webhooks (telegrambot, link preview fetcher)
 * - Pinterest: help.pinterest.com/en/business/article/pinterest-crawler (pinterestbot)
 */
export const VERIFIED_CRAWLER_TOKENS: readonly string[] = [
  "googlebot", "adsbot-google", "mediapartners-google", "bingbot", "duckduckbot",
  "yandexbot", "baiduspider", "applebot",
  "facebookexternalhit", "facebot", "twitterbot", "slackbot", "slack-imgproxy",
  "discordbot", "linkedinbot", "telegrambot", "pinterestbot", "whatsapp",
];

/**
 * Headless browsers and browser-automation drivers. Checked first: a headless
 * browser is automation no matter what else its user agent says.
 */
export const HEADLESS_TOKENS: readonly string[] = [
  "headlesschrome", "headless", "phantomjs", "puppeteer", "playwright",
  "selenium", "webdriver", "slimerjs",
];

/**
 * Scripted HTTP clients, monitors, auditors and generic bot words. These carry
 * over the click.ts list (HTTP clients, monitors, generic words); the generic
 * words are broad on purpose and will catch the odd honest product name.
 */
export const AUTOMATED_TOKENS: readonly string[] = [
  "curl", "wget", "python", "aiohttp", "httpx", "axios", "node-fetch", "undici",
  "go-http", "okhttp", "java/", "libwww", "httpclient",
  "lighthouse", "pingdom", "uptimerobot", "statuscake", "ia_archiver",
  "bot", "crawl", "spider", "slurp", "preview", "monitor", "scan", "fetch", "check",
];

/**
 * Tokens that count only as a whole word (neighbors are not letters or
 * digits), because as a substring they hit ordinary words ("got" in "gotham").
 */
export const AUTOMATED_WHOLE_WORD_TOKENS: readonly string[] = ["got"];

function isWordChar(code: number): boolean {
  return (
    (code >= 48 && code <= 57) || // 0-9
    (code >= 97 && code <= 122) // a-z (input is lowercased)
  );
}

function hasWholeWord(haystack: string, word: string): boolean {
  let from = 0;
  for (;;) {
    const at = haystack.indexOf(word, from);
    if (at === -1) return false;
    const before = at === 0 ? 0 : haystack.charCodeAt(at - 1);
    const end = at + word.length;
    const after = end >= haystack.length ? 0 : haystack.charCodeAt(end);
    if (!isWordChar(before) && !isWordChar(after)) return true;
    from = at + 1;
  }
}

function includesAny(haystack: string, tokens: readonly string[]): boolean {
  for (const t of tokens) {
    if (haystack.includes(t)) return true;
  }
  return false;
}

/**
 * Classify a user agent by token. Only the first MAX_UA_LENGTH characters are
 * examined (the cut happens before lowercasing so work is bounded by the cap).
 * Order: headless, then verified-crawler (a named crawler wins over the generic
 * "bot" word), then automated. Returns null when nothing matches. Never throws
 * for a string; a non-string returns null.
 */
export function matchUaToken(ua: string): UaTokenClass | null {
  if (typeof ua !== "string" || ua.length === 0) return null;
  const s = ua.slice(0, MAX_UA_LENGTH).toLowerCase();
  if (includesAny(s, HEADLESS_TOKENS)) return "headless";
  if (includesAny(s, VERIFIED_CRAWLER_TOKENS)) return "verified-crawler";
  if (includesAny(s, AUTOMATED_TOKENS)) return "automated";
  for (const w of AUTOMATED_WHOLE_WORD_TOKENS) {
    if (hasWholeWord(s, w)) return "automated";
  }
  return null;
}
