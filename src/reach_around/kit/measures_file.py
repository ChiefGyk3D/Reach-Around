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
* the build binding (``kit-binding.json`` in the gated directory, see
  ``reach_around.kit.binding``) must match: the display policy, the resolved
  measures recomputed from the inputs given, the gated page and the public page
  must all be the ones one build wrote, or nothing is written;
* every string and number of the document is compared, decoded and normalized,
  with an inventory of the private-source values (each private field, each free
  text of each private measure, the not-for-PDF values) before it is serialized;
  a hit names the field, never the value.

The schema is versioned by ``schema_version`` and documented in ``docs/kit.md``.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from reach_around.config import SiteConfig
from reach_around.config import kit as kit_module
from reach_around.config.blocks import _PLATFORM_NAMES
from reach_around.config.imported_history import ImportedHistory
from reach_around.config.kit import (
    PDF_EXCLUDED,
    KitConfig,
    enforce_display_policy,
    gated_projection,
    policy_fingerprint,
)
from reach_around.config.measures import REGISTRY, UNITS, compile_measures
from reach_around.config.numbers import Numbers, source_platforms
from reach_around.config.trust import LABELS as TRUST_LABELS
from reach_around.kit.binding import (
    BINDING_FILE,
    BINDING_KEYS,
    check_binding_shape,
    measure_row,
    read_binding,
    rows_digest,
    sha256_text,
)
from reach_around.kit.output import JSON_NAME, confine_out_dir, write_private
from reach_around.site.kit import GATED_FILE, GUARD_MIN_LENGTH, KIT_PAGE

FILE_NAME = "kit-measures.json"
SCHEMA_VERSION = 1
KIND = "reach-around-kit-measures"
NOTICE = "private: owner-only, never publish or share this file"
MAX_BYTES = 4 * 1024 * 1024
MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_ROWS = 5000
MAX_DEPTH = 8
MAX_TEXT = 4000
MAX_VALUE = 10**15

TOP_KEYS = frozenset(
    {"schema_version", "kind", "notice", "generated", "policy", "binding", "measures"}
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
_SLUG = re.compile(r"[a-z0-9][a-z0-9_]{0,63}")
_HEX = re.compile(r"[0-9a-f]{64}")
_FREE_TEXT = ("label", "formula", "note", "provenance", "source_note")


class MeasuresFileError(ValueError):
    """The measures file may not be written, or is not a valid one."""


def measures_document(
    config: SiteConfig,
    numbers: Numbers | None,
    imported: ImportedHistory | None,
    generated: date,
    binding: Mapping[str, str] | None = None,
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
        "binding": None if binding is None else dict(binding),
        "measures": [measure_row(m) for m in shown],
    }


# --- the private-source inventory ------------------------------------------------


def _norm(text: str) -> str:
    """Comparison form: NFKC, case-folded, spaces and invisible characters
    removed. Applied to decoded values, never to serialized text."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        ch for ch in folded if not ch.isspace() and unicodedata.category(ch) != "Cf"
    )


def _leaves(value: Any, path: str) -> Iterable[tuple[str, str | int]]:
    """Every string and whole number inside a config value, with its field path."""
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, BaseModel):
        yield from _leaves(dict(value), path)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _leaves(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _leaves(item, path)
    elif isinstance(value, date):
        yield path, value.isoformat()
    elif isinstance(value, (str, int)):
        yield path, value
    elif isinstance(value, float) and math.isfinite(value) and value == int(value):
        yield path, int(value)


def _is_date(text: str) -> bool:
    try:
        return date.fromisoformat(text).isoformat() == text
    except ValueError:
        return False


def private_scalars(
    kit: KitConfig, numbers: Numbers | None, api: frozenset[str] | None = None
) -> list[tuple[str, str | int]]:
    """The inventory: every string and whole number whose source is private. These
    are the values of each config field the display policy marks private (or the
    PDF never prints), and every number and free text of each private measure.
    Values that are only names (a platform, a registry label, a label a visible
    measure also prints) or dates are left out: they say nothing."""
    measures = compile_measures(kit, numbers, api)
    plain = {_norm(entry.label) for entry in REGISTRY.values()}
    plain |= {_norm(key) for key in REGISTRY}
    plain |= {_norm(unit) for unit in UNITS}
    plain |= {_norm(name) for name in _PLATFORM_NAMES}
    plain |= {_norm(name) for name in _PLATFORM_NAMES.values()}
    public_text = {
        (m.key, name, _norm(getattr(m, name)))
        for m in measures
        if m.visibility != "private"
        for name in _FREE_TEXT
    }
    plain |= {text for _key, name, text in public_text if name == "label"}
    found: list[tuple[str, str | int]] = []
    for name, level in kit_module.DISPLAY_POLICY.items():
        if level == "private" or name in PDF_EXCLUDED:
            found.extend(_leaves(getattr(kit, name, None), f"kit.{name}"))
    for m in measures:
        if m.visibility != "private":
            continue
        where = f"kit.{m.platform}.{m.key}"
        if m.value is not None:
            found.append((f"{where}.value", m.value))
        for name in _FREE_TEXT:
            text = getattr(m, name)
            if text and (m.key, name, _norm(text)) not in public_text:
                found.append((f"{where}.{name}", text))
    out: list[tuple[str, str | int]] = []
    for path, value in found:
        if isinstance(value, str):
            flat = _norm(value)
            if not flat or flat in plain or _is_date(value):
                continue
            if len(flat) < GUARD_MIN_LENGTH and not path.endswith(_FREE_TEXT):
                continue
        elif len(str(abs(value))) < GUARD_MIN_LENGTH:
            continue
        out.append((path, value))
    return out


def _output_scalars(document: Any) -> Iterable[str | int]:
    """Every string and number the document holds, as decoded values."""
    if isinstance(document, bool) or document is None:
        return
    if isinstance(document, Mapping):
        for item in document.values():
            yield from _output_scalars(item)
    elif isinstance(document, (list, tuple)):
        for item in document:
            yield from _output_scalars(item)
    elif isinstance(document, (str, int)):
        yield document


def check_no_private_values(
    document: Mapping[str, Any], inventory: Sequence[tuple[str, str | int]]
) -> None:
    """Refuse a document that holds any inventory value, compared as decoded,
    normalized strings and as whole numbers (a number also as a whole digit run
    inside a text). Names the field, never the value."""
    strings = [_norm(v) for v in _output_scalars(document) if isinstance(v, str)]
    numbers = {v for v in _output_scalars(document) if isinstance(v, int)}
    for path, value in inventory:
        if isinstance(value, str):
            needle = _norm(value)
            hit = any(needle in text for text in strings)
        else:
            digits = re.compile(rf"(?<![0-9]){abs(value)}(?![0-9])")
            hit = value in numbers or any(digits.search(t) for t in strings)
        if hit:
            raise MeasuresFileError(
                f"a value of the private field {path} is in the measures file; "
                "nothing was written"
            )


# --- the build binding ------------------------------------------------------------


def _digest_file(path: Path, limit: int, what: str) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise MeasuresFileError(
            f"the {what} cannot be read; rebuild the site first "
            "(reach-around build --gated-out)"
        ) from exc
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise MeasuresFileError(f"the {what} is not a regular file")
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise MeasuresFileError(f"the {what} is too large")
    return sha256_text(data)


def check_binding(
    config: SiteConfig,
    *,
    site_dir: Path,
    gated_dir: Path,
    numbers: Numbers | None,
    imported: ImportedHistory | None,
) -> dict[str, str]:
    """Recompute the build binding from the inputs and directories given and
    compare it with the record the build wrote. Returns the binding; refuses on
    any difference."""
    kit = config.kit
    if kit is None:
        raise MeasuresFileError("the config has no media kit")
    try:
        recorded = read_binding(gated_dir / BINDING_FILE)
    except ValueError as exc:
        raise MeasuresFileError(str(exc)) from exc
    shown = gated_projection(kit, numbers, imported, source_platforms(config))[
        "measures"
    ]
    now = {
        "policy": policy_fingerprint(),
        "measures": rows_digest(shown),
        "gated_page": _digest_file(
            gated_dir / GATED_FILE, MAX_PAGE_BYTES, "gated page"
        ),
        "public_page": _digest_file(site_dir / KIT_PAGE, MAX_PAGE_BYTES, "public page"),
    }
    for key in BINDING_KEYS:
        if recorded[key] != now[key]:
            raise MeasuresFileError(
                f"the {key.replace('_', ' ')} is not the one the build wrote "
                "(a different build, other inputs, or a changed display policy); "
                "nothing was written. Rebuild the site, then run this again with "
                "the same inputs"
            )
    return recorded


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
    binding = check_binding(
        config,
        site_dir=site_dir,
        gated_dir=gated_dir,
        numbers=numbers,
        imported=imported,
    )
    document["binding"] = binding
    check_no_private_values(
        document, private_scalars(kit, numbers, source_platforms(config))
    )
    text = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    forbidden = [gated_dir, site_dir, *protect]
    confine_out_dir(out_dir, forbidden)
    return write_private(
        out_dir,
        FILE_NAME,
        text.encode("utf-8"),
        overwrite=overwrite,
        pattern=JSON_NAME,
        forbidden=forbidden,
    )


# --- the reader the sponsor page uses -----------------------------------------


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise MeasuresFileError(f"the measures file has a duplicate key {key!r}")
        out[key] = value
    return out


def _no_constant(name: str) -> Any:
    raise MeasuresFileError(f"the measures file holds {name}, which is not a number")


def _depth(raw: bytes) -> int:
    """The deepest bracket nesting, string contents skipped."""
    deepest = depth = 0
    in_text = escaped = False
    for byte in raw:
        if in_text:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_text = False
        elif byte == 0x22:
            in_text = True
        elif byte in (0x5B, 0x7B):
            depth += 1
            deepest = max(deepest, depth)
        elif byte in (0x5D, 0x7D):
            depth -= 1
    return deepest


def _exact(found: object, allowed: frozenset[str], where: str) -> dict[str, Any]:
    if not isinstance(found, dict):
        raise MeasuresFileError(f"the measures file {where} is not an object")
    extra = sorted(set(found) - allowed)
    missing = sorted(allowed - set(found))
    if extra or missing:
        raise MeasuresFileError(
            f"the measures file {where} has unexpected keys {extra} or lacks {missing}"
        )
    return found


def _text(found: Any, where: str, *, empty: bool, limit: int = MAX_TEXT) -> str:
    if not isinstance(found, str) or len(found) > limit or (not found and not empty):
        raise MeasuresFileError(f"the measures file {where} is not valid text")
    return found


def _day(found: Any, where: str) -> date:
    if not isinstance(found, str) or len(found) != 10:
        raise MeasuresFileError(f"the measures file {where} is not a YYYY-MM-DD date")
    try:
        day = date.fromisoformat(found)
    except ValueError:
        raise MeasuresFileError(
            f"the measures file {where} is not a YYYY-MM-DD date"
        ) from None
    if day.isoformat() != found:
        raise MeasuresFileError(f"the measures file {where} is not a YYYY-MM-DD date")
    return day


def _row(row: Any, number: int) -> None:
    where = f"measures row {number}"
    row = _exact(row, MEASURE_KEYS, where)
    for name in ("platform", "key"):
        if not isinstance(row[name], str) or not _SLUG.fullmatch(row[name]):
            raise MeasuresFileError(f"the measures file {where} {name} is not a slug")
    _text(row["label"], f"{where} label", empty=False, limit=200)
    if row["unit"] not in UNITS:
        raise MeasuresFileError(f"the measures file {where} unit is not a known unit")
    value = row["value"]
    if value is not None:
        if type(value) is not int or abs(value) > MAX_VALUE:
            raise MeasuresFileError(f"the measures file {where} value is not valid")
        if value < 0 and row["unit"] != "percent":
            raise MeasuresFileError(f"the measures file {where} value is negative")
    if row["as_of"] is not None:
        _day(row["as_of"], f"{where} as_of")
    period = row["period"]
    if period is not None:
        if not isinstance(period, list) or len(period) != 2:
            raise MeasuresFileError(f"the measures file {where} period is not valid")
        start = _day(period[0], f"{where} period")
        end = _day(period[1], f"{where} period")
        if start > end:
            raise MeasuresFileError(f"the measures file {where} period is reversed")
    if row["visibility"] not in ("public", "gated"):
        raise MeasuresFileError(
            f"the measures file {where} has a visibility it may not have"
        )
    if type(row["in_summary"]) is not bool:
        raise MeasuresFileError(f"the measures file {where} in_summary is not a bool")
    trust = row["trust"]
    if trust is not None:
        trust = _exact(trust, TRUST_KEYS, f"{where} trust")
        if trust["label"] not in TRUST_LABELS:
            raise MeasuresFileError(f"the measures file {where} trust label is unknown")
        _text(trust["method"], f"{where} trust method", empty=True, limit=500)
    for name in ("formula", "note", "provenance", "source_note"):
        _text(row[name], f"{where} {name}", empty=True)


def load_measures_file(
    path: Path, expected_binding: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Read and validate a measures file. The sponsor page reads measure values
    through this and nothing else. Every field is checked for type and format; a
    link, a large or deeply nested file, a duplicate or unknown key, NaN or
    Infinity, another version or a private row is refused. With
    ``expected_binding`` (the binding record of the build the page came from) a
    file made from another build is refused too."""
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
    if len(raw) > MAX_BYTES:
        raise MeasuresFileError("the measures file is too large")
    if _depth(raw) > MAX_DEPTH:
        raise MeasuresFileError("the measures file is nested too deeply")
    try:
        doc = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_no_duplicates,
            parse_constant=_no_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise MeasuresFileError("the measures file is not valid JSON") from exc
    doc = _exact(doc, TOP_KEYS, "top level")
    if (
        type(doc["schema_version"]) is not int
        or doc["schema_version"] != SCHEMA_VERSION
    ):
        raise MeasuresFileError(
            f"the measures file schema_version is not {SCHEMA_VERSION}"
        )
    if doc["kind"] != KIND or doc["notice"] != NOTICE:
        raise MeasuresFileError("the measures file is not a kit measures file")
    _day(doc["generated"], "generated")
    if not isinstance(doc["policy"], str) or not _HEX.fullmatch(doc["policy"]):
        raise MeasuresFileError("the measures file policy is not a digest")
    try:
        bound = check_binding_shape(doc["binding"])
    except ValueError as exc:
        raise MeasuresFileError(f"the measures file {exc}") from exc
    if bound["policy"] != doc["policy"]:
        raise MeasuresFileError("the measures file binding disagrees with its policy")
    if expected_binding is not None and dict(expected_binding) != bound:
        raise MeasuresFileError(
            "the measures file is not from the build it was expected to match"
        )
    rows = doc["measures"]
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ROWS:
        raise MeasuresFileError("the measures file has no valid list of measures")
    for number, row in enumerate(rows):
        _row(row, number)
    return doc
