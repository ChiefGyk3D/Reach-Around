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
from reach_around.site import build as build_module
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
        "percent-four-levels": quote(
            quote(quote(quote(text, safe=""), safe=""), safe=""), safe=""
        ),
        "js-hex": "".join(f"\\x{ord(c):02x}" for c in text),
        "js-unicode": "".join(f"\\u{ord(c):04x}" for c in text),
        "css-escape": "".join(f"\\{ord(c):x} " for c in text),
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


# --- round 2 ------------------------------------------------------------------------


def test_decoding_runs_to_a_bounded_fixed_point() -> None:
    deep = PARTNER
    for _ in range(9):
        deep = quote(deep, safe="")
    assert kit_module.canonical(deep) == kit_module.canonical(PARTNER)
    too_deep = deep
    for _ in range(3):
        too_deep = quote(too_deep, safe="")
    assert kit_module.canonical(too_deep) != kit_module.canonical(PARTNER)


def test_binary_assets_are_not_canonicalized(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []
    real = kit_module.canonical

    def spy(text: str, tags: str = "") -> str:
        seen.append(len(text))
        return real(text, tags)

    monkeypatch.setattr(kit_module, "canonical", spy)
    big = b"\x89PNG" + b"\x00" * (4 * 1024 * 1024)
    files = {
        Path("static/big.png"): big,
        Path("static/font.woff2"): big,
        Path("index.html"): b"<p>hello</p>",
    }
    kit_module.check_no_gated_text(files, make_config().kit)
    assert seen and max(seen) < 1000


def test_work_limits_fail_with_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = make_config().kit
    files = {Path("index.html"): b"<p>" + b"x" * 5000 + b"</p>"}
    with monkeypatch.context() as patch:
        patch.setattr(kit_module, "GUARD_MAX_BYTES", 1000)
        with pytest.raises(kit_module.KitGuardLimitError, match=r"byte"):
            kit_module.check_no_gated_text(files, kit)
    monkeypatch.setattr(kit_module, "GUARD_MAX_NEEDLES", 2)
    with pytest.raises(kit_module.KitGuardLimitError, match=r"value"):
        kit_module.check_no_gated_text({Path("index.html"): b"x"}, kit)


def test_stale_staging_files_are_removed_and_nothing_else(tmp_path: Path) -> None:
    gated = tmp_path / "gated"
    gated.mkdir(mode=0o700)
    stale = gated / ".kit.html.0123456789abcdef.tmp"
    stale.write_text("old", encoding="utf-8")
    keep = [gated / ".kit.html.zz.tmp", gated / "notes.tmp", gated / "other.txt"]
    for path in keep:
        path.write_text("mine", encoding="utf-8")
    build_site(make_config(), tmp_path / "public", today=TODAY, gated_out=gated)
    assert not stale.exists()
    assert all(path.read_text(encoding="utf-8") == "mine" for path in keep)


def test_an_existing_gated_root_open_to_others_is_refused(tmp_path: Path) -> None:
    gated = tmp_path / "gated"
    gated.mkdir()
    gated.chmod(0o755)
    out = tmp_path / "public"
    with pytest.raises(ValueError, match=r"permissions"):
        build_site(make_config(), out, today=TODAY, gated_out=gated)
    assert not out.exists()
    gated.chmod(0o700)
    build_site(make_config(), out, today=TODAY, gated_out=gated)


def css_overrides(tmp_path: Path, css: str) -> Path:
    overrides = tmp_path / "overrides"
    overrides.mkdir(parents=True, exist_ok=True)
    (overrides / "custom.css").write_text(css, encoding="utf-8")
    return overrides


REMOTE_CSS = [
    '@import "local.css";',
    "body { background: url(https://example.com/x.png); }",
    "body { background: url( 'HTTP://example.com/x.png' ); }",
    'body { background: url("//example.com/x.png"); }',
    "body { background: u\\72 l(https://example.com/x.png); }",
    'body { background: image-set("https://example.com/x.png" 1x); }',
    "body { background: url(\\68 ttps://example.com/x.png); }",
    "/* c */ @IMPORT url(x.css);",
]


@pytest.mark.parametrize("css", REMOTE_CSS)
def test_a_linked_stylesheet_with_remote_loads_is_refused(css: str) -> None:
    page = '<link rel="stylesheet" href="/static/x.css">'
    files = {Path("static/x.css"): css.encode()}
    with pytest.raises(kit_module.KitRemoteRequestError, match=r"static/x\.css") as e:
        kit_module.check_gated_requests(page, files)
    assert "example.com" not in str(e.value)


def test_local_stylesheets_and_anchors_are_fine() -> None:
    page = (
        '<link rel="stylesheet" href="/static/x.css"><a href="https://example.com/">'
        'p</a><img src="/static/a.png" alt=""><style>a{background:url(b.png)}</style>'
    )
    css = b"a { background: url(fonts/a.woff2); } /* https://example.com */"
    kit_module.check_gated_requests(page, {Path("static/x.css"): css})


@pytest.mark.parametrize(
    "style", ["a{background:url(https://example.com/x)}", "@import 'x';"]
)
def test_inline_style_with_remote_loads_is_refused(style: str) -> None:
    with pytest.raises(kit_module.KitRemoteRequestError, match=r"inline style"):
        kit_module.check_gated_requests(f"<style>{style}</style>", {})
    with pytest.raises(kit_module.KitRemoteRequestError, match=r"inline style"):
        kit_module.check_gated_requests(f'<p style="{style}">x</p>', {})


def test_the_build_refuses_remote_css_in_custom_css(tmp_path: Path) -> None:
    overrides = css_overrides(
        tmp_path, "body { background: url(https://example.com/x); }"
    )
    with pytest.raises(ValueError, match=r"custom\.css") as caught:
        build_site(
            make_config(),
            tmp_path / "public",
            today=TODAY,
            gated_out=tmp_path / "gated",
            overrides=overrides,
        )
    assert "example.com" not in str(caught.value)
    assert not (tmp_path / "public").exists()


def test_the_build_refuses_a_remote_theme_image_on_the_gated_page(
    tmp_path: Path,
) -> None:
    config = make_config(theme={"background": "https://example.com/bg.png"})
    with pytest.raises(
        kit_module.KitRemoteRequestError, match=r"theme\.[0-9a-f]+\.css"
    ):
        build_site(
            config,
            tmp_path / "public",
            today=TODAY,
            gated_out=tmp_path / "gated",
        )
    assert not (tmp_path / "public").exists()
    # without gated content there is no gated page to protect
    build_site(
        make_config(
            kit_doc(), features={"kit_gate": False}, theme=config.theme.model_dump()
        ),
        tmp_path / "ok",
        today=TODAY,
    )


def test_remote_markup_in_the_gated_page_fails_the_build(tmp_path: Path) -> None:
    overrides = tmp_path / "overrides"
    (overrides / "templates").mkdir(parents=True)
    (overrides / "templates" / "kit_gated.html").write_text(
        '{% extends "base.html" %}{% block content %}'
        '<img src="https://example.com/p.png" alt="">{% endblock %}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"gated page") as caught:
        build_site(
            make_config(),
            tmp_path / "public",
            today=TODAY,
            gated_out=tmp_path / "gated",
            overrides=overrides,
        )
    assert "example.com" not in str(caught.value)


def test_a_root_that_changes_between_the_two_checks_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, gated = tmp_path / "public", tmp_path / "gated"
    out.mkdir()
    calls: list[int] = []
    real = kit_module.check_roots

    def swapping(public: Path, private: Path) -> None:
        calls.append(1)
        if len(calls) == 2:
            private.symlink_to(out, target_is_directory=True)
        real(public, private)

    monkeypatch.setattr(build_module, "check_roots", swapping)
    with pytest.raises(ValueError, match=r"gated"):
        build_site(make_config(), out, today=TODAY, gated_out=gated)
    assert len(calls) == 2
    assert not list(out.rglob(GATED_FILE.name))


def test_identity_alone_catches_an_alias_that_spelling_cannot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "public"
    out.mkdir()
    (tmp_path / "alias").symlink_to(out, target_is_directory=True)
    # With name resolution switched off, only the (device, inode) comparison can
    # tell that the alias is the public directory.
    monkeypatch.setattr(os.path, "realpath", lambda p, **kw: str(p))
    with pytest.raises(ValueError, match=r"gated"):
        kit_module.check_roots(out, tmp_path / "alias")
    with pytest.raises(ValueError, match=r"gated"):
        kit_module.check_roots(tmp_path / "alias", out)


# --- content, not file name, decides how a file is searched -------------------------


@pytest.mark.parametrize("name", ["static/pic.png", "static/f.woff", "data.bin"])
def test_text_content_under_a_binary_looking_name_is_canonicalized(name: str) -> None:
    text = quote(PARTNER, safe="").encode()
    with pytest.raises(KitLeakError):
        kit_module.check_no_gated_text({Path(name): b"x " + text}, make_config().kit)


@pytest.mark.parametrize("codec", ["utf-8", "utf-16-le", "utf-16-be"])
def test_gated_bytes_inside_a_binary_file_are_found(codec: str) -> None:
    blob = b"\x89PNG\x00\x01" + PARTNER.encode(codec) + b"\x00\xff" * 100
    with pytest.raises(KitLeakError):
        kit_module.check_no_gated_text(
            {Path("static/pic.png"): blob}, make_config().kit
        )


def test_a_genuinely_binary_file_is_cheap_whatever_its_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[int] = []
    real = kit_module.canonical

    def spy(text: str, tags: str = "") -> str:
        seen.append(len(text))
        return real(text, tags)

    monkeypatch.setattr(kit_module, "canonical", spy)
    big = b"\x89PNG\x00" + bytes(range(256)) * 20000
    files = {
        Path("static/a.dat"): big,
        Path("static/b.png"): big,
        Path("index.html"): b"<p>hello</p>",
    }
    kit_module.check_no_gated_text(files, make_config().kit)
    assert seen and max(seen) < 1000


TEXT_NAMES = ["a.html", "a.js", "a.css", "a.json", "a.svg", "a.map", "_headers"]


@pytest.mark.parametrize("name", TEXT_NAMES)
@pytest.mark.parametrize("flaw", [b"\x00", b"\xff\xfe\xc3("])
def test_a_known_text_file_with_nul_or_bad_utf8_fails_the_build(
    name: str, flaw: bytes
) -> None:
    kit = make_config().kit
    body = b"x " + quote(PARTNER, safe="").encode() + flaw
    with pytest.raises((KitLeakError, kit_module.KitSuspiciousFileError)):
        kit_module.check_no_gated_text({Path("static") / name: body}, kit)
    # without the canary the file is refused for being suspicious, by name
    with pytest.raises(kit_module.KitSuspiciousFileError, match=name) as caught:
        kit_module.check_no_gated_text({Path("static") / name: b"hello" + flaw}, kit)
    assert "hello" not in str(caught.value)


@pytest.mark.parametrize("name", TEXT_NAMES)
def test_a_known_text_file_is_always_canonicalized(name: str) -> None:
    text = quote(PARTNER, safe="").encode()
    with pytest.raises(KitLeakError):
        kit_module.check_no_gated_text(
            {Path("static") / name: b"x " + text}, make_config().kit
        )


def test_a_large_text_file_hits_the_per_file_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kit_module, "GUARD_MAX_FILE_BYTES", 100)
    with pytest.raises(kit_module.KitGuardLimitError, match=r"GUARD_MAX_FILE_BYTES"):
        kit_module.check_no_gated_text(
            {Path("static/x.png"): b"y" * 500}, make_config().kit
        )


# --- final round ----------------------------------------------------------------------

REMOTE_FORMS = {
    "srcset-second": '<img srcset="/a.png 1x, https://example.com/b.png 2x" alt="">',
    "picture-source": (
        '<picture><source srcset="/a.webp, //example.com/b.webp"></picture>'
    ),
    "preload": '<link rel="preload" href="https://example.com/f.woff2" as="font">',
    "prefetch": '<link rel="prefetch" href="//example.com/n">',
    "icon": '<link rel="icon" href="https://example.com/i.ico">',
    "stylesheet": '<link rel="stylesheet" href="https://example.com/s.css">',
    "base": '<base href="https://example.com/">',
    "meta-refresh": '<meta http-equiv="refresh" content="0; url=https://example.com/">',
    "form-action": '<form action="https://example.com/p"></form>',
    "object": '<object data="https://example.com/o"></object>',
    "embed": '<embed src="https://example.com/e">',
    "iframe": '<iframe src="//example.com/f"></iframe>',
    "audio": '<audio src="https://example.com/a.mp3"></audio>',
    "video-src": '<video src="https://example.com/v.mp4"></video>',
    "video-poster": '<video poster="https://example.com/p.png"></video>',
    "track": '<video><track src="https://example.com/t.vtt"></video>',
    "svg-use": '<svg><use href="//example.com/a.svg#x"></use></svg>',
    "svg-image": '<svg><image xlink:href="https://example.com/i.png"></image></svg>',
    "style-attr-image-set": (
        "<p style=\"background:image-set('https://example.com/a.png' 1x)\">x</p>"
    ),
    "style-attr-escape": '<p style="background:url(\\68 ttps://example.com/a)">x</p>',
    "font-face": "<style>@font-face{src:url(https://example.com/f.woff2)}</style>",
    "style-import": "<style>@import url(x.css);</style>",
}


@pytest.mark.parametrize("name", list(REMOTE_FORMS))
def test_each_remote_request_form_fails_the_gated_page(name: str) -> None:
    with pytest.raises(kit_module.KitRemoteRequestError) as caught:
        kit_module.check_gated_requests(REMOTE_FORMS[name], {})
    assert "example.com" not in str(caught.value)


def test_local_request_forms_are_fine() -> None:
    page = (
        '<base href="/"><meta http-equiv="refresh" content="5; url=/kit/">'
        '<img srcset="/a.png 1x, /b.png 2x" alt=""><form action="/kit/retry/"></form>'
        '<link rel="preload" href="/static/f.woff2" as="font">'
        '<svg><use href="#x"></use></svg><video poster="/p.png"></video>'
    )
    kit_module.check_gated_requests(page, {})


def test_the_build_runs_the_request_check_on_the_gated_page(tmp_path: Path) -> None:
    overrides = tmp_path / "overrides"
    (overrides / "templates").mkdir(parents=True)
    (overrides / "templates" / "kit_gated.html").write_text(
        '{% extends "base.html" %}{% block content %}'
        '<img srcset="/a.png 1x, https://example.com/b.png 2x" alt="">'
        "{% endblock %}",
        encoding="utf-8",
    )
    with pytest.raises(kit_module.KitRemoteRequestError):
        build_site(
            make_config(),
            tmp_path / "public",
            today=TODAY,
            gated_out=tmp_path / "gated",
            overrides=overrides,
        )


def test_the_build_runs_the_leak_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    real = kit_module.check_no_gated_text

    def spy(files: Any, kit: Any) -> None:
        calls.append(len(files))
        real(files, kit)

    monkeypatch.setattr(build_module, "check_no_gated_text", spy)
    build_site(
        make_config(), tmp_path / "public", today=TODAY, gated_out=tmp_path / "gated"
    )
    assert calls and calls[0] > 3


def test_zero_width_and_format_characters_do_not_hide_a_value() -> None:
    kit = make_config().kit
    for filler in ("​", "­", "⁠", "‍", "‮", "﻿"):
        hidden = filler.join(PARTNER)
        with pytest.raises(KitLeakError):
            kit_module.check_no_gated_text({Path("index.html"): hidden.encode()}, kit)


def test_the_floor_is_enforced_not_just_named() -> None:
    def kit_with(name: str) -> Any:
        return make_config(kit_doc(partners=[{"name": name}])).kit

    page = {Path("index.html"): b"<p>abcde and abcdef</p>"}
    kit_module.check_no_gated_text(page, kit_with("abcde"))  # 5 characters: skipped
    with pytest.raises(KitLeakError):
        kit_module.check_no_gated_text(page, kit_with("abcdef"))  # 6: searched


@pytest.mark.parametrize("name", ["page.xhtml", "noext", "payload.html.bin", "x.dat"])
def test_unknown_types_that_fail_strict_utf8_are_still_canonicalized(
    name: str,
) -> None:
    body = b"x " + quote(PARTNER, safe="").encode() + b" \xff"
    expected = (KitLeakError, kit_module.KitSuspiciousFileError)
    with pytest.raises(expected):
        kit_module.check_no_gated_text({Path("static") / name: body}, make_config().kit)
    if name != "page.xhtml":
        with pytest.raises(KitLeakError):
            kit_module.check_no_gated_text(
                {Path("static") / name: body}, make_config().kit
            )


def test_xhtml_is_a_known_text_type() -> None:
    with pytest.raises(kit_module.KitSuspiciousFileError):
        kit_module.check_no_gated_text(
            {Path("static/p.xhtml"): b"hello \xff"}, make_config().kit
        )


def test_a_nul_in_the_first_chunk_is_the_only_binary_signal_for_unknown_types() -> None:
    kit = make_config().kit
    text = quote(PARTNER, safe="").encode()
    kit_module.check_no_gated_text(
        {Path("static/blob"): b"\x00" + b"z" * 100 + text}, kit
    )  # binary: raw search only, the encoded form is not searched
    with pytest.raises(KitLeakError):
        kit_module.check_no_gated_text(
            {Path("static/blob"): b"z" * 9000 + text + b"\x00"}, kit
        )  # a NUL after the first chunk does not downgrade it


def test_raw_scanning_of_binary_files_has_a_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kit_module, "GUARD_MAX_BINARY_BYTES", 1000)
    files = {Path("a.png"): b"\x00" * 800, Path("b.png"): b"\x00" * 800}
    with pytest.raises(kit_module.KitGuardLimitError, match=r"GUARD_MAX_BINARY_BYTES"):
        kit_module.check_no_gated_text(files, make_config().kit)
