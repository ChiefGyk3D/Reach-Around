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
Not here, on purpose: the Turnstile script tag. The widget slot is markup only,
so the pages still make no third-party request.
"""

from __future__ import annotations

import html
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

from reach_around.config.blocks import platform_name
from reach_around.config.cutover import cutover_label, live_since, utc_today
from reach_around.config.era import classify_era, era_text
from reach_around.config.kit import (
    KitConfig,
    ResolvedMetric,
    UnifiedTotal,
    gated_projection,
    kit_warnings,
    public_projection,
)
from reach_around.config.models import SiteConfig
from reach_around.config.numbers import Numbers, stale_limit
from reach_around.config.zones import ZONES, utc_day_start_iso

KIT_PAGE = Path("kit") / "index.html"
RETRY_PAGE = Path("kit") / "retry" / "index.html"
# The gated page's name inside the gated directory. No public file may use it.
GATED_FILE = Path("kit.html")

# Placeholder until the Worker route exists (D1, #263); then this becomes
# "/k/gate" in the change that adds the route. Until then a post lands on the
# static retry page, which fails closed.
GATE_ACTION = "/kit/retry/"
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
    for field, value in item.model_dump().items():
        for one in value if isinstance(value, list) else [value]:
            text = one.isoformat() if isinstance(one, date) else one
            if isinstance(text, str):
                found.append((field, text))
    return found


def _gated_strings(kit: KitConfig) -> list[tuple[str, str]]:
    """(where, text) for every gated value long enough to search for. Values
    that are not text (dates) are searched in the form the templates print."""
    found: list[tuple[str, str]] = []
    for name in ("partners", "case_studies", "services", "rates", "demographics"):
        for index, item in enumerate(getattr(kit, name)):
            for field, text in text_fields(item):
                if len(text) >= GUARD_MIN_LENGTH:
                    found.append((f"kit.{name}[{index}].{field}", text))
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
    for metric in kit.metrics:
        values.add(metric.label)
        if metric.as_of is not None:
            values.add(metric.as_of.isoformat())
    return {canonical(v) for v in values}


def check_no_gated_text(files: Mapping[Path, bytes], kit: KitConfig | None) -> None:
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
    for where, text in _gated_strings(kit):
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
    return [
        {
            "platform": "" if m.platform == "other" else platform_name(m.platform),
            "label": m.label,
            "value": f"{m.value:,}",
            "as_of": m.as_of.isoformat() if m.as_of is not None else "",
            "era": era_text(classify_era(m.as_of, live_day)),
            "stale": (
                config is not None
                and today is not None
                and m.as_of is not None
                and (today - m.as_of).days > stale_limit(config, m.platform)
            ),
        }
        for m in metrics
        if m.value is not None
    ]


def _total(
    total: UnifiedTotal, metrics: list[ResolvedMetric], live_day: date | None = None
) -> dict[str, Any] | None:
    if total.total is None:
        return None
    dates = [m.as_of for m in metrics if m.in_total and m.value is not None and m.as_of]
    return {
        "value": f"{total.total:,}",
        "counted": total.counted,
        "oldest": min(dates).isoformat() if dates else "",
        "mixed": len({classify_era(d, live_day) for d in dates} - {"unknown"}) > 1,
    }


def _content(
    projection: dict[str, Any],
    live_day: date | None = None,
    config: Any = None,
    today: date | None = None,
) -> dict[str, Any]:
    metrics: list[ResolvedMetric] = projection["metrics"]
    return {
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
        "contact": projection["contact"],
        "metrics": _metric_rows(metrics, config, today, live_day),
        "total": _total(projection["unified_total"], metrics, live_day),
    }


def _categories(kit: KitConfig) -> str:
    names = [
        label
        for label, items in (
            ("past partners", kit.partners),
            ("case studies", kit.case_studies),
            ("packages", kit.services),
            ("rates", kit.rates),
            ("audience details", kit.demographics),
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
            "packages, rates or demographics; no gate form or gated page is built"
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
) -> KitRender:
    """Render the kit. ``base(root, tab_name, gated)`` is the context every page shares
    (navigation, titles, footer flags); the kit adds its own content."""
    kit = config.kit
    if kit is None:
        return KitRender({}, None, [])
    warnings = plan_warnings(config, numbers, today)
    # A UTC day, not ``today`` (the scheduling day in the config's time zone).
    live_day = live_since(config, utc_today())
    public = _content(public_projection(kit, numbers), live_day, config, today)
    form: dict[str, Any] | None = None
    if has_gate_form(kit):
        form = {
            "action": GATE_ACTION,
            "site_key": kit.turnstile_site_key,
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
    if has_gate_form(kit):
        data = gated_projection(kit, numbers)
        content = _content(data, live_day, config, today)
        content.update(
            partners=data["partners"],
            case_studies=data["case_studies"],
            services=data["services"],
            rates=data["rates"],
            demographics=[
                {"label": d.label, "value": d.value, "as_of": d.as_of.isoformat()}
                for d in data["demographics"]
            ],
        )
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
