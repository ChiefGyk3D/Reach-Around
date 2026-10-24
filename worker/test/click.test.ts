// SPDX-License-Identifier: Apache-2.0
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import { handleClick, type Env } from "../src/click";
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
