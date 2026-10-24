// SPDX-License-Identifier: Apache-2.0
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { cloudflareTest, readD1Migrations } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

export default defineConfig(async () => {
  // Build the path from import.meta.url (a string) rather than `new URL(...)`:
  // with @types/node 26 the global URL from workers-types no longer matches
  // node:url's URL, so passing one to fileURLToPath fails type-checking.
  const here = dirname(fileURLToPath(import.meta.url));
  const migrations = await readD1Migrations(resolve(here, "migrations"));
  return {
    plugins: [
      cloudflareTest({
        wrangler: { configPath: "./wrangler.toml" },
        miniflare: { bindings: { TEST_MIGRATIONS: migrations } },
      }),
    ],
    test: { setupFiles: ["./test/apply-migrations.ts"] },
  };
});
