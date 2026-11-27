// SPDX-License-Identifier: Apache-2.0
import { env as testEnv } from "cloudflare:workers";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { UNSTORED_FLOOR_KEY } from "../../src/kit/leads";
import { resetSeparationCache } from "../../src/kit/separation";
import { authorizeSession } from "../../src/kit/session";
import type { TokenClaims } from "../../src/kit/token";
import { mintUnstoredId } from "../../src/kit/unstored";

const KEY = "k".repeat(40);
const LEAD_ID = "a".repeat(32);
const T0 = Date.UTC(2026, 10, 9, 14, 3, 30);
const IAT = Math.floor(T0 / 1000) - 60;

const claims = (over: Partial<TokenClaims> = {}): TokenClaims => ({ lid: LEAD_ID, iat: IAT, exp: IAT + 3600, cc: "", ...over }) as TokenClaims;

// A db whose reads return a chosen floor, so a value the schema would refuse can still reach the helper.
function fakeDb(row: Record<string, unknown> | null): D1Database {
  const stmt = { bind: () => stmt, first: async () => row };
  return { prepare: () => stmt } as unknown as D1Database;
}

// The real leads database, except that the lead lookup answers a chosen row (or throws). Everything else
// passes through, so the separation proof still runs against the real database.
function leadsAnswering(row: Record<string, unknown> | null | Error): D1Database {
  return new Proxy(testEnv.LEADS_DB, {
    get(target, prop) {
      if (prop === "prepare") {
        return (q: string) => {
          if (!/FROM leads WHERE id/.test(q)) return target.prepare(q);
          if (row instanceof Error) throw row;
          const stmt = { bind: () => stmt, first: async () => row };
          return stmt;
        };
      }
      const v = Reflect.get(target, prop) as unknown;
      return typeof v === "function" ? (v as (...a: unknown[]) => unknown).bind(target) : v;
    },
  }) as D1Database;
}

// A handle that records every statement prepared through it.
function recording(base: D1Database): { db: D1Database; sql: string[] } {
  const sql: string[] = [];
  const db = new Proxy(base, {
    get(target, prop) {
      if (prop === "prepare") {
        return (q: string) => {
          sql.push(q);
          return target.prepare(q);
        };
      }
      const v = Reflect.get(target, prop) as unknown;
      return typeof v === "function" ? (v as (...a: unknown[]) => unknown).bind(target) : v;
    },
  }) as D1Database;
  return { db, sql };
}

const LEADS = testEnv.LEADS_DB;
const MAIN = testEnv.DB;

beforeEach(async () => {
  resetSeparationCache();
  await MAIN.batch([MAIN.prepare("DELETE FROM leads"), MAIN.prepare("DELETE FROM kit_meta")]);
  await LEADS.batch([
    LEADS.prepare("DELETE FROM leads"),
    LEADS.prepare("DELETE FROM kit_meta"),
    LEADS.prepare("INSERT INTO leads (id, email, created_ts, last_seen_ts, source) VALUES (?1, 'pat@example.com', ?2, ?2, 'gate')").bind(LEAD_ID, T0 - 86_400_000),
  ]);
});
afterEach(async () => {
  await MAIN.prepare("DELETE FROM leads").run();
  resetSeparationCache();
});

describe("authorizeSession: malformed floors fail closed", () => {
  const BAD: unknown[] = ["invalid", "", Number.NaN, -1, 1.5, Number.POSITIVE_INFINITY, 2 ** 60, true, {}];
  for (const bad of BAD) {
    it(`refuses a stored lead whose floor is ${String(typeof bad === "object" ? "object" : bad)}`, async () => {
      const row = { id: LEAD_ID, session_floor_ts: bad };
      expect(await authorizeSession(leadsAnswering(row), MAIN, [KEY], claims())).toBe("error");
    });
  }
  it("refuses an unstored unlock whose kit-wide floor is malformed", async () => {
    const lid = await mintUnstoredId(KEY);
    for (const bad of ["invalid", Number.NaN, -5, 0.5]) {
      expect(await authorizeSession(LEADS, fakeDb({ value: bad }), [KEY], claims({ lid }))).toBe("error");
    }
  });
  it("a malformed floor is refused where a comparison alone would open the session", async () => {
    // Without the shape check, NaN and a negative compare as "not newer" and the session would be ok.
    for (const bad of [Number.NaN, -1, "invalid"]) {
      expect(await authorizeSession(leadsAnswering({ id: LEAD_ID, session_floor_ts: bad }), MAIN, [KEY], claims())).toBe("error");
    }
    // Compatibility baseline: a null floor and a valid older floor stay ok.
    expect(await authorizeSession(LEADS, MAIN, [KEY], claims())).toBe("ok");
    await LEADS.prepare("UPDATE leads SET session_floor_ts = ? WHERE id = ?").bind(T0 - 120_000, LEAD_ID).run();
    expect(await authorizeSession(LEADS, MAIN, [KEY], claims())).toBe("ok");
  });
  it("answers error when the storage read throws", async () => {
    const lid = await mintUnstoredId(KEY);
    const boom = { prepare: () => { throw new Error("d1 down"); } } as unknown as D1Database;
    expect(await authorizeSession(leadsAnswering(new Error("d1 down")), MAIN, [KEY], claims())).toBe("error");
    expect(await authorizeSession(LEADS, boom, [KEY], claims({ lid }))).toBe("error");
  });
});

describe("authorizeSession: which database answers what (issue #772)", () => {
  it("reads the lead from the leads database only, and never touches the main database for it", async () => {
    const leads = recording(LEADS);
    const main = recording(MAIN);
    expect(await authorizeSession(leads.db, main.db, [KEY], claims())).toBe("ok");
    expect(leads.sql.some((q) => /FROM leads WHERE id/.test(q))).toBe(true);
    expect(main.sql.some((q) => /FROM leads WHERE id/.test(q))).toBe(false);
    expect(leads.sql.some((q) => /kit_meta/.test(q))).toBe(false);
  });
  it("reads the unstored floor from the main database only, and never touches the leads database for it", async () => {
    const lid = await mintUnstoredId(KEY);
    const leads = recording(LEADS);
    const main = recording(MAIN);
    expect(await authorizeSession(leads.db, main.db, [KEY], claims({ lid }))).toBe("ok");
    expect(main.sql.some((q) => /kit_meta/.test(q))).toBe(true);
    expect(leads.sql.some((q) => /kit_meta|FROM leads WHERE id/.test(q))).toBe(false);
  });
  it("a floor in the leads database does not apply to an unstored unlock, and a floor in the main database does", async () => {
    const lid = await mintUnstoredId(KEY);
    await LEADS.prepare("INSERT INTO kit_meta (key, value) VALUES (?, ?)").bind(UNSTORED_FLOOR_KEY, T0).run().catch(() => undefined);
    expect(await authorizeSession(LEADS, MAIN, [KEY], claims({ lid }))).toBe("ok");
    await MAIN.prepare("INSERT INTO kit_meta (key, value) VALUES (?, ?)").bind(UNSTORED_FLOOR_KEY, T0).run();
    expect(await authorizeSession(LEADS, MAIN, [KEY], claims({ lid }))).toBe("expired");
    await LEADS.prepare("DELETE FROM kit_meta WHERE key = ?").bind(UNSTORED_FLOOR_KEY).run().catch(() => undefined);
  });
  it("denies, without any lead read, when the leads database is the main database", async () => {
    const main = recording(MAIN);
    expect(await authorizeSession(main.db, MAIN, [KEY], claims())).toBe("error");
    expect(main.sql.some((q) => /FROM leads WHERE id/.test(q))).toBe(false);
  });
  it("denies when the leads binding is missing or is not a database", async () => {
    expect(await authorizeSession(undefined as unknown as D1Database, MAIN, [KEY], claims())).toBe("error");
    expect(await authorizeSession({} as unknown as D1Database, MAIN, [KEY], claims())).toBe("error");
  });
  it("denies, without any lead read, when the main database still holds lead rows", async () => {
    await MAIN.prepare("INSERT INTO leads (id, email, created_ts, last_seen_ts, source) VALUES (?, 'old@example.com', 1, 1, 'gate')").bind("c".repeat(32)).run();
    const leads = recording(LEADS);
    expect(await authorizeSession(leads.db, MAIN, [KEY], claims())).toBe("error");
    expect(leads.sql.some((q) => /FROM leads WHERE id/.test(q))).toBe(false);
  });
  it("denies a session for a lead that exists only in the wrong database", async () => {
    const WRONG = "d".repeat(32);
    await LEADS.prepare("DELETE FROM leads WHERE id = ?").bind(WRONG).run();
    await MAIN.prepare("INSERT INTO leads (id, email, created_ts, last_seen_ts, source) VALUES (?, 'wrong@example.com', 1, 1, 'gate')").bind(WRONG).run();
    // Never "ok": the lead is not in the leads database, and the main database holding it fails the proof.
    expect(await authorizeSession(LEADS, MAIN, [KEY], claims({ lid: WRONG }))).not.toBe("ok");
    await MAIN.prepare("DELETE FROM leads").run();
    resetSeparationCache();
    // With the main database clean again the same id is simply unknown: expired.
    expect(await authorizeSession(LEADS, MAIN, [KEY], claims({ lid: WRONG }))).toBe("expired");
  });
  it("denies when the two databases are passed the wrong way round", async () => {
    expect(await authorizeSession(MAIN, LEADS, [KEY], claims())).toBe("error");
  });
});

describe("migration 0021: negative kit-wide floor", () => {
  const mig = () => testEnv.TEST_MIGRATIONS.find((m) => m.name.includes("0021"))!;
  async function dropTriggers() {
    await testEnv.DB.prepare("DROP TRIGGER IF EXISTS kit_meta_floor_insert").run();
    await testEnv.DB.prepare("DROP TRIGGER IF EXISTS kit_meta_floor_update").run();
  }
  async function reapply() {
    await dropTriggers();
    for (const q of mig().queries) await testEnv.DB.prepare(q).run();
  }
  const floor = () => testEnv.DB.prepare("SELECT value FROM kit_meta WHERE key = ?").bind(UNSTORED_FLOOR_KEY).first<{ value: number }>();

  it("refuses a negative insert and a negative update", async () => {
    await expect(testEnv.DB.prepare("INSERT INTO kit_meta (key, value) VALUES (?, -1)").bind(UNSTORED_FLOOR_KEY).run()).rejects.toThrow();
    await testEnv.DB.prepare("INSERT INTO kit_meta (key, value) VALUES (?, 5)").bind(UNSTORED_FLOOR_KEY).run();
    await expect(testEnv.DB.prepare("UPDATE kit_meta SET value = -1 WHERE key = ?").bind(UNSTORED_FLOOR_KEY).run()).rejects.toThrow();
  });
  it("refuses renaming a negative row's key into the floor key", async () => {
    await testEnv.DB.prepare("INSERT INTO kit_meta (key, value) VALUES ('last_purge_ts', -1)").run();
    await expect(testEnv.DB.prepare("UPDATE kit_meta SET key = ? WHERE key = 'last_purge_ts'").bind(UNSTORED_FLOOR_KEY).run()).rejects.toThrow();
  });
  it("leaves other heartbeat keys alone", async () => {
    await testEnv.DB.prepare("INSERT INTO kit_meta (key, value) VALUES ('last_purge_ts', -1)").run();
  });
  it("repairs a negative floor that exists before the migration, and the session is then judged by it", async () => {
    await dropTriggers();
    await testEnv.DB.prepare("INSERT INTO kit_meta (key, value) VALUES (?, -1)").bind(UNSTORED_FLOOR_KEY).run();
    const lid = await mintUnstoredId(KEY);
    expect(await authorizeSession(LEADS, MAIN, [KEY], claims({ lid }))).toBe("error"); // the pre-migration outage
    const before = Date.now();
    await reapply();
    const v = (await floor())!.value;
    expect(v).toBeGreaterThanOrEqual(Math.floor(before / 1000) * 1000);
    expect(v).toBeLessThanOrEqual(Date.now());
    // An unlock issued before the migration is revoked, not an error; a later one is accepted.
    const nowSec = Math.floor(Date.now() / 1000);
    expect(await authorizeSession(LEADS, MAIN, [KEY], claims({ lid, iat: nowSec - 60 }))).toBe("expired");
    expect(await authorizeSession(LEADS, MAIN, [KEY], claims({ lid, iat: nowSec + 5 }))).toBe("ok");
  });
  async function triggerNames() {
    const r = await testEnv.DB.prepare("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'kit_meta_floor_%' ORDER BY name").all<{ name: string }>();
    return r.results.map((x) => x.name);
  }
  it("applies twice in a row without dropping anything", async () => {
    for (const q of mig().queries) await testEnv.DB.prepare(q).run();
    for (const q of mig().queries) await testEnv.DB.prepare(q).run();
    expect(await triggerNames()).toEqual(["kit_meta_floor_insert", "kit_meta_floor_update"]);
  });
  it("a re-run converges after a partial application stopped after each statement", async () => {
    const queries = mig().queries;
    expect(queries.length).toBe(3);
    for (let k = 0; k <= queries.length; k++) {
      await dropTriggers();
      await testEnv.DB.prepare("DELETE FROM kit_meta WHERE key = ?").bind(UNSTORED_FLOOR_KEY).run();
      await testEnv.DB.prepare("INSERT INTO kit_meta (key, value) VALUES (?, -1)").bind(UNSTORED_FLOOR_KEY).run();
      for (const q of queries.slice(0, k)) await testEnv.DB.prepare(q).run();
      for (const q of queries) await testEnv.DB.prepare(q).run(); // the retry
      expect(await triggerNames(), `after ${k} statements`).toEqual(["kit_meta_floor_insert", "kit_meta_floor_update"]);
      expect((await floor())!.value, `after ${k} statements`).toBeGreaterThanOrEqual(0);
      await expect(testEnv.DB.prepare("UPDATE kit_meta SET value = -1 WHERE key = ?").bind(UNSTORED_FLOOR_KEY).run()).rejects.toThrow();
    }
  });
  it("leaves a valid existing floor unchanged", async () => {
    await dropTriggers();
    await testEnv.DB.prepare("INSERT INTO kit_meta (key, value) VALUES (?, 12345)").bind(UNSTORED_FLOOR_KEY).run();
    await reapply();
    expect((await floor())!.value).toBe(12345);
  });
});

describe("migration 0022: the floor guard is recreated with its exact bodies", () => {
  const mig = () => testEnv.TEST_MIGRATIONS.find((m) => m.name.includes("0022"))!;
  const run = async () => { for (const q of mig().queries) await testEnv.DB.prepare(q).run(); };
  const guard = async () => {
    const r = await testEnv.DB.prepare("SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'kit_meta_floor_%' ORDER BY name").all<{ name: string; sql: string }>();
    return r.results.map((x) => [x.name, x.sql.replace(/\s+/g, " ")]);
  };
  const EXACT = [
    ["kit_meta_floor_insert", "CREATE TRIGGER kit_meta_floor_insert BEFORE INSERT ON kit_meta WHEN NEW.key = 'unstored_floor_ts' AND NEW.value < 0 BEGIN SELECT RAISE(ABORT, 'unstored_floor_ts must be >= 0'); END"],
    ["kit_meta_floor_update", "CREATE TRIGGER kit_meta_floor_update BEFORE UPDATE ON kit_meta WHEN NEW.key = 'unstored_floor_ts' AND NEW.value < 0 BEGIN SELECT RAISE(ABORT, 'unstored_floor_ts must be >= 0'); END"],
  ];
  const floorKey = UNSTORED_FLOOR_KEY;

  it("replaces same-named triggers that have a different body", async () => {
    await testEnv.DB.prepare("DROP TRIGGER kit_meta_floor_insert").run();
    await testEnv.DB.prepare("DROP TRIGGER kit_meta_floor_update").run();
    await testEnv.DB.prepare("CREATE TRIGGER kit_meta_floor_insert BEFORE INSERT ON kit_meta WHEN 0 BEGIN SELECT 1; END").run();
    await testEnv.DB.prepare("CREATE TRIGGER kit_meta_floor_update BEFORE UPDATE ON kit_meta WHEN 0 BEGIN SELECT 1; END").run();
    await testEnv.DB.prepare("INSERT INTO kit_meta (key, value) VALUES (?, -1)").bind(floorKey).run(); // weak guard allows it
    await run();
    expect(await guard()).toEqual(EXACT);
    expect((await testEnv.DB.prepare("SELECT value FROM kit_meta WHERE key = ?").bind(floorKey).first<{ value: number }>())!.value).toBeGreaterThanOrEqual(0);
    await expect(testEnv.DB.prepare("UPDATE kit_meta SET value = -1 WHERE key = ?").bind(floorKey).run()).rejects.toThrow();
  });
  it("works when the triggers do not exist yet", async () => {
    await testEnv.DB.prepare("DROP TRIGGER kit_meta_floor_insert").run();
    await testEnv.DB.prepare("DROP TRIGGER kit_meta_floor_update").run();
    await run();
    expect(await guard()).toEqual(EXACT);
  });
  it("creates the guard before the repair", () => {
    const q = mig().queries.map((s) => s.replace(/--[^\n]*/g, "").replace(/\s+/g, " ").trim()); // the first statement carries the file header comments
    const at = (re: RegExp) => q.findIndex((s) => re.test(s));
    const repair = at(/^UPDATE kit_meta SET/);
    expect(repair).toBeGreaterThan(-1);
    for (const n of ["kit_meta_floor_insert", "kit_meta_floor_update"]) {
      const drop = at(new RegExp(`^DROP TRIGGER IF EXISTS ${n}`));
      const create = at(new RegExp(`^CREATE TRIGGER ${n}`));
      expect(drop, `${n} drop exists`).toBeGreaterThan(-1);
      expect(create, `${n} create comes after its drop`).toBeGreaterThan(drop);
      expect(create, `${n} create comes before the repair`).toBeLessThan(repair);
    }
  });
});
