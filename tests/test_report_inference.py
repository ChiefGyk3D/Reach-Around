# SPDX-License-Identifier: Apache-2.0
"""Property test: no printed number lets a reader recover a withheld small cell.

The full JSON report is the reader's whole view. A cell, or a sum such as all
non-human views or one page's views, of 1 to floor-1 views is recoverable if
no other dataset prints the same report with a different value for it. The test
enumerates a small domain and, for every small cell or sum, searches for such a
neighbour: the cell changed alone, its views moved to another cell, or rotated
over two pages and two classes. A neighbour may leave the domain, so the bound
of the domain tells a reader nothing. It runs for every floor, class mix and
``include_bots`` setting.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterator

import pytest

from reach_around.funnel.export import Export
from reach_around.funnel.report import build_report

PAGES = ("p1", "p2")
CLASSES = ("h", "a", "s")
VALUES = (0, 2, 5, 9)


def make_export(cells: dict[tuple[str, str], int], kit: bool = False) -> Export:
    export = Export()
    for (page, cls), views in cells.items():
        if views:
            export.view_daily.append(
                {
                    "site": "links",
                    "day": "2026-11-01",
                    "day_tz": "UTC",
                    "page": page,
                    "src_kind": "d",
                    "src": "",
                    "country": "US",
                    "bot_class": cls,
                    "views": views,
                    "uniques": 0,
                }
            )
    export.present.add("view_daily")
    return export


def neighbours(
    cells: dict[tuple[str, str], int], key: tuple[str, str], floor: int
) -> Iterator[dict[tuple[str, str], int]]:
    """Datasets that differ from ``cells`` in ``key`` and keep every row, column
    and grand total (the sums a reader can see): a shift between two cells, and
    a rotation over two pages and two classes. Values may leave the domain."""
    keys = list(cells)
    for delta in range(-9, 10):  # no compensation: the totals may be hidden
        twin = dict(cells)
        twin[key] += delta
        if delta and twin[key] >= 0:
            yield twin
    for other in keys:
        if other == key:
            continue
        for delta in range(-9, 10):
            twin = dict(cells)
            twin[key] += delta
            twin[other] -= delta
            if delta and min(twin.values()) >= 0:
                yield twin
    for j in PAGES:
        for c, d in itertools.permutations(CLASSES, 2):
            for i in PAGES:
                if i == j:
                    continue
                corners = {(i, c): 1, (i, d): -1, (j, c): -1, (j, d): 1}
                if key not in corners:
                    continue
                for delta in range(-9, 10):
                    twin = {k: v + delta * corners.get(k, 0) for k, v in cells.items()}
                    if delta and min(twin.values()) >= 0:
                        yield twin


@pytest.mark.parametrize("floor", [5, 6])
@pytest.mark.parametrize("include_bots", [False, True])
def test_no_small_cell_is_recoverable_from_the_printed_report(
    floor: int, include_bots: bool
) -> None:
    keys = [(p, c) for p in PAGES for c in CLASSES]
    # every cell, and the sums a reader would care about: all non-human views,
    # and each page's views
    targets: list[tuple[tuple[str, str], ...]] = [(k,) for k in keys]
    targets.append(tuple(k for k in keys if k[1] != "h"))
    targets += [tuple(k for k in keys if k[0] == p) for p in PAGES]

    # The same dataset is built again and again as a neighbour of many others, and
    # building a report is a pure function of the cells, so each one is printed
    # once. Same searches, same assertions, a fraction of the report builds.
    memo: dict[tuple[int, ...], str] = {}

    def printed(cells: dict[tuple[str, str], int]) -> str:
        key = tuple(cells[k] for k in keys)
        text = memo.get(key)
        if text is None:
            report = build_report(
                make_export(cells), include_bots=include_bots, floor=floor, top=100
            )
            text = memo[key] = json.dumps(report, sort_keys=True)
        return text

    distinct: set[str] = set()
    for values in itertools.product(VALUES, repeat=len(keys)):
        cells = dict(zip(keys, values, strict=True))
        base = printed(cells)
        distinct.add(base)
        for members in targets:
            v = sum(cells[k] for k in members)
            if not 0 < v < floor:
                continue
            assert any(
                printed(twin) == base and sum(twin[k] for k in members) != v
                for key in members
                for twin in neighbours(cells, key, floor)
            ), ("recoverable", members, cells)
    assert len(distinct) > 50  # the domain really produces distinct reports
