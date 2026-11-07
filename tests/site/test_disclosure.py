# SPDX-License-Identifier: Apache-2.0
"""The affiliate disclosure: legacy text, object form, default text, limits."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import SiteConfig
from reach_around.config.disclosure import (
    DEFAULT_DISCLOSURE_TEXT,
    MAX_DISCLOSURE_TEXT,
)
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
ASSETS = Path(__file__).resolve().parents[2] / "examples" / "instance"
DEFAULT = (
    "Some links on this page may be affiliate links. Purchases made through "
    "them may earn the creator a small commission at no extra cost to the "
    "buyer, without affecting their recommendations."
)


def cfg(disclosure: Any = None, affiliate: bool = True) -> SiteConfig:
    data: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": [
            {
                "id": "start",
                "title": "Start",
                "blocks": [
                    {
                        "id": "a",
                        "title": "A",
                        "url": "https://e.com/",
                        "affiliate": affiliate,
                    }
                ],
            }
        ],
    }
    if disclosure is not None:
        data["disclosure"] = disclosure
    return SiteConfig.model_validate(data)


def home(config: SiteConfig, out: Path) -> str:
    build_site(config, out, today=TODAY, assets_dir=ASSETS)
    return (out / "index.html").read_text(encoding="utf-8")


def test_default_text_is_the_neutral_wording() -> None:
    assert DEFAULT_DISCLOSURE_TEXT == DEFAULT
    assert "NEVER" not in DEFAULT


def test_legacy_string_still_works(tmp_path: Path) -> None:
    html = home(cfg("We may earn a commission."), tmp_path)
    assert '<p class="disclosure">We may earn a commission.</p>' in html


def test_empty_object_uses_default_text(tmp_path: Path) -> None:
    html = home(cfg({}), tmp_path)
    assert f'<p class="disclosure">{DEFAULT}</p>' in html


def test_absent_disclosure_shows_nothing(tmp_path: Path) -> None:
    assert 'class="disclosure"' not in home(cfg(), tmp_path)


def test_no_affiliate_links_hides_unless_always(tmp_path: Path) -> None:
    assert 'class="disclosure"' not in home(cfg({}, affiliate=False), tmp_path)
    html = home(cfg({"always": True}, affiliate=False), tmp_path / "b")
    assert 'class="disclosure"' in html


def test_show_false_hides_even_with_always(tmp_path: Path) -> None:
    html = home(cfg({"show": False, "always": True}), tmp_path)
    assert 'class="disclosure"' not in html


def test_text_is_escaped_and_not_a_link(tmp_path: Path) -> None:
    html = home(cfg({"text": "a <script>x</script> & b"}), tmp_path)
    assert "<script>x" not in html
    assert "a &lt;script&gt;x&lt;/script&gt; &amp; b" in html


@pytest.mark.parametrize(
    "text",
    [
        "x" * (MAX_DISCLOSURE_TEXT + 1),
        "",
        "  padded ",
        "line\nbreak",
        "zero​width",
        "see https://evil.example/x",
        "see http://evil.example",
        "see www.evil.example",
        "mail me@evil.example",
        "see //evil.example",
        "//evil.example",
        "see \\\\host\\share",
        "javascript:alert(1)",
        "click JavaScript:alert(1)",
        "data:text/html,x",
        "see DATA:text/plain;base64,AAAA",
        "vbscript:x",
        "file:///etc/passwd",
    ],
)
def test_bad_text_is_refused(text: str) -> None:
    with pytest.raises(ValidationError):
        cfg({"text": text})


def test_unknown_key_and_non_bool_refused() -> None:
    with pytest.raises(ValidationError):
        cfg({"txt": "x"})
    with pytest.raises(ValidationError):
        cfg({"show": "yes"})


def test_text_at_the_cap_is_accepted() -> None:
    assert cfg({"text": "x" * MAX_DISCLOSURE_TEXT})


def test_other_pages_without_affiliates_hide_it(tmp_path: Path) -> None:
    config = SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "E"},
            "disclosure": {},
            "tabs": [
                {
                    "id": "a",
                    "title": "A",
                    "blocks": [
                        {
                            "id": "x",
                            "title": "X",
                            "url": "https://e.com/",
                            "affiliate": True,
                        }
                    ],
                },
                {
                    "id": "b",
                    "title": "B",
                    "blocks": [{"id": "y", "title": "Y", "url": "https://e.com/y"}],
                },
            ],
        }
    )
    build_site(config, tmp_path, today=TODAY, assets_dir=ASSETS)
    assert DEFAULT in (tmp_path / "index.html").read_text()
    assert DEFAULT not in (tmp_path / "b" / "index.html").read_text()


def test_schema_accepts_both_forms() -> None:
    from reach_around.schema import build_schema

    schema = build_schema()
    options = schema["properties"]["disclosure"]["anyOf"]
    kinds = {o.get("type") or o.get("$ref") for o in options}
    assert kinds == {"string", "#/$defs/Disclosure", "null"}
    string_form = next(o for o in options if o.get("type") == "string")
    assert string_form["minLength"] == 1
    assert string_form["maxLength"] == MAX_DISCLOSURE_TEXT
    # The runtime accepts what the schema advertises.
    assert cfg("Some text").disclosure is not None
