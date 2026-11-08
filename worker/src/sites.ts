// SPDX-License-Identifier: Apache-2.0
//
// The SITES configuration (docs/analytics-design.md section 4).
//
// One non-secret Worker variable, `SITES`, lists the sites this collector
// serves. `SITE_HOST` + `PAGE_IDS` + `LINK_IDS` stay accepted as the one-site
// shorthand for the site `links`, so an existing deployment keeps working.
//
// Fail-closed rules, all tested in worker/test/views/sites.test.ts:
//   - SITES that is set but unusable as a whole (too big, not JSON, too deep,
//     not an object, empty, more than MAX_SITES entries) loads NO site. It
//     never falls back to the shorthand.
//   - A bad value inside one site disables that one site only.
//   - A host may belong to one site; a duplicate disables every site that
//     lists it ('dup-host'), so one site's traffic can never land in another.
//   - A duplicate site id, or a duplicate key inside a site, disables that
//     site (JSON.parse alone would silently keep the last one).
//   - Unknown keys disable the site, so `"bots": "drop"` (not allowed for any
//     site) or a typo cannot pass quietly.
//   - Errors are CLOSED CODES (letters, digits, '-' and ':' only). Text from
//     the config is never put into an error, a log or view_meta; a site id
//     appears only after it passed the slug pattern.
//   - The parsed result is memoized per input text, so the 10 ms request
//     budget is not spent re-parsing on every request.

import { MAX_ORIGIN_HEADER } from "./click";

export const MAX_SITES = 8;
export const MAX_HOSTS_PER_SITE = 4;
export const MAX_PAGES = 200;
export const MAX_LINK_IDS = 200;
export const MAX_UTM_LABELS = 40;
export const MAX_SOURCES = 40;
/** Largest SITES text that is parsed at all. */
export const MAX_SITES_TEXT = 16_384;
const MAX_DEPTH = 8;
export const MAX_MIN_CELL = 50;
export const MAX_RAW_KEEP = 90;

export type GeoLevel = "country" | "region" | "city";

export interface Site {
  id: string;
  hosts: string[];
  mode: "beacon" | "edge";
  /** Beacon: the allowed page ids. Edge: the literal "html-200". */
  pages: ReadonlySet<string> | "html-200";
  /** Link or action id -> page id it belongs to, or "*" for no single page. */
  linkIds: ReadonlyMap<string, string>;
  geo?: GeoLevel;
  utmAllowlist?: string[];
  /** Closed list of `s` source labels (bio links and the like); absent means `s` is not read. */
  sources?: string[];
  minCell?: number;
  rawKeepDays?: number;
}

export interface SitesResult {
  sites: Site[];
  /** Closed codes only. */
  errors: string[];
}

export interface SitesEnv {
  SITES?: string;
  SITE_HOST?: string;
  PAGE_IDS?: string;
  LINK_IDS?: string;
}

const SLUG = /^[a-z0-9][a-z0-9-]{0,39}$/;
const SITE_ID = /^[a-z0-9][a-z0-9-]{0,19}$/;
const UTM = /^[a-z0-9-]{1,30}$/;
const LABEL = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;
const SITE_KEYS = new Set(["hosts", "mode", "pages", "link_ids", "geo", "utm_allowlist", "sources", "min_cell", "raw_keep_days"]);

/** Lower-case host name: two or more labels, final label has a letter, no port, wildcard, path or IP literal. */
export function validHost(host: unknown): host is string {
  if (typeof host !== "string" || host.length === 0 || host.length > 253) return false;
  const labels = host.split(".");
  if (labels.length < 2) return false;
  for (const l of labels) if (!LABEL.test(l)) return false;
  return /[a-z]/.test(labels[labels.length - 1] as string);
}

// ------------------------------------------------------------ duplicate keys

interface Scan {
  ok: boolean;
  /** Site ids that appear twice as a top-level key. */
  dupIds: Set<string>;
  /** Site ids with a duplicate key somewhere inside. */
  dupInside: Set<string>;
}

/**
 * Walk the JSON text once and report duplicate object keys, which JSON.parse
 * would resolve silently to the last one. Strings are skipped correctly
 * (escapes), nesting is capped at MAX_DEPTH. Assumes nothing about validity:
 * JSON.parse is the judge of that.
 */
function scan(text: string): Scan {
  const out: Scan = { ok: true, dupIds: new Set(), dupInside: new Set() };
  const stack: Array<Set<string> | null> = [];
  let site = "";
  let i = 0;
  const n = text.length;
  while (i < n) {
    const c = text.charCodeAt(i);
    if (c === 0x22) {
      let j = i + 1;
      while (j < n && text.charCodeAt(j) !== 0x22) j += text.charCodeAt(j) === 0x5c ? 2 : 1;
      let k = j + 1;
      while (k < n && " \t\r\n".includes(text[k] as string)) k++;
      const top = stack[stack.length - 1];
      if (top && text[k] === ":") {
        let key: string;
        try {
          key = JSON.parse(text.slice(i, j + 1)) as string;
        } catch {
          key = text.slice(i, j + 1);
        }
        if (stack.length === 1) site = key;
        if (top.has(key)) {
          if (stack.length === 1) out.dupIds.add(key);
          else out.dupInside.add(site);
        }
        top.add(key);
      }
      i = j + 1;
      continue;
    }
    if (c === 0x7b || c === 0x5b) {
      stack.push(c === 0x7b ? new Set() : null);
      if (stack.length > MAX_DEPTH) {
        out.ok = false;
        return out;
      }
    } else if (c === 0x7d || c === 0x5d) {
      stack.pop();
    }
    i++;
  }
  return out;
}

// --------------------------------------------------------------- validation

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function clampInt(v: unknown, max: number): number | null {
  if (typeof v !== "number" || !Number.isSafeInteger(v) || v < 1) return null;
  return Math.min(v, max);
}

function slugList(v: unknown, max: number): string[] | null {
  if (!Array.isArray(v) || v.length > max) return null;
  const out: string[] = [];
  for (const x of v) {
    if (typeof x !== "string" || !SLUG.test(x)) return null;
    out.push(x);
  }
  return out;
}

/** Validate one site body. Returns the Site or null (the caller records the code). */
function buildSite(id: string, body: unknown): Site | null {
  if (!isPlainObject(body)) return null;
  for (const k of Object.keys(body)) if (!SITE_KEYS.has(k)) return null;

  const hostsRaw = body.hosts;
  if (!Array.isArray(hostsRaw) || hostsRaw.length < 1 || hostsRaw.length > MAX_HOSTS_PER_SITE) return null;
  const hosts: string[] = [];
  for (const h of hostsRaw) {
    if (!validHost(h) || hosts.includes(h)) return null;
    hosts.push(h);
  }

  const mode = body.mode;
  if (mode !== "beacon" && mode !== "edge") return null;

  let pages: Site["pages"];
  if (mode === "edge") {
    if (body.pages !== "html-200") return null;
    pages = "html-200";
  } else {
    const list = slugList(body.pages, MAX_PAGES);
    if (list === null) return null;
    pages = new Set(list);
  }

  const linkIds = new Map<string, string>();
  if (body.link_ids !== undefined) {
    if (!isPlainObject(body.link_ids)) return null;
    const entries = Object.entries(body.link_ids);
    if (entries.length > MAX_LINK_IDS) return null;
    for (const [lid, page] of entries) {
      if (!SLUG.test(lid) || typeof page !== "string") return null;
      if (page !== "*") {
        if (!SLUG.test(page)) return null;
        if (pages !== "html-200" && !pages.has(page)) return null;
      }
      linkIds.set(lid, page);
    }
  }

  const site: Site = { id, hosts, mode, pages, linkIds };

  if (body.geo !== undefined) {
    if (body.geo !== "country" && body.geo !== "region" && body.geo !== "city") return null;
    site.geo = body.geo;
  }
  if (body.utm_allowlist !== undefined) {
    const u = body.utm_allowlist;
    if (!Array.isArray(u) || u.length > MAX_UTM_LABELS) return null;
    const labels: string[] = [];
    for (const x of u) {
      if (typeof x !== "string" || !UTM.test(x)) return null;
      if (!labels.includes(x)) labels.push(x);
    }
    site.utmAllowlist = labels;
  }
  if (body.sources !== undefined) {
    const u = body.sources;
    if (!Array.isArray(u) || u.length > MAX_SOURCES) return null;
    const labels: string[] = [];
    for (const x of u) {
      // "other" is the fold target, so it cannot also be a listed label.
      if (typeof x !== "string" || !UTM.test(x) || x === "other") return null;
      if (!labels.includes(x)) labels.push(x);
    }
    site.sources = labels;
  }
  if (body.min_cell !== undefined) {
    const n = clampInt(body.min_cell, MAX_MIN_CELL);
    if (n === null) return null;
    site.minCell = n;
  }
  if (body.raw_keep_days !== undefined) {
    const n = clampInt(body.raw_keep_days, MAX_RAW_KEEP);
    if (n === null) return null;
    site.rawKeepDays = n;
  }
  return site;
}

function legacy(env: SitesEnv): SitesResult {
  const host = typeof env.SITE_HOST === "string" ? env.SITE_HOST.trim().toLowerCase() : "";
  if (host === "" && env.PAGE_IDS === undefined) return { sites: [], errors: [] };
  if (!validHost(host)) return { sites: [], errors: ["legacy-host"] };
  const ids = (raw: unknown, ok: (s: string) => boolean): string[] | null => {
    if (raw === undefined) return [];
    if (typeof raw !== "string" || raw.length > MAX_SITES_TEXT) return null;
    try {
      const parsed: unknown = JSON.parse(raw);
      if (!Array.isArray(parsed)) return null;
      return parsed.filter((v): v is string => typeof v === "string" && ok(v));
    } catch {
      return null;
    }
  };
  // PAGE_IDS: every entry must be a valid page id, else the site is refused
  // (a bad id in the build manifest is a deployment mistake worth failing on).
  let pages: string[] = [];
  if (env.PAGE_IDS !== undefined) {
    if (typeof env.PAGE_IDS !== "string" || env.PAGE_IDS.length > MAX_SITES_TEXT) return { sites: [], errors: ["legacy-pages"] };
    try {
      const parsed: unknown = JSON.parse(env.PAGE_IDS);
      const list = slugList(parsed, MAX_PAGES);
      if (list === null) return { sites: [], errors: ["legacy-pages"] };
      pages = list;
    } catch {
      return { sites: [], errors: ["legacy-pages"] };
    }
  }
  // LINK_IDS keeps its historical leniency: unusable entries are skipped.
  const links = ids(env.LINK_IDS, (s) => s.length >= 1 && s.length <= 40 && !s.includes("/") && s !== "." && s !== "..") ?? [];
  const linkIds = new Map<string, string>();
  for (const l of links.slice(0, MAX_LINK_IDS)) linkIds.set(l, "*");
  return { sites: [{ id: "links", hosts: [host], mode: "beacon", pages: new Set(pages), linkIds }], errors: [] };
}

function parseSites(text: string): SitesResult {
  const fail = (code: string): SitesResult => ({ sites: [], errors: [code] });
  if (text.length > MAX_SITES_TEXT) return fail("sites-too-big");
  const dup = scan(text);
  if (!dup.ok) return fail("sites-json");
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return fail("sites-json");
  }
  if (!isPlainObject(parsed)) return fail("sites-shape");
  const entries = Object.entries(parsed);
  if (entries.length === 0) return fail("sites-empty");
  if (entries.length > MAX_SITES) return fail("too-many-sites");

  const errors = new Set<string>();
  const built: Site[] = [];
  for (const [id, body] of entries) {
    if (!SITE_ID.test(id)) {
      errors.add("bad-site-id");
      continue;
    }
    if (dup.dupIds.has(id)) {
      errors.add("dup-site-id");
      continue;
    }
    const site = dup.dupInside.has(id) ? null : buildSite(id, body);
    if (site === null) {
      errors.add(`site-bad:${id}`);
      continue;
    }
    built.push(site);
  }

  // A host belongs to one site only: a duplicate disables every site that lists it.
  const owners = new Map<string, string[]>();
  for (const s of built) for (const h of s.hosts) owners.set(h, [...(owners.get(h) ?? []), s.id]);
  const clash = new Set<string>();
  for (const list of owners.values()) if (list.length > 1) for (const sid of list) clash.add(sid);
  if (clash.size > 0) errors.add("dup-host");
  return { sites: built.filter((s) => !clash.has(s.id)), errors: [...errors].sort() };
}

const memo = new Map<string, SitesResult>();

/**
 * Load the site list from the environment. Never throws. Memoized by the
 * input text (the memo is tiny: a deployment has one text).
 */
export function loadSites(env: SitesEnv): SitesResult {
  const raw = env.SITES;
  let key: string;
  let run: () => SitesResult;
  if (typeof raw === "string" && raw.trim() !== "") {
    key = `S|${raw}`;
    run = () => parseSites(raw);
  } else {
    key = `L|${String(env.SITE_HOST)}|${String(env.PAGE_IDS)}|${String(env.LINK_IDS)}`;
    run = () => legacy(env);
  }
  const hit = memo.get(key);
  if (hit) return hit;
  let result: SitesResult;
  try {
    result = run();
  } catch {
    result = { sites: [], errors: ["sites-json"] };
  }
  if (memo.size >= 16) memo.clear();
  memo.set(key, result);
  return result;
}

// ---------------------------------------------------------------- resolving

function hostOf(value: string | null): string | null {
  if (!value) return null;
  try {
    return new URL(value).host.toLowerCase();
  } catch {
    return null;
  }
}

/**
 * The site whose host matches the request's Ping-From or Origin, or null. The
 * site is NEVER read from the body, the path or any other client claim. This
 * is a noise filter only (the sender controls both headers, threat A1).
 */
export function siteForRequest(sites: readonly Site[], request: Request): Site | null {
  // Length guard first: an oversize header is never handed to the URL parser.
  for (const name of ["Ping-From", "Origin"]) {
    const v = request.headers.get(name);
    if (v !== null && v.length > MAX_ORIGIN_HEADER) return null;
  }
  const from = hostOf(request.headers.get("Ping-From"));
  const origin = hostOf(request.headers.get("Origin"));
  if (from === null && origin === null) return null;
  for (const s of sites) {
    for (const h of s.hosts) if (from === h || origin === h) return s;
  }
  return null;
}

/** VIEWS_KILL_SITES / CLICKS_KILL_SITES: a comma list of site ids; junk entries are ignored. */
export function killedSites(raw: unknown): Set<string> {
  const out = new Set<string>();
  if (typeof raw !== "string" || raw.length > 512) return out;
  for (const part of raw.split(",")) {
    const id = part.trim();
    if (SITE_ID.test(id)) out.add(id);
  }
  return out;
}

/** Closed-code summary for view_meta('config_err'): empty string when fine. */
export function configErrText(errors: readonly string[]): string {
  return errors.slice(0, 8).join(",").slice(0, 200);
}
