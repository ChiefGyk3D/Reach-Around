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

_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"
_XML_NS = "http://www.w3.org/XML/1998/namespace"

# The allowlist is the extension point: to support another SVG feature, add
# its element or attribute name here and add a test for it. Anything not listed
# is refused, so new browser features are refused until someone decides.
ALLOWED_ELEMENTS = frozenset(
    """
    svg g defs symbol use path rect circle ellipse line polyline polygon text
    tspan title desc linearGradient radialGradient stop clipPath mask filter
    feGaussianBlur feOffset feBlend feColorMatrix feFlood feComposite feMerge
    feMergeNode feDropShadow metadata
    """.split()
)
ALLOWED_ATTRIBUTES = frozenset(
    """
    id class transform viewBox preserveAspectRatio width height x y x1 x2 y1 y2
    cx cy r rx ry d points fill fill-opacity fill-rule stroke stroke-width
    stroke-opacity stroke-linecap stroke-linejoin stroke-miterlimit
    stroke-dasharray stroke-dashoffset opacity clip-path clip-rule mask filter
    offset stop-color stop-opacity gradientUnits gradientTransform
    spreadMethod fx fy dx dy stdDeviation in in2 result mode type values
    flood-color flood-opacity operator k1 k2 k3 k4 font-family font-size
    font-weight font-style text-anchor dominant-baseline letter-spacing
    version href maskUnits clipPathUnits filterUnits primitiveUnits rotate
    textLength lengthAdjust
    """.split()
)
# Namespaced attributes allowed, by (namespace, local name).
_ALLOWED_NS_ATTRIBUTES = frozenset({(_XLINK_NS, "href"), (_XML_NS, "space")})
_ALLOWED_NAMESPACES = frozenset({_SVG_NS, _XLINK_NS})
_REFUSED_IN_VALUES = ("javascript:", "data:", "expression(", "@import")
_URL = re.compile(r"url\(\s*#[A-Za-z_][\w.:-]*\s*\)")


def _split(name: str) -> tuple[str, str]:
    namespace, sep, local = name.rpartition("}")
    return (namespace, local) if sep else ("", name)


def _screen_value(value: str) -> str | None:
    """Return why an attribute value is refused, or None."""
    if "\\" in value:
        return "it contains a backslash"
    if "/*" in value:
        return "it contains a comment opener"
    if any(ord(ch) < 32 and ch not in "\t\n\r" or ord(ch) == 127 for ch in value):
        return "it contains a control character"
    lowered = value.lower()
    for needle in _REFUSED_IN_VALUES:
        if needle in lowered:
            return f"it contains {needle!r}"
    for match in re.finditer(r"url\(", lowered):
        if not _URL.match(value, match.start()):
            return "url( may only wrap a #fragment id, such as url(#a)"
    return None


class _Refused(Exception):
    """Raised from a parser callback to stop at the first problem."""


class _Screen:
    """Receives SVG parse events and refuses everything not on the allowlist."""

    def __init__(self) -> None:
        self.depth = 0

    def start(self, tag: str, attrs: dict[str, str]) -> None:
        namespace, name = _split(tag)
        if namespace != _SVG_NS or name not in ALLOWED_ELEMENTS:
            raise _Refused(f"element <{name}> is not allowed")
        if self.depth == 0 and name != "svg":
            raise _Refused("the root element must be <svg>")
        self.depth += 1
        for attr, value in attrs.items():
            attr_ns, attr_name = _split(attr)
            if attr_ns:
                if (attr_ns, attr_name) not in _ALLOWED_NS_ATTRIBUTES:
                    raise _Refused(
                        f"attribute {attr_name!r} on <{name}> is not allowed "
                        "(unlisted namespace)"
                        if attr_ns not in (_XML_NS, _XLINK_NS)
                        else f"attribute {attr_name!r} on <{name}> is not allowed"
                    )
            elif attr_name not in ALLOWED_ATTRIBUTES:
                raise _Refused(f"attribute {attr_name!r} on <{name}> is not allowed")
            if attr_name == "href" and not value.startswith("#"):
                raise _Refused(
                    f"href on <{name}> must point at a #fragment in this file"
                )
            reason = _screen_value(value)
            if reason:
                raise _Refused(
                    f"the value of {attr_name!r} on <{name}> is not allowed: {reason}"
                )

    def end(self, tag: str) -> None:
        self.depth -= 1

    def namespace(self, prefix: str | None, uri: str) -> None:
        if uri not in _ALLOWED_NAMESPACES:
            raise _Refused(f"the namespace {uri[:60]!r} is not allowed")

    def pi(self, target: str, data: str) -> None:
        raise _Refused("a processing instruction is not allowed")

    def cdata(self) -> None:
        raise _Refused("a CDATA section is not allowed")


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
    parser.EndElementHandler = screen.end
    parser.StartNamespaceDeclHandler = screen.namespace
    parser.ProcessingInstructionHandler = screen.pi
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
