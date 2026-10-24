// SPDX-License-Identifier: Apache-2.0
// Reload both frames when the server reports a new build.
(function () {
  var seen = null;
  function poll() {
    fetch("/__preview/version", { cache: "no-store" })
      .then(function (r) { return r.text(); })
      .then(function (v) {
        if (seen !== null && v !== seen) {
          document.querySelectorAll("iframe").forEach(function (f) {
            f.contentWindow.location.reload();
          });
        }
        seen = v;
      })
      .catch(function () {});
  }
  setInterval(poll, 1000);
  poll();
})();
