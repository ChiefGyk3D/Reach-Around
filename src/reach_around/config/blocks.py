# SPDX-License-Identifier: Apache-2.0
"""Content blocks that carry no schedule: text, image and divider.

These live apart from ``models`` so each block type has one small home. They
cannot import ``models`` at module level (``models`` imports this module), so
the URL check is imported inside the validators.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
# A relative asset: letters, digits, dot, dash, underscore and slash only. No
# colon (so no scheme), no backslash, no spaces, and it must not start with a
# slash or a dot.
_ASSET = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/-]*$")
_MAX_ASSET = 512

BlockId = Annotated[str, StringConstraints(pattern=_SLUG.pattern)]


class _BlockModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _check_https(value: str) -> str:
    from reach_around.config.models import check_https_url

    return check_https_url(value)


class Text(_BlockModel):
    id: BlockId
    markdown: Annotated[str, StringConstraints(min_length=1, max_length=5000)]


class Image(_BlockModel):
    id: BlockId
    src: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    alt: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    link: Annotated[str, StringConstraints(max_length=2048)] | None = None

    @field_validator("src")
    @classmethod
    def _src_is_https_or_asset(cls, value: str) -> str:
        if ":" in value:
            return _check_https(value)
        if (
            len(value) > _MAX_ASSET
            or not _ASSET.match(value)
            or "//" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError(
                "src must be an https URL or a relative path like img/photo.png"
            )
        return value

    @field_validator("link")
    @classmethod
    def _link_is_https(cls, value: str | None) -> str | None:
        return None if value is None else _check_https(value)


class Divider(_BlockModel):
    id: BlockId
