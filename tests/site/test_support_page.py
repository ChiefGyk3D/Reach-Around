# SPDX-License-Identifier: Apache-2.0
"""The support page (#683): one page that carries the crypto addresses.

Every address here is synthetic.
"""

# ruff: noqa: E501
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
from reach_around.site.supportcheck import SupportPageError, check_support_html

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
MODELS: dict[str, type[BaseModel]] = {"crypto": Crypto, "text": Text}


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
    assert cfg.support is not None
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


# the final structural check over the built HTML
ADDRESS_ELEMENT = (
    '<ul class="crypto-list"><li class="crypto-card"><div class="crypto-body">'
    '<code class="crypto-address" id="a-1" dir="ltr">ADDR</code>'
    '<button class="copy-button" type="button" data-copy-target="a-1" '
    'aria-label="Copy">Copy</button></div></li></ul>'
)


def page(
    inner: str = "",
    *,
    head: str = "",
    body: str = "",
    main: str = "",
    address: str = ADDRESS_ELEMENT,
) -> str:
    """A well-formed page shaped like the stock output, with places to inject."""
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        "<title>T</title>"
        '<link rel="stylesheet" href="../static/site.0123456789abcdef0123.css">'
        '<script src="../static/copy.0123456789abcdef0123.js" defer></script>'
        f"{head}</head><body>"
        '<a class="skip-link" href="#content">Skip</a>'
        f'<main id="content"><header><a href="/">Home</a></header>'
        f'<section class="support"><h2>T</h2>{address}{inner}</section>'
        f'{main}<footer><a href="/privacy/">Privacy</a></footer></main>{body}'
        "</body></html>"
    ).replace("ADDR", ADDR)


def test_the_stock_shape_passes_the_structural_check() -> None:
    check_support_html(page(), [ADDR])


@pytest.mark.parametrize(
    "inner",
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
        '<meta http-equiv="refresh" content="0;url=https://e.com/">',
        "<svg><use xlink:href='#a'></use></svg>",
        "<svg></svg>",
        "<image href='x'>",
        "<style>p{color:red}</style>",
        '<p style="color:red">x</p>',
        '<p onclick="x()">x</p>',
        '<p onmouseover="x()">x</p>',
        '<p xlink:href="x">x</p>',
        '<p xml:lang="en">x</p>',
        '<p><animate attributeName="x"></animate></p>',
        '<p data-x="1">x</p>',
        "<textarea></p></textarea>",
        "<xmp><p></xmp>",
        "<plaintext>",
        "<template><p></p></template>",
        "<noscript><p></p></noscript>",
        "<!-- a comment -->",
        "<h3>heading</h3>",
        "<table></table>",
        '<section class="support"></section>',
    ],
)
def test_the_structural_check_refuses_anything_unknown_in_the_support_content(
    inner: str,
) -> None:
    with pytest.raises(SupportPageError):
        check_support_html(page(inner), [ADDR])


@pytest.mark.parametrize(
    ("where", "text"),
    [
        ("head", '<meta http-equiv="refresh" content="0;url=https://e.com/">'),
        ("head", '<meta name="other" content="x">'),
        ("head", "<style>p{}</style>"),
        (
            "head",
            '<link rel="stylesheet" href="../static/custom.0123456789abcdef0123.css">',
        ),
        ("head", '<link rel="stylesheet" href="https://e.com/x.css">'),
        ("head", '<link rel="stylesheet" href="../static/site.css">'),
        ("head", '<script src="https://e.com/x.js"></script>'),
        ("head", '<script src="../static/live.0123456789abcdef0123.js"></script>'),
        ("head", "<script>alert(1)</script>"),
        ("head", "<base href='https://e.com/'>"),
        ("head", '<link rel="alternate" href="x">'),
        ("body", "<p>text after main</p>"),
        ("body", "loose text"),
        ("body", "<div></div>"),
        ("body", "<main></main>"),
        ("main", "<p>an extra paragraph</p>"),
        ("main", "<div>content</div>"),
        ("main", "loose text"),
        ("main", "<section>other</section>"),
        ("main", '<section class="support"></section>'),
        ("main", "<nav></nav><nav></nav><nav></nav>"),
        ("main", "<img src='x'>"),
        ("main", "<footer></footer>"),
        ("main", "<script>x</script>"),
        ("main", "<nav><textarea></textarea></nav>"),
        ("main", "<nav><xmp></xmp></nav>"),
        ("main", "<nav><svg></svg></nav>"),
        ("main", "<nav><style></style></nav>"),
        ("main", "<nav><noscript></noscript></nav>"),
        ("main", "<nav><iframe></iframe></nav>"),
        ("main", '<nav onclick="x()"></nav>'),
        ("main", '<nav style="display:none"></nav>'),
        ("main", '<nav xlink:href="x"></nav>'),
        ("head", '<meta name="viewport" http-equiv="refresh" content="0;url=x">'),
    ],
)
def test_the_structural_check_refuses_content_outside_the_chrome(
    where: str, text: str
) -> None:
    with pytest.raises(SupportPageError):
        check_support_html(page(**{where: text}), [ADDR])


@pytest.mark.parametrize(
    "broken",
    [
        # a stray end tag right after the section opens
        page().replace("<h2>T</h2>", "</p><h2>T</h2>"),
        # the section closed early, content after it
        page().replace("</section>", "</section><p>x</p></section>"),
        page().replace("<h2>T</h2>", "<h2>T</h2></section><div>escaped</div><section>"),
        # an unclosed element
        page().replace("</code>", ""),
        page().replace("</section>", "<div></section>"),
        # mismatched nesting
        page().replace("<h2>T</h2>", "<p><div></p></div>"),
        # a missing end for the section
        page().replace("</section>", ""),
        # no section at all
        page().replace('<section class="support">', "<section>"),
        # an end tag for a void element
        page().replace("<h2>", "</br><h2>"),
        # a processing instruction and a marked section
        page().replace("<h2>", "<?php x ?><h2>"),
        page().replace("<h2>", "<![CDATA[x]]><h2>"),
        # a duplicate id anywhere
        page(main='<footer id="a-1"></footer>'),
        page().replace("<h2>", '<h2 id="x"></h2><h2 id="x">'),
        # a repeated attribute
        page().replace("<h2>", '<h2 class="a" class="b">'),
    ],
)
def test_malformed_or_unclosed_markup_fails_the_build_never_skips(
    broken: str,
) -> None:
    with pytest.raises(SupportPageError):
        check_support_html(broken, [ADDR])


def test_a_copy_button_must_point_at_the_one_address_in_its_own_card() -> None:
    other = ADDRESS_ELEMENT.replace("a-1", "a-2")
    two_cards = ADDRESS_ELEMENT + other.replace(
        'data-copy-target="a-2"', 'data-copy-target="a-1"'
    )
    with pytest.raises(SupportPageError, match="another card"):
        check_support_html(page(address=two_cards), [ADDR, ADDR])
    with pytest.raises(SupportPageError, match="does not point"):
        check_support_html(
            page(address=ADDRESS_ELEMENT.replace('target="a-1"', 'target="zz"')),
            [ADDR],
        )
    with pytest.raises(SupportPageError, match="does not match"):
        check_support_html(page(address=ADDRESS_ELEMENT), ["other-address"])
    with pytest.raises(SupportPageError, match="does not match"):
        check_support_html(
            page(address=ADDRESS_ELEMENT.replace("ADDR", "ADDR ")), [ADDR]
        )
    with pytest.raises(SupportPageError, match="more than once"):
        check_support_html(page(main='<nav id="a-1"></nav>'), [ADDR])


def test_a_duplicate_id_in_the_stock_page_cannot_redirect_the_copy_button() -> None:
    dupe = ADDRESS_ELEMENT.replace(
        "</div></li></ul>", '<span id="a-1"></span></div></li></ul>'
    )
    with pytest.raises(SupportPageError, match="more than once"):
        check_support_html(page(address=dupe), [ADDR])


def test_copy_script_reads_the_address_in_its_own_card_not_by_id() -> None:
    source = (ROOT / "src" / "reach_around" / "site" / "static" / "copy.js").read_text(
        "utf-8"
    )
    assert "getElementById" not in source
    assert 'closest(".crypto-card")' in source
    assert 'querySelector("code.crypto-address")' in source


# a fuzz of generated, often malformed, nesting
_TOKENS = [
    "<p>", "</p>", "<div>", "</div>", "<span>", "</span>", "<ul>", "</ul>",
    "<li>", "</li>", "<br>", "<hr>", "text", "<section>", "</section>",
    '<section class="support">', "<a href=\"x\">", "</a>", "<img src=\"x\">",
    "<h2>", "</h2>", "<code>", "</code>", "<b>", "</b>", "<!-- c -->",
]  # fmt: skip
_ALLOWED_INSIDE = {"p", "div", "span", "ul", "li", "br", "hr", "h2", "code"}


def test_a_fuzz_of_malformed_nesting_passes_only_when_wholly_balanced() -> None:
    import random

    rng = random.Random(683)  # noqa: S311 - a fixed seed, not security
    agreed_ok = agreed_bad = 0
    for _ in range(400):
        tokens = [rng.choice(_TOKENS) for _ in range(rng.randint(1, 9))]
        mid = "".join(tokens)
        html = (
            '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>'
            f'<main><section class="support">{mid}</section></main></body></html>'
        )
        try:
            check_support_html(html)
            ok = True
        except SupportPageError:
            ok = False
        if ok:
            # whatever passed is balanced and holds only allowed content
            stack: list[str] = []
            for tok in tokens:
                if tok.startswith("</"):
                    assert stack and stack.pop() == tok[2:-1], tokens
                elif tok.startswith("<") and not tok.startswith("<!--"):
                    name = tok[1:].split(" ", 1)[0].rstrip(">")
                    assert name in _ALLOWED_INSIDE, tokens
                    if name not in ("br", "hr"):
                        stack.append(name)
            assert not stack, tokens
            agreed_ok += 1
        else:
            agreed_bad += 1
    assert agreed_ok > 5 and agreed_bad > 100


def test_owner_custom_css_is_not_loaded_on_the_support_page(tmp_path: Path) -> None:
    overrides = tmp_path / "overrides"
    overrides.mkdir()
    (overrides / "custom.css").write_text("body{color:red}\n", encoding="utf-8")
    out = tmp_path / "out"
    build_site(
        SiteConfig.model_validate(data(SUPPORT)),
        out,
        today=TODAY,
        assets_dir=ASSETS,
        overrides=overrides,
    )
    assert "custom." in (out / "index.html").read_text(encoding="utf-8")
    assert "custom." not in (out / "support" / "index.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("path", ["/support/", "/support/index.html", "/support/*"])
def test_every_support_path_gets_the_image_policy_for_this_site_only(
    tmp_path: Path, path: str
) -> None:
    build(data(SUPPORT), tmp_path)
    headers = (tmp_path / "_headers").read_text(encoding="utf-8")
    block = headers[headers.index(f"\n{path}\n") :].split("\n\n", 1)[0]
    csp = block.split("Content-Security-Policy: ", 1)[1].split("\n", 1)[0]
    assert "img-src 'self';" in csp
    assert "https:" not in csp.split("img-src", 1)[1].split(";", 1)[0]
    assert "default-src 'self'" in csp
    assert "connect-src 'self'" in csp
    site_wide = headers.split("\n\n", 1)[0]
    assert "img-src 'self' https:" in site_wide


def test_no_support_policy_without_a_support_page(tmp_path: Path) -> None:
    build(data(None), tmp_path)
    assert "/support/" not in (tmp_path / "_headers").read_text(encoding="utf-8")


def _instance_doc() -> dict[str, Any]:
    import yaml

    doc: dict[str, Any] = yaml.safe_load(
        (ASSETS / "site.yaml").read_text(encoding="utf-8")
    )
    doc["support"] = SUPPORT
    doc["tabs"] = [t for t in doc["tabs"] if t["id"] != "support"]
    return doc


def test_the_stock_page_passes_the_check_with_every_piece_of_chrome(
    tmp_path: Path,
) -> None:
    doc = _instance_doc()
    doc["profile_card"] = {
        "tags": ["one"],
        "button": {"label": "Kit", "url": "https://e.com/"},
    }
    doc["site_nav"] = {"items": [{"title": "Links", "url": "https://e.com/"}]}
    doc["analytics"] = {"page_views": True}
    doc["page_layout"] = "sections"
    build(doc, tmp_path / "a", ASSETS)
    doc["page_layout"] = "tabs"
    doc["features"] = {"click_tracking": True}
    out = build(doc, tmp_path / "b", ASSETS)
    assert "support/index.html" in out


# --- the address element is read the way the copy script reads it ----------


def test_an_address_element_takes_no_child_element() -> None:
    child = ADDRESS_ELEMENT.replace("ADDR</code>", "ADDR<span>x</span></code>")
    with pytest.raises(SupportPageError, match="inside an address element"):
        check_support_html(page(address=child), [ADDR])
    comment = ADDRESS_ELEMENT.replace("ADDR</code>", "ADDR<b></b></code>")
    with pytest.raises(SupportPageError, match="inside an address element"):
        check_support_html(page(address=comment), [ADDR])


def test_the_full_decoded_text_is_compared_with_the_address() -> None:
    # "&#65;" is "A": the text a browser reads is what is compared
    entity = ADDRESS_ELEMENT.replace("ADDR", "&#65;a1")
    check_support_html(page(address=entity), ["Aa1"])
    with pytest.raises(SupportPageError, match="does not match"):
        check_support_html(page(address=entity), ["&#65;a1"])
    split = ADDRESS_ELEMENT.replace("ADDR", "Aa<!-- c -->1")
    with pytest.raises(SupportPageError, match="comment"):
        check_support_html(page(address=split), ["Aa1"])


def test_the_address_class_is_only_for_code_and_one_per_card() -> None:
    span_first = ADDRESS_ELEMENT.replace(
        '<code class="crypto-address"',
        '<span class="crypto-address">decoy</span><code class="crypto-address"',
    )
    with pytest.raises(SupportPageError, match="other than <code>"):
        check_support_html(page(address=span_first), [ADDR])
    two_codes = ADDRESS_ELEMENT.replace(
        "</code>", '</code><code class="crypto-address" id="a-2" dir="ltr">ADDR</code>'
    )
    with pytest.raises(SupportPageError, match="one address element"):
        check_support_html(page(address=two_codes), [ADDR, ADDR])
    for outside in (
        '<nav><span class="crypto-address">x</span></nav>',
        '<nav class="crypto-address"></nav>',
    ):
        with pytest.raises(SupportPageError):
            check_support_html(page(main=outside), [ADDR])


# --- the chrome outside the support section is held to an allowlist --------

COOKIE = (
    '<section class="cookie-banner" aria-label="Cookie notice"><p>Notice</p>'
    '<form method="get" action="../privacy/#cookies">'
    '<button type="submit" name="cookies" value="accept">Accept</button>'
    "</form></section>"
)


def test_the_stock_chrome_shapes_are_allowed() -> None:
    avatar = '<img class="avatar" alt="" src="../media/a.png" width="56" height="56">'
    chrome_nav = (
        '<nav aria-label="Pages" class="tabs"><ul><li>'
        '<a href="../more/" aria-current="page">More</a>'
        "<span data-live-badge hidden>LIVE</span></li></ul></nav>"
        '<nav><a href="https://e.com/k" rel="noopener noreferrer">K</a></nav>'
    )
    profile = (
        '<section class="profile-card" aria-labelledby="t"><h2 id="t">Name</h2>'
        '<ul class="profile-tags"><li class="profile-tag">one</li></ul>'
        '<a class="profile-button" id="m" href="https://e.com/kit/" '
        'ping="/e/c/m" data-link-id="m">Kit</a></section>'
    )
    check_support_html(
        page(
            main=chrome_nav + profile + COOKIE,
            body="",
        ).replace(
            '<header><a href="/">Home</a></header>',
            f'<header class="site-header">{avatar}<h1>Name</h1></header>',
        ),
        [ADDR],
        {"https://e.com/k", "https://e.com/kit/"},
    )
    in_footer = page().replace(
        '<footer><a href="/privacy/">Privacy</a></footer>',
        f'<footer><p><a href="../privacy/">Privacy</a></p>{COOKIE}</footer>',
    )
    check_support_html(in_footer, [ADDR])


@pytest.mark.parametrize(
    "main",
    [
        '<nav><a href="https://e.com/x">x</a></nav>',  # not a configured URL
        '<nav><a href="javascript:alert(1)">x</a></nav>',
        '<nav><a href="//e.com/x">x</a></nav>',
        '<nav><a href="data:text/html,x">x</a></nav>',
        '<nav><a href="/x" target="_blank">x</a></nav>',
        '<nav><a href="/x" ping="https://e.com/p">x</a></nav>',
        "<nav><a>no href</a></nav>",
        '<nav><img src="/a.png"><img src="/b.png"></nav>',
        '<nav><img src="https://e.com/a.png"></nav>',
        '<nav><img src="/a.png" onerror="x()"></nav>',
        "<nav><picture></picture></nav>",
        "<nav><source src='x'></nav>",
        "<nav><input></nav>",
        "<nav><button type='button'>b</button></nav>",
        '<nav><form method="post" action="/x"></form></nav>',
        '<section class="cookie-banner"><form method="post" action="/x"></form></section>',
        '<section class="cookie-banner"><form method="get" action="https://e.com/"></form></section>',
        '<section class="cookie-banner"><form method="get" action="/x"><button type="button">b</button></form></section>',
        '<section class="cookie-banner"><button type="submit">b</button></section>',
        "<nav><meta charset='x'></nav>",
        "<nav><link rel='stylesheet' href='x'></nav>",
        "<nav><base href='x'></nav>",
        '<nav data-x="1"></nav>',
        "<nav hidden></nav>",
        "<nav><table></table></nav>",
        "<nav><font></font></nav>",
        "<nav><span hidden data-other></span></nav>",
        '<section class="support" id="x"></section>',
        '<nav><section class="support"></section></nav>',
        '<nav><form method="get" action="/x"></form></nav>',
    ],
)
def test_the_chrome_allowlist_refuses_anything_else(main: str) -> None:
    with pytest.raises(SupportPageError):
        check_support_html(page(main=main), [ADDR])


def test_the_chrome_allowlist_applies_in_the_footer_and_header_too() -> None:
    bad = '<img src="https://e.com/x.png">'
    for where in ("footer", "header"):
        html = page().replace(f"<{where}>", f"<{where}>{bad}", 1)
        with pytest.raises(SupportPageError):
            check_support_html(html, [ADDR])


def test_the_skip_link_is_one_in_page_link_with_only_text() -> None:
    ok = page()
    check_support_html(ok, [ADDR])
    for bad in (
        ok.replace("Skip</a>", "<span>Skip</span></a>"),
        ok.replace('href="#content"', 'href="/other"'),
        ok.replace(
            "</a><main",
            '</a><a class="skip-link" href="#content">Skip</a><main',
        ),
        ok.replace('class="skip-link"', 'class="skip-link" ping="/x"'),
    ):
        with pytest.raises(SupportPageError):
            check_support_html(bad, [ADDR])


def test_the_build_passes_urls_taken_from_the_config(tmp_path: Path) -> None:
    doc = _instance_doc()
    doc["site_nav"] = {"items": [{"title": "Links", "url": "https://links.test/"}]}
    doc["profile_card"] = {
        "tags": ["one"],
        "button": {"label": "Kit", "url": "https://kit.test/"},
    }
    build(doc, tmp_path, ASSETS)


# --- the page is rendered from packaged templates only ----------------------

TOKEN = "Zq" * 14  # an obviously fake 28-character token


def _overrides(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "overrides"
    for name, text in files.items():
        target = root / "templates" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def _build_with(overrides: Path, out: Path, raw: dict[str, Any]) -> dict[str, str]:
    build_site(
        SiteConfig.model_validate(raw),
        out,
        today=TODAY,
        assets_dir=ASSETS,
        overrides=overrides,
    )
    return {
        p.relative_to(out).as_posix(): p.read_text(encoding="utf-8")
        for p in out.rglob("*.html")
    }


@pytest.mark.parametrize(
    "name",
    [
        "support.html",
        "blocks/crypto.html",
        "blocks/text.html",
        "base.html",
        "_header.html",
        "_footer.html",
        "_nav.html",
    ],
)
def test_owner_template_overrides_are_not_read_for_the_support_page(
    tmp_path: Path, name: str
) -> None:
    raw = data({**SUPPORT, "blocks": [crypto_block(), {"id": "t", "markdown": "Hi."}]})
    base = build(raw, tmp_path / "plain")["support/index.html"]
    root = _overrides(
        tmp_path, {name: "OVERRIDE-MARKER {% block content %}{% endblock %}"}
    )
    pages = _build_with(root, tmp_path / "over", raw)
    assert pages["support/index.html"] == base
    assert "OVERRIDE-MARKER" not in pages["support/index.html"]


def _packaged_env_with(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, files: dict[str, str]
) -> None:
    """Make the support page load the given templates, as if the engine's own
    template had a defect (the owner path is closed; this proves the final
    check on the built page)."""
    from reach_around.site import build as build_module
    from reach_around.site.overrides import make_environment

    root = _overrides(tmp_path, files)

    def patched(env: Any) -> Any:
        made = make_environment(root)
        made.globals.update(env.globals)
        return made

    monkeypatch.setattr(build_module, "_support_environment", patched)


STOCK_SUPPORT = (
    ROOT / "src" / "reach_around" / "site" / "templates" / "support.html"
).read_text("utf-8")
STOCK_CRYPTO = (
    ROOT / "src" / "reach_around" / "site" / "templates" / "blocks" / "crypto.html"
).read_text("utf-8")
STOCK_HEADER = (
    ROOT / "src" / "reach_around" / "site" / "templates" / "_header_variants.html"
).read_text("utf-8")


def _refused(tmp_path: Path, raw: dict[str, Any], match: str) -> None:
    with pytest.raises(SupportPageError, match=match):
        build(raw, tmp_path / "out")


@pytest.mark.parametrize(
    ("file", "edit"),
    [
        (
            "support.html",
            lambda t: t.replace("</section>", f"<p>{TOKEN}</p></section>", 1),
        ),
        ("support.html", lambda t: t.replace("<h2>", f'<h2 title="{TOKEN}">', 1)),
        ("support.html", lambda t: t.replace("<h2>", f'<h2 aria-label="{TOKEN}">', 1)),
        (
            "blocks/crypto.html",
            lambda t: t.replace(
                '<p class="crypto-note">', f'<p class="crypto-note">{TOKEN} ', 1
            ),
        ),
        (
            "blocks/crypto.html",
            lambda t: t.replace(
                '<p class="crypto-note">', f'<p class="crypto-note" title="{TOKEN}">', 1
            ),
        ),
    ],
)
def test_the_final_text_check_refuses_an_address_like_token_in_the_support_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file: str, edit: Any
) -> None:
    stock = STOCK_SUPPORT if file == "support.html" else STOCK_CRYPTO
    new = edit(stock)
    assert new != stock
    _packaged_env_with(monkeypatch, tmp_path, {file: new})
    _refused(tmp_path, data(SUPPORT), "looks like an address")


@pytest.mark.parametrize(
    ("file", "text"),
    [
        ("_footer.html", f"<footer><p>{TOKEN}</p></footer>"),
        ("_footer.html", f'<footer><p title="{TOKEN}">x</p></footer>'),
        ("_nav.html", f'<nav aria-label="{TOKEN}"></nav>'),
        ("_nav.html", f"<nav><ul><li>{TOKEN}</li></ul></nav>"),
        ("_header.html", f"<header><h1>{TOKEN}</h1></header>"),
        (
            "_header.html",
            f'<header><img class="avatar" src="/a.png" alt="{TOKEN}"></header>',
        ),
    ],
)
def test_the_final_text_check_refuses_an_address_like_token_in_the_chrome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file: str, text: str
) -> None:
    _packaged_env_with(monkeypatch, tmp_path, {file: text})
    _refused(tmp_path, data(SUPPORT), "looks like an address|not allowed")


def test_the_skip_link_and_page_title_text_are_read_too() -> None:
    with pytest.raises(SupportPageError, match="looks like an address"):
        check_support_html(page().replace("Skip</a>", f"{TOKEN}</a>"), [ADDR])
    with pytest.raises(SupportPageError, match="looks like an address"):
        check_support_html(
            page().replace("<title>T</title>", f"<title>{TOKEN}</title>"), [ADDR]
        )
    for attr in ("alt", "title", "aria-label", "value"):
        html = page().replace("<header>", f'<header><span {attr}="{TOKEN}"></span>', 1)
        with pytest.raises(SupportPageError):
            check_support_html(html, [ADDR])


def test_text_is_read_joined_across_inline_tags_and_split_at_block_tags() -> None:
    half = "Zq" * 7
    joined = page(inner=f"<p>{half}<span>{half}</span></p>")
    with pytest.raises(SupportPageError, match="looks like an address"):
        check_support_html(joined, [ADDR])
    split = page(inner=f"<p>{half}</p><p>{half}</p>")
    check_support_html(split, [ADDR])


def test_an_entity_split_address_in_a_text_block_is_shown_literally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # runs of at most ten characters, so the config accepts it; the page must
    # show the entities as typed (escaped), never as the address they spell
    sneaky = "&#98;c1qexamp&#108;eexamp&#108;eexamp&#108;eexamp&#108;eexam"
    raw = data({**SUPPORT, "blocks": [crypto_block(), {"id": "t", "markdown": sneaky}]})
    html = build(raw, tmp_path / "stock")["support/index.html"]
    assert "&amp;#98;c1qexamp" in html
    unsafe = STOCK_SUPPORT.replace("{{ line }}", "{{ line|safe }}")
    assert unsafe != STOCK_SUPPORT
    _packaged_env_with(monkeypatch, tmp_path, {"support.html": unsafe})
    _refused(tmp_path, raw, "looks like an address")


def test_the_build_passes_the_configured_addresses_to_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changed = STOCK_CRYPTO.replace("{{ entry.address }}", "{{ entry.address }}x")
    assert changed != STOCK_CRYPTO
    _packaged_env_with(monkeypatch, tmp_path, {"blocks/crypto.html": changed})
    _refused(tmp_path, data(SUPPORT), "does not match")


# --- parser differences: no comment, declaration or bare "<" anywhere -------


@pytest.mark.parametrize(
    "bad",
    [
        "<!-->",
        "<!--->",
        "<!-- c -->",
        "<!---->",
        "<![CDATA[x]]>",
        "<!ELEMENT x>",
        "<?php x ?>",
        "<?x>",
        "</ x>",
        "</>",
        "<1>",
        "< p>",
    ],
)
@pytest.mark.parametrize("where", ["head", "body", "main", "inner"])
def test_markup_that_parsers_read_differently_is_refused_everywhere(
    where: str, bad: str
) -> None:
    html = page(**{where: bad})
    with pytest.raises(SupportPageError):
        check_support_html(html, [ADDR])


@pytest.mark.parametrize(
    "html",
    [
        page().replace("<!DOCTYPE html>", "<!doctype html>"),
        page().replace("<!DOCTYPE html>", ""),
        page().replace("<!DOCTYPE html>", "<!DOCTYPE HTML PUBLIC 'x'>"),
        " " + page(),
    ],
)
def test_a_missing_or_non_standard_doctype_is_refused(html: str) -> None:
    with pytest.raises(SupportPageError):
        check_support_html(html, [ADDR])


# --- each remaining guard on its own ----------------------------------------


@pytest.mark.parametrize(
    "html",
    [
        page().replace("<html ", '<html data-x="1" '),
        page().replace("<body>", '<body data-x="1">'),
        page().replace('<main id="content">', '<main id="content" data-x="1">'),
        page().replace(
            "<footer>", '<footer><nav><section class="other"></section></nav>', 1
        ),
        page().replace(
            "<footer>", f"<footer>{COOKIE}{COOKIE.replace('Notice', 'Again')}", 1
        ),
        page().replace("</html>", ""),
        page().replace("</body></html>", "</body>"),
    ],
)
def test_page_level_guards_each_have_their_own_test(html: str) -> None:
    with pytest.raises(SupportPageError):
        check_support_html(html, [ADDR])


def test_the_stock_page_has_no_comment_and_no_odd_markup(tmp_path: Path) -> None:
    pages = build(_instance_doc(), tmp_path, ASSETS)
    assert "<!--" not in pages["support/index.html"]


# --- the head -----------------------------------------------------------------


@pytest.mark.parametrize(
    "head",
    [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="a"><meta name="viewport" content="b">',
        '<meta name="robots" content="a"><meta name="robots" content="b">',
        '<meta charset="utf-8" name="viewport" content="x">',
        '<meta name="viewport">',
        '<link rel="icon" href="https://e.com/i.png">',
        '<link rel="icon" href="//e.com/i.png">',
        '<link rel="preload" href="https://e.com/f.woff2" as="font">',
        '<link rel="preload" href="/f.woff2" as="image">',
        '<link rel="stylesheet" href="../static/site.0123456789abcdef0123.css" media="print">',
        '<link rel="stylesheet" href="../static/site.0123456789abcdef012.css">',
        '<link rel="stylesheet" href="../static/site.0123456789abcdef0123.css" crossorigin>',
        '<script src="../static/copy.0123456789abcdef0123.js" type="module"></script>',
        '<script src="../static/copy.0123456789abcdef0123.js" async></script>',
        '<script src="../static/copy.0123456789abcdef0123.js" integrity="x"></script>',
        "<title>second</title>",
    ],
)
def test_head_guards(head: str) -> None:
    with pytest.raises(SupportPageError):
        check_support_html(page(head=head), [ADDR])


def test_a_site_path_icon_and_a_configured_favicon_are_allowed() -> None:
    check_support_html(
        page(
            head='<link rel="icon" href="../static/favicon.0123456789abcdef0123.svg" type="image/svg+xml">'
        ),
        [ADDR],
    )
    check_support_html(
        page(head='<link rel="icon" href="https://e.com/i.png">'),
        [ADDR],
        {"https://e.com/i.png"},
    )


@pytest.mark.parametrize(
    "edit",
    [
        lambda h: h.replace('dir="ltr"', 'dir="rtl"'),
        lambda h: h.replace(' dir="ltr"', ""),
        lambda h: h.replace('class="crypto-address"', 'class="crypto-address extra"'),
        lambda h: h.replace('id="a-1" dir', 'id="a-1" title="x" dir'),
        lambda h: h.replace('id="a-1" dir', 'id="a-1" aria-hidden="true" dir'),
    ],
)
def test_the_address_element_is_exactly_the_stock_shape(edit: Any) -> None:
    with pytest.raises(SupportPageError):
        check_support_html(page(address=edit(ADDRESS_ELEMENT)), [ADDR])
