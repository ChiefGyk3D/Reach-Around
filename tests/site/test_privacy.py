# SPDX-License-Identifier: Apache-2.0
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from assetpaths import asset_file, has_asset
from reach_around.config import SiteConfig
from reach_around.config.privacy import (
    DEFAULT_LEAD_RETENTION_DAYS,
    MAX_LEAD_RETENTION_DAYS,
    RAW_CLICK_KEEP_DAYS,
)
from reach_around.site.build import build_site, manifest_json
from reach_around.site.privacy import Disclosure, collected_items, sets_cookies

TODAY = date(2027, 3, 10)
GOLDEN = Path(__file__).parent / "golden_privacy" / "privacy" / "index.html"
INTRO = "Cookies, sadly not the chocolate chip kind."


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
            },
            {
                "id": "more",
                "title": "More",
                "blocks": [
                    {"id": "two", "title": "Two", "url": "https://example.com/2"}
                ],
            },
        ],
    }
    data.update(extra)
    return SiteConfig.model_validate(data)


def text_of(items: list[Disclosure]) -> str:
    return " ".join(f"{i.what} {i.why} {i.kept}" for i in items)


_CLICK_SCRIPT = re.compile(
    r'<script src="(?:\.\./)?static/click\.[0-9a-f]{20}\.js" defer></script>'
)


def without_click_script(text: str) -> str:
    """Drop the one allowed tag (external, deferred click.js)."""
    return _CLICK_SCRIPT.sub("", text)


def read_all(out: Path) -> str:
    return "\n".join(p.read_text() for p in sorted(out.rglob("*.html")))


def test_default_disclosure_lists_click_counts_and_says_no_ip_address() -> None:
    items = collected_items(make_config())
    assert len(items) == 1
    text = text_of(items)
    assert "no IP address or country" in text
    assert "Hourly records are deleted after 90 days" in text


def test_click_item_says_clicks_honor_gpc_and_dnt() -> None:
    what = collected_items(make_config())[0].what
    assert (
        "The site honors Global Privacy Control and Do Not Track for link clicks. "
        "If your browser sends either signal, the click is not counted or "
        "stored, and the site's own script sends nothing. If scripts are blocked "
        "or fail, your browser may still send a small request to the site. The "
        "server throws it away without storing anything about it. The link "
        "still opens normally."
    ) in what
    # Truthful: the page never says the visitor's browser sends nothing.
    assert "Nothing about it is sent" not in what
    assert "do not stop click counting" not in what
    assert "just as it does for page views" not in what


def test_page_promise_matches_click_code_in_both_places() -> None:
    """The page says clicks honor the signal only if both click paths check it."""
    what = collected_items(make_config())[0].what
    promised = "honors Global Privacy Control and Do Not Track for link clicks" in what
    click_js = (STATIC / "click.js").read_text(encoding="utf-8")
    click_ts = (WORKER / "click.ts").read_text(encoding="utf-8")
    optout_ts = (WORKER / "optout.ts").read_text(encoding="utf-8")
    assert click_js and click_ts and optout_ts  # the guard collected something
    assert promised == (
        checks_signal_js(click_js) and checks_signal_ts(click_ts, optout_ts)
    )
    assert promised


def checks_signal_js(source: str) -> bool:
    """The signals are read AND the result is used before anything is sent."""
    return (
        "navigator.globalPrivacyControl" in source
        and "navigator.doNotTrack" in source
        and "if (optedOutSafe())" in source
        and source.index("if (optedOutSafe())") < source.index("navigator.sendBeacon(")
    )


def checks_signal_ts(source: str, optout: str) -> bool:
    """Both headers are read in the one shared parser AND the click handler uses it."""
    return (
        'get("Sec-GPC")' in optout
        and 'get("DNT")' in optout
        and 'from "./optout"' in source
        and "optedOut(request)" in source
    )


def test_the_signal_guard_bites_on_the_real_files() -> None:
    click_js = (STATIC / "click.js").read_text(encoding="utf-8")
    click_ts = (WORKER / "click.ts").read_text(encoding="utf-8")
    optout_ts = (WORKER / "optout.ts").read_text(encoding="utf-8")
    assert checks_signal_js(click_js)
    assert checks_signal_ts(click_ts, optout_ts)
    for old, new in (
        ("navigator.globalPrivacyControl", "navigator.zzGpc"),
        ("navigator.doNotTrack", "navigator.zzDnt"),
        ("if (optedOutSafe())", "if (false)"),
    ):
        assert old in click_js
        assert not checks_signal_js(click_js.replace(old, new)), old
    for old, new in (
        ('get("Sec-GPC")', 'get("X-Zz")'),
        ('get("DNT")', 'get("X-Zz")'),
    ):
        assert old in optout_ts
        assert not checks_signal_ts(click_ts, optout_ts.replace(old, new)), old
    for old, new in (
        ('from "./optout"', 'from "./zz"'),
        ("optedOut(request)", "false"),
    ):
        assert old in click_ts
        assert not checks_signal_ts(click_ts.replace(old, new), optout_ts), old
    # The page-view and beacon routes use the same shared parser, not a copy.
    for name in ("view.ts", "beacon.ts"):
        route = (WORKER / name).read_text(encoding="utf-8")
        assert 'from "./optout"' in route and "optedOut(request)" in route, name
        assert 'get("Sec-GPC")' not in route and 'get("DNT")' not in route, name


def test_click_disclosure_says_none_vs_other_and_keeps_totals_apart() -> None:
    what = collected_items(make_config())[0].what
    assert "outside the creator’s fixed list count as “other.”" in what
    assert "Missing or unusable labels count as “none.”" in what
    assert "Source and country are never combined" in what


WORKER = Path(__file__).resolve().parents[2] / "worker" / "src"
STATIC = (
    Path(__file__).resolve().parents[2] / "src" / "reach_around" / "site" / "static"
)


def purge_call_arguments(source: str) -> list[int]:
    """Argument counts of every purgeOldEvents( ... ) call in the source."""
    counts = []
    for args in re.findall(r"purgeOldEvents\(([^)]*)\)", source):
        # An object literal such as { ...env, DB: whole } is one argument.
        args = re.sub(r"\{[^}]*\}", "obj", args)
        counts.append(len(args.split(",")))
    return counts


def test_raw_click_retention_is_what_the_worker_really_does() -> None:
    retention = (WORKER / "retention.ts").read_text(encoding="utf-8")
    default = re.search(r"DEFAULT_KEEP_DAYS = (\d+);", retention)
    assert default  # the guard collected something
    assert int(default.group(1)) == RAW_CLICK_KEEP_DAYS == 90
    # The cron job runs the purge with no window of its own, so the default
    # is the whole story. A third argument would change what is kept.
    counts = purge_call_arguments((WORKER / "index.ts").read_text(encoding="utf-8"))
    assert counts and set(counts) == {2}, counts


def test_the_purge_guard_bites_a_call_with_its_own_window() -> None:
    assert purge_call_arguments("purgeOldEvents({ ...env }, now, 30)") == [3]
    assert purge_call_arguments("await purgeOldEvents({ ...env }, now);") == [2]


@pytest.mark.parametrize("bad", [1, 30, 89, 91, 3650])
def test_click_retention_other_than_the_workers_is_refused(bad: int) -> None:
    with pytest.raises(ValidationError, match="retention_days"):
        make_config(privacy={"retention_days": bad})


def test_click_retention_accepts_the_workers_value() -> None:
    assert make_config(privacy={"retention_days": 90}).privacy.retention_days == 90


def test_click_retention_says_hourly_daily_and_monthly() -> None:
    kept = collected_items(make_config())[0].kept
    assert f"Hourly records are deleted after {RAW_CLICK_KEEP_DAYS} days." in kept
    assert "Daily counts are deleted after 90 days." in kept
    assert "Monthly totals are kept for up to 25 months." in kept


FORBIDDEN_WORDS = (
    "network type",
    "hosting provider",
    "VPN",
    "Tor",
    "landing",
    "hop",
    "YouTube",  # the live block's players are Twitch and Kick only
)
ALERTS = {"channels": ["discord"], "events": ["kit_unlocked"]}
LIVE_BLOCK = {
    "id": "live",
    "players": [
        {"platform": "kick", "channel": "examplekick"},
        {"platform": "twitch", "channel": "exampletwitch"},
    ],
}


def every_feature_config() -> SiteConfig:
    return make_config(
        features={"kit_gate": True},
        analytics={"page_views": True, "other_sites": ["blog.example.test"]},
        alerts=ALERTS,
        tabs=[{"id": "start", "title": "Start", "blocks": [LIVE_BLOCK]}],
    )


def has_word(word: str, text: str) -> bool:
    return re.search(rf"\b{word}\b", text) is not None


def test_page_never_names_what_is_not_collected(tmp_path: Path) -> None:
    config = every_feature_config()
    items = collected_items(config)
    assert len(items) >= 7  # loose collection: the whole page was read
    build_site(config, tmp_path, today=TODAY)
    page = (tmp_path / "privacy" / "index.html").read_text()
    body = re.sub(r"<[^>]+>", " ", text_of(items) + " " + page)
    for word in FORBIDDEN_WORDS:
        assert not has_word(word, body), word


def test_the_forbidden_word_guard_bites() -> None:
    assert has_word("VPN", "looks like a VPN or proxy")
    assert has_word("hop", "one hop label")
    assert has_word("network type", "a network type")
    assert not has_word("hop", "shopping")  # whole words only


def test_items_come_in_order_and_each_follows_its_flag() -> None:
    assert len(collected_items(make_config())) == 1
    heads = [i.what for i in collected_items(every_feature_config())]
    assert heads[0].startswith("When you click a link")
    assert "The same counter also counts" in heads[1]
    assert heads[2].startswith("When the live block is on")
    assert heads[3].startswith("When the live block is on, the site can show a Twitch")
    assert heads[4].startswith("When alerts are on")
    assert "email address you enter" in heads[5]


def test_live_and_player_items_follow_the_live_block_only() -> None:
    on = text_of(collected_items(every_feature_config()))
    assert "whether the creator is live" in on
    assert "The player loads only after you click to play." in on
    assert "Twitch or Kick stream player" in on
    no_live = make_config(
        features={"kit_gate": True}, analytics={"page_views": True}, alerts=ALERTS
    )
    off = text_of(collected_items(no_live))
    assert "whether the creator is live" not in off
    assert "player" not in off


def test_alerts_item_follows_the_alerts_section_only() -> None:
    on = text_of(collected_items(every_feature_config()))
    assert "When alerts are on" in on
    assert "are not deleted automatically by this site" in on
    assert "When alerts are on" not in text_of(collected_items(make_config()))
    # A channel with no event is refused (#779), so no half-enabled state exists.
    with pytest.raises(ValidationError):
        make_config(alerts={"channels": ["discord"]})


def test_alerts_item_says_an_alert_never_carries_an_ip_address() -> None:
    items = collected_items(every_feature_config())
    alerts = [i for i in items if i.what.startswith("When alerts are on")]
    assert len(alerts) == 1
    what = alerts[0].what
    assert "It never includes an IP address" in what
    assert "can include your full or masked IP" not in what
    # The Worker's own list of what an alert never carries (the source of truth).
    fmt = (WORKER / "alerts" / "format.ts").read_text(encoding="utf-8")
    assert "Never an IP, user" in fmt


def test_page_views_item_follows_analytics_page_views_only() -> None:
    assert "counter counts" not in text_of(collected_items(make_config()))
    on = make_config(analytics={"page_views": True})
    assert "This counter counts this site only." in text_of(collected_items(on))


def test_no_item_is_marked_sensitive() -> None:
    config = make_config(features={"kit_gate": True})
    assert collected_items(config)
    assert not any(item.sensitive for item in collected_items(config))


def test_disclosure_never_marks_collected_items_sensitive(tmp_path: Path) -> None:
    for features in ({}, {"kit_gate": True}, {"click_tracking": False}):
        config = make_config(features=features)
        assert not any(i.sensitive for i in collected_items(config))
    build_site(make_config(features={"kit_gate": True}), tmp_path, today=TODAY)
    page = (tmp_path / "privacy" / "index.html").read_text()
    assert "This is not sensitive data." in page
    assert "This is sensitive data." not in page


def test_no_cookies_when_gate_off(tmp_path: Path) -> None:
    config = make_config()
    assert sets_cookies(config) is False
    build_site(config, tmp_path, today=TODAY)
    text = read_all(tmp_path)
    assert "document.cookie" not in text
    assert "cookie-banner" not in text
    assert "Reject" not in text


def test_gate_on_adds_email_cookie_items_and_banner_with_preferences_reject_accept(
    tmp_path: Path,
) -> None:
    config = make_config(
        features={"kit_gate": True}, privacy={"lead_retention_days": 200}
    )
    assert sets_cookies(config) is True
    items = collected_items(config)
    gate_text = text_of(items[1:])
    assert "email" in gate_text
    assert "200 days" in gate_text
    assert "cookie" in gate_text and "remembers" in gate_text
    build_site(config, tmp_path, today=TODAY)
    for page in (tmp_path / "index.html", tmp_path / "more" / "index.html"):
        html = page.read_text()
        assert "cookie-banner" in html
        for label in ("Preferences", "Reject", "Accept"):
            assert re.search(rf"<button[^>]*>{label}</button>", html)
        form = re.search(r"<form[^>]*>", html)
        assert form and "privacy/" in form.group(0)
    privacy = (tmp_path / "privacy" / "index.html").read_text()
    assert "200 days" in privacy and "email" in privacy


def test_banner_text_comes_from_config_when_set(tmp_path: Path) -> None:
    config = make_config(features={"kit_gate": True}, privacy={"intro": INTRO})
    build_site(config, tmp_path, today=TODAY)
    assert INTRO in (tmp_path / "privacy" / "index.html").read_text()
    assert INTRO in (tmp_path / "index.html").read_text()


def test_every_page_links_to_privacy_in_the_footer(tmp_path: Path) -> None:
    build_site(make_config(), tmp_path, today=TODAY)
    pages = sorted(tmp_path.rglob("*.html"))
    assert len(pages) == 4  # two tabs, privacy and 404
    for page in pages:
        html = page.read_text()
        href = re.search(r'<footer>.*<a href="([^"]*privacy/)"', html, re.S)
        assert href, page
        # 404.html is root-relative (it is served at any depth); the rest are not.
        base = tmp_path if href.group(1).startswith("/") else page.parent
        assert (base / href.group(1).lstrip("/")).resolve() == (
            tmp_path / "privacy"
        ).resolve()


def test_privacy_page_states_the_workers_click_and_the_configured_lead_numbers(
    tmp_path: Path,
) -> None:
    config = make_config(
        features={"kit_gate": True},
        privacy={"retention_days": 90, "lead_retention_days": 45},
    )
    build_site(config, tmp_path, today=TODAY)
    page = (tmp_path / "privacy" / "index.html").read_text()
    assert "Hourly records are deleted after 90 days" in page  # the Worker's own
    assert "Daily counts are deleted after 90 days" in page
    assert "45 days" in page  # the lead number does come from config
    assert "365 days" not in page


def test_default_lead_retention_prints_the_workers_default_30_days(
    tmp_path: Path,
) -> None:
    config = make_config(features={"kit_gate": True})
    assert config.privacy.lead_retention_days == 30
    build_site(config, tmp_path, today=TODAY)
    page = (tmp_path / "privacy" / "index.html").read_text()
    assert "30 days" in page and "365 days" not in page


def test_lead_retention_45_prints_45_and_lands_in_the_manifest(
    tmp_path: Path,
) -> None:
    config = make_config(
        features={"kit_gate": True}, privacy={"lead_retention_days": 45}
    )
    manifest = build_site(config, tmp_path, today=TODAY)
    page = (tmp_path / "privacy" / "index.html").read_text()
    assert "45 days" in page
    assert manifest.lead_retention_days == 45
    assert json.loads(manifest_json(manifest))["lead_retention_days"] == 45


@pytest.mark.parametrize("bad", [0, -1, 366, 3650, "30", True, 1.5, None])
def test_lead_retention_outside_the_workers_range_is_refused(bad: Any) -> None:
    with pytest.raises(ValidationError, match="lead_retention_days"):
        make_config(privacy={"lead_retention_days": bad})


@pytest.mark.parametrize("ok", [1, 30, 365])
def test_lead_retention_range_ends_are_accepted(ok: int) -> None:
    assert (
        make_config(privacy={"lead_retention_days": ok}).privacy.lead_retention_days
        == ok
    )


def test_config_limits_equal_the_workers_constants() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "worker"
        / "src"
        / "kit"
        / "leadretention.ts"
    ).read_text(encoding="utf-8")
    default = re.search(r"DEFAULT_LEAD_RETENTION_DAYS = (\d+);", source)
    maximum = re.search(r"MAX_LEAD_RETENTION_DAYS = (\d+);", source)
    assert default and maximum  # the guard collected something
    assert int(default.group(1)) == DEFAULT_LEAD_RETENTION_DAYS == 30
    assert int(maximum.group(1)) == MAX_LEAD_RETENTION_DAYS == 365


def test_disabling_click_tracking_removes_the_click_item(tmp_path: Path) -> None:
    config = make_config(features={"click_tracking": False})
    assert collected_items(config) == []
    build_site(config, tmp_path, today=TODAY)
    page = (tmp_path / "privacy" / "index.html").read_text()
    assert "click" not in page.lower()
    assert "collects nothing" in page


def test_privacy_page_has_no_scripts_and_escapes_the_intro(tmp_path: Path) -> None:
    evil = '<script>alert(1)</script> "q"'
    config = make_config(
        features={"kit_gate": True},
        privacy={"intro": evil, "contact": "<b>me</b>@example.com"},
    )
    build_site(config, tmp_path, today=TODAY)
    text = read_all(tmp_path)
    assert "<script" not in without_click_script(text).lower()
    assert "&lt;script&gt;" in (tmp_path / "privacy" / "index.html").read_text()
    assert "<b>" not in text


def test_unknown_privacy_keys_and_bad_retention_are_refused() -> None:
    with pytest.raises(ValueError):
        make_config(privacy={"retention_days": 0})
    with pytest.raises(ValueError):
        make_config(features={"tracking": True})


def test_golden_privacy_page(tmp_path: Path, update_golden: bool) -> None:
    config = make_config(
        features={"kit_gate": True},
        privacy={"contact": "privacy@example.com"},
    )
    build_site(config, tmp_path, today=TODAY)
    built = (tmp_path / "privacy" / "index.html").read_bytes()
    if update_golden:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_bytes(built)
    assert GOLDEN.is_file(), "no golden file; run pytest --update-golden"
    assert built == GOLDEN.read_bytes()


def test_click_tracking_off_removes_ping_and_the_click_script(tmp_path: Path) -> None:
    config = make_config(features={"click_tracking": False})
    build_site(config, tmp_path, today=TODAY)
    text = read_all(tmp_path)
    assert "<script" not in text.lower()
    assert "ping=" not in text and "data-link-id" not in text
    assert not has_asset(tmp_path, "static/click.js")
    assert not list((tmp_path / "static").glob("click*"))


def test_click_tracking_on_adds_ping_and_exactly_one_script_per_page(
    tmp_path: Path,
) -> None:
    build_site(make_config(), tmp_path, today=TODAY)
    pages = sorted(tmp_path.rglob("*.html"))
    assert pages
    for page in pages:
        # The 404 page is never click-counted, so it has no script at all.
        expected = 0 if page.name == "404.html" else 1
        assert page.read_text().lower().count("<script") == expected, page
    assert 'ping="/e/c/one"' in (tmp_path / "index.html").read_text()
    assert asset_file(tmp_path, "static/click.js").is_file()
