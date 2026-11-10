# SPDX-License-Identifier: Apache-2.0
"""``kit pdf --public``: the public half of the kit, sealed the same way (#425).

The browser is a stand-in (``fake_renderer``); the real one is covered by
``test_kit_pdf_chromium.py`` with ``--run-visual``.
"""

from __future__ import annotations

import socket
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import reach_around.commands.kit as command
from kitpdfharness import (
    CANARY,
    SITE_KEY,
    TODAY,
    build_instance,
    fake_renderer,
    kit_doc,
    make_config,
    make_pdf,
)
from reach_around.cli import main
from reach_around.config import SiteConfig, load_site_config
from reach_around.kit.output import OutputError
from reach_around.kit.pdf import (
    PdfError,
    PdfLeakError,
    build_kit_pdf,
    build_public_kit_pdf,
)
from reach_around.kit.pdftext import extract_pages
from reach_around.kit.stage import StageError, stage_page
from reach_around.site.build import build_site

CREATED = date(2027, 3, 10)


def public_run(
    tmp_path: Path,
    renderer: Any = fake_renderer,
    config: SiteConfig | None = None,
    **kw: Any,
) -> Path:
    built, site, _ = build_instance(tmp_path)
    return build_public_kit_pdf(
        config or built,
        site_dir=site,
        out_dir=tmp_path / "private",
        name="kit-public.pdf",
        created=CREATED,
        renderer=renderer,
        **kw,
    ).path


def flat_text(path: Path) -> str:
    return "".join(" ".join(extract_pages(path.read_bytes())).split())


# --- what is in it --------------------------------------------------------------


def test_the_public_pdf_has_the_public_text_and_the_valid_as_of_line(
    tmp_path: Path,
) -> None:
    flat = flat_text(public_run(tmp_path))
    for needle in (
        "Example Creator media kit",
        "A fake creator used in tests.",
        "Followers",
        "Subscribers",
        "4,600",
        "2027-03-01",
        "Valid as of 2027-03-10 (UTC)",
    ):
        assert "".join(needle.split()) in flat, needle


def test_the_public_pdf_has_no_gated_text_and_no_gate_form(tmp_path: Path) -> None:
    flat = flat_text(public_run(tmp_path))
    for banned in (
        CANARY,
        "2026 sponsor, fake",
        "Launch week fake",
        "Ten thousand fake views.",
        "Sponsored video fake",
        "Exampleland 40%",
        SITE_KEY,
        "Turnstile",
        "Moredetails",
        "Emailaddress",
        "Preferences",
        "Skipto",
        "privacy",
    ):
        assert banned not in flat, banned


def test_the_staged_public_page_is_sealed_and_has_no_form_or_script(
    tmp_path: Path,
) -> None:
    _, site, gated = build_instance(tmp_path)
    page = stage_page(site, site, public=True).html
    for banned in (
        "<form",
        "<input",
        "<select",
        "<script",
        SITE_KEY,
        'class="kit-gate',
        "data-zone",
    ):
        assert banned not in page
    assert "default-src 'none'" in page
    # the relaxed rules are for the public flag only: the same file handed to
    # the private stager is still refused (gate form, script)
    as_gated = tmp_path / "as-gated"
    as_gated.mkdir(mode=0o700)
    (as_gated / "kit.html").write_bytes((site / "kit" / "index.html").read_bytes())
    with pytest.raises(StageError):
        stage_page(as_gated, site)
    stage_page(gated, site)  # the real gated page is unchanged by this work


def test_a_public_page_with_a_script_in_the_body_is_still_refused(
    tmp_path: Path,
) -> None:
    _, site, _ = build_instance(tmp_path)
    page = site / "kit" / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "<main", "<script>alert(1)</script><main", 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(StageError, match="script"):
        stage_page(site, site, public=True)


def test_a_public_page_with_an_inline_head_script_is_refused(tmp_path: Path) -> None:
    _, site, _ = build_instance(tmp_path)
    page = site / "kit" / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</head>", "<script>fetch('/x')</script></head>", 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(StageError, match="script"):
        stage_page(site, site, public=True)


def test_a_form_outside_the_gate_box_is_still_refused(tmp_path: Path) -> None:
    _, site, _ = build_instance(tmp_path)
    page = site / "kit" / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            '<p class="kit-about">', '<form action="/x"></form><p class="kit-about">', 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(StageError, match="form"):
        stage_page(site, site, public=True)


# --- the leak check bites -------------------------------------------------------


def test_a_gated_value_in_the_public_pdf_text_fails_the_build(tmp_path: Path) -> None:
    def leaky(html: str, work: Path) -> bytes:
        return make_pdf(
            [
                [
                    "Example Creator media kit",
                    f"Acme Fake Co {CANARY}",
                ]
            ]
        )

    with pytest.raises(PdfLeakError, match=r"kit\.partners\[0\]\.name") as caught:
        public_run(tmp_path, leaky)
    assert CANARY not in str(caught.value)  # names the field, never the value
    assert not (tmp_path / "private" / "kit-public.pdf").exists()


def test_the_gate_key_in_the_public_pdf_text_fails_the_build(tmp_path: Path) -> None:
    def leaky(html: str, work: Path) -> bytes:
        return make_pdf([[f"stray {SITE_KEY} value"]])

    with pytest.raises(PdfLeakError, match="turnstile_site_key"):
        public_run(tmp_path, leaky)


def test_owner_report_only_engagement_text_in_the_public_pdf_fails(
    tmp_path: Path,
) -> None:
    doc = kit_doc()
    doc["engagement"] = [
        {
            "platform": "twitch",
            "period_start": "2027-02-01",
            "period_end": "2027-02-28",
            "as_of": "2027-03-01",
            "views": 12000,
            "posts": 8,
            "interactions": 600,
            "counts": "Chat messages and follows",
        }
    ]
    config = make_config(doc)

    def leaky(html: str, work: Path) -> bytes:
        return make_pdf([["Chat messages and follows"]])

    _, site, _ = build_instance(tmp_path, config)
    with pytest.raises(PdfLeakError, match=r"kit\.engagement\[0\]\.counts"):
        build_public_kit_pdf(
            config,
            site_dir=site,
            out_dir=tmp_path / "private",
            name="kit-public.pdf",
            created=CREATED,
            renderer=leaky,
        )
    # and the real stand-in render of the real public page has none of it
    ok = build_public_kit_pdf(
        config,
        site_dir=site,
        out_dir=tmp_path / "private2",
        name="kit-public.pdf",
        created=CREATED,
        renderer=fake_renderer,
    )
    assert "Chatmessages" not in flat_text(ok.path)


def test_a_stale_staging_directory_is_caught(tmp_path: Path) -> None:
    _, site, _ = build_instance(tmp_path)
    doc = kit_doc()
    doc["title"] = "A newer title not built yet"
    with pytest.raises(PdfError, match="missing from the PDF text"):
        build_public_kit_pdf(
            make_config(doc),
            site_dir=site,
            out_dir=tmp_path / "private",
            name="kit-public.pdf",
            created=CREATED,
            renderer=fake_renderer,
        )


def test_a_renderer_that_drops_the_valid_as_of_line_fails(tmp_path: Path) -> None:
    def lossy(html: str, work: Path) -> bytes:
        return make_pdf(
            [
                [
                    "Example Creator media kit",
                    "A fake creator used in tests. Streams games & talks.",
                    "Twitch Followers 1,200 2027-03-01",
                    "YouTube Subscribers 3,400 2027-03-05",
                    "4,600",
                ]
            ]
        )

    with pytest.raises(PdfError, match="valid-as-of"):
        public_run(tmp_path, lossy)


# --- the same policy ------------------------------------------------------------


def test_collected_youtube_analytics_are_refused_in_the_public_pdf(
    tmp_path: Path,
) -> None:
    _, site, _ = build_instance(tmp_path)
    doc = kit_doc()
    doc["engagement"] = [
        {
            "platform": "youtube",
            "period_start": "2027-02-01",
            "period_end": "2027-02-28",
            "as_of": "2027-03-01",
            "views": 12000,
            "posts": 8,
            "interactions": 600,
            "counts": "Views",
        }
    ]
    with pytest.raises(ValueError, match=r"YouTube Analytics.*owner_supplied: true"):
        build_public_kit_pdf(
            make_config(doc),
            site_dir=site,
            out_dir=tmp_path / "private",
            name="kit-public.pdf",
            created=CREATED,
            renderer=fake_renderer,
        )


def test_a_kit_with_only_public_content_still_makes_a_public_pdf(
    tmp_path: Path,
) -> None:
    doc = {k: v for k, v in kit_doc().items() if k in ("title", "about", "metrics")}
    config = make_config(doc)
    private_root = tmp_path / "priv"
    _, site, gated = build_instance(private_root, config)
    with pytest.raises(PdfError, match="nothing private"):
        build_kit_pdf(
            config,
            gated_dir=gated,
            site_dir=site,
            out_dir=tmp_path / "o1",
            name="k.pdf",
            created=CREATED,
            renderer=fake_renderer,
        )
    result = build_public_kit_pdf(
        config,
        site_dir=site,
        out_dir=tmp_path / "o2",
        name="kit-public.pdf",
        created=CREATED,
        renderer=fake_renderer,
    )
    assert result.path.is_file()


def test_no_kit_is_an_error(tmp_path: Path) -> None:
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
    with pytest.raises(PdfError, match="no media kit"):
        build_public_kit_pdf(
            config,
            site_dir=tmp_path,
            out_dir=tmp_path / "o",
            name="kit-public.pdf",
            created=CREATED,
            renderer=fake_renderer,
        )


# --- the same sealing -----------------------------------------------------------


def test_metadata_is_scrubbed_and_the_output_is_reproducible(tmp_path: Path) -> None:
    first = public_run(tmp_path / "a").read_bytes()
    second = public_run(tmp_path / "b").read_bytes()
    assert first == second
    for marker in (b"HeadlessChrome", b"Mozilla", b"/Creator", b"/ModDate", b"Skia"):
        assert marker not in first
    assert b"/Producer (Reach Around)" in first
    assert b"D:20270310000000Z" in first


def _blocked(*a: object, **k: object) -> None:
    raise AssertionError("a network call was attempted")


def test_the_public_pipeline_makes_no_socket_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, site, _ = build_instance(tmp_path)
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    result = build_public_kit_pdf(
        config,
        site_dir=site,
        out_dir=tmp_path / "private",
        name="kit-public.pdf",
        created=CREATED,
        renderer=fake_renderer,
    )
    assert result.pages >= 1


def test_the_workdir_is_gone_after_a_render_failure(tmp_path: Path) -> None:
    seen: list[Path] = []

    def failing(html: str, work: Path) -> bytes:
        seen.append(work)
        raise PdfError("boom")

    with pytest.raises(PdfError, match="boom"):
        public_run(tmp_path, failing)
    assert seen and not seen[0].exists()


def test_the_output_may_not_be_inside_a_git_work_tree(tmp_path: Path) -> None:
    config, site, _ = build_instance(tmp_path)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    with pytest.raises(OutputError, match="git"):
        build_public_kit_pdf(
            config,
            site_dir=site,
            out_dir=repo / "out",
            name="kit-public.pdf",
            created=CREATED,
            renderer=fake_renderer,
        )


def test_the_output_may_not_be_inside_the_public_site(tmp_path: Path) -> None:
    config, site, _ = build_instance(tmp_path)
    with pytest.raises(OutputError):
        build_public_kit_pdf(
            config,
            site_dir=site,
            out_dir=site / "pdf",
            name="kit-public.pdf",
            created=CREATED,
            renderer=fake_renderer,
        )


# --- the command ----------------------------------------------------------------

CONFIG_YAML = (
    "version: 1\ncreator: {name: Example Creator}\n"
    "tabs:\n- id: start\n  title: Start\n  blocks:\n"
    "  - {id: one, title: One, url: 'https://example.com/1'}\n"
    "features: {kit_gate: true}\nkit:\n"
    "  title: Example Creator media kit\n  about: Fake.\n"
    "  rates:\n  - {item: Sponsored video fake, price_text: Ask me}\n"
)


def _setup(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    conf = tmp_path / "conf"
    conf.mkdir(mode=0o700)
    cfg = conf / "site.yaml"
    cfg.write_text(CONFIG_YAML, encoding="utf-8")
    site, gated, out = tmp_path / "site", tmp_path / "gated", tmp_path / "private"
    build_site(load_site_config(cfg), site, today=TODAY, gated_out=gated)
    return cfg, site, gated, out


def test_command_public_prints_the_path_and_a_dated_file_name(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(command, "chromium_renderer", lambda *a, **k: fake_renderer)
    cfg, site, _, out = _setup(tmp_path)
    code = main(
        [
            "kit", "pdf", "--public", "--config", str(cfg),
            "--site-out", str(site), "--out-dir", str(out),
            "--date", "2027-03-10",
        ]
    )  # fmt: skip
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert captured.out == f"{out / 'kit-public-2027-03-10.pdf'}\n"
    assert (out / "kit-public-2027-03-10.pdf").is_file()
    # a public file may be shared: the private warning is not printed for it
    assert "do not publish" not in captured.err


def test_command_public_needs_no_gated_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(command, "chromium_renderer", lambda *a, **k: fake_renderer)
    cfg, site, gated, out = _setup(tmp_path)
    import shutil

    shutil.rmtree(gated)
    code = main(
        [
            "kit", "pdf", "--public", "--config", str(cfg),
            "--site-out", str(site), "--out-dir", str(out),
            "--date", "2027-03-10",
        ]
    )  # fmt: skip
    assert code == 0, capsys.readouterr().err


def test_command_public_refuses_a_gated_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg, site, gated, out = _setup(tmp_path)
    code = main(
        [
            "kit", "pdf", "--public", "--config", str(cfg),
            "--site-out", str(site), "--gated-out", str(gated),
            "--out-dir", str(out),
        ]
    )  # fmt: skip
    err = capsys.readouterr().err
    assert code == 1 and "--gated-out" in err and "--public" in err
    assert not out.exists()


def test_command_private_still_needs_the_gated_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg, site, _, out = _setup(tmp_path)
    code = main(
        [
            "kit", "pdf", "--config", str(cfg), "--site-out", str(site),
            "--out-dir", str(out),
        ]
    )  # fmt: skip
    err = capsys.readouterr().err
    assert code == 1 and "--gated-out" in err
    assert not out.exists()


def test_command_private_default_name_is_unchanged(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(command, "chromium_renderer", lambda *a, **k: fake_renderer)
    cfg, site, gated, out = _setup(tmp_path)
    code = main(
        [
            "kit", "pdf", "--config", str(cfg), "--site-out", str(site),
            "--gated-out", str(gated), "--out-dir", str(out), "--date", "2027-03-10",
        ]
    )  # fmt: skip
    assert code == 0, capsys.readouterr().err
    assert (out / "kit.pdf").is_file()
