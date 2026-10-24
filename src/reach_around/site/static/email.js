// SPDX-License-Identifier: Apache-2.0
// The email block. The built page holds the address only reversed and base64
// encoded in a data attribute, never as text or a link. This file decodes it
// when the block is revealed (on a click, or at load for reveal: always),
// then shows the address as selectable text, creates the mail link only at
// that moment, and offers Write email and Copy buttons. It makes no network
// request, stores nothing and builds no HTML from strings. Navigating to a
// mail link is not a script load, so script-src 'self' is unaffected.
//
// The one place that navigates is open(): it calls window.__raOpen(url) when a
// test or embedder defined it, and otherwise sets window.location.href.
(function () {
  "use strict";

  var RESET_MS = 2000;
  var SCHEME = "mailto:";
  var SHAPE = /^[A-Za-z0-9%+_.-]{1,64}@[A-Za-z0-9.-]{3,253}$/;

  function decode(card) {
    var raw = card.getAttribute("data-email");
    if (!raw) {
      return null;
    }
    var plain;
    try {
      plain = window.atob(raw);
    } catch (error) {
      return null;
    }
    var address = plain.split("").reverse().join("");
    return SHAPE.test(address) ? address : null;
  }

  function open(url) {
    if (typeof window.__raOpen === "function") {
      window.__raOpen(url);
    } else {
      window.location.href = url;
    }
  }

  function announce(card, message) {
    var status = card.querySelector("[data-email-status]");
    if (status) {
      status.textContent = message;
    }
  }

  function flash(button, card, buttonText, message) {
    var original = button.getAttribute("data-email-label");
    if (original === null) {
      original = button.textContent;
      button.setAttribute("data-email-label", original);
    }
    button.textContent = buttonText;
    announce(card, message);
    if (button.emailTimer) {
      window.clearTimeout(button.emailTimer);
    }
    button.emailTimer = window.setTimeout(function () {
      button.textContent = original;
      announce(card, "");
      button.emailTimer = 0;
    }, RESET_MS);
  }

  function selectText(node) {
    var selection = window.getSelection();
    if (!selection || !document.createRange) {
      return false;
    }
    var range = document.createRange();
    range.selectNodeContents(node);
    selection.removeAllRanges();
    selection.addRange(range);
    return true;
  }

  function copy(button, card, link, address) {
    var fallback = function () {
      if (selectText(link)) {
        flash(button, card, "Selected", "Address selected. Press Control C or use the menu to copy.");
      }
    };
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(address).then(function () {
        flash(button, card, "Copied", "Copied");
      }, fallback);
    } else {
      fallback();
    }
  }

  function makeButton(className, text) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "email-button " + className;
    button.textContent = text;
    return button;
  }

  function reveal(card) {
    if (card.getAttribute("data-email-state") === "open") {
      return;
    }
    var address = decode(card);
    var holder = card.querySelector(".email-revealed");
    if (!address || !holder) {
      return;
    }
    var url = SCHEME + address;
    // The link exists only from this point on. Its own activation goes through
    // the same open() as the Write email button.
    var link = document.createElement("a");
    link.className = "email-address";
    link.href = url;
    link.textContent = address;
    link.addEventListener("click", function (event) {
      event.preventDefault();
      open(url);
    });
    var write = makeButton("email-write", "Write email");
    write.addEventListener("click", function () {
      open(url);
    });
    var copyButton = makeButton("email-copy", "Copy");
    copyButton.addEventListener("click", function () {
      copy(copyButton, card, link, address);
    });
    var buttons = document.createElement("div");
    buttons.className = "email-buttons";
    buttons.appendChild(write);
    buttons.appendChild(copyButton);
    holder.appendChild(link);
    holder.appendChild(buttons);
    holder.hidden = false;
    card.setAttribute("data-email-state", "open");
    announce(card, "Email address shown");
  }

  function start() {
    var cards = document.querySelectorAll(".email-card[data-email]");
    if (!cards.length) {
      return;
    }
    for (var i = 0; i < cards.length; i++) {
      var card = cards[i];
      if (card.getAttribute("data-email-reveal") === "always") {
        reveal(card);
        continue;
      }
      var show = card.querySelector('button[data-email-action="show"]');
      if (show) {
        show.addEventListener("click", function (event) {
          reveal(event.currentTarget.closest(".email-card"));
        });
      }
    }
    // The Show button stays hidden by the stylesheet until this mark is set,
    // so a visitor without script sees the spelled address and no dead control.
    document.documentElement.classList.add("email-ready");
  }

  start();
})();
