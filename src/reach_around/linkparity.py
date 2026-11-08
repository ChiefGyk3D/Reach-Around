# SPDX-License-Identifier: Apache-2.0
"""Compare the links in a built site with an owner-supplied export.

Stdlib only. Nothing here touches the network: a redirect is known only if the
owner lists it in a redirect map. Results carry counts and ids, never a URL or
a label, because the output is pasted into a public pull request.

The scope of the comparison is a fixed list (see ``SCOPE``). An anchor outside
it, such as the skip link, the footer links or the tab bar, is not compared.
"""

from __future__ import annotations

import csv
import io
import json
import os
import posixpath
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 10_000
MAX_PAGES = 500

# The anchor classes the comparison covers, with where each is collected.
# "once" kinds repeat on every tab, so they are read from the root page only.
SCOPE: dict[str, bool] = {"social": True, "link": False, "profile-button": True}

_TRACKING_EXACT = {"fbclid", "gclid", "dclid", "msclkid", "igshid", "mc_cid", "mc_eid"}
_DEFAULT_PORTS = {"http": 80, "https": 443}


class ParityError(Exception):
    """The comparison cannot be made (exit 2). The message never holds a value."""


@dataclass(frozen=True)
class Entry:
    ref: str  # an id or a position, never a URL or label
    url: str  # normalized
    label: str  # normalized


@dataclass
class SiteLinks:
    entries: list[Entry] = field(default_factory=list)
    internal: int = 0
    pages: int = 0
    icon_row_differs: int = 0


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = (
        host
        if port is None or port == _DEFAULT_PORTS.get(scheme)
        else (f"{host}:{port}")
    )
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not (k.lower().startswith("utm_") or k.lower() in _TRACKING_EXACT)
    ]
    return urlunsplit((scheme, netloc, parts.path or "/", urlencode(query), ""))


def normalize_label(label: str) -> str:
    return " ".join(label.split()).casefold()


def _is_external(url: str) -> bool:
    return url.lower().startswith(("http://", "https://"))


def _valid_url(url: object) -> bool:
    if not isinstance(url, str) or not _is_external(url):
        return False
    try:
        parts = urlsplit(url.strip())
        if parts.username is not None or parts.password is not None:
            return False  # userinfo would be dropped by normalization
        return bool(parts.hostname) and parts.port != 0
    except ValueError:  # a bad port or bracketed host
        return False


# ---- reading files ---------------------------------------------------------


def _read_text(path: Path, what: str) -> str:
    try:
        if path.is_symlink() or not path.is_file():
            raise ParityError(f"{what} is not a regular file")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ParityError(f"{what} is larger than {MAX_FILE_BYTES // 1024} KiB")
        return path.read_bytes().decode("utf-8-sig")
    except OSError:
        raise ParityError(f"{what} cannot be read") from None
    except UnicodeDecodeError:
        raise ParityError(f"{what} is not UTF-8 text") from None


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError("duplicate key")
        seen[key] = value
    return seen


def _load_json(text: str, what: str) -> Any:
    try:
        return json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except (ValueError, RecursionError):
        raise ParityError(f"{what} is not valid JSON (or repeats a key)") from None


def _rows_from_csv(text: str, what: str, columns: tuple[str, str]) -> list[list[str]]:
    reader = csv.reader(io.StringIO(text))
    try:
        rows = list(reader)
    except csv.Error:
        raise ParityError(f"{what} is not valid CSV") from None
    if not rows:
        raise ParityError(f"{what} is empty")
    header = [c.strip().lower() for c in rows[0]]
    if header != list(columns):
        raise ParityError(f"{what} needs the header row: {','.join(columns)}")
    return rows[1:]


def _pairs_from_json(
    data: Any, what: str, keys: tuple[str, str], *, wrapper: str | None
) -> list[list[Any]]:
    if wrapper and isinstance(data, dict):
        data = data.get(wrapper)
    if not isinstance(data, list):
        raise ParityError(f"{what} must be a list of objects")
    out: list[list[Any]] = []
    for item in data:
        if not isinstance(item, dict):
            out.append([None, None])
        else:
            out.append([item.get(keys[0]), item.get(keys[1])])
    return out


def _load_pairs(
    path: Path, what: str, keys: tuple[str, str], wrapper: str | None
) -> list[list[Any]]:
    text = _read_text(path, what)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        rows = _rows_from_csv(text, what, keys)
        out: list[list[Any]] = []
        for row in rows:
            if not row:
                continue
            out.append(row if len(row) == 2 else [None, None])
        return out
    if suffix == ".json":
        return _pairs_from_json(_load_json(text, what), what, keys, wrapper=wrapper)
    raise ParityError(f"{what} must be a .csv or .json file")


def load_expected(path: Path) -> list[Entry]:
    """The owner's export: rows of ``url`` and ``label`` (see docs/parity.md)."""
    pairs = _load_pairs(path, "the export", ("url", "label"), "links")
    if not pairs:
        raise ParityError("the export has no links")
    if len(pairs) > MAX_ROWS:
        raise ParityError(f"the export has more than {MAX_ROWS} rows")
    entries: list[Entry] = []
    for number, (url, label) in enumerate(pairs, start=1):
        row = number + 1 if path.suffix.lower() == ".csv" else number
        if not _valid_url(url):
            raise ParityError(f"export row {row}: url must be an http or https URL")
        if not isinstance(label, str) or not label.strip():
            raise ParityError(f"export row {row}: label must be non-empty text")
        entries.append(
            Entry(f"export#{number}", normalize_url(str(url)), normalize_label(label))
        )
    return entries


def load_redirects(path: Path) -> dict[str, set[str]]:
    """Owner-listed redirects (``from``, ``to``), made two-way for matching."""
    pairs = _load_pairs(path, "the redirect map", ("from", "to"), None)
    if len(pairs) > MAX_ROWS:
        raise ParityError(f"the redirect map has more than {MAX_ROWS} rows")
    graph: dict[str, set[str]] = {}
    for number, (src, dst) in enumerate(pairs, start=1):
        if not _valid_url(src) or not _valid_url(dst):
            raise ParityError(f"redirect row {number}: both must be http(s) URLs")
        a, b = normalize_url(str(src)), normalize_url(str(dst))
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)
    return graph


# ---- reading the built site -------------------------------------------------


@dataclass
class _Raw:
    kind: str
    href: str
    ident: str
    text: str


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[_Raw] = []
        self.is_tab = False
        self._cur: _Raw | None = None
        self._span: list[bool] = []  # per open span: is it a link-title
        self._title_text: list[str] = []
        self._all_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        classes = a.get("class", "").split()
        if (tag == "ul" and "blocks" in classes) or (tag == "p" and "empty" in classes):
            self.is_tab = True  # tab.html and sections.html emit one of these
        if tag == "a" and self._cur is None:
            kind = next((k for k in SCOPE if k in classes), "")
            if kind:
                self._cur = _Raw(kind, a.get("href", ""), a.get("data-link-id", ""), "")
                self._title_text, self._all_text = [], []
        elif tag == "span" and self._cur is not None:
            self._span.append("link-title" in classes)

    def handle_endtag(self, tag: str) -> None:
        if tag == "span" and self._span:
            self._span.pop()
        elif tag == "a" and self._cur is not None:
            parts = self._title_text if self._cur.kind == "link" else self._all_text
            self._cur.text = " ".join("".join(parts).split())
            self.found.append(self._cur)
            self._cur = None
            self._span = []

    def handle_data(self, data: str) -> None:
        if self._cur is None:
            return
        self._all_text.append(data)
        if any(self._span):
            self._title_text.append(data)


def _parse_page(path: Path) -> tuple[list[_Raw], bool]:
    collector = _Collector()
    collector.feed(_read_text(path, "a built page"))
    collector.close()
    return collector.found, collector.is_tab


def _unreadable(_: OSError) -> None:
    raise ParityError("a directory in the site cannot be read")


def _target_key(page: str, href: str) -> str:
    """A page-independent form of a repeated anchor's target."""
    if _is_external(href):
        return normalize_url(href)
    if urlsplit(href).scheme:
        return href
    path = urlsplit(href).path
    return "internal:" + posixpath.normpath(posixpath.join(page, path))


def _page_files(site: Path) -> list[tuple[str, Path]]:
    if not site.is_dir():
        raise ParityError("the site directory does not exist")
    found: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(
        site, followlinks=False, onerror=_unreadable
    ):
        dirnames.sort()
        if "index.html" in filenames:
            path = Path(dirpath) / "index.html"
            if not path.is_symlink():
                rel = Path(dirpath).relative_to(site).as_posix()
                found.append(("" if rel == "." else rel, path))
        if len(found) > MAX_PAGES:
            raise ParityError(f"the site has more than {MAX_PAGES} pages")
    if not found:
        raise ParityError("the site directory has no index.html pages")
    found.sort(key=lambda item: (item[0] != "", item[0]))  # root page first
    return found


def read_site(site: Path) -> SiteLinks:
    result = SiteLinks()
    root_once: list[tuple[str, str]] | None = None
    for rel, path in _page_files(site):
        name = rel or "/"
        once_here: list[tuple[str, str]] = []
        position = 0
        raws, is_tab = _parse_page(path)
        if not is_tab:
            continue  # kit, privacy and other pages are not tab pages
        result.pages += 1
        for raw in raws:
            if SCOPE[raw.kind]:
                once_here.append((_target_key(rel, raw.href), raw.text))
                if root_once is not None:
                    continue
            if not _is_external(raw.href):
                result.internal += 1
                continue
            position += 1
            ref = raw.ident or f"{name}:{raw.kind}{position}"
            if not _valid_url(raw.href) or not raw.text:
                raise ParityError(f"anchor {ref} has no usable target or label")
            result.entries.append(
                Entry(ref, normalize_url(raw.href), normalize_label(raw.text))
            )
        if root_once is None:
            root_once = once_here
        elif once_here != root_once:
            result.icon_row_differs += 1
    if result.pages == 0 or (not result.entries and not result.internal):
        raise ParityError(
            "no tab pages or anchors found in the built site; the parser may be stale"
        )
    return result


# ---- comparison -----------------------------------------------------------


@dataclass
class Report:
    matched: int = 0
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    changed_label: list[str] = field(default_factory=list)
    changed_target: list[str] = field(default_factory=list)
    redirect_only: list[str] = field(default_factory=list)
    duplicate_site: list[str] = field(default_factory=list)
    duplicate_export: list[str] = field(default_factory=list)
    reordered: int = 0
    icon_row_differs: int = 0
    internal: int = 0
    site_total: int = 0
    export_total: int = 0

    def differences(self, *, accept_redirects: bool) -> int:
        n = (
            len(self.missing)
            + len(self.extra)
            + len(self.changed_label)
            + len(self.changed_target)
            + len(self.duplicate_site)
            + len(self.duplicate_export)
            + self.reordered
            + self.icon_row_differs
        )
        return n if accept_redirects else n + len(self.redirect_only)


def _take(pool: list[tuple[int, Entry]], pred: Any) -> tuple[int, Entry] | None:
    for i, item in enumerate(pool):
        if pred(item[1]):
            return pool.pop(i)
    return None


def _longest_increasing(seq: list[int]) -> int:
    best: list[int] = []
    for i, value in enumerate(seq):
        best.append(1 + max((best[j] for j in range(i) if seq[j] < value), default=0))
    return max(best, default=0)


def compare(
    site: SiteLinks,
    expected: list[Entry],
    redirects: dict[str, set[str]] | None = None,
    *,
    check_order: bool = True,
) -> Report:
    redirects = redirects or {}
    built = list(enumerate(site.entries))
    want = list(enumerate(expected))
    report = Report(
        internal=site.internal,
        icon_row_differs=site.icon_row_differs,
        site_total=len(built),
        export_total=len(want),
    )
    pairs: list[tuple[int, int]] = []  # (site index, export index)

    def pair(b: tuple[int, Entry], e: tuple[int, Entry]) -> None:
        pairs.append((b[0], e[0]))

    for step in ("exact", "label", "redirect", "target"):
        for b in list(built):
            if step == "exact":
                e = _take(
                    want, lambda x, b=b: (x.url, x.label) == (b[1].url, b[1].label)
                )
            elif step == "label":
                e = _take(want, lambda x, b=b: x.url == b[1].url)
            elif step == "redirect":
                near = redirects.get(b[1].url, set())
                e = _take(
                    want, lambda x, b=b, n=near: x.url in n and x.label == b[1].label
                )
            else:
                e = _take(want, lambda x, b=b: x.label == b[1].label)
            if e is None:
                continue
            built.remove(b)
            pair(b, e)
            if step == "exact":
                report.matched += 1
            elif step == "label":
                report.changed_label.append(b[1].ref)
            elif step == "redirect":
                report.redirect_only.append(b[1].ref)
            else:
                report.changed_target.append(b[1].ref)

    # What is left: copies of a link that did pair, or links with no partner.
    paired_urls = {site.entries[i].url for i, _ in pairs} | {
        expected[j].url for _, j in pairs
    }
    for b in built:
        if b[1].url in paired_urls:
            report.duplicate_site.append(b[1].ref)
        else:
            report.extra.append(b[1].ref)
    for e in want:
        if e[1].url in paired_urls:
            report.duplicate_export.append(e[1].ref)
        else:
            report.missing.append(e[1].ref)

    if check_order and pairs:
        ordered = [j for _, j in sorted(pairs)]
        report.reordered = len(ordered) - _longest_increasing(ordered)
    return report


def render(report: Report, *, accept_redirects: bool) -> str:
    def line(name: str, refs: list[str]) -> str:
        shown = ", ".join(refs[:20]) + (", ..." if len(refs) > 20 else "")
        return f"{name}: {len(refs)}" + (f" ({shown})" if refs else "")

    lines = [
        f"site links: {report.site_total}; export links: {report.export_total}",
        f"matched: {report.matched}",
        line("missing", report.missing),
        line("extra", report.extra),
        line("changed label", report.changed_label),
        line("changed target", report.changed_target),
        line("redirect only", report.redirect_only)
        + ("" if accept_redirects else " [counts as a difference]"),
        line("duplicate in site", report.duplicate_site),
        line("duplicate in export", report.duplicate_export),
        f"reordered: {report.reordered}",
    ]
    if report.icon_row_differs:
        n = report.icon_row_differs
        lines.append(f"icon row differs on {n} page{'s' if n != 1 else ''}")
    lines.append(f"internal, not compared: {report.internal}")
    diffs = report.differences(accept_redirects=accept_redirects)
    lines.append("parity: yes" if diffs == 0 else f"parity: NO ({diffs} differences)")
    return "\n".join(lines) + "\n"
