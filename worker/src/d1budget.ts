// SPDX-License-Identifier: Apache-2.0
//
// A hard per-invocation budget for D1 queries, counted in ONE place.
//
// Cloudflare limits (read 2026-10-24):
//   D1 "Limits" page (https://developers.cloudflare.com/d1/platform/limits/,
//   last updated Oct 24, 2026): "Queries per Worker invocation ... 1000
//   (Workers Paid) / 50 (Free)", and "a db.batch() call counts as a single
//   query against it, not one query per statement".
//   Workers "Limits" page (https://developers.cloudflare.com/workers/platform/limits/,
//   last updated Oct 12, 2026): subrequests per invocation 50 (Free) /
//   10,000 (Paid), D1 calls included; Cron Triggers per account 5 (Free).
//
// meteredDb() wraps a D1Database so that every prepare(...).run/all/first/raw,
// every batch() and every exec() takes one unit from a QueryMeter, and throws
// BudgetExceeded instead of making the call when the meter is empty. Work
// that wants to stop early asks meter.remaining() first.

export const DEFAULT_QUERY_BUDGET = 40; // Free allows 50; 10 left for margin
export const MAX_QUERY_BUDGET = 900; // Paid allows 1000; 100 left for margin

/** Parse QUERY_BUDGET: a whole number from 5 to 900, else the Free default. */
export function queryBudget(raw: string | undefined): number {
  if (typeof raw !== "string" || !/^\d{1,4}$/.test(raw)) return DEFAULT_QUERY_BUDGET;
  const n = Number(raw);
  if (n < 5) return DEFAULT_QUERY_BUDGET;
  return Math.min(n, MAX_QUERY_BUDGET);
}

export class BudgetExceeded extends Error {
  constructor() {
    super("query budget exceeded");
  }
}

export class QueryMeter {
  used = 0;
  constructor(
    readonly limit: number,
    private readonly parent?: QueryMeter,
  ) {}
  /** Units left, counting the parent's limit too. */
  remaining(): number {
    const own = this.limit - this.used;
    return this.parent ? Math.min(own, this.parent.remaining()) : own;
  }
  take(): void {
    if (this.remaining() < 1) throw new BudgetExceeded();
    this.count();
  }
  private count(): void {
    this.used += 1;
    this.parent?.count();
  }
  /** A child with its own smaller limit; its queries also count against this meter. */
  slice(limit: number): QueryMeter {
    return new QueryMeter(Math.max(0, limit), this);
  }
}

const meters = new WeakMap<object, QueryMeter>();
const unwrapped = new WeakMap<object, D1PreparedStatement>();

/** The meter a metered database counts against, if it is one. */
export function meterOf(db: D1Database): QueryMeter | undefined {
  return meters.get(db);
}

function wrapStatement(st: D1PreparedStatement, meter: QueryMeter): D1PreparedStatement {
  const proxy: D1PreparedStatement = new Proxy(st, {
    get(t, prop) {
      if (prop === "bind") {
        return (...args: unknown[]) => wrapStatement(t.bind(...args), meter);
      }
      if (prop === "run" || prop === "all" || prop === "first" || prop === "raw") {
        return async (...args: unknown[]) => {
          meter.take();
          return (t as unknown as Record<string, (...a: unknown[]) => unknown>)[prop as string]!(...args);
        };
      }
      const v = Reflect.get(t, prop, t);
      return typeof v === "function" ? v.bind(t) : v;
    },
  });
  unwrapped.set(proxy, st);
  return proxy;
}

export function meteredDb(db: D1Database, meter: QueryMeter): D1Database {
  const proxy: D1Database = new Proxy(db, {
    get(t, prop) {
      if (prop === "prepare") return (q: string) => wrapStatement(t.prepare(q), meter);
      if (prop === "batch") {
        return async (stmts: D1PreparedStatement[]) => {
          meter.take();
          return t.batch(stmts.map((s) => unwrapped.get(s) ?? s));
        };
      }
      if (prop === "exec") {
        return async (q: string) => {
          meter.take();
          return t.exec(q);
        };
      }
      const v = Reflect.get(t, prop, t);
      return typeof v === "function" ? v.bind(t) : v;
    },
  });
  meters.set(proxy, meter);
  return proxy;
}
