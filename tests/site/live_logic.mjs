// SPDX-License-Identifier: Apache-2.0
// Node tests for the pure logic in static/live.js. Run through test_live_logic.py.
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const live = createRequire(import.meta.url)("./live.js");
const NOW = 1_800_000_000;

function body(over = {}) {
  return JSON.stringify({
    v: 1,
    as_of: NOW - 10,
    ttl: 30,
    kick: { live: false, since: null },
    twitch: { live: false, since: null },
    ...over,
  });
}

test("a fresh document parses to one value per platform", () => {
  const t = body({ kick: { live: true, since: NOW - 600 } });
  assert.deepEqual(live.parseStatus(t, NOW), { kick: true, twitch: false });
});

test("unknown (null) stays unknown", () => {
  const t = body({ kick: { live: null, since: null }, twitch: { live: true, since: NOW - 5 } });
  assert.deepEqual(live.parseStatus(t, NOW), { kick: null, twitch: true });
});

test("the kill-switch body (null as_of, all unknown) is degraded", () => {
  const t = JSON.stringify({
    v: 1, as_of: null, ttl: 30,
    kick: { live: null, since: null }, twitch: { live: null, since: null },
  });
  assert.equal(live.parseStatus(t, NOW), null);
});

test("an unknown-everything body with a fresh as_of is accepted but never live", () => {
  const t = body({ kick: { live: null, since: null }, twitch: { live: null, since: null } });
  const s = live.parseStatus(t, NOW);
  assert.deepEqual(s, { kick: null, twitch: null });
  assert.equal(live.viewState(s), "degraded");
});

test("staleness: at the limit is fresh, one second more is degraded", () => {
  assert.equal(live.STALE_AFTER_SECONDS, 1800);
  assert.notEqual(live.parseStatus(body({ as_of: NOW - 1800 }), NOW), null);
  assert.equal(live.parseStatus(body({ as_of: NOW - 1801 }), NOW), null);
});

test("an as_of far in the future is degraded; small skew is tolerated", () => {
  assert.notEqual(live.parseStatus(body({ as_of: NOW + 300 }), NOW), null);
  assert.equal(live.parseStatus(body({ as_of: NOW + 301 }), NOW), null);
});

test("stale data can never say live", () => {
  const t = body({ as_of: NOW - 5000, kick: { live: true, since: NOW - 6000 } });
  assert.equal(live.parseStatus(t, NOW), null);
});

test("an operator failure code is accepted, ignored and never returned", () => {
  const codes = ["ok", "not_configured", "auth_failed", "rate_limited", "upstream_error", "redirect", "too_large", "bad_record", "timeout"];
  for (const code of codes) {
    const t = body({ kick: { live: true, since: NOW - 5, code }, twitch: { live: false, since: null, code } });
    assert.deepEqual(live.parseStatus(t, NOW), { kick: true, twitch: false }, code);
  }
  const t = body({ kick: { live: null, since: null, code: "auth_failed" }, twitch: { live: null, since: null, code: null } });
  assert.deepEqual(live.parseStatus(t, NOW), { kick: null, twitch: null });
});

test("an unknown or odd code value is dropped: same result, no code, nothing rendered", () => {
  for (const code of ["SENTINEL free text", "AUTH_FAILED", "", 5, true, {}, [], null, "<img src=x onerror=1>"]) {
    const t = body({ kick: { live: true, since: NOW - 5, code }, twitch: { live: false, since: null } });
    const s = live.parseStatus(t, NOW);
    assert.deepEqual(s, { kick: true, twitch: false });
    assert.equal(JSON.stringify(s).includes("SENTINEL"), false);
  }
});

const BAD = {
  "not json": "{nope",
  "empty": "",
  "array": "[]",
  "null": "null",
  "string": '"x"',
  "wrong version": body({ v: 2 }),
  "string version": body({ v: "1" }),
  "missing ttl": JSON.stringify({ v: 1, as_of: NOW, kick: { live: false, since: null }, twitch: { live: false, since: null } }),
  "extra top key": body({ title: "x" }),
  "extra platform key": body({ kick: { live: true, since: NOW, title: "x" } }),
  "missing platform key": body({ kick: { live: true } }),
  "code plus another extra key": body({ kick: { live: false, since: null, code: "ok", detail: "x" } }),
  "extra key instead of code": body({ kick: { live: false, since: null, detail: "ok" } }),
  "code but missing since": body({ kick: { live: false, code: "ok" } }),
  "since on not-live with a code": body({ kick: { live: false, since: NOW, code: "ok" } }),
  "missing platform": JSON.stringify({ v: 1, as_of: NOW, ttl: 30, kick: { live: false, since: null } }),
  "live as string": body({ kick: { live: "true", since: null } }),
  "live as number": body({ kick: { live: 1, since: null } }),
  "live undefined-ish": body({ kick: { since: null } }),
  "since on not-live": body({ kick: { live: false, since: NOW } }),
  "since on unknown": body({ kick: { live: null, since: NOW } }),
  "since negative": body({ kick: { live: true, since: -1 } }),
  "since fractional": body({ kick: { live: true, since: 1.5 } }),
  "since huge": body({ kick: { live: true, since: 1e15 } }),
  "since string": body({ kick: { live: true, since: "5" } }),
  "as_of string": body({ as_of: String(NOW) }),
  "as_of fractional": body({ as_of: NOW + 0.5 }),
  "as_of negative": body({ as_of: -5 }),
  "as_of huge": body({ as_of: 1e15 }),
  "ttl zero": body({ ttl: 0 }),
  "ttl huge": body({ ttl: 1e9 }),
  "ttl string": body({ ttl: "30" }),
  "platform is array": body({ kick: [] }),
  "platform is null": body({ kick: null }),
  "proto key": '{"__proto__":{"x":1},"v":1,"as_of":' + NOW + ',"ttl":30,"kick":{"live":true,"since":null},"twitch":{"live":true,"since":null}}',
  "oversize": body() + " ".repeat(live.MAX_BODY_CHARS),
};
for (const [name, text] of Object.entries(BAD)) {
  test(`malformed is degraded: ${name}`, () => {
    assert.equal(live.parseStatus(text, NOW), null);
  });
}

test("non-string input is degraded", () => {
  for (const v of [undefined, null, 5, {}, []]) {
    assert.equal(live.parseStatus(v, NOW), null);
  }
});

test("viewState: live if either is live; offline only if both are false; else degraded", () => {
  const v = live.viewState;
  assert.equal(v({ kick: true, twitch: false }), "live");
  assert.equal(v({ kick: false, twitch: true }), "live");
  assert.equal(v({ kick: true, twitch: null }), "live");
  assert.equal(v({ kick: true, twitch: true }), "live");
  assert.equal(v({ kick: false, twitch: false }), "offline");
  assert.equal(v({ kick: false, twitch: null }), "degraded");
  assert.equal(v({ kick: null, twitch: false }), "degraded");
  assert.equal(v({ kick: null, twitch: null }), "degraded");
  assert.equal(v(null), "degraded");
});

test("viewState is symmetric in the two platforms", () => {
  const vals = [true, false, null];
  for (const a of vals) for (const b of vals) {
    assert.equal(live.viewState({ kick: a, twitch: b }), live.viewState({ kick: b, twitch: a }));
  }
});

test("badge shows only for a live platform", () => {
  assert.equal(live.badgeOn({ kick: true, twitch: null }), true);
  assert.equal(live.badgeOn({ kick: null, twitch: true }), true);
  assert.equal(live.badgeOn({ kick: false, twitch: false }), false);
  assert.equal(live.badgeOn({ kick: null, twitch: null }), false);
  assert.equal(live.badgeOn(null), false);
});

test("player URLs are exactly the allowlisted forms", () => {
  assert.equal(
    live.playerSrc("twitch", "example_twitch", "example.test"),
    "https://player.twitch.tv/?channel=example_twitch&parent=example.test",
  );
  assert.equal(live.playerSrc("kick", "example-kick_1", "example.test"), "https://player.kick.com/example-kick_1");
});

test("handles are validated with the config model's patterns", () => {
  const h = "example.test";
  for (const bad of ["", "a b", "../x", "a/b", "a&parent=evil.test", "a?b", "a#b", "é", "a\n", "x".repeat(26)]) {
    assert.equal(live.playerSrc("twitch", bad, h), null, bad);
  }
  assert.equal(live.playerSrc("twitch", "x".repeat(25), h) !== null, true);
  assert.equal(live.playerSrc("twitch", "a-b", h), null, "no hyphen on twitch");
  for (const bad of ["", "a b", "../x", "a/b", "a?b", "a.b", "x".repeat(41), "a\n"]) {
    assert.equal(live.playerSrc("kick", bad, h), null, bad);
  }
  assert.equal(live.playerSrc("kick", "x".repeat(40), h) !== null, true);
  assert.equal(live.playerSrc("kick", "a-b", h) !== null, true);
});

test("an unknown platform or a bad host gives no URL", () => {
  assert.equal(live.playerSrc("youtube", "example", "example.test"), null);
  assert.equal(live.playerSrc("__proto__", "example", "example.test"), null);
  assert.equal(live.playerSrc("constructor", "example", "example.test"), null);
  assert.equal(live.playerSrc("twitch", "example", ""), null);
  assert.equal(live.playerSrc("twitch", "example", undefined), null);
  assert.equal(live.playerSrc("twitch", "example", "a".repeat(254)), null);
  assert.equal(live.playerSrc("twitch", 5, "example.test"), null);
});

test("an odd host is encoded, never spliced", () => {
  const u = live.playerSrc("twitch", "example", "a&b=c");
  assert.equal(u, "https://player.twitch.tv/?channel=example&parent=a%26b%3Dc");
});

test("the platform table is two entries, frozen", () => {
  assert.equal(live.PLATFORMS.length, 2);
  assert.equal(Object.isFrozen(live.PLATFORMS), true);
});

// ---- the bounded body read --------------------------------------------------

const enc = new TextEncoder();

// A fake response whose body is a reader over the given chunks; it records what
// the script did (reads made, cancel called) and never trusts any header.
function fakeResponse(chunks, opts = {}) {
  const state = { reads: 0, cancelled: false };
  const reader = {
    read() {
      state.reads += 1;
      if (opts.failAt !== undefined && state.reads > opts.failAt) {
        return Promise.reject(new Error("stream error"));
      }
      const next = chunks.shift();
      return Promise.resolve(next === undefined ? { done: true } : { done: false, value: next });
    },
    cancel() {
      state.cancelled = true;
      return Promise.resolve();
    },
  };
  return { response: { body: { getReader: () => reader }, headers: { get: () => "5" } }, state };
}

test("a small body is read whole", async () => {
  const { response } = fakeResponse([enc.encode('{"a":'), enc.encode("1}")]);
  assert.equal(await live.readBounded(response), '{"a":1}');
});

test("a body of exactly the limit is accepted, one byte more is not", async () => {
  assert.equal(live.MAX_BODY_BYTES, 2048);
  const ok = fakeResponse([new Uint8Array(2048).fill(32)]);
  assert.equal((await live.readBounded(ok.response)).length, 2048);
  assert.equal(ok.state.cancelled, false);
  const over = fakeResponse([new Uint8Array(2048).fill(32), new Uint8Array(1).fill(32)]);
  assert.equal(await live.readBounded(over.response), null);
  assert.equal(over.state.cancelled, true);
});

test("an over-limit stream is cancelled and no further chunk is read", async () => {
  const chunks = [];
  for (let i = 0; i < 1000; i++) chunks.push(new Uint8Array(1024).fill(32));
  const { response, state } = fakeResponse(chunks);
  assert.equal(await live.readBounded(response), null);
  assert.equal(state.cancelled, true);
  assert.ok(state.reads <= 4, `read ${state.reads} chunks`);
});

test("the Content-Length header is never trusted", async () => {
  const { response } = fakeResponse([new Uint8Array(5000).fill(32)]);
  assert.equal(response.headers.get("content-length"), "5");
  assert.equal(await live.readBounded(response), null);
});

test("an unreadable body is degraded", async () => {
  assert.equal(await live.readBounded({ body: null }), null);
  assert.equal(await live.readBounded({}), null);
  assert.equal(await live.readBounded(null), null);
  const failing = fakeResponse([enc.encode("{")], { failAt: 1 });
  assert.equal(await live.readBounded(failing.response), null);
  const bad = fakeResponse([new Uint8Array([0xff, 0xfe])]);
  assert.equal(await live.readBounded(bad.response), null);
  const odd = fakeResponse(["text, not bytes"]);
  assert.equal(await live.readBounded(odd.response), null);
});
