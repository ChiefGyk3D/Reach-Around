# SPDX-License-Identifier: Apache-2.0
"""Content blocks that carry no schedule: text, image, divider, crypto and email.

These live apart from ``models`` so each block type has one small home. They
cannot import ``models`` at module level (``models`` imports this module), so
the URL check is imported inside the validators.
"""

from __future__ import annotations

import base64
import re
import unicodedata
from datetime import date
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

from reach_around.config.pathlimits import check_path_bytes

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
        return check_path_bytes(value, "src")

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


# --- email ------------------------------------------------------------------

# One plain ASCII address and nothing else: no display name, no quoted local
# part, no comment, no Unicode. ``fullmatch`` on purpose (a pattern ending in
# ``$`` would accept a trailing newline).
_EMAIL_LOCAL = re.compile(r"[A-Za-z0-9%+_-]+(?:\.[A-Za-z0-9%+_-]+)*")
_HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_MAX_ADDRESS = 254
_MAX_LOCAL = 64
_SPELL_WORD = re.compile(r"[^\s@.]{1,20}")
EmailReveal = Literal["click", "always", "never_js"]


def _check_email(value: str) -> str:
    problem = (
        "address must be one plain ASCII address like name@example.com: no "
        "display name, quotes, spaces, control characters or Unicode, at most "
        f"{_MAX_ADDRESS} characters"
    )
    if len(value) > _MAX_ADDRESS or value.count("@") != 1:
        raise ValueError(problem)
    local, domain = value.split("@")
    if len(local) > _MAX_LOCAL or not _EMAIL_LOCAL.fullmatch(local):
        raise ValueError(problem)
    labels = domain.split(".")
    if len(labels) < 2 or not all(_HOST_LABEL.fullmatch(part) for part in labels):
        raise ValueError(problem)
    if labels[-1].isdigit():  # 192.0.2.1 is a number, not a name
        raise ValueError(problem)
    return value


class EmailSpell(_BlockModel):
    """The words shown in place of ``@`` and ``.`` when script is off."""

    at: StrictStr = "at"
    dot: StrictStr = "dot"

    @field_validator("at", "dot")
    @classmethod
    def _word(cls, value: str) -> str:
        _plain_text(value)
        if not _SPELL_WORD.fullmatch(value):
            raise ValueError(
                "must be 1 to 20 characters with no spaces, '@' or '.' "
                "(the spelled-out address must not look like one)"
            )
        return value


class Email(_BlockModel):
    """A contact address kept out of the built page.

    The HTML holds the address only reversed and base64 encoded in a data
    attribute; ``email.js`` assembles it. No ``@`` and no mail link is ever
    written to the output. Without script the page shows it spelled out.
    """

    id: BlockId
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    address: StrictStr
    note: Annotated[str, StringConstraints(min_length=1, max_length=300)] | None = None
    reveal: EmailReveal = "click"
    spell: EmailSpell = EmailSpell()

    @field_validator("title", "note")
    @classmethod
    def _text_is_plain(cls, value: str | None) -> str | None:
        return _plain_text(value)

    @field_validator("address")
    @classmethod
    def _address_shape(cls, value: str) -> str:
        return _check_email(value)

    @model_validator(mode="after")
    def _note_keeps_the_address_off_the_page(self) -> Email:
        if self.note is not None:
            lowered = self.note.lower()
            domain = self.address.split("@")[1].lower()
            if "@" in lowered or "mailto:" in lowered or domain in lowered:
                raise ValueError(
                    "note must not contain an address, '@', 'mailto:' or the "
                    "domain: it is shown on the page as plain text"
                )
        return self

    @property
    def spelled(self) -> str:
        """``name at example dot test``, the only form the HTML shows."""
        local, domain = self.address.split("@")
        dot = f" {self.spell.dot} "
        return f"{local.replace('.', dot)} {self.spell.at} {domain.replace('.', dot)}"

    @property
    def encoded(self) -> str:
        """The address reversed, then base64: what ``email.js`` decodes."""
        raw = self.address[::-1].encode("ascii")
        return base64.b64encode(raw).decode("ascii")


# --- platforms --------------------------------------------------------------

MAX_PLATFORMS = 40
MAX_DERIVED_ID = 40  # the click endpoint refuses longer ids
# platform -> the icon slug already shipped for it. ``pixelfed`` has no shipped
# glyph yet, so it (and ``other``) use the generic link icon.
PLATFORM_ICONS = {
    "twitch": "twitch",
    "kick": "kick",
    "youtube": "youtube",
    "tiktok": "tiktok",
    "instagram": "instagram",
    "threads": "threads",
    "bluesky": "bluesky",
    "mastodon": "mastodon",
    "pixelfed": "link",
    "discord": "discord",
    "matrix": "matrix",
    "github": "github",
    "patreon": "patreon",
    "x": "x",
    "linkedin": "linkedin",
    "rss": "rss",
}
PLATFORM_SLUGS = frozenset({*PLATFORM_ICONS, "other"})
_PLATFORM_NAMES = {
    "twitch": "Twitch",
    "kick": "Kick",
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "threads": "Threads",
    "bluesky": "Bluesky",
    "mastodon": "Mastodon",
    "pixelfed": "Pixelfed",
    "discord": "Discord",
    "matrix": "Matrix",
    "github": "GitHub",
    "patreon": "Patreon",
    "x": "X",
    "linkedin": "LinkedIn",
    "rss": "RSS",
}
PlatformKind = Literal[
    "twitch",
    "kick",
    "youtube",
    "tiktok",
    "instagram",
    "threads",
    "bluesky",
    "mastodon",
    "pixelfed",
    "discord",
    "matrix",
    "github",
    "patreon",
    "x",
    "linkedin",
    "rss",
    "other",
]
CountLabel = Literal["followers", "subscribers", "members"]
MAX_COUNT = 10**10
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class PlatformItem(_BlockModel):
    platform: PlatformKind
    name: Annotated[str, StringConstraints(min_length=1, max_length=40)] | None = None
    handle: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    url: Annotated[str, Field(max_length=2048)]
    label: CountLabel = "followers"
    count: Annotated[int, Field(strict=True, ge=0, le=MAX_COUNT)] | None = None
    as_of: date | None = None

    @field_validator("name", "handle")
    @classmethod
    def _text_is_plain(cls, value: str | None) -> str | None:
        return _plain_text(value)

    @field_validator("url")
    @classmethod
    def _url_is_https_or_tab(cls, value: str) -> str:
        from reach_around.config.models import check_link_url

        return check_link_url(value)

    @field_validator("as_of", mode="before")
    @classmethod
    def _iso_date_only(cls, value: object) -> object:
        if value is None or type(value) is date:
            return value
        if isinstance(value, str) and _ISO_DAY.match(value):
            return value
        raise ValueError("must be a date written YYYY-MM-DD")

    @model_validator(mode="after")
    def _shape(self) -> PlatformItem:
        if self.platform == "other" and self.name is None:
            raise ValueError("name is required when platform is 'other'")
        if self.platform != "other" and self.name is not None:
            raise ValueError("name is only used when platform is 'other'")
        if self.as_of is not None and self.count is None:
            raise ValueError("as_of needs a count")
        return self

    @property
    def display(self) -> str:
        return self.name if self.name is not None else _PLATFORM_NAMES[self.platform]

    @property
    def icon(self) -> str:
        return PLATFORM_ICONS.get(self.platform, "link")


class Platforms(_BlockModel):
    id: BlockId
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    layout: Literal["list", "chips"] = "list"
    note: Annotated[str, StringConstraints(min_length=1, max_length=300)] | None = None
    items: Annotated[list[PlatformItem], Field(min_length=1, max_length=MAX_PLATFORMS)]

    @field_validator("title", "note")
    @classmethod
    def _text_is_plain(cls, value: str | None) -> str | None:
        return _plain_text(value)

    @property
    def item_ids(self) -> list[str]:
        """Click ids: ``<block id>-<platform>``, ``-2``, ``-3`` for repeats."""
        seen: dict[str, int] = {}
        ids = []
        for entry in self.items:
            base = f"{self.id}-{entry.platform}"
            seen[base] = seen.get(base, 0) + 1
            ids.append(base if seen[base] == 1 else f"{base}-{seen[base]}")
        return ids

    @model_validator(mode="after")
    def _ids_fit_the_click_endpoint(self) -> Platforms:
        longest = max(len(i) for i in self.item_ids)
        if longest > MAX_DERIVED_ID:
            raise ValueError(
                f"item ids are the block id plus the platform and reach {longest} "
                f"characters; the limit is {MAX_DERIVED_ID}, so shorten the block id"
            )
        return self
