// SPDX-License-Identifier: Apache-2.0
// The click route resolves the site the way the view route does
// (docs/analytics-design.md section 6.1).
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import { handleClick, type Env } from "../src/click";

const SITES = JSON.stringify({
  links: { hosts: ["links.example.test"], mode: "beacon", pages: ["home"], link_ids: { "example-one": "home", shared: "home" } },
  blog: { hosts: ["blog.example.test"], mode: "edge", pages: "html-200", link_ids: { "support-cta": "*", shared: "*" } },
});
const UA = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0";

function limiter(limit = 20) {
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
const env = (over: Partial<Env> = {}): Env => ({ DB: testEnv.DB, SITES, CLICK_LIMITER: limiter(), ...over });
const click = (id: string, host: string | null, extra: Record<string, string> = {}, path = `/e/c/${id}`) =>
  new Request(`https://worker.example.test${path}`, {
    method: "POST",
    headers: { "User-Agent": UA, "CF-Connecting-IP": "198.51.100.7", ...(host ? { "Ping-From": `https://${host}/` } : {}), ...extra },
  });
const rows = async () => (await testEnv.DB.prepare("SELECT link_id, site FROM click_events ORDER BY id").all<{ link_id: string; site: string }>()).results;

beforeEach(async () => {
  await testEnv.DB.prepare("DELETE FROM click_events").run();
});

describe("per-site clicks", () => {
  it("a_listed_id_records_under_the_site_of_the_matched_host", async () => {
    await handleClick(click("example-one", "links.example.test"), env());
    await handleClick(click("support-cta", "blog.example.test"), env());
    expect(await rows()).toEqual([
      { link_id: "example-one", site: "links" },
      { link_id: "support-cta", site: "blog" },
    ]);
  });

  it("an_id_listed_for_another_site_records_nothing", async () => {
    await handleClick(click("support-cta", "links.example.test"), env());
    await handleClick(click("example-one", "blog.example.test"), env());
    expect(await rows()).toEqual([]);
  });

  it("identical_ids_on_two_hosts_are_separate_rows_never_merged", async () => {
    await handleClick(click("shared", "links.example.test", { "CF-Connecting-IP": "198.51.100.1" }), env());
    await handleClick(click("shared", "blog.example.test", { "CF-Connecting-IP": "198.51.100.2" }), env());
    expect(await rows()).toEqual([
      { link_id: "shared", site: "links" },
      { link_id: "shared", site: "blog" },
    ]);
  });

  it("the_site_is_never_read_from_the_client_beyond_the_matched_host", async () => {
    await handleClick(
      click("example-one", "links.example.test", { "X-Site": "blog", Referer: "https://blog.example.test/", Origin: "https://links.example.test" }, "/e/c/example-one?site=blog"),
      env(),
    );
    expect(await rows()).toEqual([{ link_id: "example-one", site: "links" }]);
  });

  it("unknown_and_missing_hosts_record_nothing", async () => {
    await handleClick(click("example-one", "other.example.test"), env());
    await handleClick(click("example-one", null), env());
    expect(await rows()).toEqual([]);
  });

  it("the_legacy_shorthand_records_as_links", async () => {
    const legacy = env({ SITES: undefined, SITE_HOST: "links.example.test", LINK_IDS: '["example-one"]' });
    await handleClick(click("example-one", "links.example.test"), legacy);
    expect(await rows()).toEqual([{ link_id: "example-one", site: "links" }]);
  });

  it("a_duplicate_host_in_SITES_disables_both_sites", async () => {
    const dup = JSON.stringify({
      links: { hosts: ["links.example.test"], mode: "beacon", pages: ["home"], link_ids: { a: "*" } },
      blog: { hosts: ["links.example.test"], mode: "beacon", pages: ["home"], link_ids: { a: "*" } },
    });
    await handleClick(click("a", "links.example.test"), env({ SITES: dup }));
    expect(await rows()).toEqual([]);
  });

  it("CLICKS_KILL_SITES_turns_off_one_site_only_and_does_no_io_for_it", async () => {
    const e = env({ CLICKS_KILL_SITES: "blog" });
    const lim = e.CLICK_LIMITER as ReturnType<typeof limiter>;
    await handleClick(click("support-cta", "blog.example.test"), e);
    expect(lim.keys).toEqual([]);
    expect(await rows()).toEqual([]);
    await handleClick(click("example-one", "links.example.test"), e);
    expect(await rows()).toHaveLength(1);
  });

  it("the_limiter_key_is_the_same_for_one_client_on_every_site", async () => {
    const e = env();
    await handleClick(click("example-one", "links.example.test"), e);
    await handleClick(click("support-cta", "blog.example.test"), e);
    const keys = (e.CLICK_LIMITER as ReturnType<typeof limiter>).keys;
    expect(keys).toHaveLength(2);
    expect(new Set(keys).size).toBe(1);
    expect(JSON.stringify(keys)).not.toContain("198.51.100.7");
  });
});
