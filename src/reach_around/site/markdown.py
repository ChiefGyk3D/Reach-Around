# SPDX-License-Identifier: Apache-2.0
"""A deliberately tiny markdown renderer.

It supports bold, italic, https links, bullet lists and line breaks, and
nothing else. The input is HTML-escaped first, so raw HTML can never reach the
output; the parser then only adds tags it writes itself.
"""

from __future__ import annotations

import html
import re

from reach_around.config.models import check_https_url

# Runs on already-escaped text, so the label and URL cannot hold < > or quotes.
_LINK = re.compile(r"\[([^\[\]]+)\]\((https://[^\s()]+)\)")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_ITALIC = re.compile(r"\*(?=\S)(.+?)(?<=\S)\*")
_BULLET = re.compile(r"^[-*] +(.*)$")
_UNSAFE_IN_ATTRIBUTE = ("&", '"', "'", "<", ">", "`", "\\")


def _emphasis(text: str) -> str:
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    return _ITALIC.sub(r"<em>\1</em>", text)


def _safe_href(escaped_url: str) -> str | None:
    """Return the URL to put in ``href``, or ``None`` if it is not allowed."""
    url = html.unescape(escaped_url)
    # Refuse anything that needed escaping beyond a plain "&" in a query string.
    stripped = url.replace("&", "")
    if any(ch in stripped for ch in _UNSAFE_IN_ATTRIBUTE) or html.escape(url) != (
        escaped_url
    ):
        return None
    try:
        check_https_url(url)
    except ValueError:
        return None
    return escaped_url


def _inline(escaped: str) -> str:
    out: list[str] = []
    pos = 0
    for match in _LINK.finditer(escaped):
        href = _safe_href(match.group(2))
        if href is None:
            continue
        out.append(_emphasis(escaped[pos : match.start()]))
        out.append(
            f'<a href="{href}" rel="noopener noreferrer nofollow ugc">'
            f"{_emphasis(match.group(1))}</a>"
        )
        pos = match.end()
    out.append(_emphasis(escaped[pos:]))
    return "".join(out)


def render_markdown(text: str) -> str:
    """Return safe HTML for ``text``; the only markup is what this module writes."""
    lines = html.escape(text.replace("\r\n", "\n").replace("\r", "\n")).split("\n")
    blocks: list[str] = []
    paragraph: list[str] = []
    items: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append("<p>" + "<br>\n".join(paragraph) + "</p>")
            paragraph.clear()
        if items:
            blocks.append(
                "<ul>\n" + "\n".join(f"<li>{item}</li>" for item in items) + "\n</ul>"
            )
            items.clear()

    for line in lines:
        stripped = line.strip()
        bullet = _BULLET.match(stripped)
        if not stripped:
            flush()
        elif bullet:
            if paragraph:
                flush()
            items.append(_inline(bullet.group(1)))
        else:
            if items:
                flush()
            paragraph.append(_inline(stripped))
    flush()
    return "\n".join(blocks)
