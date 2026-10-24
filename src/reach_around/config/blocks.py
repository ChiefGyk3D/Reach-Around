# SPDX-License-Identifier: Apache-2.0
"""Content blocks that carry no schedule: text, image, divider and crypto.

These live apart from ``models`` so each block type has one small home. They
cannot import ``models`` at module level (``models`` imports this module), so
the URL check is imported inside the validators.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    StringConstraints,
    field_validator,
    model_validator,
)

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


# --- crypto -----------------------------------------------------------------

# The only characters a public address may hold. ASCII letters and digits plus
# colon, dot, underscore and dash, so no whitespace, control, bidi, quote, angle
# bracket or backtick can reach the page. Matched with ``fullmatch``: a pattern
# ending in ``$`` would accept a trailing newline.
_ADDRESS = re.compile(r"[A-Za-z0-9:._-]{20,128}")
MAX_COINS = 12
DEFAULT_CRYPTO_NOTE = (
    "Check the first and last characters of the address before you send anything."
)
_COIN_NAMES = {
    "btc": "Bitcoin",
    "xmr": "Monero",
    "eth": "Ethereum",
    "sol": "Solana",
}
# coin -> the icon slug already shipped for it; ``other`` uses the link icon.
COIN_ICONS = {"btc": "bitcoin", "xmr": "monero", "eth": "ethereum", "sol": "solana"}

CoinKind = Literal["btc", "xmr", "eth", "sol", "other"]


def _plain_text(value: str | None) -> str | None:
    """Refuse control, format (bidi, zero width) and separator characters."""
    if value is None:
        return None
    for ch in value:
        category = unicodedata.category(ch)
        if category[0] == "C" or (category[0] == "Z" and ch != " "):
            raise ValueError("must be plain text without control or hidden characters")
    return value


class CryptoCoin(_BlockModel):
    coin: CoinKind
    name: Annotated[str, StringConstraints(min_length=1, max_length=40)] | None = None
    label: Annotated[str, StringConstraints(min_length=1, max_length=60)] | None = None
    network: Annotated[str, StringConstraints(min_length=1, max_length=40)] | None = (
        None
    )
    address: StrictStr
    qr: Annotated[str, Field(max_length=_MAX_ASSET)] | None = None

    @field_validator("name", "label", "network")
    @classmethod
    def _text_is_plain(cls, value: str | None) -> str | None:
        return _plain_text(value)

    @field_validator("address")
    @classmethod
    def _address_shape(cls, value: str) -> str:
        if not _ADDRESS.fullmatch(value):
            raise ValueError(
                "address must be 20 to 128 characters: letters, digits and "
                "the separators : . _ - only"
            )
        return value

    @field_validator("qr")
    @classmethod
    def _qr_is_local_image(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from reach_around.site.theme import check_image_path

        return check_image_path(value)

    @model_validator(mode="after")
    def _name_goes_with_other(self) -> CryptoCoin:
        if self.coin == "other" and self.name is None:
            raise ValueError("name is required when coin is 'other'")
        if self.coin != "other" and self.name is not None:
            raise ValueError("name is only used when coin is 'other'")
        return self

    @property
    def display(self) -> str:
        """The heading shown on the card."""
        if self.label is not None:
            return self.label
        return self.name if self.name is not None else _COIN_NAMES[self.coin]

    @property
    def icon(self) -> str:
        return COIN_ICONS.get(self.coin, "link")


class Crypto(_BlockModel):
    id: BlockId
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    coins: Annotated[list[CryptoCoin], Field(min_length=1, max_length=MAX_COINS)]
    note: Annotated[str, StringConstraints(min_length=1, max_length=300)] | None = None

    @field_validator("title", "note")
    @classmethod
    def _text_is_plain(cls, value: str | None) -> str | None:
        return _plain_text(value)

    @property
    def shown_note(self) -> str:
        return self.note if self.note is not None else DEFAULT_CRYPTO_NOTE
