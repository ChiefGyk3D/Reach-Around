# SPDX-License-Identifier: Apache-2.0
"""Value types for the snapshot store."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_ZONE_KEY = re.compile(r"^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+){0,2}$")
_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class StoreError(Exception):
    """A store or migration problem. Messages never carry row values."""


class Source(StrEnum):
    FETCHED = "fetched"
    IMPORTED = "imported"
    TYPED = "typed"


class Access(StrEnum):
    PUBLIC = "public"
    OWNER = "owner_authorized"


class Kind(StrEnum):
    SNAPSHOT = "snapshot"
    AGGREGATE = "aggregate"


class Coverage(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"


def check_zone(name: str) -> ZoneInfo:
    """Return the zone for a plain IANA key; raise ``ValueError`` otherwise."""
    if not isinstance(name, str) or not _ZONE_KEY.match(name):
        raise ValueError("not a plain IANA time zone name")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValueError("unknown time zone") from None


def check_utc(instant: datetime) -> None:
    if (
        not isinstance(instant, datetime)
        or instant.tzinfo is None
        or instant.utcoffset() != timedelta(0)
    ):
        raise ValueError("instant must be timezone-aware UTC")


def local_day(instant: datetime, tz_name: str) -> date:
    """The calendar day of ``instant`` in the zone ``tz_name``."""
    return instant.astimezone(check_zone(tz_name)).date()


def fmt_ts(instant: datetime) -> str:
    """Fixed-width UTC text, so text order is time order."""
    check_utc(instant)
    return instant.astimezone(UTC).strftime(_TS_FORMAT)


def parse_ts(text: str) -> datetime:
    return datetime.strptime(text, _TS_FORMAT).replace(tzinfo=UTC)


@dataclass(frozen=True)
class SeriesKey:
    platform: str
    account: str
    metric: str


@dataclass(frozen=True)
class Observation:
    platform: str
    account: str
    metric: str
    kind: Kind
    day: date
    day_tz: str
    observed_at: datetime
    value: float
    source: Source = Source.FETCHED
    access: Access = Access.PUBLIC
    expires_at: date | None = None
    coverage: Coverage = Coverage.COMPLETE
    coverage_note: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("platform", "account", "metric"):
            if not _SLUG.match(str(getattr(self, field_name))):
                raise ValueError(f"{field_name} is not a slug")
        if not isinstance(self.kind, Kind):
            raise ValueError("kind must be a Kind")
        if not isinstance(self.source, Source):
            raise ValueError("source must be a Source")
        if not isinstance(self.access, Access):
            raise ValueError("access must be an Access")
        if not isinstance(self.coverage, Coverage):
            raise ValueError("coverage must be a Coverage")
        if isinstance(self.day, datetime) or not isinstance(self.day, date):
            raise ValueError("day must be a date")
        check_zone(self.day_tz)
        check_utc(self.observed_at)
        if isinstance(self.value, bool) or not isinstance(self.value, int | float):
            raise ValueError("value must be a number")
        if not math.isfinite(self.value) or self.value < 0:
            raise ValueError("value must be finite and not negative")
        if self.expires_at is not None and self.access is Access.PUBLIC:
            raise ValueError("a public observation cannot carry expires_at")
        if self.coverage_note is not None and (
            len(self.coverage_note) > 120 or not self.coverage_note.isprintable()
        ):
            raise ValueError("coverage_note is too long or not printable")
        if (
            self.kind is Kind.SNAPSHOT
            and self.source is Source.FETCHED
            and self.day != local_day(self.observed_at, self.day_tz)
        ):
            raise ValueError("day is not the local day of observed_at")

    @property
    def key(self) -> SeriesKey:
        return SeriesKey(self.platform, self.account, self.metric)

    @property
    def origin(
        self,
    ) -> Literal["public_api", "owner_authorized_api", "imported", "typed"]:
        if self.source is Source.TYPED:
            return "typed"
        if self.source is Source.IMPORTED:
            return "imported"
        if self.access is Access.OWNER:
            return "owner_authorized_api"
        return "public_api"


@dataclass(frozen=True)
class Refusal:
    key: SeriesKey
    day: date
    reason: Literal["day_closed", "future_day", "day_incomplete", "day_sealed"]


@dataclass(frozen=True)
class WriteResult:
    inserted: int
    unchanged: int
    refused: tuple[Refusal, ...]


@dataclass(frozen=True)
class Run:
    platform: str
    account: str
    ts: datetime
    ok: bool
    reason: str | None
    partial: bool = False

    def __post_init__(self) -> None:
        check_utc(self.ts)
        if self.partial and not self.ok:
            raise ValueError("a partial run is an ok run")


@dataclass(frozen=True)
class ClickDay:
    day: date
    day_tz: str
    link_id: str
    country: str
    count: int


@dataclass(frozen=True)
class DeadLetter:
    lo: int
    hi: int
    reason: str
    first_seen: datetime
    last_seen: datetime
    attempts: int
    resolved_at: datetime | None
    resolution: Literal["applied", "skipped"] | None
