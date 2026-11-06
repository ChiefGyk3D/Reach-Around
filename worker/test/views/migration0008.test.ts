// SPDX-License-Identifier: Apache-2.0
// Migration 0008 (site column, composite keys, cutover). The test puts the
// database back into its 0007 shape, fills it the way the old code did, runs
// the migration's statements and checks the result. The migration leaves the
// database in the shape every other test expects.
import { env as testEnv } from "cloudflare:workers";
import { describe, expect, it } from "vitest";
import { DB } from "./helpers";

const q = async <T>(sql: string) => (await DB.prepare(sql).all<T>()).results;
const cols = async (t: string) => (await q<{ name: string }>(`SELECT name FROM pragma_table_info('${t}')`)).map((c) => c.name);
const pk = async (t: string) =>
  (await q<{ name: string }>(`SELECT name FROM pragma_table_info('${t}') WHERE pk > 0 ORDER BY pk`)).map((c) => c.name);
const migration = (prefix: string) => {
  const m = testEnv.TEST_MIGRATIONS.find((x) => x.name.startsWith(prefix));
  expect(m, `migration ${prefix} is present`).toBeDefined();
  return m!.queries;
};

async function toSevenShape() {
  await DB.exec("DROP INDEX idx_view_events_site_ts");
  await DB.exec("ALTER TABLE view_events DROP COLUMN site");
  await DB.exec("ALTER TABLE click_events DROP COLUMN site");
  for (const t of ["view_daily", "view_dim_daily", "view_geo_daily", "view_rollup_log"]) await DB.exec(`DROP TABLE ${t}`);
  for (const sql of [...migration("0006"), ...migration("0007")]) await DB.prepare(sql).run();
}

describe("migration 0008", () => {
  it("is_after_0007_and_rebuilds_with_site_first_keeping_every_row_as_links", async () => {
    expect(testEnv.TEST_MIGRATIONS.map((m) => m.name).sort().at(-1)).toMatch(/^0008/);
    await toSevenShape();
    expect(await cols("view_rollup_log")).not.toContain("site");

    const ins = (ts: number, net = "residential", id?: number) =>
      DB.prepare(
        `INSERT INTO view_events (${id ? "id, " : ""}ts, page, src_kind, bot_class, reason, net) VALUES (${id ? `${id}, ` : ""}?, 'home', 'd', 'h', 'ok', ?)`,
      ).bind(ts, net);
    const minuteOdd = Date.parse("2027-03-10T09:41:00Z");
    await DB.batch([
      ins(minuteOdd),
      ins(Date.parse("2027-03-10T23:59:00Z"), "vpn-proxy"),
      DB.prepare("INSERT INTO click_events (ts, link_id, country) VALUES (5, 'a', 'US')"),
      DB.prepare("INSERT INTO view_daily VALUES ('2027-03-09','UTC','home','d','','US','h',4,2)"),
      DB.prepare("INSERT INTO view_dim_daily VALUES ('2027-03-09','UTC','os','linux','h',4,2)"),
      DB.prepare("INSERT INTO view_geo_daily VALUES ('2027-03-09','UTC','US','CA','Sentinelville','h',4,2)"),
      DB.prepare("INSERT INTO view_rollup_log VALUES ('2027-03-09','UTC',4,4,2,1,1,1,99,77,2)"),
      DB.prepare("INSERT INTO visitor_seen VALUES ('2027-03-10','aaaaaaaaaaaaaaaa')"),
      DB.prepare("INSERT INTO view_meta VALUES ('rolled_through','2027-03-09'), ('rollup_failed_day','2027-03-08'), ('rollup_err','none')"),
    ]);

    for (const sql of migration("0008")) await DB.prepare(sql).run();

    // Composite keys, site first, in all four.
    for (const t of ["view_daily", "view_dim_daily", "view_geo_daily", "view_rollup_log"]) expect((await pk(t))[0], t).toBe("site");
    expect(await pk("view_rollup_log")).toEqual(["site", "day"]);

    // Every old row is copied as 'links', values unchanged.
    expect(await q("SELECT * FROM view_daily")).toEqual([
      { site: "links", day: "2027-03-09", day_tz: "UTC", page: "home", src_kind: "d", src: "", country: "US", bot_class: "h", views: 4, uniques: 2 },
    ]);
    expect(await q("SELECT site, dim, value, views FROM view_dim_daily")).toEqual([{ site: "links", dim: "os", value: "linux", views: 4 }]);
    expect(await q("SELECT site, city, uniques FROM view_geo_daily")).toEqual([{ site: "links", city: "Sentinelville", uniques: 2 }]);
    expect(await q("SELECT * FROM view_rollup_log")).toEqual([
      { site: "links", day: "2027-03-09", day_tz: "UTC", raw_views: 4, views: 4, uniques: 2, rows_core: 1, rows_dim: 1, rows_geo: 1, rolled_at: 99, max_id: 77, rerolls: 2 },
    ]);
    expect(await q("SELECT site FROM click_events")).toEqual([{ site: "links" }]);

    // Cutover: no site-less daily code survives.
    expect(await q("SELECT * FROM visitor_seen")).toEqual([]);

    // Raw rows: site links, hour-rounded, net cleared.
    const raw = await q<{ ts: number; site: string; net: string }>("SELECT ts, site, net FROM view_events ORDER BY id");
    expect(raw.map((r) => r.site)).toEqual(["links", "links"]);
    expect(raw.map((r) => r.net)).toEqual(["unknown", "unknown"]);
    for (const r of raw) expect(r.ts % 3_600_000).toBe(0);
    expect(raw[0]?.ts).toBe(Date.parse("2027-03-10T09:00:00Z"));
    expect(raw[1]?.ts).toBe(Date.parse("2027-03-10T23:00:00Z")); // same UTC day

    // State keys became per site; unrelated keys are untouched.
    const meta = Object.fromEntries((await q<{ key: string; value: string }>("SELECT * FROM view_meta")).map((m) => [m.key, m.value]));
    expect(meta).toEqual({ "rolled_through:links": "2027-03-09", "rollup_failed_day:links": "2027-03-08", rollup_err: "none" });

    // The same site is the primary key: a second row for it is refused, a row for another site is not.
    await expect(
      DB.prepare("INSERT INTO view_rollup_log VALUES ('links','2027-03-09','UTC',1,1,0,1,1,1,0,0,0)").run(),
    ).rejects.toThrow();
    await expect(
      DB.prepare("INSERT INTO view_rollup_log VALUES ('blog','2027-03-09','UTC',1,1,0,1,1,1,0,0,0)").run(),
    ).resolves.toBeTruthy();
    await DB.exec("DELETE FROM view_rollup_log; DELETE FROM view_events; DELETE FROM click_events; DELETE FROM view_meta");
  });

  it("an_empty_database_migrates_cleanly", async () => {
    await toSevenShape();
    for (const sql of migration("0008")) await DB.prepare(sql).run();
    expect(await cols("view_events")).toContain("site");
    expect(await q("SELECT * FROM view_daily")).toEqual([]);
  });
});
