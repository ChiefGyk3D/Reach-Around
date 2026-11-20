// SPDX-License-Identifier: Apache-2.0
// A click that carries Global Privacy Control (Sec-GPC: 1) or Do Not Track
// (DNT: 1) records nothing, and answers the same empty 204 as every other
// outcome, so the response form (status, headers, body) is no oracle. Timing is
// not equalized: an early refusal is faster than a recorded click (issue #599).
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import { handleClick, type Env } from "../src/click";

const SITE = "links.example.test";
const BROWSER_UA =
  "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0";

function makeEnv(): Env {
  return {
    DB: testEnv.DB,
    SITE_HOST: SITE,
    LINK_IDS: JSON.stringify(["example-one"]),
    CLICK_LIMITER: { async limit() { return { success: true }; } },
    VIEW_HASH_SECRET: btoa("k".repeat(32)),
  };
}

function click(extra: Record<string, string>): Request {
  return new Request("https://worker.example.test/e/c/example-one", {
    method: "POST",
    headers: {
      "User-Agent": BROWSER_UA,
      "Ping-From": `https://${SITE}/`,
      "CF-Connecting-IP": "198.51.100.7",
      ...extra,
    },
  });
}

async function count(): Promise<number> {
  const r = await testEnv.DB.prepare("SELECT COUNT(*) AS n FROM click_events").first<{ n: number }>();
  return r?.n ?? 0;
}

async function counters(): Promise<number> {
  const r = await testEnv.DB.prepare("SELECT COUNT(*) AS n FROM click_src_daily").first<{ n: number }>();
  return r?.n ?? 0;
}

beforeEach(async () => {
  await testEnv.DB.prepare("DELETE FROM click_events").run();
  await testEnv.DB.prepare("DELETE FROM click_src_daily").run();
});

const OPTED_OUT: Array<[string, Record<string, string>]> = [
  ["Sec-GPC: 1", { "Sec-GPC": "1" }],
  ["DNT: 1", { DNT: "1" }],
  ["Sec-GPC: 1 with spaces", { "Sec-GPC": " 1 " }],
  ["both signals", { "Sec-GPC": "1", DNT: "1" }],
  ["GPC on, DNT off", { "Sec-GPC": "1", DNT: "0" }],
  ["DNT on, GPC off", { "Sec-GPC": "0", DNT: "1" }],
  ["proxy-joined Sec-GPC: 1, 1", { "Sec-GPC": "1, 1" }],
  ["proxy-joined DNT: 0,1", { DNT: "0,1" }],
  ["proxy-joined DNT: 1 , 0", { DNT: "1 , 0" }],
  ["tab around 1", { "Sec-GPC": " 1\t" }],
];

const NORMAL: Array<[string, Record<string, string>]> = [
  ["no signal", {}],
  ["Sec-GPC: 0", { "Sec-GPC": "0" }],
  ["DNT: 0", { DNT: "0" }],
  ["both off", { "Sec-GPC": "0", DNT: "0" }],
  ["DNT: null", { DNT: "null" }],
  ["Sec-GPC: empty", { "Sec-GPC": "" }],
  ["DNT: 11", { DNT: "11" }],
  ["Sec-GPC: 0, 0", { "Sec-GPC": "0, 0" }],
  ["DNT: true", { DNT: "true" }],
  // HTTP optional whitespace is space and tab only; anything else is not a signal.
  ["Sec-GPC: NBSP around 1", { "Sec-GPC": " 1 " }],
  ["DNT: ideographic space around 1", { DNT: "　1　" }],
  ["DNT: 1 with a zero-width space", { DNT: "1​" }],
  ["Sec-GPC: 0 then NBSP-led 1", { "Sec-GPC": "0, 1" }],
];

describe("click opt-out signals", () => {
  it.each(OPTED_OUT)("records nothing with %s, and answers an empty 204", async (_n, headers) => {
    const res = await handleClick(click(headers), makeEnv());
    expect(res.status).toBe(204);
    expect(await res.text()).toBe("");
    expect(await count()).toBe(0);
    expect(await counters()).toBe(0);
  });

  it.each(NORMAL)("still records with %s", async (_n, headers) => {
    const res = await handleClick(click(headers), makeEnv());
    expect(res.status).toBe(204);
    expect(await res.text()).toBe("");
    expect(await count()).toBe(1);
  });

  it("discards a request that carries the signal whatever the page did", async () => {
    // The Worker cannot know what the page's script did. A request that carries
    // the signal is thrown away even when it is otherwise a perfect click.
    const res = await handleClick(
      new Request("https://worker.example.test/e/c/example-one?s=bio&h=bio&l=home", {
        method: "POST",
        headers: {
          "User-Agent": BROWSER_UA,
          "Ping-From": `https://${SITE}/`,
          "Ping-To": "https://example.test/out",
          "CF-Connecting-IP": "198.51.100.8",
          "Sec-GPC": "1",
        },
      }),
      makeEnv(),
    );
    expect(res.status).toBe(204);
    expect(await count()).toBe(0);
    expect(await counters()).toBe(0);
  });

  it("answers an opted-out click with the same response form as a recorded one (status, headers, body; timing is not equalized)", async () => {
    const out = await handleClick(click({ "Sec-GPC": "1" }), makeEnv());
    const rec = await handleClick(click({}), makeEnv());
    expect(out.status).toBe(rec.status);
    expect([...out.headers.entries()]).toEqual([...rec.headers.entries()]);
    expect(await out.text()).toBe(await rec.text());
  });
});
