// Entry point. Wires the key, the client, the store and the views together.
import { createAnnouncer } from "./announce.js";
import { ApiError, createClient } from "./api.js";
import { createActions, createSaver, createStore, patchFromState } from "./store.js";
import { t } from "./strings.js";
import { getToken, takeTokenFromUrl } from "./token.js";
import {
  applyStrings,
  renderConfig,
  renderEditor,
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

applyStrings(document);
document.title = t("app.title");

const client = createClient(getToken);
const saver = createSaver({ client, store, announce });
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
  if (editor && state.config !== drawn) {
    drawn = state.config;
    renderEditor(editorView, state.config, ctx);
  }
}

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

load();
