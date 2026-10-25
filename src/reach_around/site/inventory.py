# SPDX-License-Identifier: Apache-2.0
"""The published-file inventory and the cache-purge list computed from two of them.

``build`` writes ``<out>.assets.json`` beside the output (never inside it, like
the link manifest): every published path with its SHA-256, size and class
(``hashed`` or ``stable``). A later deploy compares the previous inventory with
the current one and purges, by URL, only the paths a host may still have cached
under an old state: removed files, stable files whose bytes changed, and the
entry points (pages, ``404.html``, ``robots.txt``).

The previous inventory comes from an earlier deployment's artifact, so it is
read as semi-trusted input: size-capped, strict JSON (no duplicate keys), every
path checked against a plain-character pattern before it can reach a URL.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reach_around.site.cachepolicy import url_variants

HEADERS_FILE = "_headers"
VERSION = 1
MAX_INVENTORY_BYTES = 4 * 1024 * 1024
MAX_ENTRIES = 20000
KINDS = ("hashed", "stable")
# Relative published path: plain segments, no dot segments, no leading slash.
_PATH = re.compile(r"[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*")
_SHA = re.compile(r"[0-9a-f]{64}")
# Paths every purge covers whether or not they changed.
ALWAYS = ("/", "/404.html", "/robots.txt")


class InventoryError(ValueError):
    """An inventory file is missing, unreadable or not what the build writes."""


@dataclass(frozen=True)
class InventoryEntry:
    """One published file: full SHA-256, size in bytes, and its cache class."""

    sha256: str
    size: int
    kind: str


Inventory = dict[str, InventoryEntry]


def build_inventory(files: Mapping[Path, bytes], hashed: Iterable[Path]) -> Inventory:
    """Describe every file the site publishes (``_headers`` is not served)."""
    hashed_set = set(hashed)
    return {
        path.as_posix(): InventoryEntry(
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
            kind="hashed" if path in hashed_set else "stable",
        )
        for path, data in sorted(files.items())
        if path.as_posix() != HEADERS_FILE
    }


def inventory_json(inventory: Mapping[str, InventoryEntry]) -> str:
    """The inventory file text: sorted keys, one stable form."""
    body = {
        "version": VERSION,
        "files": {
            path: {"class": e.kind, "sha256": e.sha256, "size": e.size}
            for path, e in sorted(inventory.items())
        },
    }
    return json.dumps(body, sort_keys=True, indent=2) + "\n"


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise InventoryError(f"duplicate key {key!r}")
        out[key] = value
    return out


def parse_inventory(text: str) -> Inventory:
    """Parse and validate inventory text; raise ``InventoryError`` on anything odd."""
    try:
        data = json.loads(text, object_pairs_hook=_no_duplicates)
    except (ValueError, RecursionError) as exc:
        raise InventoryError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise InventoryError(f"not an assets inventory (version {VERSION})")
    files = data.get("files")
    if not isinstance(files, dict) or set(data) != {"version", "files"}:
        raise InventoryError("the inventory must hold only version and files")
    if len(files) > MAX_ENTRIES:
        raise InventoryError(f"more than {MAX_ENTRIES} entries")
    inventory: Inventory = {}
    for path, entry in files.items():
        if not _PATH.fullmatch(path) or any(p in (".", "..") for p in path.split("/")):
            raise InventoryError(f"path {path[:60]!r} is not a plain relative path")
        if not isinstance(entry, dict) or set(entry) != {"class", "sha256", "size"}:
            raise InventoryError(f"entry for {path} must have class, sha256 and size")
        kind, sha, size = entry["class"], entry["sha256"], entry["size"]
        if kind not in KINDS:
            raise InventoryError(f"class of {path} must be one of {KINDS}")
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise InventoryError(f"sha256 of {path} is not 64 hex digits")
        if type(size) is not int or size < 0:
            raise InventoryError(f"size of {path} is not a count of bytes")
        inventory[path] = InventoryEntry(sha, size, kind)
    return inventory


def load_inventory(path: Path) -> Inventory:
    """Read an inventory file from disk (size-capped, regular files only)."""
    try:
        if not path.is_file():
            raise InventoryError(f"{path} is not a file")
        with path.open("rb") as handle:
            raw = handle.read(MAX_INVENTORY_BYTES + 1)
    except OSError as exc:
        raise InventoryError(f"cannot read {path}: {exc.strerror}") from exc
    if len(raw) > MAX_INVENTORY_BYTES:
        raise InventoryError(f"{path} is larger than {MAX_INVENTORY_BYTES} bytes")
    try:
        return parse_inventory(raw.decode("utf-8"))
    except (InventoryError, UnicodeDecodeError) as exc:
        raise InventoryError(f"{path}: {exc}") from exc


def purge_paths(
    previous: Mapping[str, InventoryEntry], current: Mapping[str, InventoryEntry]
) -> list[str]:
    """URL paths to purge: removed files, changed stable files, entry points.

    Entry points are always included: ``/``, every page, ``/404.html`` and
    ``/robots.txt``. A kept hashed file needs no purge (a new hash is a new
    URL); a removed one is listed so an old copy cannot linger.
    """
    urls: set[str] = set(ALWAYS)
    for rel in set(previous) - set(current):
        urls.update(url_variants(rel))
    for rel, entry in current.items():
        if rel.endswith(".html"):
            urls.add(url_variants(rel)[0])
            continue
        before = previous.get(rel)
        changed = before is not None and before.sha256 != entry.sha256
        if entry.kind == "stable" and changed:
            urls.update(url_variants(rel))
    return sorted(urls)
