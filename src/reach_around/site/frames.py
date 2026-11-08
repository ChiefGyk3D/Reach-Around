# SPDX-License-Identifier: Apache-2.0
"""Border frames: which component gets which level, and the build-time checks.

``theme.frame`` names the strongest level the build may use. One frame system
has three amounts of itself:

* L1, corner ticks: pure CSS, no markup beyond a class.
* L2, the button frame simplified: four chamfered corner brackets split at the
  middle of each edge, two thin rules, no glow. One small inline SVG.
* L3, the full button frame: stepped brackets, end-cap plates, center rules and
  a faint glow under the bottom rule. Never more than one on a page.

The markup comes from one template macro file (``_frames.html``); the CSS is the
packaged ``static/frame.css``, linked only when a frame level is on. Nothing here
writes a ``style`` attribute or touches the user-supplied CSS sanitizer.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from typing import Final

FRAME_LEVELS: Final[tuple[str, ...]] = ("none", "l1", "l2", "l3")

# Component slot -> frame size class (the scale of the corner artwork).
SLOT_SIZES: Final[dict[str, str]] = {
    "link": "sm",
    "featured": "sm",
    "platform": "sm",
    "stat": "md",
    "hero": "lg",
}
SIZE_SCALES: Final[dict[str, float]] = {"sm": 0.18, "md": 0.2, "lg": 0.24}

_L1: Final = {"link": "l1", "platform": "l1", "stat": "l1"}
_PLANS: Final[dict[str, dict[str, str]]] = {
    "none": {},
    "l1": _L1,
    "l2": {**_L1, "featured": "l2"},
    "l3": {**_L1, "featured": "l2", "hero": "l3"},
}

MAX_L3_PER_PAGE = 1

_CLASS_ATTR = re.compile(r'\bclass="([^"]*)"')
_ID_ATTR = re.compile(r'\sid="([^"]*)"')


def frame_plan(level: str) -> dict[str, str]:
    """The level each component slot gets when ``theme.frame`` is ``level``."""
    return dict(_PLANS[level])


def check_frames(pages: Mapping[str, str]) -> None:
    """Refuse a page with two L3 frames or a repeated ``id``.

    ``pages`` maps a page path to its finished HTML. The template macros emit
    one L3 frame at most for the packaged pages, but an override template can
    call them again; the build stops with the page named rather than ship a
    page with a second heavy frame or two elements sharing a gradient id.
    """
    for path, html in pages.items():
        heavy = sum(
            1
            for match in _CLASS_ATTR.finditer(html)
            if "ra-l3" in match.group(1).split()
        )
        if heavy > MAX_L3_PER_PAGE:
            raise ValueError(
                f"{path}: {heavy} L3 border frames on one page; at most "
                f"{MAX_L3_PER_PAGE} is allowed (use L2 or L1 for the others)"
            )
        repeated = sorted(
            ident
            for ident, count in Counter(_ID_ATTR.findall(html)).items()
            if count > 1 and ident.startswith("ra-")
        )
        if repeated:
            raise ValueError(
                f"{path}: border frame id {repeated[0]!r} appears more than once"
            )
