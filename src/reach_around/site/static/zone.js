// SPDX-License-Identifier: Apache-2.0
// Display-zone choice for the media kit. Dates on the page are UTC days and stay
// that way. This script only fills the empty note spans that carry a UTC instant
// in data-zone-start with when that UTC day begins in the zone the viewer picked.
//
// The zone comes from the select control or from the address fragment (#tz=Name).
// Only a name that is one of the select's own options is used. Nothing is stored
// anywhere, no request is made, and the text is written with textContent only.
// With JavaScript off the control stays hidden and the UTC text is all there is.
(function () {
  "use strict";

  var select = document.querySelector("[data-zone-select]");
  var wrap = document.querySelector("[data-zone-control]");
  if (!select || !wrap || typeof Intl === "undefined" || !Intl.DateTimeFormat) {
    return;
  }

  function known(name) {
    for (var i = 0; i < select.options.length; i++) {
      if (select.options[i].value === name) {
        return true;
      }
    }
    return false;
  }

  function fromFragment() {
    var match = /^#tz=([A-Za-z0-9_\/+-]{1,40})$/.exec(window.location.hash);
    if (!match) {
      return "UTC";
    }
    var name;
    try {
      name = decodeURIComponent(match[1]);
    } catch (e) {
      return "UTC";
    }
    return known(name) ? name : "UTC";
  }

  function apply(zone) {
    var notes = document.querySelectorAll("[data-zone-start]");
    var format = null;
    if (zone !== "UTC") {
      try {
        format = new Intl.DateTimeFormat("en-US", {
          timeZone: zone,
          year: "numeric",
          month: "short",
          day: "numeric",
          hour: "numeric",
          minute: "2-digit",
          timeZoneName: "short"
        });
      } catch (e) {
        format = null;
      }
    }
    for (var i = 0; i < notes.length; i++) {
      var when = new Date(notes[i].getAttribute("data-zone-start"));
      if (format === null || isNaN(when.getTime())) {
        notes[i].textContent = "";
      } else {
        notes[i].textContent = " (that UTC day begins " + format.format(when) + ")";
      }
    }
  }

  var start = fromFragment();
  select.value = start;
  apply(start);
  wrap.hidden = false;

  select.addEventListener("change", function () {
    var zone = known(select.value) ? select.value : "UTC";
    if (window.history && window.history.replaceState) {
      window.history.replaceState(
        null,
        "",
        zone === "UTC" ? window.location.pathname + window.location.search : "#tz=" + zone
      );
    }
    apply(zone);
  });
})();
