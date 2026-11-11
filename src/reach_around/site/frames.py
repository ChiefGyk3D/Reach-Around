# SPDX-License-Identifier: Apache-2.0
"""Border frames: which component gets which level, and the build-time checks.

``theme.frame`` is the master switch and the strongest level the build may use:

* ``none`` (the default): no frame markup, no frame stylesheet, no frame art.
* ``minimal``: the light frame. Stepped graphite brackets split at the middle of
  each edge, thin amber rules and small dashes.
* ``faithful``: the brand button art, traced from the owner's reference: stepped
  metallic brackets, end-cap plates with amber dashes and double rules. One
  static SVG per size class, so it is never stretched far from its drawn size.

The old names stay as aliases: ``l1`` is ``minimal``; ``l2`` and ``l3`` are
``faithful`` (the glow and the light show of ``l3`` are gone: the art is static).

Each element class (a "slot") has a default level, so a page is never fully
framed: faithful on the featured link and the big primary buttons, minimal on
tabs, mid-size links, platform links and section headers, none on small icons,
chips, the footer, list rows and the rows inside sections. ``theme.frame_classes``
overrides one slot. Nothing here paints a fill: a frame is a border only.

Every element carries one class string from the ``_frames.html`` macro, so
markup does not grow with the number of frames. Nothing here writes a ``style``
attribute or touches the user-supplied CSS sanitizer. No frame sits inside
another frame (the build checks).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from html.parser import HTMLParser
from typing import Final

FRAME_LEVELS: Final[tuple[str, ...]] = ("none", "minimal", "faithful")
# Old names, kept so an existing theme.frame keeps working.
FRAME_ALIASES: Final[dict[str, str]] = {
    "l1": "minimal",
    "l2": "faithful",
    "l3": "faithful",
}

# Default level per slot (an element class) when theme.frame is faithful.
DEFAULT_LEVELS: Final[dict[str, str]] = {
    "featured": "faithful",
    "cta": "faithful",
    "hero": "minimal",
    "link": "minimal",
    "platform": "minimal",
    "head": "minimal",
    "tab": "minimal",
    "tile": "none",
    "stat": "none",
    "card": "none",
    "ctl": "none",
    "chip": "none",
    "field": "none",
    "foot": "none",
    "bar": "none",
}
SLOTS: Final[tuple[str, ...]] = tuple(DEFAULT_LEVELS)

# Slot -> size class of the minimal frame: the artwork scale in pixels per
# original pixel of the 318 px tall frame (see static/frame.css).
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
    "foot": "s2",
    "bar": "s2",
    "cta": "s4",
    "hero": "s5",
}
# Slot -> size class of the faithful art (static/frame-fa-<size>.svg, drawn at
# that size; scripts/gen-frame-art.py lists the sizes).
SLOT_ART: Final[dict[str, str]] = {
    "link": "xl",
    "featured": "xl",
    "platform": "xl",
    "tile": "xs",
    "head": "wd",
    "stat": "wd",
    "card": "card",
    "ctl": "xs",
    "chip": "xs",
    "tab": "md",
    "field": "wd",
    "foot": "wd",
    "bar": "wd",
    "cta": "xs",
    "hero": "card",
}
# Slots with no frame fill of their own (the tab bar is already an amber bar).
BARE_SLOTS: Final[frozenset[str]] = frozenset({"tab"})
# Slots that sit on a solid amber fill: their faithful art uses ink rules.
AMBER_SLOTS: Final[frozenset[str]] = frozenset({"featured", "cta", "tab"})


def normalize_level(level: str) -> str:
    """The canonical level for a configured one (``l1`` to ``l3`` are aliases)."""
    return FRAME_ALIASES.get(level, level)


def frame_plan(
    level: str, overrides: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The level each framed component slot gets for ``theme.frame`` = ``level``.

    Slots left out are not framed. ``minimal`` caps the defaults at minimal;
    ``overrides`` (``theme.frame_classes``) then set a slot to any level.
    """
    level = normalize_level(level)
    if level == "none":
        return {}
    plan: dict[str, str] = {}
    for slot, default in DEFAULT_LEVELS.items():
        chosen = default
        if level == "minimal" and chosen == "faithful":
            chosen = "minimal"
        if overrides and overrides.get(slot) is not None:
            chosen = normalize_level(str(overrides[slot]))
        if chosen != "none":
            plan[slot] = chosen
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


def check_frames(pages: Mapping[str, str]) -> None:
    """Refuse a page with a frame inside a frame.

    ``pages`` maps a page path to its finished HTML. The template macros never
    nest, but an override template can write the classes itself; the build stops
    with the page named rather than ship a page with nested frames.
    """
    for path, html in pages.items():
        parser = _Nest()
        parser.feed(html)
        if parser.nested is not None:
            raise ValueError(
                f"{path}: a border frame sits inside another border frame "
                f"(a <{parser.nested}>); frames never nest"
            )
