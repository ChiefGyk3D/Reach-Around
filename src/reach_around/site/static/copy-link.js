// SPDX-License-Identifier: Apache-2.0
// Copy-link button for the public media kit page. The URL is public text that is
// already on the page: this file reads it from the link the button sits beside,
// hands it to the clipboard and announces the result in an aria-live region. It
// makes no network request, stores nothing, adds nothing to the URL and builds
// no HTML. With script off the button stays hidden and the link text is the
// whole control.
(function () {
  "use strict";

  var RESET_MS = 2000;
  var BOX = "kit-share";

  function box(node) {
    while (node && node.nodeType === 1 && !node.classList.contains(BOX)) {
      node = node.parentNode;
    }
    return node && node.nodeType === 1 ? node : null;
  }

  function announce(button, message) {
    var holder = box(button);
    var status = holder ? holder.querySelector("[data-copy-link-status]") : null;
    if (status) {
      status.textContent = message;
    }
  }

  function flash(button, message) {
    announce(button, message);
    if (button.copyTimer) {
      window.clearTimeout(button.copyTimer);
    }
    button.copyTimer = window.setTimeout(function () {
      announce(button, "");
      button.copyTimer = 0;
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

  // No clipboard access (an old browser, or the page is not a secure context):
  // select the link text so the visitor can copy it with the keyboard or menu.
  function fallback(button, target) {
    if (selectText(target)) {
      flash(button, "Link selected. Press Control C or use the menu to copy.");
    }
  }

  function copyFrom(button) {
    var holder = box(button);
    var target = holder ? holder.querySelector("[data-copy-link-url]") : null;
    if (!target) {
      return;
    }
    var text = target.textContent;
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(text).then(
        function () {
          flash(button, "Copied");
        },
        function () {
          fallback(button, target);
        }
      );
    } else {
      fallback(button, target);
    }
  }

  function start() {
    var buttons = document.querySelectorAll("button[data-copy-link]");
    if (!buttons.length) {
      return;
    }
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener("click", function (event) {
        copyFrom(event.currentTarget);
      });
    }
    // The button stays hidden by the stylesheet until this mark is set, so a
    // visitor without script never sees a control that does nothing.
    document.documentElement.classList.add("copy-link-ready");
  }

  start();
})();
