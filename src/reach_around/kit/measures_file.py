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
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from decimal import Decimal
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
    AnchoredReadError,
    GatedLockError,
    acquire_gated_lock,
    check_binding_shape,
    check_no_pending_commit,
    measure_row,
    read_anchored,
    read_binding,
    require_absent,
    rows_digest,
    rows_digest_of,
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
    binding: Mapping[str, str | None] | None = None,
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


Scalar = str | int | Decimal


def _leaves(value: Any, path: str) -> Iterable[tuple[str, Scalar]]:
    """Every string and finite number inside a config value, with its field path.
    A number is an ``int`` or a ``Decimal`` made from the float's shortest text,
    so 12.3 stays 12.3."""
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
    elif isinstance(value, float) and math.isfinite(value):
        yield path, Decimal(repr(value))
    elif isinstance(value, Decimal) and value.is_finite():
        yield path, value


def _is_date(text: str) -> bool:
    try:
        return date.fromisoformat(text).isoformat() == text
    except ValueError:
        return False


def private_scalars(
    kit: KitConfig,
    numbers: Numbers | None,
    api: frozenset[str] | None = None,
    imported: ImportedHistory | None = None,
) -> list[tuple[str, Scalar]]:
    """The inventory: every string and number whose source is private. These
    are the values of each config field the display policy marks private (or the
    PDF never prints), read from the same sources the projection reads (the
    loaded imported history included), and every number and free text of each
    private measure.
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
    found: list[tuple[str, Scalar]] = []
    for name, level in kit_module.DISPLAY_POLICY.items():
        source: Any = getattr(kit, name, None)
        if name == "imported_history":
            # the loaded numbers, never the path, at the level the config gives
            level = kit_module.imported_level(kit)
            source = imported
        if level == "private" or name in PDF_EXCLUDED:
            found.extend(_leaves(source, f"kit.{name}"))
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
    out: list[tuple[str, Scalar]] = []
    for path, value in found:
        if isinstance(value, str):
            flat = _norm(value)
            if not flat or flat in plain or _is_date(value):
                continue
            if len(flat) < GUARD_MIN_LENGTH and not path.endswith(_FREE_TEXT):
                continue
        out.append((path, value))
    return out


def is_short(value: Scalar) -> bool:
    """A whole number of fewer than ``GUARD_MIN_LENGTH`` digits. It is in the
    inventory, but a bare short number is too common to mean anything, so it is
    matched in a text only when written with a unit."""
    return (
        not isinstance(value, str)
        and _whole(value)
        and len(str(abs(int(value)))) < GUARD_MIN_LENGTH
    )


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


def _whole(value: Scalar) -> bool:
    return isinstance(value, int) or (
        isinstance(value, Decimal) and value == value.to_integral_value()
    )


_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
# An exponent written as superscript digits right after a 10 ("1.23 x 10¹"); NFKC
# alone would turn it into a digit glued to the 10.
_SUPER_EXPONENT = re.compile(r"(?<=10)[ \t]*([⁺⁻]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+)")
# Marks other locales use that NFKC leaves alone: the Arabic decimal and thousands
# separators and the Unicode minus.
_LOCALE_MARKS = str.maketrans({"٫": ".", "٬": ",", "−": "-"})


def _fold_text(text: str) -> str:
    """A text as a reader sees it: superscript exponents written out, Unicode NFKC
    (fullwidth digits, signs and separators become plain ones) with the invisible
    format characters (zero width joiners and the like) removed, every other
    script's decimal digit (Arabic-Indic, Devanagari and so on) written as the
    plain digit it means, and the Arabic decimal and thousands separators and the
    Unicode minus written as the plain mark."""
    text = _SUPER_EXPONENT.sub(lambda m: "^" + m.group(1).translate(_SUPERSCRIPT), text)
    folded = unicodedata.normalize("NFKC", text).translate(_LOCALE_MARKS)
    return "".join(
        str(unicodedata.decimal(ch)) if unicodedata.category(ch) == "Nd" else ch
        for ch in folded
        if unicodedata.category(ch) != "Cf"
    )


# A number as people write it in a text: digits, optional thousands separators
# (a comma, a space or a no-break space) and an optional decimal point. A
# trailing percent sign or any other character is not part of the number, and no
# punctuation is removed from the text.
_SEPARATORS = ",   "
# Any Unicode space, a comma or an apostrophe also separates thousands.
_SEPARATOR = rf"[{_SEPARATORS}‘‛ʻ′`´'’ʼ\s]"
_GROUPED = re.compile(
    rf"(?<![0-9.,])[0-9]{{1,3}}(?:{_SEPARATOR}[0-9]{{3}})+(?:\.[0-9]+)?(?![0-9])"
)
_PLAIN = re.compile(r"[0-9]+(?:\.[0-9]+)?")
# A decimal as other conventions write it: a comma or a period with spaces
# around it ("12,3", "12 .3", "12. 3"). Both parts are digit runs; read as the
# decimal they could mean. A false stop ("12. 3 more") is the price of the rule.
_DECIMAL_MARK = "[.,·'’]"
_SPLIT_DECIMAL = re.compile(
    rf"(?<![0-9.,])([0-9]+)[ \t]*{_DECIMAL_MARK}[ \t]*([0-9]+)(?![0-9])"
)
# Groups written one way and a decimal the other: "1.234,5", "1 234,5", "1,234 . 5".
# Read with the last mark as the decimal point.
_GROUPED_DECIMAL = re.compile(
    rf"(?<![0-9.,])[0-9]{{1,3}}(?:{_SEPARATOR}|\.)[0-9]{{3}}"
    rf"(?:(?:{_SEPARATOR}|\.)[0-9]{{3}})*[ \t]*[.,][ \t]*[0-9]+(?![0-9])"
)
# The Indian grouping: "12,34,567.5".
_INDIAN = re.compile(
    r"(?<![0-9.,])[0-9]{1,2}(?:,[0-9]{2})+,[0-9]{3}(?:\.[0-9]+)?(?![0-9])"
)
# Scientific notation: 1.23e1, 1.23 E+1, 1.23x10^1, 1.23 * 10 ** 1, 1.23×10^1,
# 1 . 23e1, 1.23 x 10^(1), 1.23 times 10^1 (a superscript exponent is folded to ^).
_SCIENTIFIC = re.compile(
    rf"(?<![0-9.,])([0-9]+(?:[ \t]*{_DECIMAL_MARK}[ \t]*[0-9]+)?)[ \t]*"
    r"(?:[eE]|(?:[xX×✕✖*·⋅∙]|times)[ \t]*10[ \t]*(?:\^|\*\*))"
    r"[ \t]*\(?[ \t]*([+-]?[0-9]{1,4})(?![0-9])"
)
MAX_EXPONENT = 40


# Digit groups joined by exactly ONE separator that is not a line break ("1234
# 567"): read as one number as well. "123, 456" (two characters between) and
# "123" newline-newline "456" are two numbers, never joined.
_JOIN_SEPARATOR = (
    r"(?:[,'‘’‛ʻʼ′`´]"
    r"|[^\S\n\r\v\f\x85  ])"
)
_JOINED = re.compile(
    rf"(?<![0-9.,])[0-9]+(?:{_JOIN_SEPARATOR}[0-9]+)+(?:\.[0-9]+)?(?![0-9])"
)


def _spans(folded: str) -> list[tuple[int, int, Decimal, str]]:
    """Every number in a folded text as (start, end, value, kind). The kind is
    "grouped" (true thousands groups), "joined" (digit groups with one separator
    between), "plain" (one digit run), "decimal" (a decimal written with a comma
    or spaces) or "scientific" (a number with an exponent). The one place a text
    is read as numbers, so the long-figure match and the short-figure match see
    the same spans."""
    spans: list[tuple[int, int, Decimal, str]] = []
    for kind, pattern in (("grouped", _GROUPED), ("joined", _JOINED)):
        for match in pattern.finditer(folded):
            value = Decimal(re.sub(_SEPARATOR, "", match.group()))
            spans.append((match.start(), match.end(), value, kind))
    for match in _PLAIN.finditer(folded):
        spans.append((match.start(), match.end(), Decimal(match.group()), "plain"))
    for match in _SPLIT_DECIMAL.finditer(folded):
        value = Decimal(f"{match.group(1)}.{match.group(2)}")
        spans.append((match.start(), match.end(), value, "decimal"))
    for match in _GROUPED_DECIMAL.finditer(folded):
        text = re.sub(r"[ \t]+", "", match.group())
        last = max(text.rfind("."), text.rfind(","))
        digits = re.sub(rf"{_SEPARATOR}|\.|,", "", text[:last])
        spans.append(
            (
                match.start(),
                match.end(),
                Decimal(f"{digits}.{text[last + 1 :]}"),
                "decimal",
            )
        )
    for match in _INDIAN.finditer(folded):
        value = Decimal(match.group().replace(",", ""))
        spans.append((match.start(), match.end(), value, "decimal"))
    for match in _SCIENTIFIC.finditer(folded):
        exponent = int(match.group(2))
        if abs(exponent) > MAX_EXPONENT:
            continue
        marked = re.sub(_DECIMAL_MARK, ".", match.group(1))
        mantissa = Decimal(re.sub(r"[ \t]+", "", marked))
        spans.append(
            (match.start(), match.end(), mantissa.scaleb(exponent), "scientific")
        )
    return spans


def digit_runs(text: str) -> set[int]:
    """Whole numbers as a fail-closed reader sees them: the text folded, then
    every separator between two digits removed, whatever its length or kind (a
    comma, a period used as a group mark, any space or line break, an
    apostrophe), and the digit runs left read as whole numbers. Letters are not
    separators, so "123 apples, 456 pears" stays two numbers. This also reads
    "123, 456" as 123456: that false stop is the price of the rule."""
    folded = _fold_text(text)
    closed = re.sub(rf"(?<=[0-9])(?:{_SEPARATOR}|\.)+(?=[0-9])", "", folded)
    return {int(run) for run in re.findall(r"[0-9]+", closed)}


def number_tokens(text: str) -> set[Decimal]:
    """The magnitudes of the numbers a text holds, each read every way a reader
    could: as one grouped number ("1,234" is 1234), as digit groups joined by a
    single separator ("1234 567" is 1234567) and as separate plain numbers (1 and
    234). The text is folded first (NFKC, invisible characters dropped). A sign
    is not part of a magnitude, so -12.30 and 12.3 are the same number."""
    return {value for _s, _e, value, _w in _spans(_fold_text(text))}


# What makes a short number a figure: a percent sign, a currency sign or code
# before or after it, or a word that counts an audience or an amount.
_CURRENCY = r"(?:[$€£¥₹]|\b(?:usd|eur|gbp|cad|aud|jpy)\b)"
UNIT_WORDS = (
    "follows follow percent followers follower subscribers subscriber "
    "views view viewers viewer "
    "hours hour minutes minute seconds second posts post episodes episode "
    "downloads download clicks click members member likes like comments comment "
    "shares share plays play videos video impressions impression interactions "
    "interaction emails email listeners listener fans fan users user"
).split()
_UNITS = "|".join(UNIT_WORDS)
_AFTER = re.compile(rf"\s*(?:%|{_CURRENCY}|\b(?:{_UNITS})\b)", re.IGNORECASE)
# a currency, then an optional sign: "$3", "$-3", "USD -3"
_BEFORE = re.compile(rf"{_CURRENCY}\s*[-+−]?\s*$", re.IGNORECASE)


def unit_tokens(text: str) -> set[Decimal]:
    """The magnitudes of the numbers a text writes with a unit (see
    ``is_short``). Numbers are read by the same spans as ``number_tokens`` (so
    "1,234 followers" and "1 234 followers" are 1234, and the 234 inside them is
    not a number of its own). "3 platforms" is not one; "3%", "$3", "$-3", "3 USD"
    and "3 followers" are."""
    folded = _fold_text(text)
    spans = _spans(folded)
    # only true thousands groups hide their parts; the parts of a joined span
    # ("20 40 subscribers") are numbers of their own as well
    inside = [(s, e) for s, e, _v, kind in spans if kind == "grouped"]
    found: set[Decimal] = set()
    for start, end, value, kind in spans:
        if kind == "plain" and any(s <= start and end <= e for s, e in inside):
            continue
        if _AFTER.match(folded, end) or _BEFORE.search(folded[:start]):
            found.add(value)
    return found


def row_figures(document: Any) -> set[Decimal]:
    """The magnitudes of the numeric ``value`` of every measure row that has a
    recognized unit (a percent is stored in tenths). A bare number elsewhere has
    no unit; a row's value always has one."""
    found: set[Decimal] = set()
    if isinstance(document, Mapping):
        value = document.get("value")
        unit = document.get("unit")
        if type(value) is int and unit in UNITS:
            magnitude = abs(Decimal(value))
            found.add(magnitude / 10 if unit == "percent" else magnitude)
        for item in document.values():
            found |= row_figures(item)
    elif isinstance(document, (list, tuple)):
        for item in document:
            found |= row_figures(item)
    return found


def check_no_private_values(
    document: Mapping[str, Any], inventory: Sequence[tuple[str, Scalar]]
) -> None:
    """Refuse a document that holds any inventory value, compared as decoded,
    normalized strings and as numbers (a number also as a number written in a
    text, with or without thousands separators; 12.3 and 12.30 are one number).
    Names the field, never the value."""
    values = list(_output_scalars(document))
    strings = [_norm(v) for v in values if isinstance(v, str)]
    numbers = {Decimal(abs(v)) for v in values if isinstance(v, int)}
    figures: set[Decimal] = row_figures(document)
    runs: set[int] = set()
    for text in values:
        if isinstance(text, str):
            numbers |= number_tokens(text)
            figures |= unit_tokens(text)
            runs |= digit_runs(text)
    for path, value in inventory:
        if isinstance(value, str):
            needle = _norm(value)
            hit = any(needle in text for text in strings)
        elif is_short(value):
            hit = abs(Decimal(value)) in figures
        else:
            # a row's figure is the number the row shows (a percent row stores
            # tenths), so it is compared against the decoded figures as well
            magnitude = abs(Decimal(value))
            hit = magnitude in numbers or magnitude in figures
            if not hit and _whole(value):
                # fail closed: a whole number of 6 or more digits is also
                # looked for with every separator removed
                hit = abs(int(value)) in runs
        if hit:
            raise MeasuresFileError(
                f"a value of the private field {path} is in the measures file; "
                "nothing was written. Reword the text that shows it so the "
                "number does not appear, in any spacing"
            )


# --- the build binding ------------------------------------------------------------


def _digest_file(path: Path, limit: int, what: str, lock_fd: int | None = None) -> str:
    try:
        data = read_anchored(path, limit, lock_fd)
    except AnchoredReadError as exc:
        raise MeasuresFileError(
            f"the {what} cannot be read ({exc}); rebuild the site first "
            "(reach-around build --gated-out)"
        ) from exc
    return sha256_text(data)


def check_binding(
    config: SiteConfig,
    *,
    site_dir: Path,
    gated_dir: Path,
    lock_fd: int,
    numbers: Numbers | None,
    imported: ImportedHistory | None,
) -> dict[str, str | None]:
    """Recompute the build binding from the inputs and directories given and
    compare it with the record the build wrote. Returns the binding; refuses on
    any difference. A build of a kit with no gated content records no gated page
    (null); the gated directory must then hold none either."""
    kit = config.kit
    if kit is None:
        raise MeasuresFileError("the config has no media kit")
    try:
        check_no_pending_commit(gated_dir, lock_fd)
        recorded = read_binding(gated_dir / BINDING_FILE, lock_fd)
    except ValueError as exc:
        raise MeasuresFileError(str(exc)) from exc
    shown = gated_projection(kit, numbers, imported, source_platforms(config))[
        "measures"
    ]
    gated_page: str | None = None
    if recorded["gated_page"] is not None:
        gated_page = _digest_file(
            gated_dir / GATED_FILE, MAX_PAGE_BYTES, "gated page", lock_fd
        )
    else:
        # a kit with no gated page: the directory must hold none. The name is
        # looked up through the held directory chain, and the caller holds the
        # gated lock, so a build cannot add a page between this and the write
        try:
            require_absent(gated_dir / GATED_FILE, lock_fd)
        except AnchoredReadError as exc:
            raise MeasuresFileError(
                f"the build records no gated page but the gated directory is not "
                f"clean ({exc}); rebuild the site first "
                "(reach-around build --gated-out)"
            ) from exc
    now = {
        "policy": policy_fingerprint(),
        "measures": rows_digest(shown),
        "gated_page": gated_page,
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
    try:
        lock = acquire_gated_lock(gated_dir, exclusive=False)
    except GatedLockError as exc:
        raise MeasuresFileError(str(exc)) from exc
    try:
        return _build_locked(
            config,
            kit,
            document,
            site_dir=site_dir,
            gated_dir=gated_dir,
            out_dir=out_dir,
            overwrite=overwrite,
            protect=protect,
            numbers=numbers,
            imported=imported,
            lock_fd=lock,
        )
    finally:
        os.close(lock)


def _build_locked(
    config: SiteConfig,
    kit: KitConfig,
    document: dict[str, Any],
    *,
    site_dir: Path,
    gated_dir: Path,
    out_dir: Path,
    overwrite: bool,
    protect: Sequence[Path],
    numbers: Numbers | None,
    imported: ImportedHistory | None,
    lock_fd: int,
) -> Path:
    """The checks and the write of ``build_measures_file``, with the shared lock
    of the gated directory held by the caller."""
    binding = check_binding(
        config,
        site_dir=site_dir,
        gated_dir=gated_dir,
        lock_fd=lock_fd,
        numbers=numbers,
        imported=imported,
    )
    document["binding"] = binding
    check_no_private_values(
        document, private_scalars(kit, numbers, source_platforms(config), imported)
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


ECHO_KEY = 40
ECHO_KEYS = 5


def _shown(key: str) -> str:
    """A key from the file, safe to put in an error: escaped to ASCII (no control
    or bidirectional characters) and cut short, so a hostile file cannot flood a
    log through its own keys."""
    text = ascii(key)
    return text if len(text) <= ECHO_KEY else text[:ECHO_KEY] + "..."


def _shown_all(keys: list[str]) -> str:
    more = len(keys) - ECHO_KEYS
    text = ", ".join(_shown(key) for key in keys[:ECHO_KEYS])
    return f"[{text}]" if more <= 0 else f"[{text}] and {more} more"


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise MeasuresFileError(
                f"the measures file has a duplicate key {_shown(key)}"
            )
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
            f"the measures file {where} has unexpected keys {_shown_all(extra)} "
            f"or lacks {_shown_all(missing)}"
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
    path: Path, expected_binding: Mapping[str, str | None]
) -> dict[str, Any]:
    """Read and validate a measures file. The sponsor page reads measure values
    through this and nothing else. Every field is checked for type and format; a
    link, a large or deeply nested file, a duplicate or unknown key, NaN or
    Infinity, another version or a private row is refused.

    ``expected_binding`` is required and has no default: it is the binding record
    of the build the page came from, loaded from the build's own gated directory
    (see ``load_measures_for_build``), never from the measures file. The binding
    stored inside the file is only a claim: anyone who can edit the file can
    recompute every digest in it. So the file is refused unless its binding equals
    the independent record, and the rows (recomputed here) digest to that
    record's ``measures`` digest. This holds against an editor of the measures
    file who cannot also write the gated directory; see docs/kit.md."""
    try:
        raw = read_anchored(path, MAX_BYTES)
    except AnchoredReadError as exc:
        raise MeasuresFileError(f"the measures file cannot be read ({exc})") from exc
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
    try:
        trusted = check_binding_shape(dict(expected_binding))
    except ValueError as exc:
        raise MeasuresFileError(f"the expected {exc}") from exc
    if trusted != bound:
        raise MeasuresFileError(
            "the measures file is not from the build it was expected to match"
        )
    rows = doc["measures"]
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ROWS:
        raise MeasuresFileError("the measures file has no valid list of measures")
    for number, row in enumerate(rows):
        _row(row, number)
    # The binding authenticates only itself: the rows being consumed must digest
    # to what the build recorded, or an edited value would carry a genuine binding.
    if rows_digest_of(rows) != trusted["measures"]:
        raise MeasuresFileError(
            "the measures file rows are not the measures its binding records; "
            "the file was changed after the build. Make it again"
        )
    return doc


def load_measures_for_build(path: Path, gated_dir: Path) -> dict[str, Any]:
    """The reader for consumers: the expected binding is read here from
    ``kit-binding.json`` in the build's gated directory, a different file in a
    different directory than the measures file, so an edit of the measures file
    alone (rows and its own copy of the binding together) is refused. A missing,
    linked or malformed binding record fails closed."""
    try:
        lock = acquire_gated_lock(gated_dir, exclusive=False)
    except GatedLockError as exc:
        raise MeasuresFileError(str(exc)) from exc
    try:
        try:
            check_no_pending_commit(gated_dir, lock)
            trusted = read_binding(gated_dir / BINDING_FILE, lock)
        except ValueError as exc:
            raise MeasuresFileError(str(exc)) from exc
        return load_measures_file(path, expected_binding=trusted)
    finally:
        os.close(lock)
