# SPDX-License-Identifier: Apache-2.0
import re
import time
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import SiteConfig
from reach_around.config.blocks import Divider, Image, Text
from reach_around.config.models import check_https_url
from reach_around.site.build import build_site
from reach_around.site.markdown import MAX_MARKDOWN_CHARS, render_markdown
from reach_around.site.visibility import visible_blocks

TODAY = date(2027, 3, 10)
GOLDEN = Path(__file__).parent / "golden_blocks"
LINKS: list[dict[str, Any]] = [
    {"id": "one", "title": "One", "url": "https://example.com/1"},
    {"id": "two", "title": "Two", "url": "https://example.com/2"},
]
LINK_X = {"id": "x", "title": "L", "url": "https://e.com/"}


def config(
    blocks: list[dict[str, Any]] | None = None,
    *,
    header: str | None = None,
    layout: str | None = None,
) -> SiteConfig:
    creator: dict[str, Any] = {"name": "Example Creator"}
    if header is not None:
        creator["header"] = header
    tab: dict[str, Any] = {
        "id": "start",
        "title": "Start",
        "blocks": LINKS if blocks is None else blocks,
    }
    if layout is not None:
        tab["layout"] = layout
    return SiteConfig.model_validate({"version": 1, "creator": creator, "tabs": [tab]})


_CLICK_SCRIPT = re.compile(r'<script src="(?:\.\./)?static/click\.js" defer></script>')


def without_click_script(text: str) -> str:
    """Drop the one allowed tag (external, deferred click.js)."""
    return _CLICK_SCRIPT.sub("", text)


def make_assets(root: Path) -> Path:
    """An instance folder holding the image files the test configs name."""
    for name in ("img/a.png", "img/b.png"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(b"\x89PNG synthetic")
    return root


def built(cfg: SiteConfig, out: Path) -> str:
    build_site(cfg, out, today=TODAY, assets_dir=make_assets(out.parent / "inst"))
    return (out / "index.html").read_text(encoding="utf-8")


def compare_golden(actual: str, path: Path, update: bool) -> None:
    if update:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
    assert path.is_file(), "no golden file; run pytest --update-golden"
    assert actual == path.read_text(encoding="utf-8")


# --- markdown ---------------------------------------------------------------


def test_markdown_escapes_raw_html() -> None:
    out = render_markdown('<script>alert(1)</script> <img src=x onerror="alert(1)">')
    assert "<script" not in out
    assert "<img" not in out
    assert "&lt;script&gt;" in out
    assert "&lt;img" in out


def test_markdown_supported_syntax() -> None:
    out = render_markdown("**bold** and *italic*\nnext line\n\n- one\n- two")
    assert "<strong>bold</strong>" in out
    assert "<em>italic</em>" in out
    assert "<br>" in out
    assert out.count("<li>") == 2
    assert "<ul>" in out


def test_markdown_https_link_renders() -> None:
    out = render_markdown("see [the site](https://example.com/a?x=1&y=2)")
    assert 'href="https://example.com/a?x=1&amp;y=2"' in out
    assert 'rel="noopener noreferrer' in out


@pytest.mark.parametrize(
    "target",
    ["http://example.com/", "ftp://example.com/", "//example.com/", "/rel", "x"],
)
def test_markdown_links_must_be_https(target: str) -> None:
    out = render_markdown(f"[bad]({target})")
    assert "<a " not in out
    assert "href" not in out


@pytest.mark.parametrize(
    "target",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        " javascript:alert(1)",
    ],
)
def test_markdown_javascript_and_data_links_are_not_links(target: str) -> None:
    out = render_markdown(f"[click]({target})")
    assert "<a " not in out
    assert "href=" not in out
    assert "<script" not in out


def test_markdown_cannot_inject_an_attribute_through_a_link_title() -> None:
    for text in (
        '[a](https://example.com/ "title" onmouseover="alert(1)")',
        '[a](https://example.com/" onmouseover="alert(1))',
        "[a](https://example.com/' onmouseover='alert(1))",
        '[a" onclick="x](https://example.com/)',
    ):
        out = render_markdown(text)
        assert not re.search(r"<[^>]*\son\w+=", out), out
        for tag in re.findall(r"<a [^>]*>", out):
            assert tag.count('"') == 4, tag  # only href and rel


def test_markdown_emphasis_cannot_break_out_of_a_url() -> None:
    out = render_markdown("[a](https://example.com/*x*y*)")
    assert 'href="https://example.com/*x*y*"' in out
    assert "<em>" not in out


# --- markdown: limits, shared URL validation, well-formedness ----------------


def test_render_markdown_rejects_input_over_the_cap() -> None:
    assert MAX_MARKDOWN_CHARS == 5000
    render_markdown("a" * MAX_MARKDOWN_CHARS)
    with pytest.raises(ValueError):
        render_markdown("a" * (MAX_MARKDOWN_CHARS + 1))


@pytest.mark.parametrize(
    "text",
    ["*a " * 1666, "*" * 5000, "[" * 2500, "_" * 5000, "[a](" * 1250, "**a " * 1250],
)
def test_render_markdown_is_fast_on_hostile_input(text: str) -> None:
    start = time.perf_counter()
    render_markdown(text)
    assert time.perf_counter() - start < 0.2


def test_uppercase_scheme_link_renders_with_lowercase_scheme() -> None:
    out = render_markdown("[x](HTTPS://example.com/Path)")
    assert 'href="https://example.com/Path"' in out


def test_parentheses_in_a_valid_url_render_as_a_link() -> None:
    out = render_markdown("see [x](https://example.com/a(b)) now")
    assert 'href="https://example.com/a(b)"' in out
    assert out.count("</a>") == 1
    assert "now" in out.split("</a>")[1]


URL_TABLE = [
    "https://example.com/",
    "HTTPS://example.com/",
    "https://example.com/a(b)",
    "https://example.com/a(b(c))d",
    "https://example.com/?q=1&r=2",
    'https://example.com/"quoted"',
    "https://example.com:8443/x",
    "https://example.com/" + "a" * 2028,  # exactly 2048 characters
    "https://example.com/" + "a" * 2029,  # 2049 characters
    "http://example.com/",
    "javascript:alert(1)",
    "JAVASCRIPT:alert(1)",
    "data:text/html,hi",
    "//example.com/",
    "/relative",
    "https://user:pw@example.com/",
    "https://user@example.com/",
    "https:///nohost",
    "https:example.com",
    "https://example.com/\x00",
    "https://example.com/\x07bell",
    "https://example.com/ x",
    "https://example.com:notaport/",
]


@pytest.mark.parametrize("url", URL_TABLE)
def test_any_url_the_model_accepts_the_renderer_links_else_text(
    url: str,
) -> None:
    try:
        check_https_url(url)
        accepted = True
    except ValueError:
        accepted = False
    out = render_markdown(f"[x]({url})")
    assert ("<a " in out) is accepted, out
    if accepted:
        assert 'href="https://' in out
    else:
        assert "href" not in out


def test_codex_probe_html_in_a_link_label_is_escaped() -> None:
    out = render_markdown("[<img src=x onerror=alert(1)>](https://example.com/)")
    assert "<img" not in out
    assert "&lt;img" in out
    assert 'href="https://example.com/"' in out
    assert not re.search(r"<[^>]*\son\w+=", out)


def test_codex_probe_tab_in_a_link_target_is_not_a_link() -> None:
    out = render_markdown("[x](https://example.com/\tjavascript:foo)")
    assert "<a " not in out
    assert "href" not in out


def test_codex_probe_protocol_relative_link_is_not_a_link() -> None:
    out = render_markdown("[x](//example.com/)")
    assert "<a " not in out
    assert "href" not in out


class _Balance(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.ok = True

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag != "br":
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack.pop() != tag:
            self.ok = False


@pytest.mark.parametrize(
    "text",
    [
        "***<script>***",
        "***a** b*",
        "***a* b**",
        "*a **b* c**",
        "**a *b** c*",
        "*a [b*](https://example.com/) c*",
        "**a [b**](https://example.com/)",
        "[*a](https://example.com/) b*",
        "****",
        "*** a ***",
        "**a**b**c",
        "*a*b*c*d*",
        "- *a **b*\n- c** d*",
        "*a\n\n*b*",
        "_*a_*",
    ],
)
def test_emphasis_output_is_always_well_formed(text: str) -> None:
    out = render_markdown(text)
    parser = _Balance()
    parser.feed(out)
    parser.close()
    assert parser.ok and not parser.stack, out


def test_triple_asterisk_renders_nested_emphasis() -> None:
    out = render_markdown("***x***")
    assert "<strong>" in out and "<em>" in out and "x" in out


def test_text_template_has_no_autoescape_off_block() -> None:
    import reach_around.site as site_pkg

    path = Path(site_pkg.__file__).parent / "templates" / "blocks" / "text.html"
    source = path.read_text(encoding="utf-8")
    assert "block.markdown|markdown" in source.replace(" ", "")
    assert "autoescape" not in source


def test_render_markdown_returns_markup() -> None:
    from markupsafe import Markup

    assert isinstance(render_markdown("x"), Markup)


# --- models -----------------------------------------------------------------


def test_image_requires_alt_text() -> None:
    with pytest.raises(ValidationError):
        Image.model_validate({"id": "pic", "src": "https://example.com/a.png"})
    with pytest.raises(ValidationError):
        Image.model_validate(
            {"id": "pic", "src": "https://example.com/a.png", "alt": ""}
        )


@pytest.mark.parametrize(
    "src",
    [
        "http://example.com/a.png",
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "//example.com/a.png",
        "/etc/passwd",
        "../a.png",
        "img/../../a.png",
        "img\\a.png",
        "img/a b.png",
        "https://user:pw@example.com/a.png",
        "",
    ],
)
def test_image_src_is_https_or_relative_asset_refused(src: str) -> None:
    with pytest.raises(ValidationError):
        Image.model_validate({"id": "pic", "src": src, "alt": "A picture"})


@pytest.mark.parametrize("src", ["https://example.com/a.png", "img/a.png", "a.png"])
def test_image_src_is_https_or_relative_asset(src: str) -> None:
    assert Image.model_validate({"id": "pic", "src": src, "alt": "x"}).src == src


def test_image_link_must_be_https() -> None:
    with pytest.raises(ValidationError):
        Image.model_validate(
            {"id": "p", "src": "a.png", "alt": "x", "link": "javascript:alert(1)"}
        )


def test_image_alt_is_escaped(tmp_path: Path) -> None:
    alt = '"><script>alert(1)</script>'
    cfg = config([{"id": "pic", "src": "https://example.com/a.png", "alt": alt}])
    text = built(cfg, tmp_path / "out")
    assert "<script" not in without_click_script(text)
    assert "&lt;script&gt;" in text
    assert 'alt="&#34;&gt;' in text


def test_relative_image_is_resolved_against_the_page_root(tmp_path: Path) -> None:
    cfg = SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "C"},
            "tabs": [
                {"id": "a", "title": "A", "blocks": LINKS},
                {
                    "id": "b",
                    "title": "B",
                    "blocks": [{"id": "pic", "src": "img/a.png", "alt": "x"}],
                },
            ],
        }
    )
    build_site(cfg, tmp_path / "out", today=TODAY, assets_dir=make_assets(tmp_path))
    page = (tmp_path / "out" / "b" / "index.html").read_text()
    assert 'src="../img/a.png"' in page


def test_unknown_layout_or_header_is_refused() -> None:
    with pytest.raises(ValidationError):
        config(layout="masonry")
    with pytest.raises(ValidationError):
        config(header="giant")
    section = {"id": "s", "title": "S", "layout": "x", "links": LINKS}
    with pytest.raises(ValidationError):
        config([section])


def test_block_ids_are_unique_across_new_and_old_block_types() -> None:
    section = {"id": "s", "title": "S", "links": [LINK_X]}
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = [
        ({"id": "x", "markdown": "hi"}, LINK_X),
        ({"id": "x", "src": "a.png", "alt": "a"}, {"id": "x"}),
        ({"id": "x"}, {"id": "x", "markdown": "hi"}),
        ({"id": "x", "markdown": "hi"}, section),
        ({"id": "x"}, {"id": "s2", "title": "S", "links": [LINK_X]}),
    ]
    for first, second in pairs:
        with pytest.raises(ValidationError, match="duplicate id"):
            config([first, second])


def test_block_types_are_chosen_by_shape() -> None:
    cfg = config(
        [
            {"id": "t", "markdown": "hi"},
            {"id": "i", "src": "a.png", "alt": "a"},
            {"id": "d"},
        ]
    )
    assert [type(b) for b in cfg.tabs[0].blocks] == [Text, Image, Divider]


def test_unknown_key_on_a_new_block_is_refused() -> None:
    with pytest.raises(ValidationError):
        config([{"id": "t", "markdown": "hi", "html": "<b>x</b>"}])


def test_visibility_keeps_dateless_blocks() -> None:
    cfg = config(
        [
            {"id": "t", "markdown": "hi"},
            {"id": "i", "src": "a.png", "alt": "a"},
            {"id": "d"},
        ]
    )
    blocks = cfg.tabs[0].blocks
    assert visible_blocks(blocks, TODAY) == blocks
    assert visible_blocks(blocks, date(2000, 1, 1)) == blocks


# --- rendering --------------------------------------------------------------

NEW_BLOCKS: list[dict[str, Any]] = [
    {"id": "intro", "markdown": "Hello **world**\n\n- [site](https://example.com/)"},
    {"id": "pic", "src": "https://example.com/a.png", "alt": "A picture"},
    {
        "id": "pic2",
        "src": "img/b.png",
        "alt": "Linked",
        "link": "https://example.com/b",
    },
    {"id": "rule"},
    *LINKS,
]


def test_new_blocks_render_without_javascript(tmp_path: Path) -> None:
    text = built(config(NEW_BLOCKS), tmp_path / "out")
    assert "<strong>world</strong>" in text
    assert '<img class="image"' in text
    assert "<hr" in text
    assert '<a class="image-link" href="https://example.com/b"' in text
    assert "<script" not in without_click_script(text)


def test_text_block_html_is_escaped_in_the_page(tmp_path: Path) -> None:
    cfg = config([{"id": "t", "markdown": "<script>alert(1)</script>"}])
    text = built(cfg, tmp_path / "out")
    assert "<script" not in without_click_script(text)
    assert "&lt;script&gt;" in text


def hrefs(text: str) -> list[str]:
    return re.findall(r'<a class="link" href="([^"]+)"', text)


def test_grid_and_cards_layouts_render_the_same_links_as_list(tmp_path: Path) -> None:
    pages = {
        name: built(config(layout=name), tmp_path / name)
        for name in ("list", "grid", "cards")
    }
    assert hrefs(pages["list"]) == ["https://example.com/1", "https://example.com/2"]
    assert hrefs(pages["grid"]) == hrefs(pages["list"])
    assert hrefs(pages["cards"]) == hrefs(pages["list"])
    for name, text in pages.items():
        assert f'class="blocks layout-{name}"' in text
    assert "layout-grid" not in pages["list"]


def test_section_layout_sets_the_container_class(tmp_path: Path) -> None:
    section = {"id": "s", "title": "S", "layout": "cards", "links": LINKS}
    text = built(config([section]), tmp_path / "out")
    assert 'class="section-links layout-cards"' in text
    assert hrefs(text) == ["https://example.com/1", "https://example.com/2"]


def test_default_layout_and_header() -> None:
    cfg = config()
    assert cfg.tabs[0].layout == "list"
    assert cfg.creator.header == "compact"


def test_golden_output_for_each_layout(tmp_path: Path, update_golden: bool) -> None:
    for name in ("list", "grid", "cards"):
        text = built(config(NEW_BLOCKS, layout=name), tmp_path / name)
        compare_golden(text, GOLDEN / f"layout-{name}.html", update_golden)


def test_golden_output_for_each_header_variant(
    tmp_path: Path, update_golden: bool
) -> None:
    for name in ("compact", "centered", "hero"):
        cfg = SiteConfig.model_validate(
            {
                "version": 1,
                "creator": {
                    "name": "Example Creator",
                    "avatar": "https://example.com/avatar.png",
                    "header": name,
                    "socials": [
                        {
                            "name": "Example Video",
                            "url": "https://video.example.com/@example",
                            "icon": "video",
                        }
                    ],
                },
                "tabs": [{"id": "start", "title": "Start", "blocks": LINKS}],
            }
        )
        text = built(cfg, tmp_path / name)
        compare_golden(text, GOLDEN / f"header-{name}.html", update_golden)


def test_header_variants_differ(tmp_path: Path) -> None:
    texts = {
        name: built(config(header=name), tmp_path / name)
        for name in ("compact", "centered", "hero")
    }
    for name, text in texts.items():
        assert f'class="site-header header-{name}"' in text
    assert len(set(texts.values())) == 3


def test_golden_comparison_bites(tmp_path: Path) -> None:
    path = tmp_path / "g.html"
    path.write_text("old", encoding="utf-8")
    with pytest.raises(AssertionError):
        compare_golden("new", path, False)
