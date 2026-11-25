# SPDX-License-Identifier: Apache-2.0
"""The fixed privacy notice and the gate form notice (issues #777 and #780).

The copy is owner-approved and used verbatim: only its {placeholders} are filled.
Everything here is synthetic.
"""

from __future__ import annotations

import html
import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_kit_build import build as build_kit
from test_kit_build import make_config as make_kit_config

from reach_around.config import SiteConfig
from reach_around.config.analytics import AGGREGATE_KEEP_DAYS
from reach_around.config.privacy import (
    DEFAULT_GEO_LEVEL,
    MONTHLY_KEEP_MONTHS,
    RAW_CLICK_KEEP_DAYS,
    Privacy,
)
from reach_around.site.build import build_site
from reach_around.site.privacy import (
    GATE_NOTICE_TEMPLATE,
    NOTICE_TEMPLATE,
    NoticeError,
    _fill,
    gate_notice_parts,
    notice_blocks,
    notice_markdown,
)

TODAY = date(2027, 3, 10)
ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = "Example Creator LLC"
CONTACT = "example.com/contact"
# Every placeholder name the approved copy uses (BLOCK A and BLOCK B).
PLACEHOLDERS = (
    "controller_name",
    "contact",
    "geo_level",
    "raw_view_days",
    "raw_view_max_days",
    "click_days",
    "daily_totals_days",
    "monthly_totals_months",
    "alert_providers",
    "turnstile_storage_note",
    "dpa_note",
    "lead_days",
    "privacy_link",
)
LEFTOVER = re.compile(r"\{(?:" + "|".join(PLACEHOLDERS) + r")\}")
ALERTS = {"channels": ["discord", "email"], "events": ["kit_unlocked"]}


def make_config(**extra: Any) -> SiteConfig:
    data: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": [
            {
                "id": "start",
                "title": "Start",
                "blocks": [
                    {"id": "one", "title": "One", "url": "https://example.com/1"}
                ],
            }
        ],
        "privacy": {"controller_name": CONTROLLER, "contact": CONTACT},
    }
    data.update(extra)
    return SiteConfig.model_validate(data)


class _Section(HTMLParser):
    """Rebuild the notice Markdown from the rendered section."""

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.tag = ""
        self.lines: list[str] = []
        self.text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "section" and ("id", "privacy-notice") in attrs:
            self.depth = 1
        elif self.depth and tag in {"h3", "p", "li"}:
            self.tag, self.text = tag, ""

    def handle_data(self, data: str) -> None:
        if self.depth and self.tag:
            self.text += data

    def handle_endtag(self, tag: str) -> None:
        if self.depth and tag == self.tag:
            prefix = {"h3": "## ", "li": "- ", "p": ""}[tag]
            self.lines.append((prefix + html.unescape(self.text)).rstrip())
            self.tag = ""
        elif self.depth and tag == "section":
            self.depth = 0


def rendered_notice(page: str) -> str:
    parser = _Section()
    parser.feed(page)
    out: list[str] = []
    for line in parser.lines:
        # A blank line separates blocks, except between bullets of one list.
        if out and not (line.startswith("- ") and out[-1].startswith("- ")):
            out.append("")
        out.append(line)
    return "\n".join(out) + "\n"


def substituted(template: str, values: dict[str, str]) -> str:
    out = template
    for name, value in values.items():
        out = out.replace("{" + name + "}", value)
    return out


def privacy_page(tmp_path: Path, config: SiteConfig) -> str:
    build_site(config, tmp_path, today=TODAY)
    return (tmp_path / "privacy" / "index.html").read_text()


def test_the_rendered_section_equals_the_approved_copy_with_values_filled(
    tmp_path: Path,
) -> None:
    config = make_config(
        features={"kit_gate": True},
        alerts=ALERTS,
        analytics={"raw_keep_days": 10},
        privacy={
            "controller_name": CONTROLLER,
            "contact": CONTACT,
            "dpa_note": "Our own DPA note.",
            "turnstile_storage_note": "Our own storage note.",
        },
    )
    values = {
        "controller_name": CONTROLLER,
        "contact": CONTACT,
        "geo_level": DEFAULT_GEO_LEVEL,
        "raw_view_days": "10",
        "raw_view_max_days": "30",
        "click_days": str(RAW_CLICK_KEEP_DAYS),
        "daily_totals_days": str(AGGREGATE_KEEP_DAYS),
        "monthly_totals_months": str(MONTHLY_KEEP_MONTHS),
        "alert_providers": "Discord and email",
        "dpa_note": "Our own DPA note.",
        "turnstile_storage_note": "Our own storage note.",
    }
    expected = substituted(NOTICE_TEMPLATE, values)
    # Spot checks that do not go through the template constant.
    assert (
        f"{CONTROLLER} is the controller for this site. For privacy requests, "
        f"contact {CONTACT}." in expected
    )
    assert "Raw view records are kept for 10 days by default." in expected
    assert "up to 30 days." in expected
    assert "Monthly totals are kept for 25 months." in expected
    assert notice_markdown(config) == expected
    # The page carries the same text, paragraph for paragraph.
    page = privacy_page(tmp_path, config)
    assert rendered_notice(page).replace(" \n", "\n") == expected.replace(" \n", "\n")


def test_the_notice_is_on_every_privacy_page_and_cannot_be_turned_off(
    tmp_path: Path,
) -> None:
    page = privacy_page(tmp_path, make_config(features={"click_tracking": False}))
    assert 'id="privacy-notice"' in page
    assert "<h3>Who we are</h3>" in page
    assert "<h3>About this notice</h3>" in page


def test_a_site_with_no_gate_and_no_alerts_may_leave_the_controller_unset(
    tmp_path: Path,
) -> None:
    config = SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "Example Creator"},
            "tabs": [
                {
                    "id": "start",
                    "title": "Start",
                    "blocks": [
                        {"id": "one", "title": "One", "url": "https://example.com/1"}
                    ],
                }
            ],
        }
    )
    text = notice_markdown(config)
    assert "the site owner is the controller for this site." in text
    assert not LEFTOVER.search(text)


def test_alerts_off_drops_the_alert_sentence_and_on_names_the_providers() -> None:
    off = notice_markdown(make_config())
    assert "chosen alert services" not in off
    assert (
        "Cloudflare provides hosting, database services, rate limiting, and "
        "the bot check.\n" in off
    )
    on = notice_markdown(make_config(alerts=ALERTS))
    assert "chosen alert services, Discord and email, receive notices" in on


def test_notes_default_to_empty_and_leave_no_trailing_space() -> None:
    assert Privacy().dpa_note == "" and Privacy().turnstile_storage_note == ""
    for block in notice_blocks(make_config()):
        assert block.text == block.text.rstrip()
        assert all(item == item.rstrip() for item in block.items)


def test_the_gate_form_notice_is_next_to_the_email_field(tmp_path: Path) -> None:
    out, _ = build_kit(tmp_path)
    for rel, link in (
        ("kit/index.html", "../privacy/"),
        ("kit/retry/index.html", "../../privacy/"),
    ):
        page = (out / rel).read_text()
        form = page[page.index('<form class="kit-gate"') :]
        form = form[: form.index("</form>")]
        assert 'aria-describedby="kit-gate-notice"' in form
        assert 'id="kit-gate-notice"' in form
        expected_head = (
            "Your address is used only to deliver the media kit and reply to you. "
            "It is stored for 30 days, then deleted. It is not verified when "
            "entered, shared, sold, or used for marketing. You may request "
            f"deletion at any time at {CONTACT}. This form uses a bot check and "
            "sets a secure 24-hour unlock cookie with no analytics. Privacy "
            f'details: <a href="{link}">privacy page</a>.</p>'
        )
        assert expected_head in form
        # The notice follows the email field and comes before the submit button.
        assert (
            form.index('name="email"')
            < form.index("kit-gate-notice")
            < form.index("kit-submit")
        )


def test_no_placeholder_is_left_on_any_generated_page(tmp_path: Path) -> None:
    config = make_kit_config(
        alerts=ALERTS,
        analytics={"page_views": True},
        privacy={
            "controller_name": CONTROLLER,
            "contact": CONTACT,
            "dpa_note": "Note.",
            "turnstile_storage_note": "Note.",
        },
    )
    out, gated = build_kit(tmp_path, config)
    pages = [*out.rglob("*.html"), *gated.rglob("*.html")]
    assert any(p.name == "index.html" and p.parent.name == "privacy" for p in pages)
    for page in pages:
        found = LEFTOVER.findall(page.read_text())
        assert found == [], (page, found)


def test_the_leftover_scan_catches_a_placeholder() -> None:
    assert LEFTOVER.search("Privacy details: {privacy_link}.")
    assert not LEFTOVER.search("Privacy details: ../privacy/.")


def test_the_template_has_no_placeholder_outside_the_known_list() -> None:
    names = set(re.findall(r"\{([a-z_]+)\}", NOTICE_TEMPLATE + GATE_NOTICE_TEMPLATE))
    assert names <= set(PLACEHOLDERS)


def test_a_placeholder_without_a_value_fails_the_build() -> None:
    with pytest.raises(NoticeError, match="no value for: mystery"):
        _fill("Hello {mystery}", {"contact": "x"})


def test_a_value_with_braces_is_never_read_as_a_placeholder() -> None:
    assert _fill("Hi {contact}.", {"contact": "{lead_days}"}) == "Hi {lead_days}."


@pytest.mark.parametrize(
    ("features", "alerts"),
    [({"kit_gate": True}, {}), ({}, ALERTS)],
    ids=["gate", "alerts"],
)
@pytest.mark.parametrize(
    ("privacy", "named"),
    [
        ({"contact": "secret-contact-value"}, "privacy.controller_name"),
        ({"controller_name": "Secret Name Value"}, "privacy.contact"),
        ({}, "privacy.controller_name and privacy.contact"),
    ],
)
def test_the_config_refuses_a_missing_controller_or_contact(
    features: dict[str, Any],
    alerts: dict[str, Any],
    privacy: dict[str, Any],
    named: str,
) -> None:
    with pytest.raises(ValidationError) as caught:
        make_config(features=features, alerts=alerts, privacy=privacy)
    message = str(caught.value)
    assert named in message
    assert "secret-contact-value" not in message
    assert "Secret Name Value" not in message


def test_the_build_refuses_too_even_when_validation_was_skipped(
    tmp_path: Path,
) -> None:
    ok = make_config(features={"kit_gate": True})
    broken = ok.model_copy(update={"privacy": Privacy(controller_name="Secret Name")})
    with pytest.raises(NoticeError, match=r"privacy\.contact") as caught:
        build_site(broken, tmp_path, today=TODAY)
    assert "Secret Name" not in str(caught.value)


def test_gate_notice_parts_is_none_when_the_gate_is_off() -> None:
    assert gate_notice_parts(make_config()) is None


def test_the_new_privacy_fields_are_plain_text_only() -> None:
    for field in ("controller_name", "contact", "dpa_note", "turnstile_storage_note"):
        with pytest.raises(ValidationError):
            Privacy.model_validate({field: "bad‮text"})
        with pytest.raises(ValidationError):
            Privacy.model_validate({field: "two\nlines"})


def test_the_retention_numbers_come_from_the_workers_constants() -> None:
    source = (ROOT / "worker" / "src" / "clickfunnel.ts").read_text(encoding="utf-8")
    match = re.search(r"export const MONTHS_KEPT = (\d+);", source)
    assert match is not None
    assert int(match.group(1)) == MONTHLY_KEEP_MONTHS
    retention = (ROOT / "worker" / "src" / "retention.ts").read_text(encoding="utf-8")
    assert re.search(rf"DEFAULT_KEEP_DAYS\s*=\s*{RAW_CLICK_KEEP_DAYS}\b", retention)
    view = (ROOT / "worker" / "src" / "view.ts").read_text(encoding="utf-8")
    assert f'"{DEFAULT_GEO_LEVEL}") as GeoLevel' in view
