// SPDX-License-Identifier: Apache-2.0
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import { purgeViews } from "../../src/viewretention";
import { rollupClosedDays } from "../../src/viewrollup";
import { DB, clearAll } from "./helpers";

const DAY = 86_400_000;
const NOW = Date.parse("2027-03-14T12:00:00Z");
const day = (ms: number) => new Date(ms).toISOString().slice(0, 10);
const q = async <T>(sql: string) => (await DB.prepare(sql).all<T>()).results;
const sum = async (t: string) => (await q<{ v: number }>(`SELECT COALESCE(SUM(views), 0) AS v FROM ${t}`))[0]?.v ?? 0;

beforeEach(clearAll);

async function columns(): Promise<string[]> {
  return (await q<{ name: string }>("SELECT name FROM pragma_table_info('view_rollup_log')")).map((c) => c.name);
}

describe("migration 0007 invalidates old rollup state", () => {
  it("clears_aggregates_and_log_so_the_new_code_rolls_every_day_still_in_raw_from_scratch", async () => {
    const mig = testEnv.TEST_MIGRATIONS.find((m) => m.name.startsWith("0007"));
    expect(mig, "migration 0007 is present").toBeDefined();
    expect(testEnv.TEST_MIGRATIONS.some((m) => m.name.startsWith("0006"))).toBe(true);

    // Put the table back into its 0006 shape: no max_id, no rerolls.
    await DB.prepare("ALTER TABLE view_rollup_log DROP COLUMN max_id").run();
    await DB.prepare("ALTER TABLE view_rollup_log DROP COLUMN rerolls").run();
    try {
      const old = Math.floor((NOW - 10 * DAY) / DAY) * DAY;
      const gone = Math.floor((NOW - 40 * DAY) / DAY) * DAY; // its raw rows were purged long ago
      const ins = (ts: number, first = 0) =>
        DB.prepare("INSERT INTO view_events (ts, page, src_kind, bot_class, reason, first) VALUES (?, 'home', 'd', 'h', 'ok', ?)").bind(ts, first);
      await DB.batch([ins(old + 1000, 1), ins(old + 2000), ins(old + 3000, 1)]);
      // Aggregates and log rows exactly as 0006-era code wrote them. The old log
      // says two rows were counted, but three exist: one is a never-counted late row.
      await DB.batch([
        DB.prepare("INSERT INTO view_rollup_log VALUES (?, 'UTC', 2, 2, 1, 1, 1, 1, 0)").bind(day(old)),
        DB.prepare("INSERT INTO view_rollup_log VALUES (?, 'UTC', 5, 5, 0, 1, 1, 1, 0)").bind(day(gone)),
        DB.prepare("INSERT INTO view_daily VALUES (?, 'UTC', 'home', 'd', '', 'US', 'h', 2, 1)").bind(day(old)),
        DB.prepare("INSERT INTO view_daily VALUES (?, 'UTC', 'home', 'd', '', 'US', 'h', 5, 0)").bind(day(gone)),
        DB.prepare("INSERT INTO view_meta VALUES ('rolled_through', ?)").bind(day(old)),
      ]);

      for (const sql of mig!.queries) await DB.prepare(sql).run();

      expect(await columns()).toEqual(expect.arrayContaining(["max_id", "rerolls"]));
      expect(await q("SELECT * FROM view_rollup_log")).toEqual([]);
      expect(await q("SELECT * FROM view_daily")).toEqual([]);
      expect(await q("SELECT * FROM view_meta WHERE key = 'rolled_through'")).toEqual([]);

      // The new code rolls the day from the raw rows that exist: all three, the late one included.
      await rollupClosedDays({ DB, MIN_CELL: "1" }, NOW);
      expect(await sum("view_daily")).toBe(3);
      expect((await q<{ uniques: number; max_id: number }>("SELECT * FROM view_rollup_log"))[0]).toMatchObject({ uniques: 2 });
      expect(await q("SELECT * FROM view_rollup_log WHERE day = ?".replace("?", `'${day(gone)}'`))).toEqual([]); // nothing to count: dropped

      // The late row was counted, so only now may the purge take the old raw rows.
      await purgeViews({ DB }, NOW);
      expect((await q<{ n: number }>("SELECT COUNT(*) AS n FROM view_events"))[0]?.n).toBe(0);

      // A row that arrives afterwards for the day whose raw rows were all purged is
      // simply a fresh day of the rows that exist (the old aggregate was dropped).
      await ins(gone + 5000, 1).run();
      await rollupClosedDays({ DB, MIN_CELL: "1" }, NOW + 1000);
      expect((await q<{ v: number }>(`SELECT COALESCE(SUM(views), 0) AS v FROM view_daily WHERE day = '${day(gone)}'`))[0]?.v).toBe(1);
    } finally {
      const cols = await columns();
      if (!cols.includes("max_id")) await DB.prepare("ALTER TABLE view_rollup_log ADD COLUMN max_id INTEGER NOT NULL DEFAULT 0").run();
      if (!cols.includes("rerolls")) await DB.prepare("ALTER TABLE view_rollup_log ADD COLUMN rerolls INTEGER NOT NULL DEFAULT 0").run();
    }
  });
});
