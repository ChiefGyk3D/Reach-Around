# SPDX-License-Identifier: Apache-2.0
"""Hardening of the two-root kit build: filesystem identity, no-follow writes,
stale gated output, the leak guard's canonical forms and the gated page's
requests. Synthetic data only."""

from __future__ import annotations

import json
import os
import stat
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
import yaml
from test_kit_build import (
    CANARY,
    TODAY,
    gated_doc,
    kit_doc,
    make_config,
    tree_bytes,
)

from reach_around.cli import main
from reach_around.site import kit as kit_module
from reach_around.site.build import build_site
from reach_around.site.kit import GATED_FILE, KitLeakError

PARTNER = f"Acme Fake Co {CANARY}"


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


# --- 1 and 2: root isolation by filesystem identity ---------------------------


def test_a_gated_root_that_is_a_symlink_to_the_public_tree_is_refused(
    tmp_path: Path,
) -> None:
    out = tmp_path / "public"
    out.mkdir()
    (out / "kit").mkdir()
    for name, target in (("alias-root", out), ("alias-sub", out / "kit")):
        (tmp_path / name).symlink_to(target, target_is_directory=True)
        with pytest.raises(ValueError, match=r"gated"):
            build_site(make_config(), out, today=TODAY, gated_out=tmp_path / name)
    assert not (out / GATED_FILE).exists()
    assert not (out / "kit" / GATED_FILE).exists()


def test_a_gated_root_that_is_a_symlink_to_a_parent_of_the_public_tree_is_refused(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    out = real / "public"
    out.mkdir(parents=True)
    (tmp_path / "up").symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match=r"gated"):
        build_site(make_config(), out, today=TODAY, gated_out=tmp_path / "up")
    # and a not-yet-created child of an alias of the public tree
    (tmp_path / "pub-alias").symlink_to(out, target_is_directory=True)
    with pytest.raises(ValueError, match=r"gated"):
        build_site(
            make_config(), out, today=TODAY, gated_out=tmp_path / "pub-alias" / "g"
        )
    assert not list(real.rglob(GATED_FILE.name))


def test_a_public_output_reached_through_an_alias_is_refused(tmp_path: Path) -> None:
    gated = tmp_path / "gated"
    gated.mkdir()
    (tmp_path / "pub-alias").symlink_to(gated, target_is_directory=True)
    with pytest.raises(ValueError, match=r"gated"):
        build_site(make_config(), tmp_path / "pub-alias", today=TODAY, gated_out=gated)


def test_case_alias_of_the_public_tree_is_refused_when_the_filesystem_folds_case(
    tmp_path: Path,
) -> None:
    out = tmp_path / "Public"
    out.mkdir()
    alias = tmp_path / "pUBLIC"
    # Only a case-insensitive filesystem makes these the same directory; the
    # symlink tests above cover the same identity check everywhere else.
    if alias.exists() and alias.samefile(out):
        with pytest.raises(ValueError, match=r"gated"):
            build_site(make_config(), out, today=TODAY, gated_out=alias)
    else:
        assert not alias.exists()


def test_the_gated_root_is_created_private(tmp_path: Path) -> None:
    gated = tmp_path / "gated"
    build_site(make_config(), tmp_path / "public", today=TODAY, gated_out=gated)
    assert stat.S_IMODE(gated.stat().st_mode) == 0o700
    assert stat.S_IMODE((gated / GATED_FILE).stat().st_mode) == 0o600


def test_a_symlink_at_the_gated_file_is_refused_and_nothing_is_overwritten(
    tmp_path: Path,
) -> None:
    out, gated = tmp_path / "public", tmp_path / "gated"
    build_site(make_config(), out, today=TODAY, gated_out=gated)
    before = snapshot(out)
    (gated / GATED_FILE).unlink()
    (gated / GATED_FILE).symlink_to(out / "kit" / "index.html")
    with pytest.raises(ValueError, match=r"symbolic link"):
        build_site(make_config(), out, today=TODAY, gated_out=gated)
    assert snapshot(out) == before
    assert CANARY.encode() not in tree_bytes(out)


def test_a_hard_link_at_the_gated_file_never_writes_through(tmp_path: Path) -> None:
    out, gated = tmp_path / "public", tmp_path / "gated"
    build_site(make_config(), out, today=TODAY, gated_out=gated)
    shared = out / "kit" / "index.html"
    (gated / GATED_FILE).unlink()
    os.link(shared, gated / GATED_FILE)
    build_site(make_config(), out, today=TODAY, gated_out=gated)
    assert CANARY.encode() not in tree_bytes(out)
    assert CANARY in (gated / GATED_FILE).read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", ["file", "dangling-link"])
def test_a_public_output_already_holding_the_gated_name_is_refused(
    tmp_path: Path, kind: str
) -> None:
    out = tmp_path / "public"
    out.mkdir()
    if kind == "file":
        (out / GATED_FILE).write_text("x", encoding="utf-8")
    else:
        (out / GATED_FILE).symlink_to(tmp_path / "nowhere")
    with pytest.raises(ValueError, match=r"public output"):
        build_site(make_config(), out, today=TODAY, gated_out=tmp_path / "gated")


# --- 5b: a partial build leaves an honest, safe state ---------------------------


def test_a_gated_write_failure_happens_before_the_public_tree_is_replaced(
    tmp_path: Path,
) -> None:
    out, gated = tmp_path / "public", tmp_path / "gated"
    build_site(make_config(), out, today=TODAY, gated_out=gated)
    before = snapshot(out)
    gated.chmod(0o500)
    try:
        with pytest.raises(OSError):
            build_site(make_config(), out, today=TODAY, gated_out=gated)
    finally:
        gated.chmod(0o700)
    assert snapshot(out) == before
    assert sorted(p.name for p in gated.iterdir()) == [GATED_FILE.name]


def test_a_failed_publish_step_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, gated = tmp_path / "public", tmp_path / "gated"

    def boom(src: Any, dst: Any) -> None:
        raise OSError("simulated")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match=r"simulated"):
        build_site(make_config(), out, today=TODAY, gated_out=gated)
    assert CANARY.encode() not in tree_bytes(out)
    assert list(gated.iterdir()) == []


# --- 3: the former gated artifact -------------------------------------------------


def write_instance(tmp_path: Path, kit: dict[str, Any], gate: bool = True) -> Path:
    raw = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": [
            {
                "id": "start",
                "title": "Start",
                "blocks": [{"id": "one", "title": "One", "url": "https://example.com"}],
            }
        ],
        "features": {"kit_gate": gate},
        "kit": kit,
    }
    path = tmp_path / "instance" / "site.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def run(config: Path, out: Path, gated: Path | None) -> int:
    argv = ["build", "--config", str(config), "--out", str(out)]
    if gated is not None:
        argv += ["--gated-out", str(gated)]
    return main(argv)


def test_a_build_after_the_gated_content_is_removed_must_name_the_old_destination(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out, gated = tmp_path / "out", tmp_path / "gated"
    config = write_instance(tmp_path, gated_doc())
    assert run(config, out, gated) == 0
    record = tmp_path / "out.gated.json"
    assert json.loads(record.read_text(encoding="utf-8")) == {
        "gated_out": str(gated.resolve())
    }
    assert not list(out.rglob("*.gated.json"))
    # the kit loses its gated content
    write_instance(tmp_path, kit_doc(), gate=False)
    capsys.readouterr()
    assert run(config, out, None) == 1
    err = capsys.readouterr().err
    assert "--gated-out" in err and str(gated.resolve()) in err
    assert (gated / GATED_FILE).is_file()
    # a different directory is refused too
    assert run(config, out, tmp_path / "elsewhere") == 1
    assert (gated / GATED_FILE).is_file()
    # naming the old directory removes the page and clears the record
    assert run(config, out, gated) == 0
    assert not (gated / GATED_FILE).exists()
    assert not record.exists()
    # and a plain build is allowed again
    assert run(config, out, None) == 0


def test_a_site_that_never_had_a_gated_page_needs_no_flag(tmp_path: Path) -> None:
    config = write_instance(tmp_path, kit_doc(), gate=False)
    assert run(config, tmp_path / "out", None) == 0
    assert not (tmp_path / "out.gated.json").exists()


# --- 4: the leak guard compares canonical forms -----------------------------------


def encodings(text: str) -> dict[str, str]:
    half, rest = text[:6], text[6:]
    return {
        "numeric-entity": "".join(f"&#{ord(c)};" for c in text),
        "hex-entity": "".join(f"&#x{ord(c):x};" for c in text),
        "percent": quote(text, safe=""),
        "double-percent": quote(quote(text, safe=""), safe=""),
        "casefold": text.upper(),
        "fullwidth": "".join(
            chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in text
        ),
        "json": json.dumps(text, ensure_ascii=True).strip('"').replace("-", "\\u002d"),
        "whitespace": text.replace(" ", "\n  "),
        "split-by-markup": f"{half}<span></span>{rest}",
        "split-by-block": f"{half}</p><p>{rest}",
    }


def override_with(tmp_path: Path, body: str) -> Path:
    overrides = tmp_path / "overrides"
    (overrides / "templates").mkdir(parents=True, exist_ok=True)
    (overrides / "templates" / "kit.html").write_text(
        '{% extends "base.html" %}{% block content %}{% raw %}'
        f"{body}{{% endraw %}}{{% endblock %}}",
        encoding="utf-8",
    )
    return overrides


@pytest.mark.parametrize("name", list(encodings(PARTNER)))
def test_the_guard_catches_equivalent_forms(tmp_path: Path, name: str) -> None:
    overrides = override_with(tmp_path, f"<p>{encodings(PARTNER)[name]}</p>")
    with pytest.raises(KitLeakError):
        build_site(
            make_config(),
            tmp_path / "public",
            today=TODAY,
            gated_out=tmp_path / "gated",
            overrides=overrides,
        )
    assert not (tmp_path / "public").exists()


@pytest.mark.parametrize("form", ["raw", "escaped"])
def test_the_guard_still_catches_the_plain_forms(tmp_path: Path, form: str) -> None:
    text = PARTNER if form == "raw" else PARTNER.replace(" ", "&nbsp;")
    overrides = override_with(tmp_path, f"<p>{text}</p>")
    with pytest.raises(KitLeakError):
        build_site(
            make_config(),
            tmp_path / "public",
            today=TODAY,
            gated_out=tmp_path / "gated",
            overrides=overrides,
        )


def test_a_gated_date_is_guarded_but_a_date_that_is_also_public_is_not(
    tmp_path: Path,
) -> None:
    kit = gated_doc()
    kit["demographics"] = [
        {"label": "Top city", "value": "Exampleville", "as_of": "2031-07-04"}
    ]
    config = make_config(kit)
    # 2027-03-01 is a public metric date and a safe repeat; the build is fine
    build_site(
        config, tmp_path / "ok", today=date(2027, 3, 10), gated_out=tmp_path / "g"
    )
    overrides = override_with(tmp_path, "<p>seen on 2031-07-04</p>")
    with pytest.raises(KitLeakError, match=r"demographics\[0\]\.as_of"):
        build_site(
            config,
            tmp_path / "public",
            today=TODAY,
            gated_out=tmp_path / "gated2",
            overrides=overrides,
        )


def test_the_floor_is_a_named_constant() -> None:
    assert kit_module.GUARD_MIN_LENGTH == 6


def test_the_public_tree_without_overrides_is_clean_in_every_form(
    tmp_path: Path,
) -> None:
    out = tmp_path / "public"
    build_site(make_config(), out, today=TODAY, gated_out=tmp_path / "gated")
    for name, form in encodings(PARTNER).items():
        assert form.encode() not in tree_bytes(out), name


# --- 5: the gated page makes no third-party request ---------------------------------


def test_the_gated_page_drops_a_remote_avatar_but_public_pages_keep_it(
    tmp_path: Path,
) -> None:
    config = make_config(
        creator={"name": "Example Creator", "avatar": "https://example.com/a.png"}
    )
    out, gated = tmp_path / "public", tmp_path / "gated"
    build_site(config, out, today=TODAY, gated_out=gated)
    page = (gated / GATED_FILE).read_text(encoding="utf-8")
    assert "<img" not in page and "example.com/a.png" not in page
    assert "https://example.com/a.png" in (out / "kit" / "index.html").read_text(
        encoding="utf-8"
    )
