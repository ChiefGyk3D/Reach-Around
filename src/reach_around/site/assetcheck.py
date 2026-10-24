# SPDX-License-Identifier: Apache-2.0
"""Check that an instance file is what its extension says, and screen SVG.

The extension allowlist alone lets a renamed ``secret.yaml`` through as
``secret.png``, so ``check_content`` compares the first bytes with the format's
signature. SVG is XML that browsers run as a document when it is opened
directly, so it gets a stricter screen: no DOCTYPE or ENTITY (which defuses
entity expansion before any parser runs), no script, no event handlers, no
embedded foreign content, and no reference that leaves the document.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from xml.parsers import expat

_AVIF_BRANDS = (b"avif", b"avis")

_IEND = b"\x00\x00\x00\x00IEND\xaeB`\x82"


def _sfnt(d: bytes, tags: tuple[bytes, ...]) -> bool:
    """A font container with a plausible table directory."""
    if len(d) < 12 or d[:4] not in tags:
        return False
    tables = int.from_bytes(d[4:6], "big")
    return 1 <= tables <= 64 and len(d) >= 12 + 16 * tables


def _woff(d: bytes, tag: bytes) -> bool:
    return len(d) >= 12 and d[:4] == tag and int.from_bytes(d[8:12], "big") == len(d)


# Structural sanity checks, not decoders: the signature plus the cheap
# end-of-file or length facts that a text file renamed to an image lacks.
_MAGIC: dict[str, Callable[[bytes], bool]] = {
    "png": lambda d: d.startswith(b"\x89PNG\r\n\x1a\n") and d.endswith(_IEND),
    "jpg": lambda d: d.startswith(b"\xff\xd8\xff") and d.endswith(b"\xff\xd9"),
    "jpeg": lambda d: d.startswith(b"\xff\xd8\xff") and d.endswith(b"\xff\xd9"),
    "gif": lambda d: d.startswith((b"GIF87a", b"GIF89a")) and d.endswith(b";"),
    "webp": lambda d: (
        len(d) >= 12
        and d[:4] == b"RIFF"
        and d[8:12] == b"WEBP"
        and int.from_bytes(d[4:8], "little") + 8 == len(d)
    ),
    "avif": lambda d: d[4:8] == b"ftyp" and d[8:12] in _AVIF_BRANDS,
    "woff2": lambda d: _woff(d, b"wOF2"),
    "woff": lambda d: _woff(d, b"wOFF"),
    "ttf": lambda d: _sfnt(d, (b"\x00\x01\x00\x00", b"true")),
    "otf": lambda d: _sfnt(d, (b"OTTO",)),
}

_FORBIDDEN_ELEMENTS = {
    "script": "a script element",
    "foreignobject": "a foreignObject element",
    "iframe": "an iframe element",
    "embed": "an embed element",
    "object": "an object element",
    "audio": "an audio element",
    "video": "a video element",
}
_ANIMATION = {"animate", "set", "animatetransform", "animatemotion"}


def _local(name: str) -> str:
    return name.rsplit("}", 1)[-1].rsplit(":", 1)[-1].lower()


def _screen_css(css: str) -> str | None:
    """Return the reason ``css`` is refused, or None.

    Comments are refused outright rather than stripped, so no parser can read
    the text differently from this check.
    """
    if "/*" in css:
        return "a CSS comment is not allowed"
    if "\\" in css:
        return "CSS escapes are not allowed"
    text = css.lower()
    if "@import" in text:
        return "@import is not allowed"
    if "expression(" in text:
        return "expression( is not allowed"
    if "image-set(" in text:
        return "image-set( is not allowed"
    for match in re.finditer(r"url\(", text):
        if not text[match.end() :].lstrip().startswith("#"):
            return "url( may only point at a #fragment, without quotes"
    return None


class _Refused(Exception):
    """Raised from a parser callback to stop at the first problem."""


class _Screen:
    """Receives SVG parse events and refuses anything that can run or leave."""

    def __init__(self) -> None:
        self.depth = 0
        self.style: list[str] | None = None

    def start(self, tag: str, attrs: dict[str, str]) -> None:
        name = _local(tag)
        if self.style is not None:
            raise _Refused("an element inside a style element is not allowed")
        if self.depth == 0 and name != "svg":
            raise _Refused("the root element must be svg")
        self.depth += 1
        if name in _FORBIDDEN_ELEMENTS:
            raise _Refused(f"{_FORBIDDEN_ELEMENTS[name]} is not allowed")
        if name == "style":
            self.style = []
        for attr, value in attrs.items():
            attr_name = _local(attr)
            if attr_name.startswith("on"):
                raise _Refused(
                    f"an event handler attribute ({attr_name}) is not allowed"
                )
            if attr_name == "href" and not value.strip().startswith("#"):
                raise _Refused("an href may only point at a #fragment in this file")
            if (
                name in _ANIMATION
                and attr_name == "attributename"
                and _local(value) == "href"
            ):
                raise _Refused("an animation element targeting href is not allowed")
            if attr_name == "base":
                raise _Refused("an xml:base attribute is not allowed")
            reason = _screen_css(value)
            if reason:
                raise _Refused(reason)

    def pi(self, target: str, data: str) -> None:
        raise _Refused("a processing instruction is not allowed")

    def comment(self, data: str) -> None:
        if self.style is not None:
            raise _Refused("a comment inside a style element is not allowed")

    def cdata(self) -> None:
        raise _Refused("a CDATA section is not allowed")

    def text(self, data: str) -> None:
        if self.style is not None:
            self.style.append(data)

    def end(self, tag: str) -> None:
        if _local(tag) == "style" and self.style is not None:
            reason = _screen_css("".join(self.style))
            self.style = None
            if reason:
                raise _Refused(reason)


def _screen_svg(data: bytes) -> str | None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "the file must be UTF-8 text"
    if "\x00" in text or text.startswith("\ufeff"):
        return "the file must be plain UTF-8 text"
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        return "a DOCTYPE or ENTITY declaration is not allowed"
    screen = _Screen()
    parser = expat.ParserCreate("utf-8", namespace_separator="}")
    parser.StartElementHandler = screen.start
    parser.CharacterDataHandler = screen.text
    parser.EndElementHandler = screen.end
    parser.ProcessingInstructionHandler = screen.pi
    parser.CommentHandler = screen.comment
    parser.StartCdataSectionHandler = screen.cdata
    try:
        parser.Parse(data, True)
    except _Refused as refused:
        return str(refused)
    except expat.ExpatError:
        return "it is not valid XML"
    return None


def check_content(key: str, rel: str, data: bytes) -> None:
    """Raise ``ValueError`` naming ``key`` if ``data`` is not a ``rel`` file."""
    ext = rel.lower().rsplit(".", 1)[-1]
    if ext == "svg":
        reason = _screen_svg(data)
        if reason:
            raise ValueError(f"{key}: {rel!r} is refused as an svg: {reason}")
        return
    matches = _MAGIC.get(ext)
    if matches is None or not matches(data):
        raise ValueError(
            f"{key}: {rel!r} is not a real {ext} file (its first bytes do not "
            "match the format)"
        )
