// SPDX-License-Identifier: Apache-2.0
//
// The leads database is a different database from the analytics database, and holds the only copy of
// every lead (issue #772). This is the ONE accessor that proves it; the gate, the view route and the
// lead purge all go through it, so no path can skip it.
//
// WHAT A D1 BINDING EXPOSES. Read 2026-11-24 on developers.cloudflare.com/d1/worker-api/d1-database/
// and /workers/wrangler/configuration/: a D1Database has prepare(), batch(), exec(), dump() and
// withSession(); the `database_id` lives only in the Wrangler configuration, and a statement's `meta`
// carries timings and counts, not an id. So the Worker cannot read a database id, and two binding names
// for one database look like two different objects (object identity proves nothing: a wrapper or a
// second binding defeats it). What the Worker CAN observe is what is stored in the database, so each
// database states who it is: the table db_identity holds one row, 'analytics' (worker/migrations) or
// 'leads' (worker/leads-migrations). Both bindings are read; LEADS_DB must answer exactly 'leads' and DB
// exactly 'analytics'. One database under two names answers the same thing twice, so one side fails.
//
// ANALYTICS MUST HOLD NO LEADS. The analytics database used to hold the lead tables. While `leads` or
// `kit_events` there still has a row, the lead data is readable by anything with an analytics-only
// credential, so the Worker refuses to serve leads at all until the one-time move
// (scripts/move-legacy-leads.py, docs/deploy.md) has emptied them. Absent tables are fine.
//
// FAIL CLOSED. Any error, missing table, extra row or wrong answer refuses. A verdict is remembered per
// pair of bindings for a short time (so a busy route does not pay the probes on every request); a
// refusal is remembered for a shorter time. The probes use the raw bindings, never a metering wrapper;
// a caller that has a meter passes it, and the probes take SEPARATION_QUERIES from it.

import { BudgetExceeded, QueryMeter, rawDb } from "../d1budget";
import { isLeadsDb } from "./env";

/** The separation proof failed. Carries no value from the databases. */
export class LeadsSeparationError extends Error {
  constructor() {
    super("leads database separation not proven");
    this.name = "LeadsSeparationError";
  }
}

export const SEPARATION_OK_TTL_MS = 60_000;
export const SEPARATION_REFUSED_TTL_MS = 10_000;
/** Worst-case D1 queries of one uncached proof: one on LEADS_DB, a batch on DB, and one more once the legacy tables are gone. */
export const SEPARATION_QUERIES = 3;

interface Verdict {
  analytics: object;
  ok: boolean;
  until: number;
}
let remembered = new WeakMap<object, Verdict>();

/** Forget every verdict (tests, and the one-time move). */
export function resetSeparationCache(): void {
  remembered = new WeakMap();
}

const ROLES = "SELECT role FROM db_identity ORDER BY role";

async function soleRole(db: D1Database, meter: QueryMeter | undefined): Promise<string[]> {
  meter?.take();
  return (await db.prepare(ROLES).all<{ role: string }>()).results.map((x) => x.role);
}

/** The analytics database's roles and whether it still holds lead rows. Throws on any error. */
async function analyticsFacts(analytics: D1Database, meter: QueryMeter | undefined): Promise<{ roles: string[]; leadRows: number }> {
  meter?.take();
  try {
    const [r, l, e] = await analytics.batch<{ role?: string; n?: number }>([
      analytics.prepare(ROLES),
      analytics.prepare("SELECT COUNT(*) AS n FROM (SELECT 1 FROM leads LIMIT 1)"),
      analytics.prepare("SELECT COUNT(*) AS n FROM (SELECT 1 FROM kit_events LIMIT 1)"),
    ]);
    return {
      roles: r!.results.map((x) => String(x.role)),
      leadRows: Number(l!.results[0]?.n ?? 1) + Number(e!.results[0]?.n ?? 1),
    };
  } catch {
    // The lead tables may be gone (a later contract migration drops them). Prove that, never assume it.
    meter?.take();
    const left = await analytics
      .prepare("SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table' AND name IN ('leads', 'kit_events')")
      .first<{ n: number }>();
    if (left === null || left.n !== 0) throw new LeadsSeparationError();
    const roles = (await analytics.prepare(ROLES).all<{ role: string }>()).results.map((x) => x.role);
    return { roles, leadRows: 0 };
  }
}

async function prove(leads: D1Database, analytics: D1Database, meter: QueryMeter | undefined): Promise<boolean> {
  try {
    const l = await soleRole(leads, meter);
    if (l.length !== 1 || l[0] !== "leads") return false;
    const a = await analyticsFacts(analytics, meter);
    if (a.roles.length !== 1 || a.roles[0] !== "analytics") return false;
    return a.leadRows === 0;
  } catch {
    return false;
  }
}

export interface SeparationOptions {
  /** A meter the probes count against (only when they actually run); too little room throws BudgetExceeded without probing. */
  meter?: QueryMeter;
  nowMs?: number;
}

/**
 * Resolve only when LEADS_DB is provably a different database from DB, by the identity each database
 * states, and DB holds no lead rows. Throws TypeError for a binding that is not a database at all and
 * LeadsSeparationError for everything else. Call it before the first read or write of lead data.
 */
export async function assertLeadsSeparated(env: { DB?: unknown; LEADS_DB?: unknown }, opts: SeparationOptions = {}): Promise<void> {
  const nowMs = opts.nowMs ?? Date.now();
  if (!isLeadsDb(env?.DB)) throw new TypeError("DB is not bound");
  if (!isLeadsDb(env.LEADS_DB, env.DB)) throw new TypeError("LEADS_DB is not bound");
  const leads = rawDb(env.LEADS_DB);
  const analytics = rawDb(env.DB as D1Database);
  if (leads === analytics) throw new LeadsSeparationError();
  const seen = remembered.get(leads);
  if (seen !== undefined && seen.analytics === analytics && seen.until > nowMs) {
    if (seen.ok) return;
    throw new LeadsSeparationError();
  }
  if (opts.meter !== undefined && opts.meter.remaining() < SEPARATION_QUERIES) {
    // Not enough room to prove it: nothing is served and nothing is remembered. The caller decides how to say so.
    throw new BudgetExceeded();
  }
  const ok = await prove(leads, analytics, opts.meter);
  remembered.set(leads, { analytics, ok, until: nowMs + (ok ? SEPARATION_OK_TTL_MS : SEPARATION_REFUSED_TTL_MS) });
  if (!ok) throw new LeadsSeparationError();
}
