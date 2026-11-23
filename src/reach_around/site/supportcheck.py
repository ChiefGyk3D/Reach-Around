# SPDX-License-Identifier: Apache-2.0
"""A strict structural check over the built support page (#683).

The page carries payment instructions, so the finished HTML is read again and
the build fails unless it has exactly the expected shape. The check fails
closed: a page that is not well balanced, or that holds anything it does not
know, is refused rather than partly read.

What it requires:

* every start tag is closed by its own end tag, in order (no stray, mismatched
  or unclosed tag anywhere);
* the ``body`` holds only the skip link and one ``main``; the ``main`` holds
  only the fixed chrome (``nav``, ``header``, ``footer``, the profile card, the
  one "Back to the links" paragraph) and exactly one ``section.support``;
* inside ``section.support`` only a small set of tags and attributes: no link,
  image, frame, embed, form, ``meta``, ``svg``, ``style`` element or attribute,
  event attribute or namespaced attribute, and no comment;
* ``head`` holds only known tags; stylesheets and scripts come from the fixed
  engine set, so no owner stylesheet is linked;
* element ids are unique across the page, and every copy button points at the
  one ``code.crypto-address`` in its own card whose text is exactly a coin
  address from the config, in order.

The header, tab bar, footer and profile card are the site's fixed chrome and
are not looked into.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import NoReturn

_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta"}
    | {"param", "source", "track", "wbr"}
)
# Tags a browser reads differently from html.parser (raw text, other namespaces,
# inert content): refused everywhere, so both read the page the same way.
_NEVER = frozenset(
    {"style", "svg", "math", "base", "template", "noscript", "textarea", "xmp"}
    | {"plaintext", "noembed", "noframes", "select", "iframe", "object", "frameset"}
    | {"frame", "listing", "applet"}
)
# Inside the support section: the tags the stock templates use, and no others.
_SECTION_TAGS = frozenset(
    {"h1", "h2", "p", "br", "hr", "div", "span", "code", "button", "ul", "li"}
)
_SECTION_ATTRS = frozenset(
    {"class", "id", "dir", "lang", "data-copy-target", "type", "aria-label", "title"}
    | {"aria-hidden", "aria-live", "role", "data-copy-status"}
)
# The fixed chrome (everything in <body> outside the support section) is held to
# its own allowlist, built from what the stock templates render.
_CHROME_TAGS = frozenset(
    {"a", "nav", "header", "footer", "section", "h1", "h2", "ul", "li", "p"}
    | {"span", "div", "img", "form", "button", "br"}
)
_CHROME_ATTRS = frozenset(
    {"class", "id", "aria-label", "aria-labelledby", "aria-current", "aria-hidden"}
    | {"role", "lang", "dir", "title"}
)
_CHROME_TAG_ATTRS = {
    "a": frozenset({"href", "rel", "data-link-id", "ping"}),
    "img": frozenset(
        {"src", "alt", "width", "height", "referrerpolicy", "loading", "decoding"}
    ),
    "span": frozenset({"hidden", "data-live-badge"}),
    "form": frozenset({"method", "action"}),
    "button": frozenset({"type", "name", "value"}),
}
# The fixed chrome directly in <main>, and how many of each may appear.
_MAIN_LIMITS = {
    "nav": 2,
    "header": 1,
    "footer": 1,
    "profile-card": 1,
    "cookie-banner": 1,
    "p": 1,
}
_HEAD_TAGS = frozenset({"meta", "title", "link", "script"})
_META_NAMES = frozenset({"viewport", "description", "robots"})
_LINK_RELS = frozenset({"stylesheet", "icon", "preload"})
# The engine stylesheets and scripts a support page may load (fingerprinted
# names ``static/NAME.HASH.EXT``); the owner's custom stylesheet is not among them.
_STYLES = frozenset({"tokens", "site", "theme", "crypto", "frame"})
_SCRIPTS = frozenset({"click", "copy"})
_ASSET = re.compile(r"(?:\.\./)?static/([a-z-]+)\.[0-9a-f]+\.(css|js)")
# A path inside the site: no scheme, no host, no backslash.
_INTERNAL = re.compile(r"(?:\.{0,2}/|#)?[A-Za-z0-9._~/#?=&%+-]*")
_PING = re.compile(r"/e/c/[A-Za-z0-9_-]+")
_BACK_TEXT = "Back to the links"


class SupportPageError(ValueError):
    """The built support page is not what the build allows."""


def _fail(why: str) -> NoReturn:
    raise SupportPageError(f"the support page is not allowed: {why}")


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    parent: _Node | None
    classes: frozenset[str] = frozenset()
    text: list[str] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)


def _inside_support(node: _Node | None) -> bool:
    while node is not None:
        if node.tag == "section" and "support" in node.classes:
            return True
        node = node.parent
    return False


def _card(node: _Node | None) -> _Node | None:
    while node is not None:
        if "crypto-card" in node.classes:
            return node
        node = node.parent
    return None


_BODY_ATTRS = frozenset(
    {"data-page-id", "data-view-route", "data-landing", "data-token-route"}
)


class _Check(HTMLParser):
    def __init__(self, urls: frozenset[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.urls = urls
        self.root = _Node("#root", {}, None)
        self.cur = self.root
        self.ids: dict[str, int] = {}
        self.sections = 0
        self.mains = 0
        self.images = 0
        self.main_counts: dict[str, int] = {}
        self.buttons: list[tuple[str, _Node]] = []
        self.codes: list[_Node] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: (v if v is not None else "") for k, v in attrs}
        if len(values) != len(attrs):
            _fail("a repeated attribute")
        for name in values:
            if name.startswith("on") or ":" in name or name == "style":
                _fail(f"the attribute {name!r}")
        if tag in _NEVER:
            _fail(f"the element <{tag}>")
        parent = self.cur
        node = _Node(tag, values, parent, frozenset(values.get("class", "").split()))
        if "id" in values:
            self.ids[values["id"]] = self.ids.get(values["id"], 0) + 1
        if "crypto-address" in node.classes and tag != "code":
            _fail("the address class on an element other than <code>")
        if "crypto-address" in parent.classes and parent.tag == "code":
            _fail("an element inside an address element")
        if parent.tag == "a" and "skip-link" in parent.classes:
            _fail("an element inside the skip link")
        self._place(parent, node)
        if tag not in _VOID:
            self.cur = node

    def _place(self, parent: _Node, node: _Node) -> None:
        tag = node.tag
        if parent.tag == "#root":
            if tag != "html" or parent.seen:
                _fail("content outside <html>")
            parent.seen.add(tag)
            self._only(node, {"lang"})
        elif parent.tag == "html":
            if tag not in ("head", "body") or tag in parent.seen:
                _fail(f"<{tag}> directly in <html>")
            parent.seen.add(tag)
            self._only(node, _BODY_ATTRS if tag == "body" else set())
        elif parent.tag == "head":
            self._head(node)
        elif parent.tag == "body":
            if tag == "a" and "skip-link" in node.classes:
                if "skip" in parent.seen or not node.attrs.get("href", "").startswith(
                    "#"
                ):
                    _fail("the skip link is not one in-page link")
                parent.seen.add("skip")
                self._only(node, {"class", "href"})
            elif tag == "main" and "main" not in parent.seen:
                parent.seen.add("main")
                self.mains += 1
                self._only(node, {"id", "class"})
            else:
                _fail(f"<{tag}> directly in <body>")
        elif parent.tag == "main":
            self._main_child(node)
        elif parent.tag == "p" and parent.parent is not None:
            if parent.parent.tag == "main" and (tag != "a" or parent.seen):
                _fail("more than a link in the back-link paragraph")
            parent.seen.add(tag)
        if tag in ("title", "script") and parent.tag != "head":
            _fail(f"<{tag}> outside <head>")
        if parent.tag in ("#root", "html", "head"):
            return
        if _inside_support(parent):
            self._support_element(parent, node)
        elif parent.tag != "body" or tag != "main":
            if not (parent.tag == "body" and tag == "a"):
                self._chrome(parent, node)

    def _only(self, node: _Node, allowed: set[str] | frozenset[str]) -> None:
        for name in node.attrs:
            if name not in allowed:
                _fail(f"the attribute {name!r} on <{node.tag}>")

    def _support_element(self, parent: _Node, node: _Node) -> None:
        tag = node.tag
        if tag not in _SECTION_TAGS:
            _fail(f"<{tag}> inside the support content")
        self._only(node, _SECTION_ATTRS)
        if tag == "button":
            self.buttons.append((node.attrs.get("data-copy-target", ""), node))
        if tag == "code" and "crypto-address" in node.classes:
            self.codes.append(node)

    def _internal(self, value: str) -> bool:
        return (
            bool(_INTERNAL.fullmatch(value))
            and ":" not in value
            and (not value.startswith("//"))
        )

    def _chrome(self, parent: _Node, node: _Node) -> None:
        tag = node.tag
        if tag not in _CHROME_TAGS:
            _fail(f"<{tag}> in the page chrome")
        self._only(node, _CHROME_ATTRS | _CHROME_TAG_ATTRS.get(tag, frozenset()))
        attrs = node.attrs
        if tag == "a":
            href = attrs.get("href", "")
            if not href or not (self._internal(href) or href in self.urls):
                _fail("a chrome link that is neither internal nor a configured URL")
            if "ping" in attrs and not _PING.fullmatch(attrs["ping"]):
                _fail("a chrome link ping that is not a click-count path")
        elif tag == "img":
            self.images += 1
            src = attrs.get("src", "")
            if self.images > 1 or not (self._internal(src) or src in self.urls):
                _fail("a chrome image that is not the one configured avatar")
        elif tag == "section":
            if "support" in node.classes:
                if parent.tag != "main":
                    _fail("a support section anywhere but directly in <main>")
            elif not ({"profile-card", "cookie-banner"} & node.classes):
                _fail("an unknown <section> in the page chrome")
        elif tag == "form":
            if "cookie-banner" not in parent.classes or parent.tag != "section":
                _fail("a form outside the cookie notice")
            if attrs.get("method") != "get" or not self._internal(
                attrs.get("action", "")
            ):
                _fail("a cookie form that is not a same-site GET")
        elif tag == "button":
            if parent.tag != "form" or attrs.get("type") != "submit":
                _fail("a chrome button outside the cookie form")

    def _main_child(self, node: _Node) -> None:
        tag = node.tag
        if tag == "section" and "support" in node.classes:
            self.sections += 1
            return
        kind = tag
        if tag == "section":
            kind = next(
                (k for k in ("profile-card", "cookie-banner") if k in node.classes),
                tag,
            )
        limit = _MAIN_LIMITS.get(kind)
        if limit is None:
            _fail(f"<{tag}> directly in <main>")
        self.main_counts[kind] = self.main_counts.get(kind, 0) + 1
        if self.main_counts[kind] > limit:
            _fail(f"too many <{tag}> directly in <main>")

    def _head(self, node: _Node) -> None:
        tag, attrs = node.tag, node.attrs
        if tag not in _HEAD_TAGS:
            _fail(f"<{tag}> in <head>")
        if tag == "meta":
            if "http-equiv" in attrs:
                _fail("a <meta http-equiv>")
            if "charset" not in attrs and attrs.get("name") not in _META_NAMES:
                _fail("an unknown <meta>")
        elif tag == "link":
            rel = attrs.get("rel", "")
            if rel not in _LINK_RELS:
                _fail(f"<link rel={rel!r}>")
            if rel == "stylesheet":
                m = _ASSET.fullmatch(attrs.get("href", ""))
                if not m or m.group(2) != "css" or m.group(1) not in _STYLES:
                    _fail("a stylesheet outside the engine set")
        elif tag == "script":
            m = _ASSET.fullmatch(attrs.get("src", ""))
            if not m or m.group(2) != "js" or m.group(1) not in _SCRIPTS:
                _fail("a script outside the engine set")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            _fail(f"an end tag for the void element <{tag}>")
        if self.cur.tag != tag or self.cur.parent is None:
            _fail(f"a stray or mismatched </{tag}>")
        self.cur = self.cur.parent

    def handle_data(self, data: str) -> None:
        node = self.cur
        node.text.append(data)
        if not data.strip():
            return
        if node.tag == "title":
            return
        if node.tag in ("#root", "html", "head", "body", "main", "script"):
            _fail(f"text directly in <{node.tag}>")
        parent = node.parent
        if node.tag == "p" and parent is not None and parent.tag == "main":
            _fail("text directly in the back-link paragraph")
        if node.tag == "a" and parent is not None and parent.tag == "p":
            if parent.parent is not None and parent.parent.tag == "main":
                if data.strip() != _BACK_TEXT:
                    _fail("the back-link text is not the fixed text")

    def handle_comment(self, data: str) -> None:
        if _inside_support(self.cur):
            _fail("a comment inside the support content")

    def handle_decl(self, decl: str) -> None:
        if decl.lower() != "doctype html" or self.root.seen:
            _fail("a declaration")

    def handle_pi(self, data: str) -> None:
        _fail("a processing instruction")

    def unknown_decl(self, data: str) -> None:
        _fail("a marked section")


def check_support_html(
    html: str,
    addresses: list[str] | None = None,
    urls: frozenset[str] | set[str] = frozenset(),
) -> None:
    """Refuse a support page that is not exactly the allowed shape.

    ``addresses`` are the coin addresses from the config, in order: the page's
    ``code.crypto-address`` elements must hold exactly these (an address
    element has no child elements, so its text is all of what is copied).
    Left out, only the structure and the button-to-address pairing are checked.
    ``urls`` are the outside URLs the fixed chrome may link to or show (the
    site navigation, profile button, socials, credit link and avatar); any
    other chrome link must be a path inside the site.
    """
    scan = _Check(frozenset(urls))
    scan.feed(html)
    scan.close()
    if scan.cur is not scan.root:
        _fail(f"an unclosed <{scan.cur.tag}>")
    if scan.mains != 1 or scan.sections != 1:
        _fail("it must hold exactly one <main> with one support section")
    for ident, count in scan.ids.items():
        if count > 1:
            _fail(f"the id {ident!r} is used more than once")
    texts = ["".join(node.text) for node in scan.codes]
    if addresses is not None and texts != list(addresses):
        _fail("an address element does not match the configured address")
    per_card: dict[int, int] = {}
    for node in scan.codes:
        card = _card(node)
        if card is None:
            _fail("an address element outside a card")
        per_card[id(card)] = per_card.get(id(card), 0) + 1
    if any(count != 1 for count in per_card.values()):
        _fail("a card must hold exactly one address element")
    by_id = {node.attrs.get("id", ""): node for node in scan.codes}
    for target, button in scan.buttons:
        code = by_id.get(target)
        if code is None:
            _fail("a copy button does not point at an address element")
        elif _card(code) is not _card(button):
            _fail("a copy button points at an address in another card")
