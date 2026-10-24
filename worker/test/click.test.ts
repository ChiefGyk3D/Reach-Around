// SPDX-License-Identifier: Apache-2.0
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  handleClick,
  MAX_BODY_BYTES,
  MAX_ID_LENGTH,
  MAX_PATH_LENGTH,
  MAX_URL_LENGTH,
  type Env,
} from "../src/click";
import worker from "../src/index";

const SITE = "links.example.test";
const BROWSER_UA =
  "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0";

/** Test double for the Workers Rate Limiting binding: `limit` calls per key. */
function fakeLimiter(limit = 20) {
  const counts = new Map<string, number>();
  return {
    async limit({ key }: { key: string }) {
      const n = (counts.get(key) ?? 0) + 1;
      counts.set(key, n);
      return { success: n <= limit };
    },
  };
}

function makeEnv(over: Partial<Env> = {}): Env {
  return {
    DB: testEnv.DB,
    SITE_HOST: SITE,
    LINK_IDS: JSON.stringify(["example-one", "example-two"]),
    CLICK_LIMITER: fakeLimiter(),
    ...over,
  };
}

function click(
  path = "/e/c/example-one",
  headers: Record<string, string | null> = {},
  method = "POST",
): Request {
  const h = new Headers();
  const all: Record<string, string | null> = {
    "User-Agent": BROWSER_UA,
    "Ping-From": `https://${SITE}/`,
    "CF-Connecting-IP": "198.51.100.7",
    ...headers,
  };
  for (const [k, v] of Object.entries(all)) {
    if (v !== null) h.set(k, v);
  }
  return new Request(`https://worker.example.test${path}`, { method, headers: h });
}

async function rows() {
  const res = await testEnv.DB.prepare(
    "SELECT id, ts, link_id, country FROM click_events",
  ).all<{ id: number; ts: number; link_id: string; country: string | null }>();
  return res.results;
}

async function expectEmpty204(res: Response) {
  expect(res.status).toBe(204);
  expect(await res.text()).toBe("");
  expect(res.headers.get("location")).toBeNull();
}

beforeEach(async () => {
  await testEnv.DB.prepare("DELETE FROM click_events").run();
});

describe("click endpoint", () => {
  it("records a click for a listed link id", async () => {
    const res = await handleClick(click(), makeEnv());
    await expectEmpty204(res);
    const r = await rows();
    expect(r).toHaveLength(1);
    expect(r[0].link_id).toBe("example-one");
    expect(r[0].country === null || /^[A-Z]{2}$/.test(r[0].country)).toBe(true);
    expect(typeof r[0].ts).toBe("number");
  });

  it("stores a two-letter country from Cloudflare and nothing like an IP", async () => {
    await handleClick(click("/e/c/example-two", { "CF-IPCountry": "DE" }), makeEnv());
    const r = await rows();
    expect(r[0].country).toBe("DE");
    const cols = await testEnv.DB.prepare("PRAGMA table_info(click_events)").all<{
      name: string;
    }>();
    expect(cols.results.map((c) => c.name).sort()).toEqual(
      ["country", "id", "link_id", "ts"].sort(),
    );
    expect(JSON.stringify(r)).not.toContain("198.51.100.7");
  });

  it("drops a malformed country code", async () => {
    await handleClick(click("/e/c/example-one", { "CF-IPCountry": "198.51.100.7" }), makeEnv());
    expect((await rows())[0].country).toBeNull();
  });

  it("unknown id records nothing and returns 204", async () => {
    await expectEmpty204(await handleClick(click("/e/c/not-listed"), makeEnv()));
    expect(await rows()).toHaveLength(0);
  });

  it("a URL passed as an id records nothing", async () => {
    for (const p of [
      "/e/c/https%3A%2F%2Fevil.example%2F",
      "/e/c/https://evil.example/",
      "/e/c//evil.example",
      "/e/c/%2F%2Fevil.example",
      "/e/c/example-one%00",
      "/e/c/%E0%A4%A",
    ]) {
      await expectEmpty204(await handleClick(click(p), makeEnv()));
    }
    expect(await rows()).toHaveLength(0);
  });

  it("empty id records nothing", async () => {
    await expectEmpty204(await handleClick(click("/e/c/"), makeEnv()));
    await expectEmpty204(await handleClick(click("/e/c"), makeEnv()));
    expect(await rows()).toHaveLength(0);
  });

  it("object-prototype names are not ids", async () => {
    for (const id of ["__proto__", "constructor", "toString"]) {
      await handleClick(click(`/e/c/${id}`), makeEnv());
    }
    expect(await rows()).toHaveLength(0);
  });

  it("a broken LINK_IDS variable records nothing", async () => {
    for (const bad of ["not json", "{}", '"example-one"', "[1,2]", ""]) {
      await expectEmpty204(await handleClick(click(), makeEnv({ LINK_IDS: bad })));
    }
    expect(await rows()).toHaveLength(0);
  });

  it("bot user agent records nothing", async () => {
    for (const ua of [
      "Googlebot/2.1 (+http://www.google.com/bot.html)",
      "curl/8.5.0",
      "python-requests/2.32",
      "Mozilla/5.0 (compatible; bingbot/2.0)",
      "",
      null,
    ]) {
      await expectEmpty204(await handleClick(click("/e/c/example-one", { "User-Agent": ua }), makeEnv()));
    }
    expect(await rows()).toHaveLength(0);
  });

  it("wrong origin records nothing", async () => {
    const cases: Record<string, string | null>[] = [
      { "Ping-From": "https://evil.example/", Origin: null },
      { "Ping-From": null, Origin: "https://evil.example" },
      { "Ping-From": `https://${SITE}.evil.example/`, Origin: null },
      { "Ping-From": `https://evil.example/${SITE}`, Origin: null },
      { "Ping-From": null, Origin: null },
      { "Ping-From": "not a url", Origin: null },
      { "Ping-From": null, Origin: "null" },
    ];
    for (const h of cases) {
      await expectEmpty204(await handleClick(click("/e/c/example-one", h), makeEnv()));
    }
    expect(await rows()).toHaveLength(0);
  });

  it("accepts the Origin header from the configured host", async () => {
    await handleClick(
      click("/e/c/example-one", { "Ping-From": null, Origin: `https://${SITE}` }),
      makeEnv(),
    );
    expect(await rows()).toHaveLength(1);
  });

  it("burst from one client is limited", async () => {
    const e = makeEnv();
    for (let i = 0; i < 25; i++) {
      await expectEmpty204(await handleClick(click(), e));
    }
    expect(await rows()).toHaveLength(20);
    // A different client is unaffected.
    await handleClick(click("/e/c/example-one", { "CF-Connecting-IP": "203.0.113.9" }), e);
    expect(await rows()).toHaveLength(21);
  });

  it("checks run in order: ignored requests do not spend rate-limit budget", async () => {
    const e = makeEnv();
    for (let i = 0; i < 30; i++) {
      await handleClick(click("/e/c/not-listed"), e);
      await handleClick(click("/e/c/example-one", { "User-Agent": "curl/8" }), e);
      await handleClick(click("/e/c/example-one", { "Ping-From": "https://evil.example/" }), e);
    }
    await handleClick(click(), e);
    expect(await rows()).toHaveLength(1);
  });

  it("a database failure still returns the same 204", async () => {
    const broken = {
      prepare() {
        throw new Error("boom");
      },
    } as unknown as D1Database;
    await expectEmpty204(await handleClick(click(), makeEnv({ DB: broken })));
  });

  it("response is identical for accepted and ignored requests", async () => {
    const accepted = await handleClick(click(), makeEnv());
    const ignored = await handleClick(click("/e/c/nope", { "User-Agent": "curl/8" }), makeEnv());
    const wrongMethod = await worker.fetch(click("/e/c/example-one", {}, "GET"), makeEnv());
    const elsewhere = await worker.fetch(click("/anything-else"), makeEnv());
    const sig = async (r: Response) => ({
      status: r.status,
      body: await r.text(),
      headers: [...r.headers.entries()].sort(),
    });
    const a = await sig(accepted);
    expect(a.status).toBe(204);
    expect(a.body).toBe("");
    for (const other of [ignored, wrongMethod, elsewhere]) {
      expect(await sig(other)).toEqual(a);
    }
    expect(await rows()).toHaveLength(1);
  });

  it("never redirects, whatever the caller supplies", async () => {
    const res = await worker.fetch(
      click("/e/c/example-one?url=https://evil.example&redirect=https://evil.example&next=//evil.example"),
      makeEnv(),
    );
    await expectEmpty204(res);
  });

  it("only POST on /e/c/:id records; other methods do not", async () => {
    for (const m of ["GET", "PUT", "DELETE", "HEAD"]) {
      await worker.fetch(click("/e/c/example-one", {}, m), makeEnv());
    }
    expect(await rows()).toHaveLength(0);
  });
});

const BOT_FAMILIES: Record<string, string[]> = {
  "link-preview and social crawlers": [
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "Facebot",
    "Twitterbot/1.0",
    "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)",
    "Slack-ImgProxy (+https://api.slack.com/robots)",
    "WhatsApp/2.23.20.0",
    "TelegramBot (like TwitterBot)",
    "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)",
    "LinkedInBot/1.0 (compatible; Mozilla/5.0)",
    "Pinterest/0.2 (+http://www.pinterest.com/bot.html)",
    "Mozilla/5.0 (Macintosh) Applebot/0.1",
  ],
  "search and ad crawlers": [
    "Googlebot/2.1",
    "AdsBot-Google (+http://www.google.com/adsbot.html)",
    "Mediapartners-Google",
    "Mozilla/5.0 (compatible; bingbot/2.0)",
    "Mozilla/5.0 (compatible; YandexBot/3.0)",
    "Mozilla/5.0 (compatible; Baiduspider/2.0)",
    "DuckDuckBot/1.1",
    "ia_archiver (+http://www.alexa.com/site/help/webmasters)",
  ],
  "HTTP clients and scripts": [
    "curl/8.5.0",
    "Wget/1.21",
    "python-requests/2.32",
    "Python-urllib/3.12",
    "Python/3.12 aiohttp/3.9",
    "python-httpx/0.27",
    "axios/1.7.9",
    "node-fetch/1.0",
    "undici",
    "got (https://example.test/got)",
    "Go-http-client/2.0",
    "okhttp/4.12.0",
    "Java/17.0.2",
    "libwww-perl/6.67",
  ],
  "headless browsers and monitors": [
    "Mozilla/5.0 HeadlessChrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Unknown; Linux) AppleWebKit/538.1 PhantomJS/2.1.1",
    "Mozilla/5.0 Chrome-Lighthouse",
    "Pingdom.com_bot_version_1.4",
    "UptimeRobot/2.0",
    "StatusCake_Pagespeed_Indev",
  ],
  "generic words": [
    "SomeTool bot 1.0",
    "acme crawler",
    "acme spider",
    "Yahoo! Slurp",
    "LinkPreview/1.0",
    "SiteMonitor/3",
  ],
};

describe("bot filter", () => {
  for (const [family, uas] of Object.entries(BOT_FAMILIES)) {
    it(`ignores ${family}`, async () => {
      for (const ua of uas) {
        // Matching is case-insensitive: try the original, upper and lower case.
        for (const variant of [ua, ua.toUpperCase(), ua.toLowerCase()]) {
          await expectEmpty204(
            await handleClick(click("/e/c/example-one", { "User-Agent": variant }), makeEnv()),
          );
        }
      }
      expect(await rows()).toHaveLength(0);
    });
  }

  it("accepts normal Firefox, Chrome and Safari", async () => {
    const real = [
      "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    ];
    for (const ua of real) {
      await handleClick(click("/e/c/example-one", { "User-Agent": ua }), makeEnv());
    }
    expect(await rows()).toHaveLength(3);
  });

  it("a 600-character non-bot user agent records nothing", async () => {
    const ua = "Mozilla/5.0 " + "a".repeat(588);
    expect(ua).toHaveLength(600);
    await expectEmpty204(await handleClick(click("/e/c/example-one", { "User-Agent": ua }), makeEnv()));
    expect(await rows()).toHaveLength(0);
  });
});

describe("origin policy and uniform responses", () => {
  const sig = async (r: Response) => ({
    status: r.status,
    body: await r.text(),
    headers: [...r.headers.entries()].sort(),
  });

  it("missing SITE_HOST fails closed with the same 204", async () => {
    const accepted = await sig(await handleClick(click(), makeEnv()));
    await testEnv.DB.prepare("DELETE FROM click_events").run();
    const res = await handleClick(click(), makeEnv({ SITE_HOST: "" }));
    expect(await sig(res)).toEqual(accepted);
    expect(await rows()).toHaveLength(0);
  });

  it("policy: a matching Origin is enough even if Ping-From is for another host", async () => {
    // Either header matching is accepted. This is a speed bump, not a bug.
    await handleClick(
      click("/e/c/example-one", {
        "Ping-From": "https://evil.example/",
        Origin: `https://${SITE}`,
      }),
      makeEnv(),
    );
    expect(await rows()).toHaveLength(1);
  });

  it("OPTIONS and rate-limited responses are byte-identical to an accepted one", async () => {
    const accepted = await sig(await handleClick(click(), makeEnv()));
    const options = await sig(
      await worker.fetch(click("/e/c/example-one", {}, "OPTIONS"), makeEnv()),
    );
    const limited = await sig(
      await handleClick(click(), makeEnv({ CLICK_LIMITER: fakeLimiter(0) })),
    );
    expect(options).toEqual(accepted);
    expect(limited).toEqual(accepted);
    expect(await rows()).toHaveLength(1);
  });
});

describe("cost guards: body cap, path cap, kill switch", () => {
  const sig = async (r: Response) => ({
    status: r.status,
    body: await r.text(),
    headers: [...r.headers.entries()].sort(),
  });

  /** A POST whose body stream counts how many chunks were pulled. */
  function streamed(
    chunks: number,
    chunkSize: number,
    headers: Record<string, string | null> = {},
    path = "/e/c/example-one",
    method = "POST",
  ) {
    const state = { pulls: 0, cancelled: false };
    let sent = 0;
    // highWaterMark 0: the stream pulls only when a reader asks, so the pull
    // count measures what the handler read.
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        state.pulls += 1;
        if (sent >= chunks) {
          controller.close();
          return;
        }
        sent += 1;
        controller.enqueue(new Uint8Array(chunkSize));
      },
      cancel() {
        state.cancelled = true;
      },
    }, { highWaterMark: 0 });
    const h = new Headers({
      "User-Agent": BROWSER_UA,
      "Ping-From": `https://${SITE}/`,
      "CF-Connecting-IP": "198.51.100.7",
    });
    for (const [k, v] of Object.entries(headers)) if (v !== null) h.set(k, v);
    const req = new Request(`https://worker.example.test${path}`, {
      method,
      headers: h,
      body,
      duplex: "half",
    } as RequestInit);
    return { req, state };
  }

  function spyEnv(over: Partial<Env> = {}) {
    const calls = { db: 0, limiter: 0 };
    const real = testEnv.DB;
    const DB = new Proxy(real, {
      get(t, prop) {
        if (prop === "prepare") {
          return (q: string) => {
            calls.db += 1;
            return t.prepare(q);
          };
        }
        const v = Reflect.get(t, prop, t) as unknown;
        return typeof v === "function" ? (v as (...a: unknown[]) => unknown).bind(t) : v;
      },
    });
    const CLICK_LIMITER = {
      async limit() {
        calls.limiter += 1;
        return { success: true };
      },
    };
    return { env: makeEnv({ DB, CLICK_LIMITER, ...over }), calls };
  }

  it("the cap is 1 KiB", () => {
    expect(MAX_BODY_BYTES).toBe(1024);
  });

  it("content-length over the cap: body stream is never pulled, nothing recorded", async () => {
    const { req, state } = streamed(4, 1024, { "Content-Length": "4096" });
    const { env, calls } = spyEnv();
    await expectEmpty204(await handleClick(req, env));
    expect(state.pulls).toBe(0);
    expect(calls.db).toBe(0);
    expect(calls.limiter).toBe(0);
    expect(await rows()).toHaveLength(0);
  });

  it("body with no content-length is ignored without a single pull", async () => {
    const { req, state } = streamed(1000, 512);
    const { env, calls } = spyEnv();
    await expectEmpty204(await handleClick(req, env));
    expect(state.pulls).toBe(0);
    expect(state.cancelled).toBe(true);
    expect(calls.db).toBe(0);
    expect(await rows()).toHaveLength(0);
  });

  it("transfer-encoding with a body is ignored without a pull", async () => {
    const { req, state } = streamed(2, 10, {
      "Content-Length": "20",
      "Transfer-Encoding": "chunked",
    });
    await expectEmpty204(await handleClick(req, makeEnv()));
    expect(state.pulls).toBe(0);
    expect(await rows()).toHaveLength(0);
  });

  it("one huge first chunk is never read", async () => {
    const { req, state } = streamed(1, 50 * 1024 * 1024);
    await expectEmpty204(await handleClick(req, makeEnv()));
    expect(state.pulls).toBe(0);
    expect(state.cancelled).toBe(true);
    expect(await rows()).toHaveLength(0);
  });

  it("lying low content-length with a huge body: zero pulls, body cancelled", async () => {
    const { req, state } = streamed(1, 50 * 1024 * 1024, { "Content-Length": "10" });
    await expectEmpty204(await handleClick(req, makeEnv()));
    expect(state.pulls).toBe(0);
    expect(state.cancelled).toBe(true);
  });

  it("over-cap content-length also cancels the body unread", async () => {
    const { req, state } = streamed(1, 10, { "Content-Length": "4096" });
    await handleClick(req, makeEnv());
    expect(state.pulls).toBe(0);
    expect(state.cancelled).toBe(true);
  });

  it("wrong method, wrong path and unlisted id never touch the body", async () => {
    const cases: Array<[string, string]> = [
      ["PUT", "/e/c/example-one"],
      ["OPTIONS", "/e/c/example-one"],
      ["POST", "/elsewhere"],
      ["POST", "/e/c/" + "a".repeat(MAX_PATH_LENGTH)],
      ["POST", "/e/c/nope"],
    ];
    for (const [method, path] of cases) {
      const { req, state } = streamed(1, 10, { "Content-Length": "10" }, path, method);
      await expectEmpty204(await handleClick(req, makeEnv()));
      expect(state.cancelled, `${method} ${path}`).toBe(false);
      expect(state.pulls).toBe(0);
    }
  });

  it("a 1,000,000 character query is rejected before the URL is parsed", async () => {
    const url = "https://worker.example.test/e/c/example-one?" + "q".repeat(1_000_000);
    const req = new Request(url, { method: "POST", headers: click().headers });
    expect(req.url.length).toBeGreaterThan(MAX_URL_LENGTH);
    const g = globalThis as unknown as { URL: typeof URL };
    const RealURL = g.URL;
    let constructed = 0;
    g.URL = class extends RealURL {
      constructor(...args: ConstructorParameters<typeof URL>) {
        super(...args);
        constructed += 1;
      }
    } as typeof URL;
    try {
      await expectEmpty204(await handleClick(req, makeEnv()));
      expect(constructed).toBe(0);
    } finally {
      g.URL = RealURL;
    }
    expect(await rows()).toHaveLength(0);
  });

  it("garbage content-length is rejected without reading", async () => {
    for (const cl of ["abc", "-1", "1e3", "10, 20", "0x10"]) {
      const { req, state } = streamed(1, 8, { "Content-Length": cl });
      await expectEmpty204(await handleClick(req, makeEnv()));
      expect(state.pulls).toBe(0);
    }
    expect(await rows()).toHaveLength(0);
  });

  it("a small body within the cap is never read, and the click still records", async () => {
    const { req, state } = streamed(2, 100, { "Content-Length": "200" });
    await expectEmpty204(await handleClick(req, makeEnv()));
    expect(state.pulls).toBe(0);
    expect(await rows()).toHaveLength(1);
  });

  it("a request with no body is not read at all", async () => {
    const res = await handleClick(click(), makeEnv());
    await expectEmpty204(res);
    expect(await rows()).toHaveLength(1);
  });

  it("path longer than the cap records nothing and is not decoded", async () => {
    const decode = vi.spyOn(globalThis, "decodeURIComponent");
    try {
      const long = "/e/c/" + "%41".repeat(MAX_PATH_LENGTH);
      await expectEmpty204(await handleClick(click(long), makeEnv()));
      expect(decode).not.toHaveBeenCalled();
    } finally {
      decode.mockRestore();
    }
    expect(await rows()).toHaveLength(0);
  });

  it("an id longer than the id cap records nothing even if listed", async () => {
    const id = "a".repeat(MAX_ID_LENGTH + 1);
    const env = makeEnv({ LINK_IDS: JSON.stringify([id]) });
    await expectEmpty204(await handleClick(click(`/e/c/${id}`), env));
    expect(await rows()).toHaveLength(0);
  });

  it("kill switch: same 204, no D1, no limiter, no body read", async () => {
    for (const v of ["1", "true", "TRUE", "True"]) {
      const { req, state } = streamed(1, 8);
      const { env, calls } = spyEnv({ CLICKS_KILL: v });
      await expectEmpty204(await handleClick(req, env));
      expect(calls.db).toBe(0);
      expect(calls.limiter).toBe(0);
      expect(state.pulls).toBe(0);
    }
    expect(await rows()).toHaveLength(0);
  });

  it("kill switch values other than 1/true leave the route on", async () => {
    for (const v of ["", "0", "false", "yes", "on", " 1", "2"]) {
      await testEnv.DB.prepare("DELETE FROM click_events").run();
      await handleClick(click(), makeEnv({ CLICKS_KILL: v }));
      expect(await rows(), `value ${JSON.stringify(v)}`).toHaveLength(1);
    }
  });

  it("kill switch is read on every request (no module-level cache)", async () => {
    const env = makeEnv();
    await handleClick(click(), env);
    env.CLICKS_KILL = "1";
    await handleClick(click(), env);
    env.CLICKS_KILL = "0";
    await handleClick(click(), env);
    expect(await rows()).toHaveLength(2);
  });

  it("kill switch also applies through the Worker entry point", async () => {
    await expectEmpty204(await worker.fetch(click(), makeEnv({ CLICKS_KILL: "1" })));
    expect(await rows()).toHaveLength(0);
  });

  it("17 different requests get an identical response; only the valid one writes", async () => {
    const cases: Array<[string, () => Request, Partial<Env>?]> = [
      ["GET", () => click("/e/c/example-one", {}, "GET")],
      ["PUT", () => click("/e/c/example-one", {}, "PUT")],
      ["DELETE", () => click("/e/c/example-one", {}, "DELETE")],
      ["OPTIONS", () => click("/e/c/example-one", {}, "OPTIONS")],
      ["other path", () => click("/anything")],
      ["unknown id", () => click("/e/c/nope")],
      ["encoded slash id", () => click("/e/c/..%2F..%2Fx")],
      ["overlong path", () => click("/e/c/" + "a".repeat(MAX_PATH_LENGTH))],
      ["bot UA", () => click("/e/c/example-one", { "User-Agent": "curl/8.0" })],
      ["no UA", () => click("/e/c/example-one", { "User-Agent": null })],
      ["wrong origin", () => click("/e/c/example-one", { "Ping-From": "https://evil.example/" })],
      ["oversize CL", () => streamed(4, 1024, { "Content-Length": "4096" }).req],
      ["body without length", () => streamed(1000, 512).req],
      ["huge first chunk", () => streamed(1, 50 * 1024 * 1024).req],
      ["huge query", () => click("/e/c/example-one?" + "q".repeat(1_000_000))],
      ["killed", () => click(), { CLICKS_KILL: "1" }],
      ["rate limited", () => click(), { CLICK_LIMITER: fakeLimiter(0) }],
    ];
    expect(cases).toHaveLength(17);
    const valid = await sig(await handleClick(click(), makeEnv()));
    expect(valid.status).toBe(204);
    expect(valid.body).toBe("");
    expect(await rows()).toHaveLength(1);
    await testEnv.DB.prepare("DELETE FROM click_events").run();
    for (const [name, build, over] of cases) {
      const got = await sig(await handleClick(build(), makeEnv(over)));
      expect(got, name).toEqual(valid);
    }
    expect(await rows()).toHaveLength(0);
  });
});
