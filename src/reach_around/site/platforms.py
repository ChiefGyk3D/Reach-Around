# SPDX-License-Identifier: Apache-2.0
"""Turn a platforms block into the rows the template draws."""

from __future__ import annotations

from dataclasses import dataclass

from reach_around.config.blocks import Platforms
from reach_around.config.numbers import Numbers, resolve_count
from reach_around.config.tablinks import is_tab_ref

_UNITS = ((10**3, "k"), (10**6, "M"), (10**9, "B"))


def abbreviate(count: int) -> str:
    """3812 -> ``3.8k``. Integer math, round half up, no locale, no floats."""
    if count < 1000:
        return str(count)
    for index, (size, suffix) in enumerate(_UNITS):
        tenths = (count * 10 + size // 2) // size
        if tenths < 10_000 or index == len(_UNITS) - 1:
            whole, frac = divmod(tenths, 10)
            return f"{whole}{suffix}" if frac == 0 else f"{whole}.{frac}{suffix}"
    raise AssertionError("unreachable")


@dataclass(frozen=True)
class PlatformRow:
    id: str
    name: str
    icon: str
    handle: str
    url: str
    internal: bool
    label: str
    count: int | None
    short: str | None
    exact: str | None
    as_of: str | None


def platform_rows(block: Platforms, numbers: Numbers | None) -> list[PlatformRow]:
    rows = []
    for ident, entry in zip(block.item_ids, block.items, strict=True):
        found = None if numbers is None else numbers.lookup(ident, entry.platform)
        count, stamp = resolve_count(entry.count, entry.as_of, found)
        rows.append(
            PlatformRow(
                id=ident,
                name=entry.display,
                icon=entry.icon,
                handle=entry.handle,
                url=entry.url,
                internal=is_tab_ref(entry.url),
                label=entry.label,
                count=count,
                short=None if count is None else abbreviate(count),
                exact=None if count is None else f"{count:,}",
                as_of=None if count is None or stamp is None else stamp.isoformat(),
            )
        )
    return rows
