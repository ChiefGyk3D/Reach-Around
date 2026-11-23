# SPDX-License-Identifier: Apache-2.0
"""A last check over the built support page (#683).

The page carries payment instructions, so the finished HTML is read again:
inside the page's own content (the ``section.support`` element) there may be no
link, image, frame, embed, form or any attribute that holds a URL. The header,
the tab bar and the footer are fixed page chrome and sit outside that element.
This catches a template change or an override that renders something the
config checks never saw.
"""

from __future__ import annotations

from html.parser import HTMLParser

_FORBIDDEN_TAGS = frozenset(
    {"a", "img", "picture", "source", "iframe", "object", "embed", "form", "link"}
)
_URL_ATTRS = frozenset(
    {"href", "src", "srcset", "action", "formaction", "data", "poster", "ping", "cite"}
)


class _Scan(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.problems: list[str] = []
        self._stack: list[bool] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = dict(attrs).get("class") or ""
        opens = tag == "section" and "support" in classes.split()
        inside = self.depth > 0
        if inside:
            if tag in _FORBIDDEN_TAGS:
                self.problems.append(f"<{tag}>")
            for name, _value in attrs:
                if name in _URL_ATTRS:
                    self.problems.append(f"{tag} {name}")
        if tag not in ("br", "hr", "img", "input", "link", "meta", "source"):
            self._stack.append(opens)
            if opens:
                self.depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if self._stack and tag not in ("br", "hr", "img", "input", "link", "meta"):
            if self._stack.pop():
                self.depth -= 1


def check_support_html(html: str) -> None:
    """Refuse a support page whose content holds a link, image or URL attribute."""
    scan = _Scan()
    scan.feed(html)
    scan.close()
    if scan.problems:
        raise ValueError(
            "the support page content has an element or attribute it must not "
            f"have: {scan.problems[0]}"
        )
