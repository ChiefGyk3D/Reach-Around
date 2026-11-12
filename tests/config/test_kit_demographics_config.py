# SPDX-License-Identifier: Apache-2.0
"""Structured media-kit demographics configuration."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from reach_around.config.kit import KitConfig


def demographics(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "platform": "instagram",
        "as_of": "2027-03-01",
        "gender": {"male": 40, "female": 60},
        "ages": [
            {"range": "18-24", "all": 35, "male": 30, "female": 38},
            {"range": "25-34", "all": 65, "male": 70, "female": 62},
        ],
        "countries": [{"name": "Exampleland", "percentage": 70}],
        "cities": [{"name": "Example City", "percentage": 55}],
    }
    row.update(overrides)
    return row


def kit(row: dict[str, object]) -> KitConfig:
    return KitConfig.model_validate(
        {"title": "Example kit", "about": "Synthetic profile.", "demographics": [row]}
    )


def test_structured_demographics_are_typed() -> None:
    parsed = kit(demographics()).demographics[0]
    assert parsed.platform == "instagram"
    assert parsed.gender.male == 40
    assert parsed.ages[0].female == 38
    assert parsed.countries[0].name == "Exampleland"
    assert parsed.cities[0].percentage == 55


def test_free_form_demographic_rows_are_refused() -> None:
    with pytest.raises(ValidationError, match="platform"):
        kit({"label": "Top country", "value": "Exampleland 40%", "as_of": "2027-03-01"})


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"gender": {"male": 40, "female": 50}}, "total 100"),
        (
            {"ages": [{"range": "18-24", "all": 35, "male": 100, "female": 100}]},
            "All age percentages must total 100",
        ),
        (
            {
                "platform": "threads",
                "cities": [{"name": "Example City", "percentage": 20}],
            },
            "Threads",
        ),
        ({"platform": "youtube"}, "YouTube Analytics"),
    ],
)
def test_inconsistent_structured_demographics_are_refused(
    change: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        kit(demographics(**change))
