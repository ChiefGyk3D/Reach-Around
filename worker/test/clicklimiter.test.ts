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

// Fragments that a lowercase hex digest can never contain (they hold "." ":" or a
// non-hex letter), so a chance match inside a random key is impossible. Fragments
// made only of hex characters (such as "abcd" in IP6) are checked as whole-key
// equality instead, never as a substring of a random value.
const NON_HEX_PIECES = [IP4, "198.51.100", "51.100.7", "2001:db8", "unknown"];
const HEX_PIECES = ["abcd", "c0000201", "c6336407", "20010db8"];

// Complete 128-bit hex spellings of the test addresses: IPv6 expanded and joined,
// its /64 prefix (the code cuts IPv6 to /64) padded with zeros, IPv4-mapped IPv4,
// and IPv4 padded left or right. Compared whole, lowercased, so a random digest
// is flagged only when it equals one of them exactly.
const FULL_HEX_FORMS = [
  "20010db8abcd00120000000000000007",
  "20010db8abcd00120000000000000000",
  "0000000000000000" + "20010db8abcd0012",
  "00000000000000000000ffffc6336407",
  "000000000000000000000000c6336407",
  "c6336407".padEnd(32, "0"),
];
// Colon-grouped full spellings contain ":" so a hex digest can never hold them.
NON_HEX_PIECES.push("2001:0db8:abcd:0012:0000:0000:0000:0007", "2001:db8:abcd:12:0:0:0:7", "::ffff:198.51.100.7");

/** The reasons a limiter key would count as carrying the raw address, if any. */
export function rawLeaks(key: string): string[] {
  const out: string[] = [];
  if (!/^[0-9a-f]{32}$/.test(key)) out.push("not a 32 character lowercase hex digest");
  for (const p of NON_HEX_PIECES) if (key.includes(p)) out.push(`contains ${p}`);
  const lower = key.toLowerCase();
  for (const f of FULL_HEX_FORMS) if (lower === f) out.push(`is the 128-bit form ${f}`);
  for (const p of HEX_PIECES) if (key === p || key === p.padEnd(32, "0") || key === p.padStart(32, "0")) out.push(`is ${p}`);
  return out;
}

describe("raw address guard (deterministic)", () => {
  it("ignores a hex fragment that happens to sit inside a random digest", () => {
    expect(rawLeaks("0123456789abcd0123456789abcdef01")).toEqual([]);
  });
  it("flags every complete 128-bit hex form of the test addresses, in any case", () => {
    const forms = [
      "20010db8abcd00120000000000000007", // IPv6, expanded groups joined
      "20010db8abcd00120000000000000000", // its /64 prefix, zero padded
      "00000000000000000000ffffc6336407", // IPv4-mapped IPv4
      "000000000000000000000000c6336407", // IPv4, left padded
      "c6336407".padEnd(32, "0"), // IPv4, right padded
    ];
    for (const f of forms) {
      expect(rawLeaks(f), f).not.toEqual([]);
      expect(rawLeaks(f.toUpperCase()), f.toUpperCase()).not.toEqual([]);
    }
    // Colon-grouped spellings are not hex digests and are flagged as such.
    expect(rawLeaks("2001:0db8:abcd:0012:0000:0000:0000:0007")).not.toEqual([]);
  });
  it("does not flag a genuine keyed digest that merely resembles those forms", () => {
    for (const k of [
      "20010db8abcd00120000000000000008", // differs in the last digit
      "a20010db8abcd001200000000000000f", // contains a long run, not equal
      "9f86d081884c7d659a2feaa0c55ad015",
    ]) expect(rawLeaks(k), k).toEqual([]);
  });
  it("still flags a raw address, a part of it, or a non-digest", () => {
    expect(rawLeaks(IP4)).not.toEqual([]);
    expect(rawLeaks("x198.51.100x".padEnd(32, "0"))).not.toEqual([]);
    expect(rawLeaks("2001:db8:abcd:12::7".padEnd(32, "0"))).not.toEqual([]);
    expect(rawLeaks("abcd".padEnd(32, "0"))).not.toEqual([]);
    expect(rawLeaks("")).not.toEqual([]);
  });
});

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
        expect(rawLeaks(k)).toEqual([]);
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

  const SPELLINGS: string[][] = [
    ["2001:db8::1", "2001:0db8:0:0:0:0:0:1", "2001:DB8:0000::0001", "2001:db8:0:0:ffff::9"],
    ["192.0.2.1", "::ffff:192.0.2.1", "::FFFF:c000:0201", "0:0:0:0:0:ffff:192.0.2.1"],
  ];

  async function pair(a: string, b: string, secret?: string) {
    const l = recorder();
    const e = envWith(l, secret);
    await handleClick(click(a), e);
    await handleClick(click(b), e);
    return l.keys;
  }

  for (const secret of [SECRET, undefined]) {
    const tag = secret ? "with the secret" : "without the secret";
    it(`equivalent spellings share one click limiter key ${tag}`, async () => {
      for (const group of SPELLINGS) {
        const keys = new Set<string>();
        for (const s of group) keys.add((await pair(s, s, secret))[0] as string);
        expect(keys.size, group.join(" ")).toBe(1);
      }
    });

    it(`different addresses and different /64 networks differ ${tag}`, async () => {
      const [k1, k2] = await pair("192.0.2.1", "192.0.2.2", secret);
      expect(k1).not.toBe(k2);
      const [k3, k4] = await pair("2001:db8:0:1::1", "2001:db8:0:2::1", secret);
      expect(k3).not.toBe(k4);
    });

    it(`absent, malformed and zone-id addresses all use the one unknown bucket ${tag}`, async () => {
      const l = recorder();
      const e = envWith(l, secret);
      for (const ip of [null, "not-an-ip", "fe80::1%eth0", "2001:db8::1::2", "1.2.3.999"]) {
        await handleClick(click(ip), e);
      }
      expect(new Set(l.keys).size).toBe(1);
      const known = recorder();
      await handleClick(click("192.0.2.1"), envWith(known, secret));
      expect(known.keys[0]).not.toBe(l.keys[0]);
    });
  }

  it("page-view limiter keys are spelling-stable too, and differ for different addresses", async () => {
    const v: ReturnType<typeof recorder>[] = [];
    for (const ip of ["2001:db8::1", "2001:0DB8:0:0:0:0:0:1", "::ffff:192.0.2.1", "192.0.2.1"]) {
      const r = recorder();
      v.push(r);
      await handleView(
        new Request("https://worker.example.test/e/v/home", {
          method: "POST",
          headers: { "User-Agent": UA, Origin: `https://${SITE}`, "CF-Connecting-IP": ip, "Content-Length": "0" },
          body: "",
        }),
        { DB: testEnv.DB, SITE_HOST: SITE, PAGE_IDS: '["home"]', VIEW_LIMITER: r, VIEW_HASH_SECRET: SECRET },
      );
    }
    expect(v[0]?.keys[0]).toBe(v[1]?.keys[0]);
    expect(v[2]?.keys[0]).toBe(v[3]?.keys[0]);
    expect(v[0]?.keys[0]).not.toBe(v[2]?.keys[0]);
  });
});
