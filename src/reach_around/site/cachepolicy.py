# SPDX-License-Identifier: Apache-2.0
"""Which published paths the host may cache forever and which it must revalidate.

Two classes, and every published file is in exactly one:

* ``hashed``: the name carries a hash of the content (see ``fingerprint``), so
  the bytes behind a URL never change. These get a one-year ``immutable`` rule.
* ``stable``: pages, ``404.html``, ``robots.txt`` and files a third party asks
  for by name. These are revalidated on every request, so a deploy shows at once.

Cloudflare Pages ``_headers`` merges every matching rule and joins repeated
headers with a comma. A catch-all revalidate rule plus a narrower immutable
rule would therefore send both values for a hashed file. So no rule here is a
catch-all: each class is listed by patterns that cannot match the other, and
the paths in neither class (an unknown URL) fall to the host default, which is
also ``max-age=0, must-revalidate``.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable

IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "public, max-age=0, must-revalidate"

# Characters allowed in a header path; anything else is left to the host default.
HEADER_PATH = re.compile(r"/[A-Za-z0-9._~/-]*")


def url_variants(rel: str) -> list[str]:
    """The URL paths a file answers to: a page at both its folder and file URL."""
    if rel == "index.html":
        return ["/", "/index.html"]
    if rel.endswith("/index.html"):
        return ["/" + rel[: -len("index.html")], "/" + rel]
    return ["/" + rel]


def cache_patterns(
    published: Iterable[str], hashed: Collection[str]
) -> tuple[list[str], list[str]]:
    """Return ``(immutable, revalidate)`` header patterns for the published files.

    ``published`` is every file path relative to the site root (not ``_headers``,
    which is never served); ``hashed`` is the subset with content-hashed names.
    A folder gets one ``/<folder>/*`` rule only when every file under it is
    hashed; otherwise each hashed file there gets its own exact rule.
    """
    files = sorted(set(published))
    immutable: list[str] = []
    folders: dict[str, list[str]] = {}
    for rel in files:
        top = rel.split("/", 1)[0] if "/" in rel else ""
        folders.setdefault(top, []).append(rel)
    for top, members in sorted(folders.items()):
        mine = [m for m in members if m in hashed]
        if not mine:
            continue
        if top and len(mine) == len(members) and HEADER_PATH.fullmatch(f"/{top}/"):
            immutable.append(f"/{top}/*")
        else:
            immutable.extend(f"/{m}" for m in mine if HEADER_PATH.fullmatch(f"/{m}"))
    revalidate = [
        url
        for rel in files
        if rel not in hashed
        for url in url_variants(rel)
        if HEADER_PATH.fullmatch(url)
    ]
    return immutable, revalidate
