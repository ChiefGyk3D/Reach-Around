# SPDX-License-Identifier: Apache-2.0
"""Fingerprinted assets, end to end: names, references and headers."""

from __future__ import annotations

import hashlib
import posixpath
import re
from collections.abc import Callable
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import pytest

from assetpaths import HASH, asset, asset_file, has_asset, png, read
from reach_around.config import SiteConfig, load_site_config
from reach_around.site.build import build_site
from reach_around.site.overrides import render_headers

TODAY = date(2027, 3, 10)
ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
SITES = {
    "instance": EXAMPLES / "instance" / "site.yaml",
    "crypto": EXAMPLES / "crypto" / "site.yaml",
    "email": EXAMPLES / "email" / "site.yaml",
    "platforms": EXAMPLES / "platforms" / "site.yaml",
}
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "public, max-age=0, must-revalidate"
HASHED_NAME = re.compile(rf".+\.{HASH}\.[A-Za-z0-9]+")
ONE_LINK = (
    "tabs:\n- id: start\n  title: Start\n  blocks:\n"
    "  - {id: one, title: One, url: 'https://example.com/1'}\n"
)


def build(name: str, out: Path) -> SiteConfig:
    path = SITES[name]
    config = load_site_config(path)
    build_site(config, out, today=TODAY, assets_dir=path.parent)
    return config


def build_instance(
    instance: Path, out: Path, overrides: Path | None = None
) -> SiteConfig:
    config = load_site_config(instance / "site.yaml")
    build_site(config, out, today=TODAY, assets_dir=instance, overrides=overrides)
    return config


@pytest.fixture(scope="module", params=sorted(SITES))
def built(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    out = tmp_path_factory.mktemp(request.param) / "out"
    build(request.param, out)
    return out


class _Refs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in ("link", "a") and values.get("href"):
            self.refs.append(str(values["href"]))
        if tag in ("script", "img") and values.get("src"):
            self.refs.append(str(values["src"]))


def html_refs(path: Path) -> list[str]:
    parser = _Refs()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser.refs


def resolve(page: str, ref: str) -> str | None:
    """Site-root-relative target of a local ``ref`` on ``page``, else None."""
    if ":" in ref or ref.startswith(("//", "#")):
        return None
    ref = re.split(r"[?#]", ref, maxsplit=1)[0]
    if ref.startswith("/"):
        return posixpath.normpath(ref.lstrip("/") or ".")
    return posixpath.normpath(posixpath.join(posixpath.dirname(page), ref))


def css_urls(css: str) -> list[str]:
    pattern = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)"'\s]*))\s*\)""")
    return [next(g for g in m.groups() if g is not None) for m in pattern.finditer(css)]


def published(out: Path) -> list[Path]:
    return sorted(p for p in out.rglob("*") if p.is_file())


# ----------------------------------------------------------------- names


def test_every_file_under_static_has_a_hashed_name(built: Path) -> None:
    files = [p for p in (built / "static").rglob("*") if p.is_file()]
    assert files
    for path in files:
        assert HASHED_NAME.fullmatch(path.name), path


def test_a_hashed_name_is_the_hash_of_the_published_bytes(built: Path) -> None:
    checked = 0
    for path in published(built):
        match = re.fullmatch(rf"(.+)\.({HASH})(\.[A-Za-z0-9]+)", path.name)
        if match and path.relative_to(built).parts[0] == "static":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:10]
            assert match.group(2) == digest, path
            checked += 1
    assert checked >= 5


def test_the_build_is_deterministic(tmp_path: Path) -> None:
    one, two = tmp_path / "one", tmp_path / "two"
    build("instance", one)
    build("instance", two)
    names = [p.relative_to(one).as_posix() for p in published(one)]
    assert names == [p.relative_to(two).as_posix() for p in published(two)]
    for name in names:
        assert (one / name).read_bytes() == (two / name).read_bytes(), name


def test_pages_headers_and_robots_keep_their_names(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    (instance / "site.yaml").write_text(
        "version: 1\ncreator: {name: Example Creator}\nrobots: {}\n" + ONE_LINK,
        encoding="utf-8",
    )
    out = tmp_path / "out"
    build_instance(instance, out)
    for name in ("index.html", "404.html", "_headers", "robots.txt"):
        assert (out / name).is_file(), name
    assert (out / "privacy" / "index.html").is_file()


# ------------------------------------------------------------ references


def test_every_local_reference_in_every_page_exists(built: Path) -> None:
    pages = sorted(built.rglob("*.html"))
    assert pages
    seen = 0
    for page in pages:
        rel = page.relative_to(built).as_posix()
        for ref in html_refs(page):
            target = resolve(rel, ref)
            if target is None:
                continue
            seen += 1
            assert (built / target).exists(), f"{rel} -> {ref}"
    assert seen > 10


def test_no_page_links_an_unhashed_static_file(built: Path) -> None:
    seen = 0
    for page in sorted(built.rglob("*.html")):
        for ref in html_refs(page):
            if "static/" in ref:
                seen += 1
                assert HASHED_NAME.fullmatch(posixpath.basename(ref)), (page, ref)
    assert seen > 5


def test_every_url_in_every_stylesheet_exists_and_is_hashed(built: Path) -> None:
    sheets = sorted((built / "static").glob("*.css"))
    assert sheets
    seen = 0
    for sheet in sheets:
        rel = sheet.relative_to(built).as_posix()
        for url in css_urls(sheet.read_text(encoding="utf-8")):
            target = resolve(rel, url)
            if target is None:
                continue
            seen += 1
            assert (built / target).is_file(), f"{rel} -> {url}"
            assert HASHED_NAME.fullmatch(posixpath.basename(target)), (rel, url)
    assert seen > 5


def test_instance_media_is_hashed_and_referenced_by_the_hashed_name(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    config = build("instance", out)
    avatar = config.creator.avatar
    assert avatar is not None
    assert not (out / avatar).exists()
    page = (out / "index.html").read_text(encoding="utf-8")
    assert f'src="{asset(out, avatar)}"' in page
    assert config.creator.favicon is not None
    assert f'href="{asset(out, config.creator.favicon)}"' in page


def test_the_404_page_uses_root_relative_hashed_urls(built: Path) -> None:
    refs = [r for r in html_refs(built / "404.html") if "static/" in r]
    assert refs
    for ref in refs:
        assert ref.startswith("/static/")
        assert (built / ref.lstrip("/")).is_file()


# -------------------------------------------------- changes cause misses


def _static_edit(
    monkeypatch: pytest.MonkeyPatch, edit: Callable[[dict[Path, bytes]], None]
) -> None:
    import reach_around.site.build as build_module

    original = build_module._static_files

    def patched() -> dict[Path, bytes]:
        files = original()
        edit(files)
        return files

    monkeypatch.setattr(build_module, "_static_files", patched)


def test_a_changed_icon_renames_the_css_that_uses_it_and_the_html_that_links_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = tmp_path / "before"
    build("platforms", before)

    def edit(files: dict[Path, bytes]) -> None:
        key = Path("static/icons/twitch.svg")
        files[key] = files[key] + b"<!-- changed -->"

    _static_edit(monkeypatch, edit)
    after = tmp_path / "after"
    build("platforms", after)

    assert asset(before, "static/icons/twitch.svg") != asset(
        after, "static/icons/twitch.svg"
    )
    assert asset(before, "static/site.css") != asset(after, "static/site.css")
    for same in (
        "static/theme.css",
        "static/platforms.css",
        "static/icons/github.svg",
        "static/fonts/jost-latin-wght-normal.woff2",
    ):
        assert asset(before, same) == asset(after, same), same
    old_css, new_css = asset(before, "static/site.css"), asset(after, "static/site.css")
    page_before = (before / "index.html").read_text(encoding="utf-8")
    page_after = (after / "index.html").read_text(encoding="utf-8")
    assert old_css in page_before
    assert new_css in page_after
    assert old_css not in page_after
    # the page differs only in the stylesheet it links
    assert page_before.replace(old_css, new_css) == page_after


def test_an_unrelated_change_leaves_every_other_static_name_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = tmp_path / "before"
    build("crypto", before)

    def edit(files: dict[Path, bytes]) -> None:
        files[Path("static/copy.js")] += b"\n// changed\n"

    _static_edit(monkeypatch, edit)
    after = tmp_path / "after"
    build("crypto", after)
    assert asset(before, "static/copy.js") != asset(after, "static/copy.js")
    for logical in ("static/site.css", "static/theme.css", "static/crypto.css"):
        assert asset(before, logical) == asset(after, logical)


def test_a_changed_instance_image_renames_it_and_the_theme_css(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    (instance / "bg.png").write_bytes(png("one"))
    (instance / "site.yaml").write_text(
        "version: 1\ncreator: {name: Example Creator}\n"
        "theme: {background: bg.png}\n" + ONE_LINK,
        encoding="utf-8",
    )
    one, two = tmp_path / "one", tmp_path / "two"
    build_instance(instance, one)
    (instance / "bg.png").write_bytes(png("two"))
    build_instance(instance, two)
    assert asset(one, "bg.png") != asset(two, "bg.png")
    assert asset(one, "static/theme.css") != asset(two, "static/theme.css")
    assert asset(one, "static/site.css") == asset(two, "static/site.css")
    assert "../" + asset(two, "bg.png") in read(two, "static/theme.css")
    assert asset(two, "static/theme.css") in (two / "index.html").read_text()


def test_custom_css_is_hashed_linked_and_its_urls_follow(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    (instance / "overrides").mkdir(parents=True)
    (instance / "pic.png").write_bytes(png("pic"))
    (instance / "overrides" / "custom.css").write_text(
        'body { background-image: url("../pic.png"); }\n', encoding="utf-8"
    )
    (instance / "site.yaml").write_text(
        "version: 1\ncreator: {name: Example Creator}\n"
        + ONE_LINK
        + "  - {id: pic, src: pic.png, alt: A picture}\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    build_instance(instance, out, instance / "overrides")
    custom = asset(out, "static/custom.css")
    page = (out / "index.html").read_text(encoding="utf-8")
    assert f'href="{custom}"' in page
    assert page.index(asset(out, "static/theme.css")) < page.index(custom)
    assert "../" + asset(out, "pic.png") in read(out, "static/custom.css")
    assert not (out / "static" / "custom.css").exists()


def test_an_override_template_with_an_unknown_asset_is_a_clear_error(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    (instance / "overrides" / "templates").mkdir(parents=True)
    (instance / "overrides" / "templates" / "404.html").write_text(
        '{% extends "base.html" %}{% block content %}'
        '<img src="{{ root }}{{ asset("static/missing.png") }}">'
        "{% endblock %}",
        encoding="utf-8",
    )
    (instance / "site.yaml").write_text(
        "version: 1\ncreator: {name: Example Creator}\n" + ONE_LINK,
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"static/missing\.png"):
        build_instance(instance, tmp_path / "out", instance / "overrides")


def test_conditional_files_are_published_only_with_their_feature(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    build("instance", out)
    assert not has_asset(out, "static/crypto.css")
    assert not has_asset(out, "static/copy.js")
    crypto = tmp_path / "crypto"
    build("crypto", crypto)
    assert has_asset(crypto, "static/crypto.css")
    assert asset_file(crypto, "static/copy.js").is_file()


# --------------------------------------------------------------- headers


Line = tuple[bool, str, str]


def _blocks(text: str) -> list[tuple[str, list[Line]]]:
    blocks: list[tuple[str, list[Line]]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if line[0] not in " \t":
            blocks.append((line.strip(), []))
            continue
        body = line.strip()
        if body.startswith("! "):
            blocks[-1][1].append((True, body[2:].strip(), ""))
        else:
            name, _, value = body.partition(":")
            blocks[-1][1].append((False, name.strip(), value.strip()))
    return blocks


def _pattern(pattern: str) -> re.Pattern[str]:
    return re.compile(re.escape(pattern).replace(r"\*", ".*"))


def cache_control(headers: str, url_path: str) -> list[str]:
    """Cache-Control values Pages would send: all matches, detaches applied."""
    values: list[str] = []
    for pattern, lines in _blocks(headers):
        if not _pattern(pattern).fullmatch(url_path):
            continue
        for detach, name, value in lines:
            if name == "Cache-Control":
                if detach:
                    values.clear()
                else:
                    values.append(value)
    return values


def cache_patterns(headers: str, value: str) -> list[str]:
    return [
        pattern
        for pattern, lines in _blocks(headers)
        if any(v == value for _, n, v in lines if n == "Cache-Control")
    ]


def url_variants(rel: str) -> list[str]:
    if rel == "index.html":
        return ["/", "/index.html"]
    if rel.endswith("/index.html"):
        return ["/" + rel[: -len("index.html")], "/" + rel]
    return ["/" + rel]


def is_hashed(rel: str) -> bool:
    return bool(HASHED_NAME.fullmatch(posixpath.basename(rel)))


def test_headers_keep_the_existing_block_byte_for_byte(built: Path) -> None:
    text = (built / "_headers").read_text(encoding="utf-8")
    assert text.startswith(render_headers().rstrip("\n"))


def test_every_published_file_gets_exactly_its_class_cache_control(
    built: Path,
) -> None:
    headers = (built / "_headers").read_text(encoding="utf-8")
    checked = 0
    for path in published(built):
        rel = path.relative_to(built).as_posix()
        if rel == "_headers":
            continue
        expected = IMMUTABLE if is_hashed(rel) else REVALIDATE
        for url in url_variants(rel):
            assert cache_control(headers, url) == [expected], url
            checked += 1
    assert checked > 10


def test_no_path_matches_both_an_immutable_and_a_revalidate_rule(built: Path) -> None:
    headers = (built / "_headers").read_text(encoding="utf-8")
    immutable = cache_patterns(headers, IMMUTABLE)
    revalidate = cache_patterns(headers, REVALIDATE)
    assert immutable
    assert revalidate
    # nothing relies on a detach: no block removes a Cache-Control header
    assert not [
        pattern
        for pattern, lines in _blocks(headers)
        if any(d and n == "Cache-Control" for d, n, _ in lines)
    ]
    candidates = {
        url
        for path in published(built)
        for url in url_variants(path.relative_to(built).as_posix())
    }
    candidates |= {"/static/anything", "/static/x/y.css", "/nope", "/a/b/c", "/static/"}
    for url in sorted(candidates):
        hit_immutable = any(_pattern(p).fullmatch(url) for p in immutable)
        hit_revalidate = any(_pattern(p).fullmatch(url) for p in revalidate)
        assert not (hit_immutable and hit_revalidate), url
        assert len(cache_control(headers, url)) <= 1, url


def test_the_whole_static_folder_is_one_immutable_rule(built: Path) -> None:
    headers = (built / "_headers").read_text(encoding="utf-8")
    assert "/static/*" in cache_patterns(headers, IMMUTABLE)


def test_html_robots_and_404_revalidate(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    (instance / "site.yaml").write_text(
        "version: 1\ncreator: {name: Example Creator}\nrobots: {}\n" + ONE_LINK,
        encoding="utf-8",
    )
    out = tmp_path / "out"
    build_instance(instance, out)
    headers = (out / "_headers").read_text(encoding="utf-8")
    for url in (
        "/",
        "/index.html",
        "/404.html",
        "/robots.txt",
        "/privacy/",
        "/privacy/index.html",
    ):
        assert cache_control(headers, url) == [REVALIDATE], url


def test_the_rule_count_stays_under_the_pages_limit(built: Path) -> None:
    assert len(_blocks((built / "_headers").read_text(encoding="utf-8"))) <= 100


def test_a_media_folder_of_hashed_files_gets_one_immutable_rule(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    build("instance", out)
    headers = (out / "_headers").read_text(encoding="utf-8")
    media = [
        p for p in out.rglob("*") if p.is_file() and "media" in p.relative_to(out).parts
    ]
    assert media
    assert "/media/*" in cache_patterns(headers, IMMUTABLE)
    for path in media:
        url = "/" + path.relative_to(out).as_posix()
        assert cache_control(headers, url) == [IMMUTABLE]


def test_a_hashed_folder_shared_with_a_page_falls_back_to_exact_rules(
    tmp_path: Path,
) -> None:
    # a tab called "media" puts media/index.html next to the hashed image
    instance = tmp_path / "instance"
    (instance / "media").mkdir(parents=True)
    (instance / "media" / "a.png").write_bytes(png("a"))
    (instance / "site.yaml").write_text(
        "version: 1\ncreator: {name: Example Creator}\n"
        + ONE_LINK
        + "  - {id: pic, src: media/a.png, alt: Pic}\n"
        "- id: media\n  title: Media\n  blocks:\n"
        "  - {id: two, title: Two, url: 'https://example.com/2'}\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    build_instance(instance, out)
    headers = (out / "_headers").read_text(encoding="utf-8")
    assert "/media/*" not in cache_patterns(headers, IMMUTABLE)
    image = "/" + asset(out, "media/a.png")
    assert cache_control(headers, image) == [IMMUTABLE]
    assert cache_control(headers, "/media/") == [REVALIDATE]
    assert cache_control(headers, "/media/index.html") == [REVALIDATE]


def test_a_stable_favicon_keeps_its_name_and_revalidates(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    (instance / "apple-touch-icon.png").write_bytes(png("ico"))
    (instance / "site.yaml").write_text(
        "version: 1\ncreator: {name: Example Creator, favicon: apple-touch-icon.png}\n"
        + ONE_LINK,
        encoding="utf-8",
    )
    out = tmp_path / "out"
    build_instance(instance, out)
    assert (out / "apple-touch-icon.png").read_bytes() == png("ico")
    assert 'href="apple-touch-icon.png"' in (out / "index.html").read_text(
        encoding="utf-8"
    )
    headers = (out / "_headers").read_text(encoding="utf-8")
    assert cache_control(headers, "/apple-touch-icon.png") == [REVALIDATE]


def test_render_headers_without_cache_arguments_has_no_cache_rules() -> None:
    assert "Cache-Control" not in render_headers()
