# SPDX-License-Identifier: Apache-2.0
"""The business inquiry form behind the media kit (issues #492 and #429).

The form lives on the gated page only. The owner's address and key fingerprint
come from ``kit.inquiry`` in the config. Everything here is synthetic.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_kit_build import CANARY, build, gated_doc, make_config

from reach_around.config.kit import (
    DISPLAY_POLICY,
    KitConfig,
    gated_projection,
    public_projection,
    visibility,
)
from reach_around.kit.stage import stage_page
from reach_around.site.kit import KitLeakError

OWNER = "owner@example.com"
FPR_RAW = "0123456789abcdef0123456789abcdef01234567"
FPR_SHOWN = "0123 4567 89AB CDEF 0123 4567 89AB CDEF 0123 4567"


def inquiry(**kw: Any) -> dict[str, Any]:
    return {"email": OWNER, "pgp_fingerprint": FPR_RAW, **kw}


def with_inquiry(**kw: Any) -> dict[str, Any]:
    doc = gated_doc()
    doc["inquiry"] = inquiry(**kw)
    return doc


def pages(tmp_path: Path, kit: dict[str, Any]) -> tuple[Path, Path, str, str]:
    out, gated = build(tmp_path, make_config(kit))
    return (
        out,
        gated,
        (out / "kit" / "index.html").read_text(encoding="utf-8"),
        (gated / "kit.html").read_text(encoding="utf-8"),
    )


class _Form(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, str | None]] = []
        self.controls: dict[str, dict[str, str | None]] = {}
        self.labels: dict[str, str] = {}
        self.tags: list[str] = []
        self._label_for: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        self.tags.append(tag)
        if tag == "form" and "kit-inquiry-form" in (a.get("class") or ""):
            self.forms.append(a)
        if tag in ("input", "textarea", "button") and a.get("name"):
            self.controls[a["name"] or ""] = {"tag": tag, **a}
        self._label_for = a.get("for") if tag == "label" else None

    def handle_data(self, data: str) -> None:
        if self._label_for:
            self.labels[self._label_for] = self.labels.get(self._label_for, "") + data

    def handle_endtag(self, tag: str) -> None:
        if tag == "label":
            self._label_for = None


def form_of(html: str) -> _Form:
    parser = _Form()
    parser.feed(html)
    return parser


# --- the config ---------------------------------------------------------------


def test_the_inquiry_block_is_gated_policy() -> None:
    assert visibility("inquiry") == "gated"
    assert "inquiry" in DISPLAY_POLICY


def test_the_projections_split_it_by_audience() -> None:
    kit = KitConfig.model_validate(with_inquiry())
    assert "inquiry" not in public_projection(kit, None)
    assert gated_projection(kit, None)["inquiry"].email == OWNER


def test_an_inquiry_alone_counts_as_gated_content() -> None:
    doc = {
        "title": "t",
        "about": "a",
        "inquiry": inquiry(),
    }
    assert KitConfig.model_validate(doc).has_gated_content


def test_the_inquiry_needs_the_kit_gate_feature() -> None:
    with pytest.raises(ValidationError, match=r"features\.kit_gate"):
        make_config(with_inquiry(), features={"kit_gate": False})
    assert make_config(with_inquiry()).features.kit_gate


def test_the_fingerprint_is_shown_in_groups_of_four_upper_case() -> None:
    kit = KitConfig.model_validate(with_inquiry())
    assert kit.inquiry is not None
    assert kit.inquiry.pgp_fingerprint == FPR_SHOWN
    spaced = KitConfig.model_validate(with_inquiry(pgp_fingerprint=FPR_SHOWN.lower()))
    assert spaced.inquiry is not None and spaced.inquiry.pgp_fingerprint == FPR_SHOWN
    v6 = "ab" * 32
    long = KitConfig.model_validate(with_inquiry(pgp_fingerprint=v6))
    assert long.inquiry is not None
    assert long.inquiry.pgp_fingerprint.replace(" ", "") == v6.upper()


def test_the_fingerprint_is_optional() -> None:
    kit = KitConfig.model_validate({**gated_doc(), "inquiry": {"email": OWNER}})
    assert kit.inquiry is not None and kit.inquiry.pgp_fingerprint is None


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "0123",
        FPR_RAW[:-1],
        FPR_RAW + "0",
        "g" * 40,
        FPR_RAW[:20] + "\n" + FPR_RAW[20:],
        "0x" + FPR_RAW,
        FPR_RAW + "<b>",
    ],
)
def test_bad_fingerprints_are_refused(bad: str) -> None:
    with pytest.raises(ValidationError):
        KitConfig.model_validate(with_inquiry(pgp_fingerprint=bad))


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "owner",
        "owner@",
        "@example.com",
        "owner@example",
        "two@@example.com",
        " owner@example.com",
        "owner@example.com ",
        "ow ner@example.com",
        "<b>@example.com",
        "owner@exa<mple.com",
        "owner@example.com\nBcc: x@example.com",
        "mailto:owner@example.com",
        "a" * 250 + "@example.com",
    ],
)
def test_bad_owner_addresses_are_refused(bad: str) -> None:
    with pytest.raises(ValidationError):
        KitConfig.model_validate(with_inquiry(email=bad))


def test_unknown_inquiry_keys_are_refused() -> None:
    with pytest.raises(ValidationError):
        KitConfig.model_validate(with_inquiry(phone="555"))


# --- the pages -----------------------------------------------------------------


def test_the_gated_page_has_the_form_and_the_owner_details(tmp_path: Path) -> None:
    _, _, _, gated = pages(tmp_path, with_inquiry())
    parsed = form_of(gated)
    assert len(parsed.forms) == 1
    form = parsed.forms[0]
    assert form["method"] == "post"
    assert form["action"] == "/k/inquiry"
    assert OWNER in gated
    assert FPR_SHOWN in gated
    assert 'href="mailto' not in gated


def test_the_form_fields_have_labels_and_the_size_caps(tmp_path: Path) -> None:
    _, _, _, gated = pages(tmp_path, with_inquiry())
    c = form_of(gated).controls
    assert "required" in c["name"] and c["name"]["maxlength"] == "80"
    assert c["email"]["type"] == "email" and c["email"]["maxlength"] == "254"
    assert "required" not in c["email"]
    assert c["brand"]["maxlength"] == "80"
    assert c["budget"]["maxlength"] == "40"
    assert c["details"]["tag"] == "textarea" and c["details"]["maxlength"] == "800"
    assert c["website"]["tabindex"] == "-1" and c["website"]["autocomplete"] == "off"
    labels = form_of(gated).labels
    for control in ("name", "email", "brand", "budget", "details"):
        assert labels[c[control]["id"] or ""].strip(), control


def test_the_form_page_has_no_script_and_no_remote_request(tmp_path: Path) -> None:
    _, _, _, gated = pages(tmp_path, with_inquiry())
    lowered = gated.lower()
    assert "<script" not in lowered
    assert "cf-turnstile" not in lowered
    assert not re.search(r"\son[a-z]+=", lowered)
    assert "http://" not in gated


def test_nothing_of_it_is_on_the_public_half(tmp_path: Path) -> None:
    out, _, public, _ = pages(tmp_path, with_inquiry())
    retry = (out / "kit" / "retry" / "index.html").read_text(encoding="utf-8")
    texts = {
        p.relative_to(out).as_posix(): p.read_text(encoding="utf-8", errors="ignore")
        for p in out.rglob("*")
        if p.is_file()
    }
    for where, text in {"kit": public, "retry": retry, **texts}.items():
        for needle in (OWNER, FPR_SHOWN, FPR_RAW, "/k/inquiry"):
            assert needle not in text, (where, needle)
        if not where.endswith(
            ".css"
        ):  # the shared stylesheet names the classes, nothing more
            assert "kit-inquiry" not in text, where


def test_a_kit_without_the_block_has_no_form(tmp_path: Path) -> None:
    _, _, _, gated = pages(tmp_path, gated_doc())
    assert form_of(gated).forms == []
    assert "kit-inquiry" not in gated and "/k/inquiry" not in gated


def test_no_fingerprint_means_no_fingerprint_line(tmp_path: Path) -> None:
    _, _, _, gated = pages(tmp_path, {**gated_doc(), "inquiry": {"email": OWNER}})
    assert OWNER in gated
    assert "fingerprint" not in gated.lower()


def test_the_gated_content_still_renders_beside_it(tmp_path: Path) -> None:
    _, _, _, gated = pages(tmp_path, with_inquiry())
    assert CANARY in gated


def test_an_owner_address_printed_in_the_public_contact_line_is_not_a_leak(
    tmp_path: Path,
) -> None:
    kit = with_inquiry()
    kit["contact"] = f"Brand deals: write to {OWNER}"
    _, _, public, gated = pages(tmp_path, kit)
    assert OWNER in public and OWNER in gated


def test_an_owner_address_in_other_public_text_fails_the_build(tmp_path: Path) -> None:
    kit = with_inquiry()
    kit["about"] = f"Write to {OWNER} for anything."
    with pytest.raises(KitLeakError, match=r"kit\.inquiry\.email"):
        build(tmp_path, make_config(kit))


def test_a_fingerprint_in_public_text_fails_the_build(tmp_path: Path) -> None:
    kit = with_inquiry()
    kit["contact"] = f"Key {FPR_SHOWN}"
    with pytest.raises(KitLeakError, match=r"kit\.inquiry\.pgp_fingerprint"):
        build(tmp_path, make_config(kit))


# --- the private PDF -----------------------------------------------------------


def test_the_pdf_stage_drops_the_form_and_keeps_the_owner_details(
    tmp_path: Path,
) -> None:
    out, gated = build(tmp_path, make_config(with_inquiry()))
    staged = stage_page(gated, out).html
    assert (
        "<form" not in staged and "<input" not in staged and "<textarea" not in staged
    )
    assert "/k/inquiry" not in staged
    assert OWNER in staged and FPR_SHOWN in staged


def test_the_form_caps_equal_the_workers() -> None:
    from reach_around.site.kit import INQUIRY_LIMITS

    src = (
        Path(__file__).resolve().parents[2] / "worker" / "src" / "kit" / "inquiry.ts"
    ).read_text(encoding="utf-8")
    block = re.search(r"INQUIRY_LIMITS = \{(.*?)\} as const", src, re.S)
    assert block is not None
    worker = {
        k: int(v)
        for k, v in re.findall(
            r"^\s*(name|brand|budget|details): (\d+),", block.group(1), re.M
        )
    }
    assert worker == {k: v for k, v in INQUIRY_LIMITS.items() if k != "email"}


def test_the_pdf_must_print_the_owner_details() -> None:
    from reach_around.kit.pdf import _expected_text

    kit = KitConfig.model_validate(with_inquiry())
    expected = dict(_expected_text(kit, None))
    assert expected["kit.inquiry.email"] == OWNER
    assert expected["kit.inquiry.pgp_fingerprint"] == FPR_SHOWN


def test_the_privacy_page_discloses_the_inquiry_only_when_the_form_exists() -> None:
    from reach_around.site.privacy import collected_items

    def text(kit: dict[str, Any]) -> str:
        return " ".join(
            f"{i.what} {i.why} {i.kept}" for i in collected_items(make_config(kit))
        )

    with_form = text(with_inquiry())
    assert "business inquiry form" in with_form
    assert "Not stored by this site" in with_form
    assert "business inquiry form" not in text(gated_doc())
