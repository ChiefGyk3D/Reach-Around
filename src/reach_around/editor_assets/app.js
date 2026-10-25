// Placeholder shell. The session key arrives in the URL fragment, is kept in
// this variable only, and is removed from the address bar at once.
const params = new URLSearchParams(location.hash.slice(1));
const token = params.get("t") || "";
history.replaceState(null, "", location.pathname);

const status = document.getElementById("status");

async function load() {
  if (!token) {
    status.textContent = "Open the link printed in your terminal.";
    return;
  }
  const reply = await fetch("/__edit/api/state", {
    headers: { "X-RA-Token": token },
  });
  if (!reply.ok) {
    status.textContent = "The editor refused the request.";
    return;
  }
  const state = await reply.json();
  status.textContent = "Config hash " + state.hash.slice(0, 12);
}

load().catch(() => {
  status.textContent = "The editor could not be reached.";
});
