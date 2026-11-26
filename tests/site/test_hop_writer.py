# SPDX-License-Identifier: Apache-2.0
"""The funnel hop writer (issue #593): ``h`` on links marked ``internal``.

Schema rules, the rendered hrefs, the link parity tool, and a contract test
that runs the shipped ``click.js`` reader against what the writer emits.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_click_js import _sent

from reach_around.config import SiteConfig
from reach_around.config.hoplabel import add_hop, check_hop_label
from reach_around.linkparity import strip_hop
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
PEER = "https://blog.example.test"
HREF = re.compile(r'<a class="link[^"]*" href="([^"]*)"')


def make(
    links: list[dict[str, Any]],
    *,
    label: str | None = "links",
    others: tuple[str, ...] = ("blog.example.test",),
) -> SiteConfig:
    analytics: dict[str, Any] = {"other_sites": list(others)}
    if label is not None:
        analytics["hop_label"] = label
    return SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "Example Creator"},
            "tabs": [{"id": "start", "title": "Start", "blocks": links}],
            "analytics": analytics,
        }
    )


def link(ident: str, url: str, **extra: Any) -> dict[str, Any]:
    return {"id": ident, "title": ident.title(), "url": url, **extra}


def hrefs(cfg: SiteConfig, out: Path) -> dict[str, str]:
    build_site(cfg, out, today=TODAY, assets_dir=out.parent)
    text = (out / "index.html").read_text(encoding="utf-8")
    ids = re.findall(r'data-link-id="([^"]+)"', text)
    found = HREF.findall(text)
    assert len(ids) == len(found)
    return dict(zip(ids, found, strict=True))


# ---- schema -----------------------------------------------------------------


@pytest.mark.parametrize("label", ["links", "a", "blog-2", "x" * 30])
def test_a_closed_label_is_accepted(label: str) -> None:
    assert (
        make([link("one", PEER, internal=True)], label=label).analytics.hop_label
        == label
    )


@pytest.mark.parametrize(
    ("label", "reason"),
    [
        ("", "must not be empty"),
        ("x" * 31, "at most 30"),
        ("Links", "lower case"),
        ("my links", "only a-z"),
        ("a/b", "only a-z"),
        ("<b>", "only a-z"),
        ("liñks", "only a-z"),
        ("other", "reserved"),
        ("none", "reserved"),
        ("direct", "reserved"),
    ],
)
def test_an_unusual_label_is_refused_with_a_reason(label: str, reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        make([link("one", PEER)], label=label)


def test_internal_needs_a_hop_label() -> None:
    with pytest.raises(ValidationError, match="hop_label is not set"):
        make([link("one", PEER, internal=True)], label=None)


def test_internal_must_be_a_real_boolean() -> None:
    for bad in ("yes", 1, "true"):
        with pytest.raises(ValidationError):
            make([link("one", PEER, internal=bad)])


def test_internal_needs_a_listed_site_of_the_owner() -> None:
    with pytest.raises(ValidationError, match="other_sites"):
        make([link("one", "https://elsewhere.example.net/p", internal=True)])


def test_internal_refuses_affiliate_and_tab_links() -> None:
    with pytest.raises(ValidationError, match="affiliate"):
        make([link("one", PEER, internal=True, affiliate=True)])
    with pytest.raises(ValidationError, match="tab: link already stays"):
        make([link("one", "tab:start", internal=True)])


def test_an_existing_h_is_refused_at_build_not_overwritten() -> None:
    with pytest.raises(ValidationError, match="already has an h query"):
        make([link("one", f"{PEER}/p?h=old", internal=True)])
    with pytest.raises(ValidationError, match="already has an h query"):
        make([link("one", f"{PEER}/p?a=1&h=&b=2#f", internal=True)])


# ---- render -----------------------------------------------------------------


def test_internal_link_gets_h_and_external_does_not(tmp_path: Path) -> None:
    cfg = make(
        [
            link("mine", f"{PEER}/page", internal=True),
            link("plain", f"{PEER}/page"),  # same host, not marked: untouched
            link("away", "https://elsewhere.example.net/p"),
        ]
    )
    got = hrefs(cfg, tmp_path / "out")
    assert got == {
        "mine": f"{PEER}/page?h=links",
        "plain": f"{PEER}/page",
        "away": "https://elsewhere.example.net/p",
    }


def test_query_and_fragment_are_kept(tmp_path: Path) -> None:
    cfg = make(
        [
            link("a", f"{PEER}/p?x=1&y=two#frag", internal=True),
            link("b", f"{PEER}/p#frag", internal=True),
            link("c", f"{PEER}/p?x=1", internal=True),
            link("d", f"{PEER}/", internal=True),
        ]
    )
    got = hrefs(cfg, tmp_path / "out")
    assert got["a"] == f"{PEER}/p?x=1&amp;y=two&amp;h=links#frag"
    assert got["b"] == f"{PEER}/p?h=links#frag"
    assert got["c"] == f"{PEER}/p?x=1&amp;h=links"
    assert got["d"] == f"{PEER}/?h=links"


def test_a_link_in_a_section_gets_h_too(tmp_path: Path) -> None:
    section = {
        "id": "more",
        "title": "More",
        "links": [link("s1", f"{PEER}/s", internal=True), link("s2", f"{PEER}/t")],
    }
    got = hrefs(make([section]), tmp_path / "out")
    assert got == {"s1": f"{PEER}/s?h=links", "s2": f"{PEER}/t"}


def test_the_page_works_without_javascript(tmp_path: Path) -> None:
    # The hop is part of the href itself, written at build time; no script sets it.
    out = tmp_path / "out"
    hrefs(make([link("mine", f"{PEER}/page", internal=True)]), out)
    text = (out / "index.html").read_text(encoding="utf-8")
    assert f'href="{PEER}/page?h=links"' in text


def test_without_internal_links_the_page_is_unchanged(tmp_path: Path) -> None:
    plain = make([link("a", f"{PEER}/p")], label=None)
    labelled = make([link("a", f"{PEER}/p")], label="links")
    one = hrefs(plain, tmp_path / "one")
    two = hrefs(labelled, tmp_path / "two")
    assert one == two == {"a": f"{PEER}/p"}


# ---- add_hop, direct -------------------------------------------------------


@pytest.mark.parametrize("label", ["Links", "a b", "", "other", "x" * 31])
def test_add_hop_refuses_a_malformed_label(label: str) -> None:
    with pytest.raises(ValueError):
        add_hop(f"{PEER}/p", label)


def test_add_hop_refuses_to_overwrite() -> None:
    with pytest.raises(ValueError, match="not overwritten"):
        add_hop(f"{PEER}/p?h=x", "links")


# ---- link parity understands the written h --------------------------------


def test_parity_strips_the_one_written_hop_only() -> None:
    assert strip_hop(f"{PEER}/p?h=links") == f"{PEER}/p"
    assert strip_hop(f"{PEER}/p?x=1&h=links#f") == f"{PEER}/p?x=1#f"
    # Not ours: repeated, malformed or absent stays visible as a difference.
    assert strip_hop(f"{PEER}/p?h=a&h=b") == f"{PEER}/p?h=a&h=b"
    assert strip_hop(f"{PEER}/p?h=A%20B") == f"{PEER}/p?h=A%20B"
    assert strip_hop(f"{PEER}/p?x=1") == f"{PEER}/p?x=1"


def test_parity_reads_a_built_site_with_hops_as_the_plain_urls(tmp_path: Path) -> None:
    from reach_around.linkparity import read_site

    out = tmp_path / "out"
    hrefs(make([link("mine", f"{PEER}/p?x=1#f", internal=True)]), out)
    entries = {e.ref: e.url for e in read_site(out).entries}
    assert (
        entries["mine"] == f"{PEER}/p?x=1"
    )  # fragment dropped by normalize_url, h by strip_hop


# ---- the reader accepts what the writer emits ------------------------------

CANDIDATES = [
    "links",
    "a",
    "blog-2",
    "-edge-",
    "x" * 30,
    "x" * 31,
    "",
    "Links",
    "my links",
    "a/b",
    "<b>",
    "liñks",
    "Kick",
    "other",
    "none",
]


@pytest.mark.parametrize("label", CANDIDATES)
def test_reader_accepts_what_the_writer_emits(label: str, tmp_path: Path) -> None:
    try:
        check_hop_label(label)
    except ValueError:
        writer_ok = False
    else:
        writer_ok = True
    url = add_hop(f"{PEER}/p", label) if writer_ok else f"{PEER}/p?h={label}"
    search = "?" + url.split("?", 1)[1]
    sent = _sent(search, None, tmp_path)
    forwarded = sent == [f"/e/c/twitch?h={label}"]
    if writer_ok:
        assert forwarded  # everything the writer emits, the reader forwards as is
    elif label.lower() == label or not label.isascii():
        # Anything the writer refuses and the reader also refuses is never written.
        assert forwarded is (label in {"other", "none"})
    # (an upper-case label the reader would lower-case is refused by the writer too)


def test_the_writer_rule_is_the_one_the_readers_use() -> None:
    # The shared grammar is spelled out in three places that read the label
    # (click.js, the Worker's site list, the export check); the writer's rule
    # must be the same text, so a change to one fails here until all agree.
    root = Path(__file__).resolve().parents[2]
    click = (root / "src/reach_around/site/static/click.js").read_text("utf-8")
    sites = (root / "worker/src/sites.ts").read_text("utf-8")
    export = (root / "src/reach_around/funnel/export.py").read_text("utf-8")
    assert "/^[A-Za-z0-9-]{1,30}$/.test(value)" in click
    assert "const UTM = /^[a-z0-9-]{1,30}$/;" in sites
    assert '_UTM = re.compile(r"[a-z0-9-]{1,30}")' in export
    assert 'x === "other" || x === "none"' in sites
    from reach_around.config.hoplabel import MAX_HOP_LABEL, RESERVED_HOP_LABELS

    assert MAX_HOP_LABEL == 30
    assert {"other", "none"} <= RESERVED_HOP_LABELS
