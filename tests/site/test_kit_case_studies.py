# SPDX-License-Identifier: Apache-2.0
"""Case-study thumbnails and totals stay in gated kit output (#550)."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_kit_build import build, kit_doc, make_config, tree_bytes

from assetpaths import png
from reach_around.site.kit import GATED_FILE, KitLeakError


def aggregate() -> dict[str, object]:
    return {
        "thumbnails": ["media/fake-post-one.png", "media/fake-post-two.png"],
        "views": 12345,
        "likes": 678,
        "comments": 90,
        "period_start": "2027-02-01",
        "period_end": "2027-02-28",
    }


def test_case_study_card_renders_thumbnails_totals_and_date_range(
    tmp_path: Path,
) -> None:
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "fake-post-one.png").write_bytes(png("one"))
    (tmp_path / "media" / "fake-post-two.png").write_bytes(png("two"))
    config = make_config(kit_doc(case_studies=[aggregate()]))

    out, gated = build(tmp_path, config, assets_dir=tmp_path)

    page = (gated / GATED_FILE).read_text(encoding="utf-8")
    assert 'class="kit-case kit-case-study"' in page
    assert page.count('class="kit-case-thumbnail"') == 2
    for text in (
        "12,345",
        "Views",
        "678",
        "Likes",
        "90",
        "Comments",
        "2027-02-01",
        "2027-02-28",
    ):
        assert text in page
    assert "data:image/png;base64," in page
    public = tree_bytes(out)
    assert b"fake-post-one" not in public
    assert png("one") not in public


def test_case_studies_alone_make_the_kit_gated(tmp_path: Path) -> None:
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "fake-post-one.png").write_bytes(png("one"))
    (tmp_path / "media" / "fake-post-two.png").write_bytes(png("two"))
    config = make_config(kit_doc(case_studies=[aggregate()]))

    out, gated = build(tmp_path, config, assets_dir=tmp_path)

    assert (gated / GATED_FILE).is_file()
    assert (
        "case studies"
        in (out / "kit" / "index.html").read_text(encoding="utf-8").lower()
    )


def test_case_study_totals_are_guarded_from_template_leaks(tmp_path: Path) -> None:
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "fake-post-one.png").write_bytes(png("one"))
    (tmp_path / "media" / "fake-post-two.png").write_bytes(png("two"))
    overrides = tmp_path / "overrides" / "templates"
    overrides.mkdir(parents=True)
    overrides.joinpath("kit.html").write_text(
        '{% extends "base.html" %}{% block content %}<p>12,345 Views</p>{% endblock %}',
        encoding="utf-8",
    )
    config = make_config(kit_doc(case_studies=[aggregate()]))

    with pytest.raises(KitLeakError, match=r"case_studies.*views"):
        build(
            tmp_path,
            config,
            assets_dir=tmp_path,
            overrides=tmp_path / "overrides",
        )
