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

function message(env, data, { origin = ORIGIN, fromFrame = true } = {}) {
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

test("the frame is hidden, sandboxed, not focusable and same origin", () => {
  const env = started();
  runIdle(env);
  const f = env.reach.frames[0];
  assert.equal(f.tag, "iframe");
  assert.equal(f.attrs.src, "/e-frame/");
  assert.equal(f.attrs.sandbox, "allow-scripts allow-same-origin");
  assert.equal(f.attrs["aria-hidden"], "true");
  assert.equal(f.attrs.tabindex, "-1");
  assert.ok(f.attrs.title && f.attrs.title.length > 0);
  assert.equal(f.attrs.width, "0");
  assert.equal(f.attrs.height, "0");
});

test("a valid token from our frame settles as verified and removes the frame", () => {
  const env = started();
  runIdle(env);
  message(env, { type: "reach-around-beacon", state: "token", token: GOOD });
  assert.deepEqual({ ...settled(env) }, { state: "verified", token: GOOD });
  assert.equal(env.reach.frames[0].parentNode, null);
  assert.equal(env.reach.events.length, 1);
  assert.equal(env.reach.events[0].type, "reach-around:beacon");
  assert.equal(env.reach.listeners.window.message.length, 0);
});

test("a failure message settles as unverified with no token", () => {
  const env = started();
  runIdle(env);
  message(env, { type: "reach-around-beacon", state: "failed" });
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
  message(env, { type: "reach-around-beacon", state: "token", token: GOOD });
  assert.deepEqual({ ...settled(env) }, { state: "unverified" });
  assert.equal(env.reach.events.length, 1);
});

test("a message from another origin or another window is ignored", () => {
  const env = started();
  runIdle(env);
  const ok = { type: "reach-around-beacon", state: "token", token: GOOD };
  message(env, ok, { origin: "https://evil.test" });
  message(env, ok, { origin: "null" });
  message(env, ok, { fromFrame: false });
  assert.equal(settled(env), undefined);
  assert.equal(env.reach.events.length, 0);
});

test("a malformed message never produces a verified state", () => {
  for (const bad of [
    null,
    "token",
    42,
    { type: "other", state: "token", token: GOOD },
    { type: "reach-around-beacon", state: "token" },
    { type: "reach-around-beacon", state: "token", token: "" },
    { type: "reach-around-beacon", state: "token", token: "a b" },
    { type: "reach-around-beacon", state: "token", token: "<x>" },
    { type: "reach-around-beacon", state: "token", token: "a".repeat(4097) },
    { type: "reach-around-beacon", state: "token", token: { toString: () => GOOD } },
    { type: "reach-around-beacon", state: "ok", token: GOOD },
  ]) {
    const env = started();
    runIdle(env);
    message(env, bad);
    assert.notEqual(settled(env) && settled(env).state, "verified", JSON.stringify(bad));
  }
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
  const src = fs.readFileSync(new URL("../../src/reach_around/site/static/beacon.js", import.meta.url), "utf8");
  for (const word of ["addEventListener(\"click\"", "preventDefault", "localStorage", "sessionStorage", "cookie", "indexedDB", "fetch(", "sendBeacon", "innerHTML", "eval("]) {
    assert.ok(!src.includes(word), word);
  }
});

// --- helper page side --------------------------------------------------------

function helper({ sitekey = "1x00000000000000000000AA", turnstile, parent = true } = {}) {
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
    location: { origin: ORIGIN },
    document: { getElementById: (id) => (id === "ra-turnstile" ? el : null) },
    Object,
  };
  vm.runInNewContext(
    fs.readFileSync(new URL("../../src/reach_around/site/static/beacon-frame.js", import.meta.url), "utf8"),
    env,
  );
  return { posted, rendered, el, win };
}

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

test("helper posts a token to the same origin only", () => {
  const h = helper({ turnstile: "ok" });
  h.rendered[0][1].callback(GOOD);
  assert.deepEqual(plain(h.posted), [[{ type: "reach-around-beacon", state: "token", token: GOOD }, ORIGIN]]);
  h.rendered[0][1]["expired-callback"]();
  h.rendered[0][1]["error-callback"]();
  assert.equal(h.posted.length, 1, "one message only");
});

test("helper reports failure on error, timeout and expiry callbacks", () => {
  for (const cb of ["error-callback", "timeout-callback", "expired-callback"]) {
    const h = helper({ turnstile: "ok" });
    h.rendered[0][1][cb]();
    assert.deepEqual(plain(h.posted), [[{ type: "reach-around-beacon", state: "failed" }, ORIGIN]], cb);
  }
});

test("helper reports failure when the Turnstile script is missing", () => {
  const h = helper({});
  assert.deepEqual(plain(h.posted), [[{ type: "reach-around-beacon", state: "failed" }, ORIGIN]]);
});

test("helper reports failure when render throws", () => {
  const h = helper({ turnstile: { render() { throw new Error("x"); } } });
  assert.deepEqual(plain(h.posted), [[{ type: "reach-around-beacon", state: "failed" }, ORIGIN]]);
});

test("helper refuses a bad site key and a bad token", () => {
  const h = helper({ turnstile: "ok", sitekey: "bad key!" });
  assert.equal(h.rendered.length, 0);
  assert.equal(h.posted[0][0].state, "failed");
  const g = helper({ turnstile: "ok" });
  g.rendered[0][1].callback("not a token");
  assert.equal(g.posted[0][0].state, "failed");
});

test("helper opened on its own (no parent) renders nothing and posts nothing", () => {
  const h = helper({ turnstile: "ok", parent: false });
  assert.equal(h.rendered.length, 0);
  assert.equal(h.posted.length, 0);
});

test("helper source has no storage, network call or wildcard target", () => {
  const src = fs.readFileSync(new URL("../../src/reach_around/site/static/beacon-frame.js", import.meta.url), "utf8");
  for (const word of ["localStorage", "sessionStorage", "cookie", "indexedDB", "fetch(", "XMLHttpRequest", "sendBeacon", "\"*\"", "innerHTML", "eval("]) {
    assert.ok(!src.includes(word), word);
  }
});
