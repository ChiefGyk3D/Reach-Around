// SPDX-License-Identifier: Apache-2.0
//
// Destination checks (SSRF rules). Each returns the safe value or null; none
// throws and none echoes its input. A destination that fails is simply not a
// configured channel.

/** Discord incoming webhook: https, the Discord hosts, the webhook path shape, no query, port or credentials. */
const DISCORD_RE =
  /^https:\/\/(?:(?:ptb|canary)\.)?(?:discord|discordapp)\.com\/api(?:\/v[0-9]{1,2})?\/webhooks\/[0-9]{15,25}\/[A-Za-z0-9_-]{20,100}$/;

export function validateDiscordWebhook(raw: unknown): string | null {
  return typeof raw === "string" && raw.length <= 300 && DISCORD_RE.test(raw) ? raw : null;
}

const PRIVATE_SUFFIXES = [
  "localhost", "local", "internal", "localdomain", "lan", "home", "corp", "intranet", "private",
  "test", "invalid", "example", "onion", "arpa", "home.arpa",
];
const LABEL_RE = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const TLD_RE = /^(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$/;

/**
 * A Matrix homeserver origin: `https://host` only. No userinfo, port, path,
 * query or fragment; the host is a DNS name (never an IP literal in any
 * notation, never a single label, never a private or reserved suffix).
 * Returns the lowercase host.
 *
 * LIMIT: a public name can still resolve to a private address (DNS rebinding).
 * A Worker's fetch runs on Cloudflare's network and cannot reach a private
 * network on its own, which is the real protection; this check is the first
 * layer, not the only one.
 */
export function validateHomeserver(raw: unknown): string | null {
  if (typeof raw !== "string" || raw.length > 270) return null;
  const m = /^https:\/\/([^/:?#@\s\\[\]]+)\/?$/.exec(raw);
  if (m === null) return null;
  const host = m[1]!.toLowerCase();
  if (host.length > 253 || host.endsWith(".") || host.startsWith(".")) return null;
  const labels = host.split(".");
  if (labels.length < 2) return null;
  if (!labels.every((l) => LABEL_RE.test(l))) return null;
  if (!TLD_RE.test(labels[labels.length - 1]!)) return null; // rejects 127.0.0.1, 2130706433, 0x7f.1
  for (const suffix of PRIVATE_SUFFIXES) {
    if (host === suffix || host.endsWith(`.${suffix}`)) return null;
  }
  return host;
}

/** Matrix room id: `!opaque:server` (newer room versions have no server part). */
const ROOM_RE = /^![A-Za-z0-9._=+-]{1,128}(?::[a-z0-9.-]{1,253})?$/;
export function validateRoomId(raw: unknown): string | null {
  return typeof raw === "string" && ROOM_RE.test(raw) ? raw : null;
}

/** Access token shape only (never logged, never stored). */
const TOKEN_RE = /^[A-Za-z0-9._~+/=-]{8,512}$/;
export function validateToken(raw: unknown): string | null {
  return typeof raw === "string" && TOKEN_RE.test(raw) ? raw : null;
}

/** A plain recipient address for the email adapter. */
const EMAIL_RE = /^[A-Za-z0-9._%+-]{1,64}@(?:[A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,63}$/;
export function validateRecipient(raw: unknown): string | null {
  return typeof raw === "string" && raw.length <= 254 && EMAIL_RE.test(raw) ? raw : null;
}
