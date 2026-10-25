// State and editing logic. No DOM and no network code of its own: the client
// and announcer are passed in, so all of this can run (and be tested) as is.
import { t } from "./strings.js";

// A small state store: immutable snapshots, subscribers called on change.
export function createStore(initial) {
  let state = Object.freeze({ ...initial });
  const listeners = new Set();
  return {
    get() {
      return state;
    },
    set(patch) {
      state = Object.freeze({ ...state, ...patch });
      for (const listener of [...listeners]) {
        listener(state);
      }
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

export const LINK_TEXT = ["title", "url", "subtitle", "icon"];
export const LINK_FLAGS = ["affiliate", "featured"];
export const PLATFORM_FIELDS = ["handle", "url", "name", "label", "count", "as_of"];
export const COLOR_KEYS = ["bg", "text", "accent", "on_accent", "surface", "border"];
export const MODES = ["auto", "light", "dark"];
const NAME_LIMIT = 30;
// What the build uses when a key is absent, so an untouched box is no change.
const DEFAULTS = { "theme.mode": "auto" };

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function list(value) {
  return Array.isArray(value) ? value.filter(isObject) : [];
}

function orNull(value) {
  return value === undefined ? null : value;
}

function shortName(value) {
  const text = typeof value === "string" ? value : "";
  return text.length > NAME_LIMIT ? text.slice(0, NAME_LIMIT - 1) + "…" : text;
}

export function dotted(config, key) {
  let cursor = config;
  for (const part of key.split(".")) {
    if (!isObject(cursor) || !Object.hasOwn(cursor, part)) {
      return null;
    }
    cursor = cursor[part];
  }
  return cursor;
}

// ---- reading the config ------------------------------------------------------

// Every list that holds links: a tab's blocks, or a group's links. `ids` is
// the whole list in order (what the server compares for a reorder); `links`
// are the entries that are links, with their 1-based position in `ids`.
export function linkLists(config) {
  const lists = [];
  for (const tab of list(config && config.tabs)) {
    const tabName = typeof tab.title === "string" ? tab.title : String(tab.id);
    const blocks = list(tab.blocks);
    lists.push(makeList(blocks, { id: "links.tab", name: tabName }));
    for (const block of blocks) {
      if (Array.isArray(block.links)) {
        const name = typeof block.title === "string" ? block.title : String(block.id);
        lists.push(makeList(list(block.links), { id: "links.group", name }));
      }
    }
  }
  return lists.filter((entry) => entry.links.length > 0);
}

function makeList(entries, heading) {
  const links = [];
  entries.forEach((entry, index) => {
    if (typeof entry.url === "string" && typeof entry.id === "string") {
      links.push({ data: entry, position: index + 1 });
    }
  });
  return { heading, ids: entries.map((entry) => orNull(entry.id)), links };
}

export function findLink(config, id) {
  for (const entry of linkLists(config)) {
    for (const link of entry.links) {
      if (link.data.id === id) {
        return { ...link, ids: entry.ids };
      }
    }
  }
  return null;
}

export function platformBlocks(config) {
  const found = [];
  for (const tab of list(config && config.tabs)) {
    for (const block of list(tab.blocks)) {
      if (Array.isArray(block.items) && typeof block.id === "string") {
        found.push({ id: block.id, items: list(block.items) });
      }
    }
  }
  return found;
}

export function findPlatformBlock(config, blockId) {
  return platformBlocks(config).find((block) => block.id === blockId) || null;
}

// ---- values the forms send ---------------------------------------------------

export function linkValue(field, raw) {
  if (LINK_FLAGS.includes(field)) {
    return raw === true;
  }
  const text = typeof raw === "string" ? raw.trim() : "";
  return (field === "subtitle" || field === "icon") && text === "" ? null : text;
}

export function platformValue(field, raw) {
  const text = raw === null || raw === undefined ? "" : String(raw).trim();
  const clearable = ["name", "label", "count", "as_of"].includes(field);
  return clearable && text === "" ? null : text;
}

// A number for the dim box; null when the text is not a finite number.
export function parseDim(raw) {
  const text = typeof raw === "string" ? raw.trim() : String(raw);
  if (text === "") {
    return 0;
  }
  const number = Number(text);
  return Number.isFinite(number) ? number : null;
}

// ---- operations (the body the server expects, minus `base`) --------------------

export function setOp(config, key, value) {
  return {
    op: "set",
    args: { key, value },
    expect: { value: orNull(dotted(config, key)) },
    label: t("label.set", { key }),
  };
}

export function setLinkFieldOp(config, id, field, value) {
  const found = findLink(config, id);
  return {
    op: "set_link_field",
    args: { id, field, value },
    expect: { value: found ? orNull(found.data[field]) : null },
    label: t("label.setLinkField", {
      field,
      name: shortName(found ? found.data.title : id),
    }),
  };
}

export function moveLinkOp(config, id, position) {
  const found = findLink(config, id);
  return {
    op: "move_link",
    args: { id, position },
    expect: { order: found ? found.ids : [] },
    label: t("label.moveLink", { name: shortName(found ? found.data.title : id) }),
  };
}

function platformName(item) {
  return shortName(typeof item.platform === "string" ? item.platform : "");
}

export function setPlatformFieldOp(config, block, index, field, value) {
  const found = findPlatformBlock(config, block);
  const item = found && found.items[index - 1] ? found.items[index - 1] : {};
  return {
    op: "set_platform_item_field",
    args: { block, index, field, value },
    expect: { value: orNull(item[field]), handle: orNull(item.handle) },
    label: t("label.setPlatformField", { field, platform: platformName(item) }),
  };
}

export function movePlatformOp(config, block, index, position) {
  const found = findPlatformBlock(config, block);
  const item = found && found.items[index - 1] ? found.items[index - 1] : {};
  return {
    op: "move_platform_item",
    args: { block, index, position },
    expect: { items: found ? found.items : [] },
    label: t("label.movePlatform", { platform: platformName(item) }),
  };
}

// ---- what changed: steps ---------------------------------------------------------
// A step is { field, build(config) -> op }. The op is built just before it is
// sent, from the config as the last reply left it.

export function linkSteps(config, id, draft) {
  const found = findLink(config, id);
  const steps = [];
  if (!found) {
    return steps;
  }
  for (const field of [...LINK_TEXT, ...LINK_FLAGS]) {
    if (!Object.hasOwn(draft, field)) {
      continue;
    }
    const next = linkValue(field, draft[field]);
    const flag = LINK_FLAGS.includes(field);
    const old = linkValue(field, flag ? found.data[field] === true : found.data[field] ?? "");
    if (next !== old) {
      steps.push({ field, build: (now) => setLinkFieldOp(now, id, field, next) });
    }
  }
  return steps;
}

export function platformSteps(config, block, index, draft) {
  const found = findPlatformBlock(config, block);
  const item = found && found.items[index - 1];
  const steps = [];
  if (!item) {
    return steps;
  }
  for (const field of PLATFORM_FIELDS) {
    if (!Object.hasOwn(draft, field)) {
      continue;
    }
    const next = platformValue(field, draft[field]);
    const old = platformValue(field, item[field]);
    if (next !== old) {
      steps.push({
        field,
        build: (now) => setPlatformFieldOp(now, block, index, field, next),
      });
    }
  }
  return steps;
}

// drafts: { "creator.name": text, "theme.mode": text, "theme.background_dim": text,
// "theme.colors.bg": text, ... }. Returns { steps, errors } where errors maps a
// key to a message for a value that cannot be sent at all.
export function settingSteps(config, drafts) {
  const steps = [];
  const errors = {};
  for (const key of Object.keys(drafts)) {
    const raw = drafts[key];
    if (key === "theme.background_dim") {
      const next = parseDim(raw);
      if (next === null) {
        errors[key] = t("theme.dimHint");
      } else if (next !== Number(dotted(config, key) ?? 0)) {
        steps.push({ field: key, build: (now) => setOp(now, key, next) });
      }
      continue;
    }
    const next = typeof raw === "string" ? raw.trim() : "";
    const old = dotted(config, key);
    const shown = typeof old === "string" ? old : (DEFAULTS[key] ?? "");
    if (next !== shown) {
      steps.push({ field: key, build: (now) => setOp(now, key, next) });
    }
  }
  return { steps, errors };
}

// ---- replies and errors -----------------------------------------------------------

export function patchFromState(reply) {
  const build = reply && isObject(reply.build) ? reply.build : null;
  let buildError = "";
  if (build && build.ok === false) {
    buildError = typeof build.error === "string" && build.error ? build.error : t("error.generic");
  }
  return {
    config: reply && reply.config !== undefined ? reply.config : null,
    hash: reply && typeof reply.hash === "string" ? reply.hash : "",
    previewUrl: reply && typeof reply.preview_url === "string" ? reply.preview_url : "",
    buildError,
  };
}

// A failure as the forms need it: { kind, message, ... }.
export function classify(error) {
  const status = error && typeof error.status === "number" ? error.status : 0;
  const code = error && typeof error.code === "string" ? error.code : "";
  const message = error && typeof error.message === "string" && error.message ? error.message : t("error.generic");
  if (status === 409 && code === "stale") {
    return { kind: "stale", message: t("save.stale"), state: error.state || null };
  }
  if (status === 429) {
    const seconds = Number(error.retryAfter) || 0;
    return {
      kind: "limit",
      seconds,
      message: seconds > 0 ? t("save.limit", { seconds }) : t("save.limitNow"),
    };
  }
  if (status === 401) {
    return { kind: "auth", message: t("save.auth") };
  }
  if (status === 0 || code === "network") {
    return { kind: "network", message: t("save.network") };
  }
  if (status === 422) {
    return { kind: "field", message, path: typeof error.path === "string" ? error.path : "" };
  }
  return { kind: "other", message: t("save.failed", { message }) };
}

// ---- the saver: ops one at a time, store updated from each reply -------------------

export function createSaver({ client, store, announce }) {
  let tail = Promise.resolve();

  async function run(steps) {
    if (steps.length === 0) {
      announce(t("save.nothing"));
      return { ok: true, saved: 0 };
    }
    store.set({ message: "status.saving" });
    let saved = 0;
    let label = "";
    let warning = "";
    for (const step of steps) {
      const current = store.get();
      const body = { base: current.hash, ...step.build(current.config) };
      let reply;
      try {
        reply = await client.post("op", body);
      } catch (error) {
        const problem = classify(error);
        if (problem.kind === "stale" && problem.state) {
          store.set(patchFromState(problem.state));
        }
        store.set({ message: problem.kind === "stale" ? "status.stale" : "status.notSaved" });
        announce(problem.message);
        return { ok: false, saved, field: step.field, problem };
      }
      label = body.label;
      if (reply && reply.state) {
        store.set(patchFromState(reply.state));
      }
      if (reply && typeof reply.warning === "string") {
        warning = reply.warning;
      }
      saved += 1;
    }
    store.set({ message: "status.saved" });
    announce([t("announce.saved", { label }), warning].filter(Boolean).join(" "));
    return { ok: true, saved, warning };
  }

  return {
    save(steps) {
      const job = tail.then(() => run(steps));
      tail = job.then(
        () => undefined,
        () => undefined,
      );
      return job;
    },
  };
}

// ---- what the forms call -----------------------------------------------------------

export function createActions({ store, saver, announce }) {
  function refused(field, message) {
    announce(t("save.fieldError", { field, message }));
    return { ok: false, saved: 0, field, problem: { kind: "field", message, path: "" } };
  }

  async function reorder(count, position, build, name) {
    if (position < 1 || position > count) {
      return { ok: true, saved: 0, moved: false };
    }
    const result = await saver.save([{ field: "move", build }]);
    if (result.ok) {
      announce(t("announce.moved", { name, position, count }));
    }
    return { ...result, moved: result.ok };
  }

  return {
    saveSettings(drafts) {
      const { steps, errors } = settingSteps(store.get().config, drafts);
      const bad = Object.keys(errors)[0];
      if (bad) {
        return Promise.resolve(refused(bad, errors[bad]));
      }
      return saver.save(steps);
    },
    saveLink(id, draft) {
      return saver.save(linkSteps(store.get().config, id, draft));
    },
    savePlatform(block, index, draft) {
      return saver.save(platformSteps(store.get().config, block, index, draft));
    },
    moveLink(id, delta) {
      const found = findLink(store.get().config, id);
      if (!found) {
        return Promise.resolve({ ok: true, saved: 0, moved: false });
      }
      const position = found.position + delta;
      return reorder(found.ids.length, position, (now) => moveLinkOp(now, id, position), shortName(found.data.title));
    },
    movePlatform(block, index, delta) {
      const found = findPlatformBlock(store.get().config, block);
      if (!found || !found.items[index - 1]) {
        return Promise.resolve({ ok: true, saved: 0, moved: false });
      }
      const position = index + delta;
      return reorder(found.items.length, position, (now) => movePlatformOp(now, block, index, position), platformName(found.items[index - 1]));
    },
  };
}
