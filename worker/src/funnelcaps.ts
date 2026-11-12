// SPDX-License-Identifier: Apache-2.0
//
// The click funnel's row caps, in ONE place (issue #332). The Worker
// (clickfunnel.ts) and the deploy-time budget check (kit/budgets.ts) both read
// FUNNEL_CAPS, so the numbers the Worker enforces are the numbers the check
// projects; neither file holds a copy.
//
// PROFILES. `free` is the default and is sized to the Cloudflare D1 Free plan
// (owner decision 2026-11-08: D1 starts on Free with lower caps; real row counts
// are measured for one week before any paid-plan decision, see docs/deploy.md).
// The free numbers are provisional, chosen so the default deployment stays under
// 80 percent of the free write quota; they are not measured. `standard` holds the
// caps the funnel was first written with and is kept as the documented step up.
// It is NOT selectable at run time: moving off `free` is a code change (the line
// that defines FUNNEL_CAPS), reviewed with the budget check.
//
// EXTENSION POINT (issue #566): the landing and hop tables are not built. When
// they are, add their caps to FunnelCaps and their writes to funnelDailyWrites,
// and nothing else in the check changes. Do not model them before the tables exist.

export interface FunnelCaps {
  /** General cells per (site, day) in click_src_daily, on top of the reserved ones. */
  srcDayCap: number;
  /** General cells per (site, day) in click_country_daily. */
  countryDayCap: number;
  /** Cells every destination may always create per day (and one `direct` more). */
  reservedPerLink: number;
  /** Fold cap for a closed month in click_src_monthly. */
  srcMonthCap: number;
  /** Fold cap for a closed month in click_country_monthly. */
  countryMonthCap: number;
  /** New cells a (site, day, UTC hour) may create, both tables together. */
  hourBudget: number;
}

export const FUNNEL_PROFILES = {
  free: { srcDayCap: 40, countryDayCap: 50, reservedPerLink: 2, srcMonthCap: 100, countryMonthCap: 120, hourBudget: 12 },
  standard: { srcDayCap: 110, countryDayCap: 160, reservedPerLink: 2, srcMonthCap: 300, countryMonthCap: 400, hourBudget: 150 },
} as const satisfies Record<string, FunnelCaps>;

/** The caps in force. The Worker and the budget check both read this. */
export const FUNNEL_CAPS: FunnelCaps = FUNNEL_PROFILES.free;

/** Destination kinds (`link`, `social_icon`) and counter tables (source, country): each pair has its own cells. */
const KINDS = 2;
const TABLES = 2;
/** One overflow row per kind and table per (site, day) or (site, month). */
const OVERFLOW_ROWS = KINDS * TABLES;
const HOURS = 24;

/**
 * Most rows the two day counters hold for one site and UTC day: the general
 * pools, the reserved cells of every link (times kinds and tables) and the
 * overflow rows. The hourly slice of the new-cell budget also bounds it (a click
 * may make a cell in both tables, hence +1 per hour); the lower bound binds.
 */
export function funnelRowsPerSiteDay(caps: FunnelCaps, links: number): number {
  const byCaps = caps.srcDayCap + caps.countryDayCap + links * KINDS * TABLES * (caps.reservedPerLink + 1);
  const byHours = HOURS * (caps.hourBudget + 1);
  return Math.min(byCaps, byHours) + OVERFLOW_ROWS;
}

/**
 * Worst-case rows written per UTC day by the funnel for the given sites
 * (`links` = link ids of each). Every cell costs three writes over its life: the
 * insert, the new-cell budget upsert it triggers and the retention delete (the
 * delete happens 90 days later, but a steady state does as many a day). Counting
 * on a cell that already exists is click-route traffic and stays in the click
 * reserve (CLICK_DAILY_WRITES), which has no global budget of its own. Per site
 * add the seal, the capacity row, 24 hourly click rows for the report, and the
 * month roll-up: the monthly caps plus overflow rows, written once and deleted
 * once (all counted on one day, which fails closed).
 */
export function funnelDailyWrites(caps: FunnelCaps, sites: readonly { links: number }[]): number {
  let total = 0;
  for (const s of sites) {
    const cells = funnelRowsPerSiteDay(caps, s.links);
    const month = 2 * (caps.srcMonthCap + caps.countryMonthCap + OVERFLOW_ROWS + 1);
    total += 3 * cells + 2 + HOURS + month;
  }
  return total;
}
