// SPDX-License-Identifier: Apache-2.0
// An independent, plain TypeScript model of what the rollup must produce, used
// to check the SQL implementation row for row. It deliberately re-implements
// the rules (top-N, small-cell folding, hard caps, geography level, UTM
// allowlist) the slow, obvious way.
import { DIM_TOP, ROW_CAP_CORE, ROW_CAP_GEO } from "../../src/viewrollup";

export type Core = { page: string; src_kind: string; src: string; country: string; bot_class: string; views: number; uniques: number };
export type Dim = { dim: string; value: string; bot_class: string; views: number; uniques: number };
export type Geo = { country: string; region: string; city: string; bot_class: string; views: number; uniques: number };

export interface RawRow {
  page: string;
  src_kind: string;
  src: string;
  country: string;
  region: string;
  city: string;
  browser: string;
  os: string;
  device: string;
  lang: string;
  vp: string;
  net: string;
  bot_class: string;
  reason: string;
  first: number;
}

export interface Opts {
  top: number;
  min: number;
  geoLevel: "country" | "region" | "city";
  allow: string[] | null;
}

function byViews(a: { views: number }, b: { views: number }, ka: string, kb: string): number {
  return b.views - a.views || (ka < kb ? -1 : ka > kb ? 1 : 0);
}

function merge<T extends { views: number; uniques: number }>(rows: T[], key: (r: T) => string): T[] {
  const m = new Map<string, T>();
  for (const r of rows) {
    const k = key(r);
    const c = m.get(k);
    if (c) {
      c.views += r.views;
      c.uniques += r.uniques;
    } else m.set(k, { ...r });
  }
  return [...m.values()];
}

const coreKey = (r: Core) => [r.page, r.src_kind, r.src, r.country, r.bot_class].join("\u0000");
const dimKey = (r: Dim) => [r.dim, r.value, r.bot_class].join("\u0000");
const geoKey = (r: Geo) => [r.country, r.region, r.city, r.bot_class].join("\u0000");

function hardCap<T extends { views: number; uniques: number }>(rows: T[], cap: number, key: (r: T) => string, fold: (r: T) => T, reserve: number): T[] {
  if (rows.length <= cap) return rows;
  const sorted = [...rows].sort((a, b) => byViews(a, b, key(a), key(b)));
  const keepN = Math.max(1, cap - reserve);
  return merge([...sorted.slice(0, keepN), ...sorted.slice(keepN).map(fold)], key);
}

export function oracleCore(raw: RawRow[], o: Opts): Core[] {
  let rows = merge(
    raw.map((r) => {
      const hide = o.allow !== null && r.src_kind === "u" && !o.allow.includes(r.src);
      return { page: r.page, src_kind: hide ? "o" : r.src_kind, src: hide ? "other" : r.src, country: r.country, bot_class: r.bot_class, views: 1, uniques: r.first };
    }),
    coreKey,
  );
  const tot = new Map<string, number>();
  for (const r of rows) if (r.src_kind === "r" || r.src_kind === "u") tot.set(`${r.src_kind}\u0000${r.src}`, (tot.get(`${r.src_kind}\u0000${r.src}`) ?? 0) + r.views);
  const keep = new Set([...tot.entries()].sort((a, b) => byViews({ views: a[1] }, { views: b[1] }, a[0], b[0])).slice(0, o.top).map(([k]) => k));
  rows = merge(
    rows.map((r) => ((r.src_kind === "r" || r.src_kind === "u") && !keep.has(`${r.src_kind}\u0000${r.src}`) ? { ...r, src_kind: "o", src: "other" } : r)),
    coreKey,
  );
  rows = merge(rows.map((r) => (r.views < o.min && (r.src_kind === "r" || r.src_kind === "u") ? { ...r, src_kind: "o", src: "other" } : r)), coreKey);
  rows = merge(rows.map((r) => (r.views < o.min && r.country !== "ZZ" ? { ...r, country: "ZZ" } : r)), coreKey);
  return hardCap(rows, ROW_CAP_CORE, coreKey, (r) => ({ ...r, src_kind: "o", src: "other", country: "ZZ" }), 40);
}

export function oracleDim(raw: RawRow[], o: Opts): Dim[] {
  const out: Dim[] = [];
  for (const col of Object.keys(DIM_TOP)) {
    let rows = merge(
      raw.map((r) => ({ dim: col, value: String((r as unknown as Record<string, unknown>)[col]), bot_class: r.bot_class, views: 1, uniques: r.first })),
      dimKey,
    );
    const tot = new Map<string, number>();
    for (const r of rows) tot.set(r.value, (tot.get(r.value) ?? 0) + r.views);
    const keep = new Set([...tot.entries()].sort((a, b) => byViews({ views: a[1] }, { views: b[1] }, a[0], b[0])).slice(0, DIM_TOP[col]).map(([v]) => v));
    rows = merge(rows.map((r) => (keep.has(r.value) ? r : { ...r, value: "other" })), dimKey);
    rows = merge(rows.map((r) => (r.views < o.min && r.value !== "other" ? { ...r, value: "other" } : r)), dimKey);
    out.push(...rows);
  }
  return out;
}

export function oracleGeo(raw: RawRow[], o: Opts): Geo[] {
  const min = Math.max(5, o.min);
  let rows = merge(
    raw.map((r) => ({
      country: r.country,
      region: o.geoLevel === "country" ? "" : r.region,
      city: o.geoLevel === "city" ? r.city : "",
      bot_class: r.bot_class,
      views: 1,
      uniques: r.first,
    })),
    geoKey,
  );
  rows = merge(rows.map((r) => (r.city !== "" && r.views < min ? { ...r, city: "" } : r)), geoKey);
  rows = merge(rows.map((r) => (r.city === "" && r.region !== "" && r.views < min ? { ...r, region: "" } : r)), geoKey);
  return hardCap(rows, ROW_CAP_GEO, geoKey, (r) => ({ ...r, country: "ZZ", region: "", city: "" }), 8);
}

/** A canonical, order-independent string for comparing two row sets. */
export function canon<T extends Record<string, unknown>>(rows: T[]): string {
  return JSON.stringify(rows.map((r) => JSON.stringify(Object.keys(r).sort().map((k) => [k, r[k]]))).sort());
}
