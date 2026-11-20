// SPDX-License-Identifier: Apache-2.0
// The beacon token on the routes (Refs #610): the mint route, and the click and view routes reading it
// under BEACON_MODE off, observe and enforce. Every outcome of the click and view routes is the same empty
// 204; the only route that answers with data is the mint, and only on success. Synthetic data only.
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import worker from "../src/index";
import { normalizeAddress } from "../src/address";
import { handleClick, type Env } from "../src/click";
import { CLICK_ADMIT_CEILING, clickLimits, siteShare } from "../src/clickceiling";
import { BEACON_TTL_SECONDS, ENFORCE_AVAILABLE, clientHashFor, handleMint, signBeacon, verifyBeacon } from "../src/beacon";
import { signToken } from "../src/kit/token";
import { handleView, type ViewEnv } from "../src/view";
import { fakeLimiter, makeEnv, NOW, beacon as viewBeacon, clearAll, rows } from "./views/helpers";

const DB = testEnv.DB;
const KEY = "k".repeat(32);
const KEY_OLD = "o".repeat(32);
const KEY_UNKNOWN = "u".repeat(32);
const SECRET = btoa("s".repeat(32));
const SITES = JSON.stringify({
  links: { hosts: ["links.example.test"], mode: "beacon", pages: ["home"], link_ids: { twitch: "home" } },
  plain: { hosts: ["plain.example.test"], mode: "beacon", pages: ["home"], link_ids: { twitch: "home" } },
});
const UA = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0";
const IP = "198.51.100.7";
const SAME_PREFIX_IP = "198.51.100.200";
const OTHER_PREFIX_IP = "203.0.113.9";
const EMPTY_204 = { status: 204, body: "", headers: {} as Record<string, string> };

type Mode = "off" | "observe" | "enforce";
type ClickEnv = Env & { BEACON_MODE?: string; BEACON_HMAC_KEY?: string; BEACON_HMAC_KEY_PREVIOUS?: string };

const clickEnv = (mode: string | undefined, over: Partial<ClickEnv> = {}): ClickEnv => ({
  DB,
  SITES,
  CLICK_LIMITER: { limit: async () => ({ success: true }) },
  VIEW_HASH_SECRET: SECRET,
  BEACON_HMAC_KEY: KEY,
  ...(mode === undefined ? {} : { BEACON_MODE: mode }),
  ...over,
});

const nowSec = () => Math.floor(Date.now() / 1000);
interface TokenOpts { key?: string; sid?: string; ip?: string; iat?: number; ttl?: number }
async function token(o: TokenOpts = {}, now = nowSec()): Promise<string> {
  const iat = o.iat ?? now - 5;
  return signBeacon(o.key ?? KEY, {
    sid: o.sid ?? "links",
    ch: (await clientHashFor(o.key ?? KEY, normalizeAddress(o.ip ?? IP))) as string,
    iat,
    exp: iat + (o.ttl ?? BEACON_TTL_SECONDS),
  });
}

let n = 0;
function click(query = "", o: { host?: string; ip?: string; path?: string } = {}): Request {
  return new Request(`https://worker.example.test${o.path ?? "/e/c/twitch"}${query}`, {
    method: "POST",
    headers: { "User-Agent": UA, "CF-Connecting-IP": o.ip ?? IP, "Ping-From": `https://${o.host ?? "links.example.test"}/`, "X-N": String(++n) },
  });
}
const withToken = (t: string) => `?t=${encodeURIComponent(t)}`;

const dayOf = (ms: number) => new Date(ms).toISOString().slice(0, 10);
const count = async (key: string, ms = Date.now()): Promise<number> =>
  (await DB.prepare("SELECT count FROM counters WHERE key = ?1 AND day = ?2").bind(key, dayOf(ms)).first<{ count: number }>())?.count ?? 0;
const seed = (key: string, c: number, ms = Date.now()) =>
  DB.prepare("INSERT INTO counters (key, day, count) VALUES (?1, ?2, ?3) ON CONFLICT (key, day) DO UPDATE SET count = ?3").bind(key, dayOf(ms), c).run();
const events = async () => (await DB.prepare("SELECT count(*) AS c FROM click_events").first<{ c: number }>())!.c;

async function shape(res: Response): Promise<{ status: number; body: string; headers: Record<string, string> }> {
  return { status: res.status, body: await res.text(), headers: Object.fromEntries(res.headers) };
}

beforeEach(async () => {
  for (const t of ["click_src_daily", "click_country_daily", "click_landing_daily", "click_hop_daily", "click_seal", "click_budget", "click_budget_lh", "click_capacity", "click_events", "counters"]) {
    await DB.prepare(`DELETE FROM ${t}`).run();
  }
  await clearAll();
});

// ---------------------------------------------------------------- click

describe("click route: which lane a token earns (table, observe)", () => {
  const cases: [string, () => Promise<string>, "v" | "u", Partial<ClickEnv>?][] = [
    ["a valid token", () => token(), "v"],
    ["no token", async () => "", "u"],
    ["a forged signature", async () => { const t = await token(); return t.slice(0, -2) + (t.endsWith("AA") ? "BB" : "AA"); }, "u"],
    ["a token signed with an unknown key", () => token({ key: KEY_UNKNOWN }), "u"],
    ["an expired token", () => token({ iat: nowSec() - BEACON_TTL_SECONDS - 5 }), "u"],
    ["a token for another site", () => token({ sid: "plain" }), "u"],
    ["a token minted for another network prefix", () => token({ ip: OTHER_PREFIX_IP }), "u"],
    ["a token minted in the same prefix", () => token({ ip: SAME_PREFIX_IP }), "v"],
    ["a malformed token", async () => "not-a-token", "u"],
    ["an oversized token (a longer one makes the URL itself too long, which the route already refuses)", async () => "b1." + "A".repeat(1000) + ".AAAA", "u"],
    ["a kit token", () => signToken(KEY, { lid: "a1b2c3", iat: nowSec() - 5, exp: nowSec() + 500, cc: "US" }), "u"],
    ["a token signed by the previous key during a rotation", () => token({ key: KEY_OLD }), "v", { BEACON_HMAC_KEY_PREVIOUS: KEY_OLD }],
    ["a previous-key token once the rotation ends", () => token({ key: KEY_OLD }), "u"],
  ];
  for (const mode of ["observe"] as const) {
    for (const [name, make, lane, over] of cases) {
      it(`${mode}: ${name} is the ${lane === "v" ? "verified" : "unverified"} lane, and the answer is the empty 204`, async () => {
        const t = await make();
        const res = await handleClick(click(t === "" ? "" : withToken(t)), clickEnv(mode, over));
        expect(await shape(res)).toEqual(EMPTY_204);
        expect(await events()).toBe(1);
        expect(await count(`cwl-${lane}-links`)).toBeGreaterThan(0);
        expect(await count(`cwl-${lane === "v" ? "u" : "v"}-links`)).toBe(0);
      });
    }
  }

  it("a replay inside the window is accepted every time: the token is stateless (the per-token limit is a later change)", async () => {
    const t = await token();
    for (let i = 0; i < 3; i++) await handleClick(click(withToken(t)), clickEnv("observe"));
    expect(await events()).toBe(3);
    expect(await count("cwl-u-links")).toBe(0);
    expect(await count("cwl-v-links")).toBeGreaterThan(0);
  });

  it("a parameter that is repeated or spelled differently earns nothing", async () => {
    const t = await token();
    for (const q of [`?t=${t}&t=${t}`, `?T=${t}`, `?t=${t}x&t=${t}`, `?t[]=${t}`, `?tt=${t}`, `?t=`]) {
      await DB.prepare("DELETE FROM counters").run();
      await handleClick(click(q), clickEnv("observe"));
      expect(await count("cwl-v-links"), q).toBe(0);
    }
  });

  it("a percent-encoded token is the same token", async () => {
    const t = await token();
    await handleClick(click(`?t=${t.replace(/\./g, "%2E")}`), clickEnv("observe"));
    expect(await count("cwl-v-links")).toBeGreaterThan(0);
  });

  it("a token cannot be passed in a header, a body or the Origin to earn the verified lane", async () => {
    const t = await token();
    const req = new Request("https://worker.example.test/e/c/twitch", {
      method: "POST",
      body: `t=${t}`,
      headers: { "User-Agent": UA, "CF-Connecting-IP": IP, "Ping-From": "https://links.example.test/", "X-Beacon": t, Authorization: t, "Content-Length": String(t.length + 2) },
    });
    await handleClick(req, clickEnv("observe"));
    expect(await count("cwl-v-links")).toBe(0);
  });
});

describe("click route: the modes", () => {
  it("off (the default, unset or empty or off): every click is the unverified lane, a valid token is ignored", async () => {
    for (const mode of [undefined, "", "off"]) {
      await DB.prepare("DELETE FROM counters").run();
      const res = await handleClick(click(withToken(await token())), clickEnv(mode));
      expect(await shape(res)).toEqual(EMPTY_204);
      expect(await count("cwl-v-links"), String(mode)).toBe(0);
      expect(await count("cwl-u-links"), String(mode)).toBeGreaterThan(0);
    }
  });

  it("off does not need the beacon key", async () => {
    await handleClick(click(), clickEnv("off", { BEACON_HMAC_KEY: undefined }));
    expect(await events()).toBe(1);
    expect(await count("cs-bad")).toBe(0);
  });

  it("observe never refuses and never changes the counting: the same traffic writes the same rows as off", async () => {
    const t = await token();
    const run = async (mode: Mode): Promise<[number, number, number]> => {
      for (const tb of ["click_events", "click_country_daily", "click_capacity", "counters"]) await DB.prepare(`DELETE FROM ${tb}`).run();
      for (let i = 0; i < 4; i++) await handleClick(click(i % 2 === 0 ? withToken(t) : ""), clickEnv(mode));
      return [await events(), await count("cw-links"), await count("cwh-links-" + String(new Date().getUTCHours()).padStart(2, "0"))];
    };
    expect(await run("observe")).toEqual(await run("off"));
  });

  it("observe does not let the lane cap bind even when BEACON_LANE_CAP_ENFORCED is on", async () => {
    const lim = clickLimits(siteShare(CLICK_ADMIT_CEILING, 2));
    await seed("cwl-u-links", lim.unverified + 1_000);
    await seed("cf-links_twitch", 1); // not the link's first click, so the lane cap is what would refuse it
    await handleClick(click(), clickEnv("observe", { BEACON_LANE_CAP_ENFORCED: "1" }));
    expect(await events()).toBe(1);
  });

  it("enforce is not available yet: it is an unusable value, the click is refused and the health flag is set", async () => {
    expect(ENFORCE_AVAILABLE).toBe(false);
    const res = await handleClick(click(withToken(await token())), clickEnv("enforce"));
    expect(await shape(res)).toEqual(EMPTY_204);
    expect(await events()).toBe(0);
    expect(await count("cs-bad")).toBe(1);
    expect(await count("cwl-v-links")).toBe(0);
    expect(await count("cwl-u-links")).toBe(0);
  });

  it("the verified lane is held below the whole share by the general budget and the hour slice", async () => {
    // Whatever token a request carries, the verified lane is charged against the same day, hour and
    // first-click rules: it never reaches the share's reserve (20 percent) that only a link's first click uses.
    const lim = clickLimits(siteShare(CLICK_ADMIT_CEILING, 2));
    expect(lim.general).toBeLessThan(lim.share);
    await seed("cwl-v-links", 0);
    await seed("cf-links_twitch", 1);
    await seed("cw-links", lim.general); // the general day budget is spent
    await handleClick(click(withToken(await token())), clickEnv("observe"));
    expect(await events()).toBe(0);
  });

  it("an unusable BEACON_MODE refuses the click (nothing is guessed) and sets the health flag", async () => {
    for (const mode of ["Observe", "on", "1", "enforced", " observe"]) {
      await DB.prepare("DELETE FROM counters").run();
      const res = await handleClick(click(withToken(await token())), clickEnv(mode));
      expect(await shape(res), mode).toEqual(EMPTY_204);
      expect(await events(), mode).toBe(0);
      expect(await count("cs-bad"), mode).toBe(1);
    }
  });

  it("observe or enforce with no usable beacon key keeps counting in the unverified lane and sets the health flag", async () => {
    for (const mode of ["observe"]) {
      for (const k of [undefined, "short", ""]) {
        await DB.prepare("DELETE FROM counters").run();
        await DB.prepare("DELETE FROM click_events").run();
        await handleClick(click(withToken(await token())), clickEnv(mode, { BEACON_HMAC_KEY: k }));
        expect(await events(), `${mode} ${String(k)}`).toBe(1);
        expect(await count("cwl-v-links")).toBe(0);
        expect(await count("cs-bad")).toBe(1);
      }
    }
  });

  it("switching modes between requests takes effect on the next request", async () => {
    const t = await token();
    const e = clickEnv("off");
    await handleClick(click(withToken(t)), e);
    expect(await count("cwl-v-links")).toBe(0);
    e.BEACON_MODE = "observe";
    await handleClick(click(withToken(t)), e);
    const afterObserve = await count("cwl-v-links");
    expect(afterObserve).toBeGreaterThan(0);
    e.BEACON_MODE = "enforce"; // unusable: refused, nothing recorded
    await handleClick(click(withToken(t)), e);
    expect(await count("cwl-v-links")).toBe(afterObserve);
    e.BEACON_MODE = "observe";
    await handleClick(click(withToken(t)), e);
    expect(await count("cwl-v-links")).toBeGreaterThan(afterObserve);
    const afterSecond = await count("cwl-v-links");
    e.BEACON_MODE = "off";
    await handleClick(click(withToken(t)), e);
    expect(await count("cwl-v-links")).toBe(afterSecond); // unchanged by the off request
    expect(await events()).toBe(4); // off, observe, observe and off count; the enforce request is refused
  });

  it("the answer never depends on the token: every case above returns the same status, body and headers as a click with no token", async () => {
    const plain = await shape(await handleClick(click(), clickEnv("observe")));
    for (const t of [await token(), "junk", await token({ sid: "plain" })]) {
      expect(await shape(await handleClick(click(withToken(t)), clickEnv("observe")))).toEqual(plain);
    }
    expect(plain).toEqual(EMPTY_204);
  });
});

// ----------------------------------------------------------------- mint

const mintReq = (o: { method?: string; headers?: Record<string, string | null>; path?: string; body?: string } = {}) => {
  const h = new Headers({ Origin: "https://links.example.test", "CF-Connecting-IP": IP, "User-Agent": UA });
  for (const [k, v] of Object.entries(o.headers ?? {})) v === null ? h.delete(k) : h.set(k, v);
  return new Request(`https://worker.example.test${o.path ?? "/e/t"}`, { method: o.method ?? "POST", headers: h, ...(o.body === undefined ? {} : { body: o.body }) });
};
type MintEnv = ClickEnv & { MINT_LIMITER?: { limit(o: { key: string }): Promise<{ success: boolean }> } };
const mintEnv = (mode: string | undefined, over: Partial<MintEnv> = {}): MintEnv => ({ ...clickEnv(mode), MINT_LIMITER: fakeLimiter(), ...over }) as MintEnv;

describe("mint route", () => {
  it("observe: a signed, bound, 15 minute token in a small JSON body that is not cached", async () => {
    for (const mode of ["observe"]) {
      const res = await handleMint(mintReq(), mintEnv(mode));
      expect(res.status).toBe(200);
      expect(res.headers.get("Content-Type")).toBe("application/json");
      expect(res.headers.get("Cache-Control")).toBe("no-store");
      const body = JSON.parse(await res.text()) as { t: string; ttl: number };
      expect(Object.keys(body).sort()).toEqual(["t", "ttl"]);
      expect(body.ttl).toBe(BEACON_TTL_SECONDS);
      const claims = await verifyBeacon(KEY, body.t, nowSec());
      expect(claims).not.toBeNull();
      expect(claims?.sid).toBe("links");
      expect(claims!.exp - claims!.iat).toBe(BEACON_TTL_SECONDS);
      expect(claims?.ch).toBe(await clientHashFor(KEY, normalizeAddress(SAME_PREFIX_IP)));
    }
  });

  it("a minted token earns the verified lane for the same network and not for another", async () => {
    const body = JSON.parse(await (await handleMint(mintReq(), mintEnv("observe"))).text()) as { t: string };
    await handleClick(click(withToken(body.t)), clickEnv("observe"));
    expect(await count("cwl-v-links")).toBeGreaterThan(0);
    await DB.prepare("DELETE FROM counters").run();
    await handleClick(click(withToken(body.t), { ip: OTHER_PREFIX_IP }), clickEnv("observe"));
    expect(await count("cwl-v-links")).toBe(0);
    expect(await count("cwl-u-links")).toBeGreaterThan(0);
  });

  it("the token is bound to the site the request came from", async () => {
    const body = JSON.parse(await (await handleMint(mintReq({ headers: { Origin: "https://plain.example.test" } }), mintEnv("observe"))).text()) as { t: string };
    expect((await verifyBeacon(KEY, body.t, nowSec()))?.sid).toBe("plain");
  });

  it("writes nothing and calls no D1", async () => {
    const before = JSON.stringify((await DB.prepare("SELECT * FROM counters").all()).results);
    await handleMint(mintReq(), mintEnv("observe"));
    expect(JSON.stringify((await DB.prepare("SELECT * FROM counters").all()).results)).toBe(before);
  });

  const refused: [string, Request | (() => Request), () => MintEnv][] = [
    ["mode off", mintReq(), () => mintEnv("off")],
    ["mode unset", mintReq(), () => mintEnv(undefined)],
    ["an unusable mode", mintReq(), () => mintEnv("Observe")],
    ["enforce, which is not available yet", mintReq(), () => mintEnv("enforce")],
    ["GET", mintReq({ method: "GET" }), () => mintEnv("observe")],
    ["PUT", mintReq({ method: "PUT" }), () => mintEnv("observe")],
    ["Global Privacy Control", mintReq({ headers: { "Sec-GPC": "1" } }), () => mintEnv("observe")],
    ["Do Not Track", mintReq({ headers: { DNT: "1" } }), () => mintEnv("observe")],
    ["Sec-GPC joined by a proxy or padded: 1, 1", mintReq({ headers: { "Sec-GPC": "1, 1" } }), () => mintEnv("observe")],
    ["Sec-GPC joined by a proxy or padded:  1", mintReq({ headers: { "Sec-GPC": " 1" } }), () => mintEnv("observe")],
    ["Sec-GPC joined by a proxy or padded: 0, 1", mintReq({ headers: { "Sec-GPC": "0, 1" } }), () => mintEnv("observe")],
    ["Sec-GPC joined by a proxy or padded: 1 , 0", mintReq({ headers: { "Sec-GPC": "1 , 0" } }), () => mintEnv("observe")],
    ["Sec-GPC joined by a proxy or padded: 0,1", mintReq({ headers: { "Sec-GPC": "0,1" } }), () => mintEnv("observe")],
    ["DNT joined by a proxy or padded: 1, 1", mintReq({ headers: { "DNT": "1, 1" } }), () => mintEnv("observe")],
    ["DNT joined by a proxy or padded:  1", mintReq({ headers: { "DNT": " 1" } }), () => mintEnv("observe")],
    ["DNT joined by a proxy or padded: 0, 1", mintReq({ headers: { "DNT": "0, 1" } }), () => mintEnv("observe")],
    ["DNT joined by a proxy or padded: 1 , 0", mintReq({ headers: { "DNT": "1 , 0" } }), () => mintEnv("observe")],
    ["DNT joined by a proxy or padded: 0,1", mintReq({ headers: { "DNT": "0,1" } }), () => mintEnv("observe")],
    ["no Origin or Ping-From", mintReq({ headers: { Origin: null } }), () => mintEnv("observe")],
    ["an unknown host", mintReq({ headers: { Origin: "https://elsewhere.example.test" } }), () => mintEnv("observe")],
    ["an oversize Origin", mintReq({ headers: { Origin: "https://links.example.test/" + "a".repeat(300) } }), () => mintEnv("observe")],
    ["a sub path", mintReq({ path: "/e/t/links" }), () => mintEnv("observe")],
    ["a look-alike path", mintReq({ path: "/e/tt" }), () => mintEnv("observe")],
    ["no beacon key", mintReq(), () => mintEnv("observe", { BEACON_HMAC_KEY: undefined })],
    ["a short beacon key", mintReq(), () => mintEnv("observe", { BEACON_HMAC_KEY: "short" })],
    ["no hashing secret", mintReq(), () => mintEnv("observe", { VIEW_HASH_SECRET: undefined })],
    ["no client address", mintReq({ headers: { "CF-Connecting-IP": null } }), () => mintEnv("observe")],
    ["an unparsable client address", mintReq({ headers: { "CF-Connecting-IP": "not-an-address" } }), () => mintEnv("observe")],
    ["no limiter binding", mintReq(), () => mintEnv("observe", { MINT_LIMITER: undefined })],
    ["a limiter that says no", mintReq(), () => mintEnv("observe", { MINT_LIMITER: { limit: async () => ({ success: false }) } })],
    ["a limiter that throws", mintReq(), () => mintEnv("observe", { MINT_LIMITER: { limit: async () => { throw new Error("x"); } } })],
    ["an oversize declared body", mintReq({ method: "POST", body: "x", headers: { "Content-Length": "999999" } }), () => mintEnv("observe")],
  ];
  for (const [name, req, mk] of refused) {
    it(`refused (${name}): the same empty 204 as every other refusal, no token`, async () => {
      const res = await handleMint(req instanceof Request ? req.clone() : req(), mk());
      expect(await shape(res)).toEqual(EMPTY_204);
    });
  }

  it("a mint flood from one client is stopped by the limiter, and the limiter key is not the address", async () => {
    const lim = fakeLimiter(3);
    const e = mintEnv("observe", { MINT_LIMITER: lim });
    const outcomes: number[] = [];
    for (let i = 0; i < 6; i++) outcomes.push((await handleMint(mintReq(), e)).status);
    expect(outcomes).toEqual([200, 200, 200, 204, 204, 204]);
    expect(lim.keys.every((k) => /^[0-9a-f]{32}$/.test(k) && !k.includes(IP))).toBe(true);
    expect(new Set(lim.keys).size).toBe(1);
  });

  it("the limiter key differs from the click and view limiter keys for the same address", async () => {
    const clickKeys: string[] = [];
    await handleClick(click(), clickEnv("observe", { CLICK_LIMITER: { limit: async ({ key }) => { clickKeys.push(key); return { success: true }; } } }));
    const lim = fakeLimiter();
    await handleMint(mintReq(), mintEnv("observe", { MINT_LIMITER: lim }));
    expect(lim.keys[0]).not.toBe(clickKeys[0]);
  });

  it("is wired into the Worker at exactly /e/t, and nothing else on /e/ changes", async () => {
    const e = mintEnv("observe") as unknown as Parameters<typeof worker.fetch>[1];
    const minted = await worker.fetch(mintReq(), e);
    expect(minted.status).toBe(200);
    const other = await worker.fetch(mintReq({ path: "/e/x" }), e);
    expect(await shape(other)).toEqual(EMPTY_204);
  });
});

// ----------------------------------------------------------------- view

const dayView = (ms: number) => new Date(ms).toISOString().slice(0, 10);
const viewCount = async (key: string): Promise<number> =>
  (await DB.prepare("SELECT count FROM counters WHERE key = ?1 AND day = ?2").bind(key, dayView(NOW)).first<{ count: number }>())?.count ?? 0;
const viewEnv = (mode: string | undefined, over: Partial<ViewEnv & { BEACON_MODE?: string; BEACON_HMAC_KEY?: string; BEACON_HMAC_KEY_PREVIOUS?: string }> = {}) =>
  makeEnv({ VIEW_HASH_SECRET: SECRET, ...(mode === undefined ? {} : { BEACON_MODE: mode }), BEACON_HMAC_KEY: KEY, ...over } as Partial<ViewEnv>);
const viewTok = (o: TokenOpts = {}) => token({ sid: "links", ip: "198.51.100.7", ...o }, Math.floor(NOW / 1000));
const viewReq = (t: string | null, headers: Record<string, string | null> = {}) =>
  viewBeacon({ path: t === null ? "/e/v/home" : `/e/v/home?t=${encodeURIComponent(t)}`, headers: { "CF-Connecting-IP": "198.51.100.7", ...headers } });

describe("view route", () => {
  const cases: [string, () => Promise<string | null>, "v" | "u", Record<string, string>?][] = [
    ["a valid token", () => viewTok(), "v"],
    ["no token", async () => null, "u"],
    ["a forged token", async () => (await viewTok()).slice(0, -3) + "AAA", "u"],
    ["an expired token", () => viewTok({ iat: Math.floor(NOW / 1000) - BEACON_TTL_SECONDS - 1 }), "u"],
    ["a token for another site", () => viewTok({ sid: "plain" }), "u"],
    ["a token for another network", () => viewTok({ ip: OTHER_PREFIX_IP }), "u"],
    ["a malformed token", async () => "garbage", "u"],
    ["an oversized token", async () => "b1." + "A".repeat(1000) + ".AAAA", "u"],
    ["a previous-key token", () => viewTok({ key: KEY_OLD }), "v", { BEACON_HMAC_KEY_PREVIOUS: KEY_OLD }],
  ];
  for (const mode of ["observe"] as const) {
    for (const [name, make, lane, over] of cases) {
      it(`${mode}: ${name} is recorded as the ${lane === "v" ? "verified" : "unverified"} lane; the view is counted and the answer is the empty 204`, async () => {
        const t = await make();
        const res = await handleView(viewReq(t), viewEnv(mode, over as never), NOW);
        expect(await shape(res)).toEqual(EMPTY_204);
        expect((await rows()).length).toBe(1);
        expect(await viewCount(`pvl-${lane}-links`)).toBe(1);
        expect(await viewCount(`pvl-${lane === "v" ? "u" : "v"}-links`)).toBe(0);
      });
    }
  }

  it("off: no lane counter is written, a valid token is ignored, and the view is counted", async () => {
    for (const mode of [undefined, "off"]) {
      await clearAll();
      await DB.prepare("DELETE FROM counters").run();
      const res = await handleView(viewReq(await viewTok()), viewEnv(mode), NOW);
      expect(await shape(res)).toEqual(EMPTY_204);
      expect((await rows()).length).toBe(1);
      expect(await viewCount("pvl-v-links")).toBe(0);
      expect(await viewCount("pvl-u-links")).toBe(0);
    }
  });

  it("observe counts the same views as off (the lane is a label, not a filter)", async () => {
    const run = async (mode: Mode) => {
      await clearAll();
      for (let i = 0; i < 3; i++) await handleView(viewReq(i === 1 ? null : await viewTok()), viewEnv(mode), NOW);
      return (await rows()).length;
    };
    const off = await run("off");
    expect(await run("observe")).toBe(off);

  });

  it("a refused view writes no lane counter (the counter is part of the accepting batch)", async () => {
    const res = await handleView(viewReq(await viewTok()), viewEnv("observe", { VIEWS_PER_HOUR: "0" }), NOW);
    expect(await shape(res)).toEqual(EMPTY_204);
    expect(await viewCount("pvl-v-links")).toBe(0);
    expect((await rows()).length).toBe(0);
  });

  it("Global Privacy Control and Do Not Track on the view still record nothing, token or not", async () => {
    for (const h of [{ "Sec-GPC": "1" }, { DNT: "1" }] as Record<string, string>[]) {
      const res = await handleView(viewReq(await viewTok(), h), viewEnv("observe"), NOW);
      expect(await shape(res)).toEqual(EMPTY_204);
      expect((await rows()).length).toBe(0);
      expect(await viewCount("pvl-v-links")).toBe(0);
    }
  });

  it("an unusable BEACON_MODE, enforce included (not available yet), records no view and sets the health flag", async () => {
    for (const mode of ["Observe", "enforce"]) {
      await DB.prepare("DELETE FROM counters").run();
      const res = await handleView(viewReq(await viewTok()), viewEnv(mode), NOW);
      expect(await shape(res)).toEqual(EMPTY_204);
      expect((await rows()).length, mode).toBe(0);
      expect(await viewCount("cs-bad"), mode).toBe(1);
    }
  });

  it("observe with no usable beacon key keeps counting, in the unverified lane, and sets the health flag", async () => {
    await handleView(viewReq(await viewTok()), viewEnv("observe", { BEACON_HMAC_KEY: undefined }), NOW);
    expect((await rows()).length).toBe(1);
    expect(await viewCount("pvl-u-links")).toBe(1);
    expect(await viewCount("cs-bad")).toBe(1);
  });

  it("a token in a header earns nothing", async () => {
    const t = await viewTok();
    const res = await handleView(viewBeacon({ headers: { "X-Beacon": t, Authorization: t, "CF-Connecting-IP": "198.51.100.7" } }), viewEnv("observe"), NOW);
    expect(await shape(res)).toEqual(EMPTY_204);
    expect(await viewCount("pvl-v-links")).toBe(0);
    expect(await viewCount("pvl-u-links")).toBe(1);
  });
});
