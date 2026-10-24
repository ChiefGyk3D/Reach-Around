// SPDX-License-Identifier: Apache-2.0
// Copy button for the crypto block. The address is public text already on the
// page: this file reads it from the <code> element the button points at, hands
// it to the clipboard and announces the result in an aria-live region. It makes
// no network request, stores nothing and builds no HTML.
(function () {
  "use strict";

  var RESET_MS = 2000;
  var BLOCK = "block-crypto";

  function findBlock(node) {
    while (node && node.nodeType === 1 && !node.classList.contains(BLOCK)) {
      node = node.parentNode;
    }
    return node && node.nodeType === 1 ? node : null;
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

  function announce(button, message) {
    var block = findBlock(button);
    var status = block ? block.querySelector("[data-copy-status]") : null;
    if (status) {
      status.textContent = message;
    }
  }

  function flash(button, buttonText, message) {
    var original = button.getAttribute("data-copy-label") || "Copy";
    button.textContent = buttonText;
    announce(button, message);
    var pending = button.copyTimer;
    if (pending) {
      window.clearTimeout(pending);
    }
    button.copyTimer = window.setTimeout(function () {
      button.textContent = original;
      announce(button, "");
      button.copyTimer = 0;
    }, RESET_MS);
  }

  function copyFrom(button) {
    var target = document.getElementById(button.getAttribute("data-copy-target"));
    if (!target) {
      return;
    }
    var text = target.textContent;
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(text).then(
        function () {
          flash(button, "Copied", "Copied");
        },
        function () {
          fallback(button, target);
        }
      );
    } else {
      fallback(button, target);
    }
  }

  // No clipboard access (an old browser, or the page is not a secure context):
  // select the address so the visitor can copy it with the keyboard or menu.
  function fallback(button, target) {
    if (selectText(target)) {
      flash(button, "Selected", "Address selected. Press Control C or use the menu to copy.");
    }
  }

  function start() {
    var buttons = document.querySelectorAll("button[data-copy-target]");
    if (!buttons.length) {
      return;
    }
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener("click", function (event) {
        copyFrom(event.currentTarget);
      });
    }
    // The buttons stay hidden by the stylesheet until this mark is set, so a
    // visitor without script never sees a control that does nothing.
    document.documentElement.classList.add("copy-ready");
  }

  start();
})();
