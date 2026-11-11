// SPDX-License-Identifier: Apache-2.0
// Source-attributed clicks (issue #329): the click ping carries one closed-set
// label, the Worker keeps it on the click row, and nothing else.
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import { clickSource, handleClick, type Env } from "../src/click";

const SITES = JSON.stringify({
  links: {
    hosts: ["links.example.test"],
    mode: "beacon",
    pages: ["home"],
    link_ids: { twitch: "home", kick: "home" },
    sources: ["instagram", "bluesky"],
  },
  nosrc: { hosts: ["plain.example.test"], mode: "beacon", pages: ["home"], link_ids: { twitch: "home" } },
  empty: { hosts: ["empty.example.test"], mode: "beacon", pages: ["home"], link_ids: { twitch: "home" }, sources: [] },
});
const UA = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0";
let n = 0;
const env = (): Env => ({
  DB: testEnv.DB,
  SITES,
  CLICK_LIMITER: { limit: async () => ({ success: true }) },
});
const click = (qs: string, host = "links.example.test", id = "twitch") =>
  new Request(`https://worker.example.test/e/c/${id}${qs}`, {
    method: "POST",
    headers: { "User-Agent": UA, "CF-Connecting-IP": `198.51.100.${++n % 250}`, "Ping-From": `https://${host}/` },
  });
const srcs = async () =>
  (await testEnv.DB.prepare("SELECT src FROM click_events ORDER BY id").all<{ src: string | null }>()).results.map((r) => r.src);

beforeEach(async () => {
  await testEnv.DB.prepare("DELETE FROM click_events").run();
});

describe("clickSource", () => {
  const u = (qs: string) => new URL(`https://w.example.test/e/c/x${qs}`);
  it("keeps_a_listed_label_lowercased", () => {
    expect(clickSource(u("?s=instagram"), ["instagram"])).toBe("instagram");
    expect(clickSource(u("?s=Instagram"), ["instagram"])).toBe("instagram");
  });
  it("folds_an_unlisted_valid_label_to_other_and_never_keeps_its_text", () => {
    expect(clickSource(u("?s=tiktok"), ["instagram"])).toBe("other");
    expect(clickSource(u("?s=tiktok"), [])).toBe("other");
  });
  it("is_null_without_a_sources_list_or_without_s", () => {
    expect(clickSource(u("?s=instagram"), undefined)).toBeNull();
    expect(clickSource(u(""), ["instagram"])).toBeNull();
  });
  it.each([
    ["empty", "?s="],
    ["too_long", `?s=${"a".repeat(31)}`],
    ["repeated", "?s=instagram&s=bluesky"],
    ["space", "?s=a%20b"],
    ["sql", "?s=%27%3B%20DROP%20TABLE%20click_events%3B--"],
    ["ipv4", "?s=198.51.100.7"],
    ["ipv6", "?s=2001:db8::1"],
    ["email", "?s=a%40b.test"],
    ["path", "?s=..%2F..%2Fetc"],
    ["null_byte", "?s=a%00b"],
    ["kelvin_sign", "?s=%E2%84%AAick"],
    ["dotless_i", "?s=%C4%B1nstagram"],
    ["unicode", "?s=%E3%82%A4"],
    ["script", "?s=%3Cscript%3E"],
  ])("hostile_%s_is_null", (_n, qs) => {
    expect(clickSource(u(qs), ["instagram"])).toBeNull();
  });
});

describe("the click route stores the source", () => {
  it("stores_a_listed_label_and_folds_everything_else", async () => {
    await handleClick(click("?s=instagram"), env());
    await handleClick(click("?s=bluesky"), env());
    await handleClick(click("?s=tiktok"), env());
    await handleClick(click("?s=198.51.100.7"), env());
    await handleClick(click(""), env());
    expect(await srcs()).toEqual(["instagram", "bluesky", "other", null, null]);
  });

  it("a_site_without_sources_stores_no_source", async () => {
    await handleClick(click("?s=instagram", "plain.example.test"), env());
    expect(await srcs()).toEqual([null]);
  });

  it("an_empty_list_folds_every_valid_label_to_other", async () => {
    await handleClick(click("?s=instagram", "empty.example.test"), env());
    expect(await srcs()).toEqual(["other"]);
  });

  it("a_hostile_label_never_reaches_the_table_in_any_form", async () => {
    const hostile = ["x'); DROP TABLE click_events;--", "198.51.100.9", "tiktok-secret-1"];
    for (const h of hostile) await handleClick(click(`?s=${encodeURIComponent(h)}`), env());
    const all = await testEnv.DB.prepare("SELECT * FROM click_events").all();
    const dump = JSON.stringify(all.results);
    for (const h of hostile) expect(dump).not.toContain(h);
    expect((await testEnv.DB.prepare("SELECT COUNT(*) AS c FROM click_events").first<{ c: number }>())?.c).toBeGreaterThanOrEqual(0);
  });

  it("rounds_the_click_time_down_to_the_hour", async () => {
    await handleClick(click("?s=instagram"), env());
    const r = await testEnv.DB.prepare("SELECT ts FROM click_events").first<{ ts: number }>();
    expect(r!.ts % 3_600_000).toBe(0);
    expect(Date.now() - r!.ts).toBeLessThan(3_600_000 + 5_000);
  });

  it("the_table_has_no_identifier_column", async () => {
    const cols = (await testEnv.DB.prepare("PRAGMA table_info(click_events)").all<{ name: string }>()).results.map((c) => c.name);
    expect(cols.sort()).toEqual(["country", "destination_kind", "id", "link_id", "site", "src", "ts"]);
    for (const c of cols) expect(c).not.toMatch(/ip|addr|agent|ua|cookie|visitor|session|hash|token|referr/i);
  });

  it("the_database_refuses_a_free_text_source", async () => {
    const bad = (v: string) =>
      testEnv.DB.prepare("INSERT INTO click_events (ts, link_id, src) VALUES (0, 'x', ?)").bind(v).run();
    await expect(bad("has space")).rejects.toThrow();
    await expect(bad("UPPER")).rejects.toThrow();
    await expect(bad("a".repeat(31))).rejects.toThrow();
    await expect(bad("")).rejects.toThrow();
    await bad("instagram");
  });

  it("click_cap_folding_stays_closed_many_labels_make_at_most_listed_plus_other", async () => {
    for (let i = 0; i < 40; i++) await handleClick(click(`?s=label${i}`), env());
    const d = await testEnv.DB.prepare("SELECT DISTINCT src FROM click_events").all<{ src: string }>();
    expect(d.results.map((r) => r.src)).toEqual(["other"]);
  });
});

describe("no source-by-country pair", () => {
  const withCountry = (qs: string) => {
    const r = click(qs);
    const h = new Headers(r.headers);
    h.set("CF-IPCountry", "DE");
    return new Request(r.url, { method: "POST", headers: h });
  };
  const pairs = async () =>
    (await testEnv.DB.prepare("SELECT src, country FROM click_events ORDER BY id").all<{ src: string | null; country: string | null }>()).results;

  it("a_raw_row_never_keeps_a_country_with_or_without_a_source", async () => {
    await handleClick(withCountry("?s=instagram"), env());
    await handleClick(withCountry(""), env());
    await handleClick(withCountry("?s=tiktok"), env());
    expect(await pairs()).toEqual([
      { src: "instagram", country: null },
      { src: null, country: null },
      { src: "other", country: null },
    ]);
  });

  it("the_database_refuses_a_row_with_both", async () => {
    await expect(
      testEnv.DB.prepare("INSERT INTO click_events (ts, link_id, country, src) VALUES (0, 'x', 'DE', 'instagram')").run(),
    ).rejects.toThrow();
  });
});
