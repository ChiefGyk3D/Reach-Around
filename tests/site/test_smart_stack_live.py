# SPDX-License-Identifier: Apache-2.0
"""LIVE / OFFLINE badges on the two Watch links of the smart stack (#673).

The page is complete and correct with no script: the schedule line and two equal
Watch links are in the HTML, and the badges ship empty and hidden. With
``page.live_badges: true`` the build adds the one pinned live script, which fills
the badges from the same-origin ``/e/live`` document. The checks here collect
loosely (every badge and link on the page) and assert strictly (exactly two, with
one shape), and each has a negative case proving it bites. The script's logic runs
under Node in ``live_logic.mjs``; the browser behavior is in
``test_smart_stack_live_visual.py``.
"""

from __future__ import annotations

import copy
import re
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_live_logic import node_or_none, run_node, stage
from test_smart_stack import raw as stack_raw

from assetpaths import asset
from reach_around.config import SiteConfig
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
GOLDEN = Path(__file__).parent / "golden_smart_stack_live" / "index.html"
NAMES = ("twitch", "kick")


def raw(*, badges: bool = True) -> dict[str, Any]:
    data = stack_raw()
    if badges:
        data["page"]["live_badges"] = True
    return data


def build(tmp_path: Path, data: dict[str, Any]) -> tuple[Path, str]:
    out = tmp_path / "out"
    build_site(SiteConfig.model_validate(data), out, today=TODAY)
    return out, (out / "index.html").read_text(encoding="utf-8")


def swapped(data: dict[str, Any]) -> dict[str, Any]:
    """The same site with the two platforms' data exchanged (links, icons, titles)."""
    other = copy.deepcopy(data)
    links = other["tabs"][0]["blocks"]
    for link in links:
        if link["icon"] == "twitch":
            link.update(icon="kick", title="Watch on Kick")
        else:
            link.update(icon="twitch", title="Watch on Twitch")
    return other


def named(data: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(data)
    for link in out["tabs"][0]["blocks"]:
        link["title"] = "Watch on " + str(link["icon"]).title()
    return out


WATCH = re.compile(r'<div class="smart-watch"[^>]*>.*?</div>', re.DOTALL)
BADGE = re.compile(r"<span[^>]*data-watch-badge[^>]*></span>")
LINK = re.compile(r"<a class=\"link[^\"]*smart-watch-link.*?</a>", re.DOTALL)


def watch_html(html: str) -> str:
    found = WATCH.search(html)
    assert found, "no Watch group on the page"
    return found.group(0)


def check_markup(html: str) -> None:
    """The invariants of the static, scriptless state. Raises on any break."""
    group = watch_html(html)
    links = LINK.findall(group)
    assert len(links) == 2, "the Watch group must hold exactly two links"
    shapes = []
    for link in links:
        badges = BADGE.findall(link)
        assert len(badges) == 1, "each Watch link carries exactly one badge"
        assert "hidden" in badges[0], "a badge ships hidden"
        assert re.search(r"></span>$", badges[0]), "a badge ships empty"
        assert "aria-live" not in link, "aria-live belongs on the one region"
        shapes.append(
            re.sub(
                r'data-watch-badge="[a-z]+"',
                'data-watch-badge="P"',
                re.sub(r'(data-link-id|ping|href)="[^"]*"', r'\1="X"', link),
            )
        )
    assert len(set(re.sub(r"\s+", " ", s) for s in shapes)) == 2  # titles differ
    regions = re.findall(r"<[^>]*data-watch-status[^>]*>", group)
    assert len(regions) == 1, "exactly one status region"
    assert 'aria-live="polite"' in regions[0]
    assert group.count("aria-live") == 1
    assert "aria-hidden" not in regions[0]
    assert "LIVE" not in group and "OFFLINE" not in group, "static state: no status"
    assert sorted(re.findall(r'data-watch-badge="([a-z]+)"', group)) == sorted(NAMES)


def test_scriptless_page_is_complete_and_correct(tmp_path: Path) -> None:
    _out, html = build(tmp_path, raw())
    assert "Next stream: November 13" in html
    assert len(re.findall(r'class="[^"]*\bsmart-watch-link\b', html)) == 2
    assert 'href="https://example.com/watch/1"' in html
    assert 'href="https://example.com/watch/2"' in html
    check_markup(html)


def test_default_has_no_badges_no_region_and_no_script(tmp_path: Path) -> None:
    out, html = build(tmp_path, raw(badges=False))
    assert "data-watch" not in html
    assert "aria-live" not in html
    assert "live." not in "".join(re.findall(r'<script src="([^"]*)"', html))
    assert not list((out / "static").glob("live.*.js"))


def test_the_script_is_the_pinned_same_origin_file_and_deferred(tmp_path: Path) -> None:
    out, html = build(tmp_path, raw())
    tag = re.search(r'<script src="([^"]*live\.[0-9a-f]{20}\.js)"([^>]*)>', html)
    assert tag
    assert "defer" in tag.group(2)
    assert tag.group(1).endswith(asset(out, "static/live.js"))
    assert not re.search(r"<script>", html)
    assert len(re.findall(r"<script", html)) == len(
        re.findall(r'<script src="static/', html)
    )
    loaded = re.findall(r'<script[^>]*\ssrc="([^"]*)"', html) + re.findall(
        r'<link[^>]*\shref="([^"]*)"', html
    )
    loaded += re.findall(r'<img[^>]*\ssrc="([^"]*)"', html)
    assert loaded, "the page loads its own files"
    assert not [url for url in loaded if re.match(r"(https?:)?//", url)], loaded


def test_both_platforms_get_the_same_dom_shape_and_swapping_changes_only_names(
    tmp_path: Path,
) -> None:
    one = named(raw())
    two = swapped(one)
    _a, html_a = build(tmp_path / "a", one)
    _b, html_b = build(tmp_path / "b", two)
    group_a, group_b = watch_html(html_a), watch_html(html_b)
    assert group_a != group_b  # the names did move
    for group in (group_a, group_b):
        assert group.count("data-watch-badge") == 2

    def blind(group: str, first: str, second: str) -> str:
        text = group
        for name, token in ((first, "P1"), (second, "P2")):
            text = re.sub(name, token, text, flags=re.IGNORECASE)
        return text

    # In A the first link is Twitch; in B it is Kick. Naming them by position
    # makes the two groups identical, so only the names differ.
    assert blind(group_a, "twitch", "kick") == blind(group_b, "kick", "twitch")


def test_the_swap_test_bites_when_one_platform_is_special_cased(tmp_path: Path) -> None:
    one = named(raw())
    _a, html_a = build(tmp_path / "a", one)
    _b, html_b = build(tmp_path / "b", swapped(one))
    mutated = html_a.replace(
        '<span class="smart-live-badge" data-watch-badge="kick" hidden></span>',
        '<span class="smart-live-badge x" data-watch-badge="kick" hidden></span>',
    )
    assert mutated != html_a

    def blind(group: str, first: str, second: str) -> str:
        for name, token in ((first, "P1"), (second, "P2")):
            group = re.sub(name, token, group, flags=re.IGNORECASE)
        return group

    assert blind(watch_html(mutated), "twitch", "kick") != blind(
        watch_html(html_b), "kick", "twitch"
    )


def test_markup_matches_golden(tmp_path: Path, update_golden: bool) -> None:
    _out, html = build(tmp_path, raw())
    if update_golden:
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(html, encoding="utf-8")
    assert GOLDEN.is_file(), "no golden file; run pytest --update-golden"
    assert html == GOLDEN.read_text(encoding="utf-8")


def test_the_default_stack_golden_is_unchanged_by_the_feature(tmp_path: Path) -> None:
    _out, html = build(tmp_path, raw(badges=False))
    golden = Path(__file__).parent / "golden_smart_stack" / "index.html"
    assert html == golden.read_text(encoding="utf-8")


# --- the markup guard bites ------------------------------------------------------


def _good(tmp_path: Path) -> str:
    return build(tmp_path, raw())[1]


@pytest.mark.parametrize(
    ("label", "old", "new"),
    [
        (
            "aria-live on every link",
            '<a class="link smart-watch-link',
            '<a aria-live="polite" class="link smart-watch-link',
        ),
        (
            "badge shows a status without a script",
            'data-watch-badge="kick" hidden></span>',
            'data-watch-badge="kick" hidden>LIVE</span>',
        ),
        (
            "badge is not hidden",
            'data-watch-badge="twitch" hidden>',
            'data-watch-badge="twitch">',
        ),
        (
            "the region is assertive-free but missing",
            ' aria-live="polite"',
            "",
        ),
        (
            "only one platform has a badge",
            '<span class="smart-live-badge" data-watch-badge="kick" hidden></span>',
            "",
        ),
        (
            "a second region",
            'aria-atomic="true"></p></div>',
            'aria-atomic="true"></p><p aria-live="polite" data-watch-status></p></div>',
        ),
    ],
)
def test_the_markup_guard_goes_red(
    tmp_path: Path, label: str, old: str, new: str
) -> None:
    html = _good(tmp_path)
    check_markup(html)
    assert old in html, label
    with pytest.raises(AssertionError):
        check_markup(html.replace(old, new, 1))


# --- config ------------------------------------------------------------------------


def test_live_badges_need_the_stack_layout() -> None:
    data = raw()
    data["page"]["layout"] = "classic"
    data["page"].pop("status")
    data["page"].pop("watch")
    with pytest.raises(ValidationError, match="page.live_badges needs page.layout"):
        SiteConfig.model_validate(data)


def test_live_badges_must_be_a_real_boolean() -> None:
    data = raw()
    data["page"]["live_badges"] = "yes"
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(data)


# --- the badge style ---------------------------------------------------------------


def _luminance(color: str) -> float:
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _rule(css: str, selector: str) -> str:
    found = re.search(re.escape(selector) + r" \{([^}]*)\}", css)
    assert found, selector
    return found.group(1)


def test_badge_css_is_plain_text_on_the_aa_pair_and_never_animated(
    tmp_path: Path,
) -> None:
    out, _html = build(tmp_path, raw())
    css = next((out / "static").glob("smart-stack.*.css")).read_text("utf-8")
    rule = _rule(css, ".smart-live-badge")
    assert "background-color: var(--ra-color-on-accent)" in rule
    assert re.search(r"[^-]color: var\(--ra-color-accent\)", rule)
    assert "border: " in rule  # a shape as well as a color
    assert ".smart-live-badge[hidden] { display: none; }" in css
    for word in ("animation", "transition", "@keyframes", "blink"):
        assert word not in css.split(".smart-live-badge", 1)[1].split("}", 1)[0]
    assert "@keyframes" not in css
    tokens = (out / asset(out, "static/tokens.css")).read_text(encoding="utf-8")
    accent = re.search(r"--ra-brand-amber: (#[0-9A-Fa-f]{6})", tokens)
    on_accent = re.search(r"--ra-brand-on-amber: (#[0-9A-Fa-f]{6})", tokens)
    assert accent and on_accent
    hi, lo = sorted((_luminance(accent.group(1)), _luminance(on_accent.group(1))))[::-1]
    assert (hi + 0.05) / (lo + 0.05) >= 4.5


def test_the_badge_does_not_depend_on_color_alone() -> None:
    """A badge with no text is color only; the words are written by the script."""
    shipped = (resources.files("reach_around.site") / "static" / "live.js").read_text(
        encoding="utf-8"
    )
    assert re.search(r'"LIVE"', shipped)
    assert re.search(r'"OFFLINE"', shipped)
    assert "textContent" in shipped
    assert (
        "aria-hidden"
        not in shipped.split("function applyWatch", 1)[1].split("\n  function ", 1)[0]
    )


# --- the script's rules for the badges -----------------------------------------------


def source() -> str:
    return (resources.files("reach_around.site") / "static" / "live.js").read_text(
        encoding="utf-8"
    )


def test_the_script_still_has_one_request_to_one_route() -> None:
    check_requests(source())


def check_requests(text: str) -> None:
    assert len(re.findall(r"\bfetch\(", text)) == 1
    assert 'var ROUTE = "/e/live";' in text
    assert "http" not in re.sub(r"https://player\.(kick\.com|twitch\.tv)", "", text)
    for banned in ("XMLHttpRequest", "sendBeacon", "new Image", "localStorage"):
        assert banned not in text


def watch_body(text: str) -> str:
    return text.split("function applyWatch", 1)[1].split("\n  function ", 1)[0]


def check_no_error_text(text: str) -> None:
    body = (
        watch_body(text)
        + text.split("function watchWord", 1)[1].split("function watchBadges", 1)[0]
    )
    for word in ("rror", "navailable", "ailed", "try again", "offline."):
        assert word not in body, word
    assert set(re.findall(r'"([A-Za-z][A-Za-z ]*)"', body)) <= {
        "data-watch-badge",
        "object",
    }


def test_the_script_writes_no_error_text_into_the_badges() -> None:
    check_no_error_text(source())


@pytest.mark.parametrize(
    ("check", "old", "new"),
    [
        (
            check_requests,
            'var ROUTE = "/e/live";',
            'var ROUTE = "/e/live"; fetch("/x");',
        ),
        (
            check_requests,
            "var MAX_BODY_CHARS",
            'var u = "https://example.org/"; var MAX_BODY_CHARS',
        ),
        (check_requests, '"use strict";', '"use strict"; localStorage.x = 1;'),
        (
            check_no_error_text,
            "watchBadges(status);\n    for (var w",
            'watchBadges(status);\n    var oops = "Status error";\n    for (var w',
        ),
        (
            check_no_error_text,
            ': v === false ? WORD_OFFLINE : "";',
            ': v === false ? WORD_OFFLINE : "unavailable";',
        ),
    ],
)
def test_the_source_guards_bite(check: object, old: str, new: str) -> None:
    text = source()
    assert callable(check)
    check(text)
    assert old in text, old
    with pytest.raises(AssertionError):
        check(text.replace(old, new, 1))


def _run_mutation(tmp_path: Path, old: str, new: str) -> int:
    node = node_or_none()
    if node is None:
        pytest.skip("node is not installed; the browser-side logic was not run")
    text = source()
    assert old in text, old
    done = run_node(node, stage(tmp_path, source=text.replace(old, new, 1)))
    return done.returncode


@pytest.mark.parametrize(
    ("label", "old", "new"),
    [
        (
            "a badge on a guess (unknown counts as live)",
            ': v === false ? WORD_OFFLINE : "";',
            ': v === false ? WORD_OFFLINE : WORD_LIVE;',
        ),
        (
            "a badge for one platform only",
            "result[p.id] = watchWord(status, p.id);",
            'result[p.id] = p.id === "kick" ? watchWord(status, p.id) : "";',
        ),
        (
            "OFFLINE never shown",
            'v === true ? WORD_LIVE : v === false ? WORD_OFFLINE : "";',
            'v === true ? WORD_LIVE : "";',
        ),
        (
            "live shown for a non-boolean",
            "v === true ? WORD_LIVE :",
            "v ? WORD_LIVE :",
        ),
    ],
)
def test_the_node_tests_go_red_on_a_badge_mutation(
    tmp_path: Path, label: str, old: str, new: str
) -> None:
    assert _run_mutation(tmp_path, old, new) != 0, label


def test_the_node_tests_pass_on_the_shipped_script(tmp_path: Path) -> None:
    node = node_or_none()
    if node is None:
        pytest.skip("node is not installed; the browser-side logic was not run")
    done = run_node(node, stage(tmp_path))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "watch badges" in done.stdout
