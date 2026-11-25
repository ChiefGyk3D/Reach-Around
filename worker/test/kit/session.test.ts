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
  it("still accepts a null floor and a valid older floor", async () => {
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

describe("schema refuses a malformed floor at write time", () => {
  it("rejects text, a fraction and a negative in leads.session_floor_ts", async () => {
    for (const bad of ["invalid", 1.5, -1]) {
      await expect(testEnv.DB.prepare("UPDATE leads SET session_floor_ts = ? WHERE id = ?").bind(bad, LEAD_ID).run()).rejects.toThrow();
    }
  });
  it("rejects text and a negative for the kit-wide floor, on insert and on update", async () => {
    for (const bad of ["invalid", -1]) {
      await expect(testEnv.DB.prepare("INSERT INTO kit_meta (key, value) VALUES (?, ?)").bind(UNSTORED_FLOOR_KEY, bad).run()).rejects.toThrow();
    }
    await testEnv.DB.prepare("INSERT INTO kit_meta (key, value) VALUES (?, ?)").bind(UNSTORED_FLOOR_KEY, 5).run();
    await expect(testEnv.DB.prepare("UPDATE kit_meta SET value = -1 WHERE key = ?").bind(UNSTORED_FLOOR_KEY).run()).rejects.toThrow();
    // Other heartbeat keys are untouched by the guard.
    await testEnv.DB.prepare("INSERT INTO kit_meta (key, value) VALUES ('last_purge_ts', -1)").run();
  });
});
