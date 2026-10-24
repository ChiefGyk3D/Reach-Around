# SPDX-License-Identifier: Apache-2.0
"""The optional top-level ``robots`` config.

When the key is present the build writes ``robots.txt`` and, for
``noindex_paths``, an ``X-Robots-Tag: noindex`` rule in ``_headers``. When the
key is absent the build output is unchanged. Everything here is advisory to
well-behaved crawlers; none of it is access control.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

# A user-agent product token. ASCII only; no whitespace, colon, slash or control
# character, so a value cannot start a new directive or a new line.
TOKEN = re.compile(r"[A-Za-z0-9._*-]{1,60}")
# A path prefix for the noindex header: plain segments only, no wildcards.
NOINDEX_PATH = re.compile(r"/[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*/?")

MAX_BLOCK = 100
MAX_NOINDEX_PATHS = 50
MAX_PATH = 200


def noindex_key(path: str) -> str:
    """Return ``path`` without a trailing slash, for comparing prefixes."""
    return path.rstrip("/")


class Robots(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allow_all: StrictBool = True
    block: Annotated[list[str], Field(max_length=MAX_BLOCK)] = Field(
        default_factory=list
    )
    ai_crawlers: Literal["allow", "block"] = "allow"
    # Sitemaps are not generated; the key exists only so intent is explicit.
    sitemap: Literal[False] = False
    noindex_paths: Annotated[list[str], Field(max_length=MAX_NOINDEX_PATHS)] = Field(
        default_factory=list
    )

    @field_validator("sitemap", mode="before")
    @classmethod
    def _sitemap_is_a_real_false(cls, value: object) -> object:
        if value is not False:
            raise ValueError("sitemap generation is not supported; leave it false")
        return value

    @field_validator("block")
    @classmethod
    def _tokens_are_plain(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        for agent in value:
            if not TOKEN.fullmatch(agent):
                raise ValueError(
                    f"{agent!r} is not a user-agent token: use 1 to 60 of "
                    "letters, digits, '.', '_' and '-'"
                )
            if agent == "*":
                raise ValueError("use allow_all: false instead of blocking '*'")
            if agent.lower() in seen:
                raise ValueError(f"duplicate user-agent token {agent!r}")
            seen.add(agent.lower())
        return value

    @field_validator("noindex_paths")
    @classmethod
    def _paths_are_plain(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        for path in value:
            if len(path) > MAX_PATH or not NOINDEX_PATH.fullmatch(path):
                raise ValueError(
                    f"{path!r} is not an allowed path prefix: start with '/' and "
                    "use plain segments of letters, digits, '.', '_', '~' and '-'"
                )
            if any(part in (".", "..") for part in path.split("/")):
                raise ValueError(f"{path!r} must not contain '.' or '..' segments")
            key = noindex_key(path)
            if key in seen:
                raise ValueError(f"duplicate path {path!r}")
            seen.add(key)
        return value
