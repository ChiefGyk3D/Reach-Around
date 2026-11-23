// SPDX-License-Identifier: Apache-2.0
// Live badges and the Live tab (#297, #673). It asks this site only (GET /e/live)
// on load and each minute while visible, lights badges and picks the panel, and
// builds a player iframe from two allowlisted origins on a press. Anything it
// cannot verify is degraded and never shows live. It keeps nothing in storage.
(function () {
  "use strict";
  var ROUTE = "/e/live";
  var POLL_MS = 60000;
  var TIMEOUT_MS = 5000;
  var MAX_BODY_CHARS = 2048;
  var MAX_BODY_BYTES = 2048;
  var STALE_AFTER_SECONDS = 1800;
  var FUTURE_SKEW_SECONDS = 300;
  var MAX_TTL_SECONDS = 3600;
  var MAX_UNIX_SECONDS = 4102444800;
  // BEGIN PLATFORM TABLE
  var PLATFORMS = Object.freeze([
    Object.freeze({
      id: "kick",
      name: "Kick",
      origin: "https://player.kick.com",
      handle: /^[A-Za-z0-9_-]{1,40}$/,
      src: function (origin, handle) {
        return origin + "/" + handle;
      }
    }),
    Object.freeze({
      id: "twitch",
      name: "Twitch",
      origin: "https://player.twitch.tv",
      handle: /^[A-Za-z0-9_]{1,25}$/,
      src: function (origin, handle, host) {
        return origin + "/?channel=" + handle + "&parent=" + host;
      }
    })
  ]);
  // END PLATFORM TABLE
  function own(object, key) {
    return Object.prototype.hasOwnProperty.call(object, key);
  }
  function isPlain(value) {
    return (
      value !== null &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      Object.getPrototypeOf(value) === Object.prototype
    );
  }
  function hasExactKeys(object, keys) {
    var found = Object.keys(object).sort();
    var want = keys.slice().sort();
    if (found.length !== want.length) {
      return false;
    }
    for (var i = 0; i < want.length; i += 1) {
      if (found[i] !== want[i]) {
        return false;
      }
    }
    return true;
  }
  function isUnix(value) {
    return (
      typeof value === "number" &&
      Number.isInteger(value) &&
      value >= 1 &&
      value <= MAX_UNIX_SECONDS
    );
  }
  function platformState(entry) {
    if (!isPlain(entry)) {
      return undefined;
    }
    var keys = ["live", "since"];
    if (own(entry, "code")) {
      keys.push("code");
    }
    if (!hasExactKeys(entry, keys)) {
      return undefined;
    }
    var live = entry.live;
    if (live !== true && live !== false && live !== null) {
      return undefined;
    }
    if (live === true) {
      if (entry.since !== null && !isUnix(entry.since)) {
        return undefined;
      }
    } else if (entry.since !== null) {
      return undefined;
    }
    return live;
  }
  function parseStatus(text, nowSeconds) {
    if (typeof text !== "string" || text.length === 0 || text.length > MAX_BODY_CHARS) {
      return null;
    }
    var data;
    try {
      data = JSON.parse(text);
    } catch (ignored) {
      return null;
    }
    var keys = ["v", "as_of", "ttl"];
    PLATFORMS.forEach(function (p) {
      keys.push(p.id);
    });
    if (!isPlain(data) || !hasExactKeys(data, keys)) {
      return null;
    }
    if (data.v !== 1 || !isUnix(data.as_of)) {
      return null;
    }
    if (
      typeof data.ttl !== "number" ||
      !Number.isInteger(data.ttl) ||
      data.ttl < 1 ||
      data.ttl > MAX_TTL_SECONDS
    ) {
      return null;
    }
    var result = {};
    for (var i = 0; i < PLATFORMS.length; i += 1) {
      var state = platformState(data[PLATFORMS[i].id]);
      if (state === undefined) {
        return null;
      }
      result[PLATFORMS[i].id] = state;
    }
    var age = nowSeconds - data.as_of;
    if (age > STALE_AFTER_SECONDS || age < -FUTURE_SKEW_SECONDS) {
      return null;
    }
    return result;
  }
  function readBounded(response) {
    var body = response ? response.body : null;
    if (!body || typeof body.getReader !== "function") {
      return Promise.resolve(null);
    }
    var reader;
    var decoder;
    try {
      reader = body.getReader();
      decoder = new TextDecoder("utf-8", { fatal: true });
    } catch (ignored) {
      return Promise.resolve(null);
    }
    var total = 0;
    var text = "";
    function stop() {
      try {
        Promise.resolve(reader.cancel()).catch(function () {});
      } catch (ignored) {
      }
      return null;
    }
    function step() {
      return reader.read().then(function (part) {
        if (part.done) {
          return text + decoder.decode();
        }
        var chunk = part.value;
        if (!(chunk instanceof Uint8Array)) {
          return stop();
        }
        total += chunk.length;
        if (total > MAX_BODY_BYTES) {
          return stop();
        }
        text += decoder.decode(chunk, { stream: true });
        return step();
      });
    }
    return step().catch(stop);
  }
  function anyLive(status) {
    if (status === null || typeof status !== "object") {
      return false;
    }
    return PLATFORMS.some(function (p) {
      return own(status, p.id) && status[p.id] === true;
    });
  }
  function allNotLive(status) {
    if (status === null || typeof status !== "object") {
      return false;
    }
    return PLATFORMS.every(function (p) {
      return own(status, p.id) && status[p.id] === false;
    });
  }
  function viewState(status) {
    if (anyLive(status)) {
      return "live";
    }
    return allNotLive(status) ? "offline" : "degraded";
  }
  function badgeOn(status) {
    return anyLive(status);
  }
  var WORD_LIVE = "LIVE";
  var WORD_OFFLINE = "OFFLINE";
  function watchWord(status, id) {
    if (status === null || typeof status !== "object" || !own(status, id)) {
      return "";
    }
    if (status[id] === true) {
      return WORD_LIVE;
    }
    return status[id] === false ? WORD_OFFLINE : "";
  }
  function watchBadges(status) {
    var result = {};
    PLATFORMS.forEach(function (p) {
      result[p.id] = watchWord(status, p.id);
    });
    return result;
  }
  function watchAnnouncement(badges) {
    if (badges === null || typeof badges !== "object") {
      return "";
    }
    var parts = [];
    PLATFORMS.forEach(function (p) {
      var word = own(badges, p.id) ? badges[p.id] : "";
      if (word === WORD_LIVE || word === WORD_OFFLINE) {
        parts.push(p.name + ": " + word.toLowerCase() + ".");
      }
    });
    return parts.join(" ");
  }
  function playerSrc(platform, channel, host) {
    if (typeof platform !== "string" || typeof channel !== "string") {
      return null;
    }
    if (typeof host !== "string" || host.length < 1 || host.length > 253) {
      return null;
    }
    for (var i = 0; i < PLATFORMS.length; i += 1) {
      var p = PLATFORMS[i];
      if (p.id === platform) {
        if (!p.handle.test(channel)) {
          return null;
        }
        return p.src(p.origin, channel, encodeURIComponent(host));
      }
    }
    return null;
  }
  function playerName(platform) {
    for (var i = 0; i < PLATFORMS.length; i += 1) {
      if (PLATFORMS[i].id === platform) {
        return PLATFORMS[i].name;
      }
    }
    return "";
  }
  if (typeof module === "object" && module !== null && module.exports) {
    module.exports = {
      PLATFORMS: PLATFORMS,
      STALE_AFTER_SECONDS: STALE_AFTER_SECONDS,
      MAX_BODY_CHARS: MAX_BODY_CHARS,
      MAX_BODY_BYTES: MAX_BODY_BYTES,
      readBounded: readBounded,
      parseStatus: parseStatus,
      viewState: viewState,
      badgeOn: badgeOn,
      watchBadges: watchBadges,
      watchAnnouncement: watchAnnouncement,
      playerSrc: playerSrc
    };
    return;
  }
  if (typeof document === "undefined") {
    return;
  }
  var roots = document.querySelectorAll("[data-live]");
  var badges = document.querySelectorAll("[data-live-badge]");
  var watchBadgeEls = document.querySelectorAll("[data-watch-badge]");
  var watchRegion = document.querySelector("[data-watch-status]");
  if (roots.length === 0 && badges.length === 0 && watchBadgeEls.length === 0) {
    return;
  }
  var PANELS = {
    live: ["live"],
    offline: ["offline"],
    degraded: ["degraded", "offline"]
  };
  function hasPlayer(root) {
    return root.querySelector("iframe") !== null;
  }
  function setSlots(card, live) {
    var slots = card.querySelectorAll("[data-live-slot]");
    for (var i = 0; i < slots.length; i += 1) {
      var isPlayer = slots[i].getAttribute("data-live-slot") === "player";
      slots[i].hidden = isPlayer ? !live : live;
    }
  }
  function applyWatch(status) {
    var words = watchBadges(status);
    for (var w = 0; w < watchBadgeEls.length; w += 1) {
      var id = watchBadgeEls[w].getAttribute("data-watch-badge");
      var word = id !== null && own(words, id) ? words[id] : "";
      watchBadgeEls[w].textContent = word;
      watchBadgeEls[w].hidden = word === "";
    }
    if (watchRegion) {
      var said = watchAnnouncement(words);
      if (watchRegion.textContent !== said) {
        watchRegion.textContent = said;
      }
    }
  }
  function apply(status) {
    applyWatch(status);
    var state = viewState(status);
    var lit = badgeOn(status);
    for (var b = 0; b < badges.length; b += 1) {
      badges[b].hidden = !lit;
    }
    for (var r = 0; r < roots.length; r += 1) {
      var root = roots[r];
      if (hasPlayer(root)) {
        continue;
      }
      root.setAttribute("data-live-state", state);
      var panels = root.querySelectorAll("[data-live-panel]");
      for (var i = 0; i < panels.length; i += 1) {
        panels[i].hidden = PANELS[state].indexOf(panels[i].getAttribute("data-live-panel")) < 0;
      }
      if (state !== "live") {
        continue;
      }
      var cards = root.querySelectorAll(".live-card");
      for (var c = 0; c < cards.length; c += 1) {
        var facade = cards[c].querySelector("[data-platform]");
        var id = facade ? facade.getAttribute("data-platform") : "";
        setSlots(cards[c], id !== null && own(status, id) && status[id] === true);
      }
    }
  }
  function load(button) {
    var platform = button.getAttribute("data-platform");
    var src = playerSrc(
      platform,
      button.getAttribute("data-channel"),
      window.location.hostname
    );
    if (src === null || !button.parentNode) {
      return;
    }
    var frame = document.createElement("iframe");
    frame.setAttribute("title", playerName(platform) + " live player");
    frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-presentation");
    frame.setAttribute("allow", "autoplay; fullscreen; picture-in-picture");
    frame.setAttribute("referrerpolicy", "strict-origin-when-cross-origin");
    frame.setAttribute("loading", "lazy");
    frame.setAttribute("src", src);
    var box = document.createElement("div");
    box.className = "live-frame";
    box.appendChild(frame);
    button.parentNode.replaceChild(box, button);
    frame.focus();
  }
  function onClick(event) {
    var target = event.target;
    var button = target && target.closest ? target.closest("button.live-facade") : null;
    if (button) {
      load(button);
    }
  }
  for (var r = 0; r < roots.length; r += 1) {
    roots[r].addEventListener("click", onClick);
  }
  var timer = null;
  var inflight = false;
  var checks = 0;
  function request() {
    return new Promise(function (resolve) {
      var finished = false;
      var control = new AbortController();
      var guard = setTimeout(function () {
        finish(null);
        control.abort();
      }, TIMEOUT_MS);
      function finish(text) {
        if (finished) {
          return;
        }
        finished = true;
        clearTimeout(guard);
        resolve(text);
      }
      fetch(ROUTE, {
        method: "GET",
        credentials: "omit",
        redirect: "error",
        referrerPolicy: "no-referrer",
        signal: control.signal
      }).then(function (response) {
        if (response.status !== 200) {
          finish(null);
          if (response.body) {
            response.body.cancel().catch(function () {});
          }
          return;
        }
        readBounded(response).then(finish, function () {
          finish(null);
        });
      }, function () {
        finish(null);
      });
    });
  }
  function schedule() {
    clearTimeout(timer);
    timer = null;
    if (!document.hidden) {
      timer = setTimeout(check, POLL_MS);
    }
  }
  function check() {
    clearTimeout(timer);
    timer = null;
    if (inflight) {
      return;
    }
    inflight = true;
    request().then(function (text) {
      inflight = false;
      apply(parseStatus(text, Math.floor(Date.now() / 1000)));
      checks += 1;
      document.documentElement.setAttribute("data-live-checks", String(checks));
      schedule();
    });
  }
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      clearTimeout(timer);
      timer = null;
    } else {
      check();
    }
  });
  check();
})();
