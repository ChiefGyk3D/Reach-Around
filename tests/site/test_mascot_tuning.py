# SPDX-License-Identifier: Apache-2.0
"""``theme.mascot_opacity``, ``mascot_size`` and ``mascot_position``."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config.theme import Theme
from reach_around.site.theme import theme_css


def test_defaults_add_nothing_to_theme_css() -> None:
    theme = Theme()
    assert (theme.mascot_opacity, theme.mascot_size, theme.mascot_position) == (
        None,
        None,
        None,
    )
    assert theme_css(theme) == ":root {\n}\n"


@pytest.mark.parametrize(
    ("value", "text"),
    [(0.0, "0"), (0.05, "0.05"), (0.25, "0.25"), (0.4, "0.4"), (0, "0")],
)
def test_opacity_is_written_as_the_token(value: Any, text: str) -> None:
    assert f"--ra-mascot-opacity: {text};" in theme_css(Theme(mascot_opacity=value))


@pytest.mark.parametrize(
    "value",
    [-0.01, 0.41, 1, 1.0, "0.2", "", True, False, float("nan"), float("inf"), [0.1]],
)
def test_opacity_refuses_out_of_range_and_non_numbers(value: Any) -> None:
    with pytest.raises(ValidationError):
        Theme(mascot_opacity=value)


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("cover", "cover"),
        ("contain", "contain"),
        ("1rem", "1rem"),
        ("40rem", "40rem"),
        ("200rem", "200rem"),
        ("12.5rem", "12.5rem"),
    ],
)
def test_size_accepts_keywords_and_rem_lengths(value: str, text: str) -> None:
    assert f"--ra-mascot-size: {text};" in theme_css(Theme(mascot_size=value))


HOSTILE = [
    "url(https://evil.example/x.png)",
    "url(x)",
    "red; background: blue",
    "cover;",
    "cover}",
    "var(--x)",
    "cover\nred",
    "cover\r",
    "cover /* x */",
    "cover\\",
    "expression(1)",
    "@import x",
    "",
    " ",
    "   cover",
]


@pytest.mark.parametrize(
    "value",
    [
        *HOSTILE,
        "0.5rem",
        "0rem",
        "200.1rem",
        "201rem",
        "-5rem",
        "50%",
        "10px",
        "calc(1rem + 2px)",
        "Cover",
        "auto",
        1,
        True,
    ],
)
def test_size_refuses_everything_else(value: Any) -> None:
    with pytest.raises(ValidationError):
        Theme(mascot_size=value)


@pytest.mark.parametrize(
    "value",
    [
        "center",
        "top",
        "bottom",
        "left",
        "right",
        "center top",
        "left bottom",
        "right center",
        "top left",
        "0%",
        "100%",
        "50% 50%",
        "10.5% 90%",
        "left 20%",
        "20% top",
        "center  bottom",
    ],
)
def test_position_accepts_the_allow_list(value: str) -> None:
    css = theme_css(Theme(mascot_position=value))
    assert f"--ra-mascot-position: {' '.join(value.split())};" in css


@pytest.mark.parametrize(
    "value",
    [
        *HOSTILE[:-1],
        "left right",
        "top bottom",
        "top top",
        "top 50%",
        "50% left",
        "center center center",
        "101%",
        "-1%",
        "100.1%",
        "50",
        "50px",
        "5rem",
        "left 10px",
        "inherit",
        "initial",
        "top\nleft",
        "left;",
        "Left",
        0,
    ],
)
def test_position_refuses_everything_else(value: Any) -> None:
    with pytest.raises(ValidationError):
        Theme(mascot_position=value)


def test_theme_css_rechecks_values_set_past_validation() -> None:
    forged: list[tuple[str, Theme]] = [
        ("mascot_opacity", Theme.model_construct(mascot_opacity=0.9)),
        ("mascot_size", Theme.model_construct(mascot_size="url(x)")),
        ("mascot_position", Theme.model_construct(mascot_position="left; x")),
    ]
    for field, theme in forged:
        with pytest.raises(ValueError, match=field):
            theme_css(theme)
