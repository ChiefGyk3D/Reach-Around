// SPDX-License-Identifier: Apache-2.0
//
// Lead storage for the media kit gate. Visitor data: an email address the
// visitor typed, a coarse country code, and timestamps. Never an IP address or
// a user agent; the schema has no column that could hold one.
//
// Every statement is a constant string with bound parameters.
//
// EMAIL CONTRACT. These functions take an email that is ALREADY normalized by
// Task 5 `normalizeEmail`: ASCII, lowercase, no whitespace or control
// characters, at most 254 characters. They do not normalize. As a second
// line of defense they throw a RangeError on anything else, so a caller bug
// cannot store `Partner@Example.com` as a duplicate of `partner@example.com`.

export type LeadSource = "gate" | "email";
export type KitEventKind = "unlock" | "view" | "verify";

export interface Lead {
  id: string;
  email: string;
  created_ts: number;
  last_seen_ts: number;
  verified_ts: number | null;
  source: LeadSource;
  country: string | null;
  last_view_alert_ts: number | null;
  last_view_event_ts: number | null;
  last_cc_alert_ts: number | null;
  session_floor_ts: number | null;
}

/** Unverified leads are kept this many days from creation (cutover slice D4). */
export const UNVERIFIED_RETENTION_DAYS = 30;
/** Hard maximum from creation for any lead (cutover slice D4). */
export const HARD_RETENTION_DAYS = 365;
export const LIST_DEFAULT = 100;
export const LIST_MAX = 500;

const HOUR_MS = 60 * 60 * 1000;
const EMAIL_RE = /^[\x21-\x7e]{3,254}$/;
const COUNTRY_RE = /^[A-Z]{2}$/;
const ID_RE = /^[0-9a-f]{32}$/;
const SOURCES: readonly string[] = ["gate", "email"];
const KINDS: readonly string[] = ["unlock", "view", "verify"];

function checkEmail(email: unknown): asserts email is string {
  if (typeof email !== "string" || !EMAIL_RE.test(email) || email !== email.toLowerCase()) {
    throw new RangeError("email must be a normalized ASCII lowercase address");
  }
}

function checkTs(n: unknown, what: string): asserts n is number {
  if (typeof n !== "number" || !Number.isFinite(n)) throw new RangeError(`${what} must be a finite number`);
}

function newLeadId(): string {
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}

/**
 * Insert a lead, or refresh `last_seen_ts` of the existing one. Returns null
 * only when `maxLeads` is set, the table is at that size, and the address is
 * new (a known address is always refreshed). The cap check, the insert and the
 * duplicate handling are one statement, so concurrent calls cannot overshoot
 * or duplicate.
 */
export async function upsertLead(
  db: D1Database,
  email: string,
  source: LeadSource,
  country: string | null,
  nowMs: number,
  maxLeads?: number,
): Promise<{ id: string; created: boolean } | null> {
  checkEmail(email);
  if (!SOURCES.includes(source)) throw new RangeError("unknown lead source");
  if (country !== null && (typeof country !== "string" || !COUNTRY_RE.test(country))) {
    throw new RangeError("country must be two uppercase letters or null");
  }
  checkTs(nowMs, "nowMs");
  const cap = maxLeads === undefined ? Number.MAX_SAFE_INTEGER : maxLeads;
  if (!Number.isInteger(cap) || cap < 1) throw new RangeError("maxLeads must be a positive integer");
  const now = Math.floor(nowMs);
  const id = newLeadId();
  const row = await db
    .prepare(
      `INSERT INTO leads (id, email, created_ts, last_seen_ts, source, country)
       SELECT ?1, ?2, ?3, ?3, ?4, ?5
       WHERE (SELECT COUNT(*) FROM leads) < ?6 OR EXISTS (SELECT 1 FROM leads WHERE email = ?2)
       ON CONFLICT (email) DO UPDATE SET last_seen_ts = excluded.last_seen_ts
       RETURNING id, (id = ?1) AS created`,
    )
    .bind(id, email, now, source, country, cap)
    .first<{ id: string; created: number }>();
  if (row === null) return null;
  return { id: row.id, created: row.created === 1 };
}

export async function getLead(db: D1Database, id: string): Promise<Lead | null> {
  if (typeof id !== "string" || !ID_RE.test(id)) return null;
  return db.prepare("SELECT * FROM leads WHERE id = ?").bind(id).first<Lead>();
}

export async function getLeadByEmail(db: D1Database, email: string): Promise<Lead | null> {
  if (typeof email !== "string") return null;
  return db.prepare("SELECT * FROM leads WHERE email = ?").bind(email).first<Lead>();
}

export async function countLeads(db: D1Database): Promise<number> {
  const r = await db.prepare("SELECT COUNT(*) AS n FROM leads").first<{ n: number }>();
  return r?.n ?? 0;
}

/**
 * Bounded page of leads, oldest first. `limit` defaults to LIST_DEFAULT and is
 * clamped to LIST_MAX. The cursor is the (created_ts, id) of the last row seen.
 */
export async function listLeads(
  db: D1Database,
  opts: { limit?: number; afterCreatedTs?: number; afterId?: string } = {},
): Promise<Lead[]> {
  const limit = opts.limit ?? LIST_DEFAULT;
  if (!Number.isInteger(limit) || limit < 1) throw new RangeError("limit must be a positive integer");
  const n = Math.min(limit, LIST_MAX);
  const afterTs = opts.afterCreatedTs ?? -1;
  const afterId = opts.afterId ?? "";
  checkTs(afterTs, "afterCreatedTs");
  if (typeof afterId !== "string") throw new RangeError("afterId must be a string");
  const res = await db
    .prepare(
      `SELECT * FROM leads
       WHERE created_ts > ?1 OR (created_ts = ?1 AND id > ?2)
       ORDER BY created_ts, id LIMIT ?3`,
    )
    .bind(afterTs, afterId, n)
    .all<Lead>();
  return res.results;
}

export async function touchLead(db: D1Database, id: string, nowMs: number): Promise<void> {
  checkTs(nowMs, "nowMs");
  await db.prepare("UPDATE leads SET last_seen_ts = ? WHERE id = ?").bind(Math.floor(nowMs), id).run();
}

export async function markVerified(db: D1Database, id: string, nowMs: number): Promise<void> {
  checkTs(nowMs, "nowMs");
  await db.prepare("UPDATE leads SET verified_ts = ? WHERE id = ?").bind(Math.floor(nowMs), id).run();
}

/**
 * Record a kit event. The time is rounded down to the hour on purpose: the
 * owner needs "who looked, roughly when", not a minute-level trail. Throws if
 * the lead does not exist (foreign key).
 */
export async function recordKitEvent(
  db: D1Database,
  leadId: string,
  kind: KitEventKind,
  nowMs: number,
): Promise<void> {
  if (!KINDS.includes(kind)) throw new RangeError("unknown kit event kind");
  checkTs(nowMs, "nowMs");
  const ts = Math.floor(nowMs / HOUR_MS) * HOUR_MS;
  await db
    .prepare("INSERT INTO kit_events (ts, lead_id, kind) VALUES (?, ?, ?)")
    .bind(ts, leadId, kind)
    .run();
}

/** Delete one lead (events cascade). Returns rows deleted: 1, or 0 when absent. */
export async function deleteLeadById(db: D1Database, id: string): Promise<number> {
  if (typeof id !== "string") return 0;
  // RETURNING, not meta.changes: D1 counts cascaded child rows in `changes`.
  const r = await db.prepare("DELETE FROM leads WHERE id = ? RETURNING id").bind(id).all();
  return r.results.length;
}

/** Delete one lead by normalized email (events cascade). Idempotent. */
export async function deleteLeadByEmail(db: D1Database, email: string): Promise<number> {
  if (typeof email !== "string") return 0;
  const r = await db.prepare("DELETE FROM leads WHERE email = ? RETURNING id").bind(email).all();
  return r.results.length;
}

/**
 * Delete unverified leads created strictly before `cutoffMs` (compute it as
 * now minus UNVERIFIED_RETENTION_DAYS). Verified leads are untouched.
 */
export async function purgeUnverifiedLeads(db: D1Database, cutoffMs: number): Promise<number> {
  checkTs(cutoffMs, "cutoffMs");
  const r = await db
    .prepare("DELETE FROM leads WHERE verified_ts IS NULL AND created_ts < ? RETURNING id")
    .bind(Math.floor(cutoffMs))
    .all();
  return r.results.length;
}

/** Hard maximum: delete every lead created strictly before `cutoffMs`. */
export async function purgeLeadsCreatedBefore(db: D1Database, cutoffMs: number): Promise<number> {
  checkTs(cutoffMs, "cutoffMs");
  const r = await db
    .prepare("DELETE FROM leads WHERE created_ts < ? RETURNING id")
    .bind(Math.floor(cutoffMs))
    .all();
  return r.results.length;
}
