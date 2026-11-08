// SPDX-License-Identifier: Apache-2.0
//
// Deploy-time check of the combined daily D1 write budget (issue #261), as a
// pure function so the Worker tests cover it. It takes the variables the Worker
// reads (VIEWS_PER_HOUR, BOT_VIEWS_PER_HOUR and the kit limits), asks
// assessDailyBudget (the Worker's own code) and returns GitHub workflow
// commands. Code 1 when the Worker would refuse the configuration (an invalid
// value, or over the quota); a warning with code 0 above 80 percent. Values are
// never printed, only names and totals.
import { MAX_VIEWS_PER_HOUR } from "../pvlimits";
import { assessDailyBudget, type KitLimitsEnv } from "./budgets";

const NAMES = [
  "VIEWS_PER_HOUR",
  "BOT_VIEWS_PER_HOUR",
  "ROUTE_BUDGET_GATE",
  "ROUTE_BUDGET_VIEW",
  "MAX_LEADS",
  "MAX_KIT_EVENTS",
  "DAILY_ALERT_BUDGET",
  "DAILY_SEND_BUDGET",
  "D1_DAILY_WRITE_QUOTA",
] as const;

export function runBudgetCheck(source: Record<string, string | undefined>): { code: 0 | 1; lines: string[] } {
  const env: Record<string, string> = {};
  for (const n of NAMES) {
    const v = source[n];
    if (v !== undefined && v !== "") env[n] = v;
  }
  const badPv = ["VIEWS_PER_HOUR", "BOT_VIEWS_PER_HOUR"].filter((n) => {
    const v = env[n];
    return v !== undefined && !(/^[0-9]{1,6}$/.test(v) && Number(v) <= MAX_VIEWS_PER_HOUR);
  });
  // The Worker falls back to the default for a bad page-view budget, so the
  // projection would silently be the wrong figure. Refuse here instead.
  if (badPv.length > 0) return { code: 1, lines: [`::error::unusable budget settings: ${badPv.join(", ")}`] };
  const a = assessDailyBudget(env as KitLimitsEnv);
  const pct = ((100 * a.combined) / a.quota).toFixed(1);
  const sum = `combined worst-case daily D1 writes ${a.combined} of ${a.quota} (${pct} percent; page views ${a.pageViews})`;
  if (a.invalid.length > 0) {
    return { code: 1, lines: [`::error::unusable budget settings: ${a.invalid.join(", ")}`] };
  }
  if (a.level === "fail") return { code: 1, lines: [`::error::${sum} is over the quota`] };
  if (a.level === "warn") return { code: 0, lines: [`::warning::${sum} is above 80 percent`] };
  return { code: 0, lines: [`${sum}: ok`] };
}

