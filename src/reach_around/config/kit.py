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

Unified total. ``unified_total`` adds up the headline metrics that have a value
and are not marked ``in_total: false``. A metric with no value (a manual metric
left empty, or a counts metric the numbers file does not hold) is ignored and
counted in ``ignored``. With nothing to add the total is ``None``, never 0.
"""

from __future__ import annotations

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
from reach_around.config.numbers import Numbers

STALE_DAYS = 30

Visibility = Literal["public", "gated", "private"]

# Every KitConfig field, plus the computed ``unified_total``, has exactly one
# entry. A field with no entry is a bug (``policy_gaps`` and a test catch it),
# and ``visibility`` raises for it instead of guessing.
DISPLAY_POLICY: Mapping[str, Visibility] = {
    "title": "public",
    "about": "public",
    "metrics": "public",
    "unified_total": "public",
    "partners": "gated",
    "case_studies": "gated",
    "rates": "gated",
    "demographics": "gated",
    "unlock_link": "private",
}


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

    @field_validator("url")
    @classmethod
    def _url_is_https(cls, value: str | None) -> str | None:
        return None if value is None else _https(value)


class CaseStudy(_Model):
    title: Text100
    summary: Text500
    result: Text300


class Rate(_Model):
    """One rates-card line. ``price_text`` is text, so ``Ask me!`` is fine."""

    item: Text80
    price_text: Text80


class Demographic(_Model):
    label: Text60
    value: Text80
    as_of: date

    @field_validator("as_of", mode="before")
    @classmethod
    def _as_of_is_a_date(cls, value: object) -> object:
        return _iso_date(value)


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


class KitConfig(_Model):
    title: Text120
    about: Text1000
    metrics: list[PlatformMetric] = Field(default_factory=list, max_length=30)
    partners: list[Partner] = Field(default_factory=list, max_length=50)
    case_studies: list[CaseStudy] = Field(default_factory=list, max_length=20)
    rates: list[Rate] = Field(default_factory=list, max_length=30)
    demographics: list[Demographic] = Field(default_factory=list, max_length=30)
    # v1 has no emailed unlock link; nothing reads this switch yet.
    unlock_link: StrictBool = False

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


def _age_warning(what: str, as_of: date, today: date) -> str | None:
    age = (today - as_of).days
    if age > STALE_DAYS:
        return f"{what}: as_of {as_of.isoformat()} is {age} days old (over {STALE_DAYS}); refresh it"
    if age < 0:
        return f"{what}: as_of {as_of.isoformat()} is in the future; check the date"
    return None


def kit_warnings(kit: KitConfig, numbers: Numbers | None, today: date) -> list[str]:
    """Build warnings: old or future dates, and counts metrics with no value."""
    found: list[str | None] = []
    for metric in resolve_metrics(kit, numbers):
        what = f"kit metric {metric.platform} {metric.label!r}"
        if metric.value is None and metric.source == "counts":
            found.append(f"{what}: no value in the numbers file; it is left out")
        elif metric.value is not None and metric.as_of is not None:
            found.append(_age_warning(what, metric.as_of, today))
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
        if level in allowed:
            out[name] = values[name] if name in values else getattr(kit, name)
    return out


def public_projection(kit: KitConfig, numbers: Numbers | None) -> dict[str, Any]:
    """The fields any visitor may see: the header and the headline numbers."""
    return _project(kit, numbers, frozenset({"public"}))


def gated_projection(kit: KitConfig, numbers: Numbers | None) -> dict[str, Any]:
    """Public plus gated fields. Private fields are in neither."""
    return _project(kit, numbers, frozenset({"public", "gated"}))
