# SPDX-License-Identifier: Apache-2.0
"""A reusable guard for raw platform imports (#784).

A raw import (an analytics export a creator downloads) must carry aggregate
figures only. It must never carry a field that names a person or a tiny cohort:
names, handles, usernames, emails, user ids, follower or commenter lists, IP
addresses, precise places, or a demographic row under the privacy floor.

Two layers, both closed by default:

* an ALLOWLIST: each importer names the exact aggregate columns (or JSON keys)
  it accepts; anything else is refused;
* a DENYLIST of person-level names and value shapes, as defense in depth, so a
  column added to an allowlist by mistake, or a value smuggled into an allowed
  cell, still fails.

Every refusal is one of the fixed strings below. The guard never puts a header,
key or cell value into a message, so an offending value cannot reach a log, a
traceback or a terminal. Importers re-raise ``ImportGuardError`` as their own
error type with the same text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from reach_around.funnel.floor import FLOOR

PERSON_FIELD = "the import carries a person-level field and is refused"
NOT_ALLOWED = "the import carries a field outside the allowed aggregate set"
PERSON_VALUE = "the import holds a value that identifies a person and is refused"
SMALL_CELL = "the import holds a demographic row under the privacy floor and is refused"

# Names (lowercased, letters and digits only) that identify a person or a tiny
# cohort. Matched as a substring of the normalized name.
_NAME_PARTS = (
    "username",
    "displayname",
    "handle",
    "email",
    "mail",
    "userid",
    "channelid",
    "authorid",
    "commenter",
    "commentauthor",
    "viewerid",
    "ipaddress",
    "ipaddr",
    "latitude",
    "longitude",
    "postalcode",
    "zipcode",
    "street",
    "phone",
    "fullname",
    "firstname",
    "lastname",
    "realname",
)
# Names matched whole, because as substrings they are too common.
_NAME_WHOLE = frozenset(
    {"name", "user", "users", "ip", "ua", "lat", "lng", "lon", "city", "zip", "who"}
)

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_HANDLE = re.compile(r"(^|\s)@[A-Za-z0-9_.]{2,}")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{1,4}\b")
_CHANNEL_ID = re.compile(r"\bUC[0-9A-Za-z_-]{22}\b")
_VALUES = (_EMAIL, _HANDLE, _IPV4, _IPV6, _CHANNEL_ID)


class ImportGuardError(Exception):
    """An import was refused. The message is always one of the fixed strings."""


def _normal(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def is_person_name(name: str) -> bool:
    """True when a column or key name identifies a person or a tiny cohort."""
    flat = _normal(name)
    return flat in _NAME_WHOLE or any(part in flat for part in _NAME_PARTS)


def check_names(names: Iterable[str], allowed: frozenset[str]) -> None:
    """Refuse a header or key set that is person-level or outside ``allowed``.

    The denylist runs first so a person-level column gets the more specific
    fixed message. ``allowed`` is the importer's closed set of aggregate names.
    """
    seen = list(names)
    if any(is_person_name(name) for name in seen):
        raise ImportGuardError(PERSON_FIELD)
    if any(name not in allowed for name in seen):
        raise ImportGuardError(NOT_ALLOWED)


def check_value(value: str) -> None:
    """Refuse a cell that looks like an email, @handle, IP address or channel id."""
    if any(pattern.search(value) for pattern in _VALUES):
        raise ImportGuardError(PERSON_VALUE)


def check_cohort(count: int) -> None:
    """Refuse a demographic count of 1 to FLOOR - 1 (zero is not a small cell)."""
    if 0 < count < FLOOR:
        raise ImportGuardError(SMALL_CELL)
