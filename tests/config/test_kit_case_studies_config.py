# SPDX-License-Identifier: Apache-2.0
"""Case-study aggregate cards in the gated media kit (#550)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config.kit import (
    CaseStudy,
    KitConfig,
    gated_projection,
    public_projection,
)


def case_study(**kw: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "thumbnails": ["media/fake-post-one.png", "media/fake-post-two.jpg"],
        "views": 12345,
        "likes": 678,
        "comments": 90,
        "period_start": "2027-02-01",
        "period_end": "2027-02-28",
    }
    value.update(kw)
    return value


def kit_doc(**kw: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "title": "Example Creator media kit",
        "about": "Synthetic media-kit content.",
    }
    value.update(kw)
    return value


def test_case_study_aggregate_validates_and_stays_gated() -> None:
    kit = KitConfig.model_validate(kit_doc(case_studies=[case_study()]))
    card = kit.case_studies[0]
    assert card.thumbnails == [
        "media/fake-post-one.png",
        "media/fake-post-two.jpg",
    ]
    assert (card.views, card.likes, card.comments) == (12345, 678, 90)
    assert card.period_start.isoformat() == "2027-02-01"
    assert card.period_end.isoformat() == "2027-02-28"
    assert "case_studies" not in public_projection(kit, None)
    assert gated_projection(kit, None)["case_studies"] == kit.case_studies


@pytest.mark.parametrize(
    "missing",
    ["thumbnails", "views", "likes", "comments", "period_start", "period_end"],
)
def test_every_case_study_field_is_required(missing: str) -> None:
    value = case_study()
    value.pop(missing)
    with pytest.raises(ValidationError, match=missing):
        CaseStudy.model_validate(value)


def test_pdf_image_limit_counts_photo_partner_logos_and_case_thumbnails() -> None:
    studies = [case_study(thumbnails=[f"media/post-{n}.png" for n in range(12)])]
    studies += [
        case_study(thumbnails=[f"media/post-{12 + n}.png" for n in range(12)])
        for n in range(3)
    ]
    partners = [
        {"name": f"Partner {n}", "logo": f"media/logo-{n}.png"} for n in range(3)
    ]
    valid = kit_doc(
        photo="media/header.png", partners=partners[:2], case_studies=studies
    )
    assert KitConfig.model_validate(valid).case_studies

    invalid = kit_doc(photo="media/header.png", partners=partners, case_studies=studies)
    with pytest.raises(ValidationError, match="at most 51 images"):
        KitConfig.model_validate(invalid)
