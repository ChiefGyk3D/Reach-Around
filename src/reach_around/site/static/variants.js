/* SPDX-License-Identifier: Apache-2.0 */
(() => {
  "use strict";
  for (const list of document.querySelectorAll("[role=tablist]")) {
    const tabs = [...list.querySelectorAll("[role=tab]")];
    const panels = tabs.map((tab) => document.getElementById(tab.getAttribute("aria-controls")));
    const select = (index, focus) => {
      tabs.forEach((tab, at) => {
        const active = at === index;
        tab.setAttribute("aria-selected", String(active));
        tab.tabIndex = active ? 0 : -1;
        if (panels[at]) panels[at].hidden = !active;
      });
      if (focus) tabs[index].focus();
    };
    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => select(index, false));
      tab.addEventListener("keydown", (event) => {
        let next = index;
        if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
        else if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = tabs.length - 1;
        else return;
        event.preventDefault();
        select(next, true);
      });
    });
    list.hidden = false;
    select(0, false);
  }
})();
