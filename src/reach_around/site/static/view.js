// SPDX-License-Identifier: Apache-2.0
// Page-view counting, only on sites that turn it on. One small form-encoded
// beacon per page load to a same-origin route; the server works out the rest.
//
// Sent: the referring site's host name, an allowlisted utm_source label, and
// one letter for the window width. Never sent: the page address, the referrer
// path or query, or anything else. Nothing is stored in the browser and this
// script reads no identifier. There is no fallback request: without
// navigator.sendBeacon it does nothing.
(function () {
  "use strict";

  if (typeof navigator.sendBeacon !== "function") {
    return;
  }
  // Global Privacy Control and Do Not Track: send nothing at all.
  if (navigator.globalPrivacyControl === true) {
    return;
  }
  var dnt = navigator.doNotTrack;
  if (dnt === "1" || dnt === "yes" || window.doNotTrack === "1" || navigator.msDoNotTrack === "1") {
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
      value = value.toLowerCase();
      return /^[a-z0-9-]{1,30}$/.test(value) ? value : null;
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

  function send() {
    if (done) {
      return;
    }
    done = true;
    try {
      var params = new URLSearchParams();
      var r = referrerHost();
      var u = utmSource();
      if (r !== null) {
        params.append("r", r);
      }
      if (u !== null) {
        params.append("u", u);
      }
      params.append("w", widthLetter());
      navigator.sendBeacon(route + id, params);
    } catch (ignored) {
      // Counting is best effort; never get in the way of the page.
    }
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
