# SPDX-License-Identifier: Apache-2.0
"""Render the media kit: public pages, the gated page, and the wall between them.

Two audiences, two output roots. The public pages (``kit/index.html`` and
``kit/retry/index.html``) join the Pages output and are rendered only from
``public_projection``. The gated page is rendered from ``gated_projection`` into
a *separate* directory (``--gated-out``) that is never part of the Pages output;
the Worker, not Pages, serves it after the gate. The rule about which field goes
where is the display policy in ``reach_around.config.kit``, never a template.

The wall is structural and checked at build time:

* the gated directory may not be, contain or sit inside the public output;
* the gated page has a file name no public file may use;
* before anything is written, every gated string (partner, case study, rate and
  demographic text) is searched for in every public file, as written and as
  HTML-escaped. A hit fails the build (``KitLeakError``). Strings shorter than
  ``GUARD_MIN_LENGTH`` are skipped, since a short word would match ordinary text;
  the structural separation, not this search, is what keeps them out.

The CSP exception for the Turnstile widget lives here too (``gate_path_extras``,
#257): ``_headers`` gives ``https://challenges.cloudflare.com`` to ``script-src``
and ``frame-src`` on the two exact widget paths (``GATE_PATHS``), and only when
the kit has a gate form and a ``turnstile_site_key``. No other path gets it.
The same two pages, and only when a site key is set, carry the Turnstile script
tag (``TURNSTILE_SCRIPT``, implicit rendering, ``async defer``, no inline code):
it is their one third-party request. No other page and not the gated page loads
it (``tests/site/test_kit_csp.py::test_only_the_gate_pages_load_the_vendor_script``).
"""

from __future__ import annotations

import html
import math
import os
import re
import secrets
import unicodedata
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from jinja2 import Environment

from reach_around.config.blocks import Platforms, platform_name
from reach_around.config.cutover import cutover_label, live_since, utc_today
from reach_around.config.engagement import (
    ACROSS_PLATFORMS_NOTE,
    FORMULAS,
    NOT_AVAILABLE,
    rows_for,
)
from reach_around.config.era import SPANS, classify_era, classify_window, era_text
from reach_around.config.imported_history import (
    IMPORTED_HEADING,
    IMPORTED_NOTE,
    ImportedHistory,
    display_rows,
    imported_strings,
)
from reach_around.config.kit import (
    KitConfig,
    ResolvedMetric,
    UnifiedTotal,
    enforce_display_policy,
    gated_projection,
    kit_warnings,
    public_projection,
)
from reach_around.config.models import SiteConfig
from reach_around.config.numbers import (
    STALE_DAYS,
    Numbers,
    source_platforms,
    stale_limit,
)
from reach_around.config.trust import (
    LEGEND_HEADING,
    LEGEND_SENTENCES,
    LEGEND_SENTENCES_GATED,
    Trust,
    analytics_trust,
    case_trust,
    demographic_trust,
    engagement_trust,
    imported_trust,
    metric_trust,
    posts_trust,
    weakest,
)
from reach_around.config.zones import ZONES, utc_day_start_iso
from reach_around.funnel.floor import FLOOR, share_pins_small_count, shown_count
from reach_around.funnel.floor import UNDER_FLOOR as UNDER_FLOOR
from reach_around.site.platforms import platform_rows

KIT_PAGE = Path("kit") / "index.html"
RETRY_PAGE = Path("kit") / "retry" / "index.html"
# The gated page's name inside the gated directory. No public file may use it.
GATED_FILE = Path("kit.html")

# The gate form posts to the Worker route (worker/src/kit/gate.ts, GATE_ROUTE, #263).
# Every refusal there redirects to the retry page, which carries the same form.
GATE_ACTION = "/k/gate"
# The business inquiry form posts to the Worker route (worker/src/kit/inquiry.ts).
# It is on the gated page only, which the Worker serves to an unlocked visitor.
INQUIRY_ACTION = "/k/inquiry"
# The Worker's own caps for the form fields (INQUIRY_LIMITS in the same file; a test
# keeps the two equal). Printed as maxlength so the browser enforces them first.
INQUIRY_LIMITS = {"name": 80, "email": 254, "brand": 80, "budget": 40, "details": 800}

GUARD_MIN_LENGTH = 6

# The one third-party origin the gate pages may load from. Cloudflare's
# Turnstile client-side docs (CSP reference page, last updated May 5, 2026,
# read 2026-11-07) list exactly ``script-src`` and ``frame-src`` for this
# origin and nothing for ``connect-src`` (verified). ``connect-src`` stays
# ``'self'``: add the origin there only if a browser run names a violation.
TURNSTILE_ORIGIN = "https://challenges.cloudflare.com"
# The client script, loaded with implicit rendering: it finds the ``cf-turnstile``
# container (with its site key and action) and adds the ``cf-turnstile-response``
# field to the enclosing form. Implicit rendering needs no script of our own and no
# inline code, so the policy needs nothing beyond the origin above. Only the gate
# pages carry the tag, and only when a site key is set.
TURNSTILE_SCRIPT = f"{TURNSTILE_ORIGIN}/turnstile/v0/api.js"
# The pages that render the widget, as ``_headers`` paths. Exact paths, no
# splat: nothing else under /kit/ and no other page is relaxed.
GATE_PATHS = ("/kit/", "/kit/retry/")


class KitLeakError(ValueError):
    """Gated text was found in the public output; the build is refused."""


@dataclass(frozen=True)
class KitRender:
    public: dict[Path, str]
    gated: str | None
    warnings: list[str]


def has_gate_form(kit: KitConfig) -> bool:
    """The gate form, the retry page and the gated page exist together, and only
    when the kit has gated content (which the config allows only with
    ``features.kit_gate`` on)."""
    return kit.has_gated_content


def public_paths(config: SiteConfig) -> set[Path]:
    """Every HTML file the kit adds to the public output."""
    if config.kit is None:
        return set()
    paths = {KIT_PAGE}
    if has_gate_form(config.kit):
        paths.add(RETRY_PAGE)
    return paths


def gate_path_extras(config: SiteConfig) -> dict[str, dict[str, list[str]]]:
    """The CSP extra for the gate pages: the Turnstile origin in ``script-src``
    and ``frame-src``, on the pages that render the widget and no other.

    Empty unless the kit has a gate form and a site key (without a key the
    widget slot stays empty and nothing third-party is needed).
    """
    kit = config.kit
    if kit is None or not has_gate_form(kit) or not kit.turnstile_site_key:
        return {}
    return {
        path: {"script-src": [TURNSTILE_ORIGIN], "frame-src": [TURNSTILE_ORIGIN]}
        for path in GATE_PATHS
    }


# --- the wall ----------------------------------------------------------------


def _chain(path: Path) -> set[tuple[int, int]]:
    """Filesystem identities (device, inode) of ``path`` and every existing
    ancestor, after following symbolic links. Identity, not spelling, so an
    alias (a link, a bind mount, a case-folded name) of one directory matches."""
    found: set[tuple[int, int]] = set()
    current = Path(os.path.realpath(path))
    for candidate in (current, *current.parents):
        try:
            info = os.stat(candidate)
        except OSError:
            continue
        found.add((info.st_dev, info.st_ino))
    return found


def _own(path: Path) -> tuple[int, int] | None:
    try:
        info = os.stat(os.path.realpath(path))
    except OSError:
        return None
    return (info.st_dev, info.st_ino)


def check_roots(out_dir: Path, gated_out: Path) -> None:
    """Refuse a gated directory that is, contains or sits inside the public one.

    Compared by filesystem identity as well as by resolved path, so links and
    other aliases of one directory are caught. Also refuses a public output that
    already holds an entry with the gated file's name, of any kind."""
    problem = (
        f"gated output directory {gated_out} is the public output directory "
        f"{out_dir}, inside it or around it; the gated page must never be "
        "published, so choose a separate directory"
    )
    out, gated = Path(os.path.realpath(out_dir)), Path(os.path.realpath(gated_out))
    if gated == out or out in gated.parents or gated in out.parents:
        raise ValueError(problem)
    out_id, gated_id = _own(out_dir), _own(gated_out)
    if (out_id is not None and out_id in _chain(gated_out)) or (
        gated_id is not None and gated_id in _chain(out_dir)
    ):
        raise ValueError(problem)
    if os.path.lexists(out_dir / GATED_FILE):
        raise ValueError(
            f"the public output {out_dir} already holds an entry named "
            f"{GATED_FILE}, which is reserved for the gated page; remove it"
        )


def text_fields(item: Any) -> list[tuple[str, str]]:
    """(field, text) for every printed string of one list entry: a string, a
    date in the form the templates print, or each string of a list of strings."""
    found: list[tuple[str, str]] = []

    def visit(field: str, value: Any) -> None:
        if isinstance(value, dict):
            for name, child in value.items():
                visit(f"{field}.{name}", child)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                # A list of plain strings keeps the bare field name (callers
                # filter on it, for example "thumbnails"); only structured
                # entries get an index.
                structured = isinstance(child, (dict, list))
                visit(f"{field}[{index}]" if structured else field, child)
        else:
            text = value.isoformat() if isinstance(value, date) else value
            if isinstance(text, str):
                found.append((field, text))

    for field, value in item.model_dump().items():
        visit(field, value)
    return found


def _gated_strings(
    kit: KitConfig, imported: ImportedHistory | None = None
) -> list[tuple[str, str]]:
    """Leak canaries for gated values.

    Ordinary prose must be long enough to search safely. Every engagement value
    is covered, including short numbers, through a labeled rendering that avoids
    collisions with unrelated public numbers and dates. The imported-history
    numbers are covered the same way (``imported_strings``) unless the owner
    made the block public.
    """
    found: list[tuple[str, str]] = []
    if not kit.imported_history_public:
        found.extend(imported_strings(imported))
    if kit.disclosure is not None and len(kit.disclosure) >= GUARD_MIN_LENGTH:
        found.append(("kit.disclosure", kit.disclosure))
    for name in (
        "partners",
        "case_studies",
        "services",
        "rates",
        "engagement",
        "testimonials",
        "demographics",
    ):
        for index, item in enumerate(getattr(kit, name)):
            if name == "engagement":
                continue
            for field, text in text_fields(item):
                # Platform slugs are public vocabulary used by the site's icon
                # CSS, not an audience figure. Searching one would make every
                # demographic row collide with the public stylesheet.
                if name == "demographics" and field == "platform":
                    continue
                if len(text) >= GUARD_MIN_LENGTH:
                    found.append((f"kit.{name}[{index}].{field}", text))
    for index, item in enumerate(kit.engagement):
        prefix = f"kit.engagement[{index}]"
        dumped = item.model_dump()
        for field in ("views", "posts", "interactions"):
            value = dumped[field]
            if value is None:
                continue
            for rendered in (str(value), f"{value:,}"):
                found.append((f"{prefix}.{field}", f"{rendered} {field}"))
                found.append((f"{prefix}.{field}", f"{field} {rendered}"))
        for field, label in (
            ("period_start", "period start"),
            ("period_end", "period end"),
            ("as_of", "as of"),
        ):
            value = dumped[field]
            if value is not None:
                found.append((f"{prefix}.{field}", f"{label} {value.isoformat()}"))
        if item.counts:
            found.append((f"{prefix}.counts", item.counts))
        rows = rows_for([item])
        for row_index, row in enumerate(rows):
            derived = "average_views" if row_index == 0 else "engagement_rate"
            found.append((f"{prefix}.{derived}", f"{row['measure']} {row['value']}"))
            found.append((f"{prefix}.{derived}_formula", row["formula"]))
            if row["provenance"]:
                found.append((f"{prefix}.owner_supplied", row["provenance"]))
    for index, item in enumerate(kit.case_studies):
        prefix = f"kit.case_studies[{index}]"
        for field in ("views", "likes", "comments"):
            value = getattr(item, field)
            for rendered in (str(value), f"{value:,}"):
                found.append((f"{prefix}.{field}", f"{rendered} {field}"))
                found.append((f"{prefix}.{field}", f"{field} {rendered}"))
    if kit.inquiry is not None:
        for field, text in kit.inquiry.model_dump().items():
            if isinstance(text, str) and len(text) >= GUARD_MIN_LENGTH:
                found.append((f"kit.inquiry.{field}", text))
    return found


_TAG = re.compile(r"<[^>]*>")
_ESC_U = re.compile(r"\\u([0-9a-fA-F]{4})")
_ESC_X = re.compile(r"\\x([0-9a-fA-F]{2})")
_ESC_CSS = re.compile(r"\\([0-9a-fA-F]{1,6})[ \t\n\r\f]?")
_ESC_SIMPLE = re.compile(r'\\(["\\/nrt])')
_SIMPLE_MAP = {"n": "\n", "r": "\r", "t": "\t"}
_SPACE = re.compile(r"\s+")

# Decoding repeats until the text stops changing, at most this many rounds.
DECODE_ROUNDS = 10
# Known text types are always canonicalized; one with a NUL byte or invalid
# UTF-8 fails the build. Every other file is sniffed by content: valid UTF-8
# with no NUL byte in the first chunk is canonicalized; anything else is binary
# and is searched only for the raw bytes of each gated value (UTF-8, UTF-16
# little and big endian).
TEXT_SUFFIXES = frozenset(
    {
        ".html",
        ".htm",
        ".css",
        ".js",
        ".mjs",
        ".json",
        ".xml",
        ".svg",
        ".txt",
        ".webmanifest",
        ".xhtml",
        ".map",
    }
)
TEXT_NAMES = frozenset({"_headers", "_redirects"})
SNIFF_BYTES = 8192
# Work limits for the canonical search; a build over either limit stops with an
# error that names the limit.
GUARD_MAX_BYTES = 64 * 1024 * 1024
GUARD_MAX_FILE_BYTES = 8 * 1024 * 1024
GUARD_MAX_BINARY_BYTES = 512 * 1024 * 1024
GUARD_MAX_NEEDLES = 1000


class KitGuardLimitError(ValueError):
    """The leak search would do more work than its limits allow."""


def _code_point(value: int) -> str:
    return chr(value) if 0 < value <= 0x10FFFF else "�"


def _decode_once(text: str) -> str:
    text = html.unescape(text)
    text = urllib.parse.unquote(text)
    text = _ESC_U.sub(lambda m: chr(int(m.group(1), 16)), text)
    text = _ESC_X.sub(lambda m: chr(int(m.group(1), 16)), text)
    text = _ESC_CSS.sub(lambda m: _code_point(int(m.group(1), 16)), text)
    return _ESC_SIMPLE.sub(lambda m: _SIMPLE_MAP.get(m.group(1), m.group(1)), text)


def canonical(text: str, tags: str = "") -> str:
    """A comparison form: entities, percent-encoding and JSON, JavaScript and CSS
    escapes decoded to a fixed point (at most ``DECODE_ROUNDS`` rounds), tags
    replaced by ``tags``, Unicode NFKC, case-folded, all whitespace removed."""
    for _ in range(DECODE_ROUNDS):
        decoded = _decode_once(text)
        if decoded == text:
            break
        text = decoded
    text = _TAG.sub(tags, text)
    text = unicodedata.normalize("NFKC", text).casefold()
    # drop invisible format characters (zero-width, joiners, bidi controls,
    # soft hyphen) so they cannot split a value
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return _SPACE.sub("", text)


class KitSuspiciousFileError(ValueError):
    """A public text file holds NUL bytes or is not valid UTF-8."""


def _is_known_text(rel: Path) -> bool:
    return rel.suffix.lower() in TEXT_SUFFIXES or rel.name in TEXT_NAMES


def _strict_text(rel: Path, data: bytes) -> str:
    """The text of a file whose type is known to be text. Such a file is always
    canonicalized, never downgraded by its content; one with a NUL byte or
    invalid UTF-8 fails the build instead."""
    reason = "contains a NUL byte" if b"\x00" in data else ""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        reason = reason or "is not valid UTF-8"
        text = ""
    if reason:
        raise KitSuspiciousFileError(
            f"the public file {rel.as_posix()} is a text type but {reason}; a "
            "text file like that is not expected in the public tree. Remove it "
            "or fix its encoding."
        )
    return text


def _as_text(data: bytes) -> str | None:
    """The text of a file of unknown type if its content is text (see
    ``SNIFF_BYTES``), else None (searched for raw bytes only)."""
    if b"\x00" in data[:SNIFF_BYTES]:
        return None
    return data.decode("utf-8", errors="replace")


def _public_values(kit: KitConfig) -> set[str]:
    """Canonical forms of the values that are public by policy; a gated value
    that equals one of them is not a leak."""
    values = {kit.title, kit.about}
    if kit.contact is not None:
        values.add(kit.contact)
    values.update(kit.tags)
    for metric in kit.metrics:
        values.add(metric.label)
        if metric.as_of is not None:
            values.add(metric.as_of.isoformat())
    for analytics in kit.analytics:
        values.add(analytics.synced_with)
        values.add(analytics.as_of.isoformat())
        values.update(stat.label for stat in analytics.stats)
    return {canonical(v) for v in values}


def _is_whole_element(text: str, page: str) -> bool:
    """Whether ``text`` is the entire content of some element in ``page``.

    A short gated value (under ``GUARD_MIN_LENGTH``) has no useful substring
    search: it would match inside any word. A full element match is the
    collision that matters (the same short text printed as its own line), so
    that is what is searched for, as written and HTML-escaped."""
    for variant in {text, html.escape(text), html.escape(text, quote=False)}:
        if re.search(r">\s*" + re.escape(variant) + r"\s*<", page):
            return True
    return False


def check_no_gated_text(
    files: Mapping[Path, bytes],
    kit: KitConfig | None,
    imported: ImportedHistory | None = None,
) -> None:
    """Raise ``KitLeakError`` if a gated file name or string is in ``files``.

    Defense in depth: the separate output roots are the boundary. This search
    compares the raw and HTML-escaped bytes and the canonical forms (see
    ``canonical``), also with markup removed, so equivalent encodings and values
    split by tags are caught. It does not claim to catch every transformation."""
    if GATED_FILE in files:
        raise KitLeakError(f"the public output would contain {GATED_FILE}")
    if kit is None:
        return
    allowed = _public_values(kit)
    # The owner may print the inquiry address in the public contact line.
    contact = canonical(kit.contact) if kit.contact is not None else ""
    raw: list[tuple[str, bytes]] = []
    folded: list[tuple[str, str]] = []
    for where, text in _gated_strings(kit, imported):
        if canonical(text) in allowed:
            continue
        if where == "kit.inquiry.email" and canonical(text) in contact:
            continue
        for variant in {text, html.escape(text), html.escape(text, quote=False)}:
            for codec in ("utf-8", "utf-16-le", "utf-16-be"):
                raw.append((where, variant.encode(codec)))
        folded.append((where, canonical(text)))
    if len(folded) > GUARD_MAX_NEEDLES:
        raise KitGuardLimitError(
            f"the kit has {len(folded)} gated values to search for, over the "
            f"limit of {GUARD_MAX_NEEDLES} (GUARD_MAX_NEEDLES)"
        )
    short = kit.disclosure
    if short is not None and len(short) >= GUARD_MIN_LENGTH:
        short = None
    scanned = 0
    binary = 0
    for rel, data in sorted(files.items()):
        for where, needle in raw:
            if needle in data:
                raise _leak(where, rel)
        decoded: str | None
        if _is_known_text(rel):
            decoded = _strict_text(rel, data)
        else:
            decoded = _as_text(data)
        if decoded is None:
            binary += len(data)
            if binary > GUARD_MAX_BINARY_BYTES:
                raise KitGuardLimitError(
                    f"the public binary files total more than "
                    f"{GUARD_MAX_BINARY_BYTES} bytes, over the leak search limit "
                    "(GUARD_MAX_BINARY_BYTES)"
                )
            continue
        if short is not None and _is_whole_element(short, decoded):
            raise _leak("kit.disclosure", rel)
        if len(data) > GUARD_MAX_FILE_BYTES:
            raise KitGuardLimitError(
                f"the public text file {rel.as_posix()} is over the leak search "
                f"limit of {GUARD_MAX_FILE_BYTES} bytes (GUARD_MAX_FILE_BYTES)"
            )
        scanned += len(data)
        if scanned > GUARD_MAX_BYTES:
            raise KitGuardLimitError(
                f"the public text files total more than {GUARD_MAX_BYTES} bytes, "
                "over the leak search limit (GUARD_MAX_BYTES)"
            )
        for tags in ("", " "):
            view = canonical(decoded, tags)
            for where, needle_text in folded:
                if needle_text in view:
                    raise _leak(where, rel)


def _leak(where: str, rel: Path) -> KitLeakError:
    return KitLeakError(
        f"gated text from {where} would appear in the public file "
        f"{rel.as_posix()}; gated text may only go to the gated "
        "output. Reword the public text or the gated entry."
    )


@dataclass
class StagedGated:
    """A gated page written to a temporary name in the gated root, or a pending
    removal. ``commit`` makes it the real file; ``discard`` removes the temp."""

    root: Path
    temp: Path | None

    def commit(self) -> None:
        target = self.root / GATED_FILE
        if self.temp is None:
            if os.path.lexists(target):
                target.unlink()
            return
        try:
            os.replace(self.temp, target)
        except OSError:
            self.discard()
            raise
        self.temp = None

    def discard(self) -> None:
        if self.temp is not None:
            self.temp.unlink(missing_ok=True)
            self.temp = None


def check_private_root(gated_out: Path) -> None:
    """Refuse an existing gated directory that group or others can enter."""
    if gated_out.is_dir() and gated_out.stat().st_mode & 0o077:
        raise ValueError(
            f"the gated output directory {gated_out} has permissions that "
            "let other users in; the gated page is private. Run "
            f"chmod 700 {gated_out} and build again."
        )


_STAGING = re.compile(rf"\.{re.escape(GATED_FILE.name)}\.[0-9a-f]{{16}}\.tmp")


def _remove_stale_staging(gated_out: Path) -> None:
    """Delete staging files a crashed build left behind: regular files whose
    name is exactly the temporary pattern, nothing else."""
    for entry in gated_out.iterdir():
        if _STAGING.fullmatch(entry.name) and not entry.is_symlink():
            if entry.is_file():
                entry.unlink()


class KitRemoteRequestError(ValueError):
    """The gated page would make a request to another host."""


_REQUEST_ATTRS: dict[str, tuple[str, ...]] = {
    "img": ("src", "srcset"),
    "script": ("src",),
    "iframe": ("src",),
    "source": ("src", "srcset"),
    "video": ("src", "poster"),
    "audio": ("src",),
    "embed": ("src",),
    "object": ("data",),
    "track": ("src",),
    "input": ("src",),
    "link": ("href",),
    "base": ("href",),
    "form": ("action",),
    "frame": ("src",),
    "image": ("href", "xlink:href"),
    "use": ("href", "xlink:href"),
}
_REMOTE_URL = re.compile(r"\s*(?:[a-zA-Z][a-zA-Z0-9+.-]*:)?//|\s*https?:", re.I)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_CSS_REMOTE = re.compile(
    r"url\(\s*[\"']?\s*(?:https?:|//)|[\"']\s*(?:https?:)?//[^\s\"']", re.I
)


def _css_is_remote(css: str) -> bool:
    """True if a stylesheet imports anything or names an http(s) or
    protocol-relative target, after CSS escapes are decoded."""
    text = _CSS_COMMENT.sub("", css)
    for _ in range(DECODE_ROUNDS):
        decoded = _ESC_CSS.sub(lambda m: _code_point(int(m.group(1), 16)), text)
        if decoded == text:
            break
        text = decoded
    return "@import" in text.lower() or bool(_CSS_REMOTE.search(text))


class _Requests(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.remote: list[str] = []
        self.styles: list[str] = []
        self.sheets: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "meta" and "refresh" in (values.get("http-equiv") or "").lower():
            target = re.search(r"url\s*=\s*(.*)", values.get("content") or "", re.I)
            if target and _REMOTE_URL.match(target.group(1).strip("'\" ")):
                self.remote.append("<meta http-equiv=refresh>")
        for name in _REQUEST_ATTRS.get(tag, ()):
            value = values.get(name)
            if value is None:
                continue
            candidates = (
                [part.strip().split(" ")[0] for part in value.split(",")]
                if name == "srcset"
                else [value]
            )
            if any(_REMOTE_URL.match(item) for item in candidates):
                self.remote.append(f"<{tag} {name}>")
            elif tag == "link" and "stylesheet" in (values.get("rel") or ""):
                self.sheets.append(value.strip())
        if values.get("style"):
            self.styles.append(values["style"] or "")
        self._in_style = tag == "style"

    def handle_endtag(self, tag: str) -> None:
        self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self.styles.append(data)


def check_gated_requests(page: str, files: Mapping[Path, bytes]) -> None:
    """Refuse a gated page that would make a request to another host.

    The page and every local stylesheet it links are checked: a request-bearing
    element or a stylesheet that imports or names an http(s) or protocol-relative
    target fails the build. The message names the file or element, never the
    address."""
    parser = _Requests()
    parser.feed(page)
    parser.close()
    if parser.remote:
        raise KitRemoteRequestError(
            f"the gated page ({GATED_FILE}) would make a request to another host "
            f"from {parser.remote[0]}; the gated page makes no third-party "
            "request. Use a local file."
        )
    if any(_css_is_remote(text) for text in parser.styles):
        raise KitRemoteRequestError(
            f"the gated page ({GATED_FILE}) has inline style that imports or "
            "loads from another host; use a local file."
        )
    for href in parser.sheets:
        rel = Path(href.split("?")[0].split("#")[0].lstrip("/"))
        data = files.get(rel)
        if data is not None and _css_is_remote(data.decode("utf-8", errors="ignore")):
            raise KitRemoteRequestError(
                f"the stylesheet {rel.as_posix()}, which the gated page loads, "
                "imports or loads from another host; the gated page makes no "
                "third-party request. Use local files only."
            )


def stage_gated(gated_out: Path, page: str | None) -> StagedGated:
    """Prepare the gated page without touching the destination name.

    A symbolic link at the destination is refused. The page is written to a new
    file (never following a link, owner-only) beside the destination and renamed
    into place by ``commit``, after the public tree is written."""
    target = gated_out / GATED_FILE
    if target.is_symlink():
        raise ValueError(
            f"{target} is a symbolic link; the gated page is never written "
            "through a link. Remove it and build again."
        )
    check_private_root(gated_out)
    if gated_out.is_dir():
        _remove_stale_staging(gated_out)
    if page is None:
        return StagedGated(gated_out, None)
    if not gated_out.exists():
        gated_out.mkdir(parents=True, mode=0o700)
    temp = gated_out / f".{GATED_FILE.name}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(page.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return StagedGated(gated_out, temp)


# --- content -----------------------------------------------------------------


def _metric_rows(
    metrics: list[ResolvedMetric],
    config: Any = None,
    today: date | None = None,
    live_day: date | None = None,
) -> list[dict[str, Any]]:
    # Without a config the provenance is unknown, so no label is attached (the
    # PDF text check then expects none).
    api = source_platforms(config) if config is not None else frozenset()
    return [
        {
            "platform": "" if m.platform == "other" else platform_name(m.platform),
            "label": m.label,
            "value": shown_count(m.value),
            "as_of": m.as_of.isoformat() if m.as_of is not None else "",
            "era": era_text(classify_era(m.as_of, live_day)),
            "stale": _is_stale(m.as_of, today, config, m.platform),
            "trust": (None if config is None else _metric_trust(m, api).view()),
        }
        for m in metrics
        if m.value is not None
    ]


def _metric_trust(metric: ResolvedMetric, api: frozenset[str]) -> Trust:
    return metric_trust(
        metric.platform,
        metric.source,
        metric.estimate,
        api,
        metric.origin,
        metric.as_of,
    )


def _is_stale(
    as_of: date | None, today: date | None, config: Any, platform: str | None
) -> bool:
    """Older than the platform's stale limit (the rule ``kit_warnings`` and the
    link page use); the default limit for a figure with no platform."""
    if as_of is None or today is None:
        return False
    if config is None:
        return False
    limit = STALE_DAYS if platform is None else stale_limit(config, platform)
    return (today - as_of).days > limit


# The first-screen range line (spec 6.5, decision 4). Both bounds are plain
# arithmetic on the numbers the total already adds up; never one deduplicated
# figure. NEW PUBLIC STRING: marked in the PR for Codex authoring.
RANGE_SENTENCE = "Unique people are somewhere between {low} and {high}."


def range_text(metrics: list[ResolvedMetric], total: UnifiedTotal) -> str | None:
    """The range line, or None when fewer than two numbers above zero are added
    up (one number has no range, and a zero would repeat the total). A set
    cannot be smaller than its largest member, so the largest counted number is
    the lower bound and the total the upper."""
    if total.total is None or total.counted < 2:
        return None
    counted = [m.value for m in metrics if m.in_total and m.value]
    if len(counted) < 2:
        return None
    return RANGE_SENTENCE.format(
        low=shown_count(max(counted)), high=shown_count(total.total)
    )


def _total(
    total: UnifiedTotal,
    metrics: list[ResolvedMetric],
    live_day: date | None = None,
    config: Any = None,
    today: date | None = None,
) -> dict[str, Any] | None:
    if total.total is None:
        return None
    # A printed total minus the printed members would isolate a member under
    # the floor, so one such member hides the total.
    if any(m.in_total and m.value is not None and 0 < m.value < FLOOR for m in metrics):
        return None
    dates = [m.as_of for m in metrics if m.in_total and m.value is not None and m.as_of]
    trust: dict[str, str] | None = None
    stale = False
    if config is not None:
        api = source_platforms(config)
        counted = [m for m in metrics if m.in_total and m.value is not None]
        weak = weakest(_metric_trust(m, api) for m in counted)
        trust = None if weak is None else weak.view()
        stale = any(_is_stale(m.as_of, today, config, m.platform) for m in counted)
    return {
        "trust": trust,
        "stale": stale,
        "value": shown_count(total.total),
        "range_text": range_text(metrics, total),
        "counted": total.counted,
        "oldest": min(dates).isoformat() if dates else "",
        "mixed": len({classify_era(d, live_day) for d in dates} - {"unknown"}) > 1,
    }


def _imported_block(
    history: ImportedHistory | None, live_day: date | None
) -> dict[str, Any] | None:
    """The block "Before Reach Around": its own rows, never part of a total."""
    rows = display_rows(history, live_day)
    if not rows or history is None:
        return None
    ends = [
        p.end
        for p in history.periods
        if classify_window(p.start, p.end, live_day) not in (SPANS, "after")
    ]
    # Closed past periods: they are history, so no out-of-date mark.
    return {
        "heading": IMPORTED_HEADING,
        "note": IMPORTED_NOTE,
        "rows": rows,
        "trust": imported_trust(history.note, max(ends)).view(),
    }


def platform_counts(
    config: Any, numbers: Numbers | None, day: date
) -> dict[str, list[int]]:
    """The counts the link page prints beside each platform, by platform.

    Typed counts and numbers-file counts, resolved the way the page resolves
    them (a fresh file value wins). A count under the floor prints as "Under 5"
    and is no base. The link page prints the exact count too, so the true count
    is what a reader can recover.
    """
    found: dict[str, list[int]] = {}
    for tab in config.tabs:
        for block in tab.blocks:
            if not isinstance(block, Platforms):
                continue
            rows = platform_rows(block, numbers, day, config)
            for entry, row in zip(block.items, rows, strict=True):
                if row.count is not None and row.count >= FLOOR:
                    found.setdefault(entry.platform, []).append(row.count)
    return found


def audience_bases(
    platform: str,
    metrics: Any,
    analytics: Any,
    link_page: Mapping[str, list[int]] | None = None,
) -> list[int]:
    """Every count of one platform that is printed at 5 or more, anywhere a
    reader of the kit can see it: the kit's headline numbers (typed or from the
    numbers file), the followers of its analytics tab, and the count beside it
    on the link page. A rounded form (1.2k) is read at its true value: the
    share rule is monotone, so the true count is never below what the reader can
    rule in, and a cell the reader could not call small is never hidden for it."""
    found = [
        m.value
        for m in metrics
        if m.platform == platform and m.value is not None and m.value >= FLOOR
    ]
    found += [
        row.followers
        for row in analytics
        if row.platform == platform and row.followers >= FLOOR
    ]
    found += (link_page or {}).get(platform, [])
    return found


def demographic_withheld(demo: Any, bases: list[int]) -> frozenset[str]:
    """The groups of one demographics entry that would pin a small count.

    A whole-number share printed beside a count of the same platform tells the
    reader how many people the cell holds. A group with such a cell is withheld
    whole, because its shares total 100 and one printed cell's complement would
    give the hidden one back. Groups: gender, ages, countries, cities.
    """

    def pinned(percent: int, base: int) -> bool:
        return share_pins_small_count(percent, 100, base)

    hidden: set[str] = set()
    for base in bases:
        gender = demo.gender
        if pinned(gender.male, base) or pinned(gender.female, base):
            hidden.add("gender")
        for age in demo.ages:
            if pinned(age.all, base):
                hidden.add("ages")
            for sex, share in (("male", gender.male), ("female", gender.female)):
                most_of_sex = base * (2 * share + 1) // 200
                if pinned(getattr(age, sex), most_of_sex):
                    hidden.add("ages")
        for name in ("countries", "cities"):
            if any(pinned(place.percentage, base) for place in getattr(demo, name)):
                hidden.add(name)
    return frozenset(hidden)


def _post_rows(recent: list[Any]) -> dict[str, list[dict[str, Any]]]:
    posts: dict[str, list[dict[str, Any]]] = {}
    for post in recent:
        posts.setdefault(platform_name(post.platform), []).append(
            {
                "thumbnail": post.thumbnail,
                "views": shown_count(post.views),
                "likes": shown_count(post.likes),
                "comments": shown_count(post.comments),
                "reposts": None if post.reposts is None else shown_count(post.reposts),
                "quotes": None if post.quotes is None else shown_count(post.quotes),
            }
        )
    return posts


def _post_trust(recent: list[Any]) -> dict[str, dict[str, str]]:
    """One label per platform group of recent posts."""
    return {
        name: posts_trust().view()
        for name in dict.fromkeys(platform_name(post.platform) for post in recent)
    }


def _case_rows(cases: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "trust": case_trust(case.period_start, case.period_end).view(),
            "thumbnails": case.thumbnails,
            "views": shown_count(case.views),
            "likes": shown_count(case.likes),
            "comments": shown_count(case.comments),
            "period_start": case.period_start.isoformat(),
            "period_end": case.period_end.isoformat(),
        }
        for case in cases
    ]


def _engagement_rows(
    entries: list[Any], config: Any, today: date | None
) -> list[dict[str, Any]]:
    """The printed engagement rows, each figure with its label and stale mark.
    A row that prints ``not available`` has no number, so no label."""
    out: list[dict[str, Any]] = []
    for entry in entries:
        trust = engagement_trust().view()
        stale = _is_stale(entry.as_of, today, config, entry.platform)
        for row in rows_for([entry]):
            has_number = row["value"] != NOT_AVAILABLE
            out.append(
                {
                    **row,
                    "trust": trust if has_number else None,
                    "stale": has_number and stale,
                }
            )
    return out


def _legend(content: dict[str, Any], gated: bool = False) -> dict[str, Any] | None:
    """The legend, printed once on every page that prints a labeled number."""
    labeled = (
        any(row["trust"] for row in content["metrics"])
        or content["analytics"]
        or (content["recent_posts"] and not gated)
        or any(row["trust"] for row in content["engagement"])
        or content["imported"]
        or content.get("case_studies")
        or content.get("demographics")
    )
    if not labeled:
        return None
    sentences = LEGEND_SENTENCES_GATED if gated else LEGEND_SENTENCES
    return {"heading": LEGEND_HEADING, "sentences": list(sentences)}


def _content(
    projection: dict[str, Any],
    live_day: date | None = None,
    config: Any = None,
    today: date | None = None,
) -> dict[str, Any]:
    metrics: list[ResolvedMetric] = projection["metrics"]
    posts = _post_rows(projection.get("recent_posts", []))
    content = {
        "live_since": (
            {
                "iso": live_day.isoformat(),
                "text": cutover_label(live_day),
                "start": utc_day_start_iso(live_day),
            }
            if live_day is not None
            else None
        ),
        "title": projection["title"],
        "about": projection["about"],
        "photo": projection["photo"],
        "contact": projection["contact"],
        "tags": projection["tags"],
        "public_pdf": projection["public_pdf"],
        "metrics": _metric_rows(metrics, config, today, live_day),
        "analytics": _analytics_rows(projection["analytics"], today, config),
        "recent_posts": posts,
        "recent_trust": _post_trust(projection.get("recent_posts", [])),
        "engagement": _engagement_rows(projection.get("engagement", []), config, today),
        "engagement_note": ACROSS_PLATFORMS_NOTE,
        "engagement_formulas": FORMULAS,
        "total": _total(projection["unified_total"], metrics, live_day, config, today),
        "imported": _imported_block(projection.get("imported_history"), live_day),
    }
    content["legend"] = _legend(content)
    return content


def _compact_number(value: int) -> str:
    if 0 < value < FLOOR:
        return UNDER_FLOOR
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def _change_text(followers: int, percent: float) -> str:
    """The 30-day change, or "" when it would pin a count under the floor.

    The reader sees the text, one decimal, and can work back to an earlier
    count. Every whole earlier count whose own change prints as this very text
    is checked, so a typed 66.72 (printed +66.7%) is tested as 66.7.
    """
    # Zero is withheld with the small counts: the text "-100.0%" fits every
    # earlier count, so it tells a reader nothing, and the safe side costs
    # nothing.
    if followers < FLOOR:
        return ""
    text = f"{percent:+.1f}%"
    shown = float(text[:-1])
    low, high = shown - 0.05 - 1e-6, shown + 0.05 + 1e-6
    if 1 + low / 100 > 0:
        first = math.ceil(followers / (1 + high / 100))
        last = math.floor(followers / (1 + low / 100))
        if any(0 < n < FLOOR for n in range(max(first, 0), min(last, FLOOR) + 1)):
            return ""
    return text


def _analytics_rows(
    rows: list[Any], today: date | None, config: Any = None
) -> list[dict[str, Any]]:
    return [
        {
            "trust": analytics_trust(row.synced_with).view(),
            "stale": _is_stale(row.as_of, today, config, row.platform),
            "platform": row.platform,
            "platform_name": platform_name(row.platform),
            "synced_with": row.synced_with,
            "as_of": row.as_of.isoformat(),
            "followers": _compact_number(row.followers),
            "delta": _change_text(row.followers, row.followers_30_day_percent),
            "stats": [
                {"label": stat.label, "value": _compact_number(stat.value)}
                for stat in row.stats
            ],
            "updated": today.isoformat() if today is not None else "",
        }
        for row in rows
    ]


def _categories(kit: KitConfig) -> str:
    names = [
        label
        for label, items in (
            ("past partners", kit.partners),
            ("case studies", kit.case_studies),
            ("packages", kit.services),
            ("rates", kit.rates),
            ("audience figures", kit.engagement),
            (
                "link-page history from the previous provider",
                [kit.imported_history]
                if kit.imported_history and not kit.imported_history_public
                else [],
            ),
            ("testimonials", kit.testimonials),
            ("audience details", kit.demographics),
            ("a disclosure statement", [kit.disclosure] if kit.disclosure else []),
        )
        if items
    ]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def plan_warnings(
    config: SiteConfig, numbers: Numbers | None, today: date
) -> list[str]:
    kit = config.kit
    if kit is None:
        return []
    limits = dict(config.numbers.stale_days) if config.numbers is not None else None
    found = kit_warnings(kit, numbers, today, limits)
    if config.features.kit_gate and not kit.has_gated_content:
        found.append(
            "features.kit_gate is on but the kit has no partners, case studies, "
            "packages, rates, engagement, testimonials, demographics or disclosure; "
            "no gate form or gated page is built"
        )
    if has_gate_form(kit) and kit.turnstile_site_key is None:
        found.append(
            "kit.turnstile_site_key is not set; the gate form has an empty widget slot"
        )
    return found


BaseContext = Callable[[str, str, bool], dict[str, Any]]


def render_kit(
    env: Environment,
    config: SiteConfig,
    numbers: Numbers | None,
    today: date,
    base: BaseContext,
    imported: ImportedHistory | None = None,
) -> KitRender:
    """Render the kit. ``base(root, tab_name, gated)`` is the context every page shares
    (navigation, titles, footer flags); the kit adds its own content."""
    kit = config.kit
    if kit is None:
        return KitRender({}, None, [])
    enforce_display_policy(kit)
    warnings = plan_warnings(config, numbers, today)
    # A UTC day, not ``today`` (the scheduling day in the config's time zone).
    live_day = live_since(config, utc_today())
    public = _content(
        public_projection(kit, numbers, imported), live_day, config, today
    )
    form: dict[str, Any] | None = None
    if has_gate_form(kit):
        form = {
            "action": GATE_ACTION,
            "site_key": kit.turnstile_site_key,
            "script": TURNSTILE_SCRIPT,
            "categories": _categories(kit),
        }
    pages: dict[Path, str] = {}
    ctx = base("../", "Media kit", False)
    pages[KIT_PAGE] = env.get_template("kit.html").render(
        **{
            **ctx,
            "root": "../",
            "kit": public,
            "form": form,
            "zone_choices": ZONES if public["metrics"] else None,
        }
    )
    if form is not None:
        ctx = base("../../", "Try again", False)
        pages[RETRY_PAGE] = env.get_template("kit_retry.html").render(
            **{**ctx, "root": "../../", "kit": public, "form": form, "noindex": True}
        )
    gated: str | None = None
    if has_gate_form(kit) or kit.engagement:
        data = gated_projection(kit, numbers, imported)
        if kit.engagement and not data["engagement"]:
            data["engagement"] = kit.engagement
        content = _content(data, live_day, config, today)
        link_counts = platform_counts(config, numbers, today)
        content.update(
            partners=data["partners"],
            case_studies=_case_rows(data["case_studies"]),
            services=data["services"],
            rates=data["rates"],
            testimonials=data["testimonials"],
            disclosure=data["disclosure"],
            demographics=[
                {
                    **d.model_dump(),
                    "platform_name": platform_name(d.platform),
                    "as_of": d.as_of.isoformat(),
                    "trust": demographic_trust(d.as_of).view(),
                    "stale": _is_stale(d.as_of, today, config, d.platform),
                    "withheld": sorted(
                        demographic_withheld(
                            d,
                            audience_bases(
                                d.platform,
                                data["metrics"],
                                data["analytics"],
                                link_counts,
                            ),
                        )
                    ),
                }
                for d in data["demographics"]
            ],
        )
        content["legend"] = _legend(content, gated=True)
        inquiry: dict[str, Any] | None = None
        if data["inquiry"] is not None:
            inquiry = {
                "action": INQUIRY_ACTION,
                "email": data["inquiry"].email,
                "pgp_fingerprint": data["inquiry"].pgp_fingerprint,
                "limits": INQUIRY_LIMITS,
            }
        ctx = base("/", "Media kit details", True)
        gated = env.get_template("kit_gated.html").render(
            **{**ctx, "root": "/", "kit": content, "inquiry": inquiry, "noindex": True}
        )
    return KitRender(pages, gated, warnings)
