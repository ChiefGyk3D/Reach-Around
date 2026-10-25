// SPDX-License-Identifier: Apache-2.0
import { QueryMeter, meteredDb, queryBudget } from "./d1budget";
import { handleClick, MAX_URL_LENGTH, type Env as ClickEnv } from "./click";
import { purgeOldEvents } from "./retention";
import { handleView, type ViewEnv } from "./view";
import { purgeViews } from "./viewretention";
import { rollupClosedDays } from "./viewrollup";

export type { Env } from "./click";
export type { ViewEnv } from "./view";

/** Everything the deployed Worker is bound to: the click route's plus the view route's. */
type WorkerEnv = ClickEnv & Omit<ViewEnv, "DB" | "SITE_HOST">;

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

export default {
  /**
   * Every request, to any path, gets the same empty 204; only
   * `POST /e/c/:linkId` and `POST /e/v/:pageId` can record anything. There is
   * no redirect route and no route that returns stored data.
   */
  async fetch(request: Request, env: WorkerEnv): Promise<Response> {
    if (isViewRoute(request)) return handleView(request, env);
    return handleClick(request, env);
  },

  /**
   * Hourly cron, in this order: click purge, page-view purge, page-view
   * rollup. The purge comes BEFORE the rollup so a rollup that fails, runs out
   * of CPU or is cut off can never block retention (the purge only deletes
   * days that are already rolled, plus rows past the hard ceiling). Each step
   * is isolated: one failing never stops the others, and nothing here throws.
   */
  async scheduled(
    _controller: ScheduledController,
    env: ClickEnv & {
      RAW_KEEP_DAYS?: string;
      GEO_LEVEL?: string;
      TOP_SOURCES?: string;
      MIN_CELL?: string;
      ROLLUP_GRACE_HOURS?: string;
      QUERY_BUDGET?: string;
      ROLLUP_MAX_ROWS_PER_DAY?: string;
      UTM_ALLOWLIST?: string;
    },
    ctx: ExecutionContext,
  ): Promise<void> {
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
          await rollupClosedDays({ ...env, DB: whole }, now);
        } catch {
          // The heartbeat in view_meta records the failure class.
        }
      })(),
    );
  },
};
