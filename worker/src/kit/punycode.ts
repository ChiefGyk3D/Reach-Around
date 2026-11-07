// SPDX-License-Identifier: Apache-2.0
//
// Punycode (RFC 3492) and a DNS label check built on it. Implemented directly
// rather than through the URL class so the answer does not depend on a runtime's
// IDNA tables. The check is a round trip: an `xn--` label is accepted only if it
// decodes to a string with at least one non-ASCII letter, mark or digit (no
// controls, no symbols), already lowercase and in NFC, and encodes back to the
// same ASCII. That is narrower than full IDNA validation and is meant to be.

const BASE = 36;
const TMIN = 1;
const TMAX = 26;
const SKEW = 38;
const DAMP = 700;
const INITIAL_BIAS = 72;
const INITIAL_N = 128;
const MAX_CP = 0x10ffff;

function adapt(delta: number, numPoints: number, first: boolean): number {
  delta = first ? Math.floor(delta / DAMP) : Math.floor(delta / 2);
  delta += Math.floor(delta / numPoints);
  let k = 0;
  while (delta > ((BASE - TMIN) * TMAX) >> 1) {
    delta = Math.floor(delta / (BASE - TMIN));
    k += BASE;
  }
  return k + Math.floor(((BASE - TMIN + 1) * delta) / (delta + SKEW));
}

function digitOf(c: number): number {
  if (c >= 0x30 && c <= 0x39) return c - 22; // 0-9 -> 26..35
  if (c >= 0x61 && c <= 0x7a) return c - 0x61; // a-z -> 0..25
  return -1;
}

function digitChar(d: number): string {
  return String.fromCharCode(d < 26 ? d + 0x61 : d + 22);
}

/**
 * Longest input either function accepts. A DNS label is at most 63 octets, so
 * this is generous. It also bounds the work: the insertion step below is
 * quadratic in the output length, which is at most this many code points.
 */
export const MAX_PUNYCODE_INPUT = 256;

/** Decode the part after `xn--`. Null on any malformed or over-long input; never throws. Lowercase ASCII only. */
export function punycodeDecode(input: string): string | null {
  try {
    if (typeof input !== "string" || input.length > MAX_PUNYCODE_INPUT) return null;
    return decodeBounded(input);
  } catch {
    return null;
  }
}

function decodeBounded(input: string): string | null {
  const out: number[] = [];
  const basic = input.lastIndexOf("-");
  if (basic > 0) {
    for (let j = 0; j < basic; j++) {
      const c = input.charCodeAt(j);
      if (c >= 0x80) return null;
      out.push(c);
    }
  }
  let n = INITIAL_N;
  let i = 0;
  let bias = INITIAL_BIAS;
  let idx = basic > 0 ? basic + 1 : 0;
  while (idx < input.length) {
    const oldi = i;
    let w = 1;
    for (let k = BASE; ; k += BASE) {
      if (idx >= input.length) return null;
      const d = digitOf(input.charCodeAt(idx++));
      if (d < 0) return null;
      if (d > Math.floor((Number.MAX_SAFE_INTEGER - i) / w)) return null;
      i += d * w;
      const t = k <= bias ? TMIN : k >= bias + TMAX ? TMAX : k - bias;
      if (d < t) break;
      if (w > Math.floor(Number.MAX_SAFE_INTEGER / (BASE - t))) return null;
      w *= BASE - t;
    }
    const len = out.length + 1;
    bias = adapt(i - oldi, len, oldi === 0);
    if (Math.floor(i / len) > MAX_CP - n) return null;
    n += Math.floor(i / len);
    i %= len;
    if (n > MAX_CP || (n >= 0xd800 && n <= 0xdfff)) return null;
    out.splice(i++, 0, n);
  }
  let s = "";
  for (let j = 0; j < out.length; j += 1024) s += String.fromCodePoint(...out.slice(j, j + 1024));
  return s;
}

const MAX_INT = 0x7fffffff;

/**
 * Encode a string (without the `xn--` prefix). Lowercase output. Null when the
 * input is over-long or the arithmetic would leave the signed 32-bit range, as
 * RFC 3492 requires an encoder to report.
 */
export function punycodeEncode(input: string): string | null {
  try {
    if (typeof input !== "string" || input.length > MAX_PUNYCODE_INPUT * 16) return null;
    return encodeChecked(input);
  } catch {
    return null;
  }
}

function encodeChecked(input: string): string | null {
  const cps = Array.from(input, (c) => c.codePointAt(0)!);
  let out = "";
  for (const c of cps) if (c < 0x80) out += String.fromCharCode(c);
  const b = out.length;
  let h = b;
  if (b > 0) out += "-";
  let n = INITIAL_N;
  let delta = 0;
  let bias = INITIAL_BIAS;
  while (h < cps.length) {
    let m = MAX_CP + 1;
    for (const c of cps) if (c >= n && c < m) m = c;
    // Doubles are exact far past 2^31, so the range check can wait for the
    // increment below; no value is used before it is checked.
    delta += (m - n) * (h + 1);
    if (delta > MAX_INT) return null;
    n = m;
    for (const c of cps) {
      if (c < n && ++delta > MAX_INT) return null;
      if (c === n) {
        let q = delta;
        for (let k = BASE; ; k += BASE) {
          const t = k <= bias ? TMIN : k >= bias + TMAX ? TMAX : k - bias;
          if (q < t) break;
          out += digitChar(t + ((q - t) % (BASE - t)));
          q = Math.floor((q - t) / (BASE - t));
        }
        out += digitChar(q);
        bias = adapt(delta, h + 1, h === b);
        delta = 0;
        h++;
      }
    }
    if (++delta > MAX_INT) return null;
    n++;
  }
  return out;
}

const LABEL_RE = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const UNICODE_OK = /^[\p{L}\p{M}\p{N}-]+$/u;
const NON_ASCII = /[^\x00-\x7f]/;
// Right-to-left scripts, plus the explicit direction controls. v1 refuses every
// right-to-left label rather than implementing the IDNA bidi rules, so such
// domains are out of scope for the gate's email field. (The controls are format
// characters, already outside UNICODE_OK; they are listed for clarity.)
const RTL =
  /[\p{Script_Extensions=Arab}\p{Script_Extensions=Hebr}\p{Script_Extensions=Syrc}\p{Script_Extensions=Thaa}\p{Script_Extensions=Nkoo}\p{Script_Extensions=Samr}\p{Script_Extensions=Mand}\p{Script_Extensions=Adlm}\p{Script_Extensions=Rohg}\p{Script_Extensions=Mend}\p{Script_Extensions=Phnx}\p{Script_Extensions=Lydi}\p{Script_Extensions=Armi}\p{Script_Extensions=Avst}\p{Script_Extensions=Nbat}\p{Script_Extensions=Palm}\p{Script_Extensions=Hatr}\p{Script_Extensions=Prti}\p{Script_Extensions=Phli}\p{Script_Extensions=Phlp}\p{Script_Extensions=Sogd}\p{Script_Extensions=Sogo}\p{Script_Extensions=Mani}\p{Script_Extensions=Elym}\p{Script_Extensions=Chrs}\p{Script_Extensions=Yezi}\p{Script_Extensions=Orkh}\p{Script_Extensions=Hung}\p{Script_Extensions=Cprt}\p{Script_Extensions=Khar}\p{Script_Extensions=Sarb}\p{Script_Extensions=Narb}\p{Script_Extensions=Mero}\p{Script_Extensions=Merc}؜‎‏‪-‮⁦-⁩]/u;
const LEADING_MARK = /^\p{M}/u;

/**
 * True when `label` (lowercase, no dots) is a valid `xn--` A-label: at most 63
 * characters, decodes, and the decoded text has a non-ASCII letter, mark or
 * digit, no right-to-left script, no leading combining mark, is lowercase and
 * NFC, and encodes back to the same ASCII.
 */
export function isValidALabel(label: string): boolean {
  if (typeof label !== "string" || label.length > 63 || !label.startsWith("xn--")) return false;
  const rest = label.slice(4);
  if (rest === "") return false;
  const u = punycodeDecode(rest);
  if (u === null) return false;
  if (!NON_ASCII.test(u) || !UNICODE_OK.test(u)) return false;
  if (RTL.test(u) || LEADING_MARK.test(u)) return false;
  if (u !== u.toLowerCase() || u !== u.normalize("NFC")) return false;
  return punycodeEncode(u) === rest;
}

/**
 * One DNS label: lowercase letters, digits and inner hyphens, 1 to 63 characters.
 * A hyphen in both the third and fourth position is reserved, so such a label
 * must be a valid A-label.
 */
export function dnsLabelOk(label: string): boolean {
  if (!LABEL_RE.test(label)) return false;
  if (label.startsWith("xn--")) return isValidALabel(label);
  return label.slice(2, 4) !== "--";
}
