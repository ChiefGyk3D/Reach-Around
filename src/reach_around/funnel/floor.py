# SPDX-License-Identifier: Apache-2.0
"""The privacy floor rule shared by the report and the private analytics block.

Label suppression, like the Worker's small-cell fold: a cell under the floor is
never printed (default and minimum 5), and the cells under it are merged into
one "below the floor" line only when together they reach the floor. It is not
k-anonymity. Moved out of ``reach_around.funnel.report`` (#397) so another block
can use the same rule without importing the report.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

FLOOR = 5

UNDER_FLOOR = f"Under {FLOOR}"


def shown_count(value: int) -> str:
    """A count as the kit prints it: zero, or at least the floor (#397).

    A count of 1 to 4 prints as "Under 5" on the page and in both PDFs, so no
    printed figure pins a handful of people or views. Zero is not a small cell.
    """
    return UNDER_FLOOR if 0 < value < FLOOR else f"{value:,}"


@dataclass(frozen=True)
class Floored:
    shown: list[tuple[str, int]] = field(default_factory=list)
    other: int = 0  # merged views of hidden rows; 0 unless they reach the floor
    hidden_rows: int = 0
    dropped: bool = False  # a remainder could not be made safe

    @property
    def withheld(self) -> bool:
        return not self.shown and self.other == 0


def _suppress(
    cells: Mapping[str, int], floor: int, pinned: frozenset[str] = frozenset()
) -> Floored:
    """Keep rows of at least ``floor`` views, largest first; merge the rest.

    The rows plus the merged line add up to a total that is printed elsewhere,
    so the merged line must not reveal a cell. Whenever any row is under the
    floor, the smallest shown row joins the merged line, always. That makes the
    remainder at least the floor (subtracting the shown rows from the total
    cannot isolate a small cell) and it removes the upper bound that a group of
    only small cells has (a remainder of members times floor-minus-one would
    pin every member). The rule never depends on the values, so the shape of
    the output tells a reader nothing about them. With nothing to join, the
    remainder is dropped and ``dropped`` is set.

    Labels in ``pinned`` are printed exactly somewhere else (the funnel prints
    the human class), so they never join the merged line.
    """
    items = sorted(
        ((label, views) for label, views in cells.items() if views > 0),
        key=lambda item: (-item[1], item[0]),
    )
    shown = [item for item in items if item[1] >= floor]
    hidden = [item for item in items if item[1] < floor]
    if not hidden:
        return Floored(shown, 0, 0)
    movable = [item for item in shown if item[0] not in pinned]
    if not movable:
        return Floored(shown, 0, len(hidden), dropped=True)
    pick = movable[0]
    shown.remove(pick)
    hidden.append(pick)
    return Floored(shown, sum(views for _, views in hidden), len(hidden))


def floor_cells(cells: Mapping[str, int], floor: int) -> Floored:
    return _suppress(cells, floor)


def share_pins_small_count(
    numerator: int, denominator: int, base: int, floor: int = FLOOR
) -> bool:
    """True if a share printed beside a count would tell a reader the cell is
    1 to ``floor`` - 1.

    The share is ``numerator / denominator`` rounded to the unit printed, so the
    true share lies half a unit either way. The most the cell can hold is then
    ``base * (2 * numerator + 1) / (2 * denominator)``; a cell that could hold
    nothing above zero is a zero, and a cell that could reach the floor is not
    pinned under it.
    """
    most = base * (2 * numerator + 1) // (2 * denominator)
    return 1 <= most < floor
