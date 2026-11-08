// SPDX-License-Identifier: Apache-2.0
// Two sites on one collector (docs/analytics-design.md sections 3, 4, 10, 13).
import { beforeEach, describe, expect, it } from "vitest";
import { dayKey, handleView, visitorHash } from "../../src/view";
import { DB, IP4, NOW, SECRET, beacon, budgets, clearAll, dumpDatabase, fakeLimiter, makeEnv, rows, seen, spyDb } from "./helpers";

const SITES = JSON.stringify({
  links: { hosts: ["links.example.test"], mode: "beacon", pages: ["home", "support"], link_ids: { "example-one": "home" } },
  blog: { hosts: ["blog.example.test"], mode: "beacon", pages: ["post-one"], geo: "country", utm_allowlist: ["mail"], sources: ["bio-ig", "newsletter"] },
  edgy: { hosts: ["edge.example.test"], mode: "edge", pages: "html-200" },
});
const env = (over: Record<string, unknown> = {}) =>
  makeEnv({ SITES, SITE_HOST: undefined, PAGE_IDS: undefined, ...over } as never);
const from = (host: string, o: Parameters<typeof beacon>[0] = {}) =>
  beacon({ ...o, headers: { Origin: `https://${host}`, ...(o.headers ?? {}) } });
const ipN = (n: number) => `198.51.100.${n}`;
const post = (e: ReturnType<typeof env>, host: string, page: string, n = 7, o: Parameters<typeof beacon>[0] = {}) =>
  handleView(from(host, { path: `/e/v/${page}`, ...o, headers: { "CF-Connecting-IP": ipN(n), ...(o.headers ?? {}) } }), e, NOW);

beforeEach(clearAll);

describe("the site is chosen by the matched host", () => {
  it("each_origin_lands_in_its_own_site", async () => {
    const e = env();
    await post(e, "links.example.test", "home", 1);
    await post(e, "blog.example.test", "post-one", 2);
    expect((await rows()).map((r) => [r.site, r.page])).toEqual([
      ["links", "home"],
      ["blog", "post-one"],
    ]);
  });

  it("a_client_cannot_name_the_site_in_the_body_headers_path_or_query", async () => {
    const e = env();
    const res = await handleView(
      from("links.example.test", {
        path: "/e/v/home?site=blog&s=blog",
        body: "site=blog&s=blog&r=news.example.test",
        headers: { "X-Site": "blog", "X-Forwarded-Host": "blog.example.test", Referer: "https://blog.example.test/", Host: "blog.example.test" },
      }),
      e,
      NOW,
    );
    expect(res.status).toBe(204);
    expect((await rows()).map((r) => r.site)).toEqual(["links"]);
  });

  it("a_page_of_another_site_records_nothing", async () => {
    const e = env();
    await post(e, "blog.example.test", "support");
    await post(e, "links.example.test", "post-one");
    expect(await rows()).toEqual([]);
  });

  it("unknown_hosts_lookalikes_and_ports_record_nothing", async () => {
    const e = env();
    for (const host of ["other.example.test", "links.example.test.evil.test", "evil-links.example.test", "links.example.test:8443", "links.example.test.", "blog.example.test@evil.test", "LINKS.EXAMPLE.TEST"]) {
      await post(e, host, "home");
    }
    expect((await rows()).map((r) => r.site)).toEqual(["links"]); // only the upper-case spelling of the real host (URL parsing lower-cases it)
  });

  it("an_edge_mode_site_ignores_the_beacon_route", async () => {
    await post(env(), "edge.example.test", "home");
    expect(await rows()).toEqual([]);
  });

  it("duplicate_hosts_in_the_config_disable_both_sites", async () => {
    const dup = JSON.stringify({
      links: { hosts: ["links.example.test"], mode: "beacon", pages: ["home"] },
      blog: { hosts: ["links.example.test"], mode: "beacon", pages: ["home"] },
    });
    const e = env({ SITES: dup });
    await post(e, "links.example.test", "home");
    expect(await rows()).toEqual([]);
  });

  it("a_broken_SITES_records_nothing_even_with_the_shorthand_set", async () => {
    const e = env({ SITES: "{nope", SITE_HOST: "links.example.test", PAGE_IDS: '["home"]' });
    await post(e, "links.example.test", "home");
    expect(await rows()).toEqual([]);
  });

  it("the_legacy_shorthand_records_as_links", async () => {
    const e = makeEnv();
    await handleView(beacon(), e, NOW);
    expect((await rows()).map((r) => r.site)).toEqual(["links"]);
  });

  it("the_site_geo_override_wins_over_the_global_level", async () => {
    const e = env({ GEO_LEVEL: "city" });
    const cf = { regionCode: "CA", city: "Sentinelville" };
    await post(e, "links.example.test", "home", 1, { cf });
    await post(e, "blog.example.test", "post-one", 2, { cf });
    const [a, b] = await rows();
    expect([a?.region, a?.city]).toEqual(["CA", "Sentinelville"]);
    expect([b?.region, b?.city]).toEqual(["", ""]);
  });

  it("an_internal_referrer_is_a_host_of_the_same_site_only", async () => {
    const e = env();
    await post(e, "links.example.test", "home", 1, { body: "r=links.example.test" });
    await post(e, "links.example.test", "home", 2, { body: "r=blog.example.test" });
    expect((await rows()).map((r) => [r.src_kind, r.src])).toEqual([
      ["i", ""],
      ["r", "blog.example.test"],
    ]);
  });
});

describe("raw timestamps are rounded to the hour (D-A1)", () => {
  it("ts_is_the_start_of_the_hour", async () => {
    const e = env();
    for (const [i, t] of ["2027-03-14T12:00:30Z", "2027-03-14T12:59:59.999Z", "2027-03-14T13:00:00Z"].entries()) {
      await handleView(from("links.example.test", { headers: { "CF-Connecting-IP": ipN(i + 1) } }), e, Date.parse(t));
    }
    expect((await rows()).map((r) => new Date(r.ts).toISOString())).toEqual([
      "2027-03-14T12:00:00.000Z",
      "2027-03-14T12:00:00.000Z",
      "2027-03-14T13:00:00.000Z",
    ]);
  });
});

describe("the network class is not kept in raw rows (D-A7)", () => {
  it("net_is_unknown_in_every_stored_row", async () => {
    await post(env(), "links.example.test", "home", 1, { cf: { asn: 64500 } });
    expect((await rows()).map((r) => r.net)).toEqual(["unknown"]);
    expect(await dumpDatabase()).not.toContain("64500");
  });
});

describe("per-site kill switch", () => {
  it("killing_one_site_leaves_the_other_and_a_killed_site_does_no_io", async () => {
    const spy = spyDb();
    const e = env({ VIEWS_KILL_SITES: "blog", DB: spy.DB, VIEW_LIMITER: fakeLimiter() });
    await post(e, "blog.example.test", "post-one", 1);
    expect(spy.calls.prepare + spy.calls.batch).toBe(0);
    expect((e.VIEW_LIMITER as ReturnType<typeof fakeLimiter>).calls).toBe(0);
    await post(e, "links.example.test", "home", 2);
    expect((await rows()).map((r) => r.site)).toEqual(["links"]);
  });

  it("the_list_is_read_on_every_request_and_junk_entries_kill_nothing", async () => {
    await post(env({ VIEWS_KILL_SITES: "../x,A,,links.example.test" }), "links.example.test", "home", 1);
    await post(env({ VIEWS_KILL_SITES: "links" }), "links.example.test", "home", 2);
    await post(env({ VIEWS_KILL_SITES: "" }), "links.example.test", "home", 3);
    expect(await rows()).toHaveLength(2);
  });
});

describe("budgets are per site (A4)", () => {
  it("spending_one_sites_hourly_budget_leaves_the_other_untouched", async () => {
    const e = env({ VIEWS_PER_HOUR: "3", ACCOUNT_VIEWS_PER_HOUR: "100" });
    for (let i = 1; i <= 6; i++) await post(e, "links.example.test", "home", i);
    expect((await rows()).filter((r) => r.site === "links")).toHaveLength(3);
    await post(e, "blog.example.test", "post-one", 50);
    await post(e, "blog.example.test", "post-one", 51);
    expect((await rows()).filter((r) => r.site === "blog")).toHaveLength(2);
    const keys = (await budgets()).map((b) => b.key);
    expect(keys.filter((k) => k.startsWith("bud:") && k.endsWith(":links:pv"))).toHaveLength(1);
    expect(keys.filter((k) => k.startsWith("bud:") && k.endsWith(":blog:pv"))).toHaveLength(1);
  });

  it("one_client_spreading_across_sites_is_still_bounded_by_one_client_cap", async () => {
    const e = env({ VIEWS_PER_HOUR: "10", ACCOUNT_VIEWS_PER_HOUR: "100" }); // client cap = 5
    for (let i = 0; i < 6; i++) {
      await post(e, "links.example.test", "home", 9);
      await post(e, "blog.example.test", "post-one", 9);
    }
    expect(await rows()).toHaveLength(5);
    // A different client is unaffected.
    await post(e, "blog.example.test", "post-one", 10);
    expect(await rows()).toHaveLength(6);
  });

  it("the_account_wide_client_counter_is_not_the_stored_daily_code", async () => {
    const e = env();
    await post(e, "links.example.test", "home", 9);
    await post(e, "blog.example.test", "post-one", 9);
    const h = (await seen()).map((x) => x.h.slice(0, 16));
    const keys = (await budgets()).filter((b) => b.key.startsWith("cap:")).map((b) => b.key.split(":")[3]);
    expect(keys).toHaveLength(3); // links, blog and the account-wide counter
    for (const k of keys) expect(h).not.toContain(k);
  });

  it("the_burst_limiter_key_is_the_same_for_one_client_on_every_site", async () => {
    const lim = fakeLimiter();
    const e = env({ VIEW_LIMITER: lim });
    await post(e, "links.example.test", "home", 9);
    await post(e, "blog.example.test", "post-one", 9);
    expect(lim.keys).toHaveLength(2);
    expect(new Set(lim.keys).size).toBe(1);
  });

  it("a_shared_account_ceiling_sits_above_the_site_budgets", async () => {
    const e = env({ VIEWS_PER_HOUR: "50", ACCOUNT_VIEWS_PER_HOUR: "3" });
    for (let i = 1; i <= 3; i++) await post(e, "links.example.test", "home", i);
    for (let i = 4; i <= 6; i++) await post(e, "blog.example.test", "post-one", i);
    expect(await rows()).toHaveLength(3);
    // The ceiling counted exactly what was accepted: the refused ones consumed nothing.
    const all = (await budgets()).find((b) => b.key.endsWith(":_all:pv"));
    expect(all?.value).toBe("3");
  });

  it("a_bad_account_ceiling_falls_back_to_the_default_and_never_means_unlimited", async () => {
    for (const bad of ["abc", "-1", "1e9", "999999999999"]) {
      await clearAll();
      const e = env({ VIEWS_PER_HOUR: "100000", ACCOUNT_VIEWS_PER_HOUR: bad });
      for (let i = 1; i <= 20; i++) await post(e, "links.example.test", "home", i);
      expect((await rows()).length, bad).toBe(20);
      expect((await budgets()).some((b) => b.key.endsWith(":_all:pv")), bad).toBe(true);
    }
  });
});

describe("daily codes differ per site and per day (A7)", () => {
  it("four_codes_are_four_different_values", async () => {
    const bytes = Uint8Array.from(atob(SECRET), (c) => c.charCodeAt(0));
    const codes = new Set<string>();
    for (const site of ["links", "blog"]) {
      for (const date of ["2027-03-14", "2027-03-15"]) {
        codes.add(await visitorHash(await dayKey(bytes, SECRET, date, site), "198.51.100.7", 64));
      }
    }
    expect(codes.size).toBe(4);
  });

  it("one_visitor_of_both_sites_is_counted_once_on_each_with_unrelated_stored_codes", async () => {
    const e = env();
    await post(e, "links.example.test", "home", 7);
    await post(e, "blog.example.test", "post-one", 7);
    await post(e, "links.example.test", "home", 7, { headers: { "CF-Connecting-IP": ipN(7) } });
    const r = await rows();
    expect(r.map((x) => [x.site, x.first])).toEqual([
      ["links", 1],
      ["blog", 1],
      ["links", 0],
    ]);
    const s = await seen();
    expect(s).toHaveLength(2);
    expect(new Set(s.map((x) => x.h)).size).toBe(2);
  });

  it("visitor_seen_still_has_no_column_in_common_with_events", async () => {
    const cols = (await DB.prepare("SELECT name FROM pragma_table_info('visitor_seen')").all<{ name: string }>()).results.map((c) => c.name);
    expect(cols).toEqual(["day", "h"]);
  });
});

describe("no raw value reaches the database on either site", () => {
  it("sentinels_in_headers_and_body_are_not_stored", async () => {
    const e = env();
    const sentinel = "SENTINEL-UA-7f3a";
    for (const host of ["links.example.test", "blog.example.test"]) {
      await handleView(
        from(host, {
          path: `/e/v/${host.startsWith("links") ? "home" : "post-one"}`,
          body: "r=news.example.test&u=mail",
          headers: { "User-Agent": `Mozilla/5.0 ${sentinel} Chrome/128.0.0.1`, "Accept-Language": "xx-SENTINEL-LANG", "CF-Connecting-IP": IP4 },
        }),
        e,
        NOW,
      );
    }
    const dump = await dumpDatabase();
    expect(dump).not.toContain(sentinel);
    expect(dump).not.toContain("SENTINEL-LANG");
    expect(dump).not.toContain(IP4);
    expect(dump.length).toBeGreaterThan(100); // something real was scanned
  });
});

describe("the s source label", () => {
  const src = async (host: string, page: string, body: string, n = 3) => {
    await post(env(), host, page, n, { body });
    const all = await rows();
    return all.map((r) => [r.src_kind, r.src]).at(-1);
  };

  it("a_listed_label_is_stored_and_beats_utm_and_referrer", async () => {
    expect(await src("blog.example.test", "post-one", "s=bio-ig&u=mail&r=news.example.test")).toEqual(["u", "bio-ig"]);
  });

  it("an_unlisted_label_is_stored_as_other_and_the_raw_text_is_nowhere_in_the_database", async () => {
    expect(await src("blog.example.test", "post-one", "s=Sneaky-Label&u=mail")).toEqual(["u", "other"]);
    expect((await dumpDatabase()).toLowerCase()).not.toContain("sneaky");
  });

  it("a_site_without_sources_never_reads_s", async () => {
    expect(await src("links.example.test", "home", "s=bio-ig&r=news.example.test")).toEqual(["r", "news.example.test"]);
  });

  it("a_label_listed_on_another_site_is_other_here", async () => {
    expect(await src("blog.example.test", "post-one", "s=example-one")).toEqual(["u", "other"]);
  });

  it("hostile_values_record_nothing", async () => {
    const e = env();
    let n = 10;
    for (const bad of ["a_b", "x".repeat(31), "%00", "%2e%2e", "a%20b", "%256dail", ""]) {
      await post(e, "blog.example.test", "post-one", n++, { body: `s=${bad}` });
    }
    expect(await rows()).toEqual([]);
  });

  it("an_explicit_empty_sources_list_still_closes_the_policy", async () => {
    const closed = JSON.stringify({ links: { hosts: ["links.example.test"], mode: "beacon", pages: ["home"], sources: [] } });
    const e = env({ SITES: closed, UTM_ALLOWLIST: "mail" });
    await post(e, "links.example.test", "home", 21, { body: "s=mail&u=mail&r=news.example.test" });
    expect((await rows()).map((r) => [r.src_kind, r.src])).toEqual([["u", "other"]]);
  });

  it("case_folding_oddities_record_nothing", async () => {
    const e = env();
    let n = 40;
    for (const bad of ["%E2%84%AA", "ma%C4%B1l", "%C5%BFo", "%C4%B0nfo"]) {
      await post(e, "blog.example.test", "post-one", n++, { body: `s=${bad}` });
    }
    expect(await rows()).toEqual([]);
  });
});
