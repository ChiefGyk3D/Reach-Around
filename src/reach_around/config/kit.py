# SPDX-License-Identifier: Apache-2.0
"""The optional ``kit:`` section: media kit content and its display policy.

Only data and rules live here. No page, route or Worker code reads this module
yet; the kit builder (a later task) asks ``public_projection`` and
``gated_projection`` what it may render, so the rule about what is public sits in
one table, ``DISPLAY_POLICY``, and not in a template.

Display policy. Each field is ``public`` (any visitor), ``gated`` (a visitor who
passed the gate) or ``private`` (configuration only, never rendered). The public
half shows the header and the headline numbers and nothing else.

Numbers. A *manual* number is typed in the config and must carry an ``as_of``
date. A *counts* number is read at build time from the numbers file
(``reach_around.config.numbers``) and carries that file's date. A number older
than ``STALE_DAYS`` days is reported by ``kit_warnings``; it never fails the
build.

Unlock link. ``unlock_link: true`` is refused until the emailed link exists
(it is a v1.1 feature); the build never reads the switch.

Unified total. ``unified_total`` adds up the headline metrics that have a value
and are not marked ``in_total: false``. A metric with no value (a manual metric
left empty, or a counts metric the numbers file does not hold) is ignored and
counted in ``ignored``. With nothing to add the total is ``None``, never 0.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from reach_around.config.blocks import MAX_COUNT, PLATFORM_SLUGS
from reach_around.config.numbers import STALE_DAYS as STALE_DAYS
from reach_around.config.numbers import Numbers
from reach_around.site.theme import check_image_path

# A Turnstile site key is public by design (it ships in the page). Plain
# characters only, so it can sit in an attribute with no escaping surprises.
_SITE_KEY = re.compile(r"[0-9A-Za-z_-]{1,64}")

MAX_PERIOD_DAYS = 366
MAX_ENGAGEMENT_COUNT = 10**12

Visibility = Literal["public", "gated", "private"]


@dataclass(frozen=True)
class FieldPolicy:
    visibility: Visibility
    owner_report_only: bool = False


# The richer policy table records provenance requirements as well as audience.
# DISPLAY_POLICY remains the visibility-only view used by existing callers.
KIT_FIELD_POLICY: Mapping[str, FieldPolicy] = {
    "title": FieldPolicy("public"),
    "about": FieldPolicy("public"),
    "contact": FieldPolicy("public"),
    "tags": FieldPolicy("public"),
    "photo": FieldPolicy("public"),
    "metrics": FieldPolicy("public"),
    "analytics": FieldPolicy("public"),
    "unified_total": FieldPolicy("public"),
    "turnstile_site_key": FieldPolicy("public"),
    "partners": FieldPolicy("gated"),
    "case_studies": FieldPolicy("gated"),
    "services": FieldPolicy("gated"),
    "rates": FieldPolicy("gated"),
    "engagement": FieldPolicy("gated", owner_report_only=True),
    "testimonials": FieldPolicy("gated"),
    "demographics": FieldPolicy("gated"),
    "inquiry": FieldPolicy("gated"),
    "disclosure": FieldPolicy("gated"),
    "unlock_link": FieldPolicy("private"),
}

# Every KitConfig field, plus the computed ``unified_total``, has exactly one
# entry. A field with no entry is a bug (``policy_gaps`` and a test catch it),
# and ``visibility`` raises for it instead of guessing.
DISPLAY_POLICY: Mapping[str, Visibility] = {
    name: rule.visibility for name, rule in KIT_FIELD_POLICY.items()
}

# Fields that are never printed in the private PDF although their level is not
# ``private``. The Turnstile key belongs to the gate form, which the PDF does not
# have. The PDF build searches its text for every string value of a field here or
# marked ``private`` and fails if one is found (``reach_around.kit.pdf``).
PDF_EXCLUDED: frozenset[str] = frozenset({"turnstile_site_key"})


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _plain(value: str) -> str:
    if value != value.strip():
        raise ValueError("must not start or end with whitespace")
    for ch in value:
        category = unicodedata.category(ch)
        if category[0] == "C" or (category[0] == "Z" and ch != " "):
            raise ValueError("must be plain text without control or hidden characters")
    if "<" in value or ">" in value:
        raise ValueError("html is not allowed: remove the < and > characters")
    return value


Text30 = Annotated[
    StrictStr, Field(min_length=1, max_length=30), AfterValidator(_plain)
]
Text60 = Annotated[
    StrictStr, Field(min_length=1, max_length=60), AfterValidator(_plain)
]
Text80 = Annotated[
    StrictStr, Field(min_length=1, max_length=80), AfterValidator(_plain)
]
Text100 = Annotated[
    StrictStr, Field(min_length=1, max_length=100), AfterValidator(_plain)
]
Text120 = Annotated[
    StrictStr, Field(min_length=1, max_length=120), AfterValidator(_plain)
]
Text200 = Annotated[
    StrictStr, Field(min_length=1, max_length=200), AfterValidator(_plain)
]
Text300 = Annotated[
    StrictStr, Field(min_length=1, max_length=300), AfterValidator(_plain)
]
Text500 = Annotated[
    StrictStr, Field(min_length=1, max_length=500), AfterValidator(_plain)
]
Text1000 = Annotated[
    StrictStr, Field(min_length=1, max_length=1000), AfterValidator(_plain)
]


def _https(value: str) -> str:
    from reach_around.config.models import check_https_url

    return check_https_url(value)


def _pdf_image(value: str) -> str:
    value = check_image_path(value)
    if not value.lower().endswith((".png", ".jpg", ".jpeg")):
        raise ValueError("must be a PNG or JPEG image so it can be included in PDFs")
    return value


def _iso_date(value: object) -> object:
    """A date or a quoted ``YYYY-MM-DD`` string. No datetimes, no timestamps."""
    if value is None or type(value) is date:
        return value
    if (
        isinstance(value, str)
        and value.isascii()
        and len(value) == 10
        and value[4] == "-"
        and value[7] == "-"
        and (value[:4] + value[5:7] + value[8:]).isdigit()
    ):
        return value
    raise ValueError("must be a date written YYYY-MM-DD")


class Partner(_Model):
    name: Text80
    note: Text200 | None = None
    url: Annotated[str, Field(max_length=2048)] | None = None
    logo: str | None = None

    @field_validator("url")
    @classmethod
    def _url_is_https(cls, value: str | None) -> str | None:
        return None if value is None else _https(value)

    @field_validator("logo")
    @classmethod
    def _logo_is_local(cls, value: str | None) -> str | None:
        return None if value is None else _pdf_image(value)


class CaseStudy(_Model):
    title: Text100
    summary: Text500
    result: Text300


class Testimonial(_Model):
    """One partner quote. The owner supplies the text and is responsible for
    having permission to quote it; nothing here checks that."""

    quote: Text300
    name: Text80
    brand: Text80 | None = None


class Rate(_Model):
    """One rates-card line. ``price_text`` is text, so ``Ask me!`` is fine."""

    item: Text80
    price_text: Text80


class ServicePackage(_Model):
    """One named package: a title, what is included, and a price as text.

    ``price_text`` is free text (``Ask me!`` is fine); nothing is computed from
    it. Both the list and the price are optional.
    """

    title: Text80
    included: list[Text120] = Field(default_factory=list, max_length=12)
    price_text: Text80 | None = None


class Demographic(_Model):
    label: Text60
    value: Text80
    as_of: date

    @field_validator("as_of", mode="before")
    @classmethod
    def _as_of_is_a_date(cls, value: object) -> object:
        return _iso_date(value)


# A plain address in the shape the Worker's form grammar accepts for visitors:
# ASCII, one @, dot-separated atoms, a dotted domain. Plain characters only, so
# it prints and sits in an attribute with no escaping surprises and can never
# carry a header break.
_OWNER_EMAIL = re.compile(
    r"[A-Za-z0-9_+-]+(?:\.[A-Za-z0-9_+-]+)*"
    r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}"
)
_FINGERPRINT_LENGTHS = (
    40,
    64,
)  # OpenPGP v4 (SHA-1) and v6 (SHA-256) fingerprints, in hex digits


class Inquiry(_Model):
    """The business inquiry form on the gated page and the owner details beside it.

    The address and the key fingerprint are the owner's own, taken from here and never
    hard-coded. The form posts to the Worker (``/k/inquiry``); where the message goes is
    the Worker's alert channels, not this block.
    """

    email: StrictStr
    pgp_fingerprint: StrictStr | None = None

    @field_validator("email")
    @classmethod
    def _email_is_plain(cls, value: str) -> str:
        if len(value) > 254 or not _OWNER_EMAIL.fullmatch(value):
            raise ValueError(
                "inquiry.email must be one plain address such as name@example.com "
                "(ASCII, no spaces, at most 254 characters)"
            )
        return value

    @field_validator("pgp_fingerprint")
    @classmethod
    def _fingerprint_is_hex(cls, value: str | None) -> str | None:
        if value is None:
            return None
        digits = value.replace(" ", "")
        if len(digits) not in _FINGERPRINT_LENGTHS or not re.fullmatch(
            r"[0-9A-Fa-f]+", digits
        ):
            raise ValueError(
                "inquiry.pgp_fingerprint must be 40 or 64 hexadecimal digits "
                "(spaces between groups are fine)"
            )
        digits = digits.upper()
        return " ".join(digits[i : i + 4] for i in range(0, len(digits), 4))


class PlatformMetric(_Model):
    """One headline number for one platform.

    ``source: manual`` takes ``value`` and a required ``as_of`` from the config.
    ``source: counts`` takes both from the numbers file at build time, so the
    config must not carry them.
    """

    platform: str
    label: Text60
    source: Literal["manual", "counts"] = "manual"
    value: Annotated[StrictInt, Field(ge=0, le=MAX_COUNT)] | None = None
    as_of: date | None = None
    in_total: StrictBool = True

    @field_validator("platform")
    @classmethod
    def _known_platform(cls, value: str) -> str:
        if value not in PLATFORM_SLUGS:
            raise ValueError(
                "platform must be a platform name from the platforms block"
            )
        return value

    @field_validator("as_of", mode="before")
    @classmethod
    def _as_of_is_a_date(cls, value: object) -> object:
        return _iso_date(value)

    @model_validator(mode="after")
    def _source_rules(self) -> PlatformMetric:
        if self.source == "manual" and self.as_of is None:
            raise ValueError("a typed number needs an as_of date")
        if self.source == "counts" and (
            self.value is not None or self.as_of is not None
        ):
            raise ValueError(
                "a number read from the counts file takes no value or as_of here"
            )
        return self


class AnalyticsStat(_Model):
    """One extensible count tile beside the required follower tile."""

    label: Text60
    value: Annotated[StrictInt, Field(ge=0, le=MAX_ENGAGEMENT_COUNT)]


class PlatformAnalytics(_Model):
    """One explicitly sourced public analytics row for an allowed platform."""

    platform: Literal["instagram", "tiktok", "threads", "twitch"]
    synced_with: Text80
    as_of: date
    followers: Annotated[StrictInt, Field(ge=0, le=MAX_ENGAGEMENT_COUNT)]
    followers_30_day_percent: Annotated[float, Field(ge=-100, le=1_000_000)]
    stats: list[AnalyticsStat] = Field(default_factory=list, max_length=20)

    @field_validator("as_of", mode="before")
    @classmethod
    def _as_of_is_a_date(cls, value: object) -> object:
        return _iso_date(value)

    @model_validator(mode="after")
    def _stats_are_unique(self) -> PlatformAnalytics:
        labels: set[str] = set()
        for stat in self.stats:
            key = unicodedata.normalize("NFKC", stat.label).casefold()
            if key in labels:
                raise ValueError(f"the stat {stat.label!r} is listed more than once")
            labels.add(key)
        return self


class EngagementRow(_Model):
    """Owner-kept inputs for one platform and one UTC period.

    The kit works out average views per post and the engagement rate from these
    (see ``reach_around.config.engagement``). A row with no figures is a platform
    with no source: it prints ``not available``. ``counts`` says what the
    platform counts as an interaction, because definitions differ.
    """

    platform: str
    period_start: date | None = None
    period_end: date | None = None
    as_of: date | None = None
    views: Annotated[StrictInt, Field(ge=0, le=MAX_ENGAGEMENT_COUNT)] | None = None
    posts: Annotated[StrictInt, Field(ge=1, le=1_000_000)] | None = None
    interactions: Annotated[StrictInt, Field(ge=0, le=MAX_ENGAGEMENT_COUNT)] | None = (
        None
    )
    counts: Text120 | None = None
    owner_supplied: StrictBool = False

    @field_validator("platform")
    @classmethod
    def _known_platform(cls, value: str) -> str:
        if value not in PLATFORM_SLUGS:
            raise ValueError(
                "platform must be a platform name from the platforms block"
            )
        return value

    @field_validator("period_start", "period_end", "as_of", mode="before")
    @classmethod
    def _is_a_date(cls, value: object) -> object:
        return _iso_date(value)

    @model_validator(mode="after")
    def _period_rules(self) -> EngagementRow:
        figures = (self.views, self.posts, self.interactions)
        if all(v is None for v in figures):
            return self
        if self.period_start is None or self.period_end is None or self.as_of is None:
            raise ValueError(
                "figures need a period (period_start and period_end, UTC days) "
                "and an as_of date"
            )
        if self.period_end < self.period_start:
            raise ValueError("period_end must not be before period_start")
        if (self.period_end - self.period_start).days + 1 > MAX_PERIOD_DAYS:
            raise ValueError(f"the period may be at most {MAX_PERIOD_DAYS} days")
        if self.as_of < self.period_end:
            raise ValueError("as_of must not be before the period ends")
        return self


class KitConfig(_Model):
    title: Text120
    about: Text1000
    # Optional local, wide photo at the top of both kit pages.
    photo: str | None = None
    # One plain-text line a brand can read to reach the owner. Never a link:
    # the page prints it escaped. The owner decides what goes here (an email
    # written as text is fine); no address or phone is required or checked.
    contact: Text200 | None = None
    # A few content category chips for the header, public on every page.
    tags: list[Text30] = Field(default_factory=list, max_length=8)
    metrics: list[PlatformMetric] = Field(default_factory=list, max_length=30)
    analytics: list[PlatformAnalytics] = Field(default_factory=list, max_length=4)
    # Owner-kept inputs for engagement and average views, per platform and UTC
    # period. Public: the figures are worked out in code, never typed.
    engagement: list[EngagementRow] = Field(default_factory=list, max_length=30)
    partners: list[Partner] = Field(default_factory=list, max_length=50)
    case_studies: list[CaseStudy] = Field(default_factory=list, max_length=20)
    services: list[ServicePackage] = Field(default_factory=list, max_length=20)
    rates: list[Rate] = Field(default_factory=list, max_length=30)
    testimonials: list[Testimonial] = Field(default_factory=list, max_length=10)
    demographics: list[Demographic] = Field(default_factory=list, max_length=30)
    # The inquiry form (gated page only) and the owner's address and key fingerprint
    # shown beside it. Left out: no form.
    inquiry: Inquiry | None = None
    # A short statement (a couple of sentences) on how paid relationships and
    # affiliate links are disclosed and what content a brand can expect to sit
    # next to. The owner words it; nothing is generated. Distinct from the
    # site-wide affiliate ``disclosure`` in the page footer.
    disclosure: Text500 | None = None
    # Public key of the Turnstile widget on the gate form. Optional: without it
    # the form renders an empty widget slot and the build warns.
    turnstile_site_key: StrictStr | None = None
    # v1 has no emailed unlock link, so true is refused (see the validator).
    unlock_link: StrictBool = False

    @field_validator("photo")
    @classmethod
    def _photo_is_local(cls, value: str | None) -> str | None:
        return None if value is None else _pdf_image(value)

    @field_validator("turnstile_site_key")
    @classmethod
    def _site_key_is_plain(cls, value: str | None) -> str | None:
        if value is not None and not _SITE_KEY.fullmatch(value):
            raise ValueError(
                "turnstile_site_key must be 1 to 64 letters, digits, - or _"
            )
        return value

    @field_validator("unlock_link")
    @classmethod
    def _no_unlock_link_yet(cls, value: bool) -> bool:
        if value:
            raise ValueError(
                "unlock_link: true is not available yet: the emailed unlock link "
                "arrives in a later release. Leave it false or remove it."
            )
        return value

    @model_validator(mode="after")
    def _engagement_rows_are_unique(self) -> KitConfig:
        seen: set[tuple[str, date | None, date | None]] = set()
        for entry in self.engagement:
            key = (entry.platform, entry.period_start, entry.period_end)
            if key in seen:
                raise ValueError(
                    f"the engagement row for {entry.platform!r} and that period "
                    "is listed more than once"
                )
            seen.add(key)
        return self

    @model_validator(mode="after")
    def _tags_are_unique(self) -> KitConfig:
        seen: set[str] = set()
        for tag in self.tags:
            key = unicodedata.normalize("NFKC", tag).casefold()
            if key in seen:
                raise ValueError(f"the tag {tag!r} is listed more than once")
            seen.add(key)
        return self

    @model_validator(mode="after")
    def _metrics_are_unique(self) -> KitConfig:
        seen: set[tuple[str, str]] = set()
        for metric in self.metrics:
            key = (metric.platform, metric.label)
            if key in seen:
                raise ValueError(
                    f"the metric {metric.platform!r} / {metric.label!r} is listed "
                    "more than once"
                )
            seen.add(key)
        return self

    @model_validator(mode="after")
    def _analytics_platforms_are_unique(self) -> KitConfig:
        seen: set[str] = set()
        for row in self.analytics:
            if row.platform in seen:
                raise ValueError(
                    f"the analytics row for {row.platform!r} is listed more than once"
                )
            seen.add(row.platform)
        return self

    @property
    def has_gated_content(self) -> bool:
        return any(
            getattr(self, name)
            for name, level in DISPLAY_POLICY.items()
            if level == "gated"
        )


# --- the policy --------------------------------------------------------------


def visibility(name: str) -> Visibility:
    """The visibility of ``name``; ``KeyError`` for a field with no rule."""
    return DISPLAY_POLICY[name]


def enforce_display_policy(kit: KitConfig) -> None:
    """Refuse private-scope platform data that did not come from the owner."""
    if not KIT_FIELD_POLICY["engagement"].owner_report_only:
        return
    for index, entry in enumerate(kit.engagement):
        has_figures = any(
            value is not None
            for value in (entry.views, entry.posts, entry.interactions)
        )
        if entry.platform == "youtube" and has_figures and not entry.owner_supplied:
            raise ValueError(
                "kit.engagement["
                f"{index}] contains YouTube Analytics figures. Collected YouTube "
                "Analytics values may not be used in a kit; enter the figures by "
                "hand and set owner_supplied: true to show them in the deeper kit."
            )


def policy_gaps(
    fields: Iterable[str], policy: Mapping[str, Visibility] = DISPLAY_POLICY
) -> list[str]:
    """Field names that have no entry in ``policy`` (should be empty)."""
    return sorted(name for name in fields if name not in policy)


# --- resolving numbers -------------------------------------------------------


@dataclass(frozen=True)
class ResolvedMetric:
    platform: str
    label: str
    source: str
    value: int | None
    as_of: date | None
    in_total: bool


@dataclass(frozen=True)
class UnifiedTotal:
    total: int | None
    counted: int
    ignored: int


def resolve_metrics(kit: KitConfig, numbers: Numbers | None) -> list[ResolvedMetric]:
    """Each headline metric with its value and date. A manual metric uses the
    config; a counts metric uses the numbers file entry for its platform."""
    out: list[ResolvedMetric] = []
    for metric in kit.metrics:
        value, as_of = metric.value, metric.as_of
        if metric.source == "counts":
            entry = (
                numbers.lookup(metric.platform, metric.platform)
                if numbers is not None
                else None
            )
            value, as_of = (
                (entry.count, entry.as_of_date) if entry is not None else (None, None)
            )
        out.append(
            ResolvedMetric(
                metric.platform,
                metric.label,
                metric.source,
                value,
                as_of,
                metric.in_total,
            )
        )
    return out


def unified_total(resolved: Sequence[ResolvedMetric]) -> UnifiedTotal:
    """Sum of the values that exist and count toward the total (see module doc)."""
    counting = [m for m in resolved if m.in_total]
    valued = [m.value for m in counting if m.value is not None]
    return UnifiedTotal(
        total=sum(valued) if valued else None,
        counted=len(valued),
        ignored=len(counting) - len(valued),
    )


def _age_warning(
    what: str, as_of: date, today: date, limit: int = STALE_DAYS
) -> str | None:
    age = (today - as_of).days
    if age > limit:
        return (
            f"{what}: as_of {as_of.isoformat()} is {age} days old "
            f"(over {limit}); refresh it"
        )
    if age < 0:
        return f"{what}: as_of {as_of.isoformat()} is in the future; check the date"
    return None


def kit_warnings(
    kit: KitConfig,
    numbers: Numbers | None,
    today: date,
    limits: Mapping[str, int] | None = None,
) -> list[str]:
    """Build warnings: old or future dates, and counts metrics with no value."""
    found: list[str | None] = []
    for metric in resolve_metrics(kit, numbers):
        what = f"kit metric {metric.platform} {metric.label!r}"
        if metric.value is None and metric.source == "counts":
            found.append(f"{what}: no value in the numbers file; it is left out")
        elif metric.value is not None and metric.as_of is not None:
            limit = (limits or {}).get(metric.platform, STALE_DAYS)
            found.append(_age_warning(what, metric.as_of, today, limit))
    for entry in kit.engagement:
        if entry.as_of is not None:
            limit = (limits or {}).get(entry.platform, STALE_DAYS)
            found.append(
                _age_warning(
                    f"kit engagement {entry.platform}", entry.as_of, today, limit
                )
            )
    for demo in kit.demographics:
        found.append(_age_warning(f"kit demographic {demo.label!r}", demo.as_of, today))
    return [w for w in found if w is not None]


# --- what each audience may see ---------------------------------------------


def _project(
    kit: KitConfig, numbers: Numbers | None, allowed: frozenset[str]
) -> dict[str, Any]:
    resolved = resolve_metrics(kit, numbers)
    values: dict[str, Any] = {
        "metrics": resolved,
        "unified_total": unified_total(resolved),
    }
    out: dict[str, Any] = {}
    for name, level in DISPLAY_POLICY.items():
        rule = KIT_FIELD_POLICY.get(name)
        # An owner-report-only field is a hard audience restriction: it never
        # reaches the public projection, whatever level the table gives it.
        if rule is not None and rule.owner_report_only and "gated" not in allowed:
            continue
        if level in allowed:
            out[name] = values[name] if name in values else getattr(kit, name)
    return out


def public_projection(kit: KitConfig, numbers: Numbers | None) -> dict[str, Any]:
    """The fields any visitor may see: the header and the headline numbers."""
    return _project(kit, numbers, frozenset({"public"}))


def gated_projection(kit: KitConfig, numbers: Numbers | None) -> dict[str, Any]:
    """Public plus gated fields. Private fields are in neither."""
    return _project(kit, numbers, frozenset({"public", "gated"}))


def pdf_excluded_values(kit: KitConfig) -> dict[str, str]:
    """Field name to string value, for every field that must not reach the PDF:
    those marked ``private`` and those in ``PDF_EXCLUDED``. Only strings can be
    searched for; a switch such as ``unlock_link`` has no text to find."""
    out: dict[str, str] = {}
    for name, level in DISPLAY_POLICY.items():
        if level == "private" or name in PDF_EXCLUDED:
            value = getattr(kit, name, None)
            if isinstance(value, str) and value:
                out[name] = value
    return out
