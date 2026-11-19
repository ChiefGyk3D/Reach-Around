// SPDX-License-Identifier: Apache-2.0
// Progressive enhancement for click counting. Links carry a `ping` attribute
// (/e/c/<id>); browsers that support it report the click themselves. For the
// rest, send the same request with navigator.sendBeacon. Nothing here blocks
// or changes navigation, so links work when the counter is unreachable.
//
// Labels: the ping carries up to three short closed labels and nothing else.
// `s` (source) and `h` (hop: the surface of ours the visitor came from, one
// step) are read from the page address; `l` (landing: the page the click was
// made on) is a constant the build bakes into the page as `data-landing`. The
// Worker checks each against the site's own list and keeps nothing else.
// Nothing is stored in the browser and no identifier is read or sent.
(function () {
  "use strict";

  var ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

  // One closed label from the page address: exactly one value, judged raw.
  function paramLabel(name) {
    try {
      var all = new URLSearchParams(location.search).getAll(name);
      // Exactly one value; a repeated parameter is malformed and sends nothing.
      if (all.length !== 1) {
        return "";
      }
      var value = all[0];
      // Judge the raw text first: some non-ASCII letters (the Kelvin sign,
      // dotless i, long s) turn into ASCII ones when lowered.
      return /^[A-Za-z0-9-]{1,30}$/.test(value) ? value.toLowerCase() : "";
    } catch (ignored) {
      return "";
    }
  }

  // The landing label is a build-time constant on the page, never stored.
  function landingLabel() {
    try {
      var value = document.body && document.body.getAttribute("data-landing");
      return value && /^[a-z0-9-]{1,30}$/.test(value) ? value : "";
    } catch (ignored) {
      return "";
    }
  }

  var parts = [];
  var source = paramLabel("s");
  var hop = paramLabel("h");
  var landing = landingLabel();
  if (source) {
    parts.push("s=" + source);
  }
  if (hop) {
    parts.push("h=" + hop);
  }
  if (landing) {
    parts.push("l=" + landing);
  }
  var query = parts.length ? "?" + parts.join("&") : "";
  var probe = document.createElement("a");
  var native = "ping" in probe;
  if (!native && typeof navigator.sendBeacon !== "function") {
    return;
  }

  document.addEventListener("click", function (event) {
    var node = event.target;
    while (node && node.nodeType === 1 && !(node.hasAttribute && node.hasAttribute("data-link-id"))) {
      node = node.parentNode;
    }
    if (!node || node.nodeType !== 1) {
      return;
    }
    var id = node.getAttribute("data-link-id");
    if (!id || !ID.test(id)) {
      return;
    }
    try {
      if (native) {
        // The browser sends the ping itself; set it just before it does.
        if (query && node.hasAttribute("ping")) {
          node.setAttribute("ping", "/e/c/" + id + query);
        }
        return;
      }
      navigator.sendBeacon("/e/c/" + id + query);
    } catch (ignored) {
      // Counting is best effort; never get in the way of the link.
    }
  });
})();
