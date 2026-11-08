// SPDX-License-Identifier: Apache-2.0
import { beforeEach, describe, expect, it } from "vitest";
import { ALERT_TEXT, alertConfigured, scheduleAlert, sendAlert, type AlertEnv } from "../../src/kit/alert";
import { handleGate } from "../../src/kit/gate";
import {
  DB,
  DISCORD,
  IP,
  MATRIX,
  NOW,
  UA,
  alertCalls,
  captureLogs,
  cookieOf,
  ctxOf,
  fakeFetch,
  gatePost,
  makeEnv,
  newFakes,
  resetDb,
  settle,
  type Fakes,
} from "./gatekit";

let f: Fakes;
beforeEach(async () => {
  await resetDb();
  f = newFakes();
});

const aenv = (over: Record<string, unknown> = {}): AlertEnv => ({ DB, ...over }) as unknown as AlertEnv;
const send = (over: Record<string, unknown> = {}, kind: "lead_created" | "budget_warning" = "lead_created", timeoutMs = 3000) =>
  sendAlert(aenv(over), kind, { fetchFn: fakeFetch(f), nowMs: NOW, timeoutMs });
const counter = async (key: string) =>
  (await DB.prepare("SELECT count FROM counters WHERE key = ?").bind(key).first<{ count: number }>())?.count ?? 0;

describe("what is sent", () => {
  it("Discord: one POST with the fixed text, no mentions, redirect error and a timeout signal", async () => {
    await send({ ALERT_DISCORD_WEBHOOK: DISCORD });
    const calls = alertCalls(f);
    expect(calls).toHaveLength(1);
    expect(calls[0]!.url).toBe(DISCORD);
    expect(calls[0]!.init.method).toBe("POST");
    expect(calls[0]!.init.redirect).toBe("error");
    expect(calls[0]!.init.signal).toBeInstanceOf(AbortSignal);
    expect(JSON.parse(String(calls[0]!.init.body))).toEqual({ content: ALERT_TEXT.lead_created, allowed_mentions: { parse: [] } });
  });

  it("Matrix: one PUT to the send endpoint with a bearer token and the fixed text", async () => {
    await send(MATRIX);
    const calls = alertCalls(f);
    expect(calls).toHaveLength(1);
    expect(calls[0]!.url).toMatch(/^https:\/\/matrix\.example\.test\/_matrix\/client\/v3\/rooms\/!room1%3Amatrix\.example\.test\/send\/m\.room\.message\/[0-9a-f]{32}$/);
    expect(calls[0]!.init.method).toBe("PUT");
    expect(calls[0]!.init.redirect).toBe("error");
    expect((calls[0]!.init.headers as Record<string, string>).Authorization).toBe("Bearer syt_exampletokenvalue");
    expect(JSON.parse(String(calls[0]!.init.body))).toEqual({ msgtype: "m.text", body: ALERT_TEXT.lead_created });
    expect(calls[0]!.url).not.toContain("syt_exampletokenvalue");
  });

  it("both sinks configured: both get the alert, and it costs one budget unit", async () => {
    await send({ ALERT_DISCORD_WEBHOOK: DISCORD, ...MATRIX });
    expect(alertCalls(f)).toHaveLength(2);
    expect(await counter("alert")).toBe(1);
  });

  it("no sink, or a malformed one, sends nothing and spends nothing", async () => {
    const bad: Array<Record<string, unknown>> = [
      {},
      { ALERT_DISCORD_WEBHOOK: "http://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz" },
      { ALERT_DISCORD_WEBHOOK: "https://evil.example.test/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz" },
      { ALERT_DISCORD_WEBHOOK: `${DISCORD}\nX-Injected: 1` },
      { ALERT_DISCORD_WEBHOOK: `${DISCORD}?x=https://evil.example.test` },
      { ...MATRIX, ALERT_MATRIX_HOMESERVER: "http://matrix.example.test" },
      { ...MATRIX, ALERT_MATRIX_HOMESERVER: "https://matrix.example.test/path" },
      { ...MATRIX, ALERT_MATRIX_HOMESERVER: "https://user@matrix.example.test" },
      { ...MATRIX, ALERT_MATRIX_ROOM_ID: "room-without-bang" },
      { ...MATRIX, ALERT_MATRIX_ROOM_ID: "!a/../../b:matrix.example.test\n" },
      { ...MATRIX, ALERT_MATRIX_ACCESS_TOKEN: "short" },
      { ...MATRIX, ALERT_MATRIX_ACCESS_TOKEN: "has space in it token" },
      { ALERT_MATRIX_HOMESERVER: MATRIX.ALERT_MATRIX_HOMESERVER },
    ];
    for (const b of bad) {
      expect(alertConfigured(aenv(b))).toBe(false);
      await send(b);
    }
    expect(f.fetchCalls).toEqual([]);
    expect(await counter("alert")).toBe(0);
  });

  it("an unknown kind is dropped", async () => {
    await sendAlert(aenv({ ALERT_DISCORD_WEBHOOK: DISCORD }), "view_alert" as never, { fetchFn: fakeFetch(f), nowMs: NOW });
    expect(f.fetchCalls).toEqual([]);
  });

  it("without an execution context nothing is sent", async () => {
    scheduleAlert(aenv({ ALERT_DISCORD_WEBHOOK: DISCORD }), undefined, "lead_created", { fetchFn: fakeFetch(f), nowMs: NOW });
    await new Promise((r) => setTimeout(r, 20));
    expect(f.fetchCalls).toEqual([]);
  });
});

describe("no personal data in the payload", () => {
  it("a lead created through the gate alerts with fixed text only: no email, IP, user agent, id, token or country", async () => {
    const email = "pii-sentinel-9@example.test";
    const env = makeEnv(f, { ALERT_DISCORD_WEBHOOK: DISCORD, ...MATRIX });
    const res = await handleGate(gatePost({ fields: { email, "cf-turnstile-response": "ok" } }), env, ctxOf(f), { fetchFn: fakeFetch(f), nowMs: NOW });
    await settle(f);
    const cookie = cookieOf(res)!;
    const lead = await DB.prepare("SELECT id FROM leads").first<{ id: string }>();
    const sent = alertCalls(f);
    expect(sent).toHaveLength(2);
    for (const c of sent) {
      const body = String(c.init.body);
      for (const needle of ["pii-sentinel", email, IP, "Firefox", UA, lead!.id, cookie, "ts-secret-example", "US"]) expect(body).not.toContain(needle);
      expect(body).toMatch(/new media kit lead was created/);
    }
  });

  it("the budget warning carries the limit number and nothing else", async () => {
    await send({ ALERT_DISCORD_WEBHOOK: DISCORD, DAILY_ALERT_BUDGET: "0" });
    expect(JSON.parse(String(alertCalls(f)[0]!.init.body)).content).toBe(ALERT_TEXT.budget_warning(0));
  });

  it("secrets reach no log line", async () => {
    const logs = captureLogs();
    try {
      f.alert = "500";
      await send({ ALERT_DISCORD_WEBHOOK: DISCORD, ...MATRIX });
      const all = logs.lines.join("\n");
      expect(all).not.toContain("abcdefghijklmnopqrstuvwxyz");
      expect(all).not.toContain("syt_exampletokenvalue");
      expect(all).not.toContain("secret details");
      expect(logs.lines.length).toBeGreaterThan(0);
    } finally {
      logs.restore();
    }
  });
});

describe("the daily alert budget", () => {
  it("DAILY_ALERT_BUDGET=2: two alerts, then ONE budget warning, then silence", async () => {
    const over = { ALERT_DISCORD_WEBHOOK: DISCORD, DAILY_ALERT_BUDGET: "2" };
    for (let i = 0; i < 6; i++) await send(over);
    const texts = alertCalls(f).map((c) => JSON.parse(String(c.init.body)).content as string);
    expect(texts).toEqual([ALERT_TEXT.lead_created, ALERT_TEXT.lead_created, ALERT_TEXT.budget_warning(2)]);
    expect(await counter("alert")).toBe(2);
    expect(await counter("alert-warn")).toBe(1);
  });

  it("DAILY_ALERT_BUDGET=0 sends only the one warning", async () => {
    for (let i = 0; i < 3; i++) await send({ ALERT_DISCORD_WEBHOOK: DISCORD, DAILY_ALERT_BUDGET: "0" });
    expect(alertCalls(f)).toHaveLength(1);
  });

  it("an unusable budget value sends nothing (fails closed)", async () => {
    await send({ ALERT_DISCORD_WEBHOOK: DISCORD, DAILY_ALERT_BUDGET: "lots" });
    expect(f.fetchCalls).toEqual([]);
  });

  it("the next UTC day has a fresh budget and a fresh warning", async () => {
    const over = { ALERT_DISCORD_WEBHOOK: DISCORD, DAILY_ALERT_BUDGET: "1" };
    await send(over);
    await send(over);
    await sendAlert(aenv(over), "lead_created", { fetchFn: fakeFetch(f), nowMs: NOW + 86_400_000 });
    expect(alertCalls(f)).toHaveLength(3);
  });

  it("a database failure on the budget sends nothing", async () => {
    const broken = { prepare: () => { throw new Error("db down"); } };
    await sendAlert({ DB: broken, ALERT_DISCORD_WEBHOOK: DISCORD } as unknown as AlertEnv, "lead_created", { fetchFn: fakeFetch(f), nowMs: NOW });
    expect(f.fetchCalls).toEqual([]);
  });
});

describe("a failing alert never blocks or fails the visitor", () => {
  const gateWith = async (extra: Record<string, unknown> = {}) => {
    const env = makeEnv(f, { ALERT_DISCORD_WEBHOOK: DISCORD, ...MATRIX, ...extra });
    return handleGate(gatePost(), env, ctxOf(f), { fetchFn: fakeFetch(f), nowMs: NOW });
  };

  for (const mode of ["throw", "500", "flood"] as const) {
    it(`sink ${mode}: the unlock still succeeds`, async () => {
      f.alert = mode;
      const res = await gateWith();
      expect(res.status).toBe(303);
      expect(res.headers.get("Location")).toBe("/k/view");
      expect(res.headers.get("Set-Cookie")).not.toBeNull();
      await settle(f);
      expect(await DB.prepare("SELECT COUNT(*) AS n FROM leads").first<{ n: number }>()).toEqual({ n: 1 });
    });
  }

  it("a sink that never answers does not delay the response, and the background work ends at the timeout", async () => {
    f.alert = "hang";
    const started = Date.now();
    const res = await gateWith();
    expect(res.headers.get("Location")).toBe("/k/view");
    expect(Date.now() - started).toBeLessThan(1500);
    // The alert itself is bounded by its own timeout.
    const t0 = Date.now();
    await sendAlert(aenv({ ALERT_DISCORD_WEBHOOK: DISCORD }), "lead_created", { fetchFn: fakeFetch(f), nowMs: NOW, timeoutMs: 50 });
    expect(Date.now() - t0).toBeLessThan(1500);
  });

  it("an alert is scheduled for a new address only, and with ctx.waitUntil", async () => {
    await gateWith();
    expect(f.waiting).toHaveLength(1);
    await settle(f);
    await gateWith();
    expect(f.waiting).toHaveLength(1);
  });

  it("an unbounded response body is cut off at the cap and the call returns", async () => {
    f.alert = "flood";
    const t0 = Date.now();
    await send({ ALERT_DISCORD_WEBHOOK: DISCORD });
    expect(Date.now() - t0).toBeLessThan(2500);
  });

  it("a redirect from a sink is refused by the fetch (redirect: error) and counted as a failure", async () => {
    const logs = captureLogs();
    try {
      const redirecting = (async (_u: unknown, init?: RequestInit) => {
        if (init?.redirect === "error") throw new TypeError("redirect refused");
        return new Response(null, { status: 302, headers: { Location: "https://evil.example.test/" } });
      }) as typeof fetch;
      await sendAlert(aenv({ ALERT_DISCORD_WEBHOOK: DISCORD }), "lead_created", { fetchFn: redirecting, nowMs: NOW });
      expect(logs.lines.map((l) => JSON.parse(l))).toContainEqual({ e: "alert", o: "failed", r: "provider" });
    } finally {
      logs.restore();
    }
  });

  it("a sink that throws synchronously and a waitUntil that throws still do not fail the visitor", async () => {
    const throwingCtx = { waitUntil: () => { throw new Error("ctx gone"); } };
    const env = makeEnv(f, { ALERT_DISCORD_WEBHOOK: DISCORD });
    const res = await handleGate(gatePost(), env, throwingCtx, { fetchFn: fakeFetch(f), nowMs: NOW });
    expect(res.headers.get("Location")).toBe("/k/view");
  });
});
