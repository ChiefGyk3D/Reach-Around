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
  purgeLeadsCreatedBefore,
  purgeUnverifiedLeads,
  recordKitEvent,
  touchLead,
  upsertLead,
} from "../../src/kit/leads";
import leadsSource from "../../src/kit/leads.ts?raw";

const DB = testEnv.DB;
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

  it("stores injection-shaped (but normalized) input as inert data", async () => {
    const evil = "x');drop/**/table/**/leads;--@example.com";
    const r = await upsertLead(DB, evil, "gate", null, NOW);
    expect(r!.created).toBe(true);
    expect((await getLeadByEmail(DB, evil))!.id).toBe(r!.id);
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

  it("purgeUnverifiedLeads deletes strictly older than the cutoff and never verified rows", async () => {
    const cutoff = NOW - 30 * DAY;
    const older = await seed("older@example.com", cutoff - 1);
    const exact = await seed("exact@example.com", cutoff);
    const newer = await seed("newer@example.com", cutoff + 1);
    const verifiedOld = await seed("verified@example.com", cutoff - 100 * DAY, true);
    await recordKitEvent(DB, older, "unlock", cutoff);
    expect(await purgeUnverifiedLeads(DB, cutoff)).toBe(1);
    expect(await getLead(DB, older)).toBeNull();
    for (const id of [exact, newer, verifiedOld]) expect(await getLead(DB, id)).not.toBeNull();
    expect((await DB.prepare("SELECT COUNT(*) AS n FROM kit_events").first<{ n: number }>())!.n).toBe(0);
    expect(await purgeUnverifiedLeads(DB, cutoff)).toBe(0);
  });

  it("purgeLeadsCreatedBefore is the hard maximum and also takes verified rows", async () => {
    const cutoff = NOW - 365 * DAY;
    const old = await seed("old@example.com", cutoff - 1, true);
    const edge = await seed("edge@example.com", cutoff, true);
    expect(await purgeLeadsCreatedBefore(DB, cutoff)).toBe(1);
    expect(await getLead(DB, old)).toBeNull();
    expect(await getLead(DB, edge)).not.toBeNull();
  });

  it("refuses a cutoff that is not a finite number (it would wipe or keep everything)", async () => {
    await seed("keep@example.com", NOW);
    for (const bad of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY, "1" as never]) {
      await expect(purgeUnverifiedLeads(DB, bad)).rejects.toThrow(RangeError);
      await expect(purgeLeadsCreatedBefore(DB, bad)).rejects.toThrow(RangeError);
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

  it("the free-text columns refuse an address or agent string even when written directly", async () => {
    const insert = (cols: string, vals: unknown[]) =>
      DB.prepare(`INSERT INTO leads (id, email, created_ts, last_seen_ts, ${cols}) VALUES (?, ?, 1, 1, ${vals.map(() => "?").join(",")})`);
    const id = "a".repeat(32);
    await expect(insert("country", ["203.0.113.9"]).bind(id, "x@y.com", "203.0.113.9").run()).rejects.toThrow();
    await expect(insert("country", ["2001:db8::1"]).bind(id, "x@y.com", "2001:db8::1").run()).rejects.toThrow();
    await expect(insert("source", ["Mozilla/5.0"]).bind(id, "x@y.com", "Mozilla/5.0").run()).rejects.toThrow();
    await expect(
      DB.prepare("INSERT INTO leads (id, email, created_ts, last_seen_ts, source) VALUES (?, ?, 1, 1, 'gate')")
        .bind("short", "x@y.com").run(),
    ).rejects.toThrow();
    await expect(
      DB.prepare("INSERT INTO leads (id, email, created_ts, last_seen_ts, source) VALUES (?, ?, 1, 1, 'gate')")
        .bind(id, "UPPER@y.com").run(),
    ).rejects.toThrow();
    await expect(
      DB.prepare("INSERT INTO kit_events (ts, lead_id, kind) VALUES (1, ?, 'Mozilla/5.0')").bind(id).run(),
    ).rejects.toThrow();
    expect(await countLeads(DB)).toBe(0);
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
