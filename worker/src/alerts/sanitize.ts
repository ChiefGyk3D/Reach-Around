// SPDX-License-Identifier: Apache-2.0
//
// Text that came from a visitor (or any outside source) is neutralized before
// it goes into an alert: control and invisible characters removed, length
// capped, markdown and mention syntax made inert. The result is one plain line.

/** Hard cap on a whole alert message, in UTF-16 code units. Discord allows 2000. */
export const MAX_MESSAGE_CHARS = 800;
/** Default cap for one piece of visitor text. */
export const MAX_FIELD_CHARS = 120;

/** Inclusive code point ranges that are dropped: controls, format, bidi, zero-width, separators, tags, fillers. */
const INVISIBLE_RANGES: ReadonlyArray<readonly [number, number]> = [
  [0x0000, 0x001f], // C0 controls
  [0x007f, 0x009f], // DEL and C1 controls
  [0x00ad, 0x00ad], // soft hyphen
  [0x061c, 0x061c], // Arabic letter mark
  [0x115f, 0x1160], // Hangul fillers
  [0x17b4, 0x17b5], // Khmer inherent vowels
  [0x180e, 0x180e], // Mongolian vowel separator
  [0x200b, 0x200f], // zero-width, joiners, direction marks
  [0x2028, 0x202e], // line and paragraph separators, bidi embedding and override
  [0x2060, 0x206f], // word joiner, invisible operators, bidi isolates
  [0x3164, 0x3164], // Hangul filler
  [0xfe00, 0xfe0f], // variation selectors
  [0xfeff, 0xfeff], // byte order mark
  [0xffa0, 0xffa0], // halfwidth Hangul filler
  [0xfff9, 0xfffb], // interlinear annotation
  [0xe0000, 0xe007f], // tag characters
];

function isInvisible(cp: number): boolean {
  for (const [lo, hi] of INVISIBLE_RANGES) if (cp >= lo && cp <= hi) return true;
  return false;
}

/** Characters Discord markdown would act on in a single line. Each gets a backslash. */
const MARKDOWN = /[\\*_~`>|[\]#]/g;

export type TextMode = "markdown" | "plain";

/**
 * Make one outside string safe to place in an alert. Never throws; any
 * non-string becomes "". `max` caps the output length (the escapes count).
 * "markdown" (Discord) also backslash-escapes markdown syntax; "plain"
 * (Matrix body, email) does not.
 */
export function neutralize(input: unknown, max: number = MAX_FIELD_CHARS, mode: TextMode = "plain"): string {
  if (typeof input !== "string") return "";
  // Cap the work first so a huge value cannot cost CPU.
  const bounded = input.length > 4 * max ? input.slice(0, 4 * max) : input;
  let s = "";
  for (const ch of bounded) {
    const cp = ch.codePointAt(0) ?? 0;
    // A lone surrogate is not text; whitespace of any kind and invisible characters become one space.
    if (cp >= 0xd800 && cp <= 0xdfff) s += " ";
    else s += isInvisible(cp) ? " " : ch;
  }
  s = s.replace(/\s+/g, " ").trim();
  // Mentions: "@" becomes "(at)" so @everyone, @here, <@id> and user:server ids are inert text.
  s = s.replace(/@/g, "(at)");
  // Angle brackets carry <@id>, <#id>, <:emoji:> and autolinks. Replace, do not escape.
  s = s.replace(/[<>]/g, " ");
  // Defang links: a scheme separator no longer forms a URL.
  s = s.replace(/:\/\//g, " ");
  if (mode === "markdown") s = s.replace(MARKDOWN, (c) => `\\${c}`);
  if (s.length > max) {
    s = s.slice(0, max);
    // Do not leave a lone backslash or half a surrogate pair at the cut.
    s = s.replace(/\\+$/, "").replace(/[\ud800-\udbff]$/, "");
    s += "...";
  }
  return s;
}

/** Cut a finished message to the hard cap. */
export function capMessage(text: string, max: number = MAX_MESSAGE_CHARS): string {
  if (text.length <= max) return text;
  return text.slice(0, max - 3).replace(/[\ud800-\udbff]$/, "") + "...";
}
