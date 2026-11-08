// SPDX-License-Identifier: Apache-2.0
// Dump test (issue #272): drive every write route with distinctive addresses and
// user agents, run the rollup, then read EVERY table (and the schema) back and
// prove no address and no user agent is anywhere in it. The scanner mirrors
// scripts/check-dump.py, which runs the same check on a real `wrangler d1 export`.
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import { handleClick, type Env as ClickEnv } from "../src/click";
import { rollupClosedDays } from "../src/viewrollup";
import { handleView } from "../src/view";
import { IP4, IP6, NOW, beacon, clearAll, fakeLimiter, makeEnv } from "./views/helpers";

const DB = testEnv.DB;

const OCTET = "(?:25[0-5]|2[0-4]\\d|1?\\d?\\d)";
const IPV4 = new RegExp(`(?<![\\d.])${OCTET}(?:\\.${OCTET}){3}(?!\\d|\\.\\d)`);
const IPV6 = new RegExp(
  "(?<![0-9a-f:])(?:(?:[0-9a-f]{1,4}:){1,7}:(?:[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){0,6})?" +
    "|::(?:[0-9a-f]{1,4}:){0,6}[0-9a-f]{1,4}|(?:[0-9a-f]{1,4}:){7}[0-9a-f]{1,4})(?![0-9a-f:])",
  "i",
);
const UA = /mozilla|applewebkit|gecko\/|\b[a-z][a-z0-9_-]*\/\d+(?:\.\d+)+|windows nt|\bx11;|linux x86_64|android \d|iphone os|\bmsie \d/i;

/** The guard under test: names of the kinds of leak found, never the values. */
function findLeaks(text: string): string[] {
  const out: string[] = [];
  if (IPV4.test(text)) out.push("ipv4");
  if (IPV6.test(text)) out.push("ipv6");
  if (UA.test(text)) out.push("user agent");
  return out;
}

async function dumpAll(): Promise<{ text: string; tables: string[]; rowCount: number }> {
  const master = await DB.prepare(
    "SELECT name, sql FROM sqlite_master WHERE name NOT LIKE '_cf_%' AND name NOT LIKE 'sqlite_%'",
  ).all<{ name: string; sql: string | null }>();
  const out: Record<string, unknown> = { schema: master.results.map((m) => m.sql) };
  const tables: string[] = [];
  let rowCount = 0;
  for (const m of master.results) {
    if (!m.sql || !/^CREATE TABLE/i.test(m.sql)) continue;
    tables.push(m.name);
    const rows = (await DB.prepare(`SELECT * FROM "${m.name}"`).all()).results;
    rowCount += rows.length;
    out[m.name] = rows;
  }
  return { text: JSON.stringify(out), tables, rowCount };
}

const UAS = [
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.6613.84 Safari/537.36",
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
  "curl/8.5.0",
  "Googlebot/2.1 (+http://www.google.com/bot.html)",
];
const IPS = [IP4, IP6, "203.0.113.9", "2001:db8:85a3:0:0:8a2e:370:7334"];

beforeEach(async () => {
  await clearAll();
  await DB.prepare("DELETE FROM click_events").run();
});

describe("no raw address or user agent in a database dump", () => {
  it("dump_after_clicks_views_and_rollup_holds_none", async () => {
    const clickEnv: ClickEnv = {
      DB,
      SITE_HOST: "links.example.test",
      LINK_IDS: JSON.stringify(["example-one"]),
      CLICK_LIMITER: fakeLimiter(),
    };
    for (let i = 0; i < IPS.length; i++) {
      const headers = {
        "User-Agent": UAS[i]!,
        "Ping-From": "https://links.example.test/",
        "CF-Connecting-IP": IPS[i]!,
        "CF-IPCountry": "US",
        "X-Forwarded-For": IPS[i]!,
      };
      const res = await handleClick(
        new Request("https://worker.example.test/e/c/example-one", { method: "POST", headers }),
        clickEnv,
      );
      expect(res.status).toBe(204);
      const v = await handleView(
        beacon({ headers: { "User-Agent": UAS[i]!, "CF-Connecting-IP": IPS[i]!, "X-Forwarded-For": IPS[i]! } }),
        makeEnv(),
        NOW + i * 1000,
      );
      expect(v.status).toBe(204);
    }
    // Loose collection: both raw tables and the visitor table really hold rows.
    const clicks = await DB.prepare("SELECT COUNT(*) AS n FROM click_events").first<{ n: number }>();
    const views = await DB.prepare("SELECT COUNT(*) AS n FROM view_events").first<{ n: number }>();
    const seen = await DB.prepare("SELECT COUNT(*) AS n FROM visitor_seen").first<{ n: number }>();
    expect(clicks?.n).toBeGreaterThanOrEqual(1);
    expect(views?.n).toBeGreaterThanOrEqual(1);
    expect(seen?.n).toBeGreaterThanOrEqual(1);

    // Close the day so the aggregate tables are filled too.
    await rollupClosedDays({ DB, TOP_SOURCES: "1000", MIN_CELL: "1" }, NOW + 3 * 86_400_000);
    const rolled = await DB.prepare("SELECT COUNT(*) AS n FROM view_daily").first<{ n: number }>();
    expect(rolled?.n).toBeGreaterThanOrEqual(1);

    const dump = await dumpAll();
    expect(dump.tables.length).toBeGreaterThanOrEqual(8);
    expect(dump.rowCount).toBeGreaterThanOrEqual(3);
    // Strict assertions.
    expect(findLeaks(dump.text)).toEqual([]);
    for (const s of [...IPS, ...UAS]) expect(dump.text.includes(s), "sentinel in dump").toBe(false);
  });

  it("the_scanner_bites_on_each_kind", async () => {
    // Negative control: put each kind straight into a table and see it flagged.
    const cases: Array<[string, string]> = [
      ["ipv4", IP4],
      ["ipv6", IP6],
      ["user agent", UAS[0]!],
      ["user agent", "curl/8.5.0"],
    ];
    for (const [kind, value] of cases) {
      await DB.prepare("DELETE FROM view_meta").run();
      await DB.prepare("INSERT INTO view_meta (key, value) VALUES ('probe', ?)").bind(value).run();
      const dump = await dumpAll();
      expect(findLeaks(dump.text), kind).toContain(kind);
    }
    await DB.prepare("DELETE FROM view_meta").run();
    expect(findLeaks((await dumpAll()).text)).toEqual([]);
  });
});
