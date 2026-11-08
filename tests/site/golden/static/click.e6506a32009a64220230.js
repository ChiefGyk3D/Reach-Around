// SPDX-License-Identifier: Apache-2.0
// Progressive enhancement for click counting. Links carry a `ping` attribute
// (/e/c/<id>); browsers that support it report the click themselves. For the
// rest, send the same request with navigator.sendBeacon. Nothing here blocks
// or changes navigation, so links work when the counter is unreachable.
//
// Source: if the page address carries the short source label `s` (the same
// closed label the view counter reads), the ping carries that one label as
// `?s=`. The Worker checks it against the site's list and keeps nothing else.
// Nothing is stored in the browser and no identifier is read or sent.
(function () {
  "use strict";

  var ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

  function sourceLabel() {
    try {
      var all = new URLSearchParams(location.search).getAll("s");
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

  var label = sourceLabel();
  var query = label ? "?s=" + label : "";
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
