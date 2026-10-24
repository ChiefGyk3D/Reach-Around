# SPDX-License-Identifier: Apache-2.0
"""A deliberately tiny markdown renderer.

It supports bold, italic, https links, bullet lists and line breaks, and
nothing else. Every piece of user text is HTML-escaped as it is emitted, so raw
HTML can never reach the output; the parser only adds tags it writes itself.

Emphasis is parsed in a single linear pass with a stack, never with
backtracking regexes, and unmatched markers are left as literal text so the
output is always well-formed. Link targets go through the same validator the
config models use, so the renderer and the models agree on what a URL is.
"""

from __future__ import annotations

import html
import re

from markupsafe import Markup

from reach_around.config.models import check_https_url

MAX_MARKDOWN_CHARS = 5000  # matches the Text block's model limit
_MAX_TARGET = 2049  # one more than the longest URL the models accept
_BULLET = re.compile(r"^[-*] +(.*)$")


class _Escaped(str):
    """HTML this module built from escaped pieces; ``Markup`` keeps it as is."""

    def __html__(self) -> str:
        return str(self)


def _link_target(text: str, start: int) -> tuple[str, int] | None:
    """Parse ``(target)`` at ``start``; return the target and the end index."""
    if start >= len(text) or text[start] != "(":
        return None
    if text.find(")", start) == -1:
        return None
    depth = 0
    end = min(len(text), start + 1 + _MAX_TARGET + 1)
    for pos in range(start + 1, end):
        ch = text[pos]
        if ch.isspace():
            return None
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                return text[start + 1 : pos], pos + 1
            depth -= 1
    return None


def _safe_href(url: str) -> str | None:
    """Return the escaped, scheme-normalized URL for ``href``, or ``None``."""
    try:
        check_https_url(url)
    except ValueError:
        return None
    if url[:8].lower() != "https://":
        return None
    return html.escape("https://" + url[8:])


def _inline(text: str) -> str:
    """Render one line of raw (unescaped) text; the result is safe HTML."""
    out: list[str] = []
    # Open emphasis markers: (marker, index into ``out`` holding its tag).
    stack: list[tuple[str, int]] = []
    open_count = {"*": 0, "**": 0}
    n = len(text)
    pos = 0
    plain_from = 0

    def flush(upto: int) -> None:
        if upto > plain_from:
            out.append(html.escape(text[plain_from:upto]))

    while pos < n:
        ch = text[pos]
        if ch == "[":
            close = text.find("]", pos + 1)
            if close == -1:
                # No "]" anywhere later, so no later "[" can start a link.
                pos = n
                break
            label_ok = close > pos + 1 and text.find("[", pos + 1, close) == -1
            parsed = _link_target(text, close + 1) if label_ok else None
            href = _safe_href(parsed[0]) if parsed else None
            if parsed is None or href is None:
                pos += 1
                continue
            flush(pos)
            out.append(
                f'<a href="{href}" rel="noopener noreferrer nofollow ugc">'
                f"{_inline(text[pos + 1 : close])}</a>"
            )
            pos = plain_from = parsed[1]
            continue
        if ch != "*":
            pos += 1
            continue
        run_end = pos
        while run_end < n and text[run_end] == "*":
            run_end += 1
        count = run_end - pos
        can_open = run_end < n and not text[run_end].isspace()
        can_close = pos > 0 and not text[pos - 1].isspace()
        flush(pos)
        markers = ["**"] * (count // 2) + ["*"] * (count % 2)
        if can_close and not can_open:
            markers.reverse()  # close the innermost (single) marker first
        elif can_close and can_open and not any(open_count[m] for m in markers):
            can_close = False
        for marker in markers:
            if can_close and open_count[marker]:
                while stack[-1][0] != marker:
                    popped, index = stack.pop()
                    open_count[popped] -= 1
                    out[index] = popped  # unmatched: leave as literal text
                _, index = stack.pop()
                open_count[marker] -= 1
                tag = "strong" if marker == "**" else "em"
                out[index] = f"<{tag}>"
                out.append(f"</{tag}>")
            elif can_open:
                stack.append((marker, len(out)))
                open_count[marker] += 1
                out.append(marker)  # replaced by the tag if it gets closed
            else:
                out.append(marker)
        pos = plain_from = run_end
    flush(n)
    for marker, index in stack:
        out[index] = marker
    return "".join(out)


def render_markdown(text: str) -> Markup:
    """Return safe HTML for ``text``; the only markup is what this module writes."""
    if len(text) > MAX_MARKDOWN_CHARS:
        raise ValueError(f"markdown is longer than {MAX_MARKDOWN_CHARS} characters")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
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
    return Markup("\n").join(_Escaped(block) for block in blocks)
