// SPDX-License-Identifier: Apache-2.0
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import { UNSTORED_FLOOR_KEY } from "../../src/kit/leads";
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

beforeEach(async () => {
  await testEnv.DB.batch([
    testEnv.DB.prepare("DELETE FROM leads"),
    testEnv.DB.prepare("DELETE FROM kit_meta"),
    testEnv.DB.prepare("INSERT INTO leads (id, email, created_ts, last_seen_ts, source) VALUES (?1, 'pat@example.com', ?2, ?2, 'gate')").bind(LEAD_ID, T0 - 86_400_000),
  ]);
});

describe("authorizeSession: malformed floors fail closed", () => {
  const BAD: unknown[] = ["invalid", "", Number.NaN, -1, 1.5, Number.POSITIVE_INFINITY, 2 ** 60, true, {}];
  for (const bad of BAD) {
    it(`refuses a stored lead whose floor is ${String(typeof bad === "object" ? "object" : bad)}`, async () => {
      const row = { id: LEAD_ID, session_floor_ts: bad };
      expect(await authorizeSession(fakeDb(row), [KEY], claims())).toBe("error");
    });
  }
  it("refuses an unstored unlock whose kit-wide floor is malformed", async () => {
    const lid = await mintUnstoredId(KEY);
    for (const bad of ["invalid", Number.NaN, -5, 0.5]) {
      expect(await authorizeSession(fakeDb({ value: bad }), [KEY], claims({ lid }))).toBe("error");
    }
  });
  it("a malformed floor is refused where a comparison alone would open the session", async () => {
    // Without the shape check, NaN and a negative compare as "not newer" and the session would be ok.
    for (const bad of [Number.NaN, -1, "invalid"]) {
      expect(await authorizeSession(fakeDb({ id: LEAD_ID, session_floor_ts: bad }), [KEY], claims())).toBe("error");
    }
    // Compatibility baseline: a null floor and a valid older floor stay ok.
    expect(await authorizeSession(testEnv.DB, [KEY], claims())).toBe("ok");
    await testEnv.DB.prepare("UPDATE leads SET session_floor_ts = ? WHERE id = ?").bind(T0 - 120_000, LEAD_ID).run();
    expect(await authorizeSession(testEnv.DB, [KEY], claims())).toBe("ok");
  });
  it("answers error when the storage read throws", async () => {
    const boom = { prepare: () => { throw new Error("d1 down"); } } as unknown as D1Database;
    expect(await authorizeSession(boom, [KEY], claims())).toBe("error");
    const lid = await mintUnstoredId(KEY);
    expect(await authorizeSession(boom, [KEY], claims({ lid }))).toBe("error");
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
    expect(await authorizeSession(testEnv.DB, [KEY], claims({ lid }))).toBe("error"); // the pre-migration outage
    const before = Date.now();
    await reapply();
    const v = (await floor())!.value;
    expect(v).toBeGreaterThanOrEqual(Math.floor(before / 1000) * 1000);
    expect(v).toBeLessThanOrEqual(Date.now());
    // An unlock issued before the migration is revoked, not an error; a later one is accepted.
    const nowSec = Math.floor(Date.now() / 1000);
    expect(await authorizeSession(testEnv.DB, [KEY], claims({ lid, iat: nowSec - 60 }))).toBe("expired");
    expect(await authorizeSession(testEnv.DB, [KEY], claims({ lid, iat: nowSec + 5 }))).toBe("ok");
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
