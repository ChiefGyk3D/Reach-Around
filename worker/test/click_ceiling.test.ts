// SPDX-License-Identifier: Apache-2.0
// Hardening: bound click write amplification (Refs #566). An enforced, atomic per-site daily
// admission ceiling, charged at the rows one click really writes, checked before any click write.
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import { handleClick, type Env } from "../src/click";
import {
  CLICK_ADMIT_CEILING,
  CLICK_DROP_COUNT_CAP,
  CLICK_ACCOUNT_DAILY_WRITES,
  siteShare,
  admitClick,
  clickWriteCost,
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
const env = (): Env => ({ DB, SITES, CLICK_LIMITER: { limit: async () => ({ success: true }) } });
const click = (host = "links.example.test") =>
  new Request("https://worker.example.test/e/c/twitch?l=home&h=blog", {
    method: "POST",
    headers: { "User-Agent": UA, "CF-Connecting-IP": `198.51.100.${++n % 250}`, "Ping-From": `https://${host}/` },
  });
const day = () => new Date().toISOString().slice(0, 10);
const count = async (key: string): Promise<number> =>
  (await DB.prepare("SELECT count FROM counters WHERE key = ?1 AND day = ?2").bind(key, day()).first<{ count: number }>())?.count ?? 0;
const seed = (key: string, c: number) =>
  DB.prepare("INSERT INTO counters (key, day, count) VALUES (?1, ?2, ?3) ON CONFLICT (key, day) DO UPDATE SET count = ?3").bind(key, day(), c).run();
const events = async () => (await DB.prepare("SELECT count(*) AS c FROM click_events").first<{ c: number }>())!.c;
const SHARE2 = siteShare(CLICK_ADMIT_CEILING, 2); // the route tests serve two sites
const FULL = clickWriteCost({ sources: true, landings: true, hops: true });

beforeEach(async () => {
  for (const t of ["click_src_daily", "click_country_daily", "click_landing_daily", "click_hop_daily", "click_seal", "click_budget", "click_budget_lh", "click_capacity", "click_events", "counters"]) {
    await DB.prepare(`DELETE FROM ${t}`).run();
  }
});

describe("the cost of one click is the rows it writes", () => {
  it("counts admission, raw row with its index, country, capacity, the hourly budget rows and each enabled axis", () => {
    // admission 1 + click_events 2 (row and ts index) + country 1 + capacity 1 + click_budget 1
    expect(clickWriteCost({})).toBe(6);
    expect(clickWriteCost({ sources: true })).toBe(7);
    // landing or hop adds its counter and the click_budget_lh row (one row, whichever of the two made a cell)
    expect(clickWriteCost({ landings: true })).toBe(8);
    expect(clickWriteCost({ hops: true })).toBe(8);
    expect(clickWriteCost({ landings: true, hops: true })).toBe(9);
    expect(FULL).toBe(10);
  });

  // The rows a new-cell click really changes, measured: every table is snapshotted before and after one
  // click that makes a new cell on every enabled axis. Charged cost = admission row + rows changed
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
    // a changed counters row is the admission charge itself; it is the one write counted apart below
    const nonAdmission = changed.filter((r) => !r.startsWith("counters|"));
    const rawRows = nonAdmission.filter((r) => r.startsWith("click_events|")).length;
    const measured = 1 + nonAdmission.length + rawRows; // admission + rows + the raw row's index
    const charged = clickWriteCost(on);
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
    await DB.prepare(`INSERT INTO ${c.spent} (site, day, hour, clicks) VALUES ('one', ?1, ?2, ?3)`).bind(day(), new Date().getUTCHours(), budget).run();
    const before = await snapshot();
    await handleClick(click("one.example.test"), { ...env(), SITES: sites });
    const after = await snapshot();
    const nonAdmission = [...after].filter((r) => !before.has(r) && !r.startsWith("counters|"));
    const rawRows = nonAdmission.filter((r) => r.startsWith("click_events|")).length;
    const measured = 1 + nonAdmission.length + rawRows;
    const charged = clickWriteCost(c);
    expect(rawRows).toBe(1);
    expect(nonAdmission.some((r) => r.startsWith("click_capacity|"))).toBe(true); // the fold wrote it
    expect(nonAdmission.some((r) => r.startsWith(`${c.spent}|`))).toBe(false); // the spent slice is not written again
    expect(charged).toBeGreaterThanOrEqual(measured);
    expect(await count("cw-one")).toBe(charged);
  });
});

describe("admitClick", () => {
  it("admits up to the ceiling and not one write past it", async () => {
    await seed("cw-links", CLICK_ADMIT_CEILING - FULL);
    expect(await admitClick(DB, "links", Date.now(), FULL)).toBe(true);
    expect(await count("cw-links")).toBe(CLICK_ADMIT_CEILING);
    expect(await admitClick(DB, "links", Date.now(), 1)).toBe(false);
    expect(await count("cw-links")).toBe(CLICK_ADMIT_CEILING);
  });
  it("refuses a click that would cross the ceiling and counts it as dropped", async () => {
    await seed("cw-links", CLICK_ADMIT_CEILING - FULL + 1);
    expect(await admitClick(DB, "links", Date.now(), FULL)).toBe(false);
    expect(await count("cw-links")).toBe(CLICK_ADMIT_CEILING - FULL + 1);
    expect(await count("cd-links")).toBe(1);
  });
  it("stops writing the dropped count at its cap, so refused requests cost no more writes", async () => {
    await seed("cw-links", CLICK_ADMIT_CEILING);
    for (let i = 0; i < CLICK_DROP_COUNT_CAP + 25; i++) expect(await admitClick(DB, "links", Date.now(), FULL)).toBe(false);
    expect(await count("cd-links")).toBe(CLICK_DROP_COUNT_CAP);
  });
  it("gives each site an equal share, so one site cannot spend another's", async () => {
    const half = siteShare(CLICK_ADMIT_CEILING, 2);
    await seed("cw-links", half);
    expect(await admitClick(DB, "links", Date.now(), 1, 2)).toBe(false);
    expect(await admitClick(DB, "plain", Date.now(), FULL, 2)).toBe(true);
    await seed("cw-plain", half - 1);
    expect(await admitClick(DB, "plain", Date.now(), 2, 2)).toBe(false);
  });
  it("is atomic: concurrent admissions never exceed the ceiling", async () => {
    await seed("cw-links", CLICK_ADMIT_CEILING - 3 * FULL);
    const r = await Promise.all(Array.from({ length: 12 }, () => admitClick(DB, "links", Date.now(), FULL)));
    expect(r.filter(Boolean).length).toBe(3);
    expect(await count("cw-links")).toBe(CLICK_ADMIT_CEILING);
  });
});

describe("the click route", () => {
  it("writes nothing for a click past the ceiling and answers it exactly like a counted click", async () => {
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
  it("total writes per site per day cannot pass the modeled ceiling, however many clicks arrive", async () => {
    await seed("cw-links", SHARE2 - 10 * FULL);
    for (let i = 0; i < 40; i++) await handleClick(click(), env());
    expect(await events()).toBe(10);
    expect(await count("cw-links")).toBe(SHARE2);
    expect(await count("cd-links")).toBe(30);
  });
  it("fails closed: an admission error records nothing and still answers 204", async () => {
    const broken = { ...env(), DB: { ...DB, prepare: () => { throw new Error("db down"); } } as unknown as D1Database };
    const r = await handleClick(click(), broken);
    expect(r.status).toBe(204);
    expect(await events()).toBe(0);
  });
});

describe("the budget model derives from the enforced ceiling", () => {
  it("CLICK_DAILY_WRITES is the ceiling plus the dropped-count cap, and nothing else", () => {
    expect(CLICK_ACCOUNT_DAILY_WRITES).toBe(CLICK_ADMIT_CEILING + CLICK_DROP_COUNT_CAP);
    expect(CLICK_DAILY_WRITES).toBe(CLICK_ACCOUNT_DAILY_WRITES);
  });
  it("shares never add up to more than the total, for any number of sites", () => {
    for (let n = 1; n <= 40; n++) {
      expect(n * siteShare(CLICK_ADMIT_CEILING, n)).toBeLessThanOrEqual(CLICK_ADMIT_CEILING);
      expect(n * siteShare(CLICK_DROP_COUNT_CAP, n)).toBeLessThanOrEqual(CLICK_DROP_COUNT_CAP);
    }
  });
});
