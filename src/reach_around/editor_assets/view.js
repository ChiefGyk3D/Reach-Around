// Rendering. Values reach the page only through textContent, or through
// setAttribute on attributes named here. Nothing is parsed as markup.
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
