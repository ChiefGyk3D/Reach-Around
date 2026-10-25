// SPDX-License-Identifier: Apache-2.0
// Progressive enhancement for click counting. Links carry a `ping` attribute
// (/e/c/<id>); browsers that support it report the click themselves. For the
// rest, send the same request with navigator.sendBeacon. Nothing here blocks
// or changes navigation, so links work when the counter is unreachable.
(function () {
  "use strict";

  var probe = document.createElement("a");
  if ("ping" in probe) {
    return;
  }
  if (typeof navigator.sendBeacon !== "function") {
    return;
  }

  var ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

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
      navigator.sendBeacon("/e/c/" + id);
    } catch (ignored) {
      // Counting is best effort; never get in the way of the link.
    }
  });
})();
