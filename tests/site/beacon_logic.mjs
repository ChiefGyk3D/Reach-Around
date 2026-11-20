// SPDX-License-Identifier: Apache-2.0
// Node tests for static/beacon.js (the page side) and static/beacon-frame.js (the
// helper page side), against a fake window. Run through test_beacon_frame.py.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const ORIGIN = "https://example.test";
const plain = (x) => JSON.parse(JSON.stringify(x));
const GOOD = "0.AbCd-_efgh.1234";
// The fake crypto fills 1, 2, 3 ... so the page's nonce is known.
// The source without comment lines: the scans look at code, not at prose.
const code = (text) =>
  text
    .split("\n")
    .filter((l) => !l.trim().startsWith("//"))
    .join("\n");
const NONCE = "0102030405060708090a0b0c0d0e0f10";
const T = "reach-around-beacon";
const msg = (extra = {}) => ({ type: T, nonce: NONCE, ...extra });

function load(file, env) {
  vm.runInNewContext(fs.readFileSync(new URL(file, import.meta.url), "utf8"), env);
}

// --- page side ---------------------------------------------------------------

function page(over = {}) {
  const timers = [];
  const listeners = { window: {}, document: {} };
  const frames = [];
  const events = [];
  const doc = {
    readyState: "complete",
    prerendering: false,
    body: {
      appendChild(node) {
        node.parentNode = this;
        frames.push(node);
      },
      removeChild(node) {
        node.parentNode = null;
      },
    },
    createElement(tag) {
      const node = {
        tag,
        attrs: {},
        style: {},
        contentWindow: { id: "frame-window" },
        parentNode: null,
        setAttribute(k, v) {
          this.attrs[k] = String(v);
        },
        getAttribute(k) {
          return this.attrs[k] ?? null;
        },
      };
      return node;
    },
    addEventListener(type, fn) {
      (listeners.document[type] ||= []).push(fn);
    },
    removeEventListener(type, fn) {
      listeners.document[type] = (listeners.document[type] || []).filter((f) => f !== fn);
    },
    dispatchEvent(ev) {
      events.push(ev);
      return true;
    },
  };
  const win = {
    addEventListener(type, fn) {
      (listeners.window[type] ||= []).push(fn);
    },
    removeEventListener(type, fn) {
      listeners.window[type] = (listeners.window[type] || []).filter((f) => f !== fn);
    },
  };
  const env = {
    navigator: {},
    document: doc,
    window: win,
    location: { origin: ORIGIN },
    CustomEvent: class {
      constructor(type, init) {
        this.type = type;
        this.detail = init && init.detail;
      }
    },
    setTimeout(fn, ms) {
      timers.push({ fn, ms, live: true });
      return timers.length;
    },
    clearTimeout(id) {
      if (timers[id - 1]) timers[id - 1].live = false;
    },
    requestIdleCallback(fn, opts) {
      env.idle = { fn, opts };
    },
    Object,
    String,
    crypto: {
      getRandomValues(arr) {
        for (let i = 0; i < arr.length; i++) arr[i] = i + 1;
        return arr;
      },
    },
    ...over,
  };
  win.window = win;
  env.window = Object.assign(win, over.windowProps || {});
  env.reach = { timers, listeners, frames, events };
  return env;
}

function started(over) {
  const env = page(over);
  load("../../src/reach_around/site/static/beacon.js", env);
  return env;
}

function runIdle(env) {
  assert.ok(env.idle, "an idle callback was scheduled");
  env.idle.fn();
}

function message(env, data, { origin = "null", fromFrame = true } = {}) {
  const frame = env.reach.frames[0];
  const source = fromFrame ? frame.contentWindow : { id: "someone-else" };
  for (const fn of env.reach.listeners.window.message || []) {
    fn({ origin, source, data });
  }
}

const settled = (env) => env.window.reachAroundBeacon;

test("nothing loads before the page is idle", () => {
  const env = started();
  assert.equal(env.reach.frames.length, 0);
  runIdle(env);
  assert.equal(env.reach.frames.length, 1);
});

test("the frame is hidden, sandboxed without same-origin, not focusable", () => {
  const env = started();
  runIdle(env);
  const f = env.reach.frames[0];
  assert.equal(f.tag, "iframe");
  assert.equal(f.attrs.src, "/e-frame/#" + NONCE);
  assert.equal(f.attrs.sandbox, "allow-scripts");
  assert.equal(f.attrs["aria-hidden"], "true");
  assert.equal(f.attrs.tabindex, "-1");
  assert.ok(f.attrs.title && f.attrs.title.length > 0);
  assert.equal(f.attrs.width, "0");
  assert.equal(f.attrs.height, "0");
});

test("a valid token from our frame settles as verified and removes the frame", () => {
  const env = started();
  runIdle(env);
  message(env, msg({ state: "token", token: GOOD }));
  assert.deepEqual({ ...settled(env) }, { state: "verified", token: GOOD });
  assert.equal(env.reach.frames[0].parentNode, null);
  assert.equal(env.reach.events.length, 1);
  assert.equal(env.reach.events[0].type, "reach-around:beacon");
  assert.equal(env.reach.listeners.window.message.length, 0);
});

test("a failure message settles as unverified with no token", () => {
  const env = started();
  runIdle(env);
  message(env, msg({ state: "failed" }));
  assert.deepEqual({ ...settled(env) }, { state: "unverified" });
  assert.equal(env.reach.frames[0].parentNode, null);
});

test("the timeout settles as unverified and removes the frame", () => {
  const env = started();
  runIdle(env);
  const t = env.reach.timers.find((x) => x.live);
  assert.ok(t, "a timeout was set");
  assert.ok(t.ms > 0 && t.ms <= 8000, "the timeout is short");
  t.fn();
  assert.deepEqual({ ...settled(env) }, { state: "unverified" });
  assert.equal(env.reach.frames[0].parentNode, null);
});

test("a late token after the timeout changes nothing", () => {
  const env = started();
  runIdle(env);
  env.reach.timers.find((x) => x.live).fn();
  message(env, msg({ state: "token", token: GOOD }));
  assert.deepEqual({ ...settled(env) }, { state: "unverified" });
  assert.equal(env.reach.events.length, 1);
});

test("only the opaque origin from our own frame is accepted", () => {
  const env = started();
  runIdle(env);
  const ok = msg({ state: "token", token: GOOD });
  message(env, ok, { origin: "https://evil.test" });
  message(env, ok, { origin: ORIGIN });
  message(env, ok, { origin: "" });
  message(env, ok, { fromFrame: false });
  assert.equal(settled(env), undefined);
  assert.equal(env.reach.events.length, 0);
});

test("a malformed message from our frame never produces a verified state", () => {
  for (const bad of [
    null,
    "token",
    42,
    [],
    { type: "other", nonce: NONCE, state: "token", token: GOOD },
    msg({ state: "token" }),
    msg({ state: "token", token: "" }),
    msg({ state: "token", token: "a b" }),
    msg({ state: "token", token: "<x>" }),
    msg({ state: "token", token: "a".repeat(4097) }),
    msg({ state: "token", token: { toString: () => GOOD } }),
    msg({ state: "ok", token: GOOD }),
    msg({ state: "token", token: GOOD, extra: 1 }),
    msg({ state: "failed", token: GOOD }),
    Object.create(msg({ state: "token", token: GOOD })),
  ]) {
    const env = started();
    runIdle(env);
    message(env, bad);
    assert.notEqual(settled(env) && settled(env).state, "verified", JSON.stringify(bad));
  }
});

test("a message without our nonce, or with another, is ignored and settles nothing", () => {
  for (const bad of [
    { type: T, state: "token", token: GOOD },
    { type: T, nonce: "0".repeat(32), state: "token", token: GOOD },
    { type: T, nonce: NONCE.toUpperCase(), state: "token", token: GOOD },
    { type: T, nonce: [NONCE], state: "token", token: GOOD },
    { type: T, nonce: 1, state: "failed" },
  ]) {
    const env = started();
    runIdle(env);
    message(env, bad);
    assert.equal(settled(env), undefined, JSON.stringify(bad));
    assert.equal(env.reach.events.length, 0);
  }
});

test("each page load makes its own nonce", () => {
  let n = 0;
  const env = started({
    crypto: {
      getRandomValues(arr) {
        n += 1;
        for (let i = 0; i < arr.length; i++) arr[i] = n * 16 + i;
        return arr;
      },
    },
  });
  runIdle(env);
  assert.equal(n, 1);
  const hash = env.reach.frames[0].attrs.src.split("#")[1];
  assert.match(hash, /^[0-9a-f]{32}$/);
  assert.notEqual(hash, NONCE);
});

test("no random source means no frame and an unverified state", () => {
  const env = started({ crypto: undefined });
  runIdle(env);
  assert.equal(env.reach.frames.length, 0);
  assert.deepEqual({ ...settled(env) }, { state: "unverified" });
});

test("Global Privacy Control loads nothing and schedules nothing", () => {
  const env = started({ navigator: { globalPrivacyControl: true } });
  assert.equal(env.idle, undefined);
  assert.equal(env.reach.frames.length, 0);
  assert.equal(env.reach.timers.length, 0);
  assert.equal(settled(env), undefined);
});

test("Do Not Track in every spelling loads nothing", () => {
  const cases = [
    { navigator: { doNotTrack: "1" } },
    { navigator: { doNotTrack: " YES " } },
    { navigator: { doNotTrack: "true" } },
    { navigator: { doNotTrack: 1 } },
    { navigator: { msDoNotTrack: "1" } },
    { windowProps: { doNotTrack: "1" } },
  ];
  for (const over of cases) {
    const env = started(over);
    assert.equal(env.idle, undefined, JSON.stringify(over));
    assert.equal(env.reach.frames.length, 0, JSON.stringify(over));
  }
});

test("a DNT value of 0 or unspecified still loads", () => {
  for (const nav of [{ doNotTrack: "0" }, { doNotTrack: "unspecified" }, { doNotTrack: null }]) {
    const env = started({ navigator: nav });
    runIdle(env);
    assert.equal(env.reach.frames.length, 1);
  }
});

test("a signal that arrives before the idle callback still wins", () => {
  const env = started();
  env.navigator.globalPrivacyControl = true;
  runIdle(env);
  assert.equal(env.reach.frames.length, 0);
});

test("a page still loading waits for the load event", () => {
  const env = page();
  env.document.readyState = "loading";
  load("../../src/reach_around/site/static/beacon.js", env);
  assert.equal(env.idle, undefined);
  assert.equal(env.reach.frames.length, 0);
  for (const fn of env.reach.listeners.window.load || []) fn();
  runIdle(env);
  assert.equal(env.reach.frames.length, 1);
});

test("a prerendered page waits until it is shown", () => {
  const env = page();
  env.document.prerendering = true;
  load("../../src/reach_around/site/static/beacon.js", env);
  assert.equal(env.idle, undefined);
  for (const fn of env.reach.listeners.document.prerenderingchange || []) fn();
  runIdle(env);
  assert.equal(env.reach.frames.length, 1);
});

test("a script error while building the frame is swallowed", () => {
  const env = page();
  env.document.createElement = () => {
    throw new Error("blocked");
  };
  load("../../src/reach_around/site/static/beacon.js", env);
  assert.doesNotThrow(() => runIdle(env));
  assert.deepEqual({ ...settled(env) }, { state: "unverified" });
});

test("the page script never touches links or storage", () => {
  const src = code(fs.readFileSync(new URL("../../src/reach_around/site/static/beacon.js", import.meta.url), "utf8"));
  for (const word of ["addEventListener(\"click\"", "preventDefault", "localStorage", "sessionStorage", "cookie", "indexedDB", "fetch(", "sendBeacon", "innerHTML", "eval("]) {
    assert.ok(!src.includes(word), word);
  }
});

// --- helper page side --------------------------------------------------------

function helper({ sitekey = "1x00000000000000000000AA", turnstile, parent = true, hash = "#" + NONCE } = {}) {
  const posted = [];
  const rendered = [];
  const el = {
    attrs: { "data-sitekey": sitekey, "data-action": "beacon" },
    getAttribute(k) {
      return this.attrs[k] ?? null;
    },
  };
  const win = {};
  win.window = win;
  win.parent = parent ? { postMessage: (m, o) => posted.push([m, o]) } : win;
  if (turnstile === "ok") {
    win.turnstile = { render: (node, opts) => (rendered.push([node, opts]), "w1") };
  } else if (turnstile) {
    win.turnstile = turnstile;
  }
  const env = {
    window: win,
    // An opaque origin has no usable origin string: the target must be "*".
    location: { origin: "null", hash },
    document: { getElementById: (id) => (id === "ra-turnstile" ? el : null) },
    Object,
  };
  vm.runInNewContext(
    fs.readFileSync(new URL("../../src/reach_around/site/static/beacon-frame.js", import.meta.url), "utf8"),
    env,
  );
  return { posted, rendered, el, win };
}

const failed = [[{ type: T, nonce: NONCE, state: "failed" }, "*"]];

test("helper renders once with the action and no retry", () => {
  const h = helper({ turnstile: "ok" });
  assert.equal(h.rendered.length, 1);
  const opts = h.rendered[0][1];
  assert.equal(opts.sitekey, "1x00000000000000000000AA");
  assert.equal(opts.action, "beacon");
  assert.equal(opts.retry, "never");
  assert.equal(opts.tabindex, -1);
  assert.equal(h.posted.length, 0);
});

test("helper posts a token with the nonce, to its parent only", () => {
  const h = helper({ turnstile: "ok" });
  h.rendered[0][1].callback(GOOD);
  assert.deepEqual(plain(h.posted), [[{ type: T, nonce: NONCE, state: "token", token: GOOD }, "*"]]);
  h.rendered[0][1]["expired-callback"]();
  h.rendered[0][1]["error-callback"]();
  assert.equal(h.posted.length, 1, "one message only");
});

test("helper reports failure on error, timeout and expiry callbacks", () => {
  for (const cb of ["error-callback", "timeout-callback", "expired-callback"]) {
    const h = helper({ turnstile: "ok" });
    h.rendered[0][1][cb]();
    assert.deepEqual(plain(h.posted), failed, cb);
  }
});

test("helper reports failure when the Turnstile script is missing", () => {
  assert.deepEqual(plain(helper({}).posted), failed);
});

test("helper reports failure when render throws", () => {
  const h = helper({ turnstile: { render() { throw new Error("x"); } } });
  assert.deepEqual(plain(h.posted), failed);
});

test("helper refuses a bad site key and a bad token", () => {
  const h = helper({ turnstile: "ok", sitekey: "bad key!" });
  assert.equal(h.rendered.length, 0);
  assert.equal(h.posted[0][0].state, "failed");
  const g = helper({ turnstile: "ok" });
  g.rendered[0][1].callback("not a token");
  assert.equal(g.posted[0][0].state, "failed");
});

test("helper without a valid nonce in its address renders and posts nothing", () => {
  for (const hash of ["", "#", "#abc", "#" + "G".repeat(32), "#" + NONCE.toUpperCase(), "#" + NONCE + "0"]) {
    const h = helper({ turnstile: "ok", hash });
    assert.equal(h.rendered.length, 0, hash);
    assert.equal(h.posted.length, 0, hash);
  }
});

test("helper opened on its own (no parent) renders nothing and posts nothing", () => {
  const h = helper({ turnstile: "ok", parent: false });
  assert.equal(h.rendered.length, 0);
  assert.equal(h.posted.length, 0);
});

test("helper source has no storage or network call, and one post, to the parent", () => {
  const src = code(fs.readFileSync(new URL("../../src/reach_around/site/static/beacon-frame.js", import.meta.url), "utf8"));
  for (const word of ["localStorage", "sessionStorage", "cookie", "indexedDB", "fetch(", "XMLHttpRequest", "sendBeacon", "innerHTML", "eval(", "top.", "opener"]) {
    assert.ok(!src.includes(word), word);
  }
  assert.equal(src.split("postMessage(").length - 1, 1);
  assert.ok(src.includes("window.parent.postMessage("));
});
