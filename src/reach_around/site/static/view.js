// SPDX-License-Identifier: Apache-2.0
// Page-view counting, only on sites that turn it on. One small form-encoded
// beacon per page load to a same-origin route; the server works out the rest.
//
// Sent: the referring site's host name, an allowlisted utm_source label, a
// short source label from the link's own `s` parameter, and one letter for the
// window width. Never sent: the page address, the referrer
// path or query, or anything else. Nothing is stored in the browser and this
// script reads no identifier. There is no fallback request: without
// navigator.sendBeacon it does nothing.
(function () {
  "use strict";

  if (typeof navigator.sendBeacon !== "function") {
    return;
  }
  // Global Privacy Control and Do Not Track: send nothing at all. Any of the
  // three Do Not Track properties set to "1", "yes" or "true" (any case, spaces
  // ignored) is an opt-out. Checked at start and again at send time, so a
  // signal that arrives while the page is prerendering or hidden still wins.
  // The check itself is in the shared block below.

  // --- beacon token (this block is identical in click.js and view.js; a test keeps them equal) ---
  // A short-lived signed token the Worker hands out once per page, kept in this page's memory only
  // (never in storage or a cookie) and added to the click ping or the view beacon, so the Worker can tell a
  // request from this page from one that was typed by hand. It is only asked for when the build turned it
  // on (data-token-route), never for a visitor with Global Privacy Control or Do Not Track, and a failed
  // or slow request just means the event goes without a token. Nothing waits on it except the view beacon,
  // and only briefly.
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
      (typeof window === "object" && window !== null && dntOn(window.doNotTrack)) ||
      dntOn(navigator.msDoNotTrack)
    );
  }

  var MINT_ROUTE = "/e/t";
  var TOKEN_RE = /^b1\.[A-Za-z0-9_-]{1,200}\.[A-Za-z0-9_-]{1,100}$/;
  var RETRY_MS = 60000;
  var held = { value: "", until: 0, retryAt: 0, pending: false, waiting: [] };
  var shared =
    typeof window === "object" && window !== null
      ? window.__raBeacon || (window.__raBeacon = held)
      : held;

  function tokenWanted() {
    var body = document.body;
    return !!body && body.getAttribute("data-token-route") === MINT_ROUTE && !optedOut();
  }

  function freshToken() {
    return shared.value && Date.now() < shared.until ? shared.value : "";
  }

  // Calls done(token) once, with "" when there is no token. Never throws, never blocks.
  function requestToken(done) {
    var have = freshToken();
    if (have) {
      done(have);
      return;
    }
    if (typeof fetch !== "function" || Date.now() < shared.retryAt) {
      done("");
      return;
    }
    shared.waiting.push(done);
    if (shared.pending) {
      return;
    }
    shared.pending = true;
    function finish(token, lifeSeconds) {
      shared.pending = false;
      if (token) {
        shared.value = token;
        shared.until = Date.now() + (lifeSeconds - 30) * 1000;
      } else {
        shared.value = "";
        shared.retryAt = Date.now() + RETRY_MS;
      }
      var list = shared.waiting;
      shared.waiting = [];
      for (var i = 0; i < list.length; i++) {
        try {
          list[i](token);
        } catch (ignored) {
          // One caller's failure never stops another.
        }
      }
    }
    try {
      fetch(MINT_ROUTE, { method: "POST", credentials: "omit", cache: "no-store" })
        .then(function (response) {
          return response.status === 200 ? response.json() : null;
        })
        .then(function (data) {
          var token = data && typeof data.t === "string" ? data.t : "";
          var life = data && typeof data.ttl === "number" ? data.ttl : 0;
          if (token.length > 320 || !TOKEN_RE.test(token) || !(life > 60 && life <= 900)) {
            finish("", 0);
            return;
          }
          finish(token, life);
        })
        .catch(function () {
          finish("", 0);
        });
    } catch (ignored) {
      finish("", 0);
    }
  }
  // --- end of the beacon token block ---

  if (optedOut()) {
    return;
  }

  var body = document.body;
  var id = body ? body.getAttribute("data-page-id") : null;
  var route = body ? body.getAttribute("data-view-route") : null;
  if (!id || !/^[a-z0-9][a-z0-9-]{0,39}$/.test(id)) {
    return;
  }
  if (!route || route.length > 64 || !/^(?:\/[A-Za-z0-9_~-][A-Za-z0-9._~-]*)+\/$/.test(route)) {
    return;
  }

  var done = false;

  function referrerHost() {
    if (!document.referrer) {
      return null;
    }
    try {
      var host = new URL(document.referrer).hostname.toLowerCase();
      if (!host || host.length > 80 || host.indexOf("[") !== -1 || host.indexOf(":") !== -1) {
        return null;
      }
      // Only a real name: at least two labels and a last label with a letter.
      // This leaves out IPv4 literals in every spelling (dotted, decimal, hex,
      // octal), single-label hosts and anything ending in a number, all of
      // which the Worker would refuse, dropping the whole beacon with them.
      var labels = host.split(".");
      if (labels.length < 2 || !/[a-z]/.test(labels[labels.length - 1])) {
        return null;
      }
      return host;
    } catch (ignored) {
      return null;
    }
  }

  function utmSource() {
    try {
      var value = new URLSearchParams(location.search).get("utm_source");
      if (value === null) {
        return null;
      }
      // Judge the raw text first: some non-ASCII letters (the Kelvin sign,
      // dotless i, long s) turn into ASCII ones when lowered.
      return /^[A-Za-z0-9-]{1,30}$/.test(value) ? value.toLowerCase() : null;
    } catch (ignored) {
      return null;
    }
  }

  function sourceLabel() {
    try {
      var value = new URLSearchParams(location.search).get("s");
      if (value === null) {
        return null;
      }
      // Judge the raw text first: some non-ASCII letters (the Kelvin sign,
      // dotless i, long s) turn into ASCII ones when lowered.
      return /^[A-Za-z0-9-]{1,30}$/.test(value) ? value.toLowerCase() : null;
    } catch (ignored) {
      return null;
    }
  }

  function widthLetter() {
    var w = window.innerWidth;
    if (w < 480) {
      return "s";
    }
    if (w < 768) {
      return "m";
    }
    if (w < 1200) {
      return "l";
    }
    return "x";
  }

  // The beacon itself. The opt-out is checked again here, after any wait for a token.
  function transmit(token) {
    if (optedOut()) {
      return;
    }
    try {
      var params = new URLSearchParams();
      var r = referrerHost();
      var u = utmSource();
      var sl = sourceLabel();
      if (r !== null) {
        params.append("r", r);
      }
      if (u !== null) {
        params.append("u", u);
      }
      if (sl !== null) {
        params.append("s", sl);
      }
      params.append("w", widthLetter());
      navigator.sendBeacon(route + id + (token ? "?t=" + token : ""), params);
    } catch (ignored) {
      // Counting is best effort; never get in the way of the page.
    }
  }

  // The most the beacon waits for a token. After this it goes without one.
  var TOKEN_WAIT_MS = 1500;

  function send() {
    if (done || optedOut()) {
      return;
    }
    done = true;
    if (!tokenWanted()) {
      transmit("");
      return;
    }
    var once = false;
    var timer = null;
    function go(token) {
      if (once) {
        return;
      }
      once = true;
      clearTimeout(timer);
      transmit(token);
    }
    timer = setTimeout(function () {
      go("");
    }, TOKEN_WAIT_MS);
    requestToken(go);
  }

  // `done` is the only guard against a second send, so repeated events are safe.
  function onVisibility() {
    if (document.visibilityState !== "hidden") {
      send();
    }
  }

  function whenVisible() {
    if (document.visibilityState === "hidden") {
      document.addEventListener("visibilitychange", onVisibility);
      return;
    }
    send();
  }

  if (document.prerendering) {
    document.addEventListener("prerenderingchange", whenVisible);
  } else {
    whenVisible();
  }
})();
