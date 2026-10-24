// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { geoOf, parseBeacon, parseHost, parseUtm } from "../../src/view";

const SITE = "links.example.test";

describe("parseHost", () => {
  it("accepts ordinary hosts and strips www.", () => {
    expect(parseHost("bsky.app")).toBe("bsky.app");
    expect(parseHost("news.example.test")).toBe("news.example.test");
    expect(parseHost("www.example.test")).toBe("example.test");
    expect(parseHost("xn--bcher-kva.example.test")).toBe("xn--bcher-kva.example.test");
    expect(parseHost("News.Example.TEST")).toBe("news.example.test");
  });

  it("rejects everything that is not a plain host", () => {
    const bad = [
      "192.0.2.1",
      "[2001:db8::1]",
      "2001:db8::1",
      "localhost",
      "a@b.example.test",
      "example.test:8080",
      "example.test/path",
      "https://example.test/a?b=c#d",
      "a".repeat(81),
      "-bad.example.test",
      "bad-.example.test",
      "exa mple.test",
      "example.test.",
      "",
      "www.com",
      "example.123",
      "a..example.test",
      "exämple.test",
    ];
    for (const b of bad) expect(parseHost(b), JSON.stringify(b)).toBeNull();
  });

  it("an 80 character host is the longest accepted", () => {
    const h = `${"a".repeat(63)}.${"b".repeat(13)}.cc`;
    expect(h.length).toBe(80);
    expect(parseHost(h)).toBe(h);
  });
});

describe("parseUtm", () => {
  it("accepts and lowercases", () => {
    expect(parseUtm("bluesky")).toBe("bluesky");
    expect(parseUtm("news-letter-3")).toBe("news-letter-3");
    expect(parseUtm("BlueSky")).toBe("bluesky");
  });
  it("rejects", () => {
    for (const b of ["a_b", "a b", "a".repeat(31), "x@y.test", "https://x.test", ""]) {
      expect(parseUtm(b), JSON.stringify(b)).toBeNull();
    }
  });
});

describe("parseBeacon", () => {
  it("unknown_keys_ignored_and_first_value_wins", () => {
    expect(parseBeacon("zzz=1&r=a.example.test&r=b.example.test&w=s&w=x", SITE)).toEqual({
      kind: "r",
      src: "a.example.test",
      vp: "s",
    });
  });

  it("w_outside_smlx_returns_null", () => {
    for (const w of ["", "S", "xl", "999", "0"]) expect(parseBeacon(`w=${w}`, SITE)).toBeNull();
    for (const w of ["s", "m", "l", "x"]) expect(parseBeacon(`w=${w}`, SITE)?.vp).toBe(w);
  });

  it("utm_wins_over_referrer", () => {
    expect(parseBeacon("r=news.example.test&u=Bluesky", SITE)).toEqual({ kind: "u", src: "bluesky", vp: "" });
  });

  it("self_host_referrer_is_internal", () => {
    expect(parseBeacon(`r=${SITE}`, SITE)).toEqual({ kind: "i", src: "", vp: "" });
    expect(parseBeacon("r=LINKS.example.test", SITE)?.kind).toBe("i");
  });

  it("a_present_but_invalid_key_drops_the_whole_beacon", () => {
    expect(parseBeacon("r=bad host&w=m", SITE)).toBeNull();
    expect(parseBeacon("r=news.example.test&u=a_b", SITE)).toBeNull();
    expect(parseBeacon("r=", SITE)).toBeNull();
  });

  it("empty_and_oversize_bodies", () => {
    expect(parseBeacon("", SITE)).toEqual({ kind: "d", src: "", vp: "" });
    expect(parseBeacon(`r=${"a".repeat(260)}.test`, SITE)).toBeNull();
  });
});

describe("geoOf", () => {
  function req(country: string | null, cf?: Record<string, unknown>): Request {
    const h = new Headers();
    if (country !== null) h.set("CF-IPCountry", country);
    return { headers: h, cf } as unknown as Request;
  }
  const cf = { regionCode: "EX", city: "Exampleton", country: "US" };

  it("country_only_at_country_level_even_when_cf_has_more", () => {
    expect(geoOf(req("US", cf), "country")).toEqual({ country: "US", region: "", city: "" });
  });
  it("region_at_region_and_city_at_city", () => {
    expect(geoOf(req("US", cf), "region")).toEqual({ country: "US", region: "EX", city: "" });
    expect(geoOf(req("US", cf), "city")).toEqual({ country: "US", region: "EX", city: "Exampleton" });
  });
  it("bad_countries_are_empty", () => {
    for (const c of ["XX", "T1", "us", "USA", "U", "", "1A"]) {
      expect(geoOf(req(c), "city").country, c).toBe("");
    }
  });
  it("country_falls_back_to_cf_country", () => {
    expect(geoOf(req(null, { country: "DE" }), "country").country).toBe("DE");
  });
  it("hostile_city_and_region_become_empty", () => {
    for (const city of ["<script>alert(1)</script>", "Ex\u0000ample", "a".repeat(61), "x\ny", "1234", "Ex;ample", ""]) {
      expect(geoOf(req("US", { regionCode: "EX", city }), "city").city, JSON.stringify(city)).toBe("");
    }
    for (const rc of ["ex", "EXAM", "E X", "", 5, null]) {
      expect(geoOf(req("US", { regionCode: rc, city: "Ok" }), "city").region, String(rc)).toBe("");
    }
    expect(geoOf(req("US", { city: "Saint-Jean d'Exemple St." }), "city").city).toBe("Saint-Jean d'Exemple St.");
    expect(geoOf(req("US", { city: "a".repeat(60) }), "city").city.length).toBe(60);
  });
  it("city_is_nfc_normalized", () => {
    const decomposed = "Zürich";
    expect(geoOf(req("CH", { city: decomposed }), "city").city).toBe("Zürich");
  });
  it("fine_location_fields_are_never_read", () => {
    const touched: string[] = [];
    const guarded = new Proxy(
      { country: "US", regionCode: "EX", city: "Exampleton" },
      {
        get(t, p) {
          if (typeof p === "string" && !["country", "regionCode", "city"].includes(p)) touched.push(p);
          return (t as Record<string, unknown>)[p as string];
        },
        has(_t, p) {
          touched.push(String(p));
          return false;
        },
        ownKeys() {
          touched.push("<enumerated>");
          return [];
        },
      },
    );
    geoOf(req("US", guarded as Record<string, unknown>), "city");
    expect(touched).toEqual([]);
  });
});
