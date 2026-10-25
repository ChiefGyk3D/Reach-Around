// The session key arrives once, in the URL fragment (#t=KEY). It is read,
// removed from the address bar at once, and held in this variable only: never
// in storage, a cookie, a global or the DOM.
let held = "";

const SHAPE = /^[A-Za-z0-9_-]{16,128}$/;

export function takeTokenFromUrl(where = location, past = history) {
  const params = new URLSearchParams(where.hash.slice(1));
  const found = params.get("t") || "";
  if (where.hash) {
    past.replaceState(null, "", where.pathname);
  }
  held = SHAPE.test(found) ? found : "";
  return held !== "";
}

export function getToken() {
  return held;
}
