// SPDX-License-Identifier: Apache-2.0
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
   * Daily cron, in this order: click purge, page-view rollup, page-view
   * purge. The rollup runs before the view purge so a day is counted before
   * its raw rows can be deleted. Each step is isolated: one failing never
   * stops the others, and nothing here throws.
   */
  async scheduled(
    _controller: ScheduledController,
    env: ClickEnv & { RAW_KEEP_DAYS?: string; GEO_LEVEL?: string; TOP_SOURCES?: string },
    ctx: ExecutionContext,
  ): Promise<void> {
    ctx.waitUntil(
      (async () => {
        const now = Date.now();
        try {
          await purgeOldEvents(env, now);
        } catch {
          // Next run retries; nothing request-derived to report.
        }
        try {
          await rollupClosedDays(env, now);
        } catch {
          // The heartbeat in view_meta records the failure class.
        }
        try {
          await purgeViews(env, now);
        } catch {
          // Same.
        }
      })(),
    );
  },
};
