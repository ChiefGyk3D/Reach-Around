// SPDX-License-Identifier: Apache-2.0
//
// Deploy-time check of the combined daily D1 write budget (issue #261), as a
// pure function so the Worker tests cover it. It takes the variables the Worker
// reads (VIEWS_PER_HOUR, BOT_VIEWS_PER_HOUR and the kit limits), asks
// assessDailyBudget (the Worker's own code) and returns GitHub workflow
// commands. Code 1 when the Worker would refuse the configuration (an invalid
// value, or over the quota); a warning with code 0 above 80 percent. Values are
// never printed, only names and totals.
import { MAX_VIEWS_PER_HOUR, PAGE_VIEW_VARS } from "../pvlimits";
import { loadSites } from "../sites";
import { KIT_LIMIT_VARS, assessDailyBudget, type KitLimitsEnv } from "./budgets";

/**
 * Everything the Worker's loader reads for this verdict: the kit limits, the
 * page-view budgets and ceilings, and the site list (counted by loadSites).
 * Derived from the loader's own lists, so a new limit cannot be forgotten.
 */
export const BUDGET_ENV_VARS: readonly string[] = [...PAGE_VIEW_VARS, "SITES", "SITE_HOST", "PAGE_IDS", "LINK_IDS", ...KIT_LIMIT_VARS];

export function runBudgetCheck(source: Record<string, string | undefined>): { code: 0 | 1; lines: string[] } {
  const env: Record<string, string> = {};
  for (const n of BUDGET_ENV_VARS) {
    const v = source[n];
    if (v !== undefined && v !== "") env[n] = v;
  }
  const badPv = PAGE_VIEW_VARS.filter((n) => {
    const v = env[n];
    return v !== undefined && !(/^[0-9]{1,6}$/.test(v) && Number(v) <= MAX_VIEWS_PER_HOUR);
  });
  // The Worker falls back to the default for a bad page-view budget, so the
  // projection would silently be the wrong figure. Refuse here instead.
  if (badPv.length > 0) return { code: 1, lines: [`::error::unusable budget settings: ${badPv.join(", ")}`] };
  // A site list that does not load cleanly cannot be counted: stop the deploy
  // (the projection would otherwise have to guess how many sites share the ceilings).
  if (loadSites(env).errors.length > 0) return { code: 1, lines: ["::error::the site list does not load (SITES, or SITE_HOST with PAGE_IDS and LINK_IDS)"] };
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

