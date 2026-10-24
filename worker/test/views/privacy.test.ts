// SPDX-License-Identifier: Apache-2.0
/// <reference types="vite/client" />
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BOT_REASONS } from "../../src/botclass";
import {
  BROWSERS,
  DEVICES,
  NETS,
  OSES,
  handleView,
  parseHost,
  parseUtm,
} from "../../src/view";
import {
  CHROME_UA,
  DB,
  IP4,
  IP6,
  NOW,
  SECRET,
  beacon,
  clearAll,
  dumpDatabase,
  fakeLimiter,
  makeEnv,
  rows,
  seen,
  spyConsole,
} from "./helpers";

/** Strings that must never appear in a stored value or in console output. */
const SENTINELS = [
  IP4,
  "2001:db8",
  "6613",
  "x86_64",
  "Mozilla",
  "en-US",
  "q=0.4",
  "64500",
  "Example Hosting Ltd",
  "47.6062",
  "-122.3321",
  "98101",
  "news.example.test/",
  "AppleWebKit",
];

const HOSTILE_CF = {
  asn: 64500,
  asOrganization: "Example Hosting Ltd",
  city: "Exampleton",
  regionCode: "EX",
  country: "US",
  latitude: "47.6062",
  longitude: "-122.3321",
  postalCode: "98101",
  metroCode: "819",
  timezone: "Etc/UTC",
};

function expectNoSentinel(text: string, label: string) {
  for (const s of SENTINELS) expect(text.includes(s), `${label}: ${s}`).toBe(false);
}

beforeEach(clearAll);

describe("nothing identifying is stored", () => {
  it("no_sentinel_reaches_any_table", async () => {
    for (const ip of [IP4, IP6]) {
      await handleView(
        beacon({
          body: "r=news.example.test&u=bluesky&w=m",
          headers: {
            "CF-Connecting-IP": ip,
            "Accept-Language": "en-US,en;q=0.9,de;q=0.4",
          },
          cf: HOSTILE_CF,
        }),
        makeEnv(),
        NOW,
      );
    }
    const dump = await dumpDatabase();
    expectNoSentinel(dump, "database");
    expect(dump).not.toContain(SECRET);
    // The derived categories are there.
    const r = await rows();
    expect(r.length).toBe(2);
    expect(r[0]).toMatchObject({
      city: "Exampleton",
      region: "EX",
      country: "US",
      browser: "chrome",
      bmajor: 128,
      lang: "en",
      vp: "m",
      src_kind: "u",
      src: "bluesky",
    });
  });

  it("referrer_host_is_stored_without_path_query_or_scheme", async () => {
    await handleView(beacon({ body: "r=News.Example.TEST&w=s" }), makeEnv(), NOW);
    const r = (await rows())[0];
    expect(r?.src).toBe("news.example.test");
    expect(JSON.stringify(r)).not.toMatch(/https?:|\/|\?/);
  });

  it("a_full_url_referrer_is_dropped_whole_not_trimmed", async () => {
    await handleView(
      beacon({ body: "r=https%3A%2F%2Fnews.example.test%2Fa%3Ftoken%3DSECRETQ" }),
      makeEnv(),
      NOW,
    );
    expect(await rows()).toEqual([]);
    expect(await dumpDatabase()).not.toContain("SECRETQ");
  });

  it("no_sentinel_in_console_across_accepted_rejected_limited_and_failing_requests", async () => {
    const con = spyConsole();
    try {
      const failing = new Proxy(DB, {
        get(t, p) {
          if (p === "prepare") {
            return () => {
              throw new Error(`boom ${IP4} ${CHROME_UA}`);
            };
          }
          return Reflect.get(t, p, t);
        },
      });
      const reqs: Array<[Request, Parameters<typeof makeEnv>[0]]> = [
        [beacon({ cf: HOSTILE_CF }), {}],
        [beacon({ method: "GET" }), {}],
        [beacon({ headers: { "Sec-GPC": "1" } }), {}],
        [beacon({ headers: { Origin: "https://evil.example.test" } }), {}],
        [beacon({ body: "r=192.0.2.1" }), {}],
        [beacon(), { VIEW_LIMITER: fakeLimiter(0) }],
        [beacon(), { DB: failing }],
        [beacon(), { VIEW_LIMITER: { limit: () => Promise.reject(new Error(IP4)) } }],
        [beacon(), { VIEW_SUSPECT_LIMITER: { limit: () => Promise.reject(new Error(CHROME_UA)) } }],
      ];
      for (const [req, over] of reqs) {
        const res = await handleView(req, makeEnv(over), NOW);
        expect(res.status).toBe(204);
      }
      expect(con.count()).toBe(0);
      expectNoSentinel(con.text(), "console");
    } finally {
      con.restore();
    }
  });

  it("forced_failures_leak_nothing", async () => {
    const con = spyConsole();
    try {
      // A Request-like object whose every header read throws the sentinels.
      const evil = {
        method: "POST",
        url: "https://worker.example.test/e/v/home",
        headers: {
          get() {
            throw new Error(`${IP4} ${CHROME_UA}`);
          },
          has() {
            throw new Error(IP4);
          },
        },
        body: null,
      } as unknown as Request;
      const res = await handleView(evil, makeEnv(), NOW);
      expect(res.status).toBe(204);
      expect(await res.text()).toBe("");
      expect(con.count()).toBe(0);
      expectNoSentinel(con.text(), "console");
      expect(await rows()).toEqual([]);
    } finally {
      con.restore();
    }
  });

  it("the_response_never_echoes_a_request_value", async () => {
    const res = await handleView(
      beacon({ headers: { "X-Echo": IP4 }, cf: HOSTILE_CF }),
      makeEnv(),
      NOW,
    );
    expectNoSentinel(JSON.stringify([...res.headers.entries()]) + (await res.text()), "response");
  });
});

describe("source guards", () => {
  const sources = import.meta.glob<string>("../../src/**/*.ts", {
    query: "?raw",
    import: "default",
    eager: true,
  });

  const FORBIDDEN = /latitude|longitude|postalCode|metroCode|asOrganization|ja3|ja4|botManagement|verifiedBot/;

  /** The guard under test; returns the offending file names. */
  function scan(files: Record<string, string>): string[] {
    return Object.entries(files)
      .filter(([, text]) => FORBIDDEN.test(text))
      .map(([name]) => name);
  }

  it("source_scan_forbids_fine_location_and_fingerprint_names", () => {
    expect(Object.keys(sources).length).toBeGreaterThanOrEqual(7);
    expect(Object.keys(sources).some((k) => k.endsWith("/view.ts"))).toBe(true);
    expect(scan(sources)).toEqual([]);
  });

  it("source_scan_bites_on_a_scratch_file", () => {
    expect(scan({ "scratch.ts": "const z = cf.postalCode;" })).toEqual(["scratch.ts"]);
    expect(scan({ "scratch.ts": "request.cf.latitude" })).toEqual(["scratch.ts"]);
  });

  it("raw_values_do_not_escape: header names are read only at the listed sites", () => {
    const src = Object.entries(sources).find(([k]) => k.endsWith("/view.ts"))?.[1] ?? "";
    expect(src.length).toBeGreaterThan(1000);
    const count = (needle: string) => src.split(needle).length - 1;
    // Read once, in identityFor(); the limiter keys, cap key and visitor hash all derive from it.
    expect(count('"CF-Connecting-IP"')).toBe(1);
    // derive() only.
    expect(count('"User-Agent"')).toBe(1);
    expect(count('"Accept-Language"')).toBe(1);
    // No logging and no outbound call anywhere in the file.
    expect(src).not.toMatch(/console\./);
    expect(src).not.toMatch(/\bfetch\(/);
    expect(src).not.toMatch(/\bcaches\b|\bKV\b/);
  });

  it("raw_header_scan_bites", () => {
    const sample = 'a("CF-Connecting-IP"); b("CF-Connecting-IP");';
    expect(sample.split('"CF-Connecting-IP"').length - 1).not.toBe(1);
  });
});

describe("stored values stay inside their domains (property test)", () => {
  afterEach(() => vi.restoreAllMocks());

  // Deterministic PRNG so a failure is reproducible.
  function mulberry32(seed: number) {
    let a = seed;
    return () => {
      a |= 0;
      a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  const ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._:/@?&=%+ é中<>'\"\\";
  const pick = <T,>(r: () => number, xs: readonly T[]): T => xs[Math.floor(r() * xs.length)] as T;
  const junk = (r: () => number, max: number) =>
    Array.from({ length: Math.floor(r() * max) }, () => ALPHABET[Math.floor(r() * ALPHABET.length)]).join("");

  /** Header values must be ASCII; the body and cf values may be anything. */
  const hdr = (v: string) => v.replace(/[^\x20-\x7e]/g, "");

  const UAS = [
    CHROME_UA,
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
    "curl/8.5.0",
    "Googlebot/2.1",
    "",
  ];

  it("random_inputs_never_produce_a_stored_value_outside_its_domain", async () => {
    const r = mulberry32(20261024);
    const env = makeEnv({ VIEWS_PER_HOUR: "100000", BOT_VIEWS_PER_HOUR: "100000" });
    let ok = 0;
    for (let i = 0; i < 300; i++) {
      const hosts = ["news.example.test", "Bsky.App", "www.example.test", junk(r, 90), "192.0.2.1"];
      const parts: string[] = [];
      if (r() < 0.7) parts.push(`r=${encodeURIComponent(pick(r, hosts))}`);
      if (r() < 0.4) parts.push(`u=${encodeURIComponent(pick(r, ["bluesky", "News-1", junk(r, 40)]))}`);
      if (r() < 0.7) parts.push(`w=${pick(r, ["s", "m", "l", "x", "9", junk(r, 3)])}`);
      if (r() < 0.2) parts.push(`${junk(r, 5)}=${junk(r, 20)}`);
      const body = parts.join("&");
      const req = beacon({
        body,
        headers: {
          "User-Agent": r() < 0.1 ? null : hdr(pick(r, UAS) + (r() < 0.3 ? junk(r, 600) : "")),
          "Accept-Language": r() < 0.2 ? null : hdr(junk(r, 40)),
          "CF-IPCountry": pick(r, ["US", "DE", "XX", "T1", hdr(junk(r, 4)), "us"]),
          "CF-Connecting-IP": pick(r, [IP4, IP6, "203.0.113.9", hdr(junk(r, 20)), "::ffff:203.0.113.9"]),
          "Sec-Fetch-Site": r() < 0.3 ? null : "same-origin",
        },
        cf: {
          regionCode: pick(r, ["EX", "NY", junk(r, 6), 5]),
          city: pick(r, ["Exampleton", "São Paulo", junk(r, 100), "<b>x</b>", "Zürich"]),
          country: junk(r, 3),
          latitude: "47.6",
        },
      });
      await handleView(req, env, NOW + i * 1000);
    }

    const all = await rows();
    expect(all.length).toBeGreaterThan(50); // loose: the generator produced real rows
    const letters = new Set(["s", "m", "l", "x", ""]);
    for (const row of all) {
      const label = JSON.stringify(row);
      expect(["home", "support"], label).toContain(row.page);
      expect(["d", "i", "r", "u"], label).toContain(row.src_kind);
      if (row.src_kind === "r") expect(parseHost(row.src), label).toBe(row.src);
      else if (row.src_kind === "u") expect(parseUtm(row.src), label).toBe(row.src);
      else expect(row.src, label).toBe("");
      expect(row.country, label).toMatch(/^([A-Z]{2})?$/);
      expect(row.country === "XX" || row.country === "T1", label).toBe(false);
      expect(row.region, label).toMatch(/^([A-Z0-9]{1,3})?$/);
      expect(row.city.length, label).toBeLessThanOrEqual(60);
      expect(row.city, label).toMatch(/^[\p{L}\p{M} .'-]*$/u);
      expect(BROWSERS, label).toContain(row.browser);
      expect(Number.isInteger(row.bmajor) && row.bmajor >= 0 && row.bmajor <= 999, label).toBe(true);
      expect(OSES, label).toContain(row.os);
      expect(DEVICES, label).toContain(row.device);
      expect(row.lang, label).toMatch(/^([a-z]{2,3})?$/);
      expect(letters.has(row.vp), label).toBe(true);
      expect(NETS, label).toContain(row.net);
      expect(row.net).toBe("unknown");
      expect(["h", "v", "a", "s"], label).toContain(row.bot_class);
      expect(BOT_REASONS as readonly string[], label).toContain(row.reason);
      expect([0, 1], label).toContain(row.first);
      expect(row.ts % 60_000, label).toBe(0);
      if (row.bot_class !== "h") expect(row.first, label).toBe(0);
      ok += 1;
    }
    expect(ok).toBe(all.length);
    for (const s of await seen()) expect(s.h).toMatch(/^[0-9a-f]{16}$/);
    // Every text value in the whole database is short.
    for (const table of ["view_events", "visitor_seen", "view_meta"]) {
      const res = await DB.prepare(`SELECT * FROM ${table}`).all<Record<string, unknown>>();
      for (const row of res.results) {
        for (const v of Object.values(row)) {
          if (typeof v === "string") expect(v.length, table).toBeLessThanOrEqual(80);
        }
      }
    }
  });
});
