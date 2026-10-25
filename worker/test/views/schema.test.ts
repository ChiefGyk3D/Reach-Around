// SPDX-License-Identifier: Apache-2.0
import { env as testEnv } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

const DB = testEnv.DB;

/** The exact column list of every page-view table (Plan 4 Task A3). */
const EXPECTED_COLUMNS: Record<string, string[]> = {
  view_events: [
    "id", "ts", "page", "src_kind", "src", "country", "region", "city", "browser", "bmajor",
    "os", "device", "lang", "vp", "net", "bot_class", "reason", "first",
  ],
  visitor_seen: ["day", "h"],
  view_meta: ["key", "value"],
  view_daily: [
    "day", "day_tz", "page", "src_kind", "src", "country", "bot_class", "views", "uniques",
  ],
  view_dim_daily: ["day", "day_tz", "dim", "value", "bot_class", "views", "uniques"],
  view_geo_daily: [
    "day", "day_tz", "country", "region", "city", "bot_class", "views", "uniques",
  ],
  view_rollup_log: [
    "day", "day_tz", "raw_views", "views", "uniques", "rows_core", "rows_dim", "rows_geo",
    "rolled_at", "max_id",
  ],
};

const FORBIDDEN_COLUMN = /ip|agent|referrer|url|query|lat|lon|postal|asn|org|fingerprint/i;

type Col = { table: string; column: string };

async function columnsOf(table: string): Promise<string[]> {
  const res = await DB.prepare(`SELECT name FROM pragma_table_info('${table}')`).all<{
    name: string;
  }>();
  return res.results.map((r) => r.name);
}

async function allColumns(): Promise<Col[]> {
  const tables = await DB.prepare(
    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE '_cf_%'",
  ).all<{ name: string }>();
  const out: Col[] = [];
  for (const t of tables.results) {
    for (const column of await columnsOf(t.name)) out.push({ table: t.name, column });
  }
  return out;
}

/** The guard under test: returns the offending columns, if any. */
function flagForbidden(cols: Col[]): Col[] {
  return cols.filter((c) => FORBIDDEN_COLUMN.test(c.column));
}

describe("page view schema", () => {
  it("tables_exist_after_migrations", async () => {
    const res = await DB.prepare("SELECT name FROM sqlite_master WHERE type = 'table'").all<{
      name: string;
    }>();
    const names = new Set(res.results.map((r) => r.name));
    for (const t of Object.keys(EXPECTED_COLUMNS)) expect(names.has(t), t).toBe(true);
  });

  it("column_allowlist_per_table", async () => {
    for (const [table, cols] of Object.entries(EXPECTED_COLUMNS)) {
      expect(await columnsOf(table), table).toEqual(cols);
    }
  });

  it("column_name_denylist_scan", async () => {
    const cols = await allColumns();
    // Loose collection: something real was scanned.
    expect(new Set(cols.map((c) => c.table)).size).toBeGreaterThanOrEqual(8);
    // Strict assertion.
    expect(flagForbidden(cols)).toEqual([]);
  });

  it("column_name_denylist_scan_bites", async () => {
    await DB.exec("CREATE TABLE scratch_guard_probe (user_agent TEXT, ip TEXT)");
    try {
      const flagged = flagForbidden(await allColumns()).map((c) => c.column);
      expect(flagged).toContain("user_agent");
      expect(flagged).toContain("ip");
    } finally {
      await DB.exec("DROP TABLE scratch_guard_probe");
    }
  });

  it("no_join_key_between_visitor_seen_and_events", async () => {
    const seen = await columnsOf("visitor_seen");
    expect(seen).toEqual(["day", "h"]);
    const events = new Set(await columnsOf("view_events"));
    expect(seen.filter((c) => events.has(c))).toEqual([]);
    const fks = await DB.prepare("SELECT * FROM pragma_foreign_key_list('visitor_seen')").all();
    expect(fks.results).toEqual([]);
  });

  it("bot_class_and_src_kind_checks_bite", async () => {
    const ins = (kind: string, cls: string) =>
      DB.prepare(
        "INSERT INTO view_events (ts, page, src_kind, bot_class, reason) VALUES (0, 'home', ?, ?, 'none')",
      )
        .bind(kind, cls)
        .run();
    await expect(ins("d", "x")).rejects.toThrow();
    await expect(ins("z", "h")).rejects.toThrow();
    await expect(ins("d", "h")).resolves.toBeTruthy();
    await DB.prepare("DELETE FROM view_events").run();
  });

  it("aggregate_views_must_be_positive", async () => {
    await expect(
      DB.prepare(
        "INSERT INTO view_daily (day, day_tz, page, src_kind, src, country, bot_class, views, uniques) VALUES ('2027-01-01','America/New_York','home','d','','US','h',0,0)",
      ).run(),
    ).rejects.toThrow();
    await expect(
      DB.prepare(
        "INSERT INTO view_dim_daily (day, day_tz, dim, value, bot_class, views, uniques) VALUES ('2027-01-01','America/New_York','color','x','h',1,0)",
      ).run(),
    ).rejects.toThrow();
  });

  it("existing_click_events_unchanged", async () => {
    expect(await columnsOf("click_events")).toEqual(["id", "ts", "link_id", "country"]);
  });

  it("view_events_defaults_are_closed_values", async () => {
    await DB.prepare(
      "INSERT INTO view_events (ts, page, src_kind, bot_class, reason) VALUES (0, 'home', 'd', 'h', 'none')",
    ).run();
    const row = await DB.prepare("SELECT * FROM view_events").first<Record<string, unknown>>();
    expect(row).toMatchObject({
      src: "", country: "", region: "", city: "", browser: "other", bmajor: 0, os: "other",
      device: "other", lang: "", vp: "", net: "unknown", first: 0,
    });
    await DB.prepare("DELETE FROM view_events").run();
  });
});
