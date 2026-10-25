// Every user-visible string, keyed by id. American English. A translation is
// a second file with the same keys. Placeholders are named: {count}.
export const STRINGS = {
  "app.title": "Reach Around editor",
  "skip.preview": "Skip to the preview",
  "nav.config": "Settings",
  "nav.preview": "Preview",
  "status.starting": "Starting.",
  "status.needToken": "Open the link printed in your terminal. It carries the session key.",
  "status.loading": "Loading your settings.",
  "status.ready": "Loaded. This view is read-only.",
  "status.unreachable": "The editor could not be reached. Check that it is still running.",
  "status.refused": "The editor refused the request. Open the link from your terminal again.",
  "status.failed": "The editor could not load your settings.",
  "config.heading": "Your settings",
  "config.readonly": "Read-only for now. Editing arrives in a later version.",
  "config.empty": "No settings were returned.",
  "config.truncated": "Some settings are not shown because the file is very large.",
  "config.noValue": "(empty)",
  "config.itemOf": "Item {index} of {count}",
  "preview.heading": "Preview",
  "preview.unavailable": "No preview address was provided yet.",
  "preview.shown": "The preview shows your page as it is built.",
  "announce.loaded": "Settings loaded.",
  "announce.failed": "Could not load settings: {message}",
  "error.network": "The editor could not be reached.",
  "error.unauthorized": "The session key is missing or wrong.",
  "error.generic": "Something went wrong.",
};

export function t(id, values) {
  const text = Object.hasOwn(STRINGS, id) ? STRINGS[id] : id;
  if (!values) {
    return text;
  }
  return text.replace(/\{(\w+)\}/g, (whole, name) =>
    Object.hasOwn(values, name) ? String(values[name]) : whole,
  );
}
