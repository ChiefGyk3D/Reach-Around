// SPDX-License-Identifier: Apache-2.0
// The SITES loader (docs/analytics-design.md section 4). Hostile inputs first:
// a bad entry disables that one site; a broken whole config disables all of
// them (fail closed); nothing from the config is ever echoed into an error.
import { describe, expect, it } from "vitest";
import { MAX_SITES, killedSites, loadSites, siteForRequest } from "../../src/sites";

const good = {
  links: { hosts: ["links.example.test"], mode: "beacon", pages: ["home", "support"], link_ids: { "example-one": "home" } },
  blog: {
    hosts: ["blog.example.test"],
    mode: "edge",
    pages: "html-200",
    link_ids: { "support-cta": "*" },
    geo: "region",
    utm_allowlist: ["bluesky", "mail"],
    sources: ["bio-ig", "mail"],
    min_cell: 5,
    raw_keep_days: 3,
  },
};
const load = (v: unknown) => loadSites({ SITES: typeof v === "string" ? v : JSON.stringify(v) });
const ids = (r: ReturnType<typeof loadSites>) => r.sites.map((s) => s.id).sort();

describe("loadSites: valid config", () => {
  it("loads_both_sites_with_their_overrides", () => {
    const r = load(good);
    expect(r.errors).toEqual([]);
    expect(ids(r)).toEqual(["blog", "links"]);
    const blog = r.sites.find((s) => s.id === "blog")!;
    expect(blog).toMatchObject({ mode: "edge", pages: "html-200", geo: "region", minCell: 5, rawKeepDays: 3 });
    expect(blog.utmAllowlist).toEqual(["bluesky", "mail"]);
    expect(blog.sources).toEqual(["bio-ig", "mail"]);
    expect(blog.linkIds.get("support-cta")).toBe("*");
    const links = r.sites.find((s) => s.id === "links")!;
    expect((links.pages as ReadonlySet<string>).has("home")).toBe(true);
    expect(links.geo).toBeUndefined();
    expect(links.sources).toBeUndefined();
  });

  it("legacy_one_site_shorthand_still_works", () => {
    const r = loadSites({ SITE_HOST: "Links.Example.Test", PAGE_IDS: '["home"]', LINK_IDS: '["a","b"]' });
    expect(r.errors).toEqual([]);
    expect(r.sites).toHaveLength(1);
    expect(r.sites[0]).toMatchObject({ id: "links", hosts: ["links.example.test"], mode: "beacon" });
    expect([...r.sites[0]!.linkIds.keys()]).toEqual(["a", "b"]);
  });

  it("nothing_configured_means_no_sites_and_no_error", () => {
    expect(loadSites({})).toEqual({ sites: [], errors: [] });
    expect(loadSites({ SITES: "   " })).toEqual({ sites: [], errors: [] });
  });

  it("SITES_wins_over_the_shorthand", () => {
    const r = loadSites({ SITES: JSON.stringify({ blog: good.blog }), SITE_HOST: "links.example.test", PAGE_IDS: '["home"]' });
    expect(ids(r)).toEqual(["blog"]);
  });

  it("the_result_is_memoized_for_identical_text", () => {
    const text = JSON.stringify(good);
    expect(loadSites({ SITES: text })).toBe(loadSites({ SITES: text }));
  });
});

describe("loadSites: whole config broken fails closed", () => {
  it.each([
    ["not json", "{nope", "sites-json"],
    ["array", "[]", "sites-shape"],
    ["null", "null", "sites-shape"],
    ["string", '"x"', "sites-shape"],
    ["number", "5", "sites-shape"],
    ["empty object", "{}", "sites-empty"],
  ])("%s", (_n, text, code) => {
    const r = load(text);
    expect(r.sites).toEqual([]);
    expect(r.errors).toContain(code);
  });

  it("broken_SITES_does_not_fall_back_to_the_shorthand", () => {
    const r = loadSites({ SITES: "{nope", SITE_HOST: "links.example.test", PAGE_IDS: '["home"]' });
    expect(r.sites).toEqual([]);
  });

  it("more_than_the_site_cap_loads_nothing", () => {
    const many: Record<string, unknown> = {};
    for (let i = 0; i <= MAX_SITES; i++) many[`s${i}`] = { hosts: [`h${i}.example.test`], mode: "beacon", pages: ["home"] };
    const r = load(many);
    expect(r.sites).toEqual([]);
    expect(r.errors).toContain("too-many-sites");
    const ok: Record<string, unknown> = {};
    for (let i = 0; i < MAX_SITES; i++) ok[`s${i}`] = { hosts: [`h${i}.example.test`], mode: "beacon", pages: ["home"] };
    expect(load(ok).sites).toHaveLength(MAX_SITES);
  });

  it("a_huge_value_is_refused_before_it_is_parsed", () => {
    const r = load(JSON.stringify({ links: { ...good.links, pages: ["x".repeat(200_000)] } }));
    expect(r.sites).toEqual([]);
    expect(r.errors).toEqual(["sites-too-big"]);
  });

  it("deeply_nested_json_is_refused_without_throwing", () => {
    const deep = "[".repeat(5000) + "]".repeat(5000);
    expect(() => load(`{"links":${deep}}`)).not.toThrow();
    expect(load(`{"links":${deep}}`).sites).toEqual([]);
  });

  it("non_string_values_do_not_throw", () => {
    for (const v of [undefined, null, 5, {}, [], true]) {
      expect(() => loadSites({ SITES: v as unknown as string })).not.toThrow();
    }
  });
});

describe("loadSites: one bad site is disabled, the others stay", () => {
  it.each([
    ["no hosts", { ...good.links, hosts: [] }],
    ["hosts not an array", { ...good.links, hosts: "links.example.test" }],
    ["five hosts", { ...good.links, hosts: ["a.example.test", "b.example.test", "c.example.test", "d.example.test", "e.example.test"] }],
    ["upper case host", { ...good.links, hosts: ["Links.example.test"] }],
    ["scheme", { ...good.links, hosts: ["https://links.example.test"] }],
    ["port", { ...good.links, hosts: ["links.example.test:8443"] }],
    ["path", { ...good.links, hosts: ["links.example.test/x"] }],
    ["wildcard", { ...good.links, hosts: ["*.example.test"] }],
    ["userinfo", { ...good.links, hosts: ["u@links.example.test"] }],
    ["ip literal", { ...good.links, hosts: ["198.51.100.7"] }],
    ["single label", { ...good.links, hosts: ["localhost"] }],
    ["trailing dot", { ...good.links, hosts: ["links.example.test."] }],
    ["long host", { ...good.links, hosts: [`${"a".repeat(64)}.example.test`] }],
    ["same host twice in one site", { ...good.links, hosts: ["links.example.test", "links.example.test"] }],
    ["bad mode", { ...good.links, mode: "pixel" }],
    ["mode missing", { hosts: ["links.example.test"], pages: ["home"] }],
    ["beacon with html-200", { ...good.links, pages: "html-200" }],
    ["edge with a list", { ...good.blog, pages: ["home"] }],
    ["page id traversal", { ...good.links, pages: ["../etc"] }],
    ["page id slash", { ...good.links, pages: ["a/b"] }],
    ["page id percent", { ...good.links, pages: ["%2e%2e"] }],
    ["page id upper", { ...good.links, pages: ["Home"] }],
    ["page id empty", { ...good.links, pages: [""] }],
    ["page id too long", { ...good.links, pages: ["a".repeat(41)] }],
    ["too many pages", { ...good.links, pages: Array.from({ length: 201 }, (_, i) => `p${i}`) }],
    ["link id traversal", { ...good.links, link_ids: { "../x": "home" } }],
    ["link id slash", { ...good.links, link_ids: { "a/b": "home" } }],
    ["link id too long", { ...good.links, link_ids: { ["a".repeat(41)]: "home" } }],
    ["link id maps to unknown page", { ...good.links, link_ids: { one: "nope" } }],
    ["link id maps to non-string", { ...good.links, link_ids: { one: 5 } }],
    ["link_ids is an array", { ...good.links, link_ids: ["one"] }],
    ["too many link ids", { ...good.links, link_ids: Object.fromEntries(Array.from({ length: 201 }, (_, i) => [`l${i}`, "*"])) }],
    ["bad geo", { ...good.links, geo: "street" }],
    ["geo not a string", { ...good.links, geo: 3 }],
    ["utm not an array", { ...good.links, utm_allowlist: "bluesky" }],
    ["utm label bad", { ...good.links, utm_allowlist: ["Blue Sky"] }],
    ["utm label long", { ...good.links, utm_allowlist: ["a".repeat(31)] }],
    ["too many utm labels", { ...good.links, utm_allowlist: Array.from({ length: 41 }, (_, i) => `u${i}`) }],
    ["sources not an array", { ...good.links, sources: "bluesky" }],
    ["sources label upper", { ...good.links, sources: ["Bluesky"] }],
    ["sources label bad char", { ...good.links, sources: ["a_b"] }],
    ["sources label long", { ...good.links, sources: ["a".repeat(31)] }],
    ["sources label empty", { ...good.links, sources: [""] }],
    ["sources label not a string", { ...good.links, sources: [5] }],
    ["sources reserved other", { ...good.links, sources: ["other"] }],
    ["too many sources", { ...good.links, sources: Array.from({ length: 41 }, (_, i) => `s${i}`) }],
    ["min_cell string", { ...good.links, min_cell: "5" }],
    ["min_cell fraction", { ...good.links, min_cell: 2.5 }],
    ["min_cell zero", { ...good.links, min_cell: 0 }],
    ["min_cell negative", { ...good.links, min_cell: -3 }],
    ["min_cell infinite", JSON.parse('{"hosts":["links.example.test"],"mode":"beacon","pages":["home"],"min_cell":1e999}')],
    ["raw_keep_days zero", { ...good.links, raw_keep_days: 0 }],
    ["raw_keep_days string", { ...good.links, raw_keep_days: "7" }],
    ["raw_keep_days null", { ...good.links, raw_keep_days: null }],
    ["bots drop", { ...good.links, bots: "drop" }],
    ["unknown key", { ...good.links, colour: "red" }],
    ["site is an array", [1, 2]],
    ["site is null", null],
    ["site is a string", "links.example.test"],
  ])("%s", (_name, bad) => {
    const r = load({ blog: good.blog, links: bad });
    expect(ids(r)).toEqual(["blog"]);
    expect(r.errors.length).toBeGreaterThan(0);
    // A closed code, never the offending text.
    for (const e of r.errors) expect(e).toMatch(/^[a-z0-9:-]{1,60}$/);
  });

  it("huge_numbers_are_clamped_to_the_ceilings", () => {
    const r = load({ links: { ...good.links, min_cell: 1_000_000, raw_keep_days: 1_000_000 } });
    expect(r.errors).toEqual([]);
    expect(r.sites[0]).toMatchObject({ minCell: 50, rawKeepDays: 90 });
  });

  it("an_unsafe_integer_is_refused", () => {
    const r = load('{"links":{"hosts":["links.example.test"],"mode":"beacon","pages":["home"],"min_cell":99999999999999999999}}');
    expect(r.sites).toEqual([]);
  });
});

describe("loadSites: site ids", () => {
  const badIds = ["../x", "..", ".", "a/b", "A", "-a", "a b", "a".repeat(21), "", "__proto__", "a\u0000b", "%2e%2e", "ünï"];
  it.each(badIds)("id %j is refused and not echoed", (id) => {
    const text = `{${JSON.stringify(id)}:${JSON.stringify(good.links)},"blog":${JSON.stringify(good.blog)}}`;
    const r = load(text);
    expect(ids(r)).toEqual(["blog"]);
    if (id !== "") expect(r.errors.join(",")).not.toContain(id);
    expect(r.errors).toContain("bad-site-id");
  });

  it("prototype_looking_but_valid_slugs_are_ordinary_ids", () => {
    const r = load({ constructor: good.links, tostring: good.blog });
    expect(ids(r)).toEqual(["constructor", "tostring"]);
    expect(Object.prototype.hasOwnProperty.call({}, "constructor")).toBe(false);
  });

  it("a_20_character_slug_is_fine", () => {
    const id = "a" + "b".repeat(19);
    expect(ids(load({ [id]: good.links }))).toEqual([id]);
  });

  it("a_duplicate_site_id_disables_that_id_and_keeps_the_rest", () => {
    const evil = JSON.stringify({ ...good.links, hosts: ["evil.example.test"] });
    const text = `{"links":${JSON.stringify(good.links)},"links":${evil},"blog":${JSON.stringify(good.blog)}}`;
    const r = load(text);
    expect(ids(r)).toEqual(["blog"]);
    expect(r.errors).toContain("dup-site-id");
  });

  it("a_duplicate_key_inside_a_site_disables_the_site", () => {
    const text = `{"links":{"hosts":["links.example.test"],"hosts":["evil.example.test"],"mode":"beacon","pages":["home"]},"blog":${JSON.stringify(good.blog)}}`;
    const r = load(text);
    expect(ids(r)).toEqual(["blog"]);
    expect(r.errors).toContain("site-bad:links");
  });

  it("keys_that_look_duplicated_only_inside_strings_are_not_duplicates", () => {
    const text = '{"links":{"hosts":["links.example.test"],"mode":"beacon","pages":["a\\",\\"mode\\":\\"x"]}}';
    const r = load(text);
    expect(r.sites).toEqual([]); // the page id is invalid, but it is NOT a duplicate-key error
    expect(r.errors).not.toContain("dup-site-id");
    expect(r.errors).toContain("site-bad:links");
  });
});

describe("loadSites: a host belongs to one site only", () => {
  it("duplicate_host_disables_both_sites", () => {
    const r = load({
      links: good.links,
      blog: { ...good.blog, hosts: ["blog.example.test", "links.example.test"] },
      third: { hosts: ["third.example.test"], mode: "beacon", pages: ["home"] },
    });
    expect(ids(r)).toEqual(["third"]);
    expect(r.errors).toContain("dup-host");
  });

  it("the_duplicate_cannot_be_hidden_by_case_because_hosts_must_be_lower_case", () => {
    const r = load({ links: good.links, blog: { ...good.blog, hosts: ["LINKS.example.test"] } });
    expect(ids(r)).toEqual(["links"]);
  });
});

describe("legacy shorthand hostile inputs", () => {
  it.each([
    ["bad host", { SITE_HOST: "links.example.test/x", PAGE_IDS: '["home"]' }],
    ["port", { SITE_HOST: "links.example.test:99", PAGE_IDS: '["home"]' }],
    ["no host", { PAGE_IDS: '["home"]' }],
    ["bad page", { SITE_HOST: "links.example.test", PAGE_IDS: '["../x"]' }],
    ["page ids not json", { SITE_HOST: "links.example.test", PAGE_IDS: "{" }],
  ])("%s", (_n, env) => {
    expect(loadSites(env).sites).toEqual([]);
  });

  it("no_PAGE_IDS_is_a_site_with_no_pages_and_link_ids_may_be_missing", () => {
    const r = loadSites({ SITE_HOST: "links.example.test" });
    expect(r.errors).toEqual([]);
    expect(r.sites).toHaveLength(1);
    expect((r.sites[0]!.pages as ReadonlySet<string>).size).toBe(0);
    expect(r.sites[0]!.linkIds.size).toBe(0);
  });

  it("legacy_link_ids_that_are_not_strings_or_too_long_are_dropped_not_fatal", () => {
    const r = loadSites({ SITE_HOST: "links.example.test", PAGE_IDS: "[]", LINK_IDS: JSON.stringify(["ok", 5, "", "a/b", "a".repeat(41)]) });
    expect([...r.sites[0]!.linkIds.keys()]).toEqual(["ok"]);
  });
});

describe("siteForRequest", () => {
  const sites = load(good).sites;
  const req = (h: Record<string, string>) => new Request("https://worker.example.test/e/v/home", { method: "POST", headers: h });
  it("matches_by_origin_or_ping_from_host", () => {
    expect(siteForRequest(sites, req({ Origin: "https://links.example.test" }))?.id).toBe("links");
    expect(siteForRequest(sites, req({ "Ping-From": "https://blog.example.test/post" }))?.id).toBe("blog");
  });
  it("no_match_for_other_hosts_ports_or_missing_headers", () => {
    expect(siteForRequest(sites, req({}))).toBeNull();
    expect(siteForRequest(sites, req({ Origin: "https://other.example.test" }))).toBeNull();
    expect(siteForRequest(sites, req({ Origin: "https://links.example.test:8443" }))).toBeNull();
    expect(siteForRequest(sites, req({ Origin: "https://links.example.test.evil.test" }))).toBeNull();
  });
  it("an_oversize_header_is_never_parsed", () => {
    expect(siteForRequest(sites, req({ Origin: "https://links.example.test/" + "a".repeat(300) }))).toBeNull();
  });
});

describe("killedSites", () => {
  it("parses_a_comma_list_and_ignores_junk", () => {
    expect([...killedSites("blog, links ,,../x,A,blog")].sort()).toEqual(["blog", "links"]);
    expect(killedSites(undefined).size).toBe(0);
    expect(killedSites(5 as unknown as string).size).toBe(0);
    expect(killedSites("x".repeat(100_000)).size).toBe(0);
  });
});
