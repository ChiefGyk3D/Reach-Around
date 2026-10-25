# SPDX-License-Identifier: Apache-2.0
"""Content-hashed asset names: the naming scheme, CSS rewriting and the mapping."""

import hashlib
from pathlib import Path

import pytest

from reach_around.site.fingerprint import (
    HASH_LEN,
    AssetMap,
    UnknownAssetError,
    fingerprint,
    hashed_name,
    is_stable_name,
    rewrite_css,
)


def sha10(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:HASH_LEN]


def test_hashed_name_puts_ten_hex_of_sha256_before_the_extension() -> None:
    data = b"body { color: red; }\n"
    assert hashed_name("static/site.css", data) == f"static/site.{sha10(data)}.css"
    assert HASH_LEN == 10


def test_hashed_name_changes_only_with_content() -> None:
    a = hashed_name("static/a.js", b"one")
    assert hashed_name("static/a.js", b"one") == a
    assert hashed_name("static/a.js", b"two") != a
    # the directory does not enter the hash: same bytes, same hash
    assert sha10(b"one") in hashed_name("media/b.js", b"one")


def test_stable_names_are_only_the_root_level_files_browsers_ask_for() -> None:
    assert is_stable_name("favicon.ico")
    assert is_stable_name("apple-touch-icon.png")
    assert not is_stable_name("media/favicon.ico")
    assert not is_stable_name("static/site.css")


def _files() -> dict[Path, bytes]:
    return {
        Path("static/icons/x.svg"): b"<svg>1</svg>",
        Path("static/fonts/f.woff2"): b"font",
        Path("static/click.js"): b"js",
        Path("static/site.css"): (
            b'@font-face { src: url("fonts/f.woff2") format("woff2"); }\n'
            b'.i { mask-image: url("icons/x.svg"); }\n'
        ),
        Path("static/theme.css"): b".t { background: url('../media/bg.png'); }\n",
        Path("media/bg.png"): b"png",
        Path("favicon.ico"): b"ico",
    }


def test_every_file_is_renamed_and_css_points_at_the_new_names() -> None:
    done = fingerprint(_files())
    names = {p.as_posix() for p in done.files}
    assert "favicon.ico" in names
    for logical in (
        "static/icons/x.svg",
        "static/fonts/f.woff2",
        "static/click.js",
        "static/site.css",
        "static/theme.css",
        "media/bg.png",
    ):
        assert logical not in names
        assert done.assets(logical) in names
    site = done.files[Path(done.assets("static/site.css"))].decode()
    assert Path(done.assets("static/icons/x.svg")).name in site
    assert Path(done.assets("static/fonts/f.woff2")).name in site
    theme = done.files[Path(done.assets("static/theme.css"))].decode()
    assert "../" + done.assets("media/bg.png") in theme


def test_a_css_name_hashes_its_final_bytes() -> None:
    done = fingerprint(_files())
    for logical in ("static/site.css", "static/theme.css"):
        hashed = done.assets(logical)
        assert sha10(done.files[Path(hashed)]) in hashed


def test_a_changed_leaf_changes_the_css_that_uses_it_and_nothing_else() -> None:
    before = fingerprint(_files())
    changed = _files()
    changed[Path("static/icons/x.svg")] = b"<svg>2</svg>"
    after = fingerprint(changed)
    assert after.assets("static/icons/x.svg") != before.assets("static/icons/x.svg")
    assert after.assets("static/site.css") != before.assets("static/site.css")
    for same in (
        "static/theme.css",
        "static/click.js",
        "static/fonts/f.woff2",
        "media/bg.png",
    ):
        assert after.assets(same) == before.assets(same)


def test_the_hashed_set_excludes_stable_files() -> None:
    done = fingerprint(_files())
    assert Path("favicon.ico") not in done.hashed
    assert Path(done.assets("static/site.css")) in done.hashed
    assert len(done.hashed) == len(_files()) - 1


def test_unknown_asset_is_a_clear_error() -> None:
    done = fingerprint(_files())
    with pytest.raises(UnknownAssetError, match=r"static/nope\.css"):
        done.assets("static/nope.css")
    with pytest.raises(UnknownAssetError):
        AssetMap({})("static/site.css")


def test_asset_names_are_normalized_but_never_escape() -> None:
    done = fingerprint(_files())
    assert done.assets("./static/click.js") == done.assets("static/click.js")
    with pytest.raises(UnknownAssetError):
        done.assets("../static/click.js")


def test_rewrite_css_handles_quote_styles_and_relative_segments() -> None:
    mapping = {"media/a.png": "media/a.1234567890.png"}
    css = (
        'a{background:url("../media/a.png")}'
        "b{background:url('../media/a.png')}"
        "c{background:url( ../media/a.png )}"
        'd{background:url("../media/a.png?v=2#frag")}'
    )
    out = rewrite_css(css, "static/x.css", mapping)
    assert out.count("../media/a.1234567890.png") == 4
    assert "?v=2#frag" in out


def test_rewrite_css_leaves_remote_data_and_unknown_urls_alone() -> None:
    css = (
        'a{background:url("https://example.com/a.png")}'
        'b{background:url("data:image/png;base64,AAAA")}'
        'c{background:url("//example.com/a.png")}'
        'd{background:url("#frag")}'
        'e{background:url("unknown.png")}'
    )
    assert rewrite_css(css, "static/x.css", {"static/a.png": "static/a.1.png"}) == css


def test_rewrite_css_resolves_against_the_css_directory() -> None:
    mapping = {"static/icons/x.svg": "static/icons/x.1234567890.svg"}
    out = rewrite_css('a{b:url("icons/x.svg")}', "static/site.css", mapping)
    assert 'url("icons/x.1234567890.svg")' in out
    # the same text from another directory points somewhere else: untouched
    same = 'a{b:url("icons/x.svg")}'
    assert rewrite_css(same, "media/site.css", mapping) == same
