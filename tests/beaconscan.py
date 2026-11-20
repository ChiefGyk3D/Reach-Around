# SPDX-License-Identifier: Apache-2.0
"""A source scan for the two page scripts' one allowed request (issue #610).

``click.js`` and ``view.js`` may make exactly one ``fetch``: a same-origin POST
to the token route, written as ``fetch(MINT_ROUTE, ...)`` with the constant
``MINT_ROUTE = "/e/t"``. Every other way to reach the network stays banned:
a second fetch, a fetch of anything else, a full URL, XMLHttpRequest and the
other transports. ``sendBeacon`` and ``ping`` stay same-origin paths.
"""

from __future__ import annotations

import re

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"(?m)(?<![:\"'])//.*$")

TRANSPORTS = (
    "XMLHttpRequest",
    "EventSource",
    "WebSocket",
    "importScripts",
    "Image(",
    "new Request",
    "navigator.serviceWorker",
)


def strip_comments(source: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", source))


def network_problems(source: str) -> list[str]:
    """Everything in ``source`` that is more than the one same-origin token fetch."""
    # `typeof fetch` only asks whether the function exists; it is not a request.
    code = re.sub(r"typeof\s+fetch\b", "", strip_comments(source))
    problems: list[str] = []
    fetches = re.findall(r"\bfetch\b", code)
    if len(fetches) != 1:
        problems.append(f"fetch appears {len(fetches)} times, exactly one is allowed")
    if len(re.findall(r"\bfetch\(MINT_ROUTE,", code)) != 1:
        problems.append("the one fetch must be fetch(MINT_ROUTE, ...)")
    if len(re.findall(r'\bvar MINT_ROUTE = "/e/t";', code)) != 1:
        problems.append('MINT_ROUTE must be defined once as "/e/t"')
    if (
        re.search(r"\bMINT_ROUTE\s*=(?!=)", code)
        and len(re.findall(r"\bMINT_ROUTE\s*=(?!=)", code)) != 1
    ):
        problems.append("MINT_ROUTE is assigned more than once")
    call = re.search(r"\bfetch\(MINT_ROUTE,(.*?)\)\s*\.then", code, re.DOTALL)
    if (
        call is None
        or 'method: "POST"' not in call.group(1)
        or 'credentials: "omit"' not in call.group(1)
    ):
        problems.append('the fetch must be a POST with credentials: "omit"')
    for word in TRANSPORTS:
        if word in code:
            problems.append(f"uses {word}")
    if re.search(r"""["'`](?:[a-z][a-z0-9+.-]*:)?//""", code):
        problems.append("a full or protocol-relative URL")
    if re.search(r"""["'`]https?:""", code):
        problems.append("an http(s) URL")
    return problems


SHARED_START = (
    "// --- beacon token (this block is identical in click.js and view.js; "
    "a test keeps them equal) ---"
)
SHARED_END = "// --- end of the beacon token block ---"


def shared_block(source: str) -> str | None:
    if SHARED_START not in source or SHARED_END not in source:
        return None
    return source[
        source.index(SHARED_START) : source.index(SHARED_END) + len(SHARED_END)
    ]
