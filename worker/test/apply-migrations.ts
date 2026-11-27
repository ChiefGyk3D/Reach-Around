// SPDX-License-Identifier: Apache-2.0
import { applyD1Migrations } from "cloudflare:test";
import { env } from "cloudflare:workers";

await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);
// The leads database has its own migration set and is never given the analytics migrations.
await applyD1Migrations(env.LEADS_DB, env.TEST_LEADS_MIGRATIONS);
