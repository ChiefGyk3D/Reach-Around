# SPDX-License-Identifier: Apache-2.0
"""Typed, validated site config models.

Every model refuses unknown keys and is frozen. Later phases extend
``SiteConfig`` with new optional fields and new block types; the shared
``_Model`` base and the ``Block`` union are the extension points.
"""

from __future__ import annotations

import functools
import itertools
import re
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    StrictBool,
    StringConstraints,
    Tag,
    ValidationError,
    field_validator,
    model_validator,
)

from reach_around.config.alerts import Alerts
from reach_around.config.analytics import Analytics
from reach_around.config.blocks import (
    Crypto,
    Divider,
    Email,
    Image,
    Live,
    Platforms,
    Text,
)
from reach_around.config.budget import Budget, check_budget
from reach_around.config.cutover import check_cutover_day
from reach_around.config.disclosure import DisclosureField
from reach_around.config.footer import Footer
from reach_around.config.kit import KitConfig, _iso_date
from reach_around.config.loader import ConfigError, load_yaml
from reach_around.config.numbers_sources import NumbersConfig
from reach_around.config.page_title import PageDescription, PageTitle, TitleTemplate
from reach_around.config.privacy import Features, Privacy, missing_notice_fields
from reach_around.config.profile_card import (
    MAX_TAGS,
    MEDIA_KIT_ID,
    ButtonLabel,
    ProfileTag,
)
from reach_around.config.redirects import Redirects
from reach_around.config.robots import Robots
from reach_around.config.site_nav import MAX_ITEMS as MAX_NAV_ITEMS
from reach_around.config.site_nav import NavTitle
from reach_around.config.support import check_no_address, check_no_markup, check_plain
from reach_around.config.tablinks import is_tab_ref, parse_tab_ref
from reach_around.config.theme import Theme
from reach_around.site.theme import check_image_path

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_URL = 2048
# Size limits on the owner-written lists, so one file cannot grow without bound.
MAX_TABS = 50
MAX_BLOCKS_PER_TAB = 200
MAX_LINKS_PER_SECTION = 200
MAX_SOCIALS = 50
# At most this many problems are printed; the rest are only counted.
MAX_REPORTED_PROBLEMS = 20
# The per-list caps above multiply (50 tabs x 200 blocks x 200 links), so the
# whole document is also held to one budget. The file is refused before it is
# parsed when it is larger than MAX_CONFIG_BYTES; the parsed tree is refused
# before any model is built when it holds more than MAX_CONFIG_NODES values
# (every scalar, list, mapping and mapping key counts as one). A real site is a
# few hundred nodes.
MAX_CONFIG_BYTES = 262_144
MAX_CONFIG_NODES = 20_000
MAX_CONFIG_DEPTH = 64
# Error details are only built (pydantic has no lazy form) for a list this
# short. Counting the errors is cheap and builds nothing; a longer list is
# reported as a count only.
MAX_MATERIALIZED_PROBLEMS = 1_000
# Scalar size. The node budget counts a value once however large it is, so the
# text and numbers are also bounded: one scalar at most MAX_SCALAR_CHARS long,
# and all text, mapping keys and integer digits together at most
# MAX_CONFIG_SCALAR_CHARS (a value reached twice counts twice).
MAX_SCALAR_CHARS = 65_536
MAX_CONFIG_SCALAR_CHARS = 262_144
SITE_BUDGET = Budget(
    nodes=MAX_CONFIG_NODES,
    depth=MAX_CONFIG_DEPTH,
    scalar_chars=MAX_SCALAR_CHARS,
    total_chars=MAX_CONFIG_SCALAR_CHARS,
)
# Error locations: a path segment can be a key the file's author typed, so it is
# escaped and cut, and a long path shows only its first segments.
MAX_LOC_SEGMENT_CHARS = 40
MAX_LOC_SEGMENTS = 8

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


def check_link_url(value: str) -> str:
    """Return ``value`` if it is an https URL or a well-formed ``tab:`` link."""
    if is_tab_ref(value):
        parse_tab_ref(value)
        return value
    return check_https_url(value)


HttpsUrl = Annotated[str, Field(max_length=_MAX_URL)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _day_only(value: object) -> object:
    # A date or a quoted YYYY-MM-DD string. No datetimes, no timestamps.
    if value is None:
        return value
    if type(value) is date:
        return value
    if isinstance(value, str) and _ISO_DATE.match(value):
        return value
    raise ValueError("must be a date written YYYY-MM-DD")


class Link(_Model):
    id: Slug
    title: Title
    url: HttpsUrl
    subtitle: Subtitle | None = None
    icon: Slug | None = None
    image: Annotated[str, Field(max_length=_MAX_URL)] | None = None
    image_alt: Annotated[str, StringConstraints(max_length=120)] = ""
    affiliate: bool = False
    featured: bool = False
    show_from: date | None = None
    show_until: date | None = None

    @field_validator("url")
    @classmethod
    def _url_is_https(cls, value: str) -> str:
        return check_link_url(value)

    @field_validator("image")
    @classmethod
    def _image_is_local(cls, value: str | None) -> str | None:
        return None if value is None else check_image_path(value)

    @model_validator(mode="after")
    def _alt_needs_image(self) -> Link:
        if self.image_alt and self.image is None:
            raise ValueError("image_alt needs an image")
        return self

    @field_validator("show_from", "show_until", mode="before")
    @classmethod
    def _iso_date_only(cls, value: object) -> object:
        return _day_only(value)

    @model_validator(mode="after")
    def _window_is_ordered(self) -> Link:
        if (
            self.show_from is not None
            and self.show_until is not None
            and self.show_until < self.show_from
        ):
            raise ValueError("show_until must not be before show_from")
        if self.affiliate and is_tab_ref(self.url):
            raise ValueError("an internal tab: link cannot be an affiliate link")
        return self


Layout = Literal["list", "grid", "cards"]
HeaderStyle = Literal["compact", "centered", "hero"]
PageLayout = Literal["tabs", "sections"]
_ORIGIN_HOST = re.compile(
    r"(?=.{1,253}\Z)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*"
)
PageVariant = Literal["classic", "stack"]
# Paths the build already writes (or that the host serves) at the site root.
RESERVED_SPOKE_IDS = frozenset(
    {"privacy", "static", "e", "e-frame", "kit", "404", "socials", "support"}
)
GroupStyle = Literal["buttons", "rows", "icons"]
Intent = Literal[
    "watch", "videos", "follow", "community", "support", "work", "shop", "blog"
]


class PageStatus(_Model):
    """Owner-written status copy for the smart stack."""

    text: Subtitle
    schedule: Subtitle | None = None


def _plain_kicker(value: str) -> str:
    if value != value.strip() or not value.isprintable():
        raise ValueError(
            "kicker must be plain text with no control characters or edge spaces"
        )
    return value


Kicker = Annotated[
    str,
    StringConstraints(min_length=1, max_length=60),
    AfterValidator(_plain_kicker),
]


class PageFeatured(_Model):
    """One link promoted above the groups, optionally only between two days."""

    link: Slug
    kicker: Kicker
    show_from: date | None = None
    show_until: date | None = None

    @field_validator("show_from", "show_until", mode="before")
    @classmethod
    def _iso_date_only(cls, value: object) -> object:
        return _day_only(value)

    @model_validator(mode="after")
    def _window_is_ordered(self) -> PageFeatured:
        if (
            self.show_from is not None
            and self.show_until is not None
            and self.show_until < self.show_from
        ):
            raise ValueError("show_until must not be before show_from")
        return self


class Page(_Model):
    """Production link-page presentation choices."""

    layout: PageVariant = "classic"
    status: PageStatus | None = None
    watch: list[Slug] = Field(default_factory=list)
    featured: PageFeatured | None = None
    live_badges: StrictBool = False
    # The site's own https origin, with no path. Only read by ``page.layout:
    # stack``: it makes the canonical link of the home page and every spoke page.
    url: HttpsUrl | None = None

    @field_validator("url")
    @classmethod
    def _url_is_an_origin(cls, value: str | None) -> str | None:
        if value is None:
            return value
        message = "page.url must be a bare https origin such as https://example.com"
        try:
            check_https_url(value)
            parts = urlsplit(value)
            port = parts.port
        except ValueError:
            raise ValueError(message) from None
        host = parts.hostname or ""
        if (
            parts.path not in ("", "/")
            or "?" in value
            or "#" in value
            or _ORIGIN_HOST.fullmatch(host) is None
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError(message)
        suffix = "" if port in (None, 443) else f":{port}"
        return f"https://{host}{suffix}"

    @field_validator("featured", mode="before")
    @classmethod
    def _one_featured(cls, value: object) -> object:
        if isinstance(value, list):
            raise ValueError("page.featured accepts one link, not a list")
        return value


class Section(_Model):
    id: Slug
    title: Title
    subtitle: Subtitle | None = None
    collapsed: bool = True
    layout: Layout = "list"
    links: list[Link] = Field(max_length=MAX_LINKS_PER_SECTION)


_NEW_BLOCK_KEYS = {
    "markdown": "text",
    "src": "image",
    "coins": "crypto",
    "address": "email",
    "items": "platforms",
    "players": "live",
}


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
        (Crypto, "crypto"),
        (Email, "email"),
        (Platforms, "platforms"),
        (Live, "live"),
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
    | Annotated[Crypto, Tag("crypto")]
    | Annotated[Email, Tag("email")]
    | Annotated[Platforms, Tag("platforms")]
    | Annotated[Live, Tag("live")]
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
    intent: Intent | None = None
    layout: Layout = "list"
    page_title: PageTitle | None = None
    # Only read by ``page_layout: sections``: whether this tab's section starts
    # open. Unset means the first section on the page is open and the rest closed.
    open: bool | None = None
    # Only read by ``page.layout: stack``: show the group as a closed details.
    collapsed: bool = False
    # Only read by ``page.layout: stack``: how the group's links are drawn.
    # ``icons`` is an icon-only row fed from creator.socials (Follow only).
    style: GroupStyle = "buttons"
    # Only read by ``page.layout: stack``: render the group as its own page at
    # ``/<id>/`` with a single card on the home page that links to it.
    spoke: bool = False
    blocks: list[Block] = Field(max_length=MAX_BLOCKS_PER_TAB)


class Social(_Model):
    name: Title
    url: HttpsUrl
    icon: Slug
    rel_me: bool = False

    @field_validator("url")
    @classmethod
    def _url_is_https(cls, value: str) -> str:
        return check_link_url(value)


class Creator(_Model):
    name: Title
    title: PageTitle | None = None
    description: PageDescription | None = None
    avatar: Annotated[str, Field(max_length=_MAX_URL)] | None = None
    favicon: str | None = None
    header: HeaderStyle = "compact"
    socials: list[Social] = Field(default_factory=list, max_length=MAX_SOCIALS)

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


class StoreConfig(_Model):
    """Where the snapshot store lives. Never holds a DSN or a password."""

    kind: Literal["sqlite", "postgres"] = "sqlite"
    path: Annotated[str, StringConstraints(min_length=1, max_length=1024)] = (
        "data/reach-around.db"
    )
    dsn_env: Literal["REACH_AROUND_DSN"] | None = None

    @field_validator("dsn_env", mode="before")
    @classmethod
    def _canonical_dsn_env(cls, value: object) -> object:
        if value is not None and (
            type(value) is not str or value != "REACH_AROUND_DSN"
        ):
            raise ValueError("dsn_env must be REACH_AROUND_DSN")
        return value

    @model_validator(mode="after")
    def _postgres_needs_dsn_env(self) -> StoreConfig:
        if self.kind == "postgres" and self.dsn_env is None:
            raise ValueError("a postgres store requires dsn_env")
        return self


class ProfileButton(_Model):
    label: ButtonLabel
    url: HttpsUrl

    @field_validator("url")
    @classmethod
    def _url_is_https(cls, value: str) -> str:
        return check_https_url(value)


class ProfileCard(_Model):
    """Name, topic chips and a media-kit button shown after the content."""

    title: PageTitle | None = None
    tags: list[ProfileTag] = Field(default_factory=list, max_length=MAX_TAGS)
    button: ProfileButton | None = None

    @model_validator(mode="after")
    def _has_something_to_show(self) -> ProfileCard:
        if not self.tags and self.button is None:
            raise ValueError("a profile card needs tags or a button")
        return self


class SiteNavItem(_Model):
    title: NavTitle
    url: HttpsUrl
    current: StrictBool = False

    @field_validator("url")
    @classmethod
    def _url_is_https(cls, value: str) -> str:
        return check_https_url(value)


class SiteNav(_Model):
    """The property bar: the creator's own sites, in the header and the footer."""

    items: list[SiteNavItem] = Field(min_length=1, max_length=MAX_NAV_ITEMS)
    header: StrictBool = True
    footer: StrictBool = True

    @model_validator(mode="after")
    def _one_current_and_somewhere_to_show(self) -> SiteNav:
        if sum(item.current for item in self.items) > 1:
            raise ValueError("site_nav: only one item can be current")
        if not self.header and not self.footer:
            raise ValueError("site_nav needs header or footer turned on")
        return self


SUPPORT_PAGE_ID = "support"
_SUPPORT_KINDS = (Crypto, Text, Divider)


def _def_name(ref: str) -> str:
    """The model name in a pydantic ``$defs`` reference, in either schema mode.

    ``#/$defs/pkg__mod__Text-Input__1`` (validation) and the ``-Output`` or
    plain forms (serialization) all give ``Text``.
    """
    tail = ref.rsplit("/", 1)[-1]
    tail = re.sub(r"__\d+$", "", tail)
    tail = re.sub(r"-(?:Input|Output)$", "", tail)
    return tail.rsplit("__", 1)[-1]


def _narrow_support_blocks(schema: dict[str, Any]) -> None:
    """Keep only the support block kinds in the generated schema's item list."""
    names = {kind.__name__ for kind in _SUPPORT_KINDS}
    items = schema.get("items", {})
    items["oneOf"] = [
        ref for ref in items.get("oneOf", []) if _def_name(ref["$ref"]) in names
    ]


# Every string the support page shows, with its kind: ``text`` is plain text
# and ``markdown`` is a text block (shown as escaped plain text, blank lines
# between paragraphs allowed). A coin's address is the one thing not listed.
def _displayed_text(page: SupportPage) -> Iterator[tuple[str, str]]:
    """Every string the support page shows, except a coin's address."""
    yield page.title, "text"
    if page.intro is not None:
        yield page.intro, "text"
    for block in page.blocks:
        if isinstance(block, Text):
            yield block.markdown, "markdown"
        elif isinstance(block, Crypto):
            yield block.title or "", "text"
            yield block.shown_note, "text"
            for coin in block.coins:
                for value in (coin.label, coin.name, coin.network):
                    if value is not None:
                        yield value, "text"


class SupportPage(_Model):
    """The support page (#683): the one place the crypto addresses live.

    It renders at ``/support/`` as the heading, the optional intro, the one
    crypto block, then any other blocks (crypto, text, divider only).
    Text is plain: no links, images or HTML, and nothing address-like outside
    a coin's address. The hub keeps its own direct support links.
    """

    title: PageTitle
    intro: Subtitle | None = None
    # A QR code beside each coin's address, made by the build from the address
    # itself (#713). Off by default: it adds one small image per coin.
    qr: StrictBool = False
    # The validator below refuses the other kinds; the schema says so too.
    blocks: list[Block] = Field(
        min_length=1,
        max_length=MAX_BLOCKS_PER_TAB,
        json_schema_extra=lambda schema: _narrow_support_blocks(schema),
    )

    @model_validator(mode="after")
    def _only_support_blocks(self) -> SupportPage:
        if not all(isinstance(block, _SUPPORT_KINDS) for block in self.blocks):
            raise ValueError("support blocks can be crypto, text or divider")
        if sum(isinstance(block, Crypto) for block in self.blocks) > 1:
            raise ValueError("the support page takes at most one crypto block")
        for block in self.blocks:
            if isinstance(block, Crypto) and any(c.qr is not None for c in block.coins):
                raise ValueError("support coins take no QR image")
        for value, kind in _displayed_text(self):
            check_plain(value, allow_newline=kind == "markdown")
            if kind == "markdown":
                check_no_markup(value)
            check_no_address(value)
        return self


def check_config_budget(raw: object, limit: int = MAX_CONFIG_NODES) -> None:
    """The site config's budget (see ``reach_around.config.budget``)."""
    check_budget(raw, replace(SITE_BUDGET, nodes=limit))


class SiteConfig(_Model):
    # "always": an existing SiteConfig is revalidated, so it reaches the before
    # validator below, which refuses a model instance (a constructed one would
    # otherwise pass through unchecked). A valid config is never revalidated:
    # nothing in src passes an instance to the constructor or model_validate.
    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always"
    )

    version: Literal[1]
    creator: Creator
    tabs: list[Tab] = Field(min_length=1, max_length=MAX_TABS)
    page: Page = Page()
    page_layout: PageLayout = "tabs"
    disclosure: DisclosureField | None = None
    timezone: str = "America/New_York"
    title_template: TitleTemplate | None = None
    theme: Theme = Theme()
    features: Features = Features()
    privacy: Privacy = Privacy()
    footer: Footer = Footer()
    robots: Robots | None = None
    redirects: Redirects | None = None
    store: StoreConfig = StoreConfig()
    profile_card: ProfileCard | None = None
    site_nav: SiteNav | None = None
    support: SupportPage | None = None
    numbers: NumbersConfig | None = None
    analytics: Analytics = Analytics()
    kit: KitConfig | None = None
    # Owner alerts (issue #252), default off; see reach_around.config.alerts.
    alerts: Alerts = Alerts()
    # The UTC calendar day the site went live; see reach_around.config.cutover.
    cutover_date: date | None = None

    @model_validator(mode="before")
    @classmethod
    def _within_budget(cls, data: object) -> object:
        # Runs before any field is validated: an over-budget or odd-shaped
        # document never builds the models (or the error list that goes with them).
        check_config_budget(data)
        return data

    @field_validator("cutover_date", mode="before")
    @classmethod
    def _cutover_is_a_day(cls, value: object) -> object:
        return _iso_date(value)

    @field_validator("cutover_date")
    @classmethod
    def _cutover_in_range(cls, value: date | None) -> date | None:
        return check_cutover_day(value)

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
                elif isinstance(block, Platforms):
                    ids.extend(block.item_ids)
            for ident in ids:
                if ident in seen:
                    raise ValueError(f"duplicate id {ident!r}: ids must be unique")
                seen.add(ident)
        if self.support is not None:
            for block in self.support.blocks:
                if block.id in seen:
                    raise ValueError(f"duplicate id {block.id!r}: ids must be unique")
                seen.add(block.id)
        if (
            self.profile_card is not None
            and self.profile_card.button is not None
            and MEDIA_KIT_ID in seen
        ):
            raise ValueError(
                f"id {MEDIA_KIT_ID!r} is reserved for the profile card button"
            )
        return self

    @model_validator(mode="after")
    def _crypto_lives_on_the_support_page(self) -> SiteConfig:
        if self.support is None:
            return self
        if any(isinstance(b, Crypto) for tab in self.tabs for b in tab.blocks):
            raise ValueError(
                "crypto addresses live on the support page only: remove the "
                "crypto block from the tabs"
            )
        if any(tab.id == SUPPORT_PAGE_ID for tab in self.tabs):
            raise ValueError(
                "a tab uses the id 'support', which the support page takes for "
                "its path /support/; rename the tab"
            )
        return self

    @model_validator(mode="after")
    def _smart_stack_is_complete(self) -> SiteConfig:
        """The smart stack uses tabs as ordered groups and links as its items."""
        page = self.page
        if page.layout == "classic":
            if page.featured is not None:
                raise ValueError("page.featured needs page.layout: stack")
            if page.live_badges:
                raise ValueError("page.live_badges needs page.layout: stack")
            if page.url is not None:
                raise ValueError("page.url needs page.layout: stack")
            for tab in self.tabs:
                if tab.spoke:
                    raise ValueError(f"tab {tab.id!r}: spoke needs page.layout: stack")
                if tab.collapsed:
                    raise ValueError(
                        f"tab {tab.id!r}: collapsed needs page.layout: stack"
                    )
                if tab.style != "buttons":
                    raise ValueError(f"tab {tab.id!r}: style needs page.layout: stack")
            return self
        if page.status is None:
            raise ValueError("page.status is required for page.layout: stack")
        if self.theme.mode == "light":
            raise ValueError("page.layout: stack requires the dark theme")
        if len(page.watch) != 2:
            raise ValueError("page.watch must contain exactly two link ids")
        watch_tabs = [tab for tab in self.tabs if tab.intent == "watch"]
        if len(watch_tabs) != 1:
            raise ValueError("page.layout: stack requires exactly one watch intent")

        links: dict[str, Link] = {}
        watch_links: dict[str, Link] = {}
        spoke_ids: set[str] = set()
        intents: set[str] = set()
        for tab in self.tabs:
            if tab.intent is None:
                raise ValueError(f"tab {tab.id!r} needs intent for page.layout: stack")
            if tab.intent in intents:
                raise ValueError(f"tab intent {tab.intent!r} may appear only once")
            intents.add(tab.intent)
            if tab.spoke:
                if tab.intent == "watch":
                    raise ValueError("the Watch group cannot be a spoke page")
                if tab.collapsed or tab.style != "buttons":
                    raise ValueError(
                        f"tab {tab.id!r}: a spoke page has no collapsed or style"
                    )
                if tab.id in RESERVED_SPOKE_IDS:
                    raise ValueError(
                        f"tab {tab.id!r}: that path is reserved; rename the spoke "
                        "tab (its id is its address)"
                    )
                if page.url is None:
                    raise ValueError(
                        "a spoke page needs page.url for its canonical link"
                    )
                if tab.id == self.analytics.route.strip("/").split("/")[0]:
                    raise ValueError(
                        f"tab {tab.id!r}: a spoke page cannot take the path of "
                        "analytics.route; rename the tab or the route"
                    )
            if tab.collapsed and tab.intent == "watch":
                raise ValueError("the Watch group cannot be collapsed")
            if tab.intent == "watch" and tab.style != "buttons":
                raise ValueError("the Watch group has no style")
            if tab.style == "icons":
                if tab.intent != "follow":
                    raise ValueError("icons are only for the Follow group")
                if not self.creator.socials:
                    raise ValueError("icons need creator.socials")
                if tab.blocks:
                    raise ValueError("an icons group lists no links of its own")
            group_links: list[Link] = []
            for block in tab.blocks:
                if isinstance(block, Link):
                    links[block.id] = block
                    group_links.append(block)
                elif isinstance(block, Section):
                    links.update((link.id, link) for link in block.links)
                    group_links.extend(block.links)
                else:
                    raise ValueError(
                        "page.layout: stack groups support only links and link sections"
                    )
            if tab.spoke:
                spoke_ids.update(link.id for link in group_links)
            if tab.intent == "watch":
                watch_links.update((link.id, link) for link in group_links)
            if tab.spoke and not group_links:
                raise ValueError(
                    f"tab {tab.id!r}: a spoke page needs at least one link"
                )
            if (
                tab.intent != "watch"
                and tab.style != "icons"
                and not tab.spoke
                and not 2 <= len(group_links) <= 6
            ):
                raise ValueError(
                    f"tab {tab.id!r} must contain 2 to 6 links for page.layout: stack"
                )

        missing = [ident for ident in page.watch if ident not in watch_links]
        if missing:
            location = (
                "missing" if missing[0] not in links else "not in the Watch group"
            )
            raise ValueError(f"page.watch link id {missing[0]!r} is {location}")
        selected = [watch_links[ident] for ident in page.watch]
        if any(
            link.show_from is not None or link.show_until is not None
            for link in selected
        ):
            raise ValueError("page.watch links cannot be scheduled")
        if page.featured is not None and page.featured.link not in links:
            raise ValueError(f"page.featured link id {page.featured.link!r} is missing")
        if page.featured is not None and page.featured.link in spoke_ids:
            raise ValueError(
                f"page.featured link id {page.featured.link!r} is on a spoke page; "
                "a spoke page's links cannot be featured"
            )
        icons = {link.icon for link in selected}
        if icons != {"twitch", "kick"}:
            raise ValueError("page.watch must name one Twitch and one Kick link")
        return self

    @model_validator(mode="after")
    def _live_block_fits_the_layout(self) -> SiteConfig:
        lives = [b for tab in self.tabs for b in tab.blocks if isinstance(b, Live)]
        if lives and self.page_layout == "sections":
            raise ValueError(
                "a live block needs page_layout: tabs; page_layout: sections "
                "cannot show it"
            )
        if len(lives) > 1:
            raise ValueError("a site may have only one live block")
        return self

    @model_validator(mode="after")
    def _tab_links_resolve(self) -> SiteConfig:
        known = [tab.id for tab in self.tabs]
        problems = [
            f"{where}: unknown tab {parse_tab_ref(url)[0]!r} in {url!r}; "
            f"valid tab ids: {', '.join(known)}"
            for where, url in _internal_urls(self)
            if parse_tab_ref(url)[0] not in known
        ]
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @model_validator(mode="after")
    def _bot_check_needs_a_beacon(self) -> SiteConfig:
        if (
            self.analytics.turnstile_beacon
            and not self.analytics.page_views
            and not self.features.click_tracking
        ):
            raise ValueError(
                "analytics.turnstile_beacon needs page_views or "
                "features.click_tracking: with neither there is nothing to protect"
            )
        return self

    @model_validator(mode="after")
    def _notice_needs_a_controller_and_contact(self) -> SiteConfig:
        missing = missing_notice_fields(
            self.privacy,
            kit_gate=self.features.kit_gate,
            alerts_enabled=self.alerts.enabled,
        )
        if missing:
            raise ValueError(
                "the privacy notice needs "
                + " and ".join(missing)
                + " when features.kit_gate or alerts are on"
            )
        return self

    @model_validator(mode="after")
    def _gated_kit_content_needs_the_gate(self) -> SiteConfig:
        if self.kit is not None and self.kit.has_gated_content:
            if not self.features.kit_gate:
                raise ValueError(
                    "the kit has gated content (partners, case studies, packages, "
                    "rates, engagement, imported history, testimonials, "
                    "demographics, disclosure or the inquiry form): set "
                    "features.kit_gate to true, or remove it"
                )
        return self

    @model_validator(mode="after")
    def _number_sources_name_a_platform(self) -> SiteConfig:
        """Each ``numbers`` source key is a platform an item uses, or an item id.

        The build looks a count up by item id first, then by platform slug; a
        source that matches neither would write a number nothing shows.
        """
        if self.numbers is None:
            return self
        known: set[str] = set()
        item_ids: set[str] = set()
        slugs: set[str] = set()
        for tab in self.tabs:
            for block in tab.blocks:
                if isinstance(block, Platforms):
                    known.update(block.item_ids)
                    item_ids.update(block.item_ids)
                    slugs.update(item.platform for item in block.items)
                    known.update(item.platform for item in block.items)
        kit_counts = (
            {m.platform for m in self.kit.metrics if m.source == "counts"}
            if self.kit is not None
            else set()
        )
        problems = []
        for i, source in enumerate(self.numbers.sources):
            if source.show_on_hub:
                if source.platform not in known:
                    problems.append(
                        f"numbers.sources[{i}].platform: {source.platform!r} "
                        "matches no platforms item (use an item's platform, such "
                        "as 'github', or its id, such as 'socials-github')"
                    )
            elif source.platform in item_ids and source.platform not in slugs:
                # The kit reads a count by platform slug only, so a kit-only
                # source cannot be keyed by one item's id. Fixed words.
                problems.append(
                    f"numbers.sources[{i}].platform: show_on_hub: false must be "
                    "keyed by a platform, not an item id (the kit reads counts "
                    "by platform)"
                )
            elif source.platform not in kit_counts:
                # Fixed words, no value: a kit-only source feeds the kit alone.
                problems.append(
                    f"numbers.sources[{i}].platform: show_on_hub: false needs a "
                    "kit metric with source: counts for the same platform "
                    "(the source feeds only the kit)"
                )
        if problems:
            raise ValueError("; ".join(problems))
        return self


def _internal_urls(config: SiteConfig) -> list[tuple[str, str]]:
    """Every ``tab:`` url in the config, with the key it was written under."""
    found: list[tuple[str, str]] = []
    for i, social in enumerate(config.creator.socials):
        if is_tab_ref(social.url):
            found.append((f"creator.socials[{i}].url", social.url))
    for t, tab in enumerate(config.tabs):
        for b, block in enumerate(tab.blocks):
            base = f"tabs[{t}].blocks[{b}]"
            if isinstance(block, Link) and is_tab_ref(block.url):
                found.append((f"{base}.url", block.url))
            elif isinstance(block, Platforms):
                found.extend(
                    (f"{base}.items[{k}].url", entry.url)
                    for k, entry in enumerate(block.items)
                    if is_tab_ref(entry.url)
                )
            elif isinstance(block, Section):
                found.extend(
                    (f"{base}.links[{k}].url", link.url)
                    for k, link in enumerate(block.links)
                    if is_tab_ref(link.url)
                )
    return found


_BLOCK_TAGS = {
    "link",
    "section",
    "text",
    "image",
    "crypto",
    "email",
    "platforms",
    "live",
    "divider",
}


def error_path(loc: tuple[int | str, ...]) -> tuple[int | str, ...]:
    """A pydantic error location without the union tag it inserts after a block."""
    out: list[int | str] = []
    previous: int | str | None = None
    for part in loc:
        if not (isinstance(previous, int) and part in _BLOCK_TAGS):
            out.append(part)
        previous = part
    return tuple(out)


UNKNOWN_KEY = "<key>"


@functools.cache
def schema_field_names() -> frozenset[str]:
    """Every setting name the config schema defines, anywhere in the config."""
    from reach_around.schema import build_schema  # schema imports this module

    names: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                names.update(key for key in props if isinstance(key, str))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(build_schema())
    return frozenset(names)


def _safe_segment(part: str) -> str:
    """One key of an error path.

    A name the schema defines is printed (escaped and cut, as a precaution). Any
    other text is a key the file's author typed (a mapping key, an unknown
    field), so it is replaced by a fixed marker and never echoed, at any length.
    """
    if part not in schema_field_names():
        return UNKNOWN_KEY
    text = part[: MAX_LOC_SEGMENT_CHARS + 1]
    out = text.encode("ascii", "backslashreplace").decode("ascii")
    out = "".join(c if c.isprintable() else f"\\x{ord(c):02x}" for c in out)
    if len(part) > MAX_LOC_SEGMENT_CHARS or len(out) > MAX_LOC_SEGMENT_CHARS:
        out = out[:MAX_LOC_SEGMENT_CHARS] + "..."
    return out


def _dotted(loc: tuple[int | str, ...], *, unknown_last: bool = False) -> str:
    """A safe, short rendering of an error location.

    An ``extra_forbidden`` error carries the file's own unknown key as the last
    segment; ``unknown_last`` prints a fixed marker instead of it. Every other
    text segment is escaped to ASCII and cut to ``MAX_LOC_SEGMENT_CHARS``, and
    only the first ``MAX_LOC_SEGMENTS`` segments are shown.
    """
    path = error_path(loc)
    shown = path[:MAX_LOC_SEGMENTS]
    out = ""
    for i, part in enumerate(shown):
        if isinstance(part, int):
            out += f"[{part}]"
            continue
        text = (
            "<unknown field>"
            if unknown_last and i == len(path) - 1
            else _safe_segment(part)
        )
        out += f".{text}" if out else text
    hidden = len(path) - len(shown)
    if hidden > 0:
        out += f" (+{hidden} more path segments)"
    return out


# What a printed problem says, by pydantic error type. The text of a problem is
# never taken from pydantic's own message (or a validator's ValueError), because
# those can repeat the value you wrote, at any length. An unknown type prints
# ``_UNKNOWN_PROBLEM``. ``reach-around check`` gives the specific reason.
_UNKNOWN_PROBLEM = "invalid value"
_ERROR_TEXT: dict[str, str] = {
    "missing": "this field is required",
    "extra_forbidden": "this field is not allowed",
    "value_error": "this value is not allowed; run reach-around check for the reason",
    "assertion_error": "this value is not allowed",
    "string_type": "must be text",
    "string_too_short": "is too short",
    "string_too_long": "is too long",
    "string_pattern_mismatch": "is not in the expected form",
    "too_short": "has too few items",
    "too_long": "has too many items",
    "list_type": "must be a list",
    "dict_type": "must be a mapping",
    "model_type": "must be a mapping",
    "model_attributes_type": "must be a mapping",
    "bool_type": "must be true or false",
    "bool_parsing": "must be true or false",
    "int_type": "must be a whole number",
    "int_parsing": "must be a whole number",
    "int_from_float": "must be a whole number",
    "float_type": "must be a number",
    "float_parsing": "must be a number",
    "finite_number": "must be a finite number",
    "greater_than": "is too small",
    "greater_than_equal": "is too small",
    "less_than": "is too large",
    "less_than_equal": "is too large",
    "literal_error": "is not one of the allowed choices",
    "enum": "is not one of the allowed choices",
    "union_tag_invalid": "is not one of the allowed kinds",
    "union_tag_not_found": "needs a kind",
    "block_type": "must be a mapping",
    "date_type": "must be a date",
    "date_parsing": "must be a date",
    "date_from_datetime_parsing": "must be a date",
    "date_from_datetime_inexact": "must be a date",
    "none_required": "must be empty",
    "url_type": "must be a URL",
    "url_parsing": "must be a URL",
}


# For a ``value_error`` (a validator's ValueError) the type alone says little, so
# the first MAX_REASON_SCAN characters of pydantic's message are only *matched*
# against these patterns, and the text printed is the fixed sentence beside the
# matching pattern. Nothing from the message is ever copied; a message that
# matches none (or one a hostile value spoofs into a different sentence) prints
# the generic ``value_error`` text. ``reach-around check`` has the full list.
MAX_REASON_SCAN = 200
_REASONS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), text)
    for pattern, text in (
        (
            r"^the config holds more than \d+ values",
            f"the config holds more than {MAX_CONFIG_NODES} values; "
            "split it or remove entries",
        ),
        (
            r"^the config is nested more than",
            f"the config is nested more than {MAX_CONFIG_DEPTH} levels deep",
        ),
        (
            r"^the config holds more than \d+ characters",
            f"the config holds more than {MAX_CONFIG_SCALAR_CHARS} characters "
            "of text and numbers",
        ),
        (
            r"^a value in the config is longer than",
            f"a value in the config is longer than {MAX_SCALAR_CHARS}",
        ),
        (r"^a mapping key in the config is not text", "a mapping key is not text"),
        (
            r"^the config holds a value of a type",
            "the config holds a value of a type that is not allowed",
        ),
        (r"^url must start with https://", "url must start with https://"),
        (r"^url must include a host name", "url must include a host name"),
        (r"^url must not contain a user name", "url must not hold a user name"),
        (r"^url must not contain spaces", "url must not hold spaces"),
        (r"^url is (longer|not valid)", "url is not valid"),
        (r"^duplicate id", "an id is used more than once: ids must be unique"),
        (r"^show_until must not be before", "show_until must not be before show_from"),
        (r"^unknown time zone", "unknown time zone"),
        (r"only one live block", "a site may have only one live block"),
        (r"^a live block needs page_layout", "a live block needs page_layout: tabs"),
        (r"^players must list both", "players must list both twitch and kick"),
        (r"^set both channels and events", "set both channels and events, or neither"),
        (r"more than once", "an entry is listed more than once"),
        (
            r"^an internal tab: link cannot be an affiliate link",
            "an internal tab: link cannot be an affiliate link",
        ),
        (
            r"^internal links must use the lowercase scheme",
            "internal links must use the lowercase scheme tab:",
        ),
        (
            r"^internal link must look like",
            "internal link must look like tab:<tab-id> or tab:<tab-id>#<fragment>",
        ),
        (
            r"^a profile card needs tags or a button",
            "a profile card needs tags or a button",
        ),
        (r"^unknown tab ", "unknown tab: use the id of a tab in this file"),
        (r"matches no platforms item", "matches no platforms item"),
        (r"^platform .* has two sources", "a platform has two sources"),
        (
            r"^show_on_hub: false must be keyed by",
            "show_on_hub: false must be keyed by a platform, not an item id",
        ),
        (
            r"^show_on_hub: false needs a kit metric",
            "show_on_hub: false needs a kit metric with source: counts "
            "for the same platform",
        ),
    )
)

# A model-level validator names the setting it means at the front of its message
# (``tabs[0].blocks[0].url: ...``), built from list indexes and setting names by
# code. Such a prefix is printed only when it is made of index numbers and
# names the schema defines; the rest is matched, never copied.
_REASON_PATH = re.compile(r"^([a-z_]+(?:\[\d{1,4}\]|\.[a-z_]+)*): ")
MAX_LOCATED_REASONS = 20
MAX_LOCATED_SCAN = 4_000


def _located_reasons(reason: str) -> list[tuple[str, str]]:
    """The (path, fixed text) pairs of a validator message that names settings.

    Returns an empty list when the message does not start with a path made of
    schema names and indexes, so an unmatched message prints the generic text.
    """
    head = reason[:MAX_LOCATED_SCAN].removeprefix("Value error, ")
    found: list[tuple[str, str]] = []
    for piece in head.split("; ")[:MAX_LOCATED_REASONS]:
        hit = _REASON_PATH.match(piece)
        if hit is None:
            # A continuation (``valid tab ids: ...``) belongs to the piece before.
            continue
        path = hit.group(1)
        names = re.findall(r"[a-z_]+", path)
        if any(name not in schema_field_names() for name in names):
            return []
        text = error_message("value_error", piece[hit.end() :])
        if text == _ERROR_TEXT["value_error"]:
            return []
        found.append((path, text))
    return found


def error_message(error_type: str, reason: str = "") -> str:
    """The fixed, value-free text for a pydantic error type.

    ``reason`` (a ``value_error``'s message) is only matched, never printed."""
    if error_type == "value_error":
        head = reason[:MAX_REASON_SCAN].removeprefix("Value error, ")
        for pattern, text in _REASONS:
            if pattern.search(head):
                return text
    return _ERROR_TEXT.get(error_type, _UNKNOWN_PROBLEM)


def problem_text(exc: ValidationError) -> str:
    """The capped, value-free problem list for a failed site config.

    ``error_count()`` builds nothing, but ``errors()`` builds every entry and
    pydantic has no lazy form. So the entries are only built when there are at
    most ``MAX_MATERIALIZED_PROBLEMS`` of them; a longer list is a fixed
    sentence with the count. The input and context are never requested, so no
    printed entry holds a copy of the document.
    """
    total = exc.error_count()
    if total > MAX_MATERIALIZED_PROBLEMS:
        return (
            f"the config has {total} problems, too many to list; "
            "fix the first ones and run the check again"
        )
    problems = []
    for err in itertools.islice(
        exc.errors(include_url=False, include_input=False, include_context=False),
        MAX_REPORTED_PROBLEMS,
    ):
        where = _dotted(err["loc"], unknown_last=err.get("type") == "extra_forbidden")
        kind, reason = str(err.get("type", "")), str(err.get("msg", ""))
        located = _located_reasons(reason) if kind == "value_error" else []
        if located:
            for path, text in located:
                full = f"{where}.{path}" if where else path
                problems.append(f"{full}: {text}")
            continue
        msg = error_message(kind, reason)
        problems.append(f"{where}: {msg}" if where else msg)
    extra = total - len(problems)
    text = "; ".join(problems)
    if extra > 0:
        text += f"; and {extra} more problems"
    return text


def validate_site_config(raw: object) -> SiteConfig:
    """The one way a parsed document becomes a ``SiteConfig``.

    Every loader (``load_site_config``, ``reach-around check``) calls this. The
    budget runs from the model's own before-validator, so it also runs for
    ``SiteConfig(**data)`` and ``SiteConfig.model_validate``, and an existing
    instance is refused there (``revalidate_instances="always"``).
    ``model_construct`` skips validation by design and is unsupported for
    external data; a test scans ``src`` for it.
    """
    return SiteConfig.model_validate(raw)


def load_site_config(path: Path) -> SiteConfig:
    """Load and validate ``path``; every failure is a ``ConfigError``."""
    raw = load_yaml(path, max_bytes=MAX_CONFIG_BYTES)
    try:
        return validate_site_config(raw)
    except ValidationError as exc:
        raise ConfigError(str(path), None, problem_text(exc)) from None
