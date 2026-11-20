// SPDX-License-Identifier: Apache-2.0
// Hardening: bound click write amplification (Refs #566, #610). An enforced, atomic per-site daily
// admission ceiling, charged at the rows one click really writes, checked before any click write.
// The day is sliced by hour, room is kept for each link's first click of the day, and the charge is
// kept in two lanes (verified and unverified) with the unverified lane capped.
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import { handleClick, type Env } from "../src/click";
import {
  CLICK_ADMIT_CEILING,
  CLICK_DROP_COUNT_CAP,
  CLICK_ACCOUNT_DAILY_WRITES,
  CLICK_FIRST_RESERVE_PERCENT,
  CLICK_HOUR_SLICES,
  CLICK_UNVERIFIED_PERCENT,
  siteShare,
  admitClick,
  CLICK_SETUP_FLAG_WRITES,
  clickAccountWrites,
  clickLimits,
  clickWriteCost,
  parseClickCeiling,
  parseLaneCapFlag,
} from "../src/clickceiling";
import { CLICK_DAILY_WRITES } from "../src/kit/budgets";
import { HOUR_BUDGET, LH_HOUR_BUDGET } from "../src/clickfunnel";

const DB = testEnv.DB;
const SITES = JSON.stringify({
  links: {
    hosts: ["links.example.test"], mode: "beacon", pages: ["home"], link_ids: { twitch: "home" },
    sources: ["instagram"], landings: ["home"], hops: ["blog"],
  },
  plain: { hosts: ["plain.example.test"], mode: "beacon", pages: ["home"], link_ids: { twitch: "home" } },
});
const UA = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0";
let n = 0;
const env = (): Env => ({ DB, SITES, CLICK_LIMITER: { limit: async () => ({ success: true }) }, VIEW_HASH_SECRET: btoa("k".repeat(32)) });
const click = (host = "links.example.test") =>
  new Request("https://worker.example.test/e/c/twitch?l=home&h=blog", {
    method: "POST",
    headers: { "User-Agent": UA, "CF-Connecting-IP": `198.51.100.${++n % 250}`, "Ping-From": `https://${host}/` },
  });
const dayOf = (ms: number) => new Date(ms).toISOString().slice(0, 10);
const hh = (ms: number) => String(new Date(ms).getUTCHours()).padStart(2, "0");
const count = async (key: string, ms = Date.now()): Promise<number> =>
  (await DB.prepare("SELECT count FROM counters WHERE key = ?1 AND day = ?2").bind(key, dayOf(ms)).first<{ count: number }>())?.count ?? 0;
const seed = (key: string, c: number, ms = Date.now()) =>
  DB.prepare("INSERT INTO counters (key, day, count) VALUES (?1, ?2, ?3) ON CONFLICT (key, day) DO UPDATE SET count = ?3").bind(key, dayOf(ms), c).run();
const events = async () => (await DB.prepare("SELECT count(*) AS c FROM click_events").first<{ c: number }>())!.c;
const SHARE1 = siteShare(CLICK_ADMIT_CEILING, 1);
const SHARE2 = siteShare(CLICK_ADMIT_CEILING, 2); // the route tests serve two sites
const FULL = clickWriteCost({ sources: true, landings: true, hops: true });
const L1 = clickLimits(SHARE1);
// Noon of the current UTC day, so a seed made with the default (now) lands on the same day.
const NOON = Math.floor(Date.now() / 86_400_000) * 86_400_000 + 12 * 3_600_000;
/** A link that already had its first click today, so it is judged against the general budget. */
const seedSeen = (site: string, link: string, ms = Date.now()) => seed(`cf-${site}_${link}`, 1, ms);

beforeEach(async () => {
  for (const t of ["click_src_daily", "click_country_daily", "click_landing_daily", "click_hop_daily", "click_seal", "click_budget", "click_budget_lh", "click_capacity", "click_events", "counters"]) {
    await DB.prepare(`DELETE FROM ${t}`).run();
  }
});

describe("the cost of one click is the rows it writes", () => {
  it("counts the four admission rows, raw row with its index, country, capacity, the hourly budget rows and each enabled axis", () => {
    // admission 4 (day total, hour slice, lane, first-click marker) + click_events 2 (row and ts index)
    // + country 1 + capacity 1 + click_budget 1
    expect(clickWriteCost({})).toBe(9);
    expect(clickWriteCost({ sources: true })).toBe(10);
    // landing or hop adds its counter and the click_budget_lh row (one row, whichever of the two made a cell)
    expect(clickWriteCost({ landings: true })).toBe(11);
    expect(clickWriteCost({ hops: true })).toBe(11);
    expect(clickWriteCost({ landings: true, hops: true })).toBe(12);
    expect(FULL).toBe(13);
  });

  // The rows a new-cell click really changes, measured: every table is snapshotted before and after one
  // click that makes a new cell on every enabled axis. Charged cost = the admission rows + rows changed
  // (the raw row counts twice, its ts index is a second write) + 1 for the capacity row, which a
  // click that folds on one axis writes while it makes a new cell on another.
  const TABLES = ["counters", "click_events", "click_src_daily", "click_country_daily", "click_landing_daily", "click_hop_daily", "click_budget", "click_budget_lh", "click_capacity"];
  const snapshot = async (): Promise<Set<string>> => {
    const out = new Set<string>();
    for (const t of TABLES) {
      const rows = (await DB.prepare(`SELECT * FROM ${t}`).all<Record<string, unknown>>()).results;
      for (const r of rows) out.add(`${t}|${JSON.stringify(r)}`);
    }
    return out;
  };
  const combos = [false, true].flatMap((sources) => [false, true].flatMap((landings) => [false, true].map((hops) => ({ sources, landings, hops }))));
  it.each(combos)("charges at least the rows a new-cell click changes: sources=$sources landings=$landings hops=$hops", async (on) => {
    const sites = JSON.stringify({
      one: {
        hosts: ["one.example.test"], mode: "beacon", pages: ["home"], link_ids: { twitch: "home" },
        ...(on.sources ? { sources: ["instagram"] } : {}),
        ...(on.landings ? { landings: ["home"] } : {}),
        ...(on.hops ? { hops: ["blog"] } : {}),
      },
    });
    const before = await snapshot();
    await handleClick(click("one.example.test"), { ...env(), SITES: sites });
    const after = await snapshot();
    const changed = [...after].filter((r) => !before.has(r));
    // the changed counters rows are the admission rows: day total, hour slice, lane and first-click marker
    const admission = changed.filter((r) => r.startsWith("counters|"));
    const nonAdmission = changed.filter((r) => !r.startsWith("counters|"));
    const rawRows = nonAdmission.filter((r) => r.startsWith("click_events|")).length;
    const measured = admission.length + nonAdmission.length + rawRows; // admission rows + rows + the raw row's index
    const charged = clickWriteCost(on);
    expect(admission.length).toBe(4);
    expect(rawRows).toBe(1);
    expect(nonAdmission.some((r) => r.startsWith("click_budget|"))).toBe(true);
    expect(nonAdmission.some((r) => r.startsWith("click_budget_lh|"))).toBe(on.landings || on.hops);
    expect(charged).toBeGreaterThanOrEqual(measured);
    expect(charged).toBe(measured + 1); // the capacity row, not written by a click that folds nothing
    expect(await count("cw-one")).toBe(charged);
  });

  // The fold case, measured: one slice of the hourly new-cell budget is already spent, so that axis pair
  // folds into its overflow row and writes the capacity row, while the other pair makes new cells. The
  // seed is written before the snapshot, so only this click's writes count.
  const foldCases = combos.flatMap((on) => [
    { ...on, spent: "click_budget" as const, applies: true },
    { ...on, spent: "click_budget_lh" as const, applies: on.landings || on.hops },
  ]).filter((c) => c.applies);
  it.each(foldCases)("charges at least the rows of a click that folds one axis pair: sources=$sources landings=$landings hops=$hops spent=$spent", async (c) => {
    const sites = JSON.stringify({
      one: {
        hosts: ["one.example.test"], mode: "beacon", pages: ["home"], link_ids: { twitch: "home" },
        ...(c.sources ? { sources: ["instagram"] } : {}),
        ...(c.landings ? { landings: ["home"] } : {}),
        ...(c.hops ? { hops: ["blog"] } : {}),
      },
    });
    const budget = c.spent === "click_budget" ? HOUR_BUDGET : LH_HOUR_BUDGET;
    await DB.prepare(`INSERT INTO ${c.spent} (site, day, hour, clicks) VALUES ('one', ?1, ?2, ?3)`).bind(dayOf(Date.now()), new Date().getUTCHours(), budget).run();
    const before = await snapshot();
    await handleClick(click("one.example.test"), { ...env(), SITES: sites });
    const after = await snapshot();
    const changed = [...after].filter((r) => !before.has(r));
    const admission = changed.filter((r) => r.startsWith("counters|"));
    const nonAdmission = changed.filter((r) => !r.startsWith("counters|"));
    const rawRows = nonAdmission.filter((r) => r.startsWith("click_events|")).length;
    const measured = admission.length + nonAdmission.length + rawRows;
    const charged = clickWriteCost(c);
    expect(admission.length).toBe(4);
    expect(rawRows).toBe(1);
    expect(nonAdmission.some((r) => r.startsWith("click_capacity|"))).toBe(true); // the fold wrote it
    expect(nonAdmission.some((r) => r.startsWith(`${c.spent}|`))).toBe(false); // the spent slice is not written again
    expect(charged).toBeGreaterThanOrEqual(measured);
    expect(await count("cw-one")).toBe(charged);
  });

  // The write charge is a conservative upper bound, not a count of the rows a click changes. A repeat click on a
  // link that already has its cells changes fewer rows than it is charged: the first-click marker, the new-cell
  // budget rows and the capacity row are charged but not written. Pinned here: the charge is never below the
  // rows changed, and the over-charge is exactly those rows.
  it.each(combos)("a repeat click is charged at least the rows it changes, and the over-charge is bounded: sources=$sources landings=$landings hops=$hops", async (on) => {
    const sites = JSON.stringify({
      one: {
        hosts: ["one.example.test"], mode: "beacon", pages: ["home"], link_ids: { twitch: "home" },
        ...(on.sources ? { sources: ["instagram"] } : {}),
        ...(on.landings ? { landings: ["home"] } : {}),
        ...(on.hops ? { hops: ["blog"] } : {}),
      },
    });
    await handleClick(click("one.example.test"), { ...env(), SITES: sites });
    const chargedBefore = await count("cw-one");
    const before = await snapshot();
    await handleClick(click("one.example.test"), { ...env(), SITES: sites });
    const after = await snapshot();
    const changed = [...after].filter((r) => !before.has(r));
    const raw = changed.filter((r) => r.startsWith("click_events|")).length;
    const measured = changed.length + raw; // the raw row counts twice: its ts index is a second write
    const charged = (await count("cw-one")) - chargedBefore;
    expect(charged).toBe(clickWriteCost(on));
    expect(charged).toBeGreaterThanOrEqual(measured);
    // not written by a repeat click: the marker, the new-cell budget row(s) and the capacity row
    const lh = on.landings || on.hops;
    const overCharge = 1 + 1 + (lh ? 1 : 0) + 1;
    expect(charged - measured).toBe(overCharge);
    expect(charged - measured).toBeLessThanOrEqual(4);
  });

  // The new lane counters: what one admitted click charges, compared with the rows it changed.
  it("charges the day total, the hour slice and the lane the same cost, and marks the link's first click once", async () => {
    await handleClick(click("plain.example.test"), env());
    const cost = clickWriteCost({});
    const rows = (await DB.prepare("SELECT key, count FROM counters ORDER BY key").all<{ key: string; count: number }>()).results;
    // the only rows written are the four admission rows, so the charge covers each of them
    expect(rows.map((r) => r.key)).toEqual(["cf-plain_twitch", `cw-plain`, `cwh-plain-${hh(Date.now())}`, "cwl-u-plain"].sort());
    expect(rows.length).toBe(4);
    expect(await count("cw-plain")).toBe(cost);
    expect(await count(`cwh-plain-${hh(Date.now())}`)).toBe(cost);
    expect(await count("cwl-u-plain")).toBe(cost);
    expect(await count("cf-plain_twitch")).toBe(1);
    expect(await count("cwl-v-plain")).toBe(0); // no token exists yet: every event is unverified
    await handleClick(click("plain.example.test"), env());
    expect(await count("cf-plain_twitch")).toBe(1); // written once, not per click
    expect(await count("cwl-u-plain")).toBe(2 * cost);
  });
});

describe("the ceiling arithmetic", () => {
  it("is 300000 writes a day, with the dropped-count cap on top, and nothing of the old figure is left", () => {
    expect(CLICK_ADMIT_CEILING).toBe(300_000);
    expect(CLICK_DROP_COUNT_CAP).toBe(500);
    // 300000 charged writes + 500 dropped-count writes + 1 setup-flag write (`cs-bad`, once a day)
    expect(CLICK_SETUP_FLAG_WRITES).toBe(1);
    expect(CLICK_ACCOUNT_DAILY_WRITES).toBe(300_501);
    expect(CLICK_DAILY_WRITES).toBe(300_501);
  });
  it("one site: the budgets add up as documented", () => {
    expect(CLICK_FIRST_RESERVE_PERCENT).toBe(20);
    expect(CLICK_UNVERIFIED_PERCENT).toBe(15);
    expect(CLICK_HOUR_SLICES).toBe(12);
    expect(L1).toEqual({ share: 300_000, general: 240_000, reserved: 60_000, hourSlice: 25_000, unverified: 45_000 });
  });
  it.each(Array.from({ length: 40 }, (_, i) => i + 1))("%i sites: shares, reserve, hour slices and lane stay inside the total", (sites) => {
    const l = clickLimits(siteShare(CLICK_ADMIT_CEILING, sites));
    expect(sites * l.share).toBeLessThanOrEqual(CLICK_ADMIT_CEILING);
    expect(l.general + l.reserved).toBe(l.share);
    expect(l.unverified).toBeLessThanOrEqual(l.general);
    expect(l.hourSlice * CLICK_HOUR_SLICES).toBeLessThanOrEqual(l.share);
    expect(l.hourSlice).toBeGreaterThanOrEqual(FULL); // one slice always holds at least one full click
    expect(sites * siteShare(CLICK_DROP_COUNT_CAP, sites)).toBeLessThanOrEqual(CLICK_DROP_COUNT_CAP);
  });
});

describe("admitClick", () => {
  it("admits up to the general budget and not one write past it", async () => {
    await seedSeen("links", "a");
    await seed("cw-links", L1.general - FULL);
    expect(await admitClick(DB, "links", NOON, FULL, 1, { linkId: "a", lane: "v" })).toBe(true);
    expect(await count("cw-links", NOON)).toBe(L1.general);
    expect(await admitClick(DB, "links", NOON, 1, 1, { linkId: "a", lane: "v" })).toBe(false);
    expect(await count("cw-links", NOON)).toBe(L1.general);
  });
  it("refuses a click that would cross the general budget and counts it as dropped", async () => {
    await seedSeen("links", "a");
    await seed("cw-links", L1.general - FULL + 1);
    expect(await admitClick(DB, "links", NOON, FULL, 1, { linkId: "a", lane: "v" })).toBe(false);
    expect(await count("cw-links", NOON)).toBe(L1.general - FULL + 1);
    expect(await count("cd-links", NOON)).toBe(1);
  });
  it("a refused click writes no lane, hour or marker row", async () => {
    await seedSeen("links", "a");
    await seed("cw-links", L1.general);
    expect(await admitClick(DB, "links", NOON, FULL, 1, { linkId: "a" })).toBe(false);
    expect(await count(`cwh-links-${hh(NOON)}`, NOON)).toBe(0);
    expect(await count("cwl-u-links", NOON)).toBe(0);
  });
  it("stops writing the dropped count at its cap, so refused requests cost no more writes", async () => {
    await seed("cw-links", CLICK_ADMIT_CEILING);
    for (let i = 0; i < CLICK_DROP_COUNT_CAP + 25; i++) expect(await admitClick(DB, "links", NOON, FULL, 1, { linkId: "a" })).toBe(false);
    expect(await count("cd-links", NOON)).toBe(CLICK_DROP_COUNT_CAP);
  });
  it("gives each site an equal share, so one site cannot spend another's", async () => {
    const l = clickLimits(SHARE2);
    await seedSeen("links", "a");
    await seed("cw-links", l.general);
    expect(await admitClick(DB, "links", NOON, 1, 2, { linkId: "a" })).toBe(false);
    expect(await admitClick(DB, "plain", NOON, FULL, 2, { linkId: "a" })).toBe(true);
    await seedSeen("plain", "a");
    await seed("cw-plain", l.general - 1);
    expect(await admitClick(DB, "plain", NOON, 2, 2, { linkId: "a" })).toBe(false);
  });
  it("is atomic: concurrent admissions never exceed the general budget", async () => {
    await seedSeen("links", "a");
    await seed("cw-links", L1.general - 3 * FULL);
    const r = await Promise.all(Array.from({ length: 12 }, () => admitClick(DB, "links", NOON, FULL, 1, { linkId: "a", lane: "v" })));
    expect(r.filter(Boolean).length).toBe(3);
    expect(await count("cw-links", NOON)).toBe(L1.general);
    expect(await count(`cwh-links-${hh(NOON)}`, NOON)).toBe(3 * FULL); // the slice grew by exactly what was admitted
    expect(await count("cwl-v-links", NOON)).toBe(3 * FULL);
  });

  describe("the day is sliced by hour", () => {
    // [description, seeded hour, time of the click, admitted]
    const H = (h: number, ms = 0) => Date.UTC(2027, 2, 14, h, 0, 0, ms);
    it.each([
      ["inside a spent hour", 5, H(5, 1_800_000), false],
      ["the last millisecond of a spent hour", 5, H(5, 3_599_999), false],
      ["the first millisecond of the next hour", 5, H(6, 0), true],
      ["an hour earlier", 5, H(4, 3_599_999), true],
      ["a spent hour leaves the other hours alone", 23, H(0, 0), true],
    ] as const)("%s", async (_d, spentHour, at, admitted) => {
      await seedSeen("links", "a", at);
      await seed(`cwh-links-${String(spentHour).padStart(2, "0")}`, L1.hourSlice - FULL + 1, H(spentHour));
      expect(await admitClick(DB, "links", at, FULL, 1, { linkId: "a", lane: "v" })).toBe(admitted);
    });
    it("23:59:59.999 and 00:00:00.000 UTC land in different days and different slices", async () => {
      const last = Date.UTC(2027, 2, 14, 23, 59, 59, 999);
      const first = Date.UTC(2027, 2, 15, 0, 0, 0, 0);
      await seedSeen("links", "a", last);
      await seedSeen("links", "a", first);
      expect(await admitClick(DB, "links", last, FULL, 1, { linkId: "a", lane: "v" })).toBe(true);
      expect(await admitClick(DB, "links", first, FULL, 1, { linkId: "a", lane: "v" })).toBe(true);
      expect(await count("cwh-links-23", last)).toBe(FULL);
      expect(await count("cwh-links-00", first)).toBe(FULL);
      expect(await count("cw-links", last)).toBe(FULL);
      expect(await count("cw-links", first)).toBe(FULL);
    });
    it("a burst in one hour spends only that hour's slice, so later hours still admit", async () => {
      // 40 sites: a small share, so the slice is reached in a few dozen clicks
      const l = clickLimits(siteShare(CLICK_ADMIT_CEILING, 40));
      const at = (h: number) => Date.UTC(2027, 2, 14, h, 30);
      await seedSeen("links", "a", at(3));
      let admitted = 0;
      for (let i = 0; i < Math.ceil(l.hourSlice / FULL) + 20; i++) {
        if (await admitClick(DB, "links", at(3), FULL, 40, { linkId: "a", lane: "v" })) admitted++;
      }
      expect(admitted).toBe(Math.floor(l.hourSlice / FULL));
      expect(await admitClick(DB, "links", at(3), FULL, 40, { linkId: "a", lane: "v" })).toBe(false);
      expect(await admitClick(DB, "links", at(4), FULL, 40, { linkId: "a", lane: "v" })).toBe(true);
    });
    it("the whole day never passes the share, however many hours are spent", async () => {
      const sites = 40;
      const l = clickLimits(siteShare(CLICK_ADMIT_CEILING, sites));
      await seedSeen("links", "a", NOON);
      const cost = 100; // a larger charge keeps the number of calls small; the arithmetic is the same
      for (let h = 0; h < 24; h++) {
        for (let i = 0; i < Math.ceil(l.hourSlice / cost) + 2; i++) await admitClick(DB, "links", NOON - 12 * 3_600_000 + h * 3_600_000 + 300_000, cost, sites, { linkId: "a", lane: "v" });
      }
      const total = await count("cw-links", NOON);
      expect(total).toBeLessThanOrEqual(l.general);
      expect(total).toBeGreaterThan(l.general - cost); // the day budget, not an hour slice, was the limit
    });
  });

  describe("room is kept for each link's first click of the day", () => {
    it("a link with no click today is admitted although the general budget is spent", async () => {
      await seed("cw-links", L1.general);
      expect(await admitClick(DB, "links", NOON, FULL, 1, { linkId: "b" })).toBe(true);
      expect(await count("cw-links", NOON)).toBe(L1.general + FULL);
      expect(await count("cf-links_b", NOON)).toBe(1);
    });
    it("only the first click: the second click on the same link is judged against the general budget", async () => {
      await seed("cw-links", L1.general);
      expect(await admitClick(DB, "links", NOON, FULL, 1, { linkId: "b" })).toBe(true);
      expect(await admitClick(DB, "links", NOON, FULL, 1, { linkId: "b" })).toBe(false);
    });
    it("a flood on one link does not starve another (many sites, small shares)", async () => {
      const sites = 40;
      const l = clickLimits(siteShare(CLICK_ADMIT_CEILING, sites));
      let admitted = 0;
      for (let i = 0; i < 400; i++) if (await admitClick(DB, "links", NOON, FULL, sites, { linkId: "flood" })) admitted++;
      expect(admitted).toBeGreaterThan(0);
      expect(admitted).toBeLessThan(400); // the flood was stopped
      expect(await admitClick(DB, "links", NOON, FULL, sites, { linkId: "quiet" })).toBe(true);
      expect(await count("cw-links", NOON)).toBeLessThanOrEqual(l.share);
    });
    it("is not bound by the hour slice or the lane cap", async () => {
      await seed(`cwh-links-${hh(NOON)}`, L1.hourSlice, NOON);
      await seed("cwl-u-links", L1.unverified, NOON);
      expect(await admitClick(DB, "links", NOON, FULL, 1, { linkId: "b" })).toBe(true);
      expect(await admitClick(DB, "links", NOON, FULL, 1, { linkId: "b" })).toBe(false); // the second one is
    });
    it("never passes the whole share, first clicks included", async () => {
      await seed("cw-links", L1.share - FULL + 1);
      expect(await admitClick(DB, "links", NOON, FULL, 1, { linkId: "b" })).toBe(false);
      expect(await count("cf-links_b", NOON)).toBe(0); // a refused first click leaves no marker
      await seed("cw-links", L1.share - FULL);
      expect(await admitClick(DB, "links", NOON, FULL, 1, { linkId: "b" })).toBe(true);
      expect(await count("cw-links", NOON)).toBe(L1.share);
    });
    it.each([
      ["a", "b-c", "a-b", "c"],
      ["x-y", "z", "x", "y-z"],
      ["a-b-c", "d", "a-b", "c-d"],
    ])("hyphenated site and link ids never share a marker: (%s, %s) and (%s, %s)", async (s1, l1, s2, l2) => {
      // the second pair's general budget is spent, so only its own first-click reserve can admit it
      await seed(`cw-${s2}`, L1.general, NOON);
      expect(await admitClick(DB, s1, NOON, FULL, 1, { linkId: l1 })).toBe(true);
      expect(await admitClick(DB, s2, NOON, FULL, 1, { linkId: l2 })).toBe(true);
      // and each marker is its own row, written once
      const markers = (await DB.prepare("SELECT key FROM counters WHERE key GLOB 'cf-*'").all<{ key: string }>()).results;
      expect(markers.length).toBe(2);
    });
    it("the longest site id and link id still fit the key column (64 characters, lowercase, digits, hyphen, underscore)", async () => {
      const site = "a".repeat(20);
      const link = "b".repeat(40);
      expect(await admitClick(DB, site, NOON, FULL, 1, { linkId: link })).toBe(true);
      expect(await count(`cf-${site}_${link}`, NOON)).toBe(1);
      expect(`cf-${site}_${link}`.length).toBe(64);
    });
    it("every counter key kind is injective over the site ids and link ids it is built from", async () => {
      // site and link ids are lowercase letters, digits and hyphens; the marker joins them with a character
      // that cannot occur in either, and the other keys end in a fixed-width hour or hold the site last
      await admitClick(DB, "a", NOON, FULL, 1, { linkId: "b-c", lane: "u" });
      await admitClick(DB, "a-b", NOON, FULL, 1, { linkId: "c", lane: "u" });
      const keys = (await DB.prepare("SELECT key FROM counters ORDER BY key").all<{ key: string }>()).results.map((r) => r.key);
      expect(new Set(keys).size).toBe(keys.length);
      expect(keys.filter((k) => k.startsWith("cw-")).sort()).toEqual(["cw-a", "cw-a-b"]);
      expect(keys.filter((k) => k.startsWith("cwl-")).sort()).toEqual(["cwl-u-a", "cwl-u-a-b"]);
      expect(keys.filter((k) => k.startsWith("cwh-")).length).toBe(2);
      expect(keys.filter((k) => k.startsWith("cf-")).length).toBe(2);
    });
    it("markers are per site and per day", async () => {
      await seed("cw-links", L1.general);
      expect(await admitClick(DB, "links", NOON, FULL, 1, { linkId: "b" })).toBe(true);
      expect(await admitClick(DB, "plain", NOON, FULL, 1, { linkId: "b" })).toBe(true); // another site's own first click
      const nextDay = NOON + 24 * 3_600_000;
      await seed("cw-links", L1.general, nextDay);
      expect(await admitClick(DB, "links", nextDay, FULL, 1, { linkId: "b" })).toBe(true); // first of that day
    });
  });

  describe("the two lanes", () => {
    const ON = { enforceLaneCap: true } as const;
    it("with the switch on, the unverified lane is capped at its percent of the share", async () => {
      await seedSeen("links", "a");
      await seed("cwl-u-links", L1.unverified - FULL, NOON);
      expect(await admitClick(DB, "links", NOON, FULL, 1, { linkId: "a", lane: "u", ...ON })).toBe(true);
      expect(await count("cwl-u-links", NOON)).toBe(L1.unverified);
      expect(await admitClick(DB, "links", NOON, 1, 1, { linkId: "a", lane: "u", ...ON })).toBe(false);
      expect(await count("cd-links", NOON)).toBe(1);
    });
    it("with the switch off (the default) the cap does not bind, and both lanes are still recorded", async () => {
      await seedSeen("links", "a");
      await seed("cwl-u-links", L1.unverified, NOON);
      expect(await admitClick(DB, "links", NOON, FULL, 1, { linkId: "a", lane: "u" })).toBe(true);
      expect(await admitClick(DB, "links", NOON, FULL, 1, { linkId: "a", lane: "u", enforceLaneCap: false })).toBe(true);
      expect(await count("cwl-u-links", NOON)).toBe(L1.unverified + 2 * FULL);
      expect(await admitClick(DB, "links", NOON, FULL, 1, { linkId: "a", lane: "v" })).toBe(true);
      expect(await count("cwl-v-links", NOON)).toBe(FULL);
    });
    it("with the switch off the day budget and the hour slice still bind", async () => {
      await seedSeen("links", "a");
      await seed("cw-links", L1.general, NOON);
      expect(await admitClick(DB, "links", NOON, FULL, 1, { linkId: "a" })).toBe(false);
      await seed("cw-links", 0, NOON);
      await seed(`cwh-links-${hh(NOON)}`, L1.hourSlice, NOON);
      expect(await admitClick(DB, "links", NOON, FULL, 1, { linkId: "a" })).toBe(false);
    });
    it("a spent unverified lane leaves the verified lane open, and each lane keeps its own count", async () => {
      await seedSeen("links", "a");
      await seed("cwl-u-links", L1.unverified, NOON);
      expect(await admitClick(DB, "links", NOON, FULL, 1, { linkId: "a", lane: "u", ...ON })).toBe(false);
      expect(await admitClick(DB, "links", NOON, FULL, 1, { linkId: "a", lane: "v" })).toBe(true);
      expect(await count("cwl-v-links", NOON)).toBe(FULL);
      expect(await count("cwl-u-links", NOON)).toBe(L1.unverified);
    });
    it("the default lane is unverified (no token exists yet)", async () => {
      await seedSeen("links", "a");
      await admitClick(DB, "links", NOON, FULL, 1, { linkId: "a" });
      expect(await count("cwl-u-links", NOON)).toBe(FULL);
      expect(await count("cwl-v-links", NOON)).toBe(0);
    });
    it("the lane cap is part of the day: unverified admissions also count in the day total", async () => {
      await seedSeen("links", "a");
      await admitClick(DB, "links", NOON, FULL, 1, { linkId: "a", lane: "u" });
      await admitClick(DB, "links", NOON, FULL, 1, { linkId: "a", lane: "v" });
      expect(await count("cw-links", NOON)).toBe(2 * FULL);
    });
  });
});

describe("the click route", () => {
  it("writes nothing for a click past the whole share and answers it exactly like a counted click", async () => {
    const ok = await handleClick(click(), env());
    expect(await events()).toBe(1);
    const spent = await count("cw-links");
    expect(spent).toBe(FULL);
    await seed("cw-links", SHARE2);
    const before = await events();
    const over = await handleClick(click(), env());
    expect(over.status).toBe(ok.status);
    expect(over.status).toBe(204);
    expect(await over.text()).toBe(await ok.clone().text());
    expect([...over.headers]).toEqual([...ok.headers]);
    expect(await events()).toBe(before);
    for (const t of ["click_country_daily", "click_landing_daily", "click_hop_daily", "click_src_daily"]) {
      const c = (await DB.prepare(`SELECT COALESCE(SUM(clicks), 0) AS c FROM ${t}`).first<{ c: number }>())!.c;
      expect(c, t).toBe(1);
    }
    expect(await count("cd-links")).toBe(1);
  });
  it("charges a site without the optional lists at the smaller cost", async () => {
    await handleClick(click("plain.example.test"), env());
    expect(await count("cw-plain")).toBe(clickWriteCost({}));
  });
  it("every route click is charged to the unverified lane", async () => {
    await handleClick(click(), env());
    expect(await count("cwl-u-links")).toBe(FULL);
    expect(await count("cwl-v-links")).toBe(0);
  });
  it("total writes per site per day cannot pass the modeled share, however many clicks arrive", async () => {
    // the first click makes the link's marker; then only the general budget is left
    await handleClick(click(), env());
    await seed("cw-links", clickLimits(SHARE2).general - 10 * FULL);
    const before = await events();
    for (let i = 0; i < 40; i++) await handleClick(click(), env());
    expect(await events() - before).toBe(10);
    expect(await count("cw-links")).toBe(clickLimits(SHARE2).general);
    expect(await count("cd-links")).toBe(30);
  });
  it("fails closed: an admission error records nothing and still answers 204", async () => {
    const broken = { ...env(), DB: { ...DB, prepare: () => { throw new Error("db down"); } } as unknown as D1Database };
    const r = await handleClick(click(), broken);
    expect(r.status).toBe(204);
    expect(await events()).toBe(0);
  });
});

describe("the click route and the lane switch", () => {
  const laneFull = async () => {
    await handleClick(click(), env()); // makes the link's marker, so the next click is judged against the lane
    await seed("cwl-u-links", clickLimits(SHARE2).unverified);
  };
  it("by default a spent unverified lane does not stop a click", async () => {
    await laneFull();
    const before = await events();
    await handleClick(click(), env());
    expect(await events() - before).toBe(1);
  });
  it.each(["1", "true"])("with BEACON_LANE_CAP_ENFORCED=%s a spent unverified lane stops the click (same 204)", async (v) => {
    await laneFull();
    const before = await events();
    const r = await handleClick(click(), { ...env(), BEACON_LANE_CAP_ENFORCED: v });
    expect(r.status).toBe(204);
    expect(await events()).toBe(before);
    expect(await count("cd-links")).toBe(1);
  });
  it.each(["0", "false", "", undefined])("BEACON_LANE_CAP_ENFORCED=%j leaves the cap off", async (v) => {
    await laneFull();
    const before = await events();
    await handleClick(click(), { ...env(), BEACON_LANE_CAP_ENFORCED: v });
    expect(await events() - before).toBe(1);
  });
});

describe("the click ceiling is a validated setting", () => {
  it.each([
    [undefined, 300_000],
    ["", 300_000],
    ["300000", 300_000],
    ["7500", 7_500],
    ["1000", 1_000],
    ["10000000", 10_000_000],
    ["999", null],
    ["10000001", null],
    ["0", null],
    ["-1", null],
    ["1e5", null],
    [" 5000", null],
    ["5000 ", null],
    ["+5000", null],
    ["0x1000", null],
    ["7500.0", null],
    ["abc", null],
    ["1234567890", null],
    [7500, null],
  ])("CLICK_DAILY_WRITE_CEILING=%j is %j", (v, want) => {
    expect(parseClickCeiling(v)).toBe(want);
  });
  it.each([
    [undefined, false],
    ["", false],
    ["0", false],
    ["false", false],
    ["1", true],
    ["true", true],
    ["TRUE", null],
    ["yes", null],
    [" 1", null],
    ["2", null],
    [1, null],
  ])("BEACON_LANE_CAP_ENFORCED=%j is %j", (v, want) => {
    expect(parseLaneCapFlag(v)).toBe(want);
  });
  it("the model reserves the configured ceiling plus the dropped-count cap", () => {
    expect(clickAccountWrites(7_500)).toBe(8_001);
    expect(clickAccountWrites(CLICK_ADMIT_CEILING)).toBe(CLICK_ACCOUNT_DAILY_WRITES);
  });
  it("a lower ceiling is enforced by the route: the same click is refused at a share it does not fit", async () => {
    const small = { ...env(), CLICK_DAILY_WRITE_CEILING: "1000" }; // two sites: a share of 500, first click judged against 500
    expect(await handleClick(click(), small)).toBeDefined();
    expect(await events()).toBe(1);
    await seed("cw-links", 500);
    await handleClick(click(), small);
    expect(await events()).toBe(1);
    // the default ceiling would still have room
    await handleClick(click(), env());
    expect(await events()).toBe(2);
  });
  it.each(["999", "abc", "1e5"])("an unusable ceiling %j refuses the click and flags the setup", async (v) => {
    await handleClick(click(), { ...env(), CLICK_DAILY_WRITE_CEILING: v });
    expect(await events()).toBe(0);
    expect(await count("cs-bad")).toBe(1);
  });
});

describe("clicks refused for an unusable setup are never silent", () => {
  const bad: [string, Partial<Env>][] = [
    ["a missing hashing key", { VIEW_HASH_SECRET: undefined }],
    ["a short hashing key", { VIEW_HASH_SECRET: btoa("a".repeat(8)) }],
    ["an unusable lane switch", { BEACON_LANE_CAP_ENFORCED: "maybe" }],
    ["an unusable ceiling", { CLICK_DAILY_WRITE_CEILING: "0" }],
  ];
  it.each(bad)("%s: same 204, nothing counted, one flag row for the day", async (_n, over) => {
    const ok = await handleClick(click(), env());
    await DB.prepare("DELETE FROM click_events").run();
    await DB.prepare("DELETE FROM counters").run();
    const r = await handleClick(click(), { ...env(), ...over });
    expect(r.status).toBe(ok.status);
    expect(await r.text()).toBe(await ok.text());
    expect(await events()).toBe(0);
    expect(await count("cs-bad")).toBe(1);
    // many more refusals still write that one row only
    for (let i = 0; i < 5; i++) await handleClick(click(), { ...env(), ...over });
    expect(await count("cs-bad")).toBe(1);
    const rows = (await DB.prepare("SELECT key FROM counters").all<{ key: string }>()).results.map((x) => x.key);
    expect(rows).toEqual(["cs-bad"]);
  });
  it("a usable setup leaves no flag", async () => {
    await handleClick(click(), env());
    expect(await count("cs-bad")).toBe(0);
  });
  it("the flag carries no site, link or address", async () => {
    await handleClick(click(), { ...env(), VIEW_HASH_SECRET: undefined });
    const r = (await DB.prepare("SELECT key, day, count FROM counters").all<{ key: string; day: string; count: number }>()).results;
    expect(r).toEqual([{ key: "cs-bad", day: dayOf(Date.now()), count: 1 }]);
  });
});

describe("the budget model derives from the enforced ceiling", () => {
  it("CLICK_DAILY_WRITES is the ceiling plus the dropped-count cap plus the setup flag, and nothing else", () => {
    expect(CLICK_ACCOUNT_DAILY_WRITES).toBe(CLICK_ADMIT_CEILING + CLICK_DROP_COUNT_CAP + CLICK_SETUP_FLAG_WRITES);
    expect(CLICK_DAILY_WRITES).toBe(CLICK_ACCOUNT_DAILY_WRITES);
  });
  it("a day that goes from valid to invalid settings stays inside the modeled bound", async () => {
    // A day at its limits: the charged writes are at the ceiling, the dropped count is at its cap,
    // then the settings become unusable and clicks are refused for that. All three counters are
    // bounded and together stay within what the budget model reserves for the click route.
    const small = 1_000; // a small valid ceiling, one site
    const e = { ...env(), SITES: JSON.stringify({ one: { hosts: ["one.example.test"], mode: "beacon", pages: ["home"], link_ids: { twitch: "home" } } }), CLICK_DAILY_WRITE_CEILING: String(small) };
    await seed("cw-one", small);
    await seedSeen("one", "twitch");
    for (let i = 0; i < CLICK_DROP_COUNT_CAP + 20; i++) await handleClick(click("one.example.test"), e);
    expect(await count("cd-one")).toBe(CLICK_DROP_COUNT_CAP);
    for (let i = 0; i < 5; i++) await handleClick(click("one.example.test"), { ...e, CLICK_DAILY_WRITE_CEILING: "abc" });
    expect(await count("cs-bad")).toBe(CLICK_SETUP_FLAG_WRITES);
    const written = (await count("cw-one")) + (await count("cd-one")) + (await count("cs-bad"));
    expect(written).toBeLessThanOrEqual(clickAccountWrites(small));
    expect(written).toBe(small + CLICK_DROP_COUNT_CAP + CLICK_SETUP_FLAG_WRITES); // the bound is reached, not just kept
  });
  it("shares never add up to more than the total, for any number of sites", () => {
    for (let n = 1; n <= 40; n++) {
      expect(n * siteShare(CLICK_ADMIT_CEILING, n)).toBeLessThanOrEqual(CLICK_ADMIT_CEILING);
      expect(n * siteShare(CLICK_DROP_COUNT_CAP, n)).toBeLessThanOrEqual(CLICK_DROP_COUNT_CAP);
    }
  });
});
