// SPDX-License-Identifier: Apache-2.0
//
// The page-view hourly budgets and the D1 writes they imply. One place, so the
// view route (view.ts), the kit's combined D1 check (kit/budgets.ts) and the
// deploy-time check (scripts/check-budget.ts) cannot disagree.
//
// There is no sampling: a view over the hourly budget is dropped, so the budget
// is the cap on accepted views. `VIEWS_PER_HOUR` (humans, "pv") and
// `BOT_VIEWS_PER_HOUR` ("pvbot") are the knobs. Account-wide ceilings only ever
// lower the figure, so counting each budget in full is the safe (high) side.

export const DEFAULT_HUMAN_PER_HOUR = 120;
export const DEFAULT_BOT_PER_HOUR = 30;
export const MAX_VIEWS_PER_HOUR = 100_000;

/** The Worker's lenient integer read: digits only, at most `max`, else the fallback. */
export function positiveInt(v: string | undefined, fallback: number, max: number): number {
  if (typeof v !== "string" || !/^\d{1,9}$/.test(v)) return fallback;
  const n = Number(v);
  return n <= max ? n : fallback;
}

/** Rows written per accepted view: 28/5 = 5.6 (docs/deploy.md, Plan 4 P-6: 20,160 for 3,600 views). */
export const PV_WRITES_PER_VIEW = { num: 28, den: 5 } as const;
/** Rollup, purge and idle-hour heartbeat rows per day, whatever the traffic (2,200 + 250 + rounding, docs/deploy.md). */
export const PV_FIXED_DAILY_WRITES = 2_540;

export interface PageViewBudgetEnv {
  VIEWS_PER_HOUR?: string;
  BOT_VIEWS_PER_HOUR?: string;
}

/** Worst-case rows written per UTC day by the page-view feature at the configured hourly budgets. */
export function pageViewDailyWrites(env: PageViewBudgetEnv): number {
  const human = positiveInt(env?.VIEWS_PER_HOUR, DEFAULT_HUMAN_PER_HOUR, MAX_VIEWS_PER_HOUR);
  const bot = positiveInt(env?.BOT_VIEWS_PER_HOUR, DEFAULT_BOT_PER_HOUR, MAX_VIEWS_PER_HOUR);
  const views = 24 * (human + bot);
  return Math.ceil((views * PV_WRITES_PER_VIEW.num) / PV_WRITES_PER_VIEW.den) + PV_FIXED_DAILY_WRITES;
}
