# SPDX-License-Identifier: Apache-2.0
"""The ``theme`` section of the site config.

Every value is checked by the validators in ``reach_around.site.theme`` when
the config loads, so a hostile value is refused with its key path before any
CSS is written. The defaults match the packaged stylesheet exactly: a config
with no ``theme`` builds the same look as before.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from reach_around.site.theme import (
    COLOR_KEYS,
    DEFAULT_COLUMN_WIDTH,
    MAX_BACKGROUND_DIM,
    check_background,
    check_color,
    check_font,
    check_image_path,
    check_length,
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FontStack(_Model):
    """System font stacks or font files that ship with the instance."""

    body: str | None = None
    heading: str | None = None

    @field_validator("body", "heading")
    @classmethod
    def _font_is_local(cls, value: str | None) -> str | None:
        return None if value is None else check_font(value)


class Theme(_Model):
    colors: dict[str, str] = Field(default_factory=dict)
    fonts: FontStack = FontStack()
    background: str | None = None
    mascot: str | None = None
    background_dim: float = Field(
        default=0.0, ge=0.0, le=MAX_BACKGROUND_DIM, allow_inf_nan=False
    )
    column_width: str = DEFAULT_COLUMN_WIDTH
    radius: str = "0.5rem"
    button_style: Literal["pill", "rounded", "square"] = "rounded"
    mode: Literal["auto", "light", "dark"] = "auto"

    @field_validator("colors")
    @classmethod
    def _known_colors(cls, value: dict[str, str]) -> dict[str, str]:
        for key, color in value.items():
            if key not in COLOR_KEYS:
                raise ValueError(
                    f"unknown color {key[:40]!r}; use one of {', '.join(COLOR_KEYS)}"
                )
            try:
                check_color(color)
            except ValueError as exc:
                raise ValueError(f"{key}: {exc}") from None
        return value

    @field_validator("background")
    @classmethod
    def _background_is_safe(cls, value: str | None) -> str | None:
        return None if value is None else check_background(value)

    @field_validator("mascot")
    @classmethod
    def _mascot_is_local(cls, value: str | None) -> str | None:
        return None if value is None else check_image_path(value)

    @field_validator("column_width", "radius")
    @classmethod
    def _is_a_length(cls, value: str) -> str:
        return check_length(value)
