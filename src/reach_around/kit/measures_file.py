# SPDX-License-Identifier: Apache-2.0
"""The owner-only measures file, ``kit-measures.json`` (kit v2 slice 7, #632).

The sponsor page and the quote read this file, and nothing else, for a measure
value, so a quoted number is the number the gated kit shows, with the same trust
label and date. The kit computes no price.

The file holds what the gated page may show: the public and gated measures,
never a private one, and nothing else of the kit (no partner, rate, case study,
demographic, inquiry detail or site key). It is built from the same compiled
measures and the same display policy as the pages and the PDFs, and it is held
to the same walls:

* it is written only to an owner-only directory outside the public output, the
  gated directory, the config directory and any git work tree, never through a
  link, so it is in no published or gated artifact;
* the gated page's policy record must match the current display policy (a page
  built before the policy changed is refused until rebuilt);
* the text is searched for every private figure and every not-for-PDF value
  before anything is written; a hit names the field, never the value.

The schema is versioned by ``schema_version`` and documented in ``docs/kit.md``.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from reach_around.config import SiteConfig
from reach_around.config.imported_history import ImportedHistory
from reach_around.config.kit import (
    KitConfig,
    enforce_display_policy,
    gated_projection,
    pdf_excluded_values,
    policy_fingerprint,
)
from reach_around.config.measures import Measure
from reach_around.config.numbers import Numbers, source_platforms
from reach_around.kit.output import JSON_NAME, confine_out_dir, write_private
from reach_around.kit.pdf import check_policy_record, private_measure_strings
from reach_around.site.kit import GATED_POLICY_FILE

FILE_NAME = "kit-measures.json"
SCHEMA_VERSION = 1
KIND = "reach-around-kit-measures"
NOTICE = "private: owner-only, never publish or share this file"
MAX_BYTES = 4 * 1024 * 1024

TOP_KEYS = frozenset(
    {"schema_version", "kind", "notice", "generated", "policy", "measures"}
)
MEASURE_KEYS = frozenset(
    {
        "platform",
        "key",
        "label",
        "unit",
        "value",
        "as_of",
        "period",
        "visibility",
        "trust",
        "in_summary",
        "formula",
        "note",
        "provenance",
        "source_note",
    }
)
TRUST_KEYS = frozenset({"label", "method"})


class MeasuresFileError(ValueError):
    """The measures file may not be written, or is not a valid one."""


def _row(m: Measure) -> dict[str, Any]:
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


def measures_document(
    config: SiteConfig,
    numbers: Numbers | None,
    imported: ImportedHistory | None,
    generated: date,
) -> dict[str, Any]:
    """The document: the gated projection's measures (public and gated, never
    private), in page order, each with its trust label and date."""
    kit = config.kit
    if kit is None:
        raise MeasuresFileError("the config has no media kit")
    enforce_display_policy(kit)
    shown = gated_projection(kit, numbers, imported, source_platforms(config))[
        "measures"
    ]
    if not shown:
        raise MeasuresFileError(
            "the kit has no measures to hand off; nothing was written"
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "notice": NOTICE,
        "generated": generated.isoformat(),
        "policy": policy_fingerprint(),
        "measures": [_row(m) for m in shown],
    }


def _needles(kit: KitConfig, numbers: Numbers | None) -> Sequence[tuple[str, str]]:
    found = [
        *sorted(private_measure_strings(kit, numbers).items()),
        *sorted(pdf_excluded_values(kit).items()),
    ]
    return [(field, value) for field, value in found if value]


def build_measures_file(
    config: SiteConfig,
    *,
    site_dir: Path,
    gated_dir: Path,
    out_dir: Path,
    generated: date,
    overwrite: bool = False,
    protect: Sequence[Path] = (),
    numbers: Numbers | None = None,
    imported: ImportedHistory | None = None,
) -> Path:
    """Write ``kit-measures.json`` to ``out_dir`` (see the module doc) and return
    its path. Nothing is written when any check fails."""
    kit = config.kit
    if kit is None:
        raise MeasuresFileError("the config has no media kit")
    document = measures_document(config, numbers, imported, generated)
    text = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    flat = " ".join(text.split())
    for field, value in _needles(kit, numbers):
        if " ".join(value.split()) in flat:
            raise MeasuresFileError(
                f"a figure of the private field {field} is in the measures file; "
                "nothing was written"
            )
    check_policy_record(gated_dir / GATED_POLICY_FILE, "gated page")
    target = confine_out_dir(out_dir, [gated_dir, site_dir, *protect])
    return write_private(
        target,
        FILE_NAME,
        text.encode("utf-8"),
        overwrite=overwrite,
        pattern=JSON_NAME,
    )


# --- the reader the sponsor page uses -----------------------------------------


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise MeasuresFileError(f"the measures file has a duplicate key {key!r}")
        out[key] = value
    return out


def _exact(found: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    extra = sorted(set(found) - allowed)
    missing = sorted(allowed - set(found))
    if extra or missing:
        raise MeasuresFileError(
            f"the measures file {where} has unexpected keys {extra} or lacks {missing}"
        )


def load_measures_file(path: Path) -> dict[str, Any]:
    """Read and validate a measures file. The sponsor page reads measure values
    through this and nothing else. Strict: a link, a large file, a duplicate or
    unknown key, another version or a private row is refused."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        if os.path.islink(path):
            raise MeasuresFileError(
                "the measures file is a link; it is refused"
            ) from exc
        raise MeasuresFileError(
            f"the measures file cannot be read ({exc.strerror})"
        ) from exc
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise MeasuresFileError("the measures file is not a regular file")
        if info.st_size > MAX_BYTES:
            raise MeasuresFileError("the measures file is too large")
        raw = handle.read(MAX_BYTES + 1)
    try:
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MeasuresFileError("the measures file is not valid JSON") from exc
    if not isinstance(doc, dict):
        raise MeasuresFileError("the measures file is not an object")
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise MeasuresFileError(
            f"the measures file schema_version is not {SCHEMA_VERSION}"
        )
    _exact(doc, TOP_KEYS, "top level")
    if doc["kind"] != KIND or not isinstance(doc["measures"], list):
        raise MeasuresFileError("the measures file is not a kit measures file")
    for row in doc["measures"]:
        if not isinstance(row, dict):
            raise MeasuresFileError("a row of measures is not an object")
        _exact(row, MEASURE_KEYS, "measures row")
        if row["visibility"] not in ("public", "gated"):
            raise MeasuresFileError("a measures row has a visibility it may not have")
        if row["trust"] is not None:
            if not isinstance(row["trust"], dict):
                raise MeasuresFileError("a measures row trust is not an object")
            _exact(row["trust"], TRUST_KEYS, "trust")
    return doc
