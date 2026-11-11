# SPDX-License-Identifier: Apache-2.0
"""The shared NPNC components (#517): tokens only, every state, WCAG AA contrast."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

import pytest

STATIC = resources.files("reach_around.site") / "static"
SHEET = (
    Path(__file__).resolve().parents[2] / "docs" / "design-sheet" / "components.html"
)
CSS = (STATIC / "components.css").read_text(encoding="utf-8")
TOKENS = (STATIC / "tokens.css").read_text(encoding="utf-8")


def _token(name: str) -> str:
    match = re.search(rf"--{name}:\s*(#[0-9A-Fa-f]{{6}})\s*;", TOKENS)
    assert match, name
    return match.group(1)


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# (foreground token, background token, minimum ratio, where it is used)
PAIRS = [
    ("ra-brand-text", "ra-brand-surface", 4.5, "plate title"),
    ("ra-brand-muted", "ra-brand-surface", 4.5, "plate text"),
    ("ra-brand-muted", "ra-brand-plate", 4.5, "amber plate text"),
    ("ra-brand-text", "ra-brand-carbon", 4.5, "carbon plate, chip, ghost"),
    ("ra-brand-amber", "ra-brand-ground", 4.5, "secondary button, ticks"),
    ("ra-brand-amber", "ra-brand-plate", 4.5, "amber chip"),
    ("ra-brand-on-amber", "ra-brand-amber", 4.5, "primary button, solid chip"),
    ("ra-brand-amber", "ra-brand-plate", 4.5, "pressed primary button"),
    ("ra-brand-text", "ra-brand-plate", 4.5, "pressed secondary and ghost"),
    ("ra-brand-amber", "ra-brand-carbon", 4.5, "ghost hover"),
    ("ra-brand-gray", "ra-brand-carbon", 3.0, "disabled (informational)"),
    ("ra-brand-text", "ra-brand-ground", 3.0, "focus ring"),
    ("ra-brand-text", "ra-brand-surface", 3.0, "focus ring on plate"),
    ("ra-brand-amber", "ra-brand-ground", 3.0, "corner ticks"),
    ("ra-brand-rule", "ra-brand-ground", 1.0, "hairline (decorative)"),
]


@pytest.mark.parametrize(("fg", "bg", "minimum", "use"), PAIRS)
def test_pair_meets_wcag_aa(fg: str, bg: str, minimum: float, use: str) -> None:
    assert ratio(_token(fg), _token(bg)) >= minimum, use


def test_contrast_checker_bites() -> None:
    # Negative case: the rule gray on the ground is far below AA text contrast.
    assert ratio(_token("ra-brand-rule"), _token("ra-brand-ground")) < 3.0
    assert ratio("#000000", "#FFFFFF") == pytest.approx(21.0)


def test_every_color_pair_in_the_css_is_covered_by_a_declared_pair() -> None:
    used = set(re.findall(r"var\(--(ra-brand-[a-z-]+)\)", CSS))
    covered = {t for fg, bg, _, _ in PAIRS for t in (fg, bg)}
    # Borders next to a labeled control are decorative, not text.
    decorative = {"ra-brand-rule", "ra-brand-carbon-line", "ra-brand-amber-deep"}
    # Glow and shadow tokens are translucent effects, not text or ground colors.
    effects = {"ra-brand-glow", "ra-brand-glow-strong", "ra-brand-shadow"}
    assert used - effects <= covered | decorative


def test_components_use_tokens_only() -> None:
    body = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    assert body.strip()
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|oklch)\(", body)
    assert not re.search(r"font-family\s*:(?!\s*var\()", body)
    assert not re.search(r"(?<![-\w.])\d*\.?\d+(?:rem|em)\b", body)
    assert not re.search(r"(?:line-height|letter-spacing)\s*:(?!\s*var\()", body)
    assert "prefers-color-scheme" not in body


def test_solid_chip_hover_preserves_on_amber_foreground() -> None:
    interactive_hover = re.search(
        r"([^{}]+\.ra-c-chip[^{}]*:hover[^{}]*)\s*\{([^{}]+)\}",
        CSS,
    )
    assert interactive_hover
    selectors, declarations = interactive_hover.groups()
    assert ":not(.ra-c-chip--solid)" in selectors
    assert "color: var(--ra-brand-amber)" in declarations


def test_explicit_false_aria_current_does_not_select_chip() -> None:
    selected = re.search(r"([^{}]*\[aria-pressed=\"true\"\][^{}]*)\s*\{([^{}]+)\}", CSS)
    assert selected
    selectors, declarations = selected.groups()
    assert '[aria-current]:not([aria-current="false"])' in selectors
    assert "background-color: var(--ra-brand-amber)" in declarations


def test_every_component_has_every_state() -> None:
    for needle in (
        ".ra-c-plate--carbon",
        ".ra-c-plate--amber",
        ".ra-c-ticks::before",
        ".ra-c-chip--amber",
        ".ra-c-chip--solid",
        ".ra-c-btn--secondary",
        ".ra-c-btn--ghost",
        '[aria-pressed="true"]',
        '[aria-disabled="true"]',
        ":focus-visible",
        "prefers-reduced-motion",
    ):
        assert needle in CSS, needle
    for variant in ("", "--secondary", "--ghost"):
        for state in (":is(:hover, .is-hover)", ":is(:active, .is-active)"):
            assert f".ra-c-btn{variant}{state}" in CSS, (variant, state)
    assert "min-height: var(--ra-tap)" in CSS


def test_the_sheet_renders_every_class_in_every_state() -> None:
    html = SHEET.read_text(encoding="utf-8")
    defined = set(re.findall(r"\.(ra-c-[a-z-]+)", CSS))
    assert defined
    for name in defined:
        assert f'{name}"' in html or f"{name} " in html, f"{name} is not on the sheet"
    for state in (
        "is-hover",
        "is-focus",
        "is-active",
        "disabled",
        "aria-disabled",
        "aria-pressed",
        "aria-current",
    ):
        assert state in html, state
    # The sheet is not part of any build, links the token layer first and stays noindex.
    assert html.index("tokens.css") < html.index("components.css")
    assert 'content="noindex"' in html


def test_components_are_published_but_not_linked_by_any_page() -> None:
    template = (
        resources.files("reach_around.site") / "templates" / "base.html"
    ).read_text(encoding="utf-8")
    assert "components.css" not in template
