# SPDX-License-Identifier: Apache-2.0
"""The email block: config rules, what the built page does and does not contain.

Every address is synthetic (example.test). The block's whole point is that the
built HTML never holds the plain address or a mail link; these tests prove it.
"""

from __future__ import annotations

import base64
import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from assetpaths import asset, asset_file, has_asset
from reach_around.config import SiteConfig, load_site_config
from reach_around.config.blocks import Email
from reach_around.config.loader import ConfigError
from reach_around.site.build import build_site

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "email" / "site.yaml"
STATIC = ROOT / "src" / "reach_around" / "site" / "static"
TODAY = date(2027, 3, 10)
ADDR = "name@example.test"
NOTE = "Business and press only. I read everything and answer within a week."
HOSTILE = "<script>alert(1)</script>"


def block(**over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"id": "contact", "address": ADDR}
    out.update(over)
    return out


def cfg(*blocks: dict[str, Any], extra_tabs: int = 0) -> SiteConfig:
    tabs: list[dict[str, Any]] = [
        {"id": "reach", "title": "Contact", "blocks": list(blocks)}
    ]
    for n in range(extra_tabs):
        tabs.append(
            {
                "id": f"plain{n}",
                "title": f"Plain {n}",
                "blocks": [{"id": f"t{n}", "markdown": "hello"}],
            }
        )
    return SiteConfig.model_validate(
        {"version": 1, "creator": {"name": "Example Creator"}, "tabs": tabs}
    )


def build(config: SiteConfig, tmp_path: Path) -> Path:
    out = tmp_path / "out"
    build_site(config, out, today=TODAY)
    return out


def page(config: SiteConfig, tmp_path: Path) -> str:
    return (build(config, tmp_path) / "index.html").read_text("utf-8")


# --- config ------------------------------------------------------------------


def test_minimal_block_defaults() -> None:
    email = cfg(block()).tabs[0].blocks[0]
    assert isinstance(email, Email)
    assert email.reveal == "click"
    assert email.note is None


@pytest.mark.parametrize(
    "good",
    [
        "a@example.test",
        "first.last+tag@mail.example.test",
        "A_b-c%d@Example.Test",
        "x" * 64 + "@example.test",
    ],
)
def test_good_addresses_are_accepted(good: str) -> None:
    cfg(block(address=good))


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "no-at-sign.example.test",
        "@example.test",
        "name@",
        "name@localhost",  # no dot
        "name@example.test.",  # trailing dot
        "name@.example.test",
        "name@exa..mple.test",
        "name@-bad.example.test",
        "name@example.t est",
        "a@b@example.test",
        "Name <name@example.test>",  # display name
        '"name"@example.test',
        "name@example.test, other@example.test",  # two addresses
        "name@example.test;other@example.test",
        ".name@example.test",
        "name.@example.test",
        "na..me@example.test",
        "name@example.test\n",
        "name@example.test\r\nBcc: x@example.test",
        "na\tme@example.test",
        "na\x00me@example.test",
        "na\x07me@example.test",
        "name@example.test​",  # zero width space
        "na‮me@example.test",  # bidi override
        "café@example.test",
        "name@exämple.test",
        "name@1.2.3.4",  # bare numbers are not a host name
        "name@example.test<script>",
        'name"@example.test',
        "x" * 65 + "@example.test",
        "a" * 64 + "@" + ("b" * 63 + ".") * 3 + "c" * 63 + ".test",
    ],
)
def test_bad_addresses_are_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="address"):
        cfg(block(address=bad))


def test_address_length_boundary_is_254() -> None:
    local = "a" * 64
    # 64 + "@" + (63 + 1 + 63 + 1 + 58 + 1 + 2) = 254
    domain = ".".join(["b" * 63, "c" * 63, "d" * 58, "te"])
    ok = f"{local}@{domain}"
    assert len(ok) == 254
    cfg(block(address=ok))
    over = f"{local}@{domain.replace('d' * 58, 'd' * 59)}"
    assert len(over) == 255
    with pytest.raises(ValueError, match="address"):
        cfg(block(address=over))


def test_address_must_be_a_string() -> None:
    with pytest.raises(ValueError, match="address"):
        cfg(block(address=12345))
    with pytest.raises(ValueError, match="address"):
        cfg(block(address=["name@example.test"]))


def test_address_is_required() -> None:
    with pytest.raises(ValueError, match="address"):
        cfg({"id": "contact", "address": None})


@pytest.mark.parametrize("mode", ["click", "always", "never_js"])
def test_reveal_modes(mode: str) -> None:
    cfg(block(reveal=mode))


@pytest.mark.parametrize("bad", ["hover", "", "CLICK", True, 1])
def test_unknown_reveal_is_refused(bad: Any) -> None:
    with pytest.raises(ValueError, match="reveal"):
        cfg(block(reveal=bad))


def test_text_fields_are_plain_and_bounded() -> None:
    cfg(block(title="t" * 200, note="n" * 300))
    with pytest.raises(ValueError, match="note"):
        cfg(block(note="n" * 301))
    with pytest.raises(ValueError, match="title"):
        cfg(block(title="t" * 201))
    with pytest.raises(ValueError, match="note"):
        cfg(block(note="line\x00"))
    with pytest.raises(ValueError, match="note"):
        cfg(block(note="bidi‮text"))
    with pytest.raises(ValueError, match="note"):
        cfg(block(note=""))


@pytest.mark.parametrize(
    "bad_note",
    [
        "Write to name@example.test please",
        "Write to somebody@example.test please",
        "mailto:x",
        "Mail NAME at example.test",
    ],
)
def test_note_may_not_carry_an_address_or_the_plain_domain(bad_note: str) -> None:
    with pytest.raises(ValueError, match="note"):
        cfg(block(note=bad_note))


def test_spell_words() -> None:
    cfg(block(spell={"at": "at", "dot": "dot"}))
    cfg(block(spell={"at": "(at)", "dot": "(dot)"}))
    cfg(block(spell={"at": "arobase"}))
    for bad in ("", "a" * 21, "@", "a.b", "bell\x07", "x@y"):
        with pytest.raises(ValueError, match="spell"):
            cfg(block(spell={"at": bad}))
        with pytest.raises(ValueError, match="spell"):
            cfg(block(spell={"dot": bad}))
    with pytest.raises(ValueError, match="spell"):
        cfg(block(spell={"comma": "x"}))


def test_unknown_keys_are_refused() -> None:
    with pytest.raises(ValueError, match="subject"):
        cfg(block(subject="Hello"))


def test_errors_name_the_key(tmp_path: Path) -> None:
    path = tmp_path / "site.yaml"
    path.write_text(
        "version: 1\ncreator: {name: X}\ntabs:\n- id: t\n  title: T\n  blocks:\n"
        "  - id: mail\n    address: not-an-address\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as caught:
        load_site_config(path)
    assert "tabs[0].blocks[0].address" in str(caught.value)


def test_block_ids_stay_unique() -> None:
    with pytest.raises(ValueError, match="contact"):
        cfg(block(), block())


# --- the built page ------------------------------------------------------------


def _card(html: str) -> str:
    match = re.search(r'<div class="email-card"[^>]*>', html)
    assert match, "no email card in the page"
    return match.group(0)


def _decode(attribute: str) -> str:
    return base64.b64decode(attribute)[::-1].decode("ascii")


@pytest.mark.parametrize("mode", ["click", "always", "never_js"])
def test_the_page_never_holds_the_address_or_a_mail_link(
    tmp_path: Path, mode: str
) -> None:
    html = page(cfg(block(reveal=mode, note=NOTE)), tmp_path)
    assert ADDR not in html
    assert "@" not in html
    assert "mailto" not in html.lower()
    assert "example.test" not in html  # the domain is only ever spelled out


def test_no_published_text_file_holds_the_address(tmp_path: Path) -> None:
    out = build(cfg(block()), tmp_path)
    checked = 0
    for path in out.rglob("*"):
        if path.is_file() and path.suffix in {".html", ".css", ".txt", ".json"}:
            text = path.read_text("utf-8", errors="ignore")
            assert ADDR not in text, path
            assert "example.test" not in text, path
            checked += 1
    assert checked > 3


def test_the_encoded_attribute_decodes_to_the_address(tmp_path: Path) -> None:
    html = page(cfg(block()), tmp_path)
    match = re.search(r'data-email="([^"]+)"', _card(html))
    assert match
    assert _decode(match.group(1)) == ADDR
    assert "@" not in match.group(1)


def test_never_js_ships_no_encoded_address(tmp_path: Path) -> None:
    html = page(cfg(block(reveal="never_js")), tmp_path)
    assert "data-email=" not in html
    assert "data-email-reveal" not in html


@pytest.mark.parametrize("mode", ["click", "always"])
def test_reveal_mode_is_written_for_the_script(tmp_path: Path, mode: str) -> None:
    html = page(cfg(block(reveal=mode)), tmp_path)
    assert f'data-email-reveal="{mode}"' in _card(html)


def test_the_no_script_text_is_spelled_out(tmp_path: Path) -> None:
    html = page(cfg(block()), tmp_path)
    assert ">name at example dot test</p>" in html


def test_spelled_text_uses_the_configured_words(tmp_path: Path) -> None:
    html = page(
        cfg(
            block(
                address="a.b@mail.example.test",
                spell={"at": "(at)", "dot": "-dot-"},
            )
        ),
        tmp_path,
    )
    assert ">a -dot- b (at) mail -dot- example -dot- test</p>" in html


def test_spelled_text_defaults_use_at_and_dot(tmp_path: Path) -> None:
    html = page(cfg(block(address="first.last@example.test")), tmp_path)
    assert ">first dot last at example dot test</p>" in html


def test_the_note_and_title_show(tmp_path: Path) -> None:
    html = page(cfg(block(title="Contact me", note=NOTE)), tmp_path)
    assert "Contact me" in html
    assert f'<p class="email-note">{NOTE}</p>' in html


def test_no_note_means_no_note_element(tmp_path: Path) -> None:
    assert "email-note" not in page(cfg(block()), tmp_path)


def test_hostile_text_is_escaped(tmp_path: Path) -> None:
    html = page(
        cfg(
            block(
                title=HOSTILE,
                note=HOSTILE,
                spell={"at": "<b>", "dot": '"><i>'},
            )
        ),
        tmp_path,
    )
    assert HOSTILE not in html
    assert "<b>" not in html
    assert "<i>" not in html
    assert html.count("&lt;script&gt;alert(1)&lt;/script&gt;") >= 2
    assert "&lt;b&gt;" in html


def test_the_page_has_no_inline_script_or_handler(tmp_path: Path) -> None:
    html = page(cfg(block()), tmp_path)
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert not re.search(r"\son[a-z]+=", html)


def test_the_block_has_no_link_in_the_html(tmp_path: Path) -> None:
    html = page(cfg(block()), tmp_path)
    card = html[html.index('class="email-card"') :]
    card = card[: card.index("</li>")]
    assert "<a " not in card
    assert "href" not in card


# --- assets only where the block is --------------------------------------------

SCRIPT_TAG = re.compile(
    r'<script src="(?:\.\./)?static/email\.[0-9a-f]{20}\.js" defer></script>'
)
CSS_TAG = re.compile(
    r'<link rel="stylesheet" href="(?:\.\./)?static/email\.[0-9a-f]{20}\.css">'
)
EMAIL_REF = re.compile(r"email\.[0-9a-f]{20}\.(?:js|css)")


def test_assets_ship_with_a_click_block(tmp_path: Path) -> None:
    out = build(cfg(block()), tmp_path)
    html = (out / "index.html").read_text("utf-8")
    assert f'<script src="{asset(out, "static/email.js")}" defer></script>' in html
    assert SCRIPT_TAG.search(html)
    assert f'<link rel="stylesheet" href="{asset(out, "static/email.css")}">' in html
    assert CSS_TAG.search(html)
    assert asset_file(out, "static/email.js").is_file()
    assert asset_file(out, "static/email.css").is_file()


def test_never_js_ships_the_style_but_no_script(tmp_path: Path) -> None:
    out = build(cfg(block(reveal="never_js")), tmp_path)
    html = (out / "index.html").read_text("utf-8")
    assert not re.search(r"email\.[0-9a-f]{20}\.js", html)
    assert CSS_TAG.search(html)
    assert not has_asset(out, "static/email.js")
    assert not list((out / "static").glob("email*.js"))
    assert asset_file(out, "static/email.css").is_file()


def test_other_pages_and_sites_do_not_mention_the_assets(tmp_path: Path) -> None:
    out = build(cfg(block(), extra_tabs=1), tmp_path)
    other = (out / "plain0" / "index.html").read_text("utf-8")
    assert "email." not in other
    assert asset_file(out, "static/email.js").is_file()
    out2 = tmp_path / "second"
    build_site(cfg({"id": "t", "markdown": "hello"}), out2, today=TODAY)
    assert "email." not in (out2 / "index.html").read_text("utf-8")
    assert not has_asset(out2, "static/email.js")
    assert not has_asset(out2, "static/email.css")
    assert not list((out2 / "static").glob("email*"))
    assert "email" not in (out2 / "privacy" / "index.html").read_text("utf-8").lower()


def test_the_default_csp_still_forbids_inline_script(tmp_path: Path) -> None:
    headers = (build(cfg(block()), tmp_path) / "_headers").read_text("utf-8")
    script_src = headers.split("script-src")[1].split(";")[0]
    assert script_src.strip() == "'self'"


# --- the script file -------------------------------------------------------------


def test_the_script_has_no_network_eval_storage_or_html_building() -> None:
    text = (STATIC / "email.js").read_text("utf-8")
    assert len(text) > 500
    for banned in (
        "eval(",
        "new Function",
        "fetch(",
        "XMLHttpRequest",
        "sendBeacon",
        "WebSocket",
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "innerHTML",
        "document.write",
        "import(",
    ):
        assert banned not in text, banned


def test_the_script_navigates_only_through_the_seam() -> None:
    lines = (STATIC / "email.js").read_text("utf-8").splitlines()
    code = "\n".join(line for line in lines if not line.lstrip().startswith("//"))
    # The seam is checked first and called; location.href is only the else arm.
    # (A browser test cannot prove this: a script that ignored the seam would
    # really navigate, which no test may allow.)
    pattern = (
        r'if \(typeof window\.__raOpen === "function"\) \{\s*'
        r"window\.__raOpen\(url\);\s*\} else \{\s*"
        r"window\.location\.href = url;\s*\}"
    )
    assert re.search(pattern, code)
    assert code.count("location.href") == 1
    assert "location.assign" not in code
    assert "location.replace" not in code
    assert "window.open" not in code


def test_the_example_builds_and_leaks_nothing(tmp_path: Path) -> None:
    config = load_site_config(EXAMPLE)
    out = tmp_path / "out"
    build_site(config, out, today=TODAY, assets_dir=EXAMPLE.parent)
    pages = list(out.rglob("*.html"))
    assert pages
    for path in pages:
        text = path.read_text("utf-8")
        assert "@" not in text
        assert "mailto" not in text.lower()
