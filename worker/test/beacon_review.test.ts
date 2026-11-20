// SPDX-License-Identifier: Apache-2.0
// Pins for the beacon token slice (Refs #610): the view lane row is written only for a stored view, the mint
// honors the kill switches and the site mode, and the negative paths of the mint and the verifier are each
// observed (a limiter that was never called, a setup flag that was set), not just a 204 that any path gives.
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import { normalizeAddress } from "../src/address";
import {
  BEACON_TTL_SECONDS,
  MAX_BEACON_TOKEN_LENGTH,
  clientHashFor,
  handleMint,
  signBeacon,
  verifyBeacon,
} from "../src/beacon";
import { MAX_URL_LENGTH } from "../src/click";
import { handleView, type ViewEnv } from "../src/view";
import { NOW, beacon as viewBeacon, clearAll, fakeLimiter, makeEnv, rows } from "./views/helpers";

const DB = testEnv.DB;
const KEY = "k".repeat(32);
const SECRET = btoa("s".repeat(32));
const UA = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0";
const SITES = JSON.stringify({
  links: { hosts: ["links.example.test"], mode: "beacon", pages: ["home"], link_ids: { twitch: "home" } },
  edge: { hosts: ["edge.example.test"], mode: "edge", pages: "html-200", link_ids: { twitch: "*" } },
});

const day = (ms: number) => new Date(ms).toISOString().slice(0, 10);
const counter = async (key: string, ms: number): Promise<number> =>
  (await DB.prepare("SELECT count FROM counters WHERE key = ?1 AND day = ?2").bind(key, day(ms)).first<{ count: number }>())?.count ?? 0;

beforeEach(async () => {
  await DB.prepare("DELETE FROM counters").run();
  await clearAll();
});

// ------------------------------------------------- the view lane row

describe("the lane row is written only for a view that is stored", () => {
  const env = (over: Partial<ViewEnv> = {}) =>
    makeEnv({ VIEW_HASH_SECRET: SECRET, BEACON_MODE: "observe", BEACON_HMAC_KEY: KEY, ...over } as Partial<ViewEnv>);
  const tokenFor = async (ip: string) => {
    const iat = Math.floor(NOW / 1000) - 5;
    return signBeacon(KEY, { sid: "links", ch: (await clientHashFor(KEY, normalizeAddress(ip))) as string, iat, exp: iat + BEACON_TTL_SECONDS });
  };
  const view = async (ip: string, e: ViewEnv) => {
    const t = await tokenFor(ip);
    return handleView(viewBeacon({ path: `/e/v/home?t=${encodeURIComponent(t)}`, headers: { "CF-Connecting-IP": ip } }), e, NOW);
  };
  const laneSum = async () => (await counter("pvl-v-links", NOW)) + (await counter("pvl-u-links", NOW));

  it("a spent site budget stores one view, and the lane counters sum to the stored rows", async () => {
    const e = env({ VIEWS_PER_HOUR: "1" });
    for (const ip of ["198.51.100.7", "203.0.113.9", "192.0.2.77"]) await view(ip, e);
    const stored = (await rows()).length;
    expect(stored).toBe(1);
    expect(await laneSum()).toBe(stored);
  });

  it("a spent per-client cap stores one view from that client, and the lane counters sum to the stored rows", async () => {
    const e = env({ CLIENT_VIEWS_PER_HOUR: "1" });
    for (let i = 0; i < 4; i++) await view("198.51.100.7", e);
    await view("203.0.113.9", e);
    const stored = (await rows()).length;
    expect(stored).toBe(2);
    expect(await laneSum()).toBe(stored);
  });

  it("the guard bites: the sum would exceed the rows if the lane row ignored the batch", async () => {
    // The control: with room left, every request is stored and the counters equal the rows too.
    const e = env();
    for (const ip of ["198.51.100.7", "203.0.113.9"]) await view(ip, e);
    expect(await laneSum()).toBe((await rows()).length);
    expect((await rows()).length).toBe(2);
  });
});

// --------------------------------------------------------- the mint

type MintEnv = Parameters<typeof handleMint>[1];
const mintEnv = (over: Partial<MintEnv> = {}): MintEnv =>
  ({ DB, SITES, VIEW_HASH_SECRET: SECRET, BEACON_MODE: "observe", BEACON_HMAC_KEY: KEY, MINT_LIMITER: fakeLimiter(), ...over }) as MintEnv;
const mintReq = (host = "links.example.test", url = "https://worker.example.test/e/t") =>
  new Request(url, { method: "POST", headers: { Origin: `https://${host}`, "CF-Connecting-IP": "198.51.100.7", "User-Agent": UA } });
const NOW_MS = Date.parse("2027-03-14T12:00:30Z");

describe("the mint honors the kill switches and the site mode", () => {
  const cases: [string, Partial<MintEnv>, string, number][] = [
    ["both routes killed", { CLICKS_KILL: "1", VIEWS_KILL: "true" }, "links.example.test", 204],
    ["both routes killed for the site", { CLICKS_KILL_SITES: "links", VIEWS_KILL_SITES: "links" }, "links.example.test", 204],
    ["clicks killed, views on (the view still uses a token)", { CLICKS_KILL: "1" }, "links.example.test", 200],
    ["views killed, clicks on", { VIEWS_KILL: "1" }, "links.example.test", 200],
    ["clicks killed for the site only, views on", { CLICKS_KILL_SITES: "links" }, "links.example.test", 200],
    ["another site's kill does not matter", { CLICKS_KILL_SITES: "edge", VIEWS_KILL_SITES: "edge" }, "links.example.test", 200],
    ["a click-only (edge) site still mints: its clicks use tokens", {}, "edge.example.test", 200],
    ["an edge site with its clicks killed has nothing to count", { CLICKS_KILL_SITES: "edge" }, "edge.example.test", 204],
    ["an edge site with clicks killed everywhere", { CLICKS_KILL: "1" }, "edge.example.test", 204],
    ["an edge site is not helped by the view switches being on", { VIEWS_KILL: "0", CLICKS_KILL: "true" }, "edge.example.test", 204],
  ];
  for (const [name, over, host, status] of cases) {
    it(`${name}: ${status}`, async () => {
      const lim = fakeLimiter();
      const res = await handleMint(mintReq(host), mintEnv({ ...over, MINT_LIMITER: lim }), NOW_MS);
      expect(res.status).toBe(status);
      if (status === 204) {
        expect(await res.text()).toBe("");
        expect(lim.calls).toBe(0); // refused before the limiter, so a killed route costs no allowance
      }
    });
  }
});

describe("the mint's negative paths are observed, not just answered", () => {
  it("an unusable BEACON_MODE sets the setup flag and issues nothing", async () => {
    const res = await handleMint(mintReq(), mintEnv({ BEACON_MODE: "Observe" }), NOW_MS);
    expect(res.status).toBe(204);
    expect(await counter("cs-bad", NOW_MS)).toBe(1);
  });

  it("a missing or short BEACON_HMAC_KEY sets the setup flag", async () => {
    for (const k of [undefined, "short", ""]) {
      await DB.prepare("DELETE FROM counters").run();
      const res = await handleMint(mintReq(), mintEnv({ BEACON_HMAC_KEY: k }), NOW_MS);
      expect(res.status, String(k)).toBe(204);
      expect(await counter("cs-bad", NOW_MS), String(k)).toBe(1);
    }
  });

  it("mode off sets no flag", async () => {
    await handleMint(mintReq(), mintEnv({ BEACON_MODE: "off", BEACON_HMAC_KEY: undefined }), NOW_MS);
    expect(await counter("cs-bad", NOW_MS)).toBe(0);
  });

  it("a URL over the length cap is refused before anything else, even on the right path", async () => {
    const long = `https://worker.example.test/e/t?x=${"a".repeat(MAX_URL_LENGTH)}`;
    expect(long.length).toBeGreaterThan(MAX_URL_LENGTH);
    const lim = fakeLimiter();
    const res = await handleMint(mintReq("links.example.test", long), mintEnv({ MINT_LIMITER: lim }), NOW_MS);
    expect(res.status).toBe(204);
    expect(lim.calls).toBe(0);
    // the control: the same request inside the cap mints
    expect((await handleMint(mintReq("links.example.test", "https://worker.example.test/e/t?x=a"), mintEnv(), NOW_MS)).status).toBe(200);
  });

  it("an unknown site never reaches the limiter", async () => {
    const lim = fakeLimiter();
    const res = await handleMint(mintReq("elsewhere.example.test"), mintEnv({ MINT_LIMITER: lim }), NOW_MS);
    expect(res.status).toBe(204);
    expect(lim.calls).toBe(0);
  });

  it("a missing limiter binding answers 204 without throwing, and no token is issued", async () => {
    const res = await handleMint(mintReq(), mintEnv({ MINT_LIMITER: undefined }), NOW_MS);
    expect(res.status).toBe(204);
    expect(await res.text()).toBe("");
  });
});

// --------------------------------------------------- the verifier

describe("the verifier's claim and size checks", () => {
  const IAT = 2_000_000_000;
  const enc = (s: string) => btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  async function forge(text: string): Promise<string> {
    const body = `b1.${enc(text)}`;
    const k = await crypto.subtle.importKey("raw", new TextEncoder().encode(KEY), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const sig = new Uint8Array(await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(body)));
    return `${body}.${enc(String.fromCharCode(...sig))}`;
  }
  const claims = (over: Record<string, unknown> = {}) =>
    JSON.stringify({ sid: "links", ch: "0a1b2c3d", iat: IAT, exp: IAT + BEACON_TTL_SECONDS, jti: "A".repeat(22), ...over });

  it("an expiry before the issue time is refused, even at a time that is neither expired nor from the future", async () => {
    const t = await forge(claims({ iat: IAT, exp: IAT - 10 }));
    expect(await verifyBeacon(KEY, t, IAT - 20)).toBeNull();
    expect(await verifyBeacon(KEY, await forge(claims()), IAT + 1)).not.toBeNull(); // the control
  });

  it("an expiry equal to the issue time is refused", async () => {
    expect(await verifyBeacon(KEY, await forge(claims({ exp: IAT })), IAT - 1)).toBeNull();
  });

  it("a token over the length cap is refused although it is correctly signed and its claims are well formed", async () => {
    const padded = await forge(claims() + " ".repeat(400)); // trailing white space is valid JSON
    expect(padded.length).toBeGreaterThan(MAX_BEACON_TOKEN_LENGTH);
    expect(await verifyBeacon(KEY, padded, IAT + 1)).toBeNull();
    const snug = await forge(claims() + " ".repeat(5));
    expect(snug.length).toBeLessThanOrEqual(MAX_BEACON_TOKEN_LENGTH);
    expect(await verifyBeacon(KEY, snug, IAT + 1)).not.toBeNull(); // the control: same claims inside the cap
  });
});
