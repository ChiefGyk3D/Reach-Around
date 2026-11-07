// SPDX-License-Identifier: Apache-2.0
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import {
  LIST_MAX,
  UNVERIFIED_RETENTION_DAYS,
  countLeads,
  deleteLeadByEmail,
  deleteLeadById,
  getLead,
  getLeadByEmail,
  listLeads,
  markVerified,
  purgeLeadsCreatedAtOrBefore,
  purgeUnverifiedLeads,
  recordKitEvent,
  touchLead,
  DEFAULT_MAX_LEADS,
  MAX_LEADS_CEILING,
  upsertLead as upsertLeadStrict,
  type LeadSource,
} from "../../src/kit/leads";
import leadsSource from "../../src/kit/leads.ts?raw";

const DB = testEnv.DB;

/** Test shorthand: the production function requires the cap, tests default it. */
const upsertLead = (
  db: D1Database,
  email: string,
  source: LeadSource,
  country: string | null,
  nowMs: number,
  maxLeads: number = DEFAULT_MAX_LEADS,
) => upsertLeadStrict(db, email, source, country, nowMs, maxLeads);
const DAY = 24 * 60 * 60 * 1000;
const HOUR = 60 * 60 * 1000;
const NOW = Date.UTC(2027, 0, 1, 12, 34, 56, 789);

beforeEach(async () => {
  await DB.prepare("DELETE FROM kit_events").run();
  await DB.prepare("DELETE FROM leads").run();
});

describe("upsertLead", () => {
  it("creates a lead with a 32 hex id, then returns the same id with created false", async () => {
    const a = await upsertLead(DB, "partner@example.com", "gate", "US", NOW);
    expect(a).not.toBeNull();
    expect(a!.created).toBe(true);
    expect(a!.id).toMatch(/^[0-9a-f]{32}$/);
    const b = await upsertLead(DB, "partner@example.com", "gate", "US", NOW + 5000);
    expect(b).toEqual({ id: a!.id, created: false });
    expect(await countLeads(DB)).toBe(1);
    const row = await getLead(DB, a!.id);
    expect(row!.created_ts).toBe(NOW);
    expect(row!.last_seen_ts).toBe(NOW + 5000);
    expect(row!.verified_ts).toBeNull();
    expect(row!.source).toBe("gate");
  });

  it("concurrent upserts of one address produce one row and one created", async () => {
    const rs = await Promise.all(
      Array.from({ length: 8 }, () => upsertLead(DB, "race@example.com", "gate", null, NOW)),
    );
    expect(rs.filter((r) => r?.created).length).toBe(1);
    expect(new Set(rs.map((r) => r!.id)).size).toBe(1);
    expect(await countLeads(DB)).toBe(1);
  });

  it("treats the address as already normalized: it refuses what Task 5 would have rejected", async () => {
    const bad = [
      "1.2.3.4",
      "mozilla/5.0",
      "http://example.com/a",
      "a@1.2.3.4",
      "a@[1.2.3.4]",
      "a@b",
      "a@b.c",
      "a@b.c1",
      "a@@b.com",
      "a@b.com@c.com",
      "@b.com",
      "a@",
      "a..b@c.com",
      ".a@c.com",
      "a.@c.com",
      "a@-b.com",
      "a@b-.com",
      "a@b..com",
      "a@.b.com",
      "a@b.com.",
      "a,b@c.com",
      "a;b@c.com",
      "<a@b.com>",
      "\"a\"@b.com",
      "a".repeat(65) + "@b.com",
      "a@" + "b".repeat(64) + ".com",
      "Partner@Example.com",
      " a@b.com",
      "a@b.com\n",
      "a@b.com\r\nBcc: x@y.com",
      "é@b.com",
      "a b@c.com",
      "",
      "a".repeat(250) + "@b.com",
      "a@b.com\u0000",
    ];
    for (const e of bad) {
      await expect(upsertLead(DB, e, "gate", null, NOW)).rejects.toThrow(RangeError);
    }
    await expect(upsertLead(DB, 42 as unknown as string, "gate", null, NOW)).rejects.toThrow(RangeError);
    expect(await countLeads(DB)).toBe(0);
  });

  it("accepts well-formed normalized addresses", async () => {
    const good = ["partner@example.com", "a+tag@sub.example.com", "a.b_c-d@x-y.example.org", "a@b.xn--p1ai", "a".repeat(64) + "@b.co"];
    for (const e of good) expect((await upsertLead(DB, e, "gate", null, NOW))!.created).toBe(true);
  });

  it("statement-shaped input is refused on write and inert on read and delete", async () => {
    const evil = "x');drop/**/table/**/leads;--@example.com";
    await expect(upsertLead(DB, evil, "gate", null, NOW)).rejects.toThrow(RangeError);
    const r = await upsertLead(DB, "real@example.com", "gate", null, NOW);
    expect(r!.created).toBe(true);
    expect(await getLeadByEmail(DB, evil)).toBeNull();
    const probe = "' or '1'='1@example.com";
    expect(await getLeadByEmail(DB, probe)).toBeNull();
    expect(await deleteLeadByEmail(DB, "' or '1'='1")).toBe(0);
    expect(await countLeads(DB)).toBe(1);
    expect(await deleteLeadById(DB, "' or 1=1 --")).toBe(0);
    expect(await countLeads(DB)).toBe(1);
  });

  it("rejects a bad source, country, or clock value", async () => {
    await expect(upsertLead(DB, "a@b.com", "x" as never, null, NOW)).rejects.toThrow(RangeError);
    await expect(upsertLead(DB, "a@b.com", "gate", "usa", NOW)).rejects.toThrow(RangeError);
    await expect(upsertLead(DB, "a@b.com", "gate", "1.2.3.4", NOW)).rejects.toThrow(RangeError);
    await expect(upsertLead(DB, "a@b.com", "gate", null, Number.NaN)).rejects.toThrow(RangeError);
    expect(await countLeads(DB)).toBe(0);
  });

  it("honors maxLeads: a new address is refused at the cap, a known one still updates", async () => {
    await upsertLead(DB, "one@example.com", "gate", null, NOW, 2);
    await upsertLead(DB, "two@example.com", "gate", null, NOW, 2);
    expect(await upsertLead(DB, "three@example.com", "gate", null, NOW, 2)).toBeNull();
    expect(await countLeads(DB)).toBe(2);
    const again = await upsertLead(DB, "two@example.com", "gate", null, NOW + 1, 2);
    expect(again!.created).toBe(false);
    await expect(upsertLead(DB, "four@example.com", "gate", null, NOW, 0)).rejects.toThrow(RangeError);
    await expect(upsertLead(DB, "four@example.com", "gate", null, NOW, 1.5)).rejects.toThrow(RangeError);
  });

  it("the cap is required and bounded", async () => {
    expect(DEFAULT_MAX_LEADS).toBe(5000);
    expect(MAX_LEADS_CEILING).toBeGreaterThanOrEqual(DEFAULT_MAX_LEADS);
    // @ts-expect-error maxLeads is required: omitting it is a compile error
    const omitted = upsertLeadStrict(DB, "cap@example.com", "gate", null, NOW);
    await expect(omitted).rejects.toThrow(RangeError);
    for (const bad of [undefined, null, Number.NaN, Number.POSITIVE_INFINITY, -1, 0, Number.MAX_SAFE_INTEGER, MAX_LEADS_CEILING + 1]) {
      await expect(upsertLeadStrict(DB, "cap@example.com", "gate", null, NOW, bad as never)).rejects.toThrow(RangeError);
    }
    expect((await upsertLeadStrict(DB, "cap@example.com", "gate", null, NOW, MAX_LEADS_CEILING))!.created).toBe(true);
  });
});

describe("lead updates and events", () => {
  it("markVerified and touchLead set their fields", async () => {
    const { id } = (await upsertLead(DB, "v@example.com", "gate", null, NOW))!;
    await touchLead(DB, id, NOW + 10);
    await markVerified(DB, id, NOW + 20);
    const row = await getLead(DB, id);
    expect(row!.last_seen_ts).toBe(NOW + 10);
    expect(row!.verified_ts).toBe(NOW + 20);
    await markVerified(DB, "0".repeat(32), NOW); // unknown id: no throw, no row
    expect(await countLeads(DB)).toBe(1);
  });

  it("recordKitEvent stores the kind at hour precision and nothing else about the visitor", async () => {
    const { id } = (await upsertLead(DB, "e@example.com", "gate", null, NOW))!;
    await recordKitEvent(DB, id, "unlock", NOW);
    await recordKitEvent(DB, id, "view", NOW + HOUR);
    const rows = await DB.prepare("SELECT ts, lead_id, kind FROM kit_events ORDER BY id").all<{
      ts: number;
      lead_id: string;
      kind: string;
    }>();
    expect(rows.results.map((r) => r.kind)).toEqual(["unlock", "view"]);
    for (const r of rows.results) {
      expect(r.ts % HOUR).toBe(0);
      expect(r.lead_id).toBe(id);
    }
    await expect(recordKitEvent(DB, id, "other" as never, NOW)).rejects.toThrow(RangeError);
  });

  it("an event for an unknown lead is refused by the foreign key", async () => {
    await expect(recordKitEvent(DB, "f".repeat(32), "view", NOW)).rejects.toThrow();
  });
});

describe("bounded reads", () => {
  it("listLeads is bounded and ordered, with a cursor", async () => {
    for (let i = 0; i < 5; i++) await upsertLead(DB, `u${i}@example.com`, "gate", null, NOW + i);
    const first = await listLeads(DB, { limit: 2 });
    expect(first.map((l) => l.email)).toEqual(["u0@example.com", "u1@example.com"]);
    const next = await listLeads(DB, { limit: 2, afterCreatedTs: first[1]!.created_ts, afterId: first[1]!.id });
    expect(next.map((l) => l.email)).toEqual(["u2@example.com", "u3@example.com"]);
    expect((await listLeads(DB)).length).toBe(5);
    expect((await listLeads(DB, { limit: 10_000_000 })).length).toBe(5);
    expect(LIST_MAX).toBeLessThanOrEqual(1000);
    await expect(listLeads(DB, { limit: 0 })).rejects.toThrow(RangeError);
    await expect(listLeads(DB, { limit: -1 })).rejects.toThrow(RangeError);
    await expect(listLeads(DB, { limit: 1.5 })).rejects.toThrow(RangeError);
  });

  it("getLead is exact-match only", async () => {
    const { id } = (await upsertLead(DB, "g@example.com", "gate", null, NOW))!;
    expect(await getLead(DB, id)).not.toBeNull();
    expect(await getLead(DB, id.toUpperCase())).toBeNull();
    expect(await getLead(DB, "%")).toBeNull();
    expect(await getLeadByEmail(DB, "%@example.com")).toBeNull();
  });
});

describe("delete", () => {
  it("deletes by id and by email, idempotently, and cascades to events", async () => {
    const a = (await upsertLead(DB, "a@example.com", "gate", null, NOW))!;
    const b = (await upsertLead(DB, "b@example.com", "gate", null, NOW))!;
    await recordKitEvent(DB, a.id, "unlock", NOW);
    await recordKitEvent(DB, b.id, "unlock", NOW);
    expect(await deleteLeadById(DB, a.id)).toBe(1);
    expect(await deleteLeadById(DB, a.id)).toBe(0);
    const ev = await DB.prepare("SELECT lead_id FROM kit_events").all<{ lead_id: string }>();
    expect(ev.results.map((r) => r.lead_id)).toEqual([b.id]);
    expect(await deleteLeadByEmail(DB, "b@example.com")).toBe(1);
    expect(await deleteLeadByEmail(DB, "b@example.com")).toBe(0);
    expect(await deleteLeadByEmail(DB, "never@example.com")).toBe(0);
    expect((await DB.prepare("SELECT COUNT(*) AS n FROM kit_events").first<{ n: number }>())!.n).toBe(0);
  });
});

describe("retention purge", () => {
  async function seed(email: string, createdTs: number, verified = false) {
    const { id } = (await upsertLead(DB, email, "gate", null, createdTs))!;
    if (verified) await markVerified(DB, id, createdTs);
    return id;
  }

  it("defaults: unverified leads are kept 30 days", () => {
    expect(UNVERIFIED_RETENTION_DAYS).toBe(30);
  });

  it("purgeUnverifiedLeads deletes rows at or older than the cutoff and never verified rows", async () => {
    const cutoff = NOW - 30 * DAY;
    const older = await seed("older@example.com", cutoff - 1);
    const exact = await seed("exact@example.com", cutoff);
    const newer = await seed("newer@example.com", cutoff + 1);
    const verifiedOld = await seed("verified@example.com", cutoff - 100 * DAY, true);
    await recordKitEvent(DB, older, "unlock", cutoff);
    expect(await purgeUnverifiedLeads(DB, cutoff)).toBe(2);
    expect(await getLead(DB, older)).toBeNull();
    expect(await getLead(DB, exact)).toBeNull();
    for (const id of [newer, verifiedOld]) expect(await getLead(DB, id)).not.toBeNull();
    expect((await DB.prepare("SELECT COUNT(*) AS n FROM kit_events").first<{ n: number }>())!.n).toBe(0);
    expect(await purgeUnverifiedLeads(DB, cutoff)).toBe(0);
  });

  it("purgeLeadsCreatedAtOrBefore is the hard maximum and also takes verified rows", async () => {
    const cutoff = NOW - 365 * DAY;
    const old = await seed("old@example.com", cutoff - 1, true);
    const edge = await seed("edge@example.com", cutoff, true);
    const inside = await seed("inside@example.com", cutoff + 1, true);
    expect(await purgeLeadsCreatedAtOrBefore(DB, cutoff)).toBe(2);
    expect(await getLead(DB, old)).toBeNull();
    expect(await getLead(DB, edge)).toBeNull();
    expect(await getLead(DB, inside)).not.toBeNull();
  });

  it("refuses a cutoff that is not a finite number (it would wipe or keep everything)", async () => {
    await seed("keep@example.com", NOW);
    for (const bad of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY, "1" as never]) {
      await expect(purgeUnverifiedLeads(DB, bad)).rejects.toThrow(RangeError);
      await expect(purgeLeadsCreatedAtOrBefore(DB, bad)).rejects.toThrow(RangeError);
    }
    expect(await countLeads(DB)).toBe(1);
  });
});

describe("schema", () => {
  const FORBIDDEN = /ip|addr|agent|\bua\b|_ua\b|referr|referer|fingerprint|host|url|lat|lon|postal|asn/i;
  const KIT_TABLES = ["leads", "kit_events", "counters", "kit_meta"];

  it("no kit column can hold an IP address or user agent by name", async () => {
    for (const t of KIT_TABLES) {
      const cols = await DB.prepare(`SELECT name FROM pragma_table_info('${t}')`).all<{ name: string }>();
      expect(cols.results.length, `${t} exists`).toBeGreaterThan(0);
      for (const c of cols.results) expect(c.name, `${t}.${c.name}`).not.toMatch(FORBIDDEN);
    }
  });

  it("the name guard bites", () => {
    for (const n of ["ip", "client_ip", "remote_addr", "user_agent", "ua", "referrer"]) {
      expect(n).toMatch(FORBIDDEN);
    }
  });

  const ID = "a".repeat(32);
  const SHAPES = {
    ip4: "203.0.113.9",
    ip6: "2001:db8::1",
    ua: "Mozilla/5.0 (X11; Linux x86_64)",
    url: "https://example.com/path?q=1",
    ms: "1700000000123",
  };
  const run = (sql: string, ...binds: unknown[]) => DB.prepare(sql).bind(...binds).run();
  const LEAD = "INSERT INTO leads (id, email, created_ts, last_seen_ts, source, country) VALUES (?, ?, ?, ?, ?, ?)";

  it("every text column of every kit table refuses IP, UA and URL shaped values", async () => {
    for (const v of [SHAPES.ip4, SHAPES.ip6, SHAPES.ua, SHAPES.url, "1.2.3.4", "a@1.2.3.4"]) {
      await expect(run(LEAD, ID, v, 1, 1, "gate", null), `leads.email ${v}`).rejects.toThrow();
      await expect(run(LEAD, v.padEnd(32, "a").slice(0, 32), "x@y.com", 1, 1, "gate", null), `leads.id ${v}`).rejects.toThrow();
      await expect(run(LEAD, ID, "x@y.com", 1, 1, v, null), `leads.source ${v}`).rejects.toThrow();
      await expect(run(LEAD, ID, "x@y.com", 1, 1, "gate", v), `leads.country ${v}`).rejects.toThrow();
      await expect(run("INSERT INTO kit_events (ts, lead_id, kind) VALUES (3600000, ?, ?)", ID, v), `kit_events.kind ${v}`).rejects.toThrow();
      await expect(run("INSERT INTO counters (key, day, count) VALUES (?, '2027-01-01', 1)", v), `counters.key ${v}`).rejects.toThrow();
      await expect(run("INSERT INTO counters (key, day, count) VALUES ('gate', ?, 1)", v), `counters.day ${v}`).rejects.toThrow();
      await expect(run("INSERT INTO kit_meta (key, value) VALUES (?, 1)", v), `kit_meta.key ${v}`).rejects.toThrow();
      await expect(run("INSERT INTO kit_meta (key, value) VALUES ('last_purge_ts', ?)", v), `kit_meta.value ${v}`).rejects.toThrow();
    }
    expect(await countLeads(DB)).toBe(0);
  });

  it("the tables are STRICT, so text cannot land in an integer column", async () => {
    const t = await DB.prepare("SELECT name, strict FROM pragma_table_list WHERE schema = 'main' AND name IN ('leads','kit_events','counters','kit_meta')").all<{ name: string; strict: number }>();
    expect(t.results.length).toBe(4);
    for (const r of t.results) expect(r.strict, r.name).toBe(1);
    await expect(run(LEAD, ID, "x@y.com", "Mozilla/5.0", 1, "gate", null)).rejects.toThrow();
    await expect(run(LEAD, ID, "x@y.com", 1, "1.2.3.4", "gate", null)).rejects.toThrow();
    await expect(run(LEAD, ID, "x@y.com", 1.5, 1, "gate", null)).rejects.toThrow();
    await run(LEAD, ID, "x@y.com", 1, 1, "gate", null);
    await expect(run("UPDATE leads SET verified_ts = ? WHERE id = ?", "1.2.3.4", ID)).rejects.toThrow();
    await expect(run("INSERT INTO kit_events (ts, lead_id, kind) VALUES (?, ?, 'view')", "1.2.3.4", ID)).rejects.toThrow();
    await expect(run("INSERT INTO counters (key, day, count) VALUES ('gate', '2027-01-01', ?)", "Mozilla/5.0")).rejects.toThrow();
    await expect(run("INSERT INTO kit_meta (key, value) VALUES ('last_purge_ts', ?)", "Mozilla/5.0")).rejects.toThrow();
  });

  it("kit_events.ts must be a whole-hour integer", async () => {
    await run(LEAD, ID, "x@y.com", 1, 1, "gate", null);
    const ins = "INSERT INTO kit_events (ts, lead_id, kind) VALUES (?, ?, 'view')";
    for (const bad of [1700000000123, 3600001, 3599999, 1, -3600000, 3600000.5]) {
      await expect(run(ins, bad, ID), String(bad)).rejects.toThrow();
    }
    await run(ins, 3600000, ID);
    await run(ins, 0, ID);
  });

  it("counters and kit_meta take only allow-listed keys and integer values", async () => {
    await run("INSERT INTO counters (key, day, count) VALUES ('gate_hour', '2027-01-01T05', 3)");
    await run("INSERT INTO counters (key, day, count) VALUES ('gate_day', '2027-01-01', 3)");
    await run("INSERT INTO kit_meta (key, value) VALUES ('last_purge_ts', 1700000000000)");
    for (const key of ["", "Gate", "a b", "a.b", "a:b", "a/b", "x".repeat(65)]) {
      await expect(run("INSERT INTO counters (key, day, count) VALUES (?, '2027-01-02', 1)", key), key).rejects.toThrow();
      await expect(run("INSERT INTO kit_meta (key, value) VALUES (?, 1)", key), key).rejects.toThrow();
    }
    for (const day of ["", "2027-1-1", "2027-01-01 05:00", "2027-01-01T05:00:00", "today"]) {
      await expect(run("INSERT INTO counters (key, day, count) VALUES ('gate', ?, 1)", day), day).rejects.toThrow();
    }
    await expect(run("INSERT INTO counters (key, day, count) VALUES ('gate', '2027-01-03', -1)")).rejects.toThrow();
  });

  it("leads reject uppercase, malformed and non-email text, lowercase millisecond-free ids, and bad ranges", async () => {
    const bad = ["UPPER@y.com", "a@b", "a@b.c", "a@@b.com", "a@b.com@c.com", "a b@c.com", "a@b.c1", "a@1.2.3.4", "mozilla/5.0", "a@b.com\n", "é@b.com", "a@b..com", ".a@b.com", "a@-b.com", "a@b.com."];
    for (const e of bad) await expect(run(LEAD, ID, e, 1, 1, "gate", null), e).rejects.toThrow();
    await expect(run(LEAD, "short", "x@y.com", 1, 1, "gate", null)).rejects.toThrow();
    await expect(run(LEAD, "A".repeat(32), "x@y.com", 1, 1, "gate", null)).rejects.toThrow();
    await expect(run(LEAD, ID, "x@y.com", -1, 1, "gate", null)).rejects.toThrow();
    await run(LEAD, ID, "x@y.com", 1, 1, "gate", "US");
    expect(await countLeads(DB)).toBe(1);
  });

  it("the migration declares the columns later tasks write", async () => {
    const cols = async (t: string) =>
      (await DB.prepare(`SELECT name FROM pragma_table_info('${t}')`).all<{ name: string }>()).results.map((r) => r.name);
    expect(await cols("leads")).toEqual(
      expect.arrayContaining([
        "id", "email", "created_ts", "last_seen_ts", "verified_ts", "source", "country",
        "last_view_alert_ts", "last_view_event_ts", "last_cc_alert_ts", "session_floor_ts",
      ]),
    );
    expect(await cols("kit_events")).toEqual(["id", "ts", "lead_id", "kind"]);
    expect(await cols("counters")).toEqual(["key", "day", "count"]);
    expect(await cols("kit_meta")).toEqual(["key", "value"]);
  });

  it("has indexes on the retention columns", async () => {
    const idx = await DB.prepare("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name IN ('leads','kit_events')").all<{ name: string }>();
    const names = idx.results.map((r) => r.name);
    expect(names).toEqual(expect.arrayContaining(["idx_kit_events_ts", "idx_leads_created_ts", "idx_leads_verified_ts"]));
  });
});

describe("source hygiene", () => {
  it("no SQL in leads.ts is built by interpolation", () => {
    const literals = leadsSource.match(/`[^`]*`/g) ?? [];
    for (const lit of literals) {
      if (/SELECT|INSERT|UPDATE|DELETE/i.test(lit)) expect(lit).not.toContain("${");
    }
    // negative case: the detector flags an interpolated statement
    const sample = "const q = `SELECT * FROM leads WHERE email = '${e}'`;";
    const flagged = (sample.match(/`[^`]*`/g) ?? []).filter((l) => /SELECT/i.test(l) && l.includes("${"));
    expect(flagged.length).toBe(1);
    expect(leadsSource).toContain(".bind(");
  });
});
