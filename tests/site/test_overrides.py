# SPDX-License-Identifier: Apache-2.0
import http.client
import logging
import re
import shutil
import threading
import time
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from assetpaths import asset, asset_file, has_asset, read
from reach_around.cli import main
from reach_around.config import SiteConfig, load_site_config
from reach_around.preview import Preview, PreviewError
from reach_around.site.build import build_site
from reach_around.site.overrides import (
    OverridesError,
    build_csp,
    make_environment,
    render_headers,
)

TODAY = date(2027, 3, 10)
ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "instance" / "site.yaml"


def make_config() -> SiteConfig:
    return SiteConfig.model_validate(
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


def build(out: Path, overrides: Path | None = None) -> None:
    build_site(make_config(), out, today=TODAY, overrides=overrides)


@pytest.fixture
def overrides(tmp_path: Path) -> Path:
    path = tmp_path / "overrides"
    (path / "templates").mkdir(parents=True)
    return path


def test_override_template_wins_and_missing_ones_fall_back(
    overrides: Path, tmp_path: Path
) -> None:
    (overrides / "templates" / "_footer.html").write_text("<footer>MINE</footer>\n")
    out = tmp_path / "out"
    build(out, overrides)
    index = (out / "index.html").read_text()
    assert "<footer>MINE</footer>" in index
    assert "Example Creator" in index  # header still comes from the package


@pytest.mark.parametrize("name", ["../x.html", "a/../../x.html", "..\\x.html"])
def test_override_name_cannot_escape_the_directory(
    overrides: Path, tmp_path: Path, name: str
) -> None:
    (overrides / "x.html").write_text("SECRET")  # one level above templates/
    env = make_environment(overrides)
    with pytest.raises(OverridesError):
        env.get_template(name)


def test_override_absolute_name_is_refused(overrides: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secret.html"
    secret.write_text("SECRET")
    env = make_environment(overrides)
    with pytest.raises(OverridesError):
        env.get_template(str(secret))


def test_override_symlink_pointing_outside_is_refused(
    overrides: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.html"
    outside.write_text("SECRET")
    (overrides / "templates" / "leak.html").symlink_to(outside)
    env = make_environment(overrides)
    with pytest.raises(OverridesError):
        env.get_template("leak.html")


def test_override_symlinked_directory_pointing_outside_is_refused(
    overrides: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "y.html").write_text("SECRET")
    (overrides / "templates" / "dir").symlink_to(outside, target_is_directory=True)
    env = make_environment(overrides)
    with pytest.raises(OverridesError):
        env.get_template("dir/y.html")


def test_missing_override_directory_is_a_clear_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope"
    with pytest.raises(OverridesError, match="does not exist"):
        make_environment(missing)
    code = main(
        [
            "build",
            "--config",
            str(EXAMPLE),
            "--out",
            str(tmp_path / "out"),
            "--overrides",
            str(missing),
        ]
    )
    err = capsys.readouterr().err
    assert code == 1
    assert "does not exist" in err and "Traceback" not in err
    assert not (tmp_path / "out").exists()


def test_overrides_path_that_is_a_file_is_a_clear_error(tmp_path: Path) -> None:
    file = tmp_path / "f"
    file.write_text("x")
    with pytest.raises(OverridesError, match="not a directory"):
        make_environment(file)


def test_build_command_accepts_overrides(tmp_path: Path, overrides: Path) -> None:
    (overrides / "custom.css").write_text("a { color: red }")
    out = tmp_path / "out"
    args = ["build", "--config", str(EXAMPLE), "--out", str(out)]
    assert main([*args, "--overrides", str(overrides)]) == 0
    assert asset_file(out, "static/custom.css").is_file()


def test_custom_css_is_appended_after_the_theme(
    overrides: Path, tmp_path: Path
) -> None:
    (overrides / "custom.css").write_text(".x { color: red; }\n")
    out = tmp_path / "out"
    build(out, overrides)
    assert read(out, "static/custom.css") == ".x { color: red; }\n"
    theme, custom = asset(out, "static/theme.css"), asset(out, "static/custom.css")
    for page in (out / "index.html", out / "privacy" / "index.html"):
        html = page.read_text()
        assert html.index(theme) < html.index(custom)


def test_no_custom_css_means_no_link(tmp_path: Path) -> None:
    out = tmp_path / "out"
    build(out)
    assert "custom.css" not in (out / "index.html").read_text()
    assert not has_asset(out, "static/custom.css")
    assert not list((out / "static").glob("custom*"))


@pytest.mark.parametrize(
    "css",
    [
        "@import 'other.css';",
        "@IMPORT url(x.css);",
        "a { background: url(http://example.com/x.png) }",
        "a { background: url('//example.com/x.png') }",
        "a { background: url(data:image/png;base64,AAAA) }",
        "a { background: url(javascript:alert(1)) }",
        'a { background: url("ftp://example.com/x") }',
        "a { width: expression(alert(1)) }",
        "a { behavior: url(x.htc) }",
        "a { -moz-binding: url(x.xml#y) }",
        "a { background: ur\\6c(http://example.com/x) }",
        "a { background: \\75rl(http://example.com/x) }",
        "a { background: image-set('http://example.com/x.png' 1x) }",
        "a::after { content: 'javascript:x' }",
        "a { background: url(https://example.com/x.png }",
        # a remote url() leaks visitor IPs; refused since the security fix round
        'a { background: url("https://example.com/bg.png") }',
        # any backslash is refused, because CSS escapes can hide keywords
        'a::before { content: "\\201C" }',
    ],
)
def test_custom_css_with_import_or_script_url_is_refused(
    overrides: Path, tmp_path: Path, css: str
) -> None:
    (overrides / "custom.css").write_text(css)
    out = tmp_path / "out"
    with pytest.raises(OverridesError):
        build(out, overrides)
    assert not out.exists()


@pytest.mark.parametrize(
    "css",
    [
        "a { background: url(img/bg.png) }",
        "a { background: url('../img/bg.png') }",
        'a::before { content: "quote" }',
        "/* @import not really */ a { color: red }",
    ],
)
def test_custom_css_with_safe_urls_is_accepted(
    overrides: Path, tmp_path: Path, css: str
) -> None:
    (overrides / "custom.css").write_text(css)
    build(tmp_path / "out", overrides)


def test_custom_css_over_the_size_cap_is_refused(
    overrides: Path, tmp_path: Path
) -> None:
    (overrides / "custom.css").write_text("a{}" + " " * (256 * 1024))
    with pytest.raises(OverridesError, match="256 KiB"):
        build(tmp_path / "out", overrides)


def test_custom_css_that_is_not_utf8_is_refused(
    overrides: Path, tmp_path: Path
) -> None:
    (overrides / "custom.css").write_bytes(b"a{ color: \xff }")
    with pytest.raises(OverridesError, match="UTF-8"):
        build(tmp_path / "out", overrides)


def test_custom_css_symlink_outside_is_refused(overrides: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.css"
    outside.write_text("a{}")
    (overrides / "custom.css").symlink_to(outside)
    with pytest.raises(OverridesError):
        build(tmp_path / "out", overrides)


def test_no_custom_javascript_is_loaded(
    overrides: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (overrides / "custom.js").write_text("alert(1)")
    (overrides / "templates" / "deep").mkdir()
    (overrides / "templates" / "deep" / "more.js").write_text("alert(2)")
    out = tmp_path / "out"
    with caplog.at_level(logging.WARNING):
        build(out, overrides)
    assert "custom.js" in caplog.text and "more.js" in caplog.text
    assert "ignored" in caplog.text
    # the packaged one, under its hashed name
    assert [p.relative_to(out).as_posix() for p in out.rglob("*.js")] == [
        asset(out, "static/click.js")
    ]
    html = "".join(p.read_text() for p in out.rglob("*.html"))
    assert "custom.js" not in html and "more.js" not in html


def test_headers_file_has_a_strict_csp(tmp_path: Path) -> None:
    out = tmp_path / "out"
    build(out)
    text = (out / "_headers").read_text()
    for needed in (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "object-src 'none'",
        "X-Content-Type-Options: nosniff",
        "Referrer-Policy: strict-origin-when-cross-origin",
        "Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()",
        "Cross-Origin-Opener-Policy: same-origin",
        "Strict-Transport-Security: max-age=31536000; includeSubDomains",
    ):
        assert needed in text
    assert text.startswith("/*\n")


def test_headers_file_has_no_unsafe_directives(tmp_path: Path) -> None:
    out = tmp_path / "out"
    build(out)
    text = (out / "_headers").read_text()
    assert "unsafe-inline" not in text and "unsafe-eval" not in text
    assert "data:" not in text and " * " not in text


def test_csp_is_exactly_the_specified_policy() -> None:
    assert build_csp() == (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' https:; font-src 'self'; connect-src 'self'; "
        "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'self'"
    )


def test_build_csp_accepts_a_narrow_https_extra() -> None:
    csp = build_csp(
        {
            "script-src": ["https://challenges.example.com"],
            "frame-src": ["https://challenges.example.com", "'self'"],
        }
    )
    assert "script-src 'self' https://challenges.example.com;" in csp
    assert "frame-src https://challenges.example.com 'self'" in csp
    assert "unsafe" not in csp


@pytest.mark.parametrize(
    ("directive", "source"),
    [
        ("script-src", "*"),
        ("script-src", "data:"),
        ("script-src", "'unsafe-inline'"),
        ("script-src", "'unsafe-eval'"),
        ("script-src", "http://example.com"),
        ("script-src", "https://*.example.com"),
        ("script-src", "https://example.com/path"),
        ("script-src", "https:"),
        ("script-src", "https://example.com; script-src *"),
        ("script-src", "'nonce-abc'"),
        ("script-src", "'strict-dynamic'"),
        ("frame-ancestors", "https://example.com"),
        ("object-src", "https://example.com"),
        ("base-uri", "https://example.com"),
        ("default-src", "https://example.com"),
        ("script-src; x", "https://example.com"),
    ],
)
def test_build_csp_refuses_wildcards_and_unsafe_sources(
    directive: str, source: str
) -> None:
    with pytest.raises(OverridesError):
        build_csp({directive: [source]})


def test_render_headers_per_path_extra_is_narrow() -> None:
    extra = {"/gate/*": {"script-src": ["https://challenges.example.com"]}}
    text = render_headers(extra)
    base, _, gate = text.partition("\n/gate/*\n")
    assert "challenges.example.com" not in base
    assert "! Content-Security-Policy" in gate
    assert "script-src 'self' https://challenges.example.com" in gate


def test_render_headers_refuses_a_bad_path() -> None:
    good = {"script-src": ["https://a.example.com"]}
    with pytest.raises(OverridesError):
        render_headers({"gate": good})
    with pytest.raises(OverridesError):
        render_headers({"/a b\n/*": good})


INLINE_STYLE = re.compile(r"<[^>]*\sstyle\s*=", re.IGNORECASE)
INLINE_HANDLER = re.compile(r"<[^>]*\son[a-z]+\s*=", re.IGNORECASE)
INLINE_SCRIPT = re.compile(r"<script\b(?![^>]*\bsrc\s*=)", re.IGNORECASE)
STYLE_ELEMENT = re.compile(r"<style\b", re.IGNORECASE)


def csp_violations(html: str) -> list[str]:
    found: list[str] = []
    for label, pattern in (
        ("style attribute", INLINE_STYLE),
        ("event handler", INLINE_HANDLER),
        ("inline script", INLINE_SCRIPT),
        ("style element", STYLE_ELEMENT),
    ):
        if pattern.search(html):
            found.append(label)
    return found


def test_csp_scanner_bites() -> None:
    assert csp_violations('<p style="color:red">x</p>') == ["style attribute"]
    assert csp_violations('<a onclick="x()">x</a>') == ["event handler"]
    assert csp_violations("<script>alert(1)</script>") == ["inline script"]
    assert csp_violations("<style>a{}</style>") == ["style element"]
    assert csp_violations('<script src="a.js" defer></script>') == []


def test_every_page_still_passes_with_the_csp(tmp_path: Path) -> None:
    out = tmp_path / "out"
    config = load_site_config(EXAMPLE)
    build_site(config, out, today=TODAY, protect=[EXAMPLE], assets_dir=EXAMPLE.parent)
    built = sorted(out.rglob("*.html"))
    assert len(built) >= 2
    for page in built:
        assert csp_violations(page.read_text(encoding="utf-8")) == [], page


@pytest.fixture
def running(tmp_path: Path, overrides: Path) -> Iterator[Preview]:
    config = tmp_path / "site.yaml"
    config.write_text(EXAMPLE.read_text())
    shutil.copytree(EXAMPLE.parent / "media", tmp_path / "media")
    (overrides / "templates" / "_footer.html").write_text("<footer>PREVIEWED</footer>")
    preview = Preview(config, "127.0.0.1", 0, overrides=overrides)
    thread = threading.Thread(target=preview.serve_forever, daemon=True)
    thread.start()
    try:
        yield preview
    finally:
        preview.close()
        thread.join(timeout=5)


def test_preview_serve_applies_overrides(running: Preview, overrides: Path) -> None:
    def body() -> str:
        conn = http.client.HTTPConnection("127.0.0.1", running.port, timeout=5)
        try:
            conn.request("GET", "/")
            return conn.getresponse().read().decode()
        finally:
            conn.close()

    assert "PREVIEWED" in body()
    # the watcher also notices a change inside the overrides directory
    (overrides / "templates" / "_footer.html").write_text("<footer>CHANGED</footer>")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and "CHANGED" not in body():
        time.sleep(0.1)
    assert "CHANGED" in body()


def test_preview_refuses_a_missing_overrides_directory(tmp_path: Path) -> None:
    config = tmp_path / "site.yaml"
    config.write_text(EXAMPLE.read_text())
    with pytest.raises(PreviewError, match="does not exist"):
        Preview(config, "127.0.0.1", 0, overrides=tmp_path / "nope")
