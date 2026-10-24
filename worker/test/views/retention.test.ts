// SPDX-License-Identifier: Apache-2.0
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import worker from "../../src/index";
import { DEFAULT_RAW_KEEP_DAYS, MAX_RAW_KEEP_DAYS, purgeViews } from "../../src/viewretention";
import { DB, budgets, clearAll, fakeLimiter, rows, seen } from "./helpers";

const DAY = 86_400_000;
const HOUR = 3_600_000;
const NOW = Date.parse("2027-03-14T00:05:00Z");

async function addEvent(ts: number) {
  await DB.prepare(
    "INSERT INTO view_events (ts, page, src_kind, bot_class, reason) VALUES (?, 'home', 'd', 'h', 'none')",
  )
    .bind(ts)
    .run();
}
async function addMeta(key: string) {
  await DB.prepare("INSERT INTO view_meta (key, value) VALUES (?, '1')").bind(key).run();
}
async function addSeen(day: string, h: string) {
  await DB.prepare("INSERT INTO visitor_seen (day, h) VALUES (?, ?)").bind(day, h).run();
}

const env = () => ({ DB, SITE_HOST: "x.example.test", LINK_IDS: "[]", CLICK_LIMITER: fakeLimiter() });

beforeEach(clearAll);

describe("view retention", () => {
  it("visitor_seen_before_today_utc_is_deleted_and_today_survives", async () => {
    await addSeen("2027-03-13", "aaaaaaaa");
    await addSeen("2027-03-14", "bbbbbbbb");
    await purgeViews(env(), NOW);
    expect((await seen()).map((s) => s.day)).toEqual(["2027-03-14"]);
  });

  it("raw_rows_older_than_the_keep_window_are_deleted_and_newer_survive", async () => {
    expect(DEFAULT_RAW_KEEP_DAYS).toBe(7);
    await addEvent(NOW - 8 * DAY);
    await addEvent(NOW - 6 * DAY);
    await addEvent(NOW);
    await purgeViews(env(), NOW);
    expect((await rows()).map((r) => r.ts)).toEqual([NOW - 6 * DAY, NOW]);
  });

  it("raw_keep_days_override_is_honored_and_capped", async () => {
    await addEvent(NOW - 2 * DAY);
    await purgeViews({ ...env(), RAW_KEEP_DAYS: "1" }, NOW);
    expect(await rows()).toEqual([]);

    await addEvent(NOW - 100 * DAY);
    await addEvent(NOW - 80 * DAY);
    // A huge setting is clamped to the ceiling, never "keep forever".
    await purgeViews({ ...env(), RAW_KEEP_DAYS: "100000" }, NOW);
    expect(MAX_RAW_KEEP_DAYS).toBe(90);
    expect((await rows()).map((r) => r.ts)).toEqual([NOW - 80 * DAY]);
  });

  it("junk_keep_days_falls_back_to_the_default", async () => {
    await addEvent(NOW - 8 * DAY);
    for (const bad of ["0", "-1", "abc", "", "1e9", "NaN"]) {
      await purgeViews({ ...env(), RAW_KEEP_DAYS: bad }, NOW);
    }
    expect(await rows()).toEqual([]);
    await addEvent(NOW - 6 * DAY);
    await purgeViews({ ...env(), RAW_KEEP_DAYS: "0" }, NOW);
    expect((await rows()).length).toBe(1); // zero did not wipe everything
  });

  it("old_budget_and_client_cap_keys_go_and_current_ones_stay", async () => {
    const hour = Math.floor(NOW / HOUR);
    await addMeta(`bud:pv:${hour - 49}`);
    await addMeta(`bud:pvbot:${hour - 60}`);
    await addMeta(`bud:pv:${hour - 10}`);
    await addMeta(`bud:pv:${hour}`);
    await addMeta(`cap:abcd1234:${hour - 2}`);
    await addMeta(`cap:abcd1234:${hour}`);
    await addMeta("rolled_through"); // unrelated state is never touched
    await purgeViews(env(), NOW);
    expect((await budgets()).map((b) => b.key).sort()).toEqual(
      [`bud:pv:${hour - 10}`, `bud:pv:${hour}`, `cap:abcd1234:${hour}`, "rolled_through"].sort(),
    );
  });

  it("deletes_in_bounded_batches_and_clears_a_large_backlog", async () => {
    const stmts = [];
    for (let i = 0; i < 2500; i++) {
      stmts.push(
        DB.prepare(
          "INSERT INTO view_events (ts, page, src_kind, bot_class, reason) VALUES (?, 'home', 'd', 'h', 'none')",
        ).bind(NOW - 30 * DAY - i),
      );
    }
    await DB.batch(stmts);
    await purgeViews(env(), NOW);
    expect(await rows()).toEqual([]);
  });

  it("scheduled_runs_all_three_kinds_and_the_click_purge", async () => {
    await addEvent(NOW - 8 * DAY);
    await addEvent(NOW);
    await DB.prepare("DELETE FROM click_events").run();
    await DB.prepare("INSERT INTO click_events (ts, link_id) VALUES (?, 'example-one')")
      .bind(Date.now() - 200 * DAY)
      .run();
    const pending: Promise<unknown>[] = [];
    const ctx = { waitUntil: (p: Promise<unknown>) => void pending.push(p) } as unknown as ExecutionContext;
    // The handler uses the real clock; drive it with rows relative to it.
    await DB.prepare("DELETE FROM view_events").run();
    const now = Date.now();
    await addEvent(now - 8 * DAY);
    await addEvent(now);
    await addSeen(new Date(now - DAY).toISOString().slice(0, 10), "aaaaaaaa");
    await addSeen(new Date(now).toISOString().slice(0, 10), "bbbbbbbb");
    await addMeta(`bud:pv:${Math.floor(now / HOUR) - 100}`);
    await addMeta(`bud:pv:${Math.floor(now / HOUR)}`);
    await worker.scheduled({} as ScheduledController, env(), ctx);
    await Promise.all(pending);
    expect((await rows()).map((r) => r.ts)).toEqual([now]);
    expect((await seen()).map((x) => x.h)).toEqual(["bbbbbbbb"]);
    expect((await budgets()).map((b) => b.key)).toEqual([`bud:pv:${Math.floor(now / HOUR)}`]);
    const clicks = await DB.prepare("SELECT COUNT(*) AS n FROM click_events").first<{ n: number }>();
    expect(clicks?.n).toBe(0);
  });

  it("click_purge_still_runs_when_the_view_purge_fails_and_nothing_throws", async () => {
    await DB.prepare("DELETE FROM click_events").run();
    await DB.prepare("INSERT INTO click_events (ts, link_id) VALUES (?, 'example-one')")
      .bind(Date.now() - 200 * DAY)
      .run();
    // Break only the view tables: dropping would leak across tests, so fail
    // on the statement text instead.
    const failing = new Proxy(DB, {
      get(t, p) {
        if (p === "prepare") {
          return (q: string) => {
            if (/view_|visitor_seen/.test(q)) throw new Error("view tables unavailable");
            return t.prepare(q);
          };
        }
        const v = Reflect.get(t, p, t);
        return typeof v === "function" ? v.bind(t) : v;
      },
    });
    const pending: Promise<unknown>[] = [];
    const ctx = { waitUntil: (p: Promise<unknown>) => void pending.push(p) } as unknown as ExecutionContext;
    await worker.scheduled({} as ScheduledController, { ...env(), DB: failing as D1Database }, ctx);
    await expect(Promise.all(pending)).resolves.toBeDefined();
    const clicks = await DB.prepare("SELECT COUNT(*) AS n FROM click_events").first<{ n: number }>();
    expect(clicks?.n).toBe(0);
  });

  it("view_purge_still_runs_when_the_click_purge_fails", async () => {
    const now = Date.now();
    await addEvent(now - 8 * DAY);
    const failing = new Proxy(DB, {
      get(t, p) {
        if (p === "prepare") {
          return (q: string) => {
            if (/click_events/.test(q)) throw new Error("click table unavailable");
            return t.prepare(q);
          };
        }
        const v = Reflect.get(t, p, t);
        return typeof v === "function" ? v.bind(t) : v;
      },
    });
    const pending: Promise<unknown>[] = [];
    const ctx = { waitUntil: (p: Promise<unknown>) => void pending.push(p) } as unknown as ExecutionContext;
    await worker.scheduled({} as ScheduledController, { ...env(), DB: failing as D1Database }, ctx);
    await Promise.all(pending);
    expect(await rows()).toEqual([]);
  });
});

void testEnv;
