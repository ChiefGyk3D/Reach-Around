# SPDX-License-Identifier: Apache-2.0
"""The funnel hop label and the ``?h=`` writer (issue #593, funnel #566).

A hop label names the surface of ours a visitor came from, one step back. The
reader side is ``click.js`` (reads ``h`` from the page address) and the Worker
(``worker/src/sites.ts`` lists the labels a site accepts). Both accept exactly
1 to 30 characters of ``a-z``, ``0-9`` and ``-``; the Worker also keeps
``other`` (its fold label) and ``none`` (a click with no value) for itself.
``direct`` is refused here too: it is the source counter's label for a click
with no source and would read as a mistake in a hop table.

This module is the one place the build judges a label, so the writer never
holds a second copy of the rule. ``tests/test_hoplabel_contract.py`` runs the
shipped reader against it so the two cannot drift apart.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl

HOP_PARAM = "h"
MAX_HOP_LABEL = 30
_LABEL = re.compile(r"[a-z0-9-]{1,30}")
RESERVED_HOP_LABELS = frozenset({"other", "none", "direct"})


def check_hop_label(value: str) -> str:
    """Return ``value`` if it is a hop label, else raise with the reason."""
    if not value:
        raise ValueError("hop_label must not be empty")
    if len(value) > MAX_HOP_LABEL:
        raise ValueError(f"hop_label must be at most {MAX_HOP_LABEL} characters")
    if value != value.lower() and _LABEL.fullmatch(value.lower()):
        raise ValueError("hop_label must be lower case (a-z, 0-9 and -)")
    if _LABEL.fullmatch(value) is None:
        raise ValueError("hop_label may use only a-z, 0-9 and - (no spaces or symbols)")
    if value in RESERVED_HOP_LABELS:
        raise ValueError(
            f"hop_label {value!r} is reserved by the collector: pick another name"
        )
    return value


def has_hop_param(url: str) -> bool:
    """True if the query of ``url`` already holds an ``h`` key."""
    query = url.split("#", 1)[0].partition("?")[2]
    return any(k == HOP_PARAM for k, _ in parse_qsl(query, keep_blank_values=True))


def add_hop(url: str, label: str) -> str:
    """Return ``url`` with ``h=<label>`` added to its query.

    The existing query is kept byte for byte and ``h`` is appended after it;
    the fragment, if any, stays at the end. A url that already has an ``h``
    key is refused rather than overwritten.
    """
    check_hop_label(label)
    if has_hop_param(url):
        raise ValueError(
            "the link already has an h query parameter; it is not overwritten"
        )
    base, hash_, fragment = url.partition("#")
    if "?" not in base:
        base += "?"
    elif not base.endswith(("?", "&")):
        base += "&"
    return f"{base}{HOP_PARAM}={label}{hash_}{fragment}"
