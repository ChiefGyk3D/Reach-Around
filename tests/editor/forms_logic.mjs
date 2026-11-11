// SPDX-License-Identifier: Apache-2.0
// Node tests for the editor forms: op building, error handling, form state and
// the rendered form, with stubs for fetch and a tiny fake DOM. Run by
// test_forms_logic.py from a copy of the assets next to this file.
import assert from "node:assert/strict";
import { test } from "node:test";

class Node_ {
  constructor(tag) {
    this.tag = tag;
    this.attrs = new Map();
    this.children = [];
    this.listeners = new Map();
    this.own = "";
    this.value = "";
    this.checked = false;
  }
  setAttribute(name, value) {
    this.attrs.set(name, String(value));
  }
  getAttribute(name) {
    return this.attrs.has(name) ? this.attrs.get(name) : null;
  }
  hasAttribute(name) {
    return this.attrs.has(name);
  }
  removeAttribute(name) {
    this.attrs.delete(name);
  }
  append(...nodes) {
    for (const node of nodes) {
      this.children.push(typeof node === "string" ? Object.assign(new Node_("#text"), { own: node }) : node);
    }
  }
  replaceChildren(...nodes) {
    this.children = [];
    this.own = "";
    this.append(...nodes);
  }
  set textContent(value) {
    this.children = [];
    this.own = String(value);
  }
  get textContent() {
    return this.own + this.children.map((c) => c.textContent).join("");
  }
  addEventListener(name, fn) {
    this.listeners.set(name, fn);
  }
  async fire(name) {
    const event = { preventDefault() {} };
    await this.listeners.get(name)(event);
  }
  focus() {
    globalThis.activeNode = this;
  }
  all(test_) {
    const out = [];
    const walk = (node) => {
      if (test_(node)) out.push(node);
      node.children.forEach(walk);
    };
    walk(this);
    return out;
  }
  querySelectorAll(selector) {
    assert.equal(selector, "[data-key]");
    return this.all((n) => n.hasAttribute("data-key"));
  }
}

globalThis.document = { createElement: (tag) => new Node_(tag) };

const { STRINGS, t } = await import("./strings.js");
const store = await import("./ops.js");
const { ApiError, createClient } = await import("./api.js");
const { renderEditor } = await import("./forms.js");

const HASH = "a".repeat(64);
const CONFIG = {
  creator: { name: "Example", title: "Links" },
  theme: { mode: "dark", background_dim: 0.2, colors: { bg: "#ffffff" } },
  tabs: [
    {
      id: "main",
      title: "Main",
      blocks: [
        { id: "first", title: "First", url: "https://example.com/1", affiliate: true },
        { id: "grp", title: "Group", links: [
          { id: "in-a", title: "In A", url: "https://example.com/a" },
          { id: "in-b", title: "In B", url: "https://example.com/b", subtitle: "sub" },
        ] },
        { id: "socials", layout: "chips", items: [
          { platform: "twitch", handle: "me", url: "https://example.com/t", count: 10, as_of: "2027-03-01" },
          { platform: "kick", handle: "me", url: "https://example.com/k" },
        ] },
        { id: "second", title: "Second", url: "https://example.com/2" },
      ],
    },
  ],
};

function newStore(config = CONFIG, hash = HASH) {
  return store.createStore({ phase: "ready", message: "status.ready", config, hash, previewUrl: "", buildError: "" });
}

function reply(config, hash, extra = {}) {
  return { state: { config, hash, preview_url: "http://127.0.0.1:1/", build: { ok: true, error: null } }, history: {}, ...extra };
}

test("set_link_field op carries the old value in expect and null for a blank optional", () => {
  const op = store.setLinkFieldOp(CONFIG, "in-b", "subtitle", store.linkValue("subtitle", "  "));
  assert.deepEqual(op.args, { id: "in-b", field: "subtitle", value: null });
  assert.deepEqual(op.expect, { value: "sub" });
  assert.equal(op.op, "set_link_field");
  assert.ok(op.label.length >= 1 && op.label.length <= 80);
  const missing = store.setLinkFieldOp(CONFIG, "first", "icon", "star");
  assert.deepEqual(missing.expect, { value: null });
});

test("link steps list only the fields that changed", () => {
  const draft = { title: "First", url: "https://example.com/1", subtitle: "", icon: "", affiliate: true, featured: false };
  assert.deepEqual(store.linkSteps(CONFIG, "first", draft), []);
  const steps = store.linkSteps(CONFIG, "first", { ...draft, title: " New ", affiliate: false });
  assert.deepEqual(steps.map((s) => s.field), ["title", "affiliate"]);
  assert.deepEqual(steps[0].build(CONFIG).args, { id: "first", field: "title", value: "New" });
  assert.deepEqual(steps[1].build(CONFIG).args.value, false);
  assert.deepEqual(store.linkSteps(CONFIG, "gone", draft), []);
});

test("setting steps skip untouched defaults and refuse a dim that is not a number", () => {
  const same = { "theme.mode": "dark", "theme.background_dim": "0.2", "theme.colors.bg": "#ffffff", "theme.colors.text": "", "creator.name": "Example" };
  assert.deepEqual(store.settingSteps(CONFIG, same), { steps: [], errors: {} });
  const bare = store.settingSteps({}, { "theme.mode": "auto", "theme.background_dim": "", "theme.colors.bg": "" });
  assert.deepEqual(bare.steps, []);
  const bad = store.settingSteps(CONFIG, { "theme.background_dim": "dark" });
  assert.deepEqual(Object.keys(bad.errors), ["theme.background_dim"]);
  assert.deepEqual(bad.steps, []);
  const dim = store.settingSteps(CONFIG, { "theme.background_dim": "0.4", "theme.colors.accent": " #123456 " });
  const ops = dim.steps.map((s) => s.build(CONFIG));
  assert.deepEqual(ops[0].args, { key: "theme.background_dim", value: 0.4 });
  assert.deepEqual(ops[0].expect, { value: 0.2 });
  assert.deepEqual(ops[1].args, { key: "theme.colors.accent", value: "#123456" });
  assert.deepEqual(ops[1].expect, { value: null });
});

test("platform steps send blanks as null and expect the handle too", () => {
  const draft = { handle: "me", url: "https://example.com/t", name: "", label: "", count: "", as_of: "2027-03-01" };
  const steps = store.platformSteps(CONFIG, "socials", 1, draft);
  assert.deepEqual(steps.map((s) => s.field), ["count"]);
  const op = steps[0].build(CONFIG);
  assert.deepEqual(op.args, { block: "socials", index: 1, field: "count", value: null });
  assert.deepEqual(op.expect, { value: 10, handle: "me" });
  const added = store.platformSteps(CONFIG, "socials", 2, { handle: "me", url: "https://example.com/k", name: "", label: "", count: " 42 ", as_of: "" });
  assert.equal(added.length, 1);
  assert.equal(added[0].build(CONFIG).args.value, "42");
});

test("link moves skip non-links and announce link-only positions", async () => {
  const calls = [];
  const s = newStore();
  const client = { post: async (route, body) => { calls.push(body); return reply(CONFIG, HASH); } };
  const actions = store.createActions({ store: s, saver: store.createSaver({ client, store: s, announce() {} }), announce() {} });
  const up = await actions.moveLink("second", -1);
  assert.equal(up.ok, true);
  assert.deepEqual(calls[0].args, { id: "second", position: 1 });
  assert.deepEqual(calls[0].expect, { order: ["first", "grp", "socials", "second"] });
  const inGroup = await actions.moveLink("in-a", 1);
  assert.deepEqual(calls[1].args, { id: "in-a", position: 2 });
  assert.deepEqual(calls[1].expect, { order: ["in-a", "in-b"] });
  const edge = await actions.moveLink("first", -1);
  assert.equal(edge.moved, false);
  assert.equal(calls.length, 2);
  await actions.movePlatform("socials", 1, 1);
  assert.deepEqual(calls[2].args, { block: "socials", index: 1, position: 2 });
  assert.equal(calls[2].expect.items.length, 2);
  assert.equal((await actions.movePlatform("socials", 2, 1)).moved, false);
});

test("add and remove actions build the existing editor operations", async () => {
  const calls = [];
  const s = newStore();
  const client = { post: async (_route, body) => { calls.push(body); return reply(CONFIG, HASH); } };
  const actions = store.createActions({ store: s, saver: store.createSaver({ client, store: s, announce() {} }), announce() {} });
  await actions.addLink("main", { title: "New", url: "https://example.com/new" });
  await actions.removeLink("first");
  await actions.addPlatform("socials", { platform: "youtube", handle: "sample", url: "https://example.com/y" });
  await actions.removePlatform("socials", 2);
  assert.deepEqual(calls.map((call) => call.op), ["add_link", "remove_link", "add_platform_item", "remove_platform_item"]);
  assert.deepEqual(calls[0].expect, { count: 4 });
  assert.deepEqual(calls[1].expect, { order: ["first", "grp", "socials", "second"] });
  assert.equal(calls[2].expect.items.length, 2);
  assert.equal(calls[3].args.index, 2);
});

test("group title and subtitle are saved through field operations", async () => {
  const calls = [];
  const s = newStore();
  const client = { post: async (_route, body) => { calls.push(body); return reply(CONFIG, HASH); } };
  const actions = store.createActions({ store: s, saver: store.createSaver({ client, store: s, announce() {} }), announce() {} });
  await actions.saveGroup("grp", { title: "Changed", subtitle: "Details" });
  assert.deepEqual(calls.map((call) => call.args), [
    { id: "grp", field: "title", value: "Changed" },
    { id: "grp", field: "subtitle", value: "Details" },
  ]);
});

test("successful saves request a preview version refresh", async () => {
  let refreshed = 0;
  const s = newStore();
  const client = { post: async () => reply(CONFIG, HASH) };
  const saver = store.createSaver({ client, store: s, announce() {}, refreshPreview: async () => { refreshed += 1; } });
  await saver.save([{ field: "title", build: (config) => store.setLinkFieldOp(config, "first", "title", "Changed") }]);
  assert.equal(refreshed, 1);
});

test("the app redraw helper polls version and reloads the preview frame", async () => {
  const calls = [];
  const frame = new Node_("iframe");
  frame.setAttribute("src", "http://127.0.0.1:8001/");
  const original = frame.setAttribute.bind(frame);
  frame.setAttribute = (name, value) => { calls.push([name, value]); original(name, value); };
  const refresh = store.createPreviewRefresh({ get: async (route) => { calls.push(["get", route]); } }, frame);
  await refresh();
  assert.deepEqual(calls, [["get", "version"], ["src", "http://127.0.0.1:8001/"]]);
});

test("error classes: stale, field, limit, auth, network, other", () => {
  const stale = store.classify(new ApiError(409, "stale", "x", "expect.value", { state: { hash: HASH } }));
  assert.equal(stale.kind, "stale");
  assert.deepEqual(stale.state, { hash: HASH });
  assert.equal(store.classify(new ApiError(409, "media_limit", "full", "")).kind, "other");
  const field = store.classify(new ApiError(422, "refused", "title must be text", "args.value"));
  assert.deepEqual([field.kind, field.message, field.path], ["field", "title must be text", "args.value"]);
  const limit = store.classify(new ApiError(429, "rate_limited", "slow", "", { retryAfter: 7 }));
  assert.equal(limit.kind, "limit");
  assert.ok(limit.message.includes("7"));
  assert.equal(store.classify(new ApiError(429, "rate_limited", "slow", "")).message, t("save.limitNow"));
  assert.equal(store.classify(new ApiError(401, "unauthorized", "", "")).kind, "auth");
  assert.equal(store.classify(new ApiError(0, "network", "", "")).kind, "network");
  const other = store.classify(new ApiError(500, "http_500", "boom", ""));
  assert.equal(other.kind, "other");
  assert.equal(store.classify(new Error("plain")).kind, "network");
});

test("state keeps undo, redo and outside-edit availability", () => {
  const patched = store.patchFromState({
    config: CONFIG,
    hash: HASH,
    can_undo: true,
    can_redo: false,
    outside_edit: true,
  });
  assert.equal(patched.canUndo, true);
  assert.equal(patched.canRedo, false);
  assert.equal(patched.outsideEdit, true);
  assert.equal(store.patchFromState({}).canUndo, false);
  assert.equal(store.patchFromState({}).canRedo, false);
  assert.equal(store.patchFromState({}).outsideEdit, false);
});

test("history actions use the current hash and announce completed changes", async () => {
  assert.equal(typeof store.createHistoryActions, "function");
  const s = newStore();
  s.set({ canUndo: true, canRedo: true, outsideEdit: false });
  const calls = [];
  const said = [];
  const client = {
    get: async (route) => {
      calls.push(["GET", route]);
      return { entries: [{ id: 4, label: "Changed title", time: "2026-11-10T13:00:00Z", hash: HASH }], current: 4 };
    },
    post: async (route, body) => {
      calls.push(["POST", route, body]);
      return reply(CONFIG, "b".repeat(64), { history: { label: "Changed title" } });
    },
  };
  const history = store.createHistoryActions({ client, store: s, announce: (message) => said.push(message) });
  assert.equal((await history.load()).entries[0].label, "Changed title");
  await history.undo();
  assert.deepEqual(calls[1], ["POST", "undo", { base: HASH }]);
  await history.redo();
  assert.deepEqual(calls[2], ["POST", "redo", { base: "b".repeat(64) }]);
  await history.restore(4);
  assert.deepEqual(calls[3], ["POST", "history/restore", { id: 4, base: "b".repeat(64) }]);
  assert.ok(said.some((message) => message.startsWith("Undone:")));
  assert.ok(said.some((message) => message.startsWith("Redone:")));
  assert.ok(said.some((message) => message.startsWith("Restored:")));
});

test("outside-edit undo offers safe recovery and uses the fresh hash", async () => {
  assert.equal(typeof store.createHistoryActions, "function");
  const s = newStore();
  const freshHash = "c".repeat(64);
  const calls = [];
  const client = {
    post: async (route, body) => {
      calls.push([route, body]);
      if (calls.length === 1) {
        throw new ApiError(409, "outside_edit", "changed outside", "", {
          state: { config: CONFIG, hash: freshHash, can_undo: true, can_redo: false, outside_edit: true },
        });
      }
      return reply(CONFIG, "d".repeat(64), { history: { label: "Earlier version" } });
    },
  };
  const history = store.createHistoryActions({ client, store: s, announce() {} });
  const refused = await history.undo();
  assert.equal(refused.problem.kind, "outside-edit");
  assert.equal(refused.problem.action, "Restore the earlier version");
  assert.equal(s.get().outsideEdit, true);
  await history.restoreEarlier();
  assert.deepEqual(calls[1], ["undo", { base: freshHash, anyway: true }]);
});

test("editor shortcuts leave form editing alone", () => {
  assert.equal(typeof store.historyShortcut, "function");
  assert.equal(store.historyShortcut({ key: "z", ctrlKey: true, shiftKey: false, metaKey: false }, { tagName: "DIV", isContentEditable: false }), "undo");
  assert.equal(store.historyShortcut({ key: "Z", ctrlKey: true, shiftKey: true, metaKey: false }, { tagName: "DIV", isContentEditable: false }), "redo");
  assert.equal(store.historyShortcut({ key: "z", ctrlKey: true, shiftKey: false, metaKey: false }, { tagName: "INPUT", isContentEditable: false }), "");
  assert.equal(store.historyShortcut({ key: "z", ctrlKey: false, shiftKey: false, metaKey: false }, { tagName: "DIV", isContentEditable: false }), "");
});

test("api.js carries the fresh state of a 409 and Retry-After of a 429", async () => {
  const make = (status, body, headers) => async () => ({
    ok: false,
    status,
    headers: { get: (name) => headers[name] ?? null },
    json: async () => body,
  });
  const stale = createClient(() => "k".repeat(20), make(409, { error: { code: "stale", message: "m", path: "expect.value" }, state: { hash: HASH, config: {} } }, {}));
  await assert.rejects(stale.post("op", {}), (e) => e.status === 409 && e.state.hash === HASH && e.path === "expect.value");
  const limited = createClient(() => "k".repeat(20), make(429, { error: { code: "rate_limited", message: "m", path: "" } }, { "Retry-After": "12" }));
  await assert.rejects(limited.post("op", {}), (e) => e.retryAfter === 12);
  const junk = createClient(() => "k".repeat(20), make(429, {}, { "Retry-After": "-5" }));
  await assert.rejects(junk.post("op", {}), (e) => e.retryAfter === 0);
});

test("the saver sends each op with the hash the last reply gave and updates the store", async () => {
  const s = newStore();
  const seen = [];
  const hashes = ["b".repeat(64), "c".repeat(64)];
  const client = { post: async (route, body) => {
    seen.push(body.base);
    const next = { ...CONFIG, creator: { ...CONFIG.creator, name: "Changed" } };
    return reply(next, hashes[seen.length - 1], seen.length === 2 ? { warning: "no undo" } : {});
  } };
  const said = [];
  const saver = store.createSaver({ client, store: s, announce: (m) => said.push(m) });
  const { steps } = store.settingSteps(CONFIG, { "creator.name": "A", "creator.title": "B" });
  const result = await saver.save(steps);
  assert.equal(result.ok, true);
  assert.deepEqual(seen, [HASH, hashes[0]]);
  assert.equal(s.get().hash, hashes[1]);
  assert.equal(s.get().config.creator.name, "Changed");
  assert.equal(s.get().message, "status.saved");
  assert.ok(said.at(-1).includes("no undo"));
});

test("a stale reply shows the fresh state and stops; nothing else is sent", async () => {
  const s = newStore();
  const fresh = { ...CONFIG, creator: { name: "Elsewhere" } };
  let sent = 0;
  const client = { post: async () => {
    sent += 1;
    throw new ApiError(409, "stale", "x", "expect.value", { state: { config: fresh, hash: "d".repeat(64), build: { ok: false, error: "bad" } } });
  } };
  const said = [];
  const saver = store.createSaver({ client, store: s, announce: (m) => said.push(m) });
  const { steps } = store.settingSteps(CONFIG, { "creator.name": "A", "creator.title": "B" });
  const result = await saver.save(steps);
  assert.equal(result.ok, false);
  assert.equal(result.problem.kind, "stale");
  assert.equal(sent, 1);
  assert.equal(s.get().config, fresh);
  assert.equal(s.get().hash, "d".repeat(64));
  assert.equal(s.get().buildError, "bad");
  assert.equal(s.get().message, "status.stale");
  assert.deepEqual(said, [t("save.stale")]);
});

test("a 422 keeps the config, names the field and the message; a 429 reports the wait", async () => {
  const s = newStore();
  let mode = 422;
  const client = { post: async () => {
    throw mode === 422 ? new ApiError(422, "refused", "creator.name must be non-empty", "") : new ApiError(429, "rate_limited", "", "", { retryAfter: 9 });
  } };
  const saver = store.createSaver({ client, store: s, announce() {} });
  const { steps } = store.settingSteps(CONFIG, { "creator.name": "" });
  const first = await saver.save(steps);
  assert.deepEqual([first.ok, first.field, first.problem.message], [false, "creator.name", "creator.name must be non-empty"]);
  assert.equal(s.get().config, CONFIG);
  mode = 429;
  const second = await saver.save(steps);
  assert.equal(second.problem.kind, "limit");
  assert.ok(second.problem.message.includes("9"));
});

test("saves run one at a time", async () => {
  const s = newStore();
  const log = [];
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const client = { post: async (route, body) => {
    log.push("start " + body.args.key);
    if (body.args.key === "creator.name") await gate;
    log.push("end " + body.args.key);
    return reply(CONFIG, HASH);
  } };
  const saver = store.createSaver({ client, store: s, announce() {} });
  const one = saver.save(store.settingSteps(CONFIG, { "creator.name": "A" }).steps);
  const two = saver.save(store.settingSteps(CONFIG, { "creator.title": "B" }).steps);
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(log, ["start creator.name"]);
  release();
  await Promise.all([one, two]);
  assert.deepEqual(log, ["start creator.name", "end creator.name", "start creator.title", "end creator.title"]);
});

test("history waits for an in-flight save and uses the hash from its reply", async () => {
  const s = newStore();
  const log = [];
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const freshHash = "e".repeat(64);
  const client = { post: async (route, body) => {
    log.push(["start", route, body.base]);
    if (route === "op") {
      await gate;
      s.set(store.patchFromState(reply(CONFIG, freshHash).state));
    }
    log.push(["end", route, body.base]);
    return reply(CONFIG, freshHash);
  } };
  const operations = store.createOperationQueue();
  const saver = store.createSaver({ client, store: s, announce() {}, operations });
  const history = store.createHistoryActions({ client, store: s, announce() {}, operations });
  const saving = saver.save(store.settingSteps(CONFIG, { "creator.name": "A" }).steps);
  const undoing = history.undo();
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.deepEqual(log, [["start", "op", HASH]]);
  release();
  await Promise.all([saving, undoing]);
  assert.deepEqual(log, [
    ["start", "op", HASH],
    ["end", "op", HASH],
    ["start", "undo", freshHash],
    ["end", "undo", freshHash],
  ]);
});

test("an outside-change refresh replaces history metadata with the fresh state", async () => {
  const s = newStore();
  const freshHash = "f".repeat(64);
  const fresh = {
    config: { ...CONFIG, creator: { ...CONFIG.creator, name: "Outside" } },
    hash: freshHash,
    can_undo: false,
    can_redo: true,
    outside_edit: true,
  };
  const changed = await store.refreshOutsideState({
    client: { get: async (route) => {
      assert.equal(route, "state");
      return fresh;
    } },
    store: s,
  });
  assert.equal(changed, true);
  assert.equal(s.get().hash, freshHash);
  assert.equal(s.get().canUndo, false);
  assert.equal(s.get().canRedo, true);
  assert.equal(s.get().outsideEdit, true);
  assert.equal(s.get().outsideChanged, true);
  assert.equal(s.get().message, "status.outside");
});

function draw(config = CONFIG, actions = {}) {
  const container = new Node_("div");
  const runs = [];
  const ctx = { actions, run: async (task) => { runs.push(1); return task(); } };
  renderEditor(container, config, ctx);
  return { container, runs };
}

test("every control has a label tied to it and a description", () => {
  const { container } = draw();
  const controls = container.all((n) => ["input", "select"].includes(n.tag));
  assert.ok(controls.length > 25);
  const labels = container.all((n) => n.tag === "label");
  for (const control of controls) {
    const id = control.getAttribute("id");
    assert.ok(id, "control has an id");
    const label = labels.find((l) => l.getAttribute("for") === id);
    assert.ok(label && label.textContent.length > 0, id);
    for (const part of control.getAttribute("aria-describedby").split(" ")) {
      assert.equal(container.all((n) => n.getAttribute("id") === part).length, 1, part);
    }
  }
  const buttons = container.all((n) => n.tag === "button");
  assert.ok(buttons.length > 8);
  for (const button of buttons) {
    assert.ok(button.textContent.length > 0);
    assert.ok(["button", "submit"].includes(button.getAttribute("type")));
  }
  const ids = container.all((n) => n.hasAttribute("id")).map((n) => n.getAttribute("id"));
  assert.equal(new Set(ids).size, ids.length);
});

test("hostile values stay text: no element is made from them", () => {
  const evil = '<img src=x onerror="alert(1)">';
  const config = structuredClone(CONFIG);
  config.tabs[0].blocks[0].title = evil;
  config.creator.name = evil;
  const { container } = draw(config);
  assert.equal(container.all((n) => n.tag === "img").length, 0);
  const titles = container.all((n) => n.tag === "h6").map((n) => n.textContent);
  assert.ok(titles.some((text) => text.includes(evil)));
  const inputs = container.all((n) => n.getAttribute("name") === "creator.name");
  assert.equal(inputs[0].value, evil);
});

test("rendered forms expose add and remove controls plus editable group fields", () => {
  const { container } = draw(CONFIG, new Proxy({}, { get: () => async () => ({ ok: true }) }));
  for (const key of ["add:link:main", "remove:link:first", "add:platform:socials", "remove:platform:socials:1", "save:group:grp"]) {
    assert.equal(container.all((node) => node.getAttribute("data-key") === key).length, 1, key);
  }
});

test("a refused field shows its message with aria-invalid and takes focus; a retry clears it", async () => {
  let outcome = { ok: false, saved: 0, field: "title", problem: { kind: "field", message: "title must be text", path: "" } };
  const { container } = draw(CONFIG, { saveLink: async () => outcome });
  const forms = container.all((n) => n.tag === "form");
  const form = forms.find((f) => f.all((n) => n.getAttribute("name") === "title").length === 1 && f.textContent.includes("First"));
  const titleInput = form.all((n) => n.getAttribute("name") === "title")[0];
  await form.fire("submit");
  assert.equal(titleInput.getAttribute("aria-invalid"), "true");
  assert.equal(globalThis.activeNode, titleInput);
  const message = form.all((n) => n.hasAttribute("class") && n.getAttribute("class") === "field-error" && !n.hasAttribute("hidden"));
  assert.equal(message.length, 1);
  assert.equal(message[0].textContent, t("field.error", { message: "title must be text" }));
  outcome = { ok: true, saved: 1 };
  await form.fire("submit");
  assert.equal(titleInput.hasAttribute("aria-invalid"), false);
  assert.equal(form.all((n) => n.getAttribute("class") === "field-error" && !n.hasAttribute("hidden")).length, 0);
});

test("the draft sent is what the boxes hold, flags as booleans", async () => {
  let got = null;
  const { container } = draw(CONFIG, { saveLink: async (id, draft) => { got = { id, draft }; return { ok: true, saved: 0 }; } });
  const form = container.all((n) => n.tag === "form").find((f) => f.textContent.includes("In B"));
  form.all((n) => n.getAttribute("name") === "icon")[0].value = "star";
  form.all((n) => n.getAttribute("name") === "featured")[0].checked = true;
  await form.fire("submit");
  assert.equal(got.id, "in-b");
  assert.deepEqual(got.draft, { title: "In B", url: "https://example.com/b", subtitle: "sub", icon: "star", affiliate: false, featured: true });
});

test("move buttons: edges are disabled, a click moves and focus follows the button", async () => {
  const moves = [];
  const container = new Node_("div");
  const swapped = structuredClone(CONFIG);
  const blocks = swapped.tabs[0].blocks;
  blocks.splice(0, blocks.length, blocks[1], blocks[2], blocks[3], blocks[0]);
  const ctx = {
    run: async (task) => task(),
    actions: { moveLink: async (id, delta) => {
      moves.push([id, delta]);
      renderEditor(container, swapped, ctx);  // the app redraws after a save
      return { ok: true, moved: true };
    } },
  };
  renderEditor(container, CONFIG, ctx);
  const key = (k) => container.all((n) => n.getAttribute("data-key") === k)[0];
  assert.ok(key("move:link:first:up:1").hasAttribute("disabled"));
  assert.ok(!key("move:link:first:down:1").hasAttribute("disabled"));
  assert.ok(key("move:link:second:down:2").hasAttribute("disabled"));
  assert.ok(key("move:link:in-a:up:1").hasAttribute("disabled"));
  const button = key("move:link:first:down:1");
  assert.ok(button.textContent.startsWith(t("action.moveDown")));
  assert.ok(button.textContent.includes("First"));
  await button.fire("click");
  assert.deepEqual(moves, [["first", 1]]);
  assert.equal(globalThis.activeNode, key("move:link:first:up:2"));
  assert.notEqual(globalThis.activeNode, button);
});

test("at the new edge focus falls back to the other move button", async () => {
  const container = new Node_("div");
  const swapped = structuredClone(CONFIG);
  const blocks = swapped.tabs[0].blocks;
  blocks.splice(0, blocks.length, blocks[3], blocks[0], blocks[1], blocks[2]);
  const ctx = {
    run: async (task) => task(),
    actions: { moveLink: async () => {
      renderEditor(container, swapped, ctx);
      return { ok: true, moved: true };
    } },
  };
  renderEditor(container, CONFIG, ctx);
  const key = (k) => container.all((n) => n.getAttribute("data-key") === k)[0];
  await key("move:link:second:up:2").fire("click");
  assert.equal(globalThis.activeNode, key("move:link:second:down:1"));
});

test("platform cards name the platform and position", () => {
  const { container } = draw();
  const titles = container.all((n) => n.tag === "h6").map((n) => n.textContent);
  assert.ok(titles.includes(t("platform.card", { platform: "twitch", index: 1, count: 2 })));
  assert.ok(titles.includes(t("platform.card", { platform: "kick", index: 2, count: 2 })));
});

test("an empty config draws nothing and a missing one does not throw", () => {
  assert.equal(draw(null).container.children.length, 0);
  const { container } = draw({});
  assert.ok(container.all((n) => n.tag === "form").length >= 2);
});

test("every string id used by the editor code exists", () => {
  assert.ok(Object.keys(STRINGS).length > 60);
  for (const key of ["theme.mode.auto", "theme.mode.light", "theme.mode.dark", "links.tab", "links.group", ...store.COLOR_KEYS.map((k) => "color." + k)]) {
    assert.ok(Object.hasOwn(STRINGS, key), key);
  }
});
