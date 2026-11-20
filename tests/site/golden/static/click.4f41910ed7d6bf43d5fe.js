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
//
// Token: when the build turns it on (`data-token-route` on <body>), the page
// asks the Worker once for a short-lived signed token, keeps it in memory, and
// adds it to the ping as `t`. See the shared block below.
(function () {
  "use strict";

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

  // Global Privacy Control and Do Not Track are judged by dntOn/optedOut in the shared block above,
  // the same way view.js does. Read at click time, so a signal that changes while the page is open still wins.
  // Fails toward privacy: if the signals cannot be read (a getter that throws),
  // the visitor counts as opted out and nothing is sent.
  function optedOutSafe() {
    try {
      return optedOut();
    } catch (ignored) {
      return true;
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

  // The query for this click: the labels, plus the token when there is a fresh one and the visitor has not
  // opted out (checked now, not only at load). With none, the request is started for the next click and this
  // one goes without; the click never waits.
  function queryNow() {
    try {
      if (!tokenWanted()) {
        return query;
      }
      var token = freshToken();
      if (!token) {
        requestToken(function () {});
        return query;
      }
      return (query ? query + "&" : "?") + "t=" + token;
    } catch (ignored) {
      return query;
    }
  }

  // Ask once the page is idle, or on the first sign of a visit to a link, whichever comes first.
  function askOnce() {
    try {
      if (tokenWanted()) {
        requestToken(function () {});
      }
    } catch (ignored) {
      // Counting is best effort.
    }
  }

  try {
    if (tokenWanted()) {
      if (typeof requestIdleCallback === "function") {
        requestIdleCallback(askOnce);
      } else {
        setTimeout(askOnce, 1500);
      }
      ["pointerover", "touchstart", "focusin"].forEach(function (name) {
        document.addEventListener(name, askOnce, { once: true, passive: true });
      });
    }
  } catch (ignored) {
    // Counting is best effort; never get in the way of the page.
  }

  function stripPing(link) {
    try {
      if (link.hasAttribute("ping")) {
        link.removeAttribute("ping");
      }
    } catch (ignored) {
      // Nothing more can be done for this link.
    }
  }

  // Which of our links (if any) an event came from.
  function linkOf(event) {
    var node = event.target;
    while (node && node.nodeType === 1 && !(node.hasAttribute && node.hasAttribute("data-link-id"))) {
      node = node.parentNode;
    }
    return node && node.nodeType === 1 ? node : null;
  }

  // A visitor who opted out must not carry a ping on any link: not only when
  // clicked, but also when it is opened without a click (context menu, middle
  // button). Strip at load, and again on those other gestures in case the
  // signal changed while the page was open. These never send anything.
  // A ping removed here is deliberately not put back if the signal is cleared
  // later in the same page view: that fails toward privacy, and a reload reads
  // the signal again.
  function stripAllIfOptedOut() {
    if (!native || !optedOutSafe()) {
      return;
    }
    try {
      var links = document.querySelectorAll("[data-link-id][ping]");
      for (var i = 0; i < links.length; i++) {
        stripPing(links[i]);
      }
    } catch (ignored) {
      // Best effort; the click-time strip still applies.
    }
  }
  stripAllIfOptedOut();

  function otherGesture(event) {
    if (!native || !optedOutSafe()) {
      return;
    }
    var link = linkOf(event);
    if (link) {
      stripPing(link);
    }
  }
  document.addEventListener("auxclick", otherGesture);
  document.addEventListener("contextmenu", otherGesture);

  document.addEventListener("click", function (event) {
    var node = linkOf(event);
    if (!node) {
      return;
    }
    var id = node.getAttribute("data-link-id");
    if (!id || !ID.test(id)) {
      return;
    }
    if (optedOutSafe()) {
      // Send nothing. A browser that pings on its own does so from the
      // `ping` attribute, so take that off; the link itself is untouched and
      // navigation goes ahead as before.
      if (native) {
        stripPing(node);
      }
      return;
    }
    try {
      var q = queryNow();
      if (native) {
        // The browser sends the ping itself; set it just before it does.
        if (q && node.hasAttribute("ping")) {
          node.setAttribute("ping", "/e/c/" + id + q);
        }
        return;
      }
      navigator.sendBeacon("/e/c/" + id + q);
    } catch (ignored) {
      // Counting is best effort; never get in the way of the link.
    }
  });
})();
