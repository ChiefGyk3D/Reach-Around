# SPDX-License-Identifier: Apache-2.0
"""Validate theme values and render them as ``static/theme.css``.

The theme is the one place where config text ends up inside a stylesheet, so
this module is a CSS injection boundary. Three layers keep it safe:

1. ``check_css_value`` refuses the characters and substrings that let a value
   break out of a declaration (``;``, braces, ``url(``, comments, escapes...),
   each with its own plain message.
2. Every value must then match a strict allowlist for its kind (a color, a
   length, a font stack, a font file, a background URL).
3. ``theme_css`` writes only property names from ``ALLOWED_PROPERTIES``.

Fonts are system stacks or files that ship with the site. A font on a third
party host is refused so a visitor's browser never contacts one.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reach_around.config.theme import Theme

COLOR_KEYS: tuple[str, ...] = (
    "bg",
    "text",
    "accent",
    "on_accent",
    "surface",
    "border",
)

# Folders the build writes itself; an instance file may not live inside them.
RESERVED_FOLDERS: frozenset[str] = frozenset({"static", "privacy"})

DEFAULT_FONT_BODY = "system-ui, sans-serif"
DEFAULT_COLUMN_WIDTH = "35rem"
MAX_BACKGROUND_DIM = 0.9

ALLOWED_PROPERTIES: frozenset[str] = frozenset(
    {f"--ra-color-{key.replace('_', '-')}" for key in COLOR_KEYS}
    | {
        "--ra-mascot-image",
        "--ra-bg-dim",
        "--ra-font-body",
        "--ra-font-heading",
        "--ra-bg-image",
        "--ra-column-width",
        "--ra-radius",
        "--ra-button-radius",
        "color-scheme",
        # Only inside the @font-face blocks written for local font files.
        "font-family",
        "src",
        "font-display",
    }
)

_MAX_VALUE = 2048

# (substring, name used in the message), checked in this order.
_REFUSED: tuple[tuple[str, str], ...] = (
    (";", "';'"),
    ("{", "'{'"),
    ("}", "'}'"),
    ("\\", "a backslash escape"),
    ("/*", "a comment"),
    ("*/", "a comment"),
    ("url(", "'url('"),
    ("expression(", "'expression('"),
    ("@import", "'@import'"),
)

_NAMED_COLORS = frozenset(
    """
    aliceblue antiquewhite aqua aquamarine azure beige bisque black
    blanchedalmond blue blueviolet brown burlywood cadetblue chartreuse
    chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan
    darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta
    darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen
    darkslateblue darkslategray darkslategrey darkturquoise darkviolet
    deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite
    forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green
    greenyellow grey honeydew hotpink indianred indigo ivory khaki lavender
    lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan
    lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon
    lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue
    lightyellow lime limegreen linen magenta maroon mediumaquamarine
    mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue
    mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream
    mistyrose moccasin navajowhite navy oldlace olive olivedrab orange
    orangered orchid palegoldenrod palegreen paleturquoise palevioletred
    papayawhip peachpuff peru pink plum powderblue purple rebeccapurple red
    rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell
    sienna silver skyblue slateblue slategray slategrey snow springgreen
    steelblue tan teal thistle tomato turquoise violet wheat white
    whitesmoke yellow yellowgreen transparent currentcolor
    """.split()
)

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_COLOR_ARG = r"(?:[0-9.+-]{1,8}(?:%|deg|grad|rad|turn)?|none)"
_COLOR_FUNCTION = re.compile(
    rf"^(?:rgb|hsl)a?\(\s*{_COLOR_ARG}(?:\s*[,/\s]\s*{_COLOR_ARG}){{2,3}}\s*\)$"
)
_LENGTH = re.compile(r"^(?:0|\d{1,4}(?:\.\d{1,3})?(?:px|rem|em|ch|%|vw))$")

_GENERIC_FONTS = frozenset(
    """
    serif sans-serif monospace cursive fantasy system-ui ui-serif
    ui-sans-serif ui-monospace ui-rounded emoji math fangsong
    """.split()
)
_FONT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9 -]{0,39}$")
_FONT_EXTENSIONS = (".woff2", ".woff", ".ttf", ".otf")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".svg")
_ASSET_PATH = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$"
)
_HTTPS_URL = re.compile(
    r"^https://[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::\d{1,5})?"
    r"(?:/[A-Za-z0-9._~%/+=&?@,-]*)?$"
)


def check_css_value(value: str) -> str:
    """Return ``value`` unless it could break out of a CSS declaration."""
    if len(value) > _MAX_VALUE:
        raise ValueError(f"must be at most {_MAX_VALUE} characters")
    if any(ch in "\r\n\f" or not ch.isprintable() for ch in value):
        raise ValueError("must not contain a newline or control character")
    lowered = value.lower()
    for needle, name in _REFUSED:
        if needle in lowered:
            raise ValueError(f"must not contain {name}")
    return value


def check_color(value: str) -> str:
    """Return ``value`` if it is a hex, rgb(), hsl() or named color."""
    check_css_value(value)
    if _HEX.match(value) or _COLOR_FUNCTION.match(value):
        return value
    if value.lower() in _NAMED_COLORS:
        return value
    raise ValueError(
        f"must be a hex color, rgb(), hsl() or a named color, not {value[:40]!r}"
    )


def check_length(value: str) -> str:
    """Return ``value`` if it is a plain length such as ``560px`` or ``35rem``."""
    check_css_value(value)
    if not _LENGTH.match(value):
        raise ValueError(
            "must be a length such as 560px, 35rem, 40em, 60ch, 90% or 100vw, "
            f"not {value[:40]!r}"
        )
    return value


def _check_asset_path(value: str, extensions: tuple[str, ...], what: str) -> str:
    """A relative path inside the instance: no ``..``, no root, no scheme."""
    if not _ASSET_PATH.match(value) or not value.lower().endswith(extensions):
        raise ValueError(
            f"must be a {what} path inside the instance directory "
            f"ending in {', '.join(extensions)}, not {value[:40]!r}"
        )
    return value


def _is_remote(value: str) -> bool:
    return "://" in value or value.startswith("//")


def _split_font_stack(value: str) -> list[str]:
    families = []
    for raw in value.split(","):
        name = raw.strip()
        if len(name) >= 2 and name[0] == name[-1] and name[0] in "'\"":
            name = name[1:-1]
        if not (name in _GENERIC_FONTS or _FONT_NAME.match(name)):
            raise ValueError(
                f"font family {raw.strip()[:40]!r} must be letters, digits, "
                "spaces or hyphens"
            )
        families.append(name)
    if len(families) > 8:
        raise ValueError("a font stack may list at most 8 families")
    return families


def _font_kind(value: str) -> str:
    return (
        "file" if "/" in value or value.lower().endswith(_FONT_EXTENSIONS) else "stack"
    )


def check_font(value: str) -> str:
    """Return ``value`` if it is a system font stack or a local font file."""
    check_css_value(value)
    if _is_remote(value):
        raise ValueError(
            "third-party font hosts are refused so no visitor data leaks; "
            "use a system font stack or a font file in the instance directory"
        )
    if _font_kind(value) == "file":
        _check_asset_path(value, _FONT_EXTENSIONS, "font file")
    else:
        _split_font_stack(value)
    return value


def check_image_path(value: str) -> str:
    """Return ``value`` if it is a relative image path inside the instance.

    This is the check for the slots that must stay same-origin (the mascot and
    the favicon): no scheme, no ``//``, no ``..``, no absolute path, and not in
    a folder the build writes itself.
    """
    check_css_value(value)
    _check_asset_path(value, IMAGE_EXTENSIONS, "image")
    if value.split("/", 1)[0] in RESERVED_FOLDERS:
        raise ValueError(
            "must not be inside a folder the build writes itself "
            f"({', '.join(sorted(RESERVED_FOLDERS))}), not {value[:40]!r}"
        )
    return value


def check_background(value: str) -> str:
    """Return ``value`` if it is an https URL or a relative image path."""
    check_css_value(value)
    if value.startswith("https://"):
        if not _HTTPS_URL.match(value):
            raise ValueError("background URL has characters that are not allowed")
        return value
    if _is_remote(value) or ":" in value:
        raise ValueError("background must be an https URL or a relative asset path")
    return check_image_path(value)


def _family_list(value: str) -> str:
    return ", ".join(
        name if name in _GENERIC_FONTS else f'"{name}"'
        for name in _split_font_stack(value)
    )


def _css_url(value: str) -> str:
    # theme.css lives in static/, so a path relative to the site root is one up.
    return f'url("{value if value.startswith("https://") else "../" + value}")'


def theme_css(theme: Theme) -> str:
    """Return ``theme.css``: only values that differ from the stylesheet defaults.

    Values come from a validated ``Theme``; they are checked again here so a
    model built with ``model_construct`` cannot smuggle anything through.
    """
    faces: list[str] = []
    declarations: dict[str, str] = {}

    for key in COLOR_KEYS:
        if key in theme.colors:
            name = key.replace("_", "-")
            declarations[f"--ra-color-{name}"] = check_color(theme.colors[key])

    for slot, value in (("body", theme.fonts.body), ("heading", theme.fonts.heading)):
        if value is None:
            continue
        check_font(value)
        if _font_kind(value) == "file":
            family = f"ra-{slot}"
            faces.append(
                "@font-face {\n"
                f'  font-family: "{family}";\n'
                f'  src: url("../{value}");\n'
                "  font-display: swap;\n"
                "}\n"
            )
            fallback = DEFAULT_FONT_BODY if slot == "body" else "inherit"
            declarations[f"--ra-font-{slot}"] = f'"{family}", {fallback}'
        else:
            declarations[f"--ra-font-{slot}"] = _family_list(value)

    if theme.background_dim != 0.0:
        dim = theme.background_dim
        if not 0.0 < dim <= MAX_BACKGROUND_DIM:
            raise ValueError(
                f"background_dim must be between 0 and {MAX_BACKGROUND_DIM}, "
                f"not {dim!r}"
            )
        declarations["--ra-bg-dim"] = f"{dim:g}"
    if theme.mascot is not None:
        declarations["--ra-mascot-image"] = _css_url(check_image_path(theme.mascot))
    if theme.background is not None:
        declarations["--ra-bg-image"] = _css_url(check_background(theme.background))
    if theme.column_width != DEFAULT_COLUMN_WIDTH:
        declarations["--ra-column-width"] = check_length(theme.column_width)
    if theme.radius != "0.5rem":
        declarations["--ra-radius"] = check_length(theme.radius)
    if theme.button_style == "pill":
        declarations["--ra-button-radius"] = "999px"
    elif theme.button_style == "square":
        declarations["--ra-button-radius"] = "0"
    if theme.mode != "auto":
        declarations["color-scheme"] = "dark" if theme.mode == "dark" else "light"

    unknown = set(declarations) - ALLOWED_PROPERTIES
    if unknown:  # pragma: no cover - a programming error, never user input
        raise ValueError(f"refusing to emit {sorted(unknown)}")
    body = "".join(f"  {name}: {value};\n" for name, value in declarations.items())
    return "".join(faces) + ":root {\n" + body + "}\n"
