// SPDX-License-Identifier: Apache-2.0
// The invisible bot check, helper page side. This runs only inside the hidden
// frame that static/beacon.js opens on /e-frame/. It renders the Cloudflare
// Turnstile widget (its script is loaded by the page, before this one) and posts
// one message to the parent:
//   {type, nonce, state: "token", token}  or  {type, nonce, state: "failed"}
// It stores nothing itself and never retries. Opened on its own, it does nothing.
//
// This page runs third-party code (Cloudflare's script). The parent sandboxes
// the frame without allow-same-origin, so its origin is opaque ("null"): it
// cannot read or change the parent, its storage or its cookies. An opaque origin
// cannot be named as a target, so the message goes to "*", but only ever to
// window.parent. The nonce comes from this page's address fragment, made by the
// parent for this one frame; the parent ignores any message without it. The
// vendor script can read the same fragment, so the nonce is not a defence
// against it: the server must check the token itself.
(function () {
  "use strict";

  var TYPE = "reach-around-beacon";
  var KEY = /^[0-9A-Za-z_-]{8,64}$/;
  var TOKEN = /^[A-Za-z0-9._-]{1,4096}$/;
  var NONCE = /^[0-9a-f]{32}$/;
  var sent = false;

  if (window.parent === window) {
    return;
  }

  var nonce = String(location.hash).slice(1);
  if (!NONCE.test(nonce)) {
    return;
  }

  function post(message) {
    if (sent) {
      return;
    }
    sent = true;
    try {
      window.parent.postMessage(message, "*");
    } catch (ignored) {
      // The parent is gone; nothing to report to.
    }
  }

  function fail() {
    post({ type: TYPE, nonce: nonce, state: "failed" });
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
          post({ type: TYPE, nonce: nonce, state: "token", token: token });
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
