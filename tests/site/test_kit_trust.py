# SPDX-License-Identifier: Apache-2.0
"""Kit v2 slice 1 (#626): a trust label on every number the kit prints.

The label is read from where the number came from (a configured collector, a
typed value, an import, an owner-labeled estimate), never chosen by a template.
Everything here is synthetic.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from test_kit_build import (
    CANARY,
    TODAY,
    build,
    gated_doc,
    make_config,
)
from test_kit_imported_history import imported as imported_history
from test_kit_recent_posts import PNG

from kitpdfharness import build_instance, fake_renderer
from kitpdfharness import kit_doc as pdf_kit_doc
from kitpdfharness import make_config as pdf_config
from reach_around.config import SiteConfig
from reach_around.config.kit import PlatformMetric
from reach_around.config.numbers import Numbers, NumbersEntry
from reach_around.config.trust import (
    API,
    ESTIMATE,
    HAND_KEPT,
    LABELS,
    LEGEND_HEADING,
    LEGEND_SENTENCES,
    LEGEND_SENTENCES_GATED,
    Trust,
    metric_trust,
    weakest,
)
from reach_around.kit.pdf import (
    PdfError,
    PdfLeakError,
    build_kit_pdf,
    build_public_kit_pdf,
)
from reach_around.kit.pdftext import extract_pages
from reach_around.site.kit import GATED_FILE

STAMP = datetime(2027, 3, 9, 12, 0, tzinfo=UTC)
SRC = Path(__file__).resolve().parents[2] / "src" / "reach_around"

TWITCH_SOURCE = {"platform": "twitch", "kind": "twitch", "login": "example_streamer"}


def numbers(
    api: tuple[str, ...] = (), stamp: datetime = STAMP, **counts: int
) -> Numbers:
    """A numbers file; the platforms in ``api`` carry ``source: api`` as
    ``counts refresh`` writes it, the rest are entries with no recorded source."""
    entries = {
        name: NumbersEntry(
            count=count, as_of=stamp, source="api" if name in api else None
        )
        for name, count in counts.items()
    }
    return Numbers(entries=entries)


def trust_doc(**extra: Any) -> dict[str, Any]:
    """A kit with every provenance: a collected number (Twitch, through its
    configured source), a numbers-file number no source writes (Kick), a typed
    number, and an owner-labeled estimate."""
    doc = gated_doc(
        metrics=[
            {"platform": "twitch", "label": "Followers", "source": "counts"},
            {"platform": "kick", "label": "Followers", "source": "counts"},
            {
                "platform": "youtube",
                "label": "Subscribers",
                "value": 3400,
                "as_of": "2027-03-05",
            },
            {
                "platform": "bluesky",
                "label": "Followers",
                "value": 66,
                "as_of": "2027-03-08",
                "estimate": "rounded from the profile page",
            },
        ],
        analytics=[
            {
                "platform": "instagram",
                "synced_with": "Example Social API",
                "as_of": "2027-03-08",
                "followers": 12_460,
                "followers_30_day_percent": 4.26,
                "stats": [{"label": "Example views", "value": 1_260_000}],
            }
        ],
        engagement=[
            {
                "platform": "tiktok",
                "period_start": "2027-02-01",
                "period_end": "2027-02-28",
                "as_of": "2027-03-01",
                "views": 120_000,
                "posts": 12,
                "interactions": 9_000,
            }
        ],
        case_studies=[
            {
                "thumbnails": ["media/missing.png"],
                "views": 12345,
                "likes": 678,
                "comments": 90,
                "period_start": "2027-02-01",
                "period_end": "2027-02-28",
            }
        ],
    )
    doc.update(extra)
    return doc


def trust_config(doc: dict[str, Any] | None = None, **extra: Any) -> Any:
    extra.setdefault("numbers", {"sources": [TWITCH_SOURCE]})
    extra.setdefault("tabs", TABS)
    return make_config(doc if doc is not None else trust_doc(), **extra)


TABS = [
    {
        "id": "start",
        "title": "Start",
        "blocks": [
            {"id": "one", "title": "One", "url": "https://example.com/1"},
            {
                "id": "socials",
                "items": [
                    {
                        "platform": "twitch",
                        "handle": "example_streamer",
                        "url": "https://example.com/t",
                    },
                    {
                        "platform": "kick",
                        "handle": "example_streamer",
                        "url": "https://example.com/k",
                    },
                ],
            },
        ],
    }
]
NUMS = numbers(api=("twitch",), twitch=1200, kick=800)


def built(
    tmp_path: Path, doc: dict[str, Any] | None = None, **kw: Any
) -> tuple[str, str]:
    config = kw.pop("config", None) or trust_config(doc)
    kw.setdefault("numbers", NUMS)
    assets = tmp_path / "assets"
    (assets / "media").mkdir(parents=True, exist_ok=True)
    (assets / "media" / "missing.png").write_bytes(PNG)
    kw.setdefault("assets_dir", assets)
    out, gated = build(tmp_path, config, **kw)
    return (
        (out / "kit" / "index.html").read_text(encoding="utf-8"),
        (gated / GATED_FILE).read_text(encoding="utf-8"),
    )


@pytest.fixture(scope="module")
def pages(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, str]:
    return built(tmp_path_factory.mktemp("trust"))


def row(page: str, platform: str, measure: str | None = None) -> str:
    """The headline-number table row of one platform."""
    for found in re.findall(
        r"<tr role=\"row\"><th scope=\"row\" role=\"rowheader\">.*?</tr>", page, re.S
    ):
        if f'">{platform}</th>' in found:
            return str(found)
    raise AssertionError(f"no row for {platform}")


def text_of(html: str) -> str:
    return " ".join(re.sub(r"<[^>]*>", " ", html).split())


def labels_in(page: str) -> list[str]:
    return re.findall(r'class="kit-trust kit-trust-([a-z-]+)"', page)


# --- the three labels ---------------------------------------------------------


def test_there_are_exactly_three_labels_and_no_fourth_can_be_made() -> None:
    assert LABELS == (API, HAND_KEPT, ESTIMATE) == ("api", "hand-kept", "estimate")
    for fourth in ("imported", "verified", "hand_kept", "derived", ""):
        with pytest.raises(ValueError):
            Trust(fourth, "x")


def test_no_fourth_label_exists_in_code_templates_or_output(
    pages: tuple[str, str],
) -> None:
    classes = set()
    for path in SRC.rglob("*"):
        if path.suffix in {".py", ".html", ".css"}:
            classes.update(re.findall(r"kit-trust-([a-z][a-z-]*)", path.read_text()))
    # the template builds the class from the label; the stylesheet has no rule
    # for any other word, and the macro's own placeholder is not a label
    assert classes <= {*LABELS, "line"}, classes
    for page in pages:
        assert set(labels_in(page)) <= set(LABELS)
        assert labels_in(page), "no label was printed"


# --- the label follows the provenance ------------------------------------------


def test_a_collected_number_is_api_with_its_date(pages: tuple[str, str]) -> None:
    one = row(pages[0], "Twitch")
    assert 'class="kit-trust kit-trust-api">API</span>' in one
    assert "Read from the platform&#39;s API on 2027-03-09" in one
    assert "2027-03-09" in one
    assert "kit-trust-hand-kept" not in one


def twitch_page(tmp_path: Path, nums: Numbers, **kw: Any) -> str:
    doc = trust_doc(
        metrics=[{"platform": "twitch", "label": "Followers", "source": "counts"}]
    )
    return built(tmp_path, doc, numbers=nums, **kw)[0]


def test_a_typed_entry_for_a_configured_collector_is_hand_kept(
    tmp_path: Path,
) -> None:
    # a collector is configured for twitch, but nothing recorded that it wrote
    # this entry (a hand-typed numbers.json entry has no source)
    for origin in (None, "hand"):
        entry = NumbersEntry(count=900, as_of=STAMP, source=origin)
        page = twitch_page(tmp_path / str(origin), Numbers(entries={"twitch": entry}))
        one = row(page, "Twitch")
        assert "kit-trust-hand-kept" in one and "kit-trust-api" not in one


def test_an_api_entry_with_no_collector_configured_is_hand_kept(
    tmp_path: Path,
) -> None:
    doc = trust_doc(
        metrics=[{"platform": "kick", "label": "Followers", "source": "counts"}]
    )
    page = built(tmp_path, doc, numbers=numbers(api=("kick",), kick=800))[0]
    one = row(page, "Kick")
    assert "kit-trust-hand-kept" in one and "kit-trust-api" not in one


def test_a_stale_api_entry_keeps_api_with_the_mark_and_names_its_date(
    tmp_path: Path,
) -> None:
    old = datetime(2027, 1, 5, 12, 0, tzinfo=UTC)
    page = twitch_page(tmp_path, numbers(api=("twitch",), stamp=old, twitch=900))
    one = row(page, "Twitch")
    assert "kit-trust-api" in one
    assert "Read from the platform&#39;s API on 2027-01-05" in one
    assert "(out of date)" in one


def test_a_refresh_written_entry_prints_api_end_to_end(tmp_path: Path) -> None:
    from fakenet import FakeTransport, reply
    from reach_around.cli import main
    from reach_around.commands.counts import run_refresh

    root = tmp_path / "inst"
    root.mkdir()
    (root / "site.yaml").write_text(
        """\
version: 1
creator: {name: Example Creator}
features: {kit_gate: false}
tabs:
  - id: start
    title: Start
    blocks:
      - {id: one, title: One, url: "https://example.com/1"}
kit:
  title: Example kit
  about: A synthetic test kit.
  metrics:
    - {platform: github, label: Followers, source: counts}
numbers:
  sources:
    - {platform: github, kind: github, user: some-user, show_on_hub: false}
""",
        "utf-8",
    )
    transport = FakeTransport(
        {"https://api.github.com/users/some-user": reply({"followers": 24739})}
    )
    assert (
        run_refresh(root / "site.yaml", None, None, False, transport=transport, env={})
        == 0
    )
    written = json.loads((root / "numbers.json").read_text())
    assert written["platforms"]["github"]["source"] == "api"
    out = tmp_path / "out"
    assert (
        main(
            [
                "build",
                "--config",
                str(root / "site.yaml"),
                "--out",
                str(out),
                "--numbers",
                str(root / "numbers.json"),
            ]
        )
        == 0
    )
    assert "kit-trust-api" in (out / "kit" / "index.html").read_text("utf-8")
    # the same file with the key removed (an older file) prints Hand-kept
    del written["platforms"]["github"]["source"]
    (root / "numbers.json").write_text(json.dumps(written))
    assert (
        main(
            [
                "build",
                "--config",
                str(root / "site.yaml"),
                "--out",
                str(out),
                "--numbers",
                str(root / "numbers.json"),
            ]
        )
        == 0
    )
    page = (out / "kit" / "index.html").read_text("utf-8")
    assert "kit-trust-hand-kept" in page and "kit-trust-api" not in page


def test_a_number_no_source_writes_is_hand_kept_not_api(
    pages: tuple[str, str],
) -> None:
    one = row(pages[0], "Kick")
    assert 'class="kit-trust kit-trust-hand-kept">Hand-kept</span>' in one
    assert "Typed into the numbers file by the owner" in one
    assert "kit-trust-api" not in one


def test_a_typed_number_is_hand_kept_and_an_estimate_names_its_method(
    pages: tuple[str, str],
) -> None:
    typed = row(pages[0], "YouTube")
    assert "kit-trust-hand-kept" in typed and "kit-trust-api" not in typed
    assert "Typed by the owner" in typed
    est = row(pages[0], "Bluesky")
    assert 'class="kit-trust kit-trust-estimate">Estimate</span>' in est
    assert "Estimate: rounded from the profile page" in est


def test_the_label_never_comes_from_the_platform_name() -> None:
    api = frozenset({"twitch", "kick"})
    for source in ("manual", "counts"):
        for origin in (None, "hand", "api"):
            assert metric_trust("twitch", source, None, api, origin) == metric_trust(
                "kick", source, None, api, origin
            )
    assert metric_trust("twitch", "counts", None, frozenset(), "api").label == HAND_KEPT
    assert metric_trust("twitch", "counts", None, api, "api").label == API
    assert metric_trust("twitch", "counts", None, api, None).label == HAND_KEPT
    assert metric_trust("twitch", "counts", None, api, "hand").label == HAND_KEPT
    assert metric_trust("twitch", "manual", None, api, "api").label == HAND_KEPT


def test_an_imported_value_is_hand_kept_and_names_the_export(
    tmp_path: Path,
) -> None:
    doc = trust_doc(
        imported_history="imported-history.json", imported_history_public=True
    )
    public, gated = built(tmp_path, doc, imported=imported_history())
    for page in (public, gated):
        block = re.search(r'<section class="kit-imported".*?</section>', page, re.S)
        assert block, "no imported block"
        text = block.group(0)
        assert 'kit-trust kit-trust-hand-kept">Hand-kept' in text
        assert "Imported from the Beacons export" in text
        assert "last period ending 2026-02-28" in text
        assert "kit-trust-api" not in text


def test_every_numbers_group_on_both_pages_has_a_label(
    pages: tuple[str, str],
) -> None:
    public, gated = pages
    # headline rows, total, analytics: public and gated
    for page in pages:
        assert len(re.findall(r"kit-trust-", page)) >= 6
        assert page.count('class="kit-trust ') == len(labels_in(page))
        assert "Weakest label among the numbers added" in page
        assert 'class="kit-note kit-trust-line"' in page
        assert "Typed by the owner from Example Social API" in page
    # engagement, case studies and demographics: gated only
    assert "Worked out from figures typed by the owner" in gated
    assert "Typed by the owner for 2027-02-01 to 2027-02-28" in gated
    assert "Shares typed by the owner from the report dated 2027-03-01" in gated
    for text in ("Worked out from", "Typed by the owner for", "shares typed"):
        assert text not in public


def test_the_total_takes_the_weakest_label_of_what_it_adds(tmp_path: Path) -> None:
    runs = iter(range(10))

    def total_label(doc: dict[str, Any], **kw: Any) -> str:
        page = built(tmp_path / str(next(runs)), doc, **kw)[0]
        block = re.search(r"Weakest label among.*?</p>", page, re.S)
        assert block
        found = re.findall(r"kit-trust-([a-z-]+)\"", page.split(block.group(0))[0])
        return str(found[-1])

    twitch = [{"platform": "twitch", "label": "Followers", "source": "counts"}]
    assert (
        total_label(
            trust_doc(metrics=twitch), numbers=numbers(api=("twitch",), twitch=900)
        )
        == API
    )
    mixed = [
        *twitch,
        {"platform": "youtube", "label": "S", "value": 50, "as_of": "2027-03-05"},
    ]
    assert (
        total_label(
            trust_doc(metrics=mixed), numbers=numbers(api=("twitch",), twitch=900)
        )
        == HAND_KEPT
    )
    worst = [
        *mixed,
        {
            "platform": "bluesky",
            "label": "F",
            "value": 60,
            "as_of": "2027-03-05",
            "estimate": "poll",
        },
    ]
    assert (
        total_label(
            trust_doc(metrics=worst), numbers=numbers(api=("twitch",), twitch=900)
        )
        == ESTIMATE
    )
    assert weakest([]) is None


def test_an_estimate_needs_a_typed_number() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        make_config(
            trust_doc(
                metrics=[
                    {
                        "platform": "twitch",
                        "label": "F",
                        "source": "counts",
                        "estimate": "guess",
                    }
                ]
            )
        )


# --- the legend --------------------------------------------------------------


def test_the_legend_prints_on_the_public_and_the_gated_page(
    pages: tuple[str, str],
) -> None:
    for page, sentences in zip(
        pages, (LEGEND_SENTENCES, LEGEND_SENTENCES_GATED), strict=True
    ):
        assert page.count('class="kit-legend"') == 1
        assert f'<h3 id="kit-legend-title">{LEGEND_HEADING}</h3>' in page
        flat = text_of(page).replace("&#39;", "'")
        for sentence in sentences:
            assert sentence in flat
    assert 3 <= len(LEGEND_SENTENCES) <= 4
    # the public wording names no gated section
    public_legend = " ".join(LEGEND_SENTENCES).lower()
    for word in ("engagement", "demographic", "case stud"):
        assert word not in public_legend
    # the last sentence says what the mark covers, and only that
    assert "case studies" in LEGEND_SENTENCES_GATED[-1]
    assert "carry no date" in LEGEND_SENTENCES[-1]


def test_a_kit_with_no_numbers_has_no_legend(tmp_path: Path) -> None:
    doc = {"title": "Example kit", "about": "A synthetic kit with no numbers."}
    out, _ = build(
        tmp_path, make_config(doc, features={"kit_gate": False}), gated=False
    )
    page = (out / "kit" / "index.html").read_text(encoding="utf-8")
    assert "kit-legend" not in page and "kit-trust" not in page


# --- the stale mark ----------------------------------------------------------


def test_an_old_number_is_marked_out_of_date_and_a_fresh_one_is_not(
    tmp_path: Path,
) -> None:
    doc = trust_doc(
        metrics=[
            {"platform": "youtube", "label": "Old", "value": 50, "as_of": "2027-01-01"},
            {"platform": "bluesky", "label": "New", "value": 60, "as_of": "2027-03-08"},
        ],
        analytics=[
            {
                "platform": "instagram",
                "synced_with": "Example Social API",
                "as_of": "2027-01-02",
                "followers": 12_460,
                "followers_30_day_percent": 4.26,
                "stats": [],
            }
        ],
        engagement=[
            {
                "platform": "tiktok",
                "period_start": "2027-01-01",
                "period_end": "2027-01-31",
                "as_of": "2027-02-01",
                "views": 120_000,
                "posts": 12,
                "interactions": 9_000,
            }
        ],
        demographics=[
            {
                "platform": "instagram",
                "as_of": "2027-01-03",
                "gender": {"male": 40, "female": 60},
                "ages": [{"range": "18-24", "all": 100, "male": 100, "female": 100}],
                "countries": [{"name": "Exampleland", "percentage": 40}],
            }
        ],
    )
    public, gated = built(tmp_path, doc)
    assert "(out of date)" in row(public, "YouTube")
    assert "(out of date)" not in row(public, "Bluesky")
    for page in (public, gated):
        panel = re.search(r'<p class="kit-note kit-trust-line">.*?</p>', page, re.S)
        assert panel and "(out of date)" in panel.group(0)
        # the combined total is old when a number it adds is old
        total = re.search(
            r'<p class="kit-note"><span class="kit-trust.*?</p>', page, re.S
        )
        assert total and "(out of date)" in total.group(0)
    engagement = re.search(r"Engagement</h3>.*?</table>", gated, re.S)
    assert engagement and engagement.group(0).count("(out of date)") == 2
    demo = re.search(r"Shares typed.*?</p>", gated, re.S)
    assert demo and "(out of date)" in demo.group(0)


def test_the_stale_limit_is_the_platforms_own(tmp_path: Path) -> None:
    doc = trust_doc(
        metrics=[
            {"platform": "youtube", "label": "S", "value": 50, "as_of": "2027-02-20"},
        ]
    )
    plain = built(tmp_path / "a", doc)[0]
    assert "(out of date)" not in row(plain, "YouTube")  # 18 days: inside 30
    tight = trust_config(doc, numbers={"stale_days": {"youtube": 10}})
    page = built(tmp_path / "b", config=tight)[0]
    assert "(out of date)" in row(page, "YouTube")


# --- Twitch and Kick are treated identically ----------------------------------


def shape(html: str) -> str:
    """The row with its label word, method text and words removed: the markup
    and field order that is left must not depend on the platform."""
    html = re.sub(r"kit-trust-[a-z-]+", "kit-trust-X", html)
    html = re.sub(r">[^<]+<", "><", html)
    return html


@pytest.mark.parametrize("twitch_source", [True, False])
def test_twitch_and_kick_rows_have_the_same_shape(
    tmp_path: Path, twitch_source: bool
) -> None:
    config = trust_config(
        numbers={"sources": [TWITCH_SOURCE]} if twitch_source else {"stale_days": {}}
    )
    public = built(tmp_path, config=config)[0]
    assert shape(row(public, "Twitch")) == shape(row(public, "Kick"))


def test_swapping_twitch_and_kick_swaps_only_the_names(tmp_path: Path) -> None:
    def metrics(first: str, second: str) -> list[dict[str, Any]]:
        return [
            {
                "platform": first,
                "label": "Followers",
                "value": 1200,
                "as_of": "2027-03-01",
            },
            {
                "platform": second,
                "label": "Followers",
                "value": 800,
                "as_of": "2027-03-01",
            },
        ]

    a = built(tmp_path / "a", trust_doc(metrics=metrics("twitch", "kick")))[0]
    b = built(tmp_path / "b", trust_doc(metrics=metrics("kick", "twitch")))[0]

    def normal(page: str, name: str) -> str:
        return text_of(row(page, name)).replace(name, "X")

    # same value, same date, same label and method, whichever platform has it
    assert normal(a, "Twitch").replace("1,200", "N") == normal(b, "Twitch").replace(
        "800", "N"
    )
    assert normal(a, "Kick").replace("800", "N") == normal(b, "Kick").replace(
        "1,200", "N"
    )
    assert (
        normal(a, "Twitch").split() == normal(a, "Kick").replace("800", "1,200").split()
    )


# --- the floor and the wall still hold ---------------------------------------


def numeric_text(html: str) -> str:
    """Visible text with ISO dates removed."""
    return re.sub(r"\d{4}-\d{2}-\d{2}", "", text_of(html))


def test_label_text_never_prints_a_count_under_the_floor(tmp_path: Path) -> None:
    doc = trust_doc(
        metrics=[
            {
                "platform": "kick",
                "label": "Followers",
                "value": 3,
                "as_of": "2027-03-08",
            }
        ],
        analytics=[
            {
                "platform": "instagram",
                "synced_with": "Example Social API",
                "as_of": "2027-03-08",
                "followers": 3,
                "followers_30_day_percent": 4.0,
                "stats": [{"label": "Example views", "value": 2}],
            }
        ],
        engagement=[
            {
                "platform": "tiktok",
                "period_start": "2027-02-01",
                "period_end": "2027-02-28",
                "as_of": "2027-03-01",
                "views": 4,
                "posts": 2,
                "interactions": 3,
            }
        ],
        case_studies=[
            {
                "thumbnails": ["media/missing.png"],
                "views": 4,
                "likes": 2,
                "comments": 1,
                "period_start": "2027-02-01",
                "period_end": "2027-02-28",
            }
        ],
    )
    public, gated = built(tmp_path, doc, numbers=numbers())
    assert "Under 5" in row(public, "Kick")
    for page in (public, gated):
        for line in re.findall(
            r'<[^>]*kit-trust[^>]*>.*?</p>|<td><span class="kit-trust.*?</td>',
            page,
            re.S,
        ):
            assert not re.search(r"(?<![\w.-])[1-4](?![\w-])", numeric_text(line)), line
    # the row itself says "Under 5" and prints no exact small count
    kick = numeric_text(row(public, "Kick"))
    assert not re.search(r"(?<![\w.-])3(?![\w-])", kick)


GATED_FIELDS: dict[str, Callable[[str], dict[str, Any]]] = {
    "inquiry": lambda c: {
        "inquiry": {"email": f"{c.lower()}@example.com", "pgp_fingerprint": None}
    },
    "imported_history": lambda c: {"imported_history": "imported-history.json"},
    "partners": lambda c: {"partners": [{"name": f"Acme {c}", "note": f"Note {c}"}]},
    "rates": lambda c: {"rates": [{"item": f"Item {c}", "price_text": f"Ask {c}"}]},
    "services": lambda c: {
        "services": [{"title": f"Pack {c}", "included": [f"One {c}"]}]
    },
    "testimonials": lambda c: {
        "testimonials": [{"name": f"Name {c}", "quote": f"Quote {c} fine"}]
    },
    "disclosure": lambda c: {"disclosure": f"Disclosure {c} statement"},
    "engagement": lambda c: {
        "engagement": [
            {
                "platform": "tiktok",
                "period_start": "2027-02-01",
                "period_end": "2027-02-28",
                "as_of": "2027-03-01",
                "views": 7_777_771,
                "posts": 31,
                "interactions": 6_666_661,
                "counts": f"likes {c}",
            }
        ]
    },
    "case_studies": lambda c: {
        "case_studies": [
            {
                "thumbnails": ["media/missing.png"],
                "views": 8_888_881,
                "likes": 5_555_551,
                "comments": 4_444_441,
                "period_start": "2027-02-01",
                "period_end": "2027-02-28",
            }
        ]
    },
    "demographics": lambda c: {
        "demographics": [
            {
                "platform": "instagram",
                "as_of": "2027-03-01",
                "gender": {"male": 40, "female": 60},
                "ages": [{"range": "18-24", "all": 100, "male": 100, "female": 100}],
                "countries": [{"name": f"Land {c}", "percentage": 40}],
            }
        ]
    },
}


# the imported-history sentinels of tests/site/test_kit_imported_history.py
LEAK_SECRETS = ("9,171,823", "9171823", "4,242,017", "4242017")


@pytest.mark.parametrize("field", sorted(GATED_FIELDS))
def test_a_gated_value_never_reaches_the_public_page(
    tmp_path: Path, field: str
) -> None:
    canary = "ZZQ-LEAK-8841"
    doc = {
        "title": "Example kit",
        "about": "A synthetic kit.",
        "metrics": [
            {
                "platform": "twitch",
                "label": "Followers",
                "value": 1200,
                "as_of": "2027-03-01",
            }
        ],
        "turnstile_site_key": "0x4AAAAAAAexampleKey",
        **GATED_FIELDS[field](canary),
    }
    extra = {"imported": imported_history()} if field == "imported_history" else {}
    public, gated = built(tmp_path, doc, config=make_config(doc), **extra)
    for secret in (
        *LEAK_SECRETS,
        canary,
        canary.lower(),
        "7,777,771",
        "7777771",
        "6,666,661",
        "8,888,881",
        "5,555,551",
        "4,444,441",
    ):
        assert secret not in public, secret
    assert "kit-legend" in public  # the label text did not trip the wall
    if field == "imported_history":
        assert "9,171,823" in gated  # the block is in the gated page, not the public


def test_the_label_text_is_not_a_leak_canary_collision() -> None:
    # every sentence that could print on the public page is public vocabulary,
    # so none may be a gated string (the build would refuse to write it)
    from reach_around.site.kit import _gated_strings

    config = trust_config()
    gated = {text for _, text in _gated_strings(config.kit)}
    for line in (
        *LEGEND_SENTENCES,
        *LEGEND_SENTENCES_GATED,
        LEGEND_HEADING,
        "Hand-kept",
        "API",
        "Estimate",
    ):
        assert line not in gated


def test_existing_configs_build_unchanged_in_field_order() -> None:
    schema = json.loads(
        (SRC.parents[1] / "docs" / "site.schema.json").read_text(encoding="utf-8")
    )
    metric = PlatformMetric.model_fields
    assert list(metric) == [
        "platform",
        "label",
        "source",
        "value",
        "as_of",
        "in_total",
        "estimate",
    ]
    assert "estimate" in schema["$defs"]["PlatformMetric"]["properties"]
    assert "estimate" not in schema["$defs"]["PlatformMetric"].get("required", [])
    assert CANARY  # imported for the shared harness
    assert TODAY


# --- the two PDFs ------------------------------------------------------------

CREATED = date(2027, 3, 10)


def pdf_kit(**extra: Any) -> dict[str, Any]:
    doc = pdf_kit_doc()
    doc["metrics"].append(
        {
            "platform": "kick",
            "label": "Followers",
            "value": 800,
            "as_of": "2027-03-02",
        }
    )
    doc["analytics"] = [
        {
            "platform": "instagram",
            "synced_with": "Example Social API",
            "as_of": "2027-03-08",
            "followers": 12_460,
            "followers_30_day_percent": 4.26,
            "stats": [{"label": "Example views", "value": 1_260_000}],
        }
    ]
    doc["engagement"] = [
        {
            "platform": "tiktok",
            "period_start": "2027-02-01",
            "period_end": "2027-02-28",
            "as_of": "2027-03-01",
            "views": 120_000,
            "posts": 12,
            "interactions": 9_000,
        }
    ]
    doc.update(extra)
    return doc


def pdfs(tmp_path: Path, doc: dict[str, Any], **kw: Any) -> tuple[Any, Path, Path]:
    config, site, gated = build_instance(tmp_path, pdf_config(doc), **kw)
    return config, site, gated


def public_pdf(config: Any, site: Path, tmp_path: Path, **kw: Any) -> str:
    result = build_public_kit_pdf(
        config,
        site_dir=site,
        out_dir=tmp_path / "private",
        name="public.pdf",
        created=CREATED,
        renderer=fake_renderer,
        overwrite=True,
        **kw,
    )
    return "".join(" ".join(extract_pages(result.path.read_bytes())).split())


def owner_pdf(config: Any, site: Path, gated: Path, tmp_path: Path, **kw: Any) -> str:
    result = build_kit_pdf(
        config,
        gated_dir=gated,
        site_dir=site,
        out_dir=tmp_path / "private",
        name="owner.pdf",
        created=CREATED,
        renderer=fake_renderer,
        overwrite=True,
        **kw,
    )
    return "".join(" ".join(extract_pages(result.path.read_bytes())).split())


def squash(text: str) -> str:
    return "".join(text.split())


def test_both_pdfs_print_the_labels_and_the_legend(tmp_path: Path) -> None:
    config, site, gated = pdfs(tmp_path, pdf_kit())
    public = public_pdf(config, site, tmp_path)
    owner = owner_pdf(config, site, gated, tmp_path)
    for text, sentences in (
        (public, LEGEND_SENTENCES),
        (owner, LEGEND_SENTENCES_GATED),
    ):
        for sentence in sentences:
            assert squash(sentence.replace("&#39;", "'")) in text
        assert squash(LEGEND_HEADING) in text
        assert (
            squash("Twitch Followers 1,200 Hand-kept Typed by the owner 2027-03-01")
            in text
        )
        assert (
            squash("Kick Followers 800 Hand-kept Typed by the owner 2027-03-02") in text
        )
        assert squash("Hand-kept Typed by the owner from Example Social API") in text
        assert squash("Weakest label among the numbers added") in text
    assert squash("Worked out from figures typed by the owner") in owner
    assert squash("Worked out from figures typed by the owner") not in public
    assert squash("Shares typed by the owner") in owner
    assert squash("Typed by the owner for 2027-02-01 to 2027-02-28") in owner


def pdf_config_with_source(doc: dict[str, Any], with_source: bool) -> Any:
    extra: dict[str, Any] = {"tabs": TABS}
    if with_source:
        extra["numbers"] = {"sources": [TWITCH_SOURCE]}
    return SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "Example Creator"},
            "features": {"kit_gate": True},
            "kit": doc,
            **extra,
        }
    )


@pytest.mark.parametrize("with_source", [True, False])
def test_the_pdf_prints_api_only_where_a_collector_wrote_the_number(
    tmp_path: Path, with_source: bool
) -> None:
    doc = pdf_kit(
        metrics=[{"platform": "twitch", "label": "Followers", "source": "counts"}]
    )
    config, site, _ = build_instance(
        tmp_path, pdf_config_with_source(doc, with_source), numbers=NUMS
    )
    text = public_pdf(config, site, tmp_path, numbers=NUMS)
    label, method = (
        ("API", "Read from the platform's API on 2027-03-09")
        if with_source
        else ("Hand-kept", "Typed into the numbers file by the owner")
    )
    assert squash(f"Twitch Followers 1,200 {label} {method} 2027-03-09") in text


def strip_label(page: Path, marker: str, instead: str = "") -> None:
    html = page.read_text(encoding="utf-8")
    assert marker in html, marker
    page.write_text(html.replace(marker, instead, 1), encoding="utf-8")


def test_a_pdf_without_a_metric_label_is_refused(tmp_path: Path) -> None:
    config, site, _ = pdfs(tmp_path, pdf_kit())
    strip_label(
        site / "kit" / "index.html",
        '<td role="cell" data-label="Source">'
        '<span class="kit-trust kit-trust-hand-kept">Hand-kept</span> '
        '<span class="kit-note kit-method">Typed by the owner</span></td>',
        '<td role="cell" data-label="Source"></td>',
    )
    with pytest.raises(PdfError, match="metric row 'Twitch' is missing"):
        public_pdf(config, site, tmp_path)


@pytest.mark.parametrize("owner", [False, True])
def test_a_pdf_without_the_legend_is_refused(tmp_path: Path, owner: bool) -> None:
    config, site, gated = pdfs(tmp_path, pdf_kit())
    page = gated / GATED_FILE if owner else site / "kit" / "index.html"
    html = page.read_text(encoding="utf-8")
    page.write_text(
        re.sub(r'<section class="kit-legend".*?</section>', "", html, flags=re.S),
        encoding="utf-8",
    )
    with pytest.raises(PdfError, match="trust legend"):
        if owner:
            owner_pdf(config, site, gated, tmp_path)
        else:
            public_pdf(config, site, tmp_path)


def test_an_owner_pdf_without_an_engagement_label_is_refused(tmp_path: Path) -> None:
    config, site, gated = pdfs(tmp_path, pdf_kit())
    page = gated / GATED_FILE
    html = page.read_text(encoding="utf-8")
    cell = (
        '<td role="cell" data-label="Source">'
        '<span class="kit-trust kit-trust-hand-kept">Hand-kept</span> '
        '<span class="kit-note kit-method">Worked out from figures typed by the '
        "owner</span></td>"
    )
    assert cell in html
    page.write_text(
        html.replace(cell, '<td role="cell" data-label="Source"></td>'),
        encoding="utf-8",
    )
    with pytest.raises(PdfError, match="engagement row"):
        owner_pdf(config, site, gated, tmp_path)


def test_the_stale_mark_reaches_the_pdf(tmp_path: Path) -> None:
    doc = pdf_kit()
    doc["metrics"][0]["as_of"] = "2027-01-01"
    config, site, _ = pdfs(tmp_path, doc)
    assert "(outofdate)" in public_pdf(config, site, tmp_path)


@pytest.mark.parametrize("field", sorted(GATED_FIELDS))
def test_a_gated_value_never_reaches_the_public_pdf(tmp_path: Path, field: str) -> None:
    canary = "ZZQ-LEAK-8841"
    doc = pdf_kit_doc()
    for name in GATED_FIELDS:
        doc.pop(name, None)
    doc.update(GATED_FIELDS[field](canary))
    for case in doc.get("case_studies", []):
        case["thumbnails"] = ["media/campaign.png"]
    extra = {"imported": imported_history()} if field == "imported_history" else {}
    config, site, _ = pdfs(tmp_path, doc, **extra)
    text = public_pdf(config, site, tmp_path, **extra)
    for secret in (
        *LEAK_SECRETS,
        canary,
        canary.lower(),
        "7,777,771",
        "6,666,661",
        "8,888,881",
        "5,555,551",
        "4,444,441",
    ):
        assert squash(secret) not in text, secret
    assert squash(LEGEND_HEADING) in text


def test_the_pdf_check_still_catches_a_gated_value_in_the_public_text(
    tmp_path: Path,
) -> None:
    config, site, _ = pdfs(tmp_path, pdf_kit())
    page = site / "kit" / "index.html"
    html = page.read_text(encoding="utf-8")
    page.write_text(
        html.replace("</section>", f"<p>Acme Fake Co {CANARY}</p></section>", 1),
        encoding="utf-8",
    )
    with pytest.raises(PdfLeakError):
        public_pdf(config, site, tmp_path)


# --- the stale mark in the PDFs, case, and the estimate line -------------------


@pytest.fixture
def pdf_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """The PDF check judges staleness on the days the page may have been built;
    pin the real day to the PDF's own so the old numbers are old on both."""
    import reach_around.kit.pdf as pdf_module

    monkeypatch.setattr(pdf_module, "utc_today", lambda: CREATED)


def old_pdf_kit() -> dict[str, Any]:
    doc = pdf_kit()
    doc["metrics"][0]["as_of"] = "2027-01-01"
    doc["analytics"][0]["as_of"] = "2027-01-02"
    doc["engagement"][0].update(period_end="2027-01-31", as_of="2027-02-01")
    doc["engagement"][0]["period_start"] = "2027-01-01"
    doc["demographics"][0]["as_of"] = "2027-01-03"
    return doc


def test_old_numbers_print_the_mark_in_both_pdfs(
    tmp_path: Path, pdf_today: None
) -> None:
    config, site, gated = pdfs(tmp_path, old_pdf_kit())
    public = public_pdf(config, site, tmp_path)
    owner = owner_pdf(config, site, gated, tmp_path)
    assert squash("2027-01-01 UTC (out of date)") in public
    assert squash("2027-01-01 UTC (out of date)") in owner
    assert owner.count("(outofdate)") >= 5


@pytest.mark.parametrize("owner", [False, True])
def test_a_pdf_page_that_lost_the_stale_mark_is_refused(
    tmp_path: Path, pdf_today: None, owner: bool
) -> None:
    config, site, gated = pdfs(tmp_path, old_pdf_kit())
    page = gated / GATED_FILE if owner else site / "kit" / "index.html"
    html = page.read_text(encoding="utf-8")
    assert "UTC (out of date)" in html
    page.write_text(html.replace(" (out of date)", ""), encoding="utf-8")
    with pytest.raises(PdfError, match="out of date|with its label"):
        if owner:
            owner_pdf(config, site, gated, tmp_path)
        else:
            public_pdf(config, site, tmp_path)


def test_a_pdf_with_the_label_in_the_wrong_case_is_refused(tmp_path: Path) -> None:
    config, site, _ = pdfs(tmp_path, pdf_kit())
    strip_label(
        site / "kit" / "index.html",
        '<span class="kit-trust kit-trust-hand-kept">Hand-kept</span>',
        '<span class="kit-trust kit-trust-hand-kept">hand-kept</span>',
    )
    with pytest.raises(PdfError, match="as written"):
        public_pdf(config, site, tmp_path)


@pytest.mark.parametrize(
    "text",
    [
        "about 3 people",
        "under 5",
        "see example.com",
        "a — b",
        "@owner",
        "https://example.com/x",
        "a.b",
        "12345",
        "year 20261",
    ],
)
def test_an_estimate_line_refuses_digits_links_handles_and_dashes(text: str) -> None:
    from pydantic import ValidationError

    metric = {
        "platform": "bluesky",
        "label": "F",
        "value": 60,
        "as_of": "2027-03-05",
        "estimate": text,
    }
    with pytest.raises(ValidationError) as caught:
        make_config(trust_doc(metrics=[metric]))
    assert "holds only words" in str(caught.value)


@pytest.mark.parametrize(
    "text", ["rounded from the profile page", "poll of 2026 viewers, roughly (typed)"]
)
def test_an_estimate_line_accepts_plain_words_and_a_year(text: str) -> None:
    make_config(
        trust_doc(
            metrics=[
                {
                    "platform": "bluesky",
                    "label": "F",
                    "value": 60,
                    "as_of": "2027-03-05",
                    "estimate": text,
                }
            ]
        )
    )


def test_the_closed_entry_sources_are_pinned_in_the_engine_and_the_validator() -> None:
    import importlib.util

    from reach_around.config.numbers import ENTRY_SOURCES

    path = (
        SRC.parents[1]
        / "examples"
        / "instance-repo"
        / "scripts"
        / "validate-numbers.py"
    )
    spec = importlib.util.spec_from_file_location("validate_numbers", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert set(ENTRY_SOURCES) == {"api", "hand"}
    assert set(module.SOURCES) == {"api", "hand"}


# --- review round: recent posts in the owner PDF, demographic stale limits -----


def recent_post_doc() -> dict[str, Any]:
    doc = pdf_kit()
    doc["recent_posts"] = [
        {
            "platform": "tiktok",
            "thumbnail": "media/clip.png",
            "views": 1234,
            "likes": 56,
            "comments": 7,
        }
    ]
    return doc


def test_the_owner_pdf_does_not_expect_a_section_the_gated_page_lacks(
    tmp_path: Path,
) -> None:
    config, site, gated = pdfs(tmp_path, recent_post_doc())
    assert "kit-recent" in (site / "kit" / "index.html").read_text(encoding="utf-8")
    assert "kit-recent" not in (gated / GATED_FILE).read_text(encoding="utf-8")
    owner = owner_pdf(config, site, gated, tmp_path)  # must not be refused
    assert squash("Counts typed by the owner") not in owner
    public = public_pdf(config, site, tmp_path)
    assert squash("TikTok Hand-kept Counts typed by the owner") in public


def test_the_public_pdf_still_requires_the_recent_post_label(tmp_path: Path) -> None:
    config, site, _ = pdfs(tmp_path, recent_post_doc())
    strip_label(
        site / "kit" / "index.html",
        '<span class="kit-trust kit-trust-hand-kept">Hand-kept</span> '
        '<span class="kit-note kit-method">Counts typed by the owner</span>',
        "",
    )
    with pytest.raises(PdfError, match="recent posts"):
        public_pdf(config, site, tmp_path)


def demo_doc(platform_days: int) -> dict[str, Any]:
    doc = pdf_kit()
    doc["demographics"][0]["as_of"] = "2027-02-20"  # 18 days before the PDF day
    del platform_days
    return doc


def demo_config(doc: dict[str, Any], days: int | None) -> Any:
    cfg = pdf_config(doc).model_dump(mode="json", exclude_none=True)
    if days is not None:
        cfg["numbers"] = {"stale_days": {"instagram": days}}
    return SiteConfig.model_validate(cfg)


def test_a_platforms_shorter_limit_marks_its_demographics(
    tmp_path: Path, pdf_today: None
) -> None:
    doc = demo_doc(0)
    config, site, gated = build_instance(tmp_path / "a", demo_config(doc, None))
    plain = re.search(
        r"Shares typed.*?</p>", (gated / GATED_FILE).read_text(encoding="utf-8")
    )
    assert plain and "(out of date)" not in plain.group(0)
    config, site, gated = build_instance(tmp_path / "b", demo_config(doc, 10))
    page = (gated / GATED_FILE).read_text(encoding="utf-8")
    demo = re.search(r"Shares typed.*?</p>", page)
    assert demo and "(out of date)" in demo.group(0)
    assert "(outofdate)" in owner_pdf(config, site, gated, tmp_path)
    # the check requires it: a page without the mark is refused
    (gated / GATED_FILE).write_text(
        page.replace(demo.group(0), demo.group(0).replace(" (out of date)", "")),
        encoding="utf-8",
    )
    with pytest.raises(PdfError, match="demographic|kit.demographics"):
        owner_pdf(config, site, gated, tmp_path)


def test_a_demographic_warning_uses_the_platforms_own_limit() -> None:
    from reach_around.config.kit import kit_warnings

    doc = demo_doc(0)
    kit = demo_config(doc, 10).kit
    assert kit is not None
    today = date(2027, 3, 10)
    assert not [w for w in kit_warnings(kit, None, today) if "demographic" in w]
    limited = kit_warnings(kit, None, today, {"instagram": 10})
    assert any("demographic" in w for w in limited)
