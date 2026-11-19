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
// LANDING AND HOP (issue #566, migration 0017): click_landing_* and click_hop_* are modeled here
// too. Their caps are in FunnelCaps and their writes in funnelDailyWrites; the deploy-time check
// reads nothing else. The model is a worst case: it assumes every site writes both tables.

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
  /** New cells a (site, day, UTC hour) may create, source and country tables together. */
  hourBudget: number;
  /** General cells per (site, day) in click_landing_daily. */
  landingDayCap: number;
  /** General cells per (site, day) in click_hop_daily. */
  hopDayCap: number;
  /** Cells every destination may always create per day in each of the landing and hop tables. */
  lhReservedPerLink: number;
  /** Fold cap for a closed month in click_landing_monthly. */
  landingMonthCap: number;
  /** Fold cap for a closed month in click_hop_monthly. */
  hopMonthCap: number;
  /** New cells a (site, day, UTC hour) may create, landing and hop tables together (their own slice). */
  lhHourBudget: number;
}

export const FUNNEL_PROFILES = {
  free: {
    srcDayCap: 40, countryDayCap: 50, reservedPerLink: 2, srcMonthCap: 100, countryMonthCap: 120, hourBudget: 12,
    landingDayCap: 24, hopDayCap: 16, lhReservedPerLink: 1, landingMonthCap: 40, hopMonthCap: 30, lhHourBudget: 16,
  },
  // The landing and hop numbers of `standard` are placeholders (not measured, not proposed by the design):
  // they only keep the step-up profile above `free` until the measurement week sets real ones.
  standard: {
    srcDayCap: 110, countryDayCap: 160, reservedPerLink: 2, srcMonthCap: 300, countryMonthCap: 400, hourBudget: 150,
    landingDayCap: 60, hopDayCap: 40, lhReservedPerLink: 1, landingMonthCap: 120, hopMonthCap: 90, lhHourBudget: 60,
  },
} as const satisfies Record<string, FunnelCaps>;

/** The caps in force. The Worker and the budget check both read this. */
export const FUNNEL_CAPS: FunnelCaps = FUNNEL_PROFILES.free;

/** Destination kinds (`link`, `social_icon`): each pair has its own cells and its own overflow row per table. */
const KINDS = 2;
const HOURS = 24;

/**
 * What one site writes. `landings` and `hops` say whether the site lists them (a site without the list writes
 * no cells of that table, worker/src/click.ts), so the model counts them only then. Both default to off.
 */
export interface FunnelSite {
  links: number;
  landings?: boolean;
  hops?: boolean;
}

/**
 * Most rows the day counters hold for one site and UTC day: the general pools, the reserved cells
 * of every link (times kinds; source and country also keep one `direct` more, landing and hop do not)
 * and the overflow rows. The hourly slices of the new-cell budget also bound it (a click may make a
 * cell in both tables of a slice, hence +1 per hour; landing and hop have a slice of their own); the lower
 * bound binds. The landing and hop terms count only for a site that lists them.
 */
export function funnelRowsPerSiteDay(caps: FunnelCaps, links: number, on: { landings?: boolean; hops?: boolean } = {}): number {
  const lh = (on.landings === true ? 1 : 0) + (on.hops === true ? 1 : 0);
  let byCaps = caps.srcDayCap + caps.countryDayCap + links * KINDS * 2 * (caps.reservedPerLink + 1);
  if (on.landings === true) byCaps += caps.landingDayCap;
  if (on.hops === true) byCaps += caps.hopDayCap;
  byCaps += links * KINDS * lh * caps.lhReservedPerLink;
  const byHours = HOURS * (caps.hourBudget + 1) + (lh > 0 ? HOURS * (caps.lhHourBudget + 1) : 0);
  return Math.min(byCaps, byHours) + KINDS * (2 + lh);
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
export function funnelDailyWrites(caps: FunnelCaps, sites: readonly FunnelSite[]): number {
  let total = 0;
  for (const s of sites) {
    const cells = funnelRowsPerSiteDay(caps, s.links, s);
    const lh = (s.landings === true ? 1 : 0) + (s.hops === true ? 1 : 0);
    const monthly =
      caps.srcMonthCap + caps.countryMonthCap + (s.landings === true ? caps.landingMonthCap : 0) + (s.hops === true ? caps.hopMonthCap : 0);
    const month = 2 * (monthly + KINDS * (2 + lh) + 1);
    total += 3 * cells + 2 + HOURS + month;
  }
  return total;
}
