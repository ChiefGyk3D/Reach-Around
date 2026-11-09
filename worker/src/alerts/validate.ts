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
/**
 * Well-known wildcard-DNS and rebinding helper services: any name under them
 * can be made to resolve to any address, including a private one. Not
 * exhaustive; the allowed_hosts list is the real control.
 */
export const WILDCARD_DNS_SUFFIXES = [
  "nip.io", "sslip.io", "xip.io", "traefik.me", "localtest.me", "lvh.me", "localhost.run", "lacolhost.com",
  "vcap.me", "fbi.com", "1u.ms", "rebind.it", "rbndr.us", "rebind.network", "ssrf.cc", "local.gd",
  "xip.name", "nip.name",
];
const LABEL_RE = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const TLD_RE = /^(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$/;

/**
 * True when a lowercase host name embeds an IP address in any label form: a
 * label that is all digits or 0x hex (decimal, octal, hex parts), an 8-digit
 * hex label holding a digit (a packed address), or four numbers of 1 to 3
 * digits joined by dots or dashes anywhere (`127.0.0.1.example`, `10-0-0-1`).
 */
export function embedsIpLiteral(host: string): boolean {
  for (const label of host.split(".")) {
    if (/^[0-9]+$/.test(label) || /^0x[0-9a-f]*$/.test(label)) return true;
    if (/^[0-9a-f]{8}$/.test(label) && /[0-9]/.test(label)) return true;
  }
  return /(?:^|[^0-9])[0-9]{1,3}(?:[.-][0-9]{1,3}){3}(?:$|[^0-9])/.test(host);
}

function underSuffix(host: string, suffixes: readonly string[]): boolean {
  return suffixes.some((suffix) => host === suffix || host.endsWith(`.${suffix}`));
}

/**
 * The shape rules for a Matrix homeserver host name: a DNS name (never an IP
 * literal in any notation, never one that embeds an address, never a single
 * label, never a private, reserved or wildcard-DNS suffix). Returns the
 * lowercase host or null.
 */
export function validateHostShape(raw: unknown): string | null {
  if (typeof raw !== "string" || raw.length > 253) return null;
  const host = raw.toLowerCase();
  if (host.endsWith(".") || host.startsWith(".")) return null;
  const labels = host.split(".");
  if (labels.length < 2) return null;
  if (!labels.every((l) => LABEL_RE.test(l))) return null;
  if (!TLD_RE.test(labels[labels.length - 1]!)) return null; // rejects 127.0.0.1, 2130706433, 0x7f.1
  if (embedsIpLiteral(host)) return null;
  if (underSuffix(host, PRIVATE_SUFFIXES) || underSuffix(host, WILDCARD_DNS_SUFFIXES)) return null;
  return host;
}

/**
 * A Matrix homeserver origin: `https://host` only (no userinfo, port, path,
 * query or fragment), whose host passes validateHostShape AND is an exact
 * match for an entry of `allowedHosts` (the owner's alerts.matrix.allowed_hosts).
 * An empty list allows nothing. Returns the lowercase host.
 *
 * RESIDUAL RISK, stated plainly: the name authorizes the access token to be
 * sent to whatever it resolves to. A Worker cannot pin DNS, so a name the
 * owner does not control could rebind to another address. Use a homeserver you
 * control. See docs/alerts.md.
 */
export function validateHomeserver(raw: unknown, allowedHosts: readonly string[]): string | null {
  if (typeof raw !== "string" || raw.length > 270) return null;
  const m = /^https:\/\/([^/:?#@\s\\[\]]+)\/?$/.exec(raw);
  if (m === null) return null;
  const host = validateHostShape(m[1]);
  if (host === null) return null;
  return allowedHosts.includes(host) ? host : null;
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
