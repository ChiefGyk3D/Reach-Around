// SPDX-License-Identifier: Apache-2.0
// The invisible bot check, page side (only on sites that turn it on). After the
// page has loaded and the browser is idle, open one hidden same-origin frame
// (/e-frame/) that runs the check and posts the result back. The result is held
// in memory only, as window.reachAroundBeacon and one "reach-around:beacon"
// event on the document: {state: "verified", token} or {state: "unverified"}.
//
// The frame runs Cloudflare's script, which this repository cannot vouch for,
// so the frame is sandboxed WITHOUT allow-same-origin: it gets an opaque origin
// and cannot read or change this page, its storage or its cookies. Because of
// that, its messages arrive with the origin string "null", which says nothing
// about who sent them. They are accepted only when ALL of these hold: the origin
// is exactly "null", the sender is exactly our own frame's window, the message
// is a plain object with exactly the expected own keys, and it carries the
// random nonce this page made for this frame. The nonce is passed in the frame's
// address fragment (never sent to a server). It is NOT a secret from the vendor
// script, which can read its own address; it keeps a stray frame or a replayed
// message from being taken for ours. A "verified" state here is therefore a hint
// for the browser side only: the token must still be checked by the server
// (siteverify, hostname and action) before anything trusts it.
//
// Never done here: no request when Global Privacy Control or Do Not Track is
// on (checked at start and again just before the frame is made), nothing
// stored in the browser, no change to a link. A blocked, slow or failed check
// ends as "unverified" after a short wait; the page and every link work the
// same either way.
(function () {
  "use strict";

  var FRAME = "/e-frame/";
  var TYPE = "reach-around-beacon";
  var TIMEOUT_MS = 5000;
  var IDLE_MS = 3000;
  var TOKEN = /^[A-Za-z0-9._-]{1,4096}$/;
  var TOKEN_KEYS = "nonce,state,token,type";

  function dntOn(value) {
    if (typeof value === "number") {
      value = String(value);
    }
    if (typeof value !== "string") {
      return false;
    }
    value = value.trim().toLowerCase();
    return value === "1" || value === "yes" || value === "true";
  }

  function optedOut() {
    return (
      navigator.globalPrivacyControl === true ||
      dntOn(navigator.doNotTrack) ||
      dntOn(window.doNotTrack) ||
      dntOn(navigator.msDoNotTrack)
    );
  }

  if (optedOut()) {
    return;
  }

  var frame = null;
  var nonce = null;
  var timer = null;
  var settled = false;

  function settle(state, token) {
    if (settled) {
      return;
    }
    settled = true;
    window.removeEventListener("message", onMessage);
    if (timer !== null) {
      clearTimeout(timer);
    }
    try {
      if (frame && frame.parentNode) {
        frame.parentNode.removeChild(frame);
      }
    } catch (ignored) {
      // Nothing to clean up.
    }
    var result = state === "verified" ? { state: state, token: token } : { state: state };
    window.reachAroundBeacon = Object.freeze(result);
    try {
      document.dispatchEvent(new CustomEvent("reach-around:beacon", { detail: result }));
    } catch (ignored) {
      // The state above is already set.
    }
  }

  function ownKeys(data) {
    return Object.keys(data).sort().join(",");
  }

  function onMessage(event) {
    // An opaque-origin frame reports the origin "null"; anything else is not it.
    if (!frame || event.origin !== "null" || event.source !== frame.contentWindow) {
      return;
    }
    var data = event.data;
    if (!data || typeof data !== "object" || data.type !== TYPE || data.nonce !== nonce) {
      return;
    }
    if (
      data.state === "token" &&
      ownKeys(data) === TOKEN_KEYS &&
      typeof data.token === "string" &&
      TOKEN.test(data.token)
    ) {
      settle("verified", data.token);
    } else {
      // "failed", or anything else from our own frame with our nonce.
      settle("unverified");
    }
  }

  function makeNonce() {
    if (typeof crypto === "undefined" || !crypto || typeof crypto.getRandomValues !== "function") {
      return null;
    }
    var bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    var out = "";
    for (var i = 0; i < bytes.length; i++) {
      out += (bytes[i] + 256).toString(16).slice(1);
    }
    return out;
  }

  function start() {
    if (settled || frame || optedOut()) {
      return;
    }
    try {
      nonce = makeNonce();
      if (nonce === null) {
        settle("unverified");
        return;
      }
      frame = document.createElement("iframe");
      frame.setAttribute("src", FRAME + "#" + nonce);
      frame.setAttribute("title", "Bot check, not shown");
      // Scripts only: no same-origin, forms, popups or top navigation.
      frame.setAttribute("sandbox", "allow-scripts");
      frame.setAttribute("aria-hidden", "true");
      frame.setAttribute("tabindex", "-1");
      frame.setAttribute("width", "0");
      frame.setAttribute("height", "0");
      // Style through the object model: the page policy forbids a style attribute.
      frame.style.position = "absolute";
      frame.style.border = "0";
      frame.style.opacity = "0";
      frame.style.pointerEvents = "none";
      window.addEventListener("message", onMessage);
      timer = setTimeout(function () {
        settle("unverified");
      }, TIMEOUT_MS);
      document.body.appendChild(frame);
    } catch (ignored) {
      settle("unverified");
    }
  }

  function whenIdle() {
    if (typeof requestIdleCallback === "function") {
      requestIdleCallback(start, { timeout: IDLE_MS });
    } else {
      setTimeout(start, 1);
    }
  }

  function afterLoad() {
    if (document.readyState === "complete") {
      whenIdle();
    } else {
      window.addEventListener("load", whenIdle);
    }
  }

  if (document.prerendering) {
    document.addEventListener("prerenderingchange", afterLoad);
  } else {
    afterLoad();
  }
})();
