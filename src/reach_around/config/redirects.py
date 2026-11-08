# SPDX-License-Identifier: Apache-2.0
"""The optional top-level ``redirects`` config.

When ``redirects.socials`` is present the build writes a ``_redirects`` file that
sends the old ``/socials`` page (and ``/socials/``) to a root-relative target,
``/`` by default. When the key is absent no ``_redirects`` file is written and
the build output is unchanged. See ``reach_around.site.redirects``.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

# A root-relative path: a leading slash, then plain characters. No scheme, no
# host, no query or fragment, no whitespace or control character, no backslash.
TARGET = re.compile(r"/[A-Za-z0-9._~/-]*")
MAX_TARGET = 200
# The two paths this option owns. A target equal to either would redirect a
# path to itself (a loop).
SOCIALS_PATHS = ("/socials", "/socials/")


def target_problem(value: object) -> str | None:
    """Return why ``value`` is not a safe redirect target, or None if it is.

    One predicate shared by config validation and the renderer's defense in
    depth, so a model built without validation cannot emit a rule.
    """
    if type(value) is not str:
        return "target must be a string"
    if len(value) > MAX_TARGET or not TARGET.fullmatch(value):
        return (
            f"{value[:60]!r} is not a root-relative path: start with '/' and "
            "use letters, digits, '.', '_', '~', '-' and '/' only"
        )
    if value.startswith("//"):
        return "target must not start with '//' (a different host)"
    if any(part in (".", "..") for part in value.split("/")):
        return "target must not contain '.' or '..' segments"
    if value in SOCIALS_PATHS:
        return "target must not be /socials or /socials/ (a redirect loop)"
    return None


class SocialsRedirect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # 301 for production; 302 while testing, so a browser does not cache it.
    # The validator below also refuses True, "301" and 301.0.
    status: Literal[301, 302] = 301
    target: str = "/"

    @field_validator("status", mode="before")
    @classmethod
    def _status_is_a_real_int(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("status must be the number 301 or 302")
        return value

    @field_validator("target")
    @classmethod
    def _target_is_root_relative(cls, value: str) -> str:
        problem = target_problem(value)
        if problem:
            raise ValueError(problem)
        return value


class Redirects(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    socials: SocialsRedirect | None = None
