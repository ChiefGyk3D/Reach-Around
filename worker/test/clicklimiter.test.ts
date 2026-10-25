// SPDX-License-Identifier: Apache-2.0
// The click route must never hand the raw client address to CLICK_LIMITER.
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import { handleClick, type Env } from "../src/click";
import { handleView } from "../src/view";

const SITE = "links.example.test";
const UA = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0";
const SECRET = btoa("a".repeat(32));
const SECRET_2 = btoa("b".repeat(32));
const IP4 = "198.51.100.7";
const IP6 = "2001:db8:abcd:12::7";

function recorder(limit = 100000) {
  const counts = new Map<string, number>();
  return {
    keys: [] as string[],
    async limit({ key }: { key: string }) {
      this.keys.push(key);
      const n = (counts.get(key) ?? 0) + 1;
      counts.set(key, n);
      return { success: n <= limit };
    },
  };
}

function envWith(limiter: ReturnType<typeof recorder>, secret?: string): Env {
  return {
    DB: testEnv.DB,
    SITE_HOST: SITE,
    LINK_IDS: JSON.stringify(["example-one"]),
    CLICK_LIMITER: limiter,
    ...(secret === undefined ? {} : { VIEW_HASH_SECRET: secret }),
  };
}

function click(ip: string | null): Request {
  const h = new Headers({ "User-Agent": UA, "Ping-From": `https://${SITE}/` });
  if (ip !== null) h.set("CF-Connecting-IP", ip);
  return new Request("https://worker.example.test/e/c/example-one", { method: "POST", headers: h });
}

beforeEach(async () => {
  await testEnv.DB.prepare("DELETE FROM click_events").run();
});

const PIECES = [IP4, "198.51.100", "51.100.7", "2001:db8", "abcd", "unknown"];

describe("click limiter keys", () => {
  for (const [name, secret] of [
    ["with the secret", SECRET],
    ["without the secret", undefined],
  ] as const) {
    it(`no raw address or part of it reaches CLICK_LIMITER ${name}`, async () => {
      const l = recorder();
      for (const ip of [IP4, IP6, null]) await handleClick(click(ip), envWith(l, secret));
      expect(l.keys.length).toBe(3);
      for (const k of l.keys) {
        expect(k).toMatch(/^[0-9a-f]{32}$/);
        for (const piece of PIECES) expect(k).not.toContain(piece);
      }
    });

    it(`one client maps to one key and two clients to two ${name}`, async () => {
      const l = recorder();
      const e = envWith(l, secret);
      await handleClick(click(IP4), e);
      await handleClick(click(IP4), e);
      await handleClick(click("203.0.113.9"), e);
      expect(l.keys[0]).toBe(l.keys[1]);
      expect(l.keys[0]).not.toBe(l.keys[2]);
    });
  }

  it("the key differs by secret", async () => {
    const a = recorder();
    const b = recorder();
    await handleClick(click(IP4), envWith(a, SECRET));
    await handleClick(click(IP4), envWith(b, SECRET_2));
    expect(a.keys[0]).not.toBe(b.keys[0]);
  });

  it("the click key differs from the page-view limiter key for the same address", async () => {
    const c = recorder();
    const v = recorder();
    await handleClick(click(IP4), envWith(c, SECRET));
    await handleView(
      new Request("https://worker.example.test/e/v/home", {
        method: "POST",
        headers: {
          "User-Agent": UA,
          Origin: `https://${SITE}`,
          "CF-Connecting-IP": IP4,
          "Content-Length": "0",
        },
        body: "",
      }),
      { DB: testEnv.DB, SITE_HOST: SITE, PAGE_IDS: '["home"]', VIEW_LIMITER: v, VIEW_HASH_SECRET: SECRET },
    );
    expect(v.keys.length).toBe(1);
    expect(c.keys[0]).not.toBe(v.keys[0]);
  });

  it("a client over the limit is still limited without the secret", async () => {
    const l = recorder(2);
    const e = envWith(l);
    for (let i = 0; i < 5; i++) await handleClick(click(IP4), e);
    const n = await testEnv.DB.prepare("SELECT COUNT(*) AS n FROM click_events").first<{ n: number }>();
    expect(n?.n).toBe(2);
  });
});
