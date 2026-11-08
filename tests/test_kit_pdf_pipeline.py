# SPDX-License-Identifier: Apache-2.0
"""The kit PDF pipeline end to end, with a stand-in for the browser.

The browser run itself is in ``test_kit_pdf_chromium.py`` (``--run-visual``).
"""

from __future__ import annotations

import getpass
import os
import re
import socket
import stat
import time
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any, NoReturn

import pytest

import reach_around.commands.kit as command
from kitpdfharness import (
    CANARY,
    SITE_KEY,
    build_instance,
    fake_renderer,
    kit_doc,
    make_config,
    make_pdf,
)
from reach_around.cli import main
from reach_around.config import SiteConfig, load_site_config
from reach_around.config.cutover import utc_today
from reach_around.config.kit import (
    DISPLAY_POLICY,
    PDF_EXCLUDED,
    KitConfig,
    pdf_excluded_values,
)
from reach_around.kit.pdf import (
    MAX_PAGES,
    MAX_PDF_BYTES,
    PdfError,
    PdfLeakError,
    build_kit_pdf,
    scrub_pdf,
)
from reach_around.kit.pdftext import extract_pages
from reach_around.site.build import build_site

CREATED = date(2027, 3, 10)


def run(
    tmp_path: Path,
    renderer: Callable[[str, Path], bytes] = fake_renderer,
    created: date = CREATED,
    **kw: Any,
) -> Path:
    config, site, gated = build_instance(tmp_path)
    out = tmp_path / "private"
    result = build_kit_pdf(
        config,
        gated_dir=gated,
        site_dir=site,
        out_dir=out,
        name="kit.pdf",
        created=created,
        renderer=renderer,
        **kw,
    )
    return result.path


def info_keys(data: bytes) -> set[str]:
    match = re.search(rb"/Info (\d+) 0 R", data)
    assert match is not None
    obj = re.search(
        rb"\n" + match.group(1) + rb" 0 obj\n<<(.*?)>>\s*endobj", data, re.S
    )
    assert obj is not None
    return {k.decode() for k in re.findall(rb"/([A-Za-z]+)", obj.group(1))}


# --- the result ---------------------------------------------------------------


def test_the_pdf_has_every_gated_and_public_text(tmp_path: Path) -> None:
    text = " ".join(extract_pages(run(tmp_path).read_bytes()))
    flat = "".join(text.split())
    for needle in (
        f"Acme Fake Co {CANARY}",
        "2026 sponsor, fake",
        "Launch week fake",
        "Ten thousand fake views.",
        "Sponsored video fake",
        "Ask me!",
        "Exampleland 40%",
        "Example Creator media kit",
        "Followers",
        "4,600",
    ):
        assert "".join(needle.split()) in flat, needle


def test_the_pdf_has_no_gate_artifacts_or_private_values(tmp_path: Path) -> None:
    data = run(tmp_path).read_bytes()
    flat = "".join(" ".join(extract_pages(data)).split())
    for banned in (SITE_KEY, "Turnstile", "Preferences", "Cookie", "Skip to"):
        assert banned not in flat


def test_metadata_is_scrubbed_and_fixed(tmp_path: Path) -> None:
    data = run(tmp_path).read_bytes()
    assert info_keys(data) == {"Title", "Producer", "CreationDate"}
    assert b"(Reach Around)" in data and b"D:20270310000000Z" in data
    for leaked in (
        b"HeadlessChrome",
        b"Mozilla",
        b"Skia",
        b"/Creator",
        b"/ModDate",
        b"/Author",
        b"/Metadata",
        getpass.getuser().encode(),
        str(tmp_path).encode(),
        os.path.expanduser("~").encode(),
    ):
        assert leaked not in data, leaked


def test_the_xref_still_points_at_every_object_after_the_scrub(
    tmp_path: Path,
) -> None:
    data = run(tmp_path).read_bytes()
    match = re.search(rb"startxref\n(\d+)", data)
    assert match is not None
    start = int(match.group(1))
    assert data[start : start + 4] == b"xref"
    rows = re.findall(rb"(\d{10}) (\d{5}) ([nf]) \n", data)
    assert rows
    used = 0
    for number, (offset, _, kind) in enumerate(rows):
        if kind == b"n":
            used += 1
            assert data[int(offset) :].startswith(b"%d 0 obj" % number), number
    assert used > 5


def test_output_is_reproducible_byte_for_byte(tmp_path: Path) -> None:
    first = run(tmp_path / "a")
    time.sleep(1.1)  # the stand-in browser stamps the current second
    second = run(tmp_path / "b")
    assert first.read_bytes() == second.read_bytes()


def test_a_different_date_changes_only_the_date(tmp_path: Path) -> None:
    a = run(tmp_path / "a").read_bytes()
    b = run(tmp_path / "b", created=date(2027, 4, 1)).read_bytes()
    assert a != b and len(a) == len(b)
    assert a.replace(b"20270310", b"20270401") == b


def test_the_file_is_owner_only_and_the_workdir_is_gone(tmp_path: Path) -> None:
    seen: list[Path] = []

    def spy(html: str, work: Path) -> bytes:
        seen.append(work)
        assert stat.S_IMODE(work.stat().st_mode) == 0o700
        return fake_renderer(html, work)

    path = run(tmp_path, spy)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(seen) == 1 and not seen[0].exists()


def test_the_workdir_is_gone_after_a_render_failure(tmp_path: Path) -> None:
    seen: list[Path] = []

    def boom(html: str, work: Path) -> bytes:
        seen.append(work)
        raise PdfError("render failed")

    with pytest.raises(PdfError):
        run(tmp_path, boom)
    assert len(seen) == 1 and not seen[0].exists()
    assert not (tmp_path / "private" / "kit.pdf").exists()


# --- no network at all --------------------------------------------------------


def _blocked(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("a network call was attempted")


def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    monkeypatch.setattr(socket, "gethostbyname", _blocked)


def test_the_block_bites(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)
    with pytest.raises(AssertionError, match="network"):
        socket.create_connection(("127.0.0.1", 9))
    with pytest.raises(AssertionError, match="network"):
        socket.getaddrinfo("example.com", 80)


def test_the_pipeline_makes_no_socket_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, site, gated = build_instance(tmp_path)  # builds before the block
    block_network(monkeypatch)
    result = build_kit_pdf(
        config,
        gated_dir=gated,
        site_dir=site,
        out_dir=tmp_path / "private",
        name="kit.pdf",
        created=CREATED,
        renderer=fake_renderer,
    )
    assert result.pages >= 1 and result.size == result.path.stat().st_size


# --- the leak check -----------------------------------------------------------


def test_policy_names_every_field_for_the_pdf() -> None:
    fields = set(KitConfig.model_fields) | {"unified_total"}
    in_pdf = {n for n, v in DISPLAY_POLICY.items() if v in ("public", "gated")}
    private = {n for n, v in DISPLAY_POLICY.items() if v == "private"}
    assert fields == set(DISPLAY_POLICY)
    assert PDF_EXCLUDED <= set(DISPLAY_POLICY)
    # every field is in the PDF, or private, or named as excluded: none by accident
    assert in_pdf | private | PDF_EXCLUDED == set(DISPLAY_POLICY)
    assert "turnstile_site_key" in PDF_EXCLUDED


def test_excluded_values_are_the_strings_of_private_and_excluded_fields() -> None:
    kit = make_config().kit
    assert kit is not None
    assert pdf_excluded_values(kit) == {"turnstile_site_key": SITE_KEY}


def test_a_private_value_in_the_rendered_text_fails_the_build(
    tmp_path: Path,
) -> None:
    def leaky(html: str, work: Path) -> bytes:
        return make_pdf([["Some kit text here", f"stray {SITE_KEY} value"]])

    with pytest.raises(PdfLeakError, match="turnstile_site_key") as caught:
        run(tmp_path, leaky)
    assert SITE_KEY not in str(caught.value)  # names the field, never the value
    assert not (tmp_path / "private" / "kit.pdf").exists()


def test_a_value_split_across_runs_or_cased_differently_is_still_found(
    tmp_path: Path,
) -> None:
    def leaky(html: str, work: Path) -> bytes:
        return make_pdf([[SITE_KEY[:8], SITE_KEY[8:].upper()]])

    with pytest.raises(PdfLeakError):
        run(tmp_path, leaky)


def test_a_clean_pdf_passes_the_same_check(tmp_path: Path) -> None:
    assert run(tmp_path).is_file()  # the check does not fire on ordinary text


def test_a_staged_page_that_lacks_the_configured_content_is_stale(
    tmp_path: Path,
) -> None:
    _, site, gated = build_instance(tmp_path)
    doc = kit_doc()
    doc["partners"].append({"name": "Newer Partner Not Built", "note": "added later"})
    with pytest.raises(PdfError, match="rebuild"):
        build_kit_pdf(
            make_config(doc),
            gated_dir=gated,
            site_dir=site,
            out_dir=tmp_path / "private",
            name="kit.pdf",
            created=CREATED,
            renderer=fake_renderer,
        )


def test_a_renderer_that_loses_text_fails_the_completeness_check(
    tmp_path: Path,
) -> None:
    def lossy(html: str, work: Path) -> bytes:
        return make_pdf([["only this survives"]])

    with pytest.raises(PdfError, match="missing"):
        run(tmp_path, lossy)


def with_cutover(day: str | None) -> SiteConfig:
    return SiteConfig.model_validate(
        {
            **make_config().model_dump(mode="json", exclude_unset=True),
            "cutover_date": day,
        }
    )


def _pdf_text(tmp_path: Path, **config: Any) -> str:
    config_obj, site, gated = build_instance(
        tmp_path, with_cutover(config.get("cutover_date"))
    )
    result = build_kit_pdf(
        config_obj,
        gated_dir=gated,
        site_dir=site,
        out_dir=tmp_path / "private",
        name="kit.pdf",
        created=CREATED,
        renderer=fake_renderer,
    )
    return "".join("".join(extract_pages(result.path.read_bytes())).split())


def test_the_pdf_prints_the_live_since_line_when_the_day_has_arrived(
    tmp_path: Path,
) -> None:
    text = _pdf_text(tmp_path, cutover_date="2026-11-08")
    assert "".join("Live since 2026-11-08 (UTC)".split()) in text


def test_the_pdf_has_no_line_when_unset_or_in_the_future(tmp_path: Path) -> None:
    assert "Livesince" not in _pdf_text(tmp_path / "a")
    future = (utc_today() + timedelta(days=5)).isoformat()
    assert "Livesince" not in _pdf_text(tmp_path / "b", cutover_date=future)


def test_a_renderer_that_drops_the_live_since_line_fails_the_check(
    tmp_path: Path,
) -> None:
    def without_line(html: str, work: Path) -> bytes:
        return fake_renderer(html.replace("Live since", "Zzz"), work)

    config_obj, site, gated = build_instance(tmp_path, with_cutover("2026-11-08"))
    with pytest.raises(PdfError, match="live-since"):
        build_kit_pdf(
            config_obj,
            gated_dir=gated,
            site_dir=site,
            out_dir=tmp_path / "private",
            name="kit.pdf",
            created=CREATED,
            renderer=without_line,
        )


def test_a_kit_with_nothing_gated_is_an_error(tmp_path: Path) -> None:
    _, site, gated = build_instance(tmp_path)
    plain = make_config(
        {k: v for k, v in kit_doc().items() if k in ("title", "about", "metrics")}
    )
    with pytest.raises(PdfError, match="gated"):
        build_kit_pdf(
            plain,
            gated_dir=gated,
            site_dir=site,
            out_dir=tmp_path / "private",
            name="kit.pdf",
            created=CREATED,
            renderer=fake_renderer,
        )


# --- limits -------------------------------------------------------------------


def test_the_size_cap(tmp_path: Path) -> None:
    def huge(html: str, work: Path) -> bytes:
        return fake_renderer(html, work) + b"\n%" + b"x" * MAX_PDF_BYTES

    with pytest.raises(PdfError, match="larger"):
        run(tmp_path, huge)
    assert not (tmp_path / "private" / "kit.pdf").exists()


def test_the_page_cap(tmp_path: Path) -> None:
    def many(html: str, work: Path) -> bytes:
        return make_pdf([["a b c"]] * (MAX_PAGES + 1))

    with pytest.raises(PdfError, match="pages"):
        run(tmp_path, many)


# --- scrub, alone -------------------------------------------------------------


def test_scrub_refuses_a_pdf_it_cannot_read_rather_than_passing_it_on() -> None:
    with pytest.raises(PdfError):
        scrub_pdf(b"%PDF-1.4\nnothing useful", title="t", created=CREATED)


def test_scrub_checks_that_the_new_info_fits_the_old_one() -> None:
    data = make_pdf([["a"]], title="short", creator=False)
    with pytest.raises(PdfError, match="fit"):
        scrub_pdf(data, title="t" * 5000, created=CREATED)


# --- the command --------------------------------------------------------------

CONFIG_YAML = (
    "version: 1\ncreator: {name: Example Creator}\n"
    "tabs:\n- id: start\n  title: Start\n  blocks:\n"
    "  - {id: one, title: One, url: 'https://example.com/1'}\n"
    "features: {kit_gate: true}\nkit:\n"
    "  title: Example Creator media kit\n  about: Fake.\n"
    "  rates:\n  - {item: Sponsored video fake, price_text: Ask me}\n"
)


def command_args(cfg: Path, gated: Path, site: Path, out: Path) -> list[str]:
    return [
        "kit",
        "pdf",
        "--config",
        str(cfg),
        "--gated-out",
        str(gated),
        "--site-out",
        str(site),
        "--out-dir",
        str(out),
        "--date",
        "2027-03-10",
    ]


def test_command_prints_the_path_and_writes_the_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setattr(command, "chromium_renderer", lambda *a, **k: fake_renderer)
    conf = tmp_path / "conf"
    conf.mkdir(mode=0o700)
    cfg = conf / "site.yaml"
    cfg.write_text(CONFIG_YAML, encoding="utf-8")
    site, gated, out = tmp_path / "site", tmp_path / "gated", tmp_path / "private"
    build_site(load_site_config(cfg), site, today=CREATED, gated_out=gated)
    code = main(command_args(cfg, gated, site, out))
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert captured.out == f"{out / 'kit.pdf'}\n"
    assert (out / "kit.pdf").is_file()


def test_command_refuses_to_write_next_to_the_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setattr(command, "chromium_renderer", lambda *a, **k: fake_renderer)
    conf = tmp_path / "conf"
    conf.mkdir(mode=0o700)
    cfg = conf / "site.yaml"
    cfg.write_text(CONFIG_YAML, encoding="utf-8")
    site, gated = tmp_path / "site", tmp_path / "gated"
    build_site(load_site_config(cfg), site, today=CREATED, gated_out=gated)
    code = main(command_args(cfg, gated, site, conf / "pdf"))
    err = capsys.readouterr().err
    assert code == 1 and err.startswith("error: ")
    assert not (conf / "pdf").exists()


def test_command_errors_are_one_line_and_exit_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        command_args(
            tmp_path / "missing.yaml", tmp_path, tmp_path, tmp_path / "private"
        )
    )
    err = capsys.readouterr().err
    assert code == 1 and err.startswith("error: ") and "Traceback" not in err


def test_command_refuses_a_bad_date() -> None:
    args = command_args(Path("x"), Path("x"), Path("x"), Path("x"))
    args[-1] = "yesterday"
    with pytest.raises(SystemExit) as caught:
        main(args)
    assert caught.value.code == 2
