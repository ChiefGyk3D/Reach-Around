// SPDX-License-Identifier: Apache-2.0
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import { purgeViews } from "../../src/viewretention";
import { DB, clearAll } from "./helpers";

const DAY = 86_400_000;
const NOW = Date.parse("2027-03-14T12:00:00Z");

beforeEach(clearAll);

describe("migration 0007", () => {
  it("backfills_max_id_so_the_purge_deletes_the_old_raw_rows_of_an_already_logged_day", async () => {
    const mig = testEnv.TEST_MIGRATIONS.find((m) => m.name.startsWith("0007"));
    expect(mig, "migration 0007 is present").toBeDefined();
    expect(testEnv.TEST_MIGRATIONS.some((m) => m.name.startsWith("0006"))).toBe(true);

    // Put the table back into its 0006 shape: no max_id column.
    await DB.prepare("ALTER TABLE view_rollup_log DROP COLUMN max_id").run();
    try {
      const old = Math.floor((NOW - 10 * DAY) / DAY) * DAY;
      const recent = Math.floor((NOW - 1 * DAY) / DAY) * DAY;
      const day = (ms: number) => new Date(ms).toISOString().slice(0, 10);
      const ins = (ts: number) =>
        DB.prepare("INSERT INTO view_events (ts, page, src_kind, bot_class, reason) VALUES (?, 'home', 'd', 'h', 'ok')").bind(ts);
      await DB.batch([ins(old + 1000), ins(old + 2000), ins(recent + 1000)]);
      // Log rows as 0006 wrote them (nine columns), for the old day and a day with no raw rows left.
      await DB.batch([
        DB.prepare("INSERT INTO view_rollup_log VALUES (?, 'UTC', 2, 2, 0, 1, 1, 1, 0)").bind(day(old)),
        DB.prepare("INSERT INTO view_rollup_log VALUES ('2027-01-01', 'UTC', 1, 1, 0, 1, 1, 1, 0)"),
      ]);

      // Apply 0007 exactly as the migration runner would.
      for (const sql of mig!.queries) await DB.prepare(sql).run();

      const logs = (await DB.prepare("SELECT day, max_id FROM view_rollup_log ORDER BY day").all<{ day: string; max_id: number }>()).results;
      const ids = (await DB.prepare("SELECT id, ts FROM view_events ORDER BY id").all<{ id: number; ts: number }>()).results;
      expect(logs.find((l) => l.day === day(old))?.max_id).toBe(ids[1]?.id); // highest id of that day
      expect(logs.find((l) => l.day === "2027-01-01")?.max_id).toBe(0); // nothing left to count

      await purgeViews({ DB }, NOW);
      const left = (await DB.prepare("SELECT ts FROM view_events ORDER BY id").all<{ ts: number }>()).results.map((r) => r.ts);
      expect(left).toEqual([recent + 1000]); // the logged old day's raw rows are gone; the recent row stays
    } finally {
      // Whatever happened, leave the shared test database with the column back.
      const cols = (await DB.prepare("SELECT name FROM pragma_table_info('view_rollup_log')").all<{ name: string }>()).results.map((c) => c.name);
      if (!cols.includes("max_id")) await DB.prepare("ALTER TABLE view_rollup_log ADD COLUMN max_id INTEGER NOT NULL DEFAULT 0").run();
    }
  });

  it("without_the_backfill_the_old_rows_would_have_stayed", async () => {
    // The guard bites: a log row at max_id 0 keeps its raw rows (the purge needs id <= max_id).
    const old = Math.floor((NOW - 10 * DAY) / DAY) * DAY;
    await DB.prepare("INSERT INTO view_events (ts, page, src_kind, bot_class, reason) VALUES (?, 'home', 'd', 'h', 'ok')").bind(old + 1000).run();
    await DB.prepare("INSERT INTO view_rollup_log (day, day_tz, raw_views, views, uniques, rows_core, rows_dim, rows_geo, rolled_at) VALUES (?, 'UTC', 1, 1, 0, 1, 1, 1, 0)")
      .bind(new Date(old).toISOString().slice(0, 10))
      .run();
    await purgeViews({ DB }, NOW);
    expect((await DB.prepare("SELECT COUNT(*) AS n FROM view_events").first<{ n: number }>())?.n).toBe(1);
  });
});
