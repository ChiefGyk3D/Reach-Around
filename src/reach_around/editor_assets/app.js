// Entry point. Wires the key, the client, the store and the views together.
import { createAnnouncer } from "./announce.js";
import { ApiError, createClient } from "./api.js";
import { createStore } from "./store.js";
import { t } from "./strings.js";
import { getToken, takeTokenFromUrl } from "./token.js";
import { applyStrings, renderConfig, renderPreview, renderStatus } from "./view.js";

const store = createStore({
  phase: "starting",
  message: "status.starting",
  config: null,
  hash: "",
  previewUrl: "",
});

const status = document.getElementById("status");
const configView = document.getElementById("config-view");
const frame = document.getElementById("preview-frame");
const previewNote = document.getElementById("preview-note");
const announce = createAnnouncer(document.getElementById("announcer"));

applyStrings(document);
document.title = t("app.title");

store.subscribe((state) => {
  renderStatus(status, state.message);
  renderConfig(configView, state.config);
  renderPreview(frame, previewNote, state.previewUrl);
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
  const client = createClient(getToken);
  try {
    const reply = await client.get("state");
    store.set({
      phase: "ready",
      message: "status.ready",
      config: reply.config === undefined ? null : reply.config,
      hash: typeof reply.hash === "string" ? reply.hash : "",
      previewUrl: typeof reply.preview_url === "string" ? reply.preview_url : "",
    });
    announce(t("announce.loaded"));
  } catch (error) {
    const message = messageFor(error);
    store.set({ phase: "error", message });
    announce(t("announce.failed", { message: t(message) }));
  }
}

load();
