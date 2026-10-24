// SPDX-License-Identifier: Apache-2.0
import type { D1Migration } from "@cloudflare/vitest-plugin";

declare global {
  namespace Cloudflare {
    interface Env {
      DB: D1Database;
      SITE_HOST: string;
      LINK_IDS: string;
      TEST_MIGRATIONS: D1Migration[];
    }
  }
}

export {};
