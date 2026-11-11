// Entry point. Wires the key, the client, the store and the views together.
import { createAnnouncer } from "./announce.js";
import { ApiError, createClient } from "./api.js";
import {
  createActions,
  createHistoryActions,
  createSaver,
  createStore,
  historyShortcut,
  patchFromState,
} from "./store.js";
import { t } from "./strings.js";
import { getToken, takeTokenFromUrl } from "./token.js";
import {
  applyStrings,
  renderConfig,
  renderEditor,
  renderHistory,
  renderPreview,
  renderStatus,
  showNote,
} from "./view.js";

const store = createStore({
  phase: "starting",
  message: "status.starting",
  config: null,
  hash: "",
  previewUrl: "",
  buildError: "",
});

const status = document.getElementById("status");
const configView = document.getElementById("config-view");
const editorView = document.getElementById("editor-view");
const editorNote = document.getElementById("editor-note");
const buildNote = document.getElementById("build-note");
const frame = document.getElementById("preview-frame");
const previewNote = document.getElementById("preview-note");
const announce = createAnnouncer(document.getElementById("announcer"));
const undo = document.getElementById("undo");
const redo = document.getElementById("redo");
const historyToggle = document.getElementById("history-toggle");
const historyPanel = document.getElementById("history-panel");
const historyList = document.getElementById("history-list");
const staleBanner = document.getElementById("stale-banner");
const staleMessage = document.getElementById("stale-message");
const restoreEarlier = document.getElementById("restore-earlier");

applyStrings(document);
document.title = t("app.title");

const client = createClient(getToken);
const saver = createSaver({ client, store, announce });
const history = createHistoryActions({ client, store, announce });
const ctx = {
  actions: createActions({ store, saver, announce }),
  run,
};

let busy = false;
let drawn = null;

// The forms are rebuilt only when the config changed and never in the middle
// of a save, so what the person typed is not wiped under their hands.
function paint(state, editor) {
  renderStatus(status, state.message);
  renderConfig(configView, state.config);
  renderPreview(frame, previewNote, state.previewUrl);
  showNote(buildNote, state.buildError ? t("build.failed", { message: state.buildError }) : "");
  undo.disabled = !state.canUndo;
  redo.disabled = !state.canRedo;
  staleBanner.hidden = !state.outsideEdit && !state.outsideChanged && state.message !== "status.stale";
  staleMessage.textContent = state.message === "status.stale" ? t("status.stale") : t("status.outside");
  restoreEarlier.hidden = !state.outsideEdit;
  if (editor && state.config !== drawn) {
    drawn = state.config;
    renderEditor(editorView, state.config, ctx);
  }
}

async function runHistory(task) {
  const result = await run(task);
  if (result.ok && !historyPanel.hidden) {
    await loadHistory();
  }
  return result;
}

async function loadHistory() {
  try {
    renderHistory(historyList, await history.load(), (id) => runHistory(() => history.restore(id)));
  } catch (error) {
    const message = error instanceof Error ? error.message : t("error.generic");
    showNote(editorNote, t("history.loadFailed", { message }));
    announce(t("history.loadFailed", { message }));
  }
}

undo.addEventListener("click", () => runHistory(history.undo));
redo.addEventListener("click", () => runHistory(history.redo));
restoreEarlier.addEventListener("click", () => runHistory(history.restoreEarlier));
historyToggle.addEventListener("click", async () => {
  historyPanel.hidden = !historyPanel.hidden;
  historyToggle.setAttribute("aria-expanded", String(!historyPanel.hidden));
  if (!historyPanel.hidden) {
    await loadHistory();
  }
});

document.addEventListener("keydown", (event) => {
  const action = historyShortcut(event, event.target);
  if (action === "undo" && !undo.disabled) {
    event.preventDefault();
    runHistory(history.undo);
  } else if (action === "redo" && !redo.disabled) {
    event.preventDefault();
    runHistory(history.redo);
  }
});

async function run(task) {
  busy = true;
  showNote(editorNote, "");
  let result;
  try {
    result = await task();
  } finally {
    busy = false;
  }
  const kind = result.ok ? "" : result.problem.kind;
  if (kind !== "" && kind !== "field") {
    showNote(editorNote, result.problem.message);
  }
  // After a refusal or a failure the boxes keep what the person typed; after a
  // save, or a stale reply, they show what is now saved.
  paint(store.get(), result.ok || kind === "stale");
  return result;
}

store.subscribe((state) => {
  if (!busy) {
    paint(state, true);
  }
});

function messageFor(error) {
  if (error instanceof ApiError && error.status === 401) {
    return "status.refused";
  }
  if (error instanceof ApiError && error.code === "network") {
    return "status.unreachable";
  }
  return "status.failed";
}

async function load() {
  if (!takeTokenFromUrl()) {
    store.set({ phase: "need-token", message: "status.needToken" });
    return;
  }
  store.set({ phase: "loading", message: "status.loading" });
  try {
    const reply = await client.get("state");
    store.set({
      phase: "ready",
      message: "status.ready",
      ...patchFromState(reply),
    });
    announce(t("announce.loaded"));
  } catch (error) {
    const message = messageFor(error);
    store.set({ phase: "error", message });
    announce(t("announce.failed", { message: t(message) }));
  }
}

async function pollVersion() {
  if (store.get().phase !== "ready") {
    return;
  }
  try {
    const version = await client.get("version");
    if (version && typeof version.hash === "string" && version.hash !== store.get().hash) {
      if (!store.get().outsideChanged) {
        store.set({ message: "status.outside", outsideChanged: true });
        announce(t("status.outside"));
      }
    }
  } catch {
    // A later save or the next poll gives the person an actionable error.
  }
}

load();
setInterval(pollVersion, 500);
