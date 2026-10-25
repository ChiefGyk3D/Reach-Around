# SPDX-License-Identifier: Apache-2.0
"""Content-hashed names for every static file the build publishes.

A file ``static/site.css`` is published as ``static/site.<hash>.css`` where
``<hash>`` is the first 20 hex digits (80 bits) of the SHA-256 of the final bytes. The
name changes when, and only when, the content changes, so the host can send
``Cache-Control: immutable`` for these files and a changed file is always a
cache miss.

The order matters. Files that nothing else references (images, fonts, icons,
scripts) are named first. A stylesheet is then rewritten so each ``url()``
points at the hashed name of its target, and only after that is it hashed, so
a changed icon changes the stylesheet's name too. HTML is rendered last with
the finished mapping (the ``asset`` template function); it is never rewritten
after the fact and its own name never changes.

Files a third party must know by name stay as they are (see
``STABLE_NAMES``); the host revalidates those on every request.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# 20 hex digits = 80 bits. A name is cached for a year as immutable, so it must
# stay out of reach of a birthday collision or a second preimage.
HASH_LEN = 20

# Root-level files that browsers and crawlers request by a fixed name.
STABLE_NAMES = frozenset(
    {"favicon.ico", "apple-touch-icon.png", "apple-touch-icon-precomposed.png"}
)


class UnknownAssetError(ValueError):
    """A template or stylesheet named a file the build does not publish."""


def content_hash(data: bytes) -> str:
    """The full SHA-256 of ``data`` as hex."""
    return hashlib.sha256(data).hexdigest()


def hashed_name(path: str, data: bytes) -> str:
    """``dir/stem.<hash>.ext`` for ``dir/stem.ext`` with the given content."""
    posix = PurePosixPath(path)
    digest = content_hash(data)[:HASH_LEN]
    name = f"{posix.stem}.{digest}{posix.suffix}"
    return str(posix.with_name(name))


def is_stable_name(path: str) -> bool:
    """True for a root-level file that must keep its name."""
    return path in STABLE_NAMES


def _normalize(name: str) -> str:
    return posixpath.normpath(name)


class AssetMap:
    """Logical published path -> the path the build actually wrote."""

    def __init__(self, mapping: Mapping[str, str]) -> None:
        self._mapping = dict(mapping)

    def __call__(self, name: str) -> str:
        found = self._mapping.get(_normalize(name))
        if found is None:
            raise UnknownAssetError(
                f"asset({name!r}): the build does not publish that file "
                "(a template or override names a file that is missing, or one "
                "that is only published when its feature is on)"
            )
        return found

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and _normalize(name) in self._mapping

    def as_dict(self) -> dict[str, str]:
        return dict(self._mapping)


_CSS_URL = re.compile(
    r"""(url\s*\(\s*)(?:"([^"]*)"|'([^']*)'|([^)"'\s]*))(\s*\))""", re.IGNORECASE
)


def rewrite_css(css: str, css_path: str, mapping: Mapping[str, str]) -> str:
    """Point every ``url()`` that names a published file at its hashed name.

    Relative targets resolve against the stylesheet's own directory and
    root-relative ones (``/media/a.png``) against the site root. Remote and
    ``data:`` targets, fragments and names the build does not publish are left
    exactly as written; a query or fragment suffix is kept.
    """
    here = posixpath.dirname(css_path)

    def replace(match: re.Match[str]) -> str:
        opener, double, single, bare, closer = match.groups()
        quote = '"' if double is not None else "'" if single is not None else ""
        target = next(g for g in (double, single, bare) if g is not None).strip()
        if not target or target.startswith(("#", "//")) or ":" in target:
            return match.group(0)
        cut = re.search(r"[?#]", target)
        core, suffix = (
            (target, "")
            if cut is None
            else (target[: cut.start()], target[cut.start() :])
        )
        rooted = core.startswith("/")
        logical = _normalize(core.lstrip("/") if rooted else posixpath.join(here, core))
        mapped = mapping.get(logical)
        if mapped is None or logical.startswith(".."):
            return match.group(0)
        if rooted:
            new = "/" + mapped
        else:
            new = posixpath.relpath(mapped, here or ".")
        return f"{opener}{quote}{new}{suffix}{quote}{closer}"

    return _CSS_URL.sub(replace, css)


@dataclass(frozen=True)
class Fingerprinted:
    """The renamed files, the mapping, and which of them carry a hash."""

    files: dict[Path, bytes]
    assets: AssetMap
    hashed: frozenset[Path]


def fingerprint(files: Mapping[Path, bytes]) -> Fingerprinted:
    """Name every file by content, leaves first, then the CSS that uses them."""
    mapping: dict[str, str] = {}
    out: dict[Path, bytes] = {}
    hashed: set[Path] = set()

    def place(logical: str, new: str, data: bytes) -> None:
        mapping[logical] = new
        out[Path(new)] = data
        hashed.add(Path(new))

    stylesheets: dict[str, bytes] = {}
    for path, data in sorted(files.items()):
        logical = path.as_posix()
        if path.suffix.lower() == ".css":
            stylesheets[logical] = data
        elif is_stable_name(logical):
            mapping[logical] = logical
            out[path] = data
        else:
            place(logical, hashed_name(logical, data), data)
    for logical, raw in sorted(stylesheets.items()):
        text = rewrite_css(raw.decode("utf-8"), logical, mapping).encode("utf-8")
        place(logical, hashed_name(logical, text), text)
    return Fingerprinted(out, AssetMap(mapping), frozenset(hashed))
