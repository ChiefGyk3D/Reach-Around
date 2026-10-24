# SPDX-License-Identifier: Apache-2.0
"""The crypto block: config rules, rendering, the copy script's reach and contract.

Every address here is synthetic. The block never validates a checksum, so any
string of the right shape is accepted.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from reach_around.config import SiteConfig, load_site_config
from reach_around.config.loader import ConfigError
from reach_around.site.build import build_site

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "crypto" / "site.yaml"
STATIC = ROOT / "src" / "reach_around" / "site" / "static"
COPY_JS = STATIC / "copy.js"
CRYPTO_CSS = STATIC / "crypto.css"
TODAY = date(2027, 3, 10)
ADDR = "bc1qexampleexampleexampleexampleexample"
NOTE = "Check the first and last characters of the address before you send anything."
GOOD_PNG = b"\x89PNG\r\n\x1a\nsynthetic\x00\x00\x00\x00IEND\xaeB`\x82"
HOSTILE = "<script>alert(1)</script>"

SCRIPT_TAG = '<script src="static/copy.js" defer></script>'
CSS_TAG = '<link rel="stylesheet" href="static/crypto.css">'


def coin(**over: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"coin": "btc", "address": ADDR}
    entry.update(over)
    return entry


def block(*coins: dict[str, Any], **over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"id": "pay", "coins": list(coins) or [coin()]}
    out.update(over)
    return out


def cfg(*blocks: dict[str, Any]) -> SiteConfig:
    return SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "Example Creator"},
            "tabs": [{"id": "support", "title": "Support", "blocks": list(blocks)}],
        }
    )


def build(
    config: SiteConfig, tmp_path: Path, files: dict[str, bytes] | None = None
) -> Path:
    inst = tmp_path / "inst"
    inst.mkdir(parents=True, exist_ok=True)
    for name, data in (files or {}).items():
        (inst / name).parent.mkdir(parents=True, exist_ok=True)
        (inst / name).write_bytes(data)
    out = tmp_path / "out"
    build_site(config, out, today=TODAY, assets_dir=inst)
    return out


def page(
    config: SiteConfig, tmp_path: Path, files: dict[str, bytes] | None = None
) -> str:
    return (build(config, tmp_path, files) / "index.html").read_text("utf-8")


# --- config ------------------------------------------------------------------


@pytest.mark.parametrize("name", ["btc", "xmr", "eth", "sol"])
def test_each_named_coin_is_accepted(name: str) -> None:
    cfg(block(coin(coin=name)))


def test_other_needs_a_name_and_named_coins_refuse_one() -> None:
    cfg(block(coin(coin="other", name="Litecoin")))
    with pytest.raises(ValueError, match="name"):
        cfg(block(coin(coin="other")))
    with pytest.raises(ValueError, match="name"):
        cfg(block(coin(coin="btc", name="Bitcoin")))


def test_unknown_coin_is_refused() -> None:
    with pytest.raises(ValueError, match="coin"):
        cfg(block(coin(coin="doge")))


@pytest.mark.parametrize(
    ("length", "ok"), [(19, False), (20, True), (128, True), (129, False)]
)
def test_address_length_boundaries(length: int, ok: bool) -> None:
    candidate = "a" * length
    if ok:
        cfg(block(coin(address=candidate)))
    else:
        with pytest.raises(ValueError, match="address"):
            cfg(block(coin(address=candidate)))


@pytest.mark.parametrize(
    "bad",
    [
        "has space" + "a" * 20,
        "tab\there" + "a" * 20,
        "line\nbreak" + "a" * 20,
        "a" * 20 + "\n",
        "nul\x00byte" + "a" * 20,
        "bell\x07" + "a" * 20,
        "bidi‮evil" + "a" * 20,
        "isolate⁦x" + "a" * 20,
        "zero​width" + "a" * 20,
        'quote"' + "a" * 20,
        "apos'" + "a" * 20,
        "angle<" + "a" * 20,
        "angle>" + "a" * 20,
        "tick`" + "a" * 20,
        "slash/" + "a" * 20,
        "eq=" + "a" * 20,
        "café" + "a" * 20,
        "１" * 20,
    ],
)
def test_hostile_address_characters_are_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="address"):
        cfg(block(coin(address=bad)))


def test_address_must_be_a_string() -> None:
    with pytest.raises(ValueError, match="address"):
        cfg(block(coin(address=12345678901234567890)))


def test_allowed_separators_pass() -> None:
    cfg(block(coin(address="bitcoin:bc1q.example_example-example")))


def test_address_is_never_checksum_validated() -> None:
    cfg(block(coin(address="x" * 30)))


def test_coin_count_bounds() -> None:
    cfg(block(*[coin() for _ in range(12)]))
    with pytest.raises(ValueError, match="coins"):
        cfg(block(*[coin() for _ in range(13)]))
    with pytest.raises(ValueError, match="coins"):
        cfg({"id": "pay", "coins": []})


def test_text_fields_are_plain_and_bounded() -> None:
    cfg(block(coin(network="n" * 40)))
    with pytest.raises(ValueError, match="network"):
        cfg(block(coin(network="n" * 41)))
    for key in ("network", "label"):
        with pytest.raises(ValueError, match=key):
            cfg(block(coin(**{key: "bad\x07text"})))
        with pytest.raises(ValueError, match=key):
            cfg(block(coin(**{key: "bidi‮text"})))
    with pytest.raises(ValueError, match="note"):
        cfg(block(note="line\x00"))
    with pytest.raises(ValueError, match="note"):
        cfg(block(note="x" * 301))


def test_unknown_keys_are_refused() -> None:
    with pytest.raises(ValueError, match="wallet"):
        cfg(block(coin(wallet="x")))
    with pytest.raises(ValueError, match="color"):
        cfg(block(color="red"))


@pytest.mark.parametrize(
    "qr",
    ["/etc/qr.png", "../qr.png", "https://e.com/qr.png", "media/qr.txt", "a b.png"],
)
def test_qr_path_rules(qr: str) -> None:
    with pytest.raises(ValueError, match="qr"):
        cfg(block(coin(qr=qr)))


def test_errors_name_the_key(tmp_path: Path) -> None:
    path = tmp_path / "site.yaml"
    path.write_text(
        "version: 1\ncreator: {name: X}\ntabs:\n- id: t\n  title: T\n  blocks:\n"
        "  - id: pay\n    coins:\n    - {coin: btc, address: short}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as caught:
        load_site_config(path)
    assert "tabs[0].blocks[0].coins[0].address" in str(caught.value)


def test_crypto_block_ids_stay_unique() -> None:
    with pytest.raises(ValueError, match="duplicate id"):
        cfg(block(), {"id": "pay"})


# --- rendering ---------------------------------------------------------------


def test_cards_show_icon_label_network_code_button_and_note(tmp_path: Path) -> None:
    html = page(
        cfg(
            block(
                coin(network="Bitcoin mainnet"),
                coin(coin="xmr", address="4" + "a" * 30),
                coin(coin="eth", address="0x" + "b" * 30),
                coin(coin="sol", address="s" * 30),
                coin(coin="other", name="Litecoin", address="l" * 30),
                title="Send crypto",
            )
        ),
        tmp_path,
    )
    for icon in ("bitcoin", "monero", "ethereum", "solana", "link"):
        assert f'class="icon icon-{icon}"' in html, icon
    assert "<h2" in html and "Send crypto" in html
    assert "Bitcoin mainnet" in html and "Litecoin" in html
    assert html.count("<code") == 5
    assert f">{ADDR}</code>" in html
    assert html.count('type="button"') == 5
    assert html.count(">Copy</button>") == 5
    assert html.count(NOTE) == 1
    assert 'aria-live="polite"' in html
    assert "<details" not in html  # no qr, no details


def test_button_points_at_the_code_element(tmp_path: Path) -> None:
    html = page(cfg(block()), tmp_path)
    target = re.search(r'data-copy-target="([^"]+)"', html)
    assert target is not None
    assert f'<code class="crypto-address" id="{target.group(1)}"' in html


def test_note_override_replaces_the_default(tmp_path: Path) -> None:
    html = page(cfg(block(note="Ask me first.")), tmp_path)
    assert "Ask me first." in html and NOTE not in html


def test_hostile_text_is_escaped(tmp_path: Path) -> None:
    html = page(
        cfg(
            block(
                coin(label=HOSTILE, network=HOSTILE),
                coin(coin="other", name=HOSTILE),
                title=HOSTILE,
                note=HOSTILE,
            )
        ),
        tmp_path,
    )
    assert HOSTILE not in html
    assert html.count("&lt;script&gt;alert(1)&lt;/script&gt;") >= 5


def test_qr_renders_inside_native_details_with_alt_and_lazy(tmp_path: Path) -> None:
    html = page(
        cfg(block(coin(label="Bitcoin", qr="media/qr.png"))),
        tmp_path,
        {"media/qr.png": GOOD_PNG},
    )
    assert "<details" in html and "<summary" in html
    assert 'alt="QR code for Bitcoin address"' in html
    img = re.search(r'<img[^>]*src="media/qr\.png"[^>]*>', html)
    assert img is not None and 'loading="lazy"' in img.group(0)
    assert (tmp_path / "out" / "media" / "qr.png").read_bytes() == GOOD_PNG


def test_qr_alt_text_is_escaped(tmp_path: Path) -> None:
    html = page(
        cfg(block(coin(label=HOSTILE, qr="media/qr.png"))),
        tmp_path,
        {"media/qr.png": GOOD_PNG},
    )
    assert HOSTILE not in html


def test_qr_goes_through_the_asset_pipeline(tmp_path: Path) -> None:
    config = cfg(block(coin(qr="media/qr.png")))
    with pytest.raises(ValueError, match="not a real png"):
        build(config, tmp_path, {"media/qr.png": b"just some text, not an image"})
    with pytest.raises(ValueError, match="assets_dir"):
        build_site(config, tmp_path / "o2", today=TODAY)
    with pytest.raises(ValueError, match="cannot read"):
        build(config, tmp_path / "fresh", {})


def test_the_qr_slot_is_reported_by_key(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"tabs\[0\]\.blocks\[0\]\.coins\[0\]\.qr"):
        build(cfg(block(coin(qr="media/qr.png"))), tmp_path, {"media/qr.png": b"x"})


# --- the script's reach ------------------------------------------------------


def test_pages_without_a_crypto_block_do_not_mention_the_script(
    tmp_path: Path,
) -> None:
    out = build(cfg({"id": "rule"}), tmp_path)
    html = (out / "index.html").read_text("utf-8")
    assert "copy.js" not in html and "crypto.css" not in html
    assert not (out / "static" / "copy.js").exists()
    assert not (out / "static" / "crypto.css").exists()


def test_only_crypto_pages_reference_the_script(tmp_path: Path) -> None:
    config = SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "Example Creator"},
            "tabs": [
                {"id": "home", "title": "Home", "blocks": [{"id": "rule"}]},
                {"id": "support", "title": "Support", "blocks": [block()]},
            ],
        }
    )
    out = build(config, tmp_path)
    assert (out / "static" / "copy.js").is_file()
    assert (out / "static" / "crypto.css").is_file()
    home = (out / "index.html").read_text("utf-8")
    support = (out / "support" / "index.html").read_text("utf-8")
    privacy = (out / "privacy" / "index.html").read_text("utf-8")
    assert "copy.js" not in home and "copy.js" not in privacy
    assert "crypto.css" not in home and "crypto.css" not in privacy
    assert SCRIPT_TAG.replace("static/", "../static/") in support
    assert CSS_TAG.replace("static/", "../static/") in support
    assert support.count("copy.js") == 1


def test_a_crypto_block_on_the_first_tab_uses_root_relative_paths(
    tmp_path: Path,
) -> None:
    html = page(cfg(block()), tmp_path)
    assert SCRIPT_TAG in html and CSS_TAG in html


def test_two_crypto_blocks_still_load_the_script_once(tmp_path: Path) -> None:
    second = block(coin(), id="pay-two")
    html = page(cfg(block(), second), tmp_path)
    assert html.count("copy.js") == 1


# --- CSP safety --------------------------------------------------------------

INLINE_STYLE = re.compile(r"<[^>]*\sstyle\s*=", re.IGNORECASE)
INLINE_HANDLER = re.compile(r"<[^>]*\son[a-z]+\s*=", re.IGNORECASE)
INLINE_SCRIPT = re.compile(r"<script\b(?![^>]*\bsrc\s*=)", re.IGNORECASE)


def violations(html: str) -> list[str]:
    return [
        label
        for label, pattern in (
            ("style attribute", INLINE_STYLE),
            ("event handler", INLINE_HANDLER),
            ("inline script", INLINE_SCRIPT),
        )
        if pattern.search(html)
    ]


def test_guard_bites() -> None:
    assert violations('<button onclick="x()">') == ["event handler"]
    assert violations('<p style="a">') == ["style attribute"]
    assert violations("<script>x</script>") == ["inline script"]
    assert violations(SCRIPT_TAG) == []


def test_crypto_pages_stay_csp_safe(tmp_path: Path) -> None:
    html = page(
        cfg(block(coin(label=HOSTILE, qr="media/qr.png"), coin(coin="eth"))),
        tmp_path,
        {"media/qr.png": GOOD_PNG},
    )
    assert violations(html) == []
    assert "copy.js" in html


def test_the_example_builds_and_is_csp_safe(tmp_path: Path) -> None:
    config = load_site_config(EXAMPLE)
    out = tmp_path / "out"
    build_site(config, out, today=TODAY, assets_dir=EXAMPLE.parent)
    html = (out / "index.html").read_text("utf-8")
    assert violations(html) == []
    assert html.count("<code") == 5
    assert "QR code for Bitcoin address" in html
    assert (out / "media" / "qr-example.png").is_file()
    assert "Litecoin" in html
    headers = (out / "_headers").read_text("utf-8")
    assert "script-src 'self'" in headers


# --- the script's contract ---------------------------------------------------

FORBIDDEN = {
    "eval": r"\beval\s*\(",
    "Function constructor": r"\bnew\s+Function\b|\bFunction\s*\(",
    "setTimeout string": r"setTimeout\s*\(\s*[\"']",
    "innerHTML": r"\binnerHTML\b|\bouterHTML\b|insertAdjacentHTML",
    "document.write": r"document\s*\.\s*write",
    "fetch": r"\bfetch\s*\(",
    "XMLHttpRequest": r"XMLHttpRequest",
    "sendBeacon": r"sendBeacon",
    "WebSocket": r"WebSocket|EventSource",
    "storage": r"localStorage|sessionStorage|indexedDB|document\s*\.\s*cookie",
    "import": r"\bimport\s*\(|\bimportScripts\b",
    "http url": r"https?://",
}


def forbidden_in(source: str) -> list[str]:
    stripped = re.sub(r"//[^\n]*", "", source)
    return [name for name, pattern in FORBIDDEN.items() if re.search(pattern, stripped)]


def test_contract_guard_bites() -> None:
    assert forbidden_in("fetch('x')") == ["fetch"]
    assert forbidden_in("el.innerHTML = a") == ["innerHTML"]
    assert forbidden_in("eval('1')") == ["eval"]
    assert forbidden_in("navigator.sendBeacon(u)") == ["sendBeacon"]
    assert forbidden_in("// fetch(x) in a comment\nvar a = 1;") == []


def test_copy_script_has_no_network_eval_or_html_injection() -> None:
    source = COPY_JS.read_text("utf-8")
    assert len(source) > 200, "the script should not be empty"
    assert forbidden_in(source) == []
    assert "navigator.clipboard.writeText" in source
    assert "data-copy-status" in source
    assert "2000" in source
    assert "Copied" in source


def test_crypto_css_reads_only_tokens_for_colors() -> None:
    css = re.sub(r"/\*.*?\*/", "", CRYPTO_CSS.read_text("utf-8"), flags=re.S)
    assert css.count("{") > 8, "the guard collected too few rules"
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", css)
    assert not re.findall(r"\b(?:rgb|rgba|hsl|hsla|oklch|lab|lch)\(", css)
    assert "overflow-wrap: anywhere" in css
    assert "user-select: all" in css
