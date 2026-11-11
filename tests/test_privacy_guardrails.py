# SPDX-License-Identifier: Apache-2.0
"""Public audience-analysis privacy guardrails (issue #408)."""

import itertools
import json
from pathlib import Path

import pytest

from reach_around.funnel.report import _link_breakdown

ROOT = Path(__file__).parents[1]
POLICY = ROOT / "docs" / "audience-analysis-privacy.md"


def test_live_status_request_has_owner_voice_privacy_wording() -> None:
    privacy = " ".join(
        (ROOT / "docs" / "privacy.md").read_text(encoding="utf-8").split()
    )

    assert (
        "When Live is enabled, your browser asks my site for the current stream "
        "status as soon as you open any tab page or the privacy page, then asks "
        "again every minute while the page is visible. My code sets no cookies "
        "and does not collect or store identifiers from those requests. The "
        "response contains a format version, a cache time, and the time the "
        "status was checked. For each stream it contains whether the stream is "
        "live, when it went live, and a fixed diagnostic code such as `ok` or "
        "`auth_failed`; the code never contains platform or visitor data."
    ) in privacy


def test_do_not_collect_policy_is_public_and_linked_from_privacy_page() -> None:
    policy = POLICY.read_text(encoding="utf-8")
    privacy = (ROOT / "docs" / "privacy.md").read_text(encoding="utf-8")

    required = (
        "raw or partial IP address",
        "user-agent string",
        "TLS or HTTP fingerprint",
        "cross-site or third-party identifier",
        "cookie, `localStorage`, `sessionStorage` or `IndexedDB`",
        "email, account, handle, lead or donation",
        "coordinates, postal code, ASN or organization",
        "full referrer URL or query string",
        "per-visitor network-classification detail",
    )
    for wording in required:
        assert wording in policy
    assert (
        "[Audience-analysis do-not-collect list](audience-analysis-privacy.md)"
        in privacy
    )
    assert "docs/audience-analysis-privacy.md" in (
        ROOT / "docs" / "security" / "threat-model.md"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "matrix_key",
    (
        "click_matrix",
        "click_countries",
    ),
)
def test_report_matrix_cells_and_margins_cannot_solve_a_small_cell(
    matrix_key: str,
) -> None:
    keys = tuple(itertools.product(("row-a", "row-b"), ("col-a", "col-b")))
    floor = 5
    datasets: list[tuple[tuple[int, ...], str]] = []
    alternatives: dict[str, list[tuple[int, ...]]] = {}

    for values in itertools.product(range(7), repeat=len(keys)):
        cells: dict[str, dict[str, int]] = {}
        for (row, column), value in zip(keys, values, strict=True):
            cells.setdefault(column, {})[row] = value
        released = _link_breakdown(
            cells,
            printed=None,
            floor=floor,
            top=10,
            present=True,
            months=[],
        )
        released["matrix_key"] = matrix_key
        signature = json.dumps(released, sort_keys=True)
        datasets.append((values, signature))
        alternatives.setdefault(signature, []).append(values)

    assert len(alternatives) > 20
    for values, signature in datasets:
        for index, value in enumerate(values):
            if 0 < value < floor:
                assert any(
                    other[index] != value for other in alternatives[signature]
                ), (
                    "recoverable",
                    keys[index],
                    values,
                )
