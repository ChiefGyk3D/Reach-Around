// SPDX-License-Identifier: Apache-2.0
//
// Canonical client-address text shared by the click and page-view routes, so
// every spelling of one address (case, zero compression, IPv4-mapped) gives
// the same rate-limit key. The result is only ever hashed, never stored.

function parseV4(s: string): number[] | null {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(s);
  if (!m) return null;
  const parts = [Number(m[1]), Number(m[2]), Number(m[3]), Number(m[4])];
  return parts.every((p) => p <= 255) ? parts : null;
}

/**
 * IPv4 as canonical dotted text; IPv6 cut to its first 64 bits (so one
 * household's privacy addresses count once); an IPv4-mapped IPv6 address
 * becomes its IPv4 form; anything else is null.
 */
export function normalizeAddress(ip: string | null): string | null {
  if (typeof ip !== "string" || ip.length === 0 || ip.length > 45) return null;
  const v4 = parseV4(ip);
  if (v4) return v4.join(".");
  if (!/^[0-9a-fA-F:.]+$/.test(ip) || !ip.includes(":")) return null;

  let text = ip;
  const lastColon = text.lastIndexOf(":");
  const tail = text.slice(lastColon + 1);
  if (tail.includes(".")) {
    const t = parseV4(tail);
    if (!t) return null;
    const hi = ((t[0] as number) << 8) | (t[1] as number);
    const lo = ((t[2] as number) << 8) | (t[3] as number);
    text = `${text.slice(0, lastColon + 1)}${hi.toString(16)}:${lo.toString(16)}`;
  }
  const halves = text.split("::");
  if (halves.length > 2) return null;
  const toGroups = (s: string): string[] | null => {
    if (s === "") return [];
    const g = s.split(":");
    return g.every((x) => /^[0-9a-fA-F]{1,4}$/.test(x)) ? g : null;
  };
  const head = toGroups(halves[0] as string);
  if (head === null) return null;
  let groups: string[];
  if (halves.length === 2) {
    const rest = toGroups(halves[1] as string);
    if (rest === null || head.length + rest.length > 7) return null;
    groups = [...head, ...Array<string>(8 - head.length - rest.length).fill("0"), ...rest];
  } else {
    if (head.length !== 8) return null;
    groups = head;
  }
  const n = groups.map((g) => Number.parseInt(g, 16));
  if (n.slice(0, 5).every((x) => x === 0) && n[5] === 0xffff) {
    const a = n[6] as number;
    const b = n[7] as number;
    return [a >> 8, a & 255, b >> 8, b & 255].join(".");
  }
  return `v6:${n
    .slice(0, 4)
    .map((x) => x.toString(16).padStart(4, "0"))
    .join("")}`;
}
