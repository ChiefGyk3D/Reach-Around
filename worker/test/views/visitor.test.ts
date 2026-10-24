// SPDX-License-Identifier: Apache-2.0
import { beforeEach, describe, expect, it } from "vitest";
import { dayKey, handleView, normalizeAddress, visitorHash } from "../../src/view";
import { IP4, IP6, NOW, SECRET, SECRET_2, beacon, clearAll, makeEnv, rows, seen } from "./helpers";

const DAY = 86_400_000;

function bytes(b64: string): Uint8Array {
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}

async function hash(secret: string, date: string, addr: string, bits = 32): Promise<string> {
  const key = await dayKey(bytes(secret), secret, date);
  return visitorHash(key, addr, bits);
}

beforeEach(clearAll);

describe("normalizeAddress", () => {
  it("handles v4, v6, mapped and junk", () => {
    expect(normalizeAddress(IP4)).toBe("198.51.100.7");
    expect(normalizeAddress("2001:db8:0:1::5")).toBe(normalizeAddress("2001:db8:0:1:ffff::9"));
    expect(normalizeAddress("2001:db8:0:1::5")).not.toBe(normalizeAddress("2001:db8:0:2::5"));
    expect(normalizeAddress("::ffff:198.51.100.7")).toBe("198.51.100.7");
    expect(normalizeAddress("::ffff:c633:6407")).toBe("198.51.100.7");
    expect(normalizeAddress(IP6)).toMatch(/^v6:[0-9a-f]{16}$/);
    for (const bad of [null, "", "unknown", "999.1.1.1", "1.2.3", "1.2.3.4.5", "::g", "1:2:3:4:5:6:7:8:9", "1::2::3", "fe80::1%eth0", "x".repeat(60), "<script>"]) {
      expect(normalizeAddress(bad), String(bad)).toBeNull();
    }
  });
  it("output contains no host bits of an IPv6 address", () => {
    expect(normalizeAddress("2001:db8:1:2:dead:beef:cafe:f00d")).toBe("v6:20010db800010002");
  });
});

describe("visitorHash", () => {
  it("is stable within a day and differs across days, secrets and addresses", async () => {
    const a = await hash(SECRET, "2027-03-14", IP4);
    expect(await hash(SECRET, "2027-03-14", IP4)).toBe(a);
    expect(await hash(SECRET, "2027-03-15", IP4)).not.toBe(a);
    expect(await hash(SECRET_2, "2027-03-14", IP4)).not.toBe(a);
    expect(await hash(SECRET, "2027-03-14", "203.0.113.9")).not.toBe(a);
  });

  it("width is bits/4 hex characters", async () => {
    expect((await hash(SECRET, "2027-03-14", IP4, 16)).length).toBe(4);
    expect((await hash(SECRET, "2027-03-14", IP4, 32)).length).toBe(8);
    expect((await hash(SECRET, "2027-03-14", IP4, 64)).length).toBe(16);
    expect(await hash(SECRET, "2027-03-14", IP4, 16)).toMatch(/^[0-9a-f]{4}$/);
  });

  it("odd widths mask the spare bits", async () => {
    const h = await hash(SECRET, "2027-03-14", IP4, 18);
    expect(h.length).toBe(5);
    expect(Number.parseInt(h.slice(4), 16) & 0x3).toBe(0);
  });

  it("is not a bare digest of the address and does not contain it", async () => {
    const h = await hash(SECRET, "2027-03-14", IP4, 64);
    const d = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(IP4)));
    const plain = Array.from(d.slice(0, 8), (b) => b.toString(16).padStart(2, "0")).join("");
    expect(h).not.toBe(plain);
    expect(h).not.toContain("198");
  });
});

describe("unique visitors in the handler", () => {
  it("first_is_1_once_per_utc_day_and_the_hash_rotates", async () => {
    const env = makeEnv();
    await handleView(beacon(), env, NOW);
    await handleView(beacon(), env, NOW + 1000);
    await handleView(beacon(), env, NOW + DAY);
    const r = await rows();
    expect(r.map((x) => x.first)).toEqual([1, 0, 1]);
    const s = await seen();
    expect(s.length).toBe(2);
    expect(s[0]?.day).not.toBe(s[1]?.day);
    expect(s[0]?.h).not.toBe(s[1]?.h);
    expect(s[0]?.h).toMatch(/^[0-9a-f]{16}$/);
  });

  it("uses_the_utc_day_not_the_local_day", async () => {
    // 2027-03-14T23:59:59Z and 2027-03-15T00:00:01Z are different UTC days.
    await handleView(beacon(), makeEnv(), Date.parse("2027-03-14T23:59:59Z"));
    await handleView(beacon(), makeEnv(), Date.parse("2027-03-15T00:00:01Z"));
    expect((await rows()).map((x) => x.first)).toEqual([1, 1]);
  });

  it("a_different_secret_gives_a_different_stored_hash", async () => {
    await handleView(beacon(), makeEnv({ VIEW_HASH_SECRET: SECRET }), NOW);
    const a = (await seen())[0]?.h;
    await clearAll();
    await handleView(beacon(), makeEnv({ VIEW_HASH_SECRET: SECRET_2 }), NOW);
    expect((await seen())[0]?.h).not.toBe(a);
  });

  it("same_slash_64_counts_once", async () => {
    await handleView(beacon({ headers: { "CF-Connecting-IP": "2001:db8:0:1::5" } }), makeEnv(), NOW);
    await handleView(beacon({ headers: { "CF-Connecting-IP": "2001:db8:0:1:ffff::9" } }), makeEnv(), NOW);
    await handleView(beacon({ headers: { "CF-Connecting-IP": "2001:db8:0:2::5" } }), makeEnv(), NOW);
    expect((await rows()).map((x) => x.first)).toEqual([1, 0, 1]);
  });

  it("mapped_v6_equals_v4", async () => {
    await handleView(beacon(), makeEnv(), NOW);
    await handleView(beacon({ headers: { "CF-Connecting-IP": "::ffff:198.51.100.7" } }), makeEnv(), NOW);
    expect((await rows()).map((x) => x.first)).toEqual([1, 0]);
  });

  it("uv_off_still_records_the_view_with_no_visitor_row", async () => {
    for (const over of [{ UV_ENABLED: "0" }, { UV_ENABLED: "false" }]) {
      await clearAll();
      const res = await handleView(beacon(), makeEnv(over), NOW);
      expect(res.status).toBe(204);
      const r = await rows();
      expect(r.length, JSON.stringify(over)).toBe(1);
      expect(r[0]?.first).toBe(0);
      expect(await seen()).toEqual([]);
    }
  });

  it("a_missing_short_or_malformed_secret_or_an_unparsable_address_records_nothing", async () => {
    for (const over of [
      { VIEW_HASH_SECRET: btoa("short") },
      { VIEW_HASH_SECRET: undefined },
      { VIEW_HASH_SECRET: "%%%not base64%%%" },
    ]) {
      const res = await handleView(beacon(), makeEnv(over), NOW);
      expect(res.status).toBe(204);
    }
    await handleView(beacon({ headers: { "CF-Connecting-IP": null } }), makeEnv(), NOW);
    await handleView(beacon({ headers: { "CF-Connecting-IP": "junk" } }), makeEnv(), NOW);
    expect(await rows()).toEqual([]);
    expect(await seen()).toEqual([]);
  });

  it("uv_bits_is_64_by_default_and_anything_lower_falls_back_to_64", async () => {
    for (const bits of [undefined, "64", "32", "16", "48", "15", "0", "x", "", "129", "-1"]) {
      await clearAll();
      await handleView(beacon(), makeEnv({ UV_BITS: bits }), NOW);
      expect((await seen())[0]?.h.length, String(bits)).toBe(16);
    }
    for (const [bits, len] of [["80", 20], ["128", 32]] as const) {
      await clearAll();
      await handleView(beacon(), makeEnv({ UV_BITS: bits }), NOW);
      expect((await seen())[0]?.h.length, bits).toBe(len);
    }
  });

  it("twenty_concurrent_identical_views_give_exactly_one_first", async () => {
    const env = makeEnv();
    await Promise.all(Array.from({ length: 20 }, () => handleView(beacon(), env, NOW)));
    const r = await rows();
    expect(r.length).toBe(20);
    expect(r.filter((x) => x.first === 1).length).toBe(1);
    expect((await seen()).length).toBe(1);
  });

  it("the_secret_does_not_appear_in_any_stored_value", async () => {
    await handleView(beacon(), makeEnv(), NOW);
    const text = JSON.stringify([await rows(), await seen()]);
    expect(text).not.toContain(SECRET);
  });
});
