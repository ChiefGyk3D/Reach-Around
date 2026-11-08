// SPDX-License-Identifier: Apache-2.0
import { QueryMeter, meteredDb, queryBudget } from "./d1budget";
import { handleClick, MAX_URL_LENGTH, type Env as ClickEnv } from "./click";
import { handleLive, LIVE_CRON, LIVE_ROUTE, runLivePoller, type LiveEnv } from "./live";
import { LEAD_PURGE_QUERIES, purgeExpiredLeads } from "./kit/leadretention";
import { logEvent } from "./kit/log";
import { purgeOldEvents } from "./retention";
import { handleView, type ViewEnv } from "./view";
import { purgeViews } from "./viewretention";
import { rollupClosedDays } from "./viewrollup";

export type { Env } from "./click";
export type { ViewEnv } from "./view";

/** Everything the deployed Worker is bound to: the click route's plus the view route's. */
type WorkerEnv = ClickEnv &
  Omit<ViewEnv, "DB" | "SITES" | "SITE_HOST" | "PAGE_IDS" | "LINK_IDS"> &
  LiveEnv;

const VIEW_PREFIX = "/e/v/";

function isViewRoute(request: Request): boolean {
  // Same length guard as the click route: an oversize URL is never parsed here.
  if (request.url.length > MAX_URL_LENGTH) return false;
  try {
    return new URL(request.url).pathname.startsWith(VIEW_PREFIX);
  } catch {
    return false;
  }
}

function isLiveRoute(request: Request): boolean {
  // Exact path only (no trailing slash, no prefix). The query string is
  // ignored by the handler. Oversize URLs are never parsed here.
  if (request.url.length > MAX_URL_LENGTH) return false;
  try {
    return new URL(request.url).pathname === LIVE_ROUTE;
  } catch {
    return false;
  }
}

export default {
  /**
   * Every request, to any path, gets the same empty 204; only
   * `POST /e/c/:linkId` and `POST /e/v/:pageId` can record anything. There is
   * no redirect route, and no route returns stored data with ONE deliberate
   * exception: `GET /e/live` (src/live.ts), which returns only booleans and
   * timestamps about whether two channels are live. Any other method on
   * /e/live is the same empty 204.
   */
  async fetch(request: Request, env: WorkerEnv): Promise<Response> {
    if (isLiveRoute(request)) return handleLive(request, env);
    if (isViewRoute(request)) return handleView(request, env);
    return handleClick(request, env);
  },

  /**
   * The 2-minute live cron (LIVE_CRON) runs ONLY the live poller. Any other
   * trigger runs the hourly jobs below exactly as before.
   *
   * Hourly cron, in this order: click purge, page-view purge, lead purge,
   * page-view rollup. The purge comes BEFORE the rollup so a rollup that fails, runs out
   * of CPU or is cut off can never block retention (the purge only deletes
   * days that are already rolled, plus rows past the hard ceiling). Each step
   * is isolated: one failing never stops the others, and nothing here throws.
   */
  async scheduled(
    controller: ScheduledController,
    env: ClickEnv &
      LiveEnv & {
      RAW_KEEP_DAYS?: string;
      GEO_LEVEL?: string;
      TOP_SOURCES?: string;
      MIN_CELL?: string;
      ROLLUP_GRACE_HOURS?: string;
      QUERY_BUDGET?: string;
      ROLLUP_MAX_ROWS_PER_DAY?: string;
      UTM_ALLOWLIST?: string;
      LEAD_RETENTION_DAYS?: string;
    },
    ctx: ExecutionContext,
  ): Promise<void> {
    if (controller.cron === LIVE_CRON) {
      ctx.waitUntil(runLivePoller(env, Date.now()));
      return;
    }
    ctx.waitUntil(
      (async () => {
        const now = Date.now();
        // One meter for the whole invocation (Free allows 50 D1 queries, a
        // batch counts as one). The purge is held to half of it; the rollup
        // takes what is left.
        const budget = queryBudget(env.QUERY_BUDGET);
        const meter = new QueryMeter(budget);
        const whole = meteredDb(env.DB, meter);
        const purgeDb = meteredDb(env.DB, meter.slice(Math.floor(budget / 2)));
        try {
          await purgeOldEvents({ ...env, DB: whole }, now);
        } catch {
          // Next run retries; nothing request-derived to report.
        }
        try {
          await purgeViews({ ...env, DB: purgeDb }, now);
        } catch {
          // Same.
        }
        try {
          // Lead retention (issue #265): bounded, and on the same meter. Its own
          // slice keeps it from eating the rollup's queries.
          const leadDb = meteredDb(env.DB, meter.slice(Math.min(LEAD_PURGE_QUERIES, Math.floor(budget / 4))));
          await purgeExpiredLeads({ DB: leadDb, LEAD_RETENTION_DAYS: env.LEAD_RETENTION_DAYS }, now);
        } catch {
          logEvent("purge", "failed", "other");
        }
        try {
          await rollupClosedDays({ ...env, DB: whole }, now);
        } catch {
          // The heartbeat in view_meta records the failure class.
        }
      })(),
    );
  },
};
