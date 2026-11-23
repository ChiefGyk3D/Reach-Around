# SPDX-License-Identifier: Apache-2.0
"""The support page (#683): one page that carries the crypto addresses.

Every address here is synthetic.
"""

from __future__ import annotations

import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from types import UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin

import pytest
from pydantic import BaseModel, ValidationError

from reach_around.config import SiteConfig
from reach_around.config.blocks import Crypto, Text
from reach_around.config.models import _displayed_text
from reach_around.site.build import build_site
from reach_around.site.supportcheck import check_support_html

TODAY = date(2027, 3, 10)
ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "examples" / "instance"
ADDR = "bc1qexampleexampleexampleexampleexample"


def crypto_block(ident: str = "pay") -> dict[str, Any]:
    return {"id": ident, "coins": [{"coin": "btc", "address": ADDR}]}


def data(
    support: dict[str, Any] | None, tabs: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": tabs
        or [
            {
                "id": "start",
                "title": "Start",
                "blocks": [{"id": "a", "title": "A", "url": "https://e.com/"}],
            }
        ],
    }
    if support is not None:
        out["support"] = support
    return out


SUPPORT: dict[str, Any] = {
    "title": "Support the show",
    "intro": "Every bit helps.",
    "blocks": [crypto_block()],
}


def build(raw: dict[str, Any], out: Path, assets: Path = ASSETS) -> dict[str, str]:
    build_site(SiteConfig.model_validate(raw), out, today=TODAY, assets_dir=assets)
    return {
        p.relative_to(out).as_posix(): p.read_text(encoding="utf-8")
        for p in out.rglob("*.html")
    }


def test_no_support_config_writes_no_support_page(tmp_path: Path) -> None:
    assert "support/index.html" not in build(data(None), tmp_path)


def test_support_page_is_written_with_title_intro_and_address(tmp_path: Path) -> None:
    html = build(data(SUPPORT), tmp_path)["support/index.html"]
    assert "<title>Support the show" in html
    assert "<h2>Support the show</h2>" in html
    assert "Every bit helps." in html
    assert ADDR in html
    assert 'data-copy-target="pay-addr-1"' in html
    assert re.search(r'<script src="\.\./static/copy\.[0-9a-f]{20}\.js"', html)
    assert re.search(r'href="\.\./static/crypto\.[0-9a-f]{20}\.css"', html)


def test_address_appears_on_the_support_page_only(tmp_path: Path) -> None:
    pages = build(data(SUPPORT), tmp_path)
    assert [name for name, html in pages.items() if ADDR in html] == [
        "support/index.html"
    ]


def test_crypto_in_a_tab_is_refused_when_support_is_set() -> None:
    tabs = [{"id": "start", "title": "Start", "blocks": [crypto_block("tabpay")]}]
    with pytest.raises(ValidationError, match="crypto addresses live on the support"):
        SiteConfig.model_validate(data(SUPPORT, tabs))


def test_only_one_crypto_block_on_the_support_page() -> None:
    support = {**SUPPORT, "blocks": [crypto_block("one"), crypto_block("two")]}
    with pytest.raises(ValidationError, match="at most one crypto block"):
        SiteConfig.model_validate(data(support))


def test_links_are_not_support_blocks() -> None:
    support = {
        **SUPPORT,
        "blocks": [{"id": "l", "title": "L", "url": "https://e.com/l"}],
    }
    with pytest.raises(ValidationError, match="support blocks"):
        SiteConfig.model_validate(data(support))


def test_block_ids_stay_unique_across_the_site() -> None:
    support = {**SUPPORT, "blocks": [crypto_block("a")]}
    with pytest.raises(ValidationError, match="duplicate id"):
        SiteConfig.model_validate(data(support))


def test_a_tab_cannot_take_the_support_path(tmp_path: Path) -> None:
    tabs = [
        {
            "id": "start",
            "title": "Start",
            "blocks": [{"id": "a", "title": "A", "url": "https://e.com/"}],
        },
        {
            "id": "support",
            "title": "Support",
            "blocks": [{"id": "b", "title": "B", "url": "https://e.com/b"}],
        },
    ]
    with pytest.raises(ValueError, match="support"):
        build(data(SUPPORT, tabs), tmp_path)


def test_support_coin_qr_is_refused() -> None:
    coin = {"coin": "btc", "address": ADDR, "qr": "media/qr.png"}
    support = {**SUPPORT, "blocks": [{"id": "pay", "coins": [coin]}]}
    with pytest.raises(ValidationError, match="no QR image"):
        SiteConfig.model_validate(data(support))


def test_image_blocks_are_not_support_blocks() -> None:
    block = {"id": "pic", "src": "media/qr.png", "alt": "A picture"}
    with pytest.raises(ValidationError, match="support blocks"):
        SiteConfig.model_validate(data({**SUPPORT, "blocks": [block]}))


def test_the_page_renders_intro_then_crypto_then_other_blocks(tmp_path: Path) -> None:
    text = {"id": "words", "markdown": "Plain words after."}
    support = {**SUPPORT, "blocks": [text, crypto_block()]}
    html = build(data(support), tmp_path)["support/index.html"]
    assert (
        html.index("Every bit helps.")
        < html.index('class="block block-crypto"')
        < html.index("Plain words after.")
    )


def test_the_address_is_an_isolated_ltr_element(tmp_path: Path) -> None:
    html = build(data(SUPPORT), tmp_path)["support/index.html"]
    assert f'dir="ltr">{ADDR}</code>' in html


BIDI = chr(0x202E)
ZERO = chr(0x200B)
NONCHAR = chr(0xFFFE)
LINESEP = chr(0x2028)
HEX40 = "0x" + "a1" * 20
HEX_SHORT = "0xa1a1a1a1a1a1"
LONG = "A" * 26


def _with(block: dict[str, Any]) -> dict[str, Any]:
    return {**SUPPORT, "blocks": [crypto_block("pay"), block]}


def _field_cases() -> list[tuple[str, Any]]:
    """Each displayed field, as a function from a bad value to a config."""

    def crypto_field(name: str) -> Any:
        return lambda v: {**SUPPORT, "blocks": [{**crypto_block(), name: v}]}

    def coin_field(name: str) -> Any:
        def make(v: str) -> dict[str, Any]:
            coin = {"coin": "btc", "address": ADDR, name: v}
            if name == "name":
                coin["coin"] = "other"
            return {**SUPPORT, "blocks": [{"id": "pay", "coins": [coin]}]}

        return make

    return [
        ("title", lambda v: {**SUPPORT, "title": v}),
        ("intro", lambda v: {**SUPPORT, "intro": v}),
        ("text", lambda v: _with({"id": "t", "markdown": v})),
        ("crypto title", crypto_field("title")),
        ("crypto note", crypto_field("note")),
        ("coin label", coin_field("label")),
        ("coin name", coin_field("name")),
        ("coin network", coin_field("network")),
    ]


@pytest.mark.parametrize("bad", [BIDI, ZERO, NONCHAR, LINESEP])
@pytest.mark.parametrize(
    ("field", "make"), _field_cases(), ids=[n for n, _ in _field_cases()]
)
def test_every_displayed_field_is_plain_text(field: str, make: Any, bad: str) -> None:
    with pytest.raises(ValidationError, match="plain text|plain"):
        SiteConfig.model_validate(data(make("a" + bad + "b")))


@pytest.mark.parametrize(
    "bad",
    [
        HEX_SHORT,
        LONG,
        "bc1qabcdefgh",
        "lnbc1abcdef",
        "send " + LONG,
        "name.eth",
        "pay.example.com",
        "hello@example",
        "$somehandle",
        "word.tld",
        "name.sol",
        "x.xyz",
    ],
)
@pytest.mark.parametrize(
    ("field", "make"), _field_cases(), ids=[n for n, _ in _field_cases()]
)
def test_no_displayed_field_holds_an_address_like_token(
    field: str, make: Any, bad: str
) -> None:
    with pytest.raises(ValidationError, match="address-like"):
        SiteConfig.model_validate(data(make(bad)))


def test_the_coin_address_itself_is_still_allowed() -> None:
    SiteConfig.model_validate(data(SUPPORT))


def test_text_blocks_allow_paragraph_breaks_but_no_other_control() -> None:
    SiteConfig.model_validate(data(_with({"id": "t", "markdown": "One.\n\nTwo."})))
    with pytest.raises(ValidationError, match="plain text"):
        SiteConfig.model_validate(data(_with({"id": "t", "markdown": "a\rb"})))


@pytest.mark.parametrize(
    "md",
    [
        "[x](https://e.com/)",
        "![x](media/a.png)",
        "<b>x</b>",
        "see https://e.com/x",
        "bitcoin:abc",
        "mailto:a@e.com",
        "www.e.com",
    ],
)
def test_text_blocks_take_no_links_images_html_or_schemes(md: str) -> None:
    with pytest.raises(ValidationError, match="no links"):
        SiteConfig.model_validate(data(_with({"id": "t", "markdown": md})))


def test_a_full_hex_address_in_text_is_refused() -> None:
    with pytest.raises(ValidationError, match="address-like"):
        SiteConfig.model_validate(data(_with({"id": "t", "markdown": HEX40})))


# --- every rendered string is checked ---------------------------------------


def _leaf_paths(
    model: type[BaseModel], prefix: tuple[str, ...] = ()
) -> list[tuple[str, ...]]:
    """Every string field of a block model, nested ones included."""
    out: list[tuple[str, ...]] = []
    for name, field in model.model_fields.items():
        ann = field.annotation
        inner = get_args(ann)[0] if get_origin(ann) in (list, Union, UnionType) else ann
        args = [a for a in get_args(ann) if a is not type(None)]
        if get_origin(ann) in (Union, UnionType) and args:
            inner = args[0]
        if get_origin(ann) is list:
            inner = get_args(ann)[0]
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            out += _leaf_paths(inner, (*prefix, name))
        elif get_origin(inner) is Literal:
            continue
        elif (
            inner is str or get_origin(inner) is Annotated or "str" in str(ann).lower()
        ):
            out.append((*prefix, name))
    return out


# Strings the walk does not read as prose, each with its own rule: ``id`` is a
# slug (BlockId pattern), ``address`` is the coin address or the email address
# (read separately), ``qr`` is refused on this page.
EXEMPT = {"id", "address", "qr"}
SAMPLES: dict[str, dict[str, Any]] = {
    "crypto": {"id": "pay", "coins": [{"coin": "other", "address": ADDR}]},
    "text": {"id": "words", "markdown": "x"},
}
MODELS = {"crypto": Crypto, "text": Text}


def _set(block: dict[str, Any], path: tuple[str, ...], value: str) -> None:
    node: Any = block
    for key in path[:-1]:
        node = (
            node[key][0]
            if isinstance(node.get(key), list)
            else node.setdefault(key, {})
        )
    node[path[-1]] = value


@pytest.mark.parametrize("kind", sorted(MODELS))
def test_every_rendered_string_is_in_the_checked_set(kind: str, tmp_path: Path) -> None:
    block = {
        k: (v.copy() if isinstance(v, dict) else v) for k, v in SAMPLES[kind].items()
    }
    paths = [p for p in _leaf_paths(MODELS[kind]) if p[-1] not in EXEMPT]
    expected = {
        "crypto": {
            ("title",),
            ("note",),
            ("coins", "name"),
            ("coins", "label"),
            ("coins", "network"),
        },
        "text": {("markdown",)},
    }
    assert set(paths) == expected[kind], (kind, paths)
    marks = {}
    for path in paths:
        mark = "Zmark" + "".join(path).replace("_", "")
        marks[mark] = path
        _set(block, path, mark)
    support = {
        **SUPPORT,
        "blocks": [crypto_block("pay"), block] if kind != "crypto" else [block],
    }
    cfg = SiteConfig.model_validate(data(support))
    html = build(data(support), tmp_path)["support/index.html"]
    walked = [value for value, _kind in _displayed_text(cfg.support)]
    rendered = [m for m in marks if m in html]
    for mark in marks:
        # a field that is displayed must be in the walk: no field slips through
        if mark in html:
            assert any(mark in value for value in walked), (kind, marks[mark])
    assert rendered, kind


# --- structure: plain text only, no email, final HTML check, policy --------


def test_email_blocks_are_not_support_blocks() -> None:
    block = {"id": "mail", "address": "hello@example.com"}
    with pytest.raises(ValidationError, match="support blocks"):
        SiteConfig.model_validate(data(_with(block)))


FULLWIDTH_ONE = chr(0xFF11)  # a digit: only NFKC turns it into a long ASCII run
FULLWIDTH_A = chr(0xFF21)  # NFKC folds this to "A"
CYRILLIC_A = chr(0x0430)  # a lookalike letter that stays non-ASCII
GREEK_O = chr(0x03BF)


@pytest.mark.parametrize(
    "bad",
    [
        FULLWIDTH_A * 26,
        FULLWIDTH_ONE * 26,
        "caf" + CYRILLIC_A,
        "n" + GREEK_O + "te",
    ],
)
@pytest.mark.parametrize(
    ("field", "make"), _field_cases(), ids=[n for n, _ in _field_cases()]
)
def test_lookalike_and_non_ascii_letters_are_refused(
    field: str, make: Any, bad: str
) -> None:
    with pytest.raises(ValidationError, match="address-like|non-ASCII"):
        SiteConfig.model_validate(data(make(bad)))


def test_ordinary_sentences_pass_the_address_check() -> None:
    SiteConfig.model_validate(
        data(
            {
                **SUPPORT,
                "title": "Support the show",
                "intro": "Thank you. Every bit helps, e.g. a coffee.",
                "blocks": [crypto_block(), {"id": "t", "markdown": "Be kind.\n\nBye."}],
            }
        )
    )


class _Visible(HTMLParser):
    """The visible text of a page, skipping scripts, styles and each address."""

    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        if tag in ("script", "style") or "crypto-address" in classes:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if self._skip and tag in ("script", "style", "code"):
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.text.append(data)


def _visible_support_text(html: str) -> str:
    section = html[html.index('<section class="support">') : html.index("</section>")]
    parser = _Visible()
    parser.feed(section)
    return " ".join(parser.text)


def test_rendered_visible_text_passes_the_address_check_for_every_field(
    tmp_path: Path,
) -> None:
    from reach_around.config.support import check_no_address

    support = {
        "title": "Support",
        "intro": "Thanks for stopping by.",
        "blocks": [
            {
                "id": "pay",
                "title": "Crypto",
                "note": "Check twice.",
                "coins": [
                    {
                        "coin": "other",
                        "name": "Coin",
                        "label": "My coin",
                        "network": "Mainnet",
                        "address": ADDR,
                    }
                ],
            },
            {"id": "t", "markdown": "First line\nsecond line\n\nNew paragraph."},
        ],
    }
    html = build(data(support), tmp_path)["support/index.html"]
    visible = _visible_support_text(html)
    for word in ("Support", "Thanks", "Check twice", "My coin", "New paragraph"):
        assert word in visible
    assert ADDR not in visible  # the address element is skipped
    check_no_address(visible)


def test_text_is_escaped_plain_text_not_markdown(tmp_path: Path) -> None:
    run = "AAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    text = f"{run[:12]}**{run[:12]}**{run[:12]} _under_ `code` # not a heading"
    support = {**SUPPORT, "blocks": [crypto_block(), {"id": "t", "markdown": text}]}
    with pytest.raises(ValidationError, match="address-like"):
        # the long run is refused whatever the markers between its pieces
        SiteConfig.model_validate(
            data(
                {
                    **support,
                    "blocks": [
                        crypto_block(),
                        {"id": "t", "markdown": run + " " + run},
                    ],
                }
            )
        )
    short = "AAAAAAAAAAAA**BBBBBBBBBBBB**CCCCCCCCCCCC _under_ `code` # not a heading"
    support = {**SUPPORT, "blocks": [crypto_block(), {"id": "t", "markdown": short}]}
    html = build(data(support), tmp_path)["support/index.html"]
    body = html[html.index('<div class="text">') : html.index("</section>")]
    assert short in body  # shown literally
    for tag in ("<strong", "<em", "<code", "<h1", "<a "):
        assert tag not in body


def test_text_lines_and_paragraphs(tmp_path: Path) -> None:
    support = {
        **SUPPORT,
        "blocks": [crypto_block(), {"id": "t", "markdown": "One\ntwo\n\nThree"}],
    }
    html = build(data(support), tmp_path)["support/index.html"]
    assert "<p>One<br>two</p>" in html
    assert "<p>Three</p>" in html


# the final check over the built HTML
@pytest.mark.parametrize(
    "injected",
    [
        '<a href="https://e.com/">x</a>',
        '<img src="https://e.com/x.png" alt="x">',
        '<picture><source srcset="x.png"></picture>',
        '<iframe src="https://e.com/"></iframe>',
        "<object data='x'></object>",
        "<embed src='x'>",
        '<form action="/x"></form>',
        '<p ping="https://e.com/">x</p>',
        '<div data="x"></div>',
        '<span poster="x"></span>',
    ],
)
def test_the_final_html_check_refuses_links_images_and_url_attributes(
    injected: str,
) -> None:
    page = (
        '<main><header><a href="/">chrome</a><img src="a.png"></header>'
        f'<section class="support"><p>x</p>{injected}</section>'
        '<footer><a href="/privacy/">Privacy</a></footer></main>'
    )
    with pytest.raises(ValueError, match="must not have"):
        check_support_html(page)


def test_the_final_html_check_allows_chrome_and_the_copy_button() -> None:
    page = (
        '<main><header><a href="/">chrome</a><img src="a.png"></header>'
        '<section class="support"><ul><li><code dir="ltr">x</code>'
        '<button type="button" data-copy-target="a">Copy</button></li></ul>'
        '</section><footer><a href="/privacy/">Privacy</a></footer></main>'
    )
    check_support_html(page)


def test_the_build_refuses_a_template_override_that_injects_an_image(
    tmp_path: Path,
) -> None:
    overrides = tmp_path / "overrides"
    (overrides / "templates" / "blocks").mkdir(parents=True)
    (overrides / "templates" / "blocks" / "crypto.html").write_text(
        '<li class="block"><img src="https://e.com/q.png" alt=""></li>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must not have"):
        build_site(
            SiteConfig.model_validate(data(SUPPORT)),
            tmp_path / "out",
            today=TODAY,
            assets_dir=ASSETS,
            overrides=overrides,
        )


def test_the_support_path_gets_an_image_policy_for_this_site_only(
    tmp_path: Path,
) -> None:
    build(data(SUPPORT), tmp_path)
    headers = (tmp_path / "_headers").read_text(encoding="utf-8")
    block = headers[headers.index("\n/support/\n") :].split("\n\n", 1)[0]
    assert "img-src 'self';" in block
    assert "https:" not in block.split("img-src", 1)[1].split(";", 1)[0]
    assert "default-src 'self'" in block
    assert "connect-src 'self'" in block
    site_wide = headers.split("\n\n", 1)[0]
    assert "img-src 'self' https:" in site_wide


def test_no_support_policy_without_a_support_page(tmp_path: Path) -> None:
    build(data(None), tmp_path)
    assert "/support/" not in (tmp_path / "_headers").read_text(encoding="utf-8")
