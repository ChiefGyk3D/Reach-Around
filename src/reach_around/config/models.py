# SPDX-License-Identifier: Apache-2.0
"""Typed, validated site config models.

Every model refuses unknown keys and is frozen. Later phases extend
``SiteConfig`` with new optional fields and new block types; the shared
``_Model`` base and the ``Block`` union are the extension points.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    StringConstraints,
    Tag,
    ValidationError,
    field_validator,
    model_validator,
)

from reach_around.config.blocks import Divider, Image, Text
from reach_around.config.loader import ConfigError, load_yaml
from reach_around.config.privacy import Features, Privacy
from reach_around.config.theme import Theme
from reach_around.site.theme import check_image_path

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_URL = 2048

Title = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Subtitle = Annotated[str, StringConstraints(min_length=1, max_length=500)]
Slug = Annotated[str, StringConstraints(pattern=_SLUG.pattern)]


def check_https_url(value: str) -> str:
    """Return ``value`` if it is a plain https URL, else raise ``ValueError``."""
    if len(value) > _MAX_URL:
        raise ValueError(f"url is longer than {_MAX_URL} characters")
    if any(ch.isspace() or not ch.isprintable() for ch in value):
        raise ValueError("url must not contain spaces or control characters")
    try:
        parts = urlsplit(value)
        host = parts.hostname
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError as exc:
        raise ValueError(f"url is not valid: {exc}") from None
    if parts.scheme != "https":
        raise ValueError("url must start with https://")
    if not host:
        raise ValueError("url must include a host name")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ValueError("url must not contain a user name or password")
    return value


HttpsUrl = Annotated[str, Field(max_length=_MAX_URL)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Link(_Model):
    id: Slug
    title: Title
    url: HttpsUrl
    subtitle: Subtitle | None = None
    icon: Slug | None = None
    affiliate: bool = False
    featured: bool = False
    show_from: date | None = None
    show_until: date | None = None

    @field_validator("url")
    @classmethod
    def _url_is_https(cls, value: str) -> str:
        return check_https_url(value)

    @field_validator("show_from", "show_until", mode="before")
    @classmethod
    def _iso_date_only(cls, value: object) -> object:
        # A date or a quoted YYYY-MM-DD string. No datetimes, no timestamps.
        if value is None:
            return value
        if type(value) is date:
            return value
        if isinstance(value, str) and _ISO_DATE.match(value):
            return value
        raise ValueError("must be a date written YYYY-MM-DD")

    @model_validator(mode="after")
    def _window_is_ordered(self) -> Link:
        if (
            self.show_from is not None
            and self.show_until is not None
            and self.show_until < self.show_from
        ):
            raise ValueError("show_until must not be before show_from")
        return self


Layout = Literal["list", "grid", "cards"]
HeaderStyle = Literal["compact", "centered", "hero"]


class Section(_Model):
    id: Slug
    title: Title
    subtitle: Subtitle | None = None
    collapsed: bool = True
    layout: Layout = "list"
    links: list[Link]


_NEW_BLOCK_KEYS = {"markdown": "text", "src": "image"}


def _block_kind(value: Any) -> str | None:
    if isinstance(value, dict):
        if "links" in value:
            return "section"
        for key, kind in _NEW_BLOCK_KEYS.items():
            if key in value:
                return kind
        if "url" not in value and set(value) == {"id"}:
            return "divider"
        return "link"
    for model, kind in (
        (Section, "section"),
        (Link, "link"),
        (Text, "text"),
        (Image, "image"),
        (Divider, "divider"),
    ):
        if isinstance(value, model):
            return kind
    return None


# Later block types are added to this union with their own Tag.
Block = Annotated[
    Annotated[Link, Tag("link")]
    | Annotated[Section, Tag("section")]
    | Annotated[Text, Tag("text")]
    | Annotated[Image, Tag("image")]
    | Annotated[Divider, Tag("divider")],
    Discriminator(
        _block_kind,
        custom_error_type="block_type",
        custom_error_message="block must be a mapping",
    ),
]


class Tab(_Model):
    id: Slug
    title: Title
    layout: Layout = "list"
    blocks: list[Block]


class Social(_Model):
    name: Title
    url: HttpsUrl
    icon: Slug

    @field_validator("url")
    @classmethod
    def _url_is_https(cls, value: str) -> str:
        return check_https_url(value)


class Creator(_Model):
    name: Title
    avatar: Annotated[str, Field(max_length=_MAX_URL)] | None = None
    favicon: str | None = None
    header: HeaderStyle = "compact"
    socials: list[Social] = Field(default_factory=list)

    @field_validator("avatar")
    @classmethod
    def _avatar_is_local_or_https(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "://" in value or value.startswith("//"):
            return check_https_url(value)
        try:
            return check_image_path(value)
        except ValueError as exc:
            raise ValueError(
                f"must be an https URL or a local image path: {exc}"
            ) from None

    @field_validator("favicon")
    @classmethod
    def _favicon_is_local(cls, value: str | None) -> str | None:
        return None if value is None else check_image_path(value)


class SiteConfig(_Model):
    version: Literal[1]
    creator: Creator
    tabs: list[Tab] = Field(min_length=1)
    disclosure: Annotated[str, StringConstraints(max_length=2000)] | None = None
    timezone: str = "America/New_York"
    theme: Theme = Theme()
    features: Features = Features()
    privacy: Privacy = Privacy()

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (KeyError, ValueError, OSError):
            raise ValueError(f"unknown time zone {value!r}") from None
        return value

    @model_validator(mode="after")
    def _ids_are_unique(self) -> SiteConfig:
        seen: set[str] = set()
        for tab in self.tabs:
            ids = [tab.id]
            for block in tab.blocks:
                ids.append(block.id)
                if isinstance(block, Section):
                    ids.extend(link.id for link in block.links)
            for ident in ids:
                if ident in seen:
                    raise ValueError(f"duplicate id {ident!r}: ids must be unique")
                seen.add(ident)
        return self


_BLOCK_TAGS = {"link", "section", "text", "image", "divider"}


def _dotted(loc: tuple[int | str, ...]) -> str:
    out = ""
    previous: int | str | None = None
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        elif isinstance(previous, int) and part in _BLOCK_TAGS:
            continue  # the union tag pydantic inserts after a block index
        else:
            out += f".{part}" if out else part
        previous = part
    return out


def load_site_config(path: Path) -> SiteConfig:
    """Load and validate ``path``; every failure is a ``ConfigError``."""
    raw = load_yaml(path)
    try:
        return SiteConfig.model_validate(raw)
    except ValidationError as exc:
        problems = []
        for err in exc.errors(include_url=False):
            where = _dotted(err["loc"])
            msg = err["msg"].removeprefix("Value error, ")
            problems.append(f"{where}: {msg}" if where else msg)
        raise ConfigError(str(path), None, "; ".join(problems)) from None
