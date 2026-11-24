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
// NO CACHED APPROVAL. A remembered "separated" answer would let a lead row appear in analytics (an
// older Worker still writing there), or a binding be retargeted, and still be served for the rest of the
// window. So an approval is never remembered: every lead operation proves it again, right before it
// reads or writes. Only a REFUSAL is remembered (REFUSED_TTL_MS, fail closed, so a refused deployment
// does not pay the probes on every request). What remains is the few milliseconds between the proof's
// last query and the operation's own query, which no check done through a separate query can close; the
// one-time move and a stop of any older Worker are the operational controls (docs/deploy.md).
//
// FAIL CLOSED. Any error, missing table, extra row or wrong answer refuses. The probes use the raw
// bindings, never a metering wrapper; a caller that has a meter passes it, and the probes take what they
// really use from it (at most SEPARATION_QUERIES).

import { BudgetExceeded, QueryMeter, rawDb } from "../d1budget";
import { isLeadsDb } from "./env";

/** The separation proof failed. Carries no value from the databases. */
export class LeadsSeparationError extends Error {
  constructor() {
    super("leads database separation not proven");
    this.name = "LeadsSeparationError";
  }
}

export const SEPARATION_REFUSED_TTL_MS = 10_000;
/**
 * D1 queries of one proof, worst case, each one counted: 1 on LEADS_DB (its identity), 1 batch on DB
 * (its identity and which lead tables exist), 1 on DB to look for rows when a lead table exists.
 */
export const SEPARATION_QUERIES = 3;

interface Refusal {
  analytics: object;
  until: number;
}
let refused = new WeakMap<object, Refusal>();

/** Forget every remembered refusal (tests, and right after the one-time move). */
export function resetSeparationCache(): void {
  refused = new WeakMap();
}

const ROLES = "SELECT role FROM db_identity ORDER BY role";
const TABLES = "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('leads', 'kit_events') ORDER BY name";
// Fixed statements for the three cases: no table name is ever built from input.
const ROWS: Record<string, string> = {
  leads: "SELECT COUNT(*) AS n FROM (SELECT 1 FROM leads LIMIT 1)",
  kit_events: "SELECT COUNT(*) AS n FROM (SELECT 1 FROM kit_events LIMIT 1)",
  both: "SELECT (SELECT COUNT(*) FROM (SELECT 1 FROM leads LIMIT 1)) + (SELECT COUNT(*) FROM (SELECT 1 FROM kit_events LIMIT 1)) AS n",
};

async function leadsRole(db: D1Database, meter: QueryMeter | undefined): Promise<string[]> {
  meter?.take();
  return (await db.prepare(ROLES).all<{ role: string }>()).results.map((x) => x.role);
}

/** The analytics database's roles and whether it still holds lead rows. Throws on any error. */
async function analyticsFacts(analytics: D1Database, meter: QueryMeter | undefined): Promise<{ roles: string[]; leadRows: number }> {
  meter?.take();
  const [r, t] = await analytics.batch<{ role?: string; name?: string }>([analytics.prepare(ROLES), analytics.prepare(TABLES)]);
  const roles = r!.results.map((x) => String(x.role));
  const names = t!.results.map((x) => String(x.name));
  if (names.length === 0) return { roles, leadRows: 0 }; // the lead tables are gone, proven by sqlite_master
  const key = names.length === 2 ? "both" : names[0]!;
  const sql = ROWS[key];
  if (sql === undefined) throw new LeadsSeparationError();
  meter?.take();
  const n = (await analytics.prepare(sql).first<{ n: number }>())?.n;
  return { roles, leadRows: typeof n === "number" ? n : 1 };
}

async function prove(leads: D1Database, analytics: D1Database, meter: QueryMeter | undefined): Promise<boolean> {
  try {
    const l = await leadsRole(leads, meter);
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
 * states, and DB holds no lead rows, as of this call. Throws TypeError for a binding that is not a
 * database at all and LeadsSeparationError for everything else. Call it immediately before each read or
 * write of lead data; an approval is never remembered.
 */
export async function assertLeadsSeparated(env: { DB?: unknown; LEADS_DB?: unknown }, opts: SeparationOptions = {}): Promise<void> {
  const nowMs = opts.nowMs ?? Date.now();
  if (!isLeadsDb(env?.DB)) throw new TypeError("DB is not bound");
  if (!isLeadsDb(env.LEADS_DB, env.DB)) throw new TypeError("LEADS_DB is not bound");
  const leads = rawDb(env.LEADS_DB);
  const analytics = rawDb(env.DB as D1Database);
  if (leads === analytics) throw new LeadsSeparationError();
  const seen = refused.get(leads);
  if (seen !== undefined && seen.analytics === analytics && seen.until > nowMs) throw new LeadsSeparationError();
  if (opts.meter !== undefined && opts.meter.remaining() < SEPARATION_QUERIES) {
    // Not enough room to prove it: nothing is served and nothing is remembered. The caller decides how to say so.
    throw new BudgetExceeded();
  }
  if (!(await prove(leads, analytics, opts.meter))) {
    refused.set(leads, { analytics, until: nowMs + SEPARATION_REFUSED_TTL_MS });
    throw new LeadsSeparationError();
  }
}
