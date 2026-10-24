// SPDX-License-Identifier: Apache-2.0
import { handleClick, type Env } from "./click";
import { purgeOldEvents } from "./retention";

export type { Env } from "./click";

export default {
  /**
   * Every request, to any path, gets the same empty 204;
   * only `POST /e/c/:linkId` can record anything. There is no redirect route
   * and no route that returns stored data.
   */
  async fetch(request: Request, env: Env): Promise<Response> {
    return handleClick(request, env);
  },

  /** Daily cron: drop click rows past the retention window. */
  async scheduled(
    _controller: ScheduledController,
    env: Env,
    ctx: ExecutionContext,
  ): Promise<void> {
    ctx.waitUntil(purgeOldEvents(env, Date.now()).then(() => undefined));
  },
};
