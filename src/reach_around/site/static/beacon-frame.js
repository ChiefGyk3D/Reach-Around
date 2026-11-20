// SPDX-License-Identifier: Apache-2.0
// The invisible bot check, helper page side. This runs only inside the hidden
// frame that static/beacon.js opens on /e-frame/. It renders the Cloudflare
// Turnstile widget (its script is loaded by the page, before this one) and posts
// one message to the parent, same origin only:
//   {type: "reach-around-beacon", state: "token", token: "..."}  or
//   {type: "reach-around-beacon", state: "failed"}
// It stores nothing itself and never retries. Opened on its own, it does nothing.
(function () {
  "use strict";

  var TYPE = "reach-around-beacon";
  var KEY = /^[0-9A-Za-z_-]{8,64}$/;
  var TOKEN = /^[A-Za-z0-9._-]{1,4096}$/;
  var sent = false;

  function post(message) {
    if (sent) {
      return;
    }
    sent = true;
    try {
      window.parent.postMessage(message, location.origin);
    } catch (ignored) {
      // The parent is gone; nothing to report to.
    }
  }

  function fail() {
    post({ type: TYPE, state: "failed" });
  }

  if (window.parent === window) {
    return;
  }

  var slot = document.getElementById("ra-turnstile");
  var key = slot ? slot.getAttribute("data-sitekey") : null;
  if (!slot || !key || !KEY.test(key) || !window.turnstile || typeof window.turnstile.render !== "function") {
    fail();
    return;
  }

  try {
    window.turnstile.render(slot, {
      sitekey: key,
      action: "beacon",
      retry: "never",
      "refresh-expired": "never",
      tabindex: -1,
      callback: function (token) {
        if (typeof token === "string" && TOKEN.test(token)) {
          post({ type: TYPE, state: "token", token: token });
        } else {
          fail();
        }
      },
      "error-callback": fail,
      "timeout-callback": fail,
      "expired-callback": fail,
    });
  } catch (ignored) {
    fail();
  }
})();
