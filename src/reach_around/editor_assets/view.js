// Rendering. Values reach the page only through textContent, or through
// setAttribute on attributes named here. Nothing is parsed as markup.
import { COLOR_KEYS, MODES, dotted, linkLists, platformBlocks } from "./store.js";
import { t } from "./strings.js";

const MAX_DEPTH = 12;
const MAX_NODES = 3000;
const PREVIEW_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);

function make(tag, className) {
  const node = document.createElement(tag);
  if (className) {
    node.setAttribute("class", className);
  }
  return node;
}

export function applyStrings(root) {
  for (const node of root.querySelectorAll("[data-string]")) {
    node.textContent = t(node.getAttribute("data-string"));
  }
}

function scalarText(value) {
  if (value === null || value === undefined || value === "") {
    return t("config.noValue");
  }
  return String(value);
}

function renderValue(value, depth, budget) {
  budget.left -= 1;
  if (budget.left < 0 || depth > MAX_DEPTH) {
    budget.cut = true;
    return null;
  }
  if (Array.isArray(value)) {
    const list = make("ol", "tree");
    value.forEach((item, index) => {
      const row = make("li");
      const label = make("span", "tree-index");
      label.textContent = t("config.itemOf", { index: index + 1, count: value.length });
      const body = renderValue(item, depth + 1, budget);
      row.append(label);
      if (body) {
        row.append(body);
      }
      list.append(row);
    });
    return list;
  }
  if (value !== null && typeof value === "object") {
    const list = make("dl", "tree");
    for (const key of Object.keys(value)) {
      const name = make("dt");
      name.textContent = key;
      const item = make("dd");
      const body = renderValue(value[key], depth + 1, budget);
      if (body) {
        item.append(body);
      }
      list.append(name, item);
    }
    return list;
  }
  const leaf = make("span", "tree-value");
  leaf.textContent = scalarText(value);
  return leaf;
}

export function renderConfig(container, config) {
  container.replaceChildren();
  const isEmpty =
    config === null ||
    typeof config !== "object" ||
    Object.keys(config).length === 0;
  if (isEmpty) {
    const note = make("p", "note");
    note.textContent = t("config.empty");
    container.append(note);
    return;
  }
  const budget = { left: MAX_NODES, cut: false };
  const tree = renderValue(config, 0, budget);
  if (tree) {
    container.append(tree);
  }
  if (budget.cut) {
    const note = make("p", "note");
    note.textContent = t("config.truncated");
    container.append(note);
  }
}

export function renderStatus(node, id, values) {
  node.textContent = t(id, values);
}

// Only a loopback address on plain http is ever loaded into the frame.
export function safePreviewUrl(value) {
  if (typeof value !== "string" || value.length > 200) {
    return "";
  }
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return "";
  }
  const loopback = parsed.protocol === "http:" && PREVIEW_HOSTS.has(parsed.hostname);
  const plainOrigin = parsed.username === "" && parsed.password === "";
  return loopback && plainOrigin && parsed.port !== "" ? parsed.href : "";
}

export function renderPreview(frame, note, previewUrl) {
  const safe = safePreviewUrl(previewUrl);
  if (safe) {
    frame.setAttribute("src", safe);
    note.textContent = t("preview.shown");
  } else {
    frame.removeAttribute("src");
    note.textContent = t("preview.unavailable");
  }
}

// ---- the editor forms -------------------------------------------------------------

let serial = 0;

function uid(prefix) {
  serial += 1;
  return prefix + "-" + serial;
}

function textual(tag, className, value) {
  const node = make(tag, className);
  node.textContent = value;
  return node;
}

function withHidden(button, visible, extra) {
  button.textContent = visible;
  if (extra) {
    button.append(textual("span", "visually-hidden", ": " + extra));
  }
}

// One labelled control with a hint and an error slot, both tied to it.
function buildField(spec) {
  const row = make("div", spec.kind === "checkbox" ? "field field-check" : "field");
  const id = uid("f");
  const label = make("label");
  label.setAttribute("for", id);
  label.textContent = spec.optional ? spec.label + " " + t("field.optional") : spec.label;
  let input;
  if (spec.kind === "select") {
    input = make("select");
    for (const choice of spec.options) {
      const option = make("option");
      option.setAttribute("value", choice.value);
      option.textContent = choice.label;
      input.append(option);
    }
    input.value = spec.value;
  } else {
    input = make("input");
    input.setAttribute("type", spec.kind);
    if (spec.kind === "checkbox") {
      input.checked = spec.value === true;
    } else {
      input.value = spec.value === null || spec.value === undefined ? "" : String(spec.value);
    }
    for (const name of ["min", "max", "step", "inputmode"]) {
      if (spec[name] !== undefined) {
        input.setAttribute(name, spec[name]);
      }
    }
    input.setAttribute("autocomplete", "off");
  }
  input.setAttribute("id", id);
  input.setAttribute("name", spec.name);
  const described = [];
  const error = textual("p", "field-error", "");
  const errorId = uid("e");
  error.setAttribute("id", errorId);
  error.setAttribute("hidden", "");
  if (spec.hint) {
    const hintId = uid("h");
    const hint = textual("p", "hint", spec.hint);
    hint.setAttribute("id", hintId);
    described.push(hintId);
    row.append(label, input, hint, error);
  } else if (spec.kind === "checkbox") {
    row.append(input, label, error);
  } else {
    row.append(label, input, error);
  }
  described.push(errorId);
  input.setAttribute("aria-describedby", described.join(" "));
  return {
    row,
    input,
    read: () => (spec.kind === "checkbox" ? input.checked : input.value),
    fail(message) {
      error.textContent = t("field.error", { message });
      error.removeAttribute("hidden");
      input.setAttribute("aria-invalid", "true");
    },
    clear() {
      error.textContent = "";
      error.setAttribute("hidden", "");
      input.removeAttribute("aria-invalid");
    },
  };
}

function keyed(node, key) {
  node.setAttribute("data-key", key);
  return node;
}

function findKey(container, keys) {
  const wanted = Array.isArray(keys) ? keys : [keys];
  for (const key of wanted) {
    for (const node of container.querySelectorAll("[data-key]")) {
      if (node.getAttribute("data-key") === key && !node.hasAttribute("disabled")) {
        return node;
      }
    }
  }
  return null;
}

function focusKey(container, keys) {
  const node = findKey(container, keys);
  if (node) {
    node.focus();
  }
}

// A form of fields with one Save button. `submit(draft)` returns a result.
function buildForm(ctx, spec) {
  const form = make("form", "card");
  form.setAttribute("novalidate", "");
  form.setAttribute("aria-labelledby", uid("t"));
  const heading = textual(spec.level, "card-title", spec.title);
  heading.setAttribute("id", form.getAttribute("aria-labelledby"));
  const head = make("div", "card-head");
  head.append(heading);
  if (spec.extras) {
    head.append(spec.extras);
  }
  form.append(head);
  const rows = {};
  for (const field of spec.fields) {
    const made = buildField(field);
    rows[field.name] = made;
    (field.into || form).append(made.row);
  }
  if (spec.tail) {
    form.append(spec.tail);
  }
  const save = make("button", "button");
  save.setAttribute("type", "submit");
  withHidden(save, t("action.save"), spec.title);
  keyed(save, spec.saveKey);
  form.append(save);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const draft = {};
    for (const name of Object.keys(rows)) {
      rows[name].clear();
      draft[name] = rows[name].read();
    }
    const result = await ctx.run(() => spec.submit(draft));
    if (result.ok) {
      focusKey(ctx.container, spec.saveKey);
      return;
    }
    const target = rows[result.field];
    if (result.problem.kind === "field" && target) {
      target.fail(result.problem.message);
      target.input.focus();
    } else if (result.problem.kind === "stale") {
      focusKey(ctx.container, "editor");
    }
  });
  return form;
}

function moveButtons(ctx, spec) {
  const box = make("div", "moves");
  for (const way of ["up", "down"]) {
    const button = make("button", "button button-small");
    button.setAttribute("type", "button");
    withHidden(button, t(way === "up" ? "action.moveUp" : "action.moveDown"), spec.name);
    keyed(button, spec.key(0, way));
    const edge = way === "up" ? spec.first : spec.last;
    if (edge) {
      button.setAttribute("disabled", "");
    }
    button.addEventListener("click", async () => {
      const delta = way === "up" ? -1 : 1;
      const result = await ctx.run(() => spec.move(delta));
      if (result.ok) {
        focusKey(ctx.container, [spec.key(delta, way), spec.key(delta, way === "up" ? "down" : "up")]);
      } else if (result.problem.kind === "stale") {
        focusKey(ctx.container, "editor");
      }
    });
    box.append(button);
  }
  return box;
}

function settingField(config, name, label, extra) {
  const value = dotted(config, name);
  return { name, label, kind: "text", value: value === null ? "" : value, ...extra };
}

function creatorForm(ctx, config) {
  return buildForm(ctx, {
    level: "h4",
    title: t("creator.heading"),
    saveKey: "save:creator",
    fields: [
      settingField(config, "creator.name", t("creator.name")),
      settingField(config, "creator.title", t("creator.title"), {
        hint: t("creator.hintTitle"),
      }),
    ],
    submit: (draft) => ctx.actions.saveSettings(draft),
  });
}

function lookForm(ctx, config) {
  const mode = dotted(config, "theme.mode");
  const dim = dotted(config, "theme.background_dim");
  const colors = make("fieldset", "colors");
  const legend = textual("legend", "", t("theme.colors"));
  const hintId = uid("h");
  const hint = textual("p", "hint", t("theme.colorHint"));
  hint.setAttribute("id", hintId);
  colors.setAttribute("aria-describedby", hintId);
  colors.append(legend, hint);
  const fields = [
    {
      name: "theme.mode",
      label: t("theme.mode"),
      kind: "select",
      value: MODES.includes(mode) ? mode : "auto",
      options: MODES.map((value) => ({ value, label: t("theme.mode." + value) })),
    },
    {
      name: "theme.background_dim",
      label: t("theme.dim"),
      kind: "number",
      value: dim === null ? 0 : dim,
      min: "0",
      max: "0.9",
      step: "0.05",
      hint: t("theme.dimHint"),
    },
    ...COLOR_KEYS.map((key) =>
      settingField(config, "theme.colors." + key, t("color." + key), { into: colors }),
    ),
  ];
  return buildForm(ctx, {
    level: "h4",
    title: t("theme.heading"),
    saveKey: "save:look",
    fields,
    tail: colors,
    submit: (draft) => ctx.actions.saveSettings(draft),
  });
}

function linkCard(ctx, link, list) {
  const data = link.data;
  const name = typeof data.title === "string" ? data.title : data.id;
  const optionalText = (field, label) => ({
    name: field,
    label,
    kind: "text",
    optional: true,
    value: data[field] === undefined ? "" : data[field],
  });
  const first = link.position === 1;
  const last = link.position === list.ids.length;
  const item = make("li");
  item.append(
    buildForm(ctx, {
      level: "h6",
      title: t("link.card", { name }),
      saveKey: "save:link:" + data.id,
      extras: moveButtons(ctx, {
        name,
        first,
        last,
        key: (delta, way) => "move:link:" + data.id + ":" + way + ":" + (link.position + delta),
        move: (delta) => ctx.actions.moveLink(data.id, delta),
      }),
      fields: [
        { name: "title", label: t("link.title"), kind: "text", value: data.title },
        { name: "url", label: t("link.url"), kind: "text", value: data.url },
        optionalText("subtitle", t("link.subtitle")),
        optionalText("icon", t("link.icon")),
        { name: "affiliate", label: t("link.affiliate"), kind: "checkbox", value: data.affiliate === true },
        { name: "featured", label: t("link.featured"), kind: "checkbox", value: data.featured === true },
      ],
      submit: (draft) => ctx.actions.saveLink(data.id, draft),
    }),
  );
  return item;
}

function linksSection(ctx, config) {
  const section = make("section", "group");
  section.append(textual("h4", "", t("links.heading")));
  const lists = linkLists(config);
  if (lists.length === 0) {
    section.append(textual("p", "note", t("links.none")));
  }
  for (const entry of lists) {
    section.append(textual("h5", "", t(entry.heading.id, { name: entry.heading.name })));
    const cards = make("ul", "cards");
    for (const link of entry.links) {
      cards.append(linkCard(ctx, link, entry));
    }
    section.append(cards);
  }
  return section;
}

function platformCard(ctx, block, index) {
  const data = block.items[index - 1];
  const name = typeof data.platform === "string" ? data.platform : String(index);
  const text = (field, label, extra) => ({
    name: field,
    label,
    kind: "text",
    value: data[field] === undefined || data[field] === null ? "" : data[field],
    ...extra,
  });
  const item = make("li");
  item.append(
    buildForm(ctx, {
      level: "h6",
      title: t("platform.card", { platform: name, index, count: block.items.length }),
      saveKey: "save:platform:" + block.id + ":" + index,
      extras: moveButtons(ctx, {
        name,
        first: index === 1,
        last: index === block.items.length,
        key: (delta, way) => "move:platform:" + block.id + ":" + way + ":" + (index + delta),
        move: (delta) => ctx.actions.movePlatform(block.id, index, delta),
      }),
      fields: [
        text("handle", t("platform.handle")),
        text("url", t("platform.url")),
        text("name", t("platform.name"), { optional: true }),
        text("label", t("platform.label"), { optional: true }),
        text("count", t("platform.count"), {
          optional: true,
          inputmode: "numeric",
          hint: t("platform.countHint"),
        }),
        text("as_of", t("platform.as_of"), { optional: true, hint: t("platform.asOfHint") }),
      ],
      submit: (draft) => ctx.actions.savePlatform(block.id, index, draft),
    }),
  );
  return item;
}

function platformsSection(ctx, config) {
  const section = make("section", "group");
  section.append(textual("h4", "", t("platforms.heading")));
  const blocks = platformBlocks(config).filter((block) => block.items.length > 0);
  if (blocks.length === 0) {
    section.append(textual("p", "note", t("platforms.none")));
  }
  for (const block of blocks) {
    section.append(textual("h5", "", t("platforms.block", { name: block.id })));
    const cards = make("ul", "cards");
    block.items.forEach((_item, position) => {
      cards.append(platformCard(ctx, block, position + 1));
    });
    section.append(cards);
  }
  return section;
}

// ctx: { actions, run(task) -> result, container }. Rebuilds the whole editor
// from the config; the caller decides when (never in the middle of a save).
export function renderEditor(container, config, ctx) {
  const scope = { ...ctx, container };
  container.replaceChildren();
  if (config === null || typeof config !== "object") {
    return;
  }
  const heading = keyed(textual("h3", "", t("editor.heading")), "editor");
  heading.setAttribute("tabindex", "-1");
  const settings = make("section", "group");
  settings.append(creatorForm(scope, config), lookForm(scope, config));
  container.append(heading, settings, linksSection(scope, config), platformsSection(scope, config));
}

export function showNote(node, message) {
  node.textContent = message;
}
