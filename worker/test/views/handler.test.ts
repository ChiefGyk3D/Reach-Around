// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import worker from "../../src/index";
import {
  BODY_DEADLINE_MS,
  DEFAULT_CLIENT_VIEWS_PER_HOUR,
  MAX_CLIENT_VIEWS_PER_HOUR,
  clientLimit,
  handleView,
} from "../../src/view";
import {
  DB,
  IP4,
  IP6,
  NOW,
  SECRET_2,
  SITE,
  beacon,
  budgets,
  clearAll,
  countingStream,
  fakeLimiter,
  dumpDatabase,
  makeEnv,
  rows,
  seen,
  spyDb,
} from "./helpers";

const fetchSpy = vi.fn(() => {
  throw new Error("fetch must not be called in the request path");
});

beforeEach(async () => {
  await clearAll();
  fetchSpy.mockClear();
  vi.stubGlobal("fetch", fetchSpy);
});

afterEach(() => {
  expect(fetchSpy).not.toHaveBeenCalled(); // no_fetch_in_the_request_path
  vi.unstubAllGlobals();
});

function headerList(res: Response): string {
  return JSON.stringify([...res.headers.entries()].sort());
}

type Case = { name: string; req: () => Request; env?: () => ReturnType<typeof makeEnv>; writes: boolean };

const long = "x".repeat(300);
const CASES: Case[] = [
  { name: "valid", req: () => beacon(), writes: true },
  { name: "valid empty body is a direct view", req: () => beacon({ body: "" }), writes: true },
  { name: "GET", req: () => beacon({ method: "GET" }), writes: false },
  { name: "PUT", req: () => beacon({ method: "PUT" }), writes: false },
  { name: "DELETE", req: () => beacon({ method: "DELETE" }), writes: false },
  { name: "OPTIONS", req: () => beacon({ method: "OPTIONS" }), writes: false },
  { name: "HEAD", req: () => beacon({ method: "HEAD" }), writes: false },
  { name: "unknown page", req: () => beacon({ path: "/e/v/nope" }), writes: false },
  { name: "uppercase page", req: () => beacon({ path: "/e/v/HOME" }), writes: false },
  { name: "encoded slash page", req: () => beacon({ path: "/e/v/ho%2Fme" }), writes: false },
  { name: "encoded dots", req: () => beacon({ path: "/e/v/%2e%2e" }), writes: false },
  { name: "trailing slash", req: () => beacon({ path: "/e/v/home/" }), writes: false },
  { name: "no id", req: () => beacon({ path: "/e/v/" }), writes: false },
  { name: "click route path", req: () => beacon({ path: "/e/c/home" }), writes: false },
  { name: "129 char path", req: () => beacon({ path: `/e/v/${"a".repeat(124)}` }), writes: false },
  { name: "1 MB query", req: () => beacon({ path: `/e/v/home?${"q".repeat(1_000_000)}` }), writes: false },
  { name: "GPC", req: () => beacon({ headers: { "Sec-GPC": "1" } }), writes: false },
  { name: "DNT", req: () => beacon({ headers: { DNT: "1" } }), writes: false },
  { name: "no origin", req: () => beacon({ headers: { Origin: null } }), writes: false },
  { name: "foreign origin", req: () => beacon({ headers: { Origin: "https://evil.example.test" } }), writes: false },
  { name: "300 char origin", req: () => beacon({ headers: { Origin: `https://${long}.example.test` } }), writes: false },
  { name: "missing length", req: () => beacon({ noLength: true }), writes: false },
  { name: "length 257", req: () => beacon({ headers: { "Content-Length": "257" } }), writes: false },
  { name: "length not digits", req: () => beacon({ headers: { "Content-Length": "12abc" } }), writes: false },
  { name: "transfer-encoding", req: () => beacon({ headers: { "Transfer-Encoding": "chunked" } }), writes: false },
  { name: "IP literal referrer", req: () => beacon({ body: "r=192.0.2.1" }), writes: false },
  { name: "URL referrer", req: () => beacon({ body: "r=https%3A%2F%2Fnews.example.test%2Fa%3Fb%3Dc" }), writes: false },
  { name: "email utm", req: () => beacon({ body: "u=a%40b.example.test" }), writes: false },
  { name: "bad size", req: () => beacon({ body: "w=999" }), writes: false },
  { name: "body over cap with honest length", req: () => beacon({ body: `r=${"a".repeat(300)}.example.test` }), writes: false },
  {
    name: "limited",
    req: () => beacon(),
    env: () => makeEnv({ VIEW_LIMITER: fakeLimiter(0) }),
    writes: false,
  },
  { name: "killed", req: () => beacon(), env: () => makeEnv({ VIEWS_KILL: "1" }), writes: false },
  { name: "no limiter bound", req: () => beacon(), env: () => makeEnv({ VIEW_LIMITER: undefined }), writes: false },
  { name: "no secret (fails closed)", req: () => beacon(), env: () => makeEnv({ VIEW_HASH_SECRET: undefined }), writes: false },
  { name: "short secret (fails closed)", req: () => beacon(), env: () => makeEnv({ VIEW_HASH_SECRET: btoa("short") }), writes: false },
  { name: "malformed secret (fails closed)", req: () => beacon(), env: () => makeEnv({ VIEW_HASH_SECRET: "%%%" }), writes: false },
  { name: "no client address (fails closed)", req: () => beacon({ headers: { "CF-Connecting-IP": null } }), writes: false },
  { name: "unparsable client address (fails closed)", req: () => beacon({ headers: { "CF-Connecting-IP": "not-an-ip" } }), writes: false },
  { name: "no page ids", req: () => beacon(), env: () => makeEnv({ PAGE_IDS: undefined }), writes: false },
  { name: "bad page ids", req: () => beacon(), env: () => makeEnv({ PAGE_IDS: "{not json" }), writes: false },
];

describe("uniformity", () => {
  it("every_outcome_is_the_same_empty_204_with_the_same_headers", async () => {
    expect(CASES.length).toBeGreaterThanOrEqual(20);
    const ref = await handleView(beacon({ method: "GET" }), makeEnv(), NOW);
    const refHeaders = headerList(ref);
    let writers = 0;
    for (const c of CASES) {
      const before = (await rows()).length;
      const res = await handleView(c.req(), c.env ? c.env() : makeEnv(), NOW);
      expect(res.status, c.name).toBe(204);
      expect(await res.text(), c.name).toBe("");
      expect(headerList(res), c.name).toBe(refHeaders);
      expect(res.headers.get("location"), c.name).toBeNull();
      const after = (await rows()).length;
      expect(after - before, c.name).toBe(c.writes ? 1 : 0);
      if (c.writes) writers += 1;
    }
    expect(writers).toBe(2);
  });

  it("requests_refused_before_the_budget_make_zero_db_statements", async () => {
    for (const c of CASES.filter((x) => !x.writes)) {
      const spy = spyDb();
      const base = c.env ? c.env() : makeEnv();
      await handleView(c.req(), { ...base, DB: spy.DB }, NOW);
      expect(spy.calls.prepare, c.name).toBe(0);
    }
  });

  it("router_sends_view_paths_to_the_view_route_and_leaves_clicks_alone", async () => {
    const env = { ...makeEnv(), LINK_IDS: '["example-one"]', CLICK_LIMITER: fakeLimiter() };
    const res = await worker.fetch(beacon(), env);
    expect(res.status).toBe(204);
    expect((await rows()).length).toBe(1);
    // A click path never records a view and a view path never records a click.
    await worker.fetch(beacon({ path: "/e/c/example-one" }), env);
    expect((await rows()).length).toBe(1);
  });
});

describe("kill switch", () => {
  it("kill_switch_makes_zero_io", async () => {
    const spy = spyDb();
    const limiter = fakeLimiter();
    const { stream, state } = countingStream(10);
    const env = { ...makeEnv({ VIEWS_KILL: "1", VIEW_LIMITER: limiter }), DB: spy.DB };
    const res = await handleView(beacon({ stream }), env, NOW);
    expect(res.status).toBe(204);
    expect(spy.calls.prepare).toBe(0);
    expect(limiter.calls).toBe(0);
    expect(state.pulls).toBe(0);
    expect(state.cancelled).toBe(true);
  });

  it("kill_switch_is_read_on_every_request", async () => {
    const env = makeEnv({ VIEWS_KILL: "TRUE" });
    await handleView(beacon(), env, NOW);
    expect((await rows()).length).toBe(0);
    env.VIEWS_KILL = "0";
    await handleView(beacon(), env, NOW);
    expect((await rows()).length).toBe(1);
    env.VIEWS_KILL = "true";
    await handleView(beacon(), env, NOW);
    expect((await rows()).length).toBe(1);
  });
});

describe("privacy signals and caps", () => {
  it("gpc_and_dnt_record_nothing", async () => {
    const limiter = fakeLimiter();
    const spy = spyDb();
    const env = { ...makeEnv({ VIEW_LIMITER: limiter }), DB: spy.DB };
    await handleView(beacon({ headers: { "Sec-GPC": "1" } }), env, NOW);
    await handleView(beacon({ headers: { DNT: "1" } }), env, NOW);
    expect((await rows()).length).toBe(0);
    expect(spy.calls.prepare).toBe(0);
    expect(limiter.calls).toBe(0);
    // Explicit opt-outs only: 0 still records.
    await handleView(beacon({ headers: { "Sec-GPC": "0", DNT: "0" } }), makeEnv(), NOW);
    expect((await rows()).length).toBe(1);
  });

  it("gpc_visitors_leave_no_trace_end_to_end", async () => {
    await handleView(beacon({ headers: { "Sec-GPC": "1" } }), makeEnv(), NOW);
    expect(await rows()).toEqual([]);
    expect(await seen()).toEqual([]);
    expect(await budgets()).toEqual([]);
  });

  it("missing_or_foreign_origin_is_dropped_not_bucketed", async () => {
    await handleView(beacon({ headers: { Origin: null } }), makeEnv(), NOW);
    await handleView(beacon({ headers: { Origin: "https://evil.example.test" } }), makeEnv(), NOW);
    expect(await rows()).toEqual([]);
    expect(await budgets()).toEqual([]);
  });

  it("ping_from_is_accepted_like_origin", async () => {
    await handleView(
      beacon({ headers: { Origin: null, "Ping-From": `https://${SITE}/` } }),
      makeEnv(),
      NOW,
    );
    expect((await rows()).length).toBe(1);
  });

  it("path_longer_than_128_is_refused_before_decode", async () => {
    const dec = vi.spyOn(globalThis, "decodeURIComponent");
    await handleView(beacon({ path: `/e/v/${"a".repeat(124)}` }), makeEnv(), NOW);
    await handleView(beacon({ path: "/e/v/ho%2Fme" }), makeEnv(), NOW);
    expect(dec).toHaveBeenCalledTimes(0);
    dec.mockRestore();
  });

  it("content_length_missing_or_above_256_records_nothing_and_the_stream_is_not_pulled", async () => {
    const variants: Array<Record<string, string | null>> = [{}, { "Content-Length": "257" }, { "Content-Length": "9999999" }];
    for (const headers of variants) {
      const { stream, state } = countingStream(5000);
      await handleView(beacon({ stream, headers }), makeEnv(), NOW);
      expect(state.pulls).toBe(0);
      expect(state.cancelled).toBe(true);
    }
    expect(await rows()).toEqual([]);
  });

  it("a_lying_short_content_length_cannot_make_the_handler_read_past_the_cap", async () => {
    const { stream, state } = countingStream(100_000);
    await handleView(beacon({ stream, headers: { "Content-Length": "10" } }), makeEnv(), NOW);
    expect(await rows()).toEqual([]);
    // The one chunk was seen, counted, and the read aborted.
    expect(state.pulls).toBeLessThanOrEqual(1);
    expect(state.cancelled).toBe(true);
  });

  it("limiter_burst_is_dropped", async () => {
    const env = makeEnv({ VIEW_LIMITER: fakeLimiter(2) });
    for (let i = 0; i < 5; i++) await handleView(beacon(), env, NOW);
    expect((await rows()).length).toBe(2);
  });

  it("refused_requests_spend_no_limiter_budget", async () => {
    const limiter = fakeLimiter(1);
    const env = makeEnv({ VIEW_LIMITER: limiter });
    await handleView(beacon({ method: "GET" }), env, NOW);
    await handleView(beacon({ headers: { Origin: null } }), env, NOW);
    await handleView(beacon({ path: "/e/v/nope" }), env, NOW);
    expect(limiter.calls).toBe(0);
    await handleView(beacon(), env, NOW);
    expect((await rows()).length).toBe(1);
  });
});

describe("bots and budgets", () => {
  const CURL = { "User-Agent": "curl/8.5.0" };

  const from = (n: number, extra: Record<string, string | null> = {}) => ({
    ...extra,
    "CF-Connecting-IP": `203.0.113.${n}`,
  });

  it("budget_exhaustion_records_nothing_and_a_human_still_gets_through", async () => {
    const env = makeEnv({ BOT_VIEWS_PER_HOUR: "3" });
    for (let i = 1; i <= 5; i++) await handleView(beacon({ headers: from(i, CURL) }), env, NOW);
    expect((await rows()).filter((r) => r.bot_class === "a").length).toBe(3);
    await handleView(beacon({ headers: from(9) }), env, NOW);
    const all = await rows();
    expect(all.length).toBe(4);
    expect(all.filter((r) => r.bot_class === "h").length).toBe(1);
  });

  it("human_budget_is_enforced_per_hour_and_resets_next_hour", async () => {
    const env = makeEnv({ VIEWS_PER_HOUR: "2" });
    for (let i = 1; i <= 4; i++) await handleView(beacon({ headers: from(i) }), env, NOW);
    expect((await rows()).length).toBe(2);
    await handleView(beacon({ headers: from(5) }), env, NOW + 3_600_000);
    expect((await rows()).length).toBe(3);
  });

  it("zero_budget_stores_nothing", async () => {
    await handleView(beacon(), makeEnv({ VIEWS_PER_HOUR: "0" }), NOW);
    expect(await rows()).toEqual([]);
  });

  it("bot_flood_does_not_move_human_counts", async () => {
    const env = makeEnv({ BOT_VIEWS_PER_HOUR: "30" });
    for (let i = 0; i < 300; i++) {
      await handleView(beacon({ headers: { ...CURL, "CF-Connecting-IP": `203.0.113.${(i % 60) + 1}` } }), env, NOW);
    }
    for (let i = 0; i < 5; i++) {
      await handleView(beacon({ headers: { "CF-Connecting-IP": `198.51.100.${i + 1}` } }), env, NOW);
    }
    const all = await rows();
    expect(all.filter((r) => r.bot_class === "h").length).toBe(5);
    expect(all.filter((r) => r.bot_class !== "h").length).toBe(30);
  }, 60_000);

  it("bot_rows_land_in_the_bot_bucket_with_a_reason_and_no_visitor_hash", async () => {
    const cases: Array<[Record<string, string | null>, string, string]> = [
      [{ "User-Agent": "curl/8.5.0" }, "a", "ua-declared-bot"],
      [{ "User-Agent": null }, "a", "ua-missing"],
      [{ "User-Agent": "Mozilla/5.0 HeadlessChrome/120.0.0.0" }, "a", "ua-headless"],
      [{ "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)" }, "v", "verified-crawler-ua"],
      [{ "Accept-Language": null, "Sec-Fetch-Site": null }, "s", "no-sec-fetch"],
    ];
    for (const [headers, cls, reason] of cases) {
      await clearAll();
      await handleView(beacon({ headers }), makeEnv(), NOW);
      const r = await rows();
      expect(r.length, JSON.stringify(headers)).toBe(1);
      expect(r[0]?.bot_class).toBe(cls);
      expect(r[0]?.reason).toBe(reason);
      expect(r[0]?.first).toBe(0);
      expect(await seen()).toEqual([]);
      expect((await budgets()).filter((b) => b.key.startsWith("bud:")).map((b) => b.key.split(":")[2])).toEqual(["pvbot"]);
    }
  });

  it("bots_mode_drop_stores_no_automated_or_suspicious_row_and_spends_no_budget", async () => {
    const env = makeEnv({ BOTS_MODE: "drop" });
    await handleView(beacon({ headers: CURL }), env, NOW);
    await handleView(beacon({ headers: { "Accept-Language": null, "Sec-Fetch-Site": null } }), env, NOW);
    expect(await rows()).toEqual([]);
    expect(await budgets()).toEqual([]);
    await handleView(beacon(), env, NOW);
    expect((await rows()).length).toBe(1);
  });

  it("bots_mode_count_is_the_default", async () => {
    await handleView(beacon({ headers: CURL }), makeEnv(), NOW);
    expect((await rows()).length).toBe(1);
  });
});

describe("per-client cap", () => {
  const ip = (n: number) => ({ "CF-Connecting-IP": `203.0.113.${n}` });

  it("one_client_exhausting_its_cap_never_spends_more_than_its_cap_of_the_global_budget", async () => {
    const env = makeEnv({ CLIENT_VIEWS_PER_HOUR: "3", VIEWS_PER_HOUR: "100" });
    for (let i = 0; i < 20; i++) await handleView(beacon({ headers: ip(9) }), env, NOW);
    expect((await rows()).length).toBe(3);
    const global = (await budgets()).find((b) => b.key.startsWith("bud:"));
    expect(global?.value).toBe("3");
    // Other visitors are untouched by the flood.
    for (let n = 1; n <= 4; n++) await handleView(beacon({ headers: ip(n) }), env, NOW);
    expect((await rows()).length).toBe(7);
  });

  it("many_distinct_clients_still_share_the_global_budget", async () => {
    const env = makeEnv({ CLIENT_VIEWS_PER_HOUR: "5", VIEWS_PER_HOUR: "4" });
    for (let n = 1; n <= 10; n++) await handleView(beacon({ headers: ip(n) }), env, NOW);
    expect((await rows()).length).toBe(4);
  });

  it("the_cap_resets_next_hour_and_default_is_documented", async () => {
    expect(DEFAULT_CLIENT_VIEWS_PER_HOUR).toBe(60);
    expect(MAX_CLIENT_VIEWS_PER_HOUR).toBe(1000);
    const env = makeEnv({ CLIENT_VIEWS_PER_HOUR: "1" });
    await handleView(beacon(), env, NOW);
    await handleView(beacon(), env, NOW);
    await handleView(beacon(), env, NOW + 3_600_000);
    expect((await rows()).length).toBe(2);
  });

  it("an_override_above_the_ceiling_falls_back_to_the_default", async () => {
    const env = makeEnv({ CLIENT_VIEWS_PER_HOUR: "999999" });
    for (let i = 0; i < 70; i++) await handleView(beacon(), env, NOW);
    expect((await rows()).length).toBe(DEFAULT_CLIENT_VIEWS_PER_HOUR);
  }, 60_000);

  it("the_stored_key_is_not_the_visitor_hash_nor_the_address", async () => {
    await handleView(beacon(), makeEnv(), NOW);
    const key = (await budgets()).find((b) => b.key.startsWith("cap:"))?.key ?? "";
    expect(key).toMatch(/^cap:\d{8}:[0-9a-f]{16}$/);
    expect(key).not.toContain(IP4);
    expect(key.split(":")[2]).not.toBe((await seen())[0]?.h?.slice(0, 16));
  });

  it("bots_are_capped_per_client_too", async () => {
    const env = makeEnv({ CLIENT_VIEWS_PER_HOUR: "2" });
    for (let i = 0; i < 6; i++) await handleView(beacon({ headers: { "User-Agent": "curl/8.5.0" } }), env, NOW);
    expect((await rows()).length).toBe(2);
  });

  it("one_shared_cap_covers_bot_and_human_traffic_from_one_address", async () => {
    const env = makeEnv({ CLIENT_VIEWS_PER_HOUR: "3", VIEWS_PER_HOUR: "100", BOT_VIEWS_PER_HOUR: "100" });
    await handleView(beacon({ headers: { "User-Agent": "curl/8.5.0" } }), env, NOW);
    await handleView(beacon({ headers: { "User-Agent": "curl/8.5.0" } }), env, NOW);
    await handleView(beacon(), env, NOW);
    await handleView(beacon(), env, NOW); // the fourth view of any kind is over the shared cap
    await handleView(beacon({ headers: { "User-Agent": "curl/8.5.0" } }), env, NOW);
    expect((await rows()).length).toBe(3);
    expect((await budgets()).filter((b) => b.key.startsWith("cap:")).length).toBe(1);
  });

  it("client_cap_is_clamped_below_the_class_budget", () => {
    // human at most half the human budget, bot at most a quarter of the bot budget
    expect(clientLimit(makeEnv({ CLIENT_VIEWS_PER_HOUR: "1000", VIEWS_PER_HOUR: "120" }), false)).toBe(60);
    expect(clientLimit(makeEnv({ CLIENT_VIEWS_PER_HOUR: "1000", VIEWS_PER_HOUR: "10" }), false)).toBe(5);
    expect(clientLimit(makeEnv({ CLIENT_VIEWS_PER_HOUR: "1000", BOT_VIEWS_PER_HOUR: "30" }), true)).toBe(7);
    expect(clientLimit(makeEnv({ CLIENT_VIEWS_PER_HOUR: "2", BOT_VIEWS_PER_HOUR: "30" }), true)).toBe(2);
    expect(clientLimit(makeEnv({ VIEWS_PER_HOUR: "1" }), false)).toBe(1);
    expect(clientLimit(makeEnv({ VIEWS_PER_HOUR: "0" }), false)).toBe(0);
    expect(clientLimit(makeEnv({}), false)).toBe(60);
    expect(clientLimit(makeEnv({}), true)).toBe(7);
  });

  it("a_client_cannot_use_more_than_half_the_human_budget", async () => {
    const env = makeEnv({ VIEWS_PER_HOUR: "10", CLIENT_VIEWS_PER_HOUR: "1000" });
    for (let i = 0; i < 30; i++) await handleView(beacon(), env, NOW);
    expect((await rows()).length).toBe(5);
  });
});

describe("atomic accept", () => {
  const client = (n: number) => ({ "CF-Connecting-IP": `203.0.113.${n}` });
  const count = async (like: string) => (await budgets()).filter((b) => b.key.startsWith(like)).length;

  it("after_the_global_budget_is_spent_distinct_clients_create_no_rows_and_no_writes", async () => {
    const env = makeEnv({ VIEWS_PER_HOUR: "2", CLIENT_VIEWS_PER_HOUR: "5" });
    await handleView(beacon({ headers: client(1) }), env, NOW);
    await handleView(beacon({ headers: client(2) }), env, NOW);
    expect((await rows()).length).toBe(2);
    const capRows = await count("cap:");
    expect(capRows).toBe(2);

    const spy = spyDb();
    for (let n = 3; n < 63; n++) {
      await handleView(beacon({ headers: client(n) }), { ...env, DB: spy.DB }, NOW);
    }
    expect(spy.calls.rowsWritten).toBe(0);
    expect(await count("cap:")).toBe(capRows);
    expect((await rows()).length).toBe(2);
    expect((await seen()).length).toBe(2);
    expect((await budgets()).find((b) => b.key.startsWith("bud:"))?.value).toBe("2");
  });

  it("an_exhausted_client_cap_spends_nothing_globally", async () => {
    const env = makeEnv({ CLIENT_VIEWS_PER_HOUR: "1", VIEWS_PER_HOUR: "100" });
    for (let i = 0; i < 10; i++) await handleView(beacon({ headers: client(7) }), env, NOW);
    expect((await budgets()).find((b) => b.key.startsWith("bud:"))?.value).toBe("1");
    expect((await rows()).length).toBe(1);
  });

  it("accept_is_one_batch_and_a_later_failure_rolls_everything_back", async () => {
    const spy = spyDb((stmts) => [...stmts, DB.prepare("INSERT INTO table_that_does_not_exist VALUES (1)")]);
    const res = await handleView(beacon(), { ...makeEnv(), DB: spy.DB }, NOW);
    expect(res.status).toBe(204);
    expect(spy.calls.batch).toBe(1); // attempted, then rolled back as a whole
    expect(spy.calls.rowsWritten).toBe(0);
    expect(await rows()).toEqual([]);
    expect(await seen()).toEqual([]);
    expect(await budgets()).toEqual([]);
  });

  it("a_normal_accept_uses_exactly_one_batch", async () => {
    const spy = spyDb();
    await handleView(beacon(), { ...makeEnv(), DB: spy.DB }, NOW);
    expect(spy.calls.batch).toBe(1);
    expect((await rows()).length).toBe(1);
  });

  it("no_visitor_seen_row_without_an_event", async () => {
    const env = makeEnv({ VIEWS_PER_HOUR: "1" });
    for (let n = 1; n <= 6; n++) await handleView(beacon({ headers: client(n) }), env, NOW);
    expect((await seen()).length).toBe((await rows()).filter((r) => r.first === 1).length);
    expect((await rows()).length).toBe(1);
  });
});

describe("limiter keys", () => {
  it("the_raw_address_never_reaches_a_rate_limiter_binding", async () => {
    const view = fakeLimiter();
    const suspect = fakeLimiter();
    const env = makeEnv({ VIEW_LIMITER: view, VIEW_SUSPECT_LIMITER: suspect });
    await handleView(beacon({ headers: { "CF-Connecting-IP": IP4 } }), env, NOW);
    await handleView(beacon({ headers: { "CF-Connecting-IP": IP6 } }), env, NOW);
    expect(view.keys.length).toBe(2);
    expect(suspect.keys.length).toBe(2);
    for (const k of [...view.keys, ...suspect.keys]) {
      expect(k).toMatch(/^[0-9a-f]{32}$/);
      expect(k).not.toContain(IP4);
      expect(k).not.toContain("2001");
    }
  });

  it("limiter_keys_are_purpose_separated_and_stable_across_the_utc_midnight_boundary", async () => {
    const view = fakeLimiter();
    const suspect = fakeLimiter();
    const env = makeEnv({ VIEW_LIMITER: view, VIEW_SUSPECT_LIMITER: suspect });
    const before = Date.parse("2027-03-14T23:59:59Z");
    const after = Date.parse("2027-03-15T00:00:01Z");
    await handleView(beacon(), env, before);
    await handleView(beacon(), env, before + 500);
    await handleView(beacon(), env, after);
    await handleView(beacon(), env, after + 86_400_000 * 30);
    expect(new Set(view.keys).size).toBe(1); // no daily rotation: no boundary burst
    expect(new Set(suspect.keys).size).toBe(1);
    expect(view.keys[0]).not.toBe(suspect.keys[0]); // different purpose label
    const everything = JSON.stringify([await budgets(), await seen(), await rows()]);
    for (const k of [view.keys[0], suspect.keys[0]] as string[]) {
      expect(everything).not.toContain(k);
      expect(everything).not.toContain(k.slice(0, 16));
    }
  });

  it("the_visitor_hash_still_rotates_daily_while_limiter_keys_do_not", async () => {
    const view = fakeLimiter();
    const env = makeEnv({ VIEW_LIMITER: view });
    await handleView(beacon(), env, Date.parse("2027-03-14T23:59:59Z"));
    await handleView(beacon(), env, Date.parse("2027-03-15T00:00:01Z"));
    const hashes = (await seen()).map((r) => r.h);
    expect(new Set(hashes).size).toBe(2);
    expect(new Set(view.keys).size).toBe(1);
  });

  it("a_different_secret_gives_different_limiter_keys", async () => {
    const l1 = fakeLimiter();
    const l2 = fakeLimiter();
    await handleView(beacon(), makeEnv({ VIEW_LIMITER: l1 }), NOW);
    await handleView(beacon(), makeEnv({ VIEW_LIMITER: l2, VIEW_HASH_SECRET: SECRET_2 }), NOW);
    expect(l1.keys[0]).not.toBe(l2.keys[0]);
  });

  it("requests_that_cannot_be_keyed_never_reach_the_limiter", async () => {
    const l = fakeLimiter();
    await handleView(beacon({ headers: { "CF-Connecting-IP": null } }), makeEnv({ VIEW_LIMITER: l }), NOW);
    await handleView(beacon(), makeEnv({ VIEW_LIMITER: l, VIEW_HASH_SECRET: undefined }), NOW);
    expect(l.calls).toBe(0);
  });
});

describe("fail closed and deadlines", () => {
  it("without_a_usable_secret_or_address_nothing_is_written", async () => {
    for (const over of [
      { VIEW_HASH_SECRET: undefined },
      { VIEW_HASH_SECRET: btoa("short") },
      { VIEW_HASH_SECRET: "%%%not base64%%%" },
    ]) {
      const spy = spyDb();
      const res = await handleView(beacon(), { ...makeEnv(over), DB: spy.DB }, NOW);
      expect(res.status).toBe(204);
      expect(spy.calls.prepare + spy.calls.batch).toBe(0);
    }
    const spy = spyDb();
    await handleView(beacon({ headers: { "CF-Connecting-IP": "bogus" } }), { ...makeEnv(), DB: spy.DB }, NOW);
    expect(spy.calls.prepare + spy.calls.batch).toBe(0);
    expect(await rows()).toEqual([]);
  });

  it("a_stalled_body_is_abandoned_after_the_deadline_with_the_same_204", async () => {
    expect(BODY_DEADLINE_MS).toBeLessThanOrEqual(3000);
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>(
      {
        pull: () => new Promise(() => undefined), // never produces anything
        cancel() {
          cancelled = true;
        },
      },
      { highWaterMark: 0 },
    );
    const t0 = Date.now();
    const res = await handleView(beacon({ stream, headers: { "Content-Length": "20" } }), makeEnv(), NOW, 40);
    expect(res.status).toBe(204);
    expect(Date.now() - t0).toBeLessThan(1500);
    expect(cancelled).toBe(true);
    expect(await rows()).toEqual([]);
  });

  it("country_mode_stores_no_region_or_city_anywhere", async () => {
    const cf = { regionCode: "ZQ", city: "Zzyzxville" };
    await handleView(beacon({ cf }), makeEnv({ GEO_LEVEL: "country" }), NOW);
    expect((await rows()).length).toBe(1);
    const dump = await dumpDatabase();
    expect(dump).not.toContain("Zzyzxville");
    expect(dump).not.toContain("ZQ");
    expect((await rows())[0]).toMatchObject({ country: "US", region: "", city: "" });
    // The default stores them, so the assertions above are not vacuous.
    await handleView(beacon({ cf }), makeEnv(), NOW + 1000);
    expect(await dumpDatabase()).toContain("Zzyzxville");
  });
});

describe("accepted view", () => {
  it("accepted_view_writes_exactly_the_expected_rows", async () => {
    const spy = spyDb();
    const env = { ...makeEnv(), DB: spy.DB };
    await handleView(beacon({ cf: { regionCode: "EX", city: "Exampleton" } }), env, NOW);
    const r = await rows();
    expect(r.length).toBe(1);
    expect(r[0]?.ts).toBe(Math.floor(NOW / 60_000) * 60_000);
    expect((r[0]?.ts as number) % 60_000).toBe(0);
    expect((await seen()).length).toBe(1);
    expect(r[0]).toMatchObject({
      page: "home",
      src_kind: "u",
      src: "bluesky",
      country: "US",
      browser: "chrome",
      bmajor: 128,
      os: "linux",
      device: "desktop",
      lang: "en",
      vp: "m",
      net: "unknown",
      bot_class: "h",
      reason: "none",
      first: 1,
    });
    // Global counter + client counter + event row + ts index + visitor_seen (measured: 6 in the test runtime).
    expect(spy.calls.rowsWritten).toBeLessThanOrEqual(6);
    expect(spy.calls.rowsWritten).toBeGreaterThan(0);
  });

  it("cloudflare_region_and_city_are_stored_at_the_default_level", async () => {
    await handleView(beacon({ cf: { regionCode: "EX", city: "Exampleton" } }), makeEnv(), NOW);
    expect((await rows())[0]).toMatchObject({ country: "US", region: "EX", city: "Exampleton" });
  });

  it("geo_level_limits_what_is_stored", async () => {
    await handleView(beacon({ cf: { regionCode: "EX", city: "Exampleton" } }), makeEnv({ GEO_LEVEL: "region" }), NOW);
    await handleView(beacon({ cf: { regionCode: "EX", city: "Exampleton" } }), makeEnv({ GEO_LEVEL: "country" }), NOW);
    const r = await rows();
    expect(r[0]).toMatchObject({ region: "EX", city: "" });
    expect(r[1]).toMatchObject({ region: "", city: "" });
  });

  it("an_internal_referrer_is_kind_i_and_a_direct_view_is_kind_d", async () => {
    await handleView(beacon({ body: `r=${SITE}&w=l` }), makeEnv(), NOW);
    await handleView(beacon({ body: "" }), makeEnv(), NOW);
    const r = await rows();
    expect(r[0]).toMatchObject({ src_kind: "i", src: "", vp: "l" });
    expect(r[1]).toMatchObject({ src_kind: "d", src: "", vp: "" });
  });

  it("a_database_failure_still_returns_the_same_204", async () => {
    const boom = new Proxy(makeEnv().DB, {
      get(t, p) {
        if (p === "prepare") return () => { throw new Error("db down"); };
        return Reflect.get(t, p, t);
      },
    });
    const res = await handleView(beacon(), { ...makeEnv(), DB: boom }, NOW);
    expect(res.status).toBe(204);
    expect(await res.text()).toBe("");
  });
});
