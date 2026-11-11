# SPDX-License-Identifier: Apache-2.0
"""The media kit builder: the public pages, the gated page and the wall between.

Everything is synthetic: a fake brand, example.com and a canary string.
"""

from __future__ import annotations

import re
import shutil
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

from reach_around.cli import main
from reach_around.config import SiteConfig
from reach_around.config.numbers import Numbers, NumbersEntry
from reach_around.site.build import build_site
from reach_around.site.kit import (
    GATE_ACTION,
    GATED_FILE,
    GUARD_MIN_LENGTH,
    KitLeakError,
)

TODAY = date(2027, 3, 10)
CANARY = "CANARY-GATED-7f3a"
GOLDEN = Path(__file__).parent / "golden_kit"


def kit_doc(**kw: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "title": "Example Creator media kit",
        "about": "A fake creator used in tests. Streams games & talks.",
        "metrics": [
            {
                "platform": "twitch",
                "label": "Followers",
                "value": 1200,
                "as_of": "2027-03-01",
            },
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
                "in_total": False,
            },
        ],
        "turnstile_site_key": "0x4AAAAAAAexampleKey",
    }
    doc.update(kw)
    return doc


def gated_doc(**kw: Any) -> dict[str, Any]:
    return kit_doc(
        partners=[
            {
                "name": f"Acme Fake Co {CANARY}",
                "note": f"2026 sponsor {CANARY}",
                "url": "https://example.com/acme",
            }
        ],
        case_studies=[
            {
                "title": f"Launch week {CANARY}",
                "summary": "A fake product launch.",
                "result": "Ten thousand fake views.",
            }
        ],
        rates=[{"item": f"Sponsored video {CANARY}", "price_text": "Ask me!"}],
        demographics=[
            {"label": "Top country", "value": "Exampleland 40%", "as_of": "2027-03-01"}
        ],
        **kw,
    )


def make_config(kit: dict[str, Any] | None = None, **extra: Any) -> SiteConfig:
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
        "features": {"kit_gate": True},
        "kit": kit if kit is not None else gated_doc(),
    }
    data.update(extra)
    return SiteConfig.model_validate(data)


def build(
    tmp_path: Path,
    config: SiteConfig | None = None,
    numbers: Numbers | None = None,
    gated: bool = True,
    **kw: Any,
) -> tuple[Path, Path]:
    out, gated_out = tmp_path / "public", tmp_path / "gated"
    build_site(
        config or make_config(),
        out,
        today=TODAY,
        numbers=numbers,
        gated_out=gated_out if gated else None,
        **kw,
    )
    return out, gated_out


def tree_bytes(root: Path) -> bytes:
    return b"\n".join(p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file())


class Page(HTMLParser):
    """Collect what the accessibility checks need."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.headings: list[int] = []
        self.label_for: set[str] = set()
        self.scripts = 0
        self.feed(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        self.tags.append((tag, a))
        if tag == "script":
            self.scripts += 1
        if re.fullmatch(r"h[1-6]", tag):
            self.headings.append(int(tag[1]))
        if tag == "label" and a.get("for"):
            self.label_for.add(a["for"] or "")

    def find(self, tag: str) -> list[dict[str, str | None]]:
        return [a for t, a in self.tags if t == tag]


# --- the public page ----------------------------------------------------------


def test_public_kit_page_shows_header_numbers_total_and_dates(tmp_path: Path) -> None:
    out, _ = build(tmp_path)
    html = (out / "kit" / "index.html").read_text(encoding="utf-8")
    assert "<title>" in html and "Example Creator media kit" in html
    assert "A fake creator used in tests. Streams games &amp; talks." in html
    assert "Twitch" in html and "1,200" in html and "2027-03-01" in html
    assert "YouTube" in html and "3,400" in html and "2027-03-05" in html
    # the third number is shown but not in the total: 1,200 + 3,400
    assert "Bluesky" in html and "4,600" in html
    assert "2 numbers" in html
    assert "oldest" in html.lower() and "2027-03-01" in html


def test_counts_metric_uses_the_numbers_file_and_a_missing_one_is_left_out(
    tmp_path: Path,
) -> None:
    kit = kit_doc(
        metrics=[
            {"platform": "twitch", "label": "Followers", "source": "counts"},
            {"platform": "kick", "label": "Followers", "source": "counts"},
        ]
    )
    stamp = datetime(2027, 3, 9, 12, 0, tzinfo=UTC)
    numbers = Numbers(entries={"twitch": NumbersEntry(count=777, as_of=stamp)})
    out, _ = build(tmp_path, make_config(kit, features={"kit_gate": False}), numbers)
    html = (out / "kit" / "index.html").read_text(encoding="utf-8")
    assert "777" in html and "2027-03-09" in html
    assert "Kick" not in html


def test_no_total_when_nothing_counts(tmp_path: Path) -> None:
    kit = kit_doc(
        metrics=[
            {"platform": "twitch", "label": "Followers", "source": "counts"},
        ]
    )
    out, _ = build(tmp_path, make_config(kit, features={"kit_gate": False}))
    html = (out / "kit" / "index.html").read_text(encoding="utf-8")
    assert "Combined audience" not in html


def test_the_public_tree_holds_no_gated_text(tmp_path: Path) -> None:
    out, gated = build(tmp_path)
    public = tree_bytes(out).decode("utf-8", "replace")
    for text in (
        CANARY,
        "Acme Fake Co",
        "2026 sponsor",
        "Launch week",
        "Ten thousand fake views",
        "Sponsored video",
        "Ask me!",
        "Exampleland",
        "example.com/acme",
    ):
        assert text not in public, text
    page = (gated / GATED_FILE).read_text(encoding="utf-8")
    for text in (
        CANARY,
        "Acme Fake Co",
        "2026 sponsor",
        "Launch week",
        "Ten thousand fake views",
        "Sponsored video",
        "Ask me!",
        "Exampleland 40%",
        "https://example.com/acme",
    ):
        assert text in page, text


def test_a_gated_string_placed_in_config_never_reaches_the_public_tree(
    tmp_path: Path,
) -> None:
    for place in ("partners", "case_studies", "rates", "demographics"):
        canary = f"CANARY-{place.upper()}-91c2"
        kit = kit_doc()
        if place == "partners":
            kit["partners"] = [{"name": canary}]
        elif place == "case_studies":
            kit["case_studies"] = [{"title": canary, "summary": "s", "result": "r"}]
        elif place == "rates":
            kit["rates"] = [{"item": canary, "price_text": "Ask me!"}]
        else:
            kit["demographics"] = [
                {"label": canary, "value": "v", "as_of": "2027-03-01"}
            ]
        out, gated = build(tmp_path / place, make_config(kit))
        assert canary.encode() not in tree_bytes(out)
        assert canary in (gated / GATED_FILE).read_text(encoding="utf-8")


def test_guard_bites_when_an_override_template_prints_a_gated_string(
    tmp_path: Path,
) -> None:
    overrides = tmp_path / "overrides"
    (overrides / "templates").mkdir(parents=True)
    (overrides / "templates" / "kit.html").write_text(
        '{% extends "base.html" %}{% block content %}'
        f"<p>Our partner: Acme Fake Co {CANARY}</p>{{% endblock %}}",
        encoding="utf-8",
    )
    with pytest.raises(KitLeakError, match=r"partners"):
        build(tmp_path, overrides=overrides)
    # a refused build writes nothing into either root
    assert not (tmp_path / "public").exists()
    assert not (tmp_path / "gated").exists()


def test_guard_ignores_short_strings_and_documents_the_floor() -> None:
    assert GUARD_MIN_LENGTH >= 4


def test_site_wide_text_that_matches_a_gated_string_fails_the_build(
    tmp_path: Path,
) -> None:
    config = make_config(
        tabs=[
            {
                "id": "start",
                "title": "Start",
                "blocks": [
                    {
                        "id": "one",
                        "title": f"Backed by Acme Fake Co {CANARY}",
                        "url": "https://example.com/1",
                    }
                ],
            }
        ]
    )
    with pytest.raises(KitLeakError):
        build(tmp_path, config)


def test_gated_output_is_never_inside_or_around_the_public_output(
    tmp_path: Path,
) -> None:
    out = tmp_path / "public"
    for gated_out in (out, out / "inside", tmp_path):
        with pytest.raises(ValueError, match=r"gated"):
            build_site(make_config(), out, today=TODAY, gated_out=gated_out)
    assert not (out / "inside").exists()


def test_gated_content_needs_a_gated_output_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"--gated-out"):
        build(tmp_path, gated=False)


def test_public_only_kit_needs_no_gated_directory(tmp_path: Path) -> None:
    config = make_config(kit_doc(), features={"kit_gate": False})
    out, gated = build(tmp_path, config, gated=False)
    assert (out / "kit" / "index.html").is_file()
    assert not gated.exists()


def test_a_stale_gated_page_is_removed_when_nothing_is_gated(tmp_path: Path) -> None:
    out, gated = build(tmp_path)
    assert (gated / GATED_FILE).is_file()
    config = make_config(kit_doc(), features={"kit_gate": True})
    build_site(config, out, today=TODAY, gated_out=gated)
    assert not (gated / GATED_FILE).exists()


def test_build_only_touches_its_own_file_in_the_gated_directory(
    tmp_path: Path,
) -> None:
    gated = tmp_path / "gated"
    gated.mkdir(mode=0o700)
    (gated / "keep.txt").write_text("mine", encoding="utf-8")
    build_site(make_config(), tmp_path / "public", today=TODAY, gated_out=gated)
    assert (gated / "keep.txt").read_text(encoding="utf-8") == "mine"
    assert (gated / GATED_FILE).is_file()


def test_gated_filename_cannot_exist_in_the_public_tree(tmp_path: Path) -> None:
    out, _ = build(tmp_path)
    assert not list(out.rglob(GATED_FILE.name))


# --- the gate form ------------------------------------------------------------


def test_gate_form_markup(tmp_path: Path) -> None:
    out, _ = build(tmp_path)
    for rel in ("kit/index.html", "kit/retry/index.html"):
        html = (out / rel).read_text(encoding="utf-8")
        page = Page(html)
        # the cookie notice is a form too; the gate form is the one that posts
        (form,) = [a for a in page.find("form") if a.get("method") == "post"]
        assert form["method"] == "post"
        assert form["action"] == GATE_ACTION
        inputs = {a["name"]: a for a in page.find("input")}
        assert set(inputs) == {"email", "website"}
        assert inputs["email"]["type"] == "email"
        assert "required" in inputs["email"]
        assert inputs["email"]["autocomplete"] == "email"
        assert "value" not in inputs["email"]
        assert inputs["website"]["tabindex"] == "-1"
        assert inputs["website"]["autocomplete"] == "off"
        assert inputs["website"]["id"] in page.label_for
        assert inputs["email"]["id"] in page.label_for
        (widget,) = [
            a for a in page.find("div") if "cf-turnstile" in (a.get("class") or "")
        ]
        assert widget["data-sitekey"] == "0x4AAAAAAAexampleKey"
        assert widget["data-action"] == "gate"
        assert 'type="hidden"' not in html
        assert "?t=" not in html
        assert not any(
            "token" in value.lower() for value in form.values() if value is not None
        )


def test_gate_action_is_still_a_placeholder() -> None:
    # Flip this to "/k/gate" in the change that adds the Worker route (D1, #263).
    assert GATE_ACTION == "/kit/retry/"


def test_no_script_and_no_third_party_request_anywhere(tmp_path: Path) -> None:
    out, gated = build(tmp_path)
    for path in (
        out / "kit" / "index.html",
        out / "kit" / "retry" / "index.html",
        gated / GATED_FILE,
    ):
        text = path.read_text(encoding="utf-8")
        # The public page links the one same-origin display-zone script (#372);
        # the retry and gated pages carry none.
        expected = 1 if path == out / "kit" / "index.html" else 0
        assert Page(text).scripts == expected, path
        assert "challenges.cloudflare.com" not in text
        assert "style=" not in text and "onclick" not in text.lower()
    # The CSP allows the Turnstile origin on the two gate paths only (#257;
    # tests/site/test_kit_csp.py pins which); no page loads it yet.
    headers = (out / "_headers").read_text(encoding="utf-8")
    assert headers.count("challenges.cloudflare.com") == 4


def test_gate_page_explains_the_gate_and_noscript(tmp_path: Path) -> None:
    out, _ = build(tmp_path)
    html = (out / "kit" / "index.html").read_text(encoding="utf-8")
    assert "not checked" in html.lower() or "not verified" in html.lower()
    assert "<noscript>" in html
    assert 'href="../privacy/"' in html


def test_site_key_missing_renders_an_empty_slot_and_warns(tmp_path: Path) -> None:
    kit = gated_doc()
    del kit["turnstile_site_key"]
    out, _ = build_with_manifest(tmp_path, make_config(kit))
    html = (out[0] / "kit" / "index.html").read_text(encoding="utf-8")
    assert "data-sitekey" not in html
    assert any("turnstile_site_key" in w for w in out[1].warnings)


def build_with_manifest(
    tmp_path: Path, config: SiteConfig, numbers: Numbers | None = None
) -> tuple[tuple[Path, Any], Path]:
    out, gated = tmp_path / "public", tmp_path / "gated"
    manifest = build_site(config, out, today=TODAY, numbers=numbers, gated_out=gated)
    return (out, manifest), gated


def test_no_form_and_no_retry_page_when_the_gate_is_off(tmp_path: Path) -> None:
    config = make_config(kit_doc(), features={"kit_gate": False})
    out, _ = build(tmp_path, config, gated=False)
    html = (out / "kit" / "index.html").read_text(encoding="utf-8")
    assert 'method="post"' not in html and "cf-turnstile" not in html
    assert not (out / "kit" / "retry").exists()
    assert not (out / "kit" / "sent").exists()


def test_gate_on_with_nothing_gated_renders_no_form_and_warns(tmp_path: Path) -> None:
    config = make_config(kit_doc(), features={"kit_gate": True})
    (out, manifest), gated = build_with_manifest(tmp_path, config)
    page = (out / "kit" / "index.html").read_text(encoding="utf-8")
    assert 'method="post"' not in page and "cf-turnstile" not in page
    assert not (gated / GATED_FILE).exists()
    assert any("kit_gate" in w for w in manifest.warnings)


def test_sent_page_is_not_built_in_v1(tmp_path: Path) -> None:
    out, _ = build(tmp_path)
    assert not (out / "kit" / "sent").exists()


def test_retry_page_is_noindex_and_cause_free(tmp_path: Path) -> None:
    out, _ = build(tmp_path)
    html = (out / "kit" / "retry" / "index.html").read_text(encoding="utf-8")
    assert '<meta name="robots" content="noindex">' in html
    assert "try again" in html.lower()
    for word in ("turnstile failed", "rate limit", "invalid address", "too many"):
        assert word not in html.lower()


# --- the gated page -----------------------------------------------------------


def test_gated_page_is_a_noindex_document_with_root_absolute_assets(
    tmp_path: Path,
) -> None:
    _, gated = build(tmp_path)
    html = (gated / GATED_FILE).read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert '<meta name="robots" content="noindex">' in html
    page = Page(html)
    hrefs = [a["href"] for a in page.find("link") if a.get("rel") == "stylesheet"]
    assert hrefs and all((h or "").startswith("/static/") for h in hrefs)
    assert 'method="post"' not in html and 'name="email"' not in html
    for a in page.find("a"):
        if (a.get("href") or "").startswith("https://"):
            assert a["rel"] == "noopener noreferrer"
    assert "<table" in html and 'scope="col"' in html


def test_text_is_escaped(tmp_path: Path) -> None:
    kit = gated_doc()
    kit["partners"] = [{"name": 'Fish & "Chips" Fake Co', "note": "a & b"}]
    out, gated = build(tmp_path, make_config(kit))
    html = (gated / GATED_FILE).read_text(encoding="utf-8")
    assert "Fish &amp; &#34;Chips&#34; Fake Co" in html
    assert "a &amp; b" in html


# --- accessibility ------------------------------------------------------------


def test_landmarks_headings_and_labels(tmp_path: Path) -> None:
    out, gated = build(tmp_path)
    for path in (
        out / "kit" / "index.html",
        out / "kit" / "retry" / "index.html",
        gated / GATED_FILE,
    ):
        page = Page(path.read_text(encoding="utf-8"))
        assert len(page.find("main")) == 1, path
        assert page.headings.count(1) == 1, path
        for before, after in zip(page.headings, page.headings[1:], strict=False):
            assert after - before <= 1, (path, page.headings)
        assert any(t == "html" and a.get("lang") == "en" for t, a in page.tags), path
        for a in page.find("input"):
            assert a["id"] in page.label_for, (path, a)
        for a in page.find("section"):
            assert a.get("aria-labelledby") or a.get("aria-label"), (path, a)


# --- warnings -----------------------------------------------------------------


def test_staleness_warnings_surface_in_the_manifest(tmp_path: Path) -> None:
    kit = kit_doc(
        metrics=[
            {
                "platform": "twitch",
                "label": "Followers",
                "value": 5,
                "as_of": "2026-12-01",
            },
            {"platform": "kick", "label": "Followers", "source": "counts"},
        ]
    )
    (out, manifest), _ = build_with_manifest(
        tmp_path, make_config(kit, features={"kit_gate": False})
    )
    text = "\n".join(manifest.warnings)
    assert "twitch" in text and "99 days old" in text
    assert "kick" in text and "no value in the numbers file" in text


def test_cli_prints_kit_warnings_and_takes_gated_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import yaml

    instance = tmp_path / "instance"
    instance.mkdir()
    raw: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": [
            {
                "id": "start",
                "title": "Start",
                "blocks": [{"id": "one", "title": "One", "url": "https://example.com"}],
            }
        ],
        "features": {"kit_gate": True},
        "kit": gated_doc(),
    }
    raw["kit"]["metrics"][0]["as_of"] = "2020-01-01"
    (instance / "site.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    out, gated = tmp_path / "out", tmp_path / "gated"
    code = main(
        [
            "build",
            "--config",
            str(instance / "site.yaml"),
            "--out",
            str(out),
            "--gated-out",
            str(gated),
        ]
    )
    err = capsys.readouterr().err
    assert code == 0, err
    assert "warning: kit metric twitch" in err
    assert (gated / GATED_FILE).is_file()
    assert CANARY.encode() not in tree_bytes(out)
    # the same build without the flag is refused, and says what to do
    code = main(
        [
            "build",
            "--config",
            str(instance / "site.yaml"),
            "--out",
            str(tmp_path / "o2"),
        ]
    )
    assert code == 1
    assert "--gated-out" in capsys.readouterr().err
    # a gated directory inside the public one is refused
    code = main(
        [
            "build",
            "--config",
            str(instance / "site.yaml"),
            "--out",
            str(tmp_path / "o3"),
            "--gated-out",
            str(tmp_path / "o3" / "g"),
        ]
    )
    assert code == 1


# --- other site facts ---------------------------------------------------------


def test_kit_css_only_ships_with_a_kit(tmp_path: Path) -> None:
    out, _ = build(tmp_path)
    assert list((out / "static").glob("kit.*.css"))
    plain = SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "Example Creator"},
            "tabs": [
                {
                    "id": "start",
                    "title": "Start",
                    "blocks": [{"id": "a", "title": "A", "url": "https://example.com"}],
                }
            ],
        }
    )
    build_site(plain, tmp_path / "plain", today=TODAY)
    assert not list((tmp_path / "plain" / "static").glob("kit.*"))
    assert not (tmp_path / "plain" / "kit").exists()


def test_a_tab_named_kit_collides_with_the_kit_page(tmp_path: Path) -> None:
    config = make_config(
        tabs=[
            {
                "id": "start",
                "title": "Start",
                "blocks": [{"id": "a", "title": "A", "url": "https://example.com"}],
            },
            {
                "id": "kit",
                "title": "Kit",
                "blocks": [{"id": "b", "title": "B", "url": "https://example.com/b"}],
            },
        ]
    )
    with pytest.raises(ValueError, match=r"kit"):
        build(tmp_path, config)


def test_kit_pages_are_in_the_inventory_and_gated_file_is_not(tmp_path: Path) -> None:
    (out, manifest), _ = build_with_manifest(tmp_path, make_config())
    assert "kit/index.html" in manifest.inventory
    assert "kit/retry/index.html" in manifest.inventory
    assert not any(GATED_FILE.name == k.split("/")[-1] for k in manifest.inventory)


def test_sections_layout_builds_the_kit_too(tmp_path: Path) -> None:
    config = make_config(
        page_layout="sections",
        tabs=[
            {
                "id": "start",
                "title": "Start",
                "blocks": [{"id": "a", "title": "A", "url": "https://example.com"}],
            },
            {
                "id": "more",
                "title": "More",
                "blocks": [{"id": "b", "title": "B", "url": "https://example.com/b"}],
            },
        ],
    )
    out, gated = build(tmp_path, config)
    assert (out / "kit" / "index.html").is_file()
    assert CANARY.encode() not in tree_bytes(out)
    assert (gated / GATED_FILE).is_file()


def test_builds_are_deterministic(tmp_path: Path) -> None:
    a_out, a_gated = build(tmp_path / "a")
    b_out, b_gated = build(tmp_path / "b")
    assert tree_bytes(a_out) == tree_bytes(b_out)
    assert tree_bytes(a_gated) == tree_bytes(b_gated)


# --- golden outputs -----------------------------------------------------------


def test_golden_pages(tmp_path: Path, update_golden: bool) -> None:
    out, gated = build(tmp_path)
    pairs = {
        "public/kit/index.html": out / "kit" / "index.html",
        "public/kit/retry/index.html": out / "kit" / "retry" / "index.html",
        "gated/kit.html": gated / GATED_FILE,
    }
    if update_golden:
        shutil.rmtree(GOLDEN, ignore_errors=True)
        for rel, built in pairs.items():
            (GOLDEN / rel).parent.mkdir(parents=True, exist_ok=True)
            (GOLDEN / rel).write_bytes(built.read_bytes())
    for rel, built in pairs.items():
        assert (GOLDEN / rel).is_file(), "no golden file; run pytest --update-golden"
        assert built.read_bytes() == (GOLDEN / rel).read_bytes(), rel
