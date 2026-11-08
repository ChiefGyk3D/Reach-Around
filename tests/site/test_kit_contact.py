# SPDX-License-Identifier: Apache-2.0
"""The optional kit contact line (#418): plain text, never a link."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_kit_build import build, kit_doc, make_config

from reach_around.config.kit import KitConfig
from reach_around.kit.pdf import _expected_text


def with_contact(text: str | None) -> dict[str, Any]:
    doc = kit_doc()
    if text is not None:
        doc["contact"] = text
    return doc


def pages(tmp_path: Path, text: str | None) -> tuple[str, str]:
    kit = with_contact(text)
    kit["partners"] = [{"name": "Acme"}]
    out, gated = build(tmp_path, make_config(kit))
    return (
        (out / "kit" / "index.html").read_text(encoding="utf-8"),
        (gated / "kit.html").read_text(encoding="utf-8"),
    )


def test_the_contact_line_shows_on_both_pages_as_plain_text(tmp_path: Path) -> None:
    public, gated = pages(tmp_path, "Brand deals: partners at example.com")
    for html in (public, gated):
        assert "Brand deals: partners at example.com" in html
        assert "mailto:" not in html and "tel:" not in html


def test_no_contact_means_no_contact_markup(tmp_path: Path) -> None:
    public, gated = pages(tmp_path, None)
    assert "kit-contact" not in public and "kit-contact" not in gated


def test_hostile_text_is_refused_or_escaped_and_never_linked(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        KitConfig.model_validate(with_contact("<script>alert(1)</script>"))
    public, _ = pages(tmp_path, 'a & b "quoted" mailto:me@example.com')
    line = public.split("kit-contact")[1].split("</p>")[0]
    assert "a &amp; b" in line and "<a " not in line
    assert 'href="mailto' not in public


BAD = ["", " lead", "trail ", "two\nlines", "tab\there", "x" * 201, "a‮b"]


@pytest.mark.parametrize("bad", BAD)
def test_bad_contact_values_are_refused(bad: str) -> None:
    with pytest.raises(ValidationError):
        KitConfig.model_validate(with_contact(bad))


def test_the_contact_line_is_optional_and_the_limit_is_inclusive() -> None:
    assert KitConfig.model_validate(with_contact(None)).contact is None
    assert KitConfig.model_validate(with_contact("x" * 200)).contact == "x" * 200


def test_the_pdf_text_check_expects_the_contact_line() -> None:
    kit = KitConfig.model_validate(with_contact("Reach me at example.com"))
    assert ("kit.contact", "Reach me at example.com") in _expected_text(kit, None)
