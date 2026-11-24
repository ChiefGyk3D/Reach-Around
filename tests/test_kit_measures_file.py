# SPDX-License-Identifier: Apache-2.0
"""The owner-only measures file (kit v2 slice 7, #632). Synthetic data only."""

from __future__ import annotations

import json
import os
import stat
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import reach_around.commands.kit as kit_command
from kitpdfharness import SITE_KEY, TODAY, build_instance, kit_doc, make_config
from reach_around.cli import main
from reach_around.config import SiteConfig
from reach_around.kit.measures_file import (
    FILE_NAME,
    SCHEMA_VERSION,
    MeasuresFileError,
    build_measures_file,
    load_measures_file,
)
from reach_around.kit.output import OutputError
from reach_around.kit.pdf import PdfError
from reach_around.site.kit import GATED_POLICY_FILE
from test_kit_private_measures import KIT as PRIVATE_KIT
from test_kit_private_measures import SECRET_FOLLOWERS, make_private

CREATED = date(2027, 3, 10)


def write(
    tmp_path: Path, config: SiteConfig, site: Path, gated: Path, **extra: Any
) -> Path:
    return build_measures_file(
        config,
        site_dir=site,
        gated_dir=gated,
        out_dir=tmp_path / "private",
        generated=CREATED,
        **extra,
    )


def instance(
    tmp_path: Path, doc: dict[str, Any] | None = None
) -> tuple[SiteConfig, Path, Path]:
    return build_instance(tmp_path, make_config(doc or kit_doc()))


def published_hits(*roots: Path) -> list[Path]:
    """Every file under the roots whose name is the measures file, or whose text
    mentions its name. Collected loosely, asserted strictly by the callers."""
    found: list[Path] = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.name == FILE_NAME or FILE_NAME.encode() in path.read_bytes():
                found.append(path)
    return found


# --- what the file holds -------------------------------------------------------


def test_the_file_holds_the_resolved_measures_with_trust_and_date(
    tmp_path: Path,
) -> None:
    config, site, gated = instance(tmp_path)
    path = write(tmp_path, config, site, gated)
    assert path == tmp_path / "private" / FILE_NAME
    doc = load_measures_file(path)
    assert doc["schema_version"] == SCHEMA_VERSION == 1
    assert doc["generated"] == "2027-03-10"
    rows = {(m["platform"], m["key"]): m for m in doc["measures"]}
    twitch = rows[("twitch", "followers")]
    assert twitch["value"] == 1200
    assert twitch["unit"] == "follows"
    assert twitch["as_of"] == "2027-03-01"
    assert twitch["trust"]["label"] == "hand-kept"
    assert twitch["trust"]["method"]
    assert rows[("youtube", "followers")]["value"] == 3400


def test_the_file_is_deterministic_and_owner_only(tmp_path: Path) -> None:
    config, site, gated = instance(tmp_path)
    path = write(tmp_path, config, site, gated)
    first = path.read_bytes()
    write(tmp_path, config, site, gated, overwrite=True)
    assert path.read_bytes() == first
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert first.endswith(b"\n")


def test_nothing_but_measures_is_in_the_file(tmp_path: Path) -> None:
    config, site, gated = instance(tmp_path)
    text = write(tmp_path, config, site, gated).read_text(encoding="utf-8")
    assert "Acme Fake Co" not in text  # a partner
    assert "Sponsored video fake" not in text  # a rate
    assert SITE_KEY not in text
    assert "Exampleland" not in text  # demographics


# --- the wall -------------------------------------------------------------------


def test_the_file_never_lands_in_a_published_directory(tmp_path: Path) -> None:
    config, site, gated = instance(tmp_path)
    for inside in (site, site / "kit", gated, gated / "sub"):
        with pytest.raises(OutputError):
            build_measures_file(
                config,
                site_dir=site,
                gated_dir=gated,
                out_dir=inside,
                generated=CREATED,
            )
    # the config's directory is protected too
    with pytest.raises(OutputError):
        write(tmp_path, config, site, gated, protect=[tmp_path / "private"])
    assert not (tmp_path / "private").exists()


def test_the_name_is_in_no_published_artifact(tmp_path: Path) -> None:
    config, site, gated = instance(tmp_path)
    write(tmp_path, config, site, gated)
    assert list(site.rglob("*")) and list(gated.rglob("*"))  # something collected
    assert published_hits(site, gated) == []


def test_the_name_guard_bites(tmp_path: Path) -> None:
    """Negative case: the scan above finds the file by name and by mention."""
    config, site, gated = instance(tmp_path)
    (site / FILE_NAME).write_text("{}", encoding="utf-8")
    (gated / "other.txt").write_text(f"see {FILE_NAME}", encoding="utf-8")
    assert {p.name for p in published_hits(site, gated)} == {FILE_NAME, "other.txt"}


def test_a_stale_policy_record_is_refused(tmp_path: Path) -> None:
    config, site, gated = instance(tmp_path)
    (gated / GATED_POLICY_FILE).write_text("0" * 64, encoding="ascii")
    with pytest.raises(PdfError, match="display policy"):
        write(tmp_path, config, site, gated)
    assert not (tmp_path / "private" / FILE_NAME).exists()


def test_a_missing_policy_record_is_refused(tmp_path: Path) -> None:
    config, site, gated = instance(tmp_path)
    (gated / GATED_POLICY_FILE).unlink()
    with pytest.raises(PdfError, match="policy record"):
        write(tmp_path, config, site, gated)


def test_no_measures_means_no_file(tmp_path: Path) -> None:
    doc = {"title": "Example Creator media kit", "about": "A fake creator."}
    config, site, gated = instance(tmp_path, doc)
    with pytest.raises(MeasuresFileError, match="no measures"):
        write(tmp_path, config, site, gated)
    assert not (tmp_path / "private").exists()


def test_an_existing_file_is_kept_without_overwrite(tmp_path: Path) -> None:
    config, site, gated = instance(tmp_path)
    path = write(tmp_path, config, site, gated)
    path.write_text("keep", encoding="utf-8")
    with pytest.raises(OutputError, match="overwrite"):
        write(tmp_path, config, site, gated)
    assert path.read_text(encoding="utf-8") == "keep"


# --- private figures ------------------------------------------------------------


def private_doc() -> dict[str, Any]:
    doc = kit_doc()
    doc["engagement"] = PRIVATE_KIT["engagement"]
    doc["analytics"] = PRIVATE_KIT["analytics"]
    return doc


def test_a_private_field_is_not_in_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = private_doc()
    config, site, gated = instance(tmp_path, doc)
    control = write(tmp_path, config, site, gated).read_text(encoding="utf-8")
    assert "5555555" in control and "765432" in control  # shown while not private
    make_private(monkeypatch, "analytics")
    make_private(monkeypatch, "engagement")
    # the pages are rebuilt under the new policy, as a real run would
    config, site, gated = build_instance(tmp_path / "again", make_config(doc))
    out = tmp_path / "again" / "private"
    path = build_measures_file(
        config,
        site_dir=site,
        gated_dir=gated,
        out_dir=out,
        generated=CREATED,
    )
    text = path.read_text(encoding="utf-8")
    for needle in ("5555555", "9162534", "Canary tile label", "765432"):
        assert needle not in text, needle
    assert "1200" in text  # a gated headline number still is


def test_a_stale_page_with_now_private_figures_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, site, gated = instance(tmp_path, private_doc())
    make_private(monkeypatch, "engagement")
    with pytest.raises(PdfError, match="display policy"):
        write(tmp_path, config, site, gated)


def test_a_private_small_figure_is_needled_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last check: a private figure that reaches the document anyway stops
    the write, naming the field and never the value."""
    from reach_around.kit import measures_file

    config, site, gated = instance(tmp_path)
    monkeypatch.setattr(
        measures_file,
        "private_measure_strings",
        lambda kit, numbers: {"metrics.twitch.followers.Followers": "1200"},
    )
    with pytest.raises(MeasuresFileError) as caught:
        write(tmp_path, config, site, gated)
    assert "metrics.twitch.followers.Followers" in str(caught.value)
    assert "1200" not in str(caught.value)
    assert not (tmp_path / "private" / FILE_NAME).exists()


def test_the_turnstile_key_is_a_needle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reach_around.kit import measures_file

    config, site, gated = instance(tmp_path)
    monkeypatch.setattr(
        measures_file,
        "pdf_excluded_values",
        lambda kit: {"turnstile_site_key": "Followers"},
    )
    with pytest.raises(MeasuresFileError, match="turnstile_site_key"):
        write(tmp_path, config, site, gated)


# --- the reader -----------------------------------------------------------------


def good(tmp_path: Path) -> dict[str, Any]:
    config, site, gated = instance(tmp_path)
    doc: dict[str, Any] = json.loads(
        write(tmp_path, config, site, gated).read_text("utf-8")
    )
    return doc


def put(tmp_path: Path, doc: Any, name: str = "x.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_the_reader_refuses_another_version(tmp_path: Path) -> None:
    doc = good(tmp_path)
    doc["schema_version"] = 2
    with pytest.raises(MeasuresFileError, match="schema_version"):
        load_measures_file(put(tmp_path, doc))


def test_the_reader_refuses_an_unknown_key(tmp_path: Path) -> None:
    doc = good(tmp_path)
    doc["measures"][0]["extra"] = 1
    with pytest.raises(MeasuresFileError, match="measures"):
        load_measures_file(put(tmp_path, doc))
    doc = good(tmp_path / "b")
    doc["partners"] = ["x"]
    with pytest.raises(MeasuresFileError, match="partners"):
        load_measures_file(put(tmp_path, doc, "y.json"))


def test_the_reader_refuses_a_private_row(tmp_path: Path) -> None:
    doc = good(tmp_path)
    doc["measures"][0]["visibility"] = "private"
    with pytest.raises(MeasuresFileError, match="visibility"):
        load_measures_file(put(tmp_path, doc))


def test_the_reader_refuses_a_link_and_bad_json(tmp_path: Path) -> None:
    doc = good(tmp_path)
    real = put(tmp_path, doc, "real.json")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(MeasuresFileError, match="link"):
        load_measures_file(link)
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(MeasuresFileError, match="JSON"):
        load_measures_file(bad)
    big = tmp_path / "big.json"
    big.write_bytes(b" " * (5 * 1024 * 1024))
    with pytest.raises(MeasuresFileError, match="large"):
        load_measures_file(big)


def test_the_reader_refuses_a_duplicate_key(tmp_path: Path) -> None:
    path = tmp_path / "dup.json"
    path.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")
    with pytest.raises(MeasuresFileError, match="duplicate"):
        load_measures_file(path)


# --- the command ----------------------------------------------------------------


def run_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config: SiteConfig,
    site: Path,
    gated: Path,
    out_dir: Path,
) -> int:
    cfg = tmp_path / "cfg" / "site.yaml"
    cfg.parent.mkdir()
    cfg.write_text("x", encoding="utf-8")
    monkeypatch.setattr(kit_command, "load_site_config", lambda path: config)
    return main(
        [
            "kit",
            "measures",
            "--config",
            str(cfg),
            "--site-out",
            str(site),
            "--gated-out",
            str(gated),
            "--out-dir",
            str(out_dir),
            "--date",
            "2027-03-10",
        ]
    )


def test_the_command_prints_only_the_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, site, gated = instance(tmp_path)
    out_dir = tmp_path / "private"
    code = run_command(monkeypatch, tmp_path, config, site, gated, out_dir)
    out = capsys.readouterr()
    assert code == 0
    assert out.out.strip() == str(out_dir / FILE_NAME)
    assert "private" in out.err
    for text in (out.out, out.err):
        assert "1200" not in text and "Followers" not in text
    assert os.path.isfile(out.out.strip())


def test_the_command_refuses_a_published_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, site, gated = instance(tmp_path)
    code = run_command(monkeypatch, tmp_path, config, site, gated, site / "kit")
    assert code == 1
    assert "error:" in capsys.readouterr().err
    assert published_hits(site, gated) == []


# --- the docs -------------------------------------------------------------------


def test_the_schema_is_documented() -> None:
    docs = (Path(__file__).parent.parent / "docs" / "kit.md").read_text("utf-8")
    assert FILE_NAME in docs
    assert f"schema_version` is {SCHEMA_VERSION}" in docs
    for field in ("generated", "policy", "measures", "trust", "as_of", "period"):
        assert f"`{field}`" in docs, field


_ = (TODAY, SECRET_FOLLOWERS)
