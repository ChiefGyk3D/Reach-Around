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
  // The leads database (issue #772) has its own migration folder and its own binding. The binding is
  // declared HERE for the tests and only as a comment in wrangler.toml, so no deploy path creates it early.
  const leadsMigrations = await readD1Migrations(resolve(here, "leads-migrations"));
  return {
    plugins: [
      cloudflareTest({
        wrangler: { configPath: "./wrangler.toml" },
        miniflare: {
          bindings: { TEST_MIGRATIONS: migrations, TEST_LEADS_MIGRATIONS: leadsMigrations },
          d1Databases: { LEADS_DB: "00000000-0000-0000-0000-0000000000a2" },
        },
      }),
    ],
    test: { setupFiles: ["./test/apply-migrations.ts"] },
  };
});
