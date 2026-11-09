# SPDX-License-Identifier: Apache-2.0
"""Border frames: which component gets which level, and the build-time checks.

``theme.frame`` names the strongest level the build may use. One frame, drawn
from the brand's button, in three amounts:

* L1, the lite frame: heavy stepped brackets split at the middle of each edge,
  thin amber rules, small amber dashes and stubs.
* L2, the full frame: L1 plus the angled end-cap plates and their dashes.
* L3, the full frame plus the center rules and a faint glow under the bottom
  rule. At most ``theme.frame_l3_max`` per page (default 1).

Every element carries one class string from the ``_frames.html`` macro; the
artwork is two packaged SVG files used as a nine-slice border image by the
packaged ``static/frame.css``, so markup does not grow with the number of
frames. Nothing here writes a ``style`` attribute or touches the user-supplied
CSS sanitizer. No frame sits inside another frame (the build checks).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from html.parser import HTMLParser
from typing import Final

FRAME_LEVELS: Final[tuple[str, ...]] = ("none", "l1", "l2", "l3")

# Component slot -> size class: the artwork scale in pixels per original pixel
# of the 318 px tall frame (see static/frame.css). The element is at least
# 318 * scale tall, so a bracket bar is always about 6 percent of its height.
SLOT_SIZES: Final[dict[str, str]] = {
    "link": "s4",
    "featured": "s4",
    "platform": "s4",
    "tile": "s2",
    "head": "s3",
    "stat": "s4",
    "card": "s4",
    "ctl": "s2",
    "chip": "s1",
    "tab": "s2",
    "field": "s2",
    "foot": "s3",
    "cta": "s4",
    "hero": "s5",
}
# Slots whose frame is lite at every level, because they are small or tall.
LITE_SLOTS: Final[frozenset[str]] = frozenset(
    {"tile", "ctl", "chip", "tab", "field", "card", "foot"}
)
# Slots with no cream panel of their own (the tab bar is already an amber bar).
BARE_SLOTS: Final[frozenset[str]] = frozenset({"tab"})
# Slots that ask for the strongest frame the level allows.
L3_SLOTS: Final[frozenset[str]] = frozenset({"featured", "hero", "cta"})


def frame_plan(level: str) -> dict[str, str]:
    """The level each component slot gets when ``theme.frame`` is ``level``."""
    if level == "none":
        return {}
    plan: dict[str, str] = {}
    for slot in SLOT_SIZES:
        if level == "l1" or slot in LITE_SLOTS:
            plan[slot] = "l1"
        elif slot in L3_SLOTS and level == "l3":
            plan[slot] = "l3"
        else:
            plan[slot] = "l2"
    return plan


_CLASS_ATTR = re.compile(r'\bclass="([^"]*)"')
_VOID: Final = frozenset(
    "area base br col embed hr img input link meta source track wbr".split()
)


class _Nest(HTMLParser):
    """Finds a framed element inside a framed element."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[tuple[str, bool]] = []
        self.nested: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = dict(attrs).get("class") or ""
        framed = "ra-bx" in classes.split()
        if framed and any(flag for _, flag in self.stack) and self.nested is None:
            self.nested = tag
        if tag not in _VOID:
            self.stack.append((tag, framed))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return


def check_frames(pages: Mapping[str, str], l3_max: int = 1) -> None:
    """Refuse a page with too many L3 frames or a frame inside a frame.

    ``pages`` maps a page path to its finished HTML. The template macros keep
    to ``l3_max`` for the packaged pages, but an override template can write
    the classes itself; the build stops with the page named rather than ship
    a page with more heavy frames than the owner allowed.
    """
    for path, html in pages.items():
        heavy = sum(
            1
            for match in _CLASS_ATTR.finditer(html)
            if "ra-l3" in match.group(1).split()
        )
        if heavy > l3_max:
            raise ValueError(
                f"{path}: {heavy} L3 border frames on one page; at most "
                f"{l3_max} is allowed (raise theme.frame_l3_max, or use L2 or "
                "L1 for the others)"
            )
        parser = _Nest()
        parser.feed(html)
        if parser.nested is not None:
            raise ValueError(
                f"{path}: a border frame sits inside another border frame "
                f"(a <{parser.nested}>); frames never nest"
            )
