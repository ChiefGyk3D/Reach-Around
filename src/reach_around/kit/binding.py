# SPDX-License-Identifier: Apache-2.0
"""The build binding: what ties a measures file to the pages one build wrote.

At build time the gated directory gets ``kit-binding.json`` beside the policy
record. It holds digests of the display policy, of the resolved measures the
build compiled, of the gated page and of the public kit page. ``kit private-measures``
recomputes each one from the inputs it was given and the directories it was
pointed at, and refuses when any differs, so a measures file is always the
numbers of the pages it names, from one build. The file records the same
digests, and a reader can check them against an expected binding.

The binding stays in the gated directory (never published): the digests of
gated figures are not for the public tree.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from reach_around.config.measures import Measure

BINDING_FILE = Path("kit-binding.json")
BINDING_KEYS = ("policy", "measures", "gated_page", "public_page")
_HEX = re.compile(r"[0-9a-f]{64}")
MAX_BINDING_BYTES = 4096


def measure_row(m: Measure) -> dict[str, Any]:
    """One measure as the measures file prints it."""
    return {
        "platform": m.platform,
        "key": m.key,
        "label": m.label,
        "unit": m.unit,
        "value": m.value,
        "as_of": None if m.as_of is None else m.as_of.isoformat(),
        "period": None
        if m.period is None
        else [m.period[0].isoformat(), m.period[1].isoformat()],
        "visibility": m.visibility,
        "trust": None
        if m.trust is None
        else {"label": m.trust.label, "method": m.trust.method},
        "in_summary": m.in_summary,
        "formula": m.formula,
        "note": m.note,
        "provenance": m.provenance,
        "source_note": m.source_note,
    }


def sha256_text(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


def rows_digest_of(rows: Iterable[Mapping[str, Any]]) -> str:
    """A digest of measure rows as ``measure_row`` prints them, in page order. The
    one canonical form: the build, the writer and the reader all use it, so a
    row read back from a file digests exactly as the measure it came from."""
    return sha256_text(
        json.dumps([dict(r) for r in rows], sort_keys=True, ensure_ascii=True)
    )


def rows_digest(measures: Iterable[Measure]) -> str:
    """A digest of the resolved measures, in page order."""
    return rows_digest_of(measure_row(m) for m in measures)


def make_binding(
    policy: str,
    measures: str,
    gated_page: str | bytes | None,
    public_page: str | bytes,
) -> dict[str, str | None]:
    """The binding of one build. A kit with no gated content has no gated page:
    ``gated_page`` is then None (null in the record), and the reader requires that
    the gated directory holds no page either."""
    return {
        "policy": policy,
        "measures": measures,
        "gated_page": None if gated_page is None else sha256_text(gated_page),
        "public_page": sha256_text(public_page),
    }


def binding_text(binding: Mapping[str, str | None]) -> str:
    return json.dumps(dict(binding), sort_keys=True, indent=2) + "\n"


def check_binding_shape(found: object) -> dict[str, str | None]:
    """``found`` as a binding: exactly the four keys, each a SHA-256 hex digest
    (``gated_page`` may be null: a kit with no gated page)."""
    if not isinstance(found, dict) or set(found) != set(BINDING_KEYS):
        raise ValueError("the build binding does not have the expected keys")
    for key in BINDING_KEYS:
        value = found[key]
        if value is None and key == "gated_page":
            continue
        if not isinstance(value, str) or not _HEX.fullmatch(value):
            raise ValueError(f"the build binding {key} is not a SHA-256 digest")
    return {key: found[key] for key in BINDING_KEYS}


def read_binding(path: Path) -> dict[str, str | None]:
    """Read the binding record of a build. A missing, linked or malformed record
    is refused with a message that says to rebuild."""
    rebuild = "rebuild the site first (reach-around build --gated-out)"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise ValueError(f"the build has no binding record; {rebuild}") from exc
    with os.fdopen(fd, "rb") as handle:
        raw = handle.read(MAX_BINDING_BYTES + 1)
    try:
        if len(raw) > MAX_BINDING_BYTES:
            raise ValueError("too large")
        return check_binding_shape(json.loads(raw.decode("utf-8")))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"the build binding record is not valid; {rebuild}") from exc
