// SPDX-License-Identifier: Apache-2.0
import { afterEach, describe, expect, it, vi } from "vitest";
import worker from "../../src/index";
import {
  DAILY_WRITE_CAP,
  HEARTBEAT_SECONDS,
  LIVE_CRON,
  STALE_AFTER_SECONDS,
  handleLive,
  isLiveKilled,
  runLivePoller,
  validKickHandle,
  validTwitchHandle,
  type LiveEnv,
  type LiveKV,
} from "../../src/live";

const NOW = Date.UTC(2027, 0, 1, 12, 0, 0);
const NOW_S = Math.floor(NOW / 1000);
const KEY_SENTINEL = "sk_SENTINEL_STREAM_KEY_do_not_leak";
const EXTRA_SENTINEL = "SENTINEL_TITLE_and_viewers";

class FakeKV implements LiveKV {
  store = new Map<string, string>();
  puts: Array<{ key: string; value: string }> = [];
  deletes: string[] = [];
  async get(key: string) {
    return this.store.get(key) ?? null;
  }
  async put(key: string, value: string) {
    this.puts.push({ key, value });
    this.store.set(key, value);
  }
  async delete(key: string) {
    this.deletes.push(key);
    this.store.delete(key);
  }
  statePuts() {
    return this.puts.filter((p) => p.key === "live:state");
  }
}

class FakeCache {
  m = new Map<string, Response>();
  async match(req: Request) {
    return this.m.get(req.url)?.clone();
  }
  async put(req: Request, res: Response) {
    this.m.set(req.url, res);
  }
}

function baseEnv(kv: FakeKV, extra: Partial<LiveEnv> = {}): LiveEnv {
  return {
    LIVE_KV: kv,
    LIVE_TWITCH_HANDLE: "example_channel",
    LIVE_KICK_HANDLE: "example-channel",
    TWITCH_CLIENT_ID: "tid",
    TWITCH_CLIENT_SECRET: "tsecret",
    KICK_CLIENT_ID: "kid",
    KICK_CLIENT_SECRET: "ksecret",
    ...extra,
  };
}

interface Net {
  twitchLive: boolean;
  kickLive: boolean;
  fail?: boolean;
  tokenTtl?: number;
}

function fakeFetch(net: Net) {
  const calls: string[] = [];
  const f = vi.fn(async (url: string) => {
    calls.push(url);
    if (net.fail) return new Response("no", { status: 500 });
    const ttl = net.tokenTtl ?? 3600;
    if (url.startsWith("https://id.twitch.tv/")) {
      return Response.json({ access_token: "ttok", expires_in: ttl });
    }
    if (url.startsWith("https://id.kick.com/")) {
      return Response.json({ access_token: "ktok", expires_in: ttl });
    }
    if (url.startsWith("https://api.twitch.tv/helix/streams")) {
      return Response.json({
        data: net.twitchLive
          ? [{ type: "live", started_at: "2027-01-01T10:00:00Z", title: EXTRA_SENTINEL, viewer_count: 99 }]
          : [],
      });
    }
    if (url.startsWith("https://api.kick.com/public/v1/channels")) {
      return Response.json({
        data: [
          {
            slug: "example-channel",
            stream_title: EXTRA_SENTINEL,
            stream: {
              is_live: net.kickLive,
              key: KEY_SENTINEL,
              url: "rtmp://x/" + KEY_SENTINEL,
              start_time: "2027-01-01T09:00:00Z",
              viewer_count: 42,
            },
          },
        ],
      });
    }
    throw new Error("unexpected url " + url);
  });
  return { f: f as unknown as (u: string, i?: RequestInit) => Promise<Response>, calls, mock: f };
}

const get = (path = "/e/live") => new Request("https://links.example.test" + path);

afterEach(() => vi.restoreAllMocks());

describe("GET /e/live", () => {
  it("serves the exact shape and headers, booleans and timestamps only", async () => {
    const kv = new FakeKV();
    const net = fakeFetch({ twitchLive: true, kickLive: false });
    await runLivePoller(baseEnv(kv), NOW, { fetch: net.f });
    const res = await handleLive(get(), baseEnv(kv), { cache: new FakeCache(), now: () => NOW + 1000 });
    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Type")).toBe("application/json");
    expect(res.headers.get("Cache-Control")).toBe("public, max-age=30");
    expect(res.headers.get("X-Content-Type-Options")).toBe("nosniff");
    expect([...res.headers.keys()].some((k) => k.startsWith("access-control"))).toBe(false);
    const text = await res.text();
    expect(JSON.parse(text)).toEqual({
      v: 1,
      as_of: NOW_S,
      ttl: 30,
      kick: { live: false, since: null },
      twitch: { live: true, since: Date.UTC(2027, 0, 1, 10) / 1000 },
    });
    expect(Object.keys(JSON.parse(text))).toEqual(["v", "as_of", "ttl", "kick", "twitch"]);
    expect(text).not.toContain(EXTRA_SENTINEL);
  });

  it("returns the same cached body for any query string and does not re-read KV", async () => {
    const kv = new FakeKV();
    await runLivePoller(baseEnv(kv), NOW, { fetch: fakeFetch({ twitchLive: true, kickLive: true }).f });
    const cache = new FakeCache();
    const spy = vi.spyOn(kv, "get");
    const env = baseEnv(kv);
    const a = await (await handleLive(get(), env, { cache, now: () => NOW })).text();
    const reads = spy.mock.calls.length;
    const b = await (await handleLive(get("/e/live?x=1&evil=%3Cscript%3E"), env, { cache, now: () => NOW })).text();
    const c = await (await handleLive(get("/e/live?"), env, { cache, now: () => NOW })).text();
    expect(b).toBe(a);
    expect(c).toBe(a);
    expect(spy.mock.calls.length).toBe(reads);
    expect(cache.m.size).toBe(1);
  });

  it("works through the Worker with the real edge cache and ignores the query", async () => {
    const kv = new FakeKV();
    await runLivePoller(baseEnv(kv), Date.now(), { fetch: fakeFetch({ twitchLive: false, kickLive: true }).f });
    const env = { ...baseEnv(kv) } as never;
    const a = await worker.fetch(get("/e/live?a=1"), env);
    const b = await worker.fetch(get("/e/live?b=2"), env);
    expect(a.status).toBe(200);
    expect(await b.text()).toBe(await a.text());
  });

  it.each(["POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])("%s is the empty 204", async (method) => {
    const kv = new FakeKV();
    const res = await worker.fetch(new Request("https://links.example.test/e/live", { method }), baseEnv(kv) as never);
    expect(res.status).toBe(204);
    expect(await res.text()).toBe("");
  });

  it("other paths keep the empty 204", async () => {
    const kv = new FakeKV();
    for (const p of ["/e/live/", "/e/livex", "/e/lives", "/", "/e/live/x"]) {
      const res = await worker.fetch(get(p), baseEnv(kv) as never);
      expect(res.status, p).toBe(204);
      expect(await res.text()).toBe("");
    }
  });

  it("serves unknown when no KV is bound or KV throws or holds junk", async () => {
    const unknown = { v: 1, as_of: null, ttl: 30, kick: { live: null, since: null }, twitch: { live: null, since: null } };
    const noKv = await handleLive(get(), {}, { cache: new FakeCache() });
    expect(await noKv.json()).toEqual(unknown);
    const kv = new FakeKV();
    kv.store.set("live:state", "{not json");
    expect(await (await handleLive(get(), baseEnv(kv), { cache: new FakeCache() })).json()).toEqual(unknown);
    const bad = { get: async () => { throw new Error("x"); }, put: async () => {}, delete: async () => {} };
    expect(await (await handleLive(get(), { LIVE_KV: bad }, { cache: new FakeCache() })).json()).toEqual(unknown);
  });

  it("serves stale data as unknown, fresh data as stored", async () => {
    const kv = new FakeKV();
    await runLivePoller(baseEnv(kv), NOW, { fetch: fakeFetch({ twitchLive: true, kickLive: true }).f });
    const fresh = await handleLive(get(), baseEnv(kv), { cache: new FakeCache(), now: () => NOW + STALE_AFTER_SECONDS * 1000 });
    expect(((await fresh.json()) as { twitch: { live: boolean } }).twitch.live).toBe(true);
    const stale = await handleLive(get(), baseEnv(kv), { cache: new FakeCache(), now: () => NOW + (STALE_AFTER_SECONDS + 1) * 1000 });
    expect(await stale.json()).toEqual({
      v: 1, as_of: NOW_S, ttl: 30, kick: { live: null, since: null }, twitch: { live: null, since: null },
    });
  });
});

describe("kill switch", () => {
  it.each([["1", true], ["true", true], ["TRUE", true], ["on", true], ["yes", true], ["0", false], ["false", false], ["off", false], ["no", false], [undefined, false], ["", false]])(
    "parses %s",
    (v, killed) => {
      expect(isLiveKilled({ LIVE_KILL: v as string | undefined })).toBe(killed);
    },
  );

  it.each(["maybe", " 1", "1 ", "2", "enabled", "tru", "null"])("invalid %j fails closed", (v) => {
    expect(isLiveKilled({ LIVE_KILL: v })).toBe(true);
  });

  it("serves the degraded body when killed or invalid, even with fresh data and a warm cache", async () => {
    const kv = new FakeKV();
    await runLivePoller(baseEnv(kv), NOW, { fetch: fakeFetch({ twitchLive: true, kickLive: true }).f });
    const cache = new FakeCache();
    await handleLive(get(), baseEnv(kv), { cache, now: () => NOW });
    for (const v of ["1", "garbage"]) {
      const res = await handleLive(get(), baseEnv(kv, { LIVE_KILL: v }), { cache, now: () => NOW });
      expect(await res.json()).toEqual({
        v: 1, as_of: null, ttl: 30, kick: { live: null, since: null }, twitch: { live: null, since: null },
      });
    }
  });

  it("the poller does nothing when killed", async () => {
    const kv = new FakeKV();
    const net = fakeFetch({ twitchLive: true, kickLive: true });
    await runLivePoller(baseEnv(kv, { LIVE_KILL: "bogus" }), NOW, { fetch: net.f });
    expect(net.calls).toEqual([]);
    expect(kv.puts).toEqual([]);
  });
});

describe("poller secrecy", () => {
  it("never lets a Kick stream key or extra field reach KV, the response or logs", async () => {
    const logs = [
      vi.spyOn(console, "log"), vi.spyOn(console, "warn"), vi.spyOn(console, "error"),
      vi.spyOn(console, "info"), vi.spyOn(console, "debug"),
    ];
    const kv = new FakeKV();
    await runLivePoller(baseEnv(kv), NOW, { fetch: fakeFetch({ twitchLive: true, kickLive: true }).f });
    const res = await handleLive(get(), baseEnv(kv), { cache: new FakeCache(), now: () => NOW });
    const everything = JSON.stringify(kv.puts) + (await res.text());
    expect(everything).not.toContain(KEY_SENTINEL);
    expect(everything).not.toContain(EXTRA_SENTINEL);
    expect(everything).not.toContain("rtmp");
    // Client secrets never reach KV either.
    expect(everything).not.toContain("ksecret");
    expect(everything).not.toContain("tsecret");
    for (const l of logs) expect(l).not.toHaveBeenCalled();
  });

  it("a failing platform body is not logged and degrades only that platform", async () => {
    const logs = vi.spyOn(console, "error");
    const kv = new FakeKV();
    const net = fakeFetch({ twitchLive: true, kickLive: true });
    const f = async (u: string, i?: RequestInit) =>
      u.startsWith("https://api.kick.com/") ? new Response(KEY_SENTINEL, { status: 500 }) : net.f(u, i);
    await runLivePoller(baseEnv(kv), NOW, { fetch: f });
    const stored = JSON.parse(kv.store.get("live:state")!);
    expect(stored.kick).toEqual({ live: null, since: null });
    expect(stored.twitch.live).toBe(true);
    expect(JSON.stringify(kv.puts)).not.toContain(KEY_SENTINEL);
    expect(logs).not.toHaveBeenCalled();
  });
});

describe("write budget", () => {
  it("writes on change and a heartbeat every 10 minutes, not otherwise", async () => {
    const kv = new FakeKV();
    const net = { twitchLive: false, kickLive: false };
    const { f } = fakeFetch(net);
    let t = NOW;
    await runLivePoller(baseEnv(kv), t, { fetch: f }); // first run
    expect(kv.statePuts().length).toBe(1);
    for (let i = 0; i < 4; i++) {
      t += 120_000;
      await runLivePoller(baseEnv(kv), t, { fetch: f }); // unchanged, under 10 min
    }
    expect(kv.statePuts().length).toBe(1);
    t += 120_000; // 10 minutes since the write
    await runLivePoller(baseEnv(kv), t, { fetch: f });
    expect(kv.statePuts().length).toBe(2);
    net.twitchLive = true;
    t += 120_000;
    await runLivePoller(baseEnv(kv), t, { fetch: f }); // change
    expect(kv.statePuts().length).toBe(3);
    net.kickLive = true;
    t += 120_000;
    await runLivePoller(baseEnv(kv), t, { fetch: f });
    expect(kv.statePuts().length).toBe(4);
    expect(HEARTBEAT_SECONDS).toBe(600);
  });

  it("skips the heartbeat past the daily cap but still writes changes; a new UTC day resets", async () => {
    const kv = new FakeKV();
    const net = { twitchLive: false, kickLive: false };
    const { f } = fakeFetch(net);
    const day = new Date(NOW).toISOString().slice(0, 10);
    kv.store.set(
      "live:state",
      JSON.stringify({ v: 1, as_of: NOW_S - 700, kick: { live: false, since: null }, twitch: { live: false, since: null }, day, writes: DAILY_WRITE_CAP }),
    );
    await runLivePoller(baseEnv(kv), NOW, { fetch: f });
    expect(kv.statePuts().length).toBe(0); // heartbeat skipped
    net.kickLive = true;
    await runLivePoller(baseEnv(kv), NOW + 1000, { fetch: f });
    expect(kv.statePuts().length).toBe(1); // change still recorded
    // Next UTC day: heartbeat allowed again, counter reset.
    const tomorrow = NOW + 24 * 3600 * 1000;
    await runLivePoller(baseEnv(kv), tomorrow, { fetch: f });
    expect(kv.statePuts().length).toBe(2);
    expect(JSON.parse(kv.store.get("live:state")!).writes).toBe(1);
  });

  it("a platform error is written once as unknown, then not again", async () => {
    const kv = new FakeKV();
    const ok = fakeFetch({ twitchLive: true, kickLive: true });
    await runLivePoller(baseEnv(kv), NOW, { fetch: ok.f });
    const bad = fakeFetch({ twitchLive: true, kickLive: true, fail: true });
    await runLivePoller(baseEnv(kv), NOW + 120_000, { fetch: bad.f });
    await runLivePoller(baseEnv(kv), NOW + 240_000, { fetch: bad.f });
    expect(kv.statePuts().length).toBe(2);
    expect(JSON.parse(kv.statePuts()[1].value).kick.live).toBeNull();
  });
});

describe("app tokens", () => {
  it("are reused from KV until near expiry, then refreshed", async () => {
    const kv = new FakeKV();
    const net = fakeFetch({ twitchLive: true, kickLive: true, tokenTtl: 3600 });
    const tokenCalls = () => net.calls.filter((u) => u.includes("/oauth")).length;
    await runLivePoller(baseEnv(kv), NOW, { fetch: net.f });
    expect(tokenCalls()).toBe(2);
    await runLivePoller(baseEnv(kv), NOW + 120_000, { fetch: net.f });
    await runLivePoller(baseEnv(kv), NOW + 3000_000, { fetch: net.f }); // 3000 s in, 600 s left
    expect(tokenCalls()).toBe(2);
    await runLivePoller(baseEnv(kv), NOW + 3400_000, { fetch: net.f }); // 200 s left: inside the margin
    expect(tokenCalls()).toBe(4);
  });

  it("a 401 drops the cached token", async () => {
    const kv = new FakeKV();
    const net = fakeFetch({ twitchLive: true, kickLive: true });
    await runLivePoller(baseEnv(kv), NOW, { fetch: net.f });
    const f = async (u: string, i?: RequestInit) =>
      u.startsWith("https://api.twitch.tv/") ? new Response("", { status: 401 }) : net.f(u, i);
    await runLivePoller(baseEnv(kv), NOW + 120_000, { fetch: f });
    expect(kv.deletes).toEqual(["live:token:twitch"]);
  });

  it("request building carries the secrets only in the token form body and auth headers", async () => {
    const kv = new FakeKV();
    const net = fakeFetch({ twitchLive: true, kickLive: true });
    await runLivePoller(baseEnv(kv), NOW, { fetch: net.f });
    for (const url of net.calls) expect(url).not.toMatch(/secret|ksecret|tsecret/);
  });
});

describe("handles", () => {
  it("accept the config model patterns only", () => {
    expect(validTwitchHandle("a_B9")).toBe(true);
    expect(validTwitchHandle("a".repeat(25))).toBe(true);
    expect(validTwitchHandle("a".repeat(26))).toBe(false);
    expect(validKickHandle("a-b_9")).toBe(true);
    expect(validKickHandle("a".repeat(41))).toBe(false);
    expect(validTwitchHandle("a-b")).toBe(false);
  });

  it.each(["", "a b", "a&user_login=evil", "a/../b", "a\n", "a?b", "https://evil.test", "a%2Fb", "é", "a#b"])(
    "hostile handle %j is rejected and never fetched",
    async (h) => {
      expect(validTwitchHandle(h)).toBe(false);
      expect(validKickHandle(h)).toBe(false);
      const kv = new FakeKV();
      const net = fakeFetch({ twitchLive: true, kickLive: true });
      await runLivePoller(baseEnv(kv, { LIVE_TWITCH_HANDLE: h, LIVE_KICK_HANDLE: h }), NOW, { fetch: net.f });
      expect(net.calls).toEqual([]);
    },
  );

  it("an unconfigured platform is unknown", async () => {
    const kv = new FakeKV();
    const net = fakeFetch({ twitchLive: true, kickLive: true });
    await runLivePoller(baseEnv(kv, { LIVE_KICK_HANDLE: undefined }), NOW, { fetch: net.f });
    const s = JSON.parse(kv.store.get("live:state")!);
    expect(s.kick.live).toBeNull();
    expect(s.twitch.live).toBe(true);
  });
});

describe("cron dispatch", () => {
  function ctx() {
    const waits: Promise<unknown>[] = [];
    return { c: { waitUntil: (p: Promise<unknown>) => void waits.push(p), passThroughOnException() {} } as unknown as ExecutionContext, waits };
  }

  it("the 2-minute cron runs only the poller", async () => {
    const kv = new FakeKV();
    const db = { prepare: vi.fn(() => { throw new Error("DB must not be touched"); }), batch: vi.fn() };
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const u = String(input);
      if (u.includes("/oauth")) return Response.json({ access_token: "t", expires_in: 3600 });
      if (u.includes("twitch.tv/helix")) return Response.json({ data: [] });
      return Response.json({ data: [{ stream: { is_live: false, key: KEY_SENTINEL } }] });
    });
    const { c, waits } = ctx();
    await worker.scheduled({ cron: LIVE_CRON } as ScheduledController, { ...baseEnv(kv), DB: db } as never, c);
    await Promise.all(waits);
    expect(db.prepare).not.toHaveBeenCalled();
    expect(db.batch).not.toHaveBeenCalled();
    expect(kv.statePuts().length).toBe(1);
    expect(fetchSpy).toHaveBeenCalled();
    expect(JSON.stringify(kv.puts)).not.toContain(KEY_SENTINEL);
  });

  it("the hourly cron runs the existing jobs and never the poller", async () => {
    const kv = new FakeKV();
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const prepared: string[] = [];
    const stmt = { bind: () => stmt, run: async () => ({ meta: { changes: 0 } }), all: async () => ({ results: [] }), first: async () => null };
    const db = { prepare: (q: string) => { prepared.push(q); return stmt; }, batch: async () => [] };
    const { c, waits } = ctx();
    await worker.scheduled({ cron: "5 * * * *" } as ScheduledController, { ...baseEnv(kv), DB: db } as never, c);
    await Promise.all(waits);
    expect(prepared.length).toBeGreaterThan(0);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(kv.puts).toEqual([]);
  });
});
