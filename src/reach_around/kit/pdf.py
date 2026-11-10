# SPDX-License-Identifier: Apache-2.0
"""Render the full private media kit to a PDF, offline and reproducibly.

Pipeline (``build_kit_pdf``):

1. ``stage_page`` turns the gated page into a sealed, self-contained document
   (``reach_around.kit.stage``): allowlisted markup, inlined local CSS, embedded
   local images, a Content-Security-Policy that forbids every load.
2. A renderer prints it in an owner-only work directory that is removed on every
   exit path. The shipped renderer runs headless Chromium (no Python dependency:
   the browser the visual tests already use, ``RA_CHROMIUM``). Browser flags
   disable DNS, proxy traffic, background networking and JavaScript as layered
   defenses. The public command additionally requires a network namespace,
   which is the actual network cutoff.
3. ``scrub_pdf`` rewrites the Info dictionary in place to a fixed form (title,
   the producer string ``Reach Around`` and the creation date you give), so the
   same inputs give the same bytes. No author, creator, browser version or
   modification time is kept, and the file is checked for the leftovers.
4. The text is extracted (``reach_around.kit.pdftext``) and searched: the build
   fails if any field the config marks private or not-for-PDF is in it, and if
   any configured gated text is missing (a stale staging directory, or text the
   renderer lost).
5. ``reach_around.kit.output`` writes the file, owner-only, into a confined
   directory.

The public half (``build_public_kit_pdf``, ``kit pdf --public``) runs the same
steps on the page the site publishes, with the gate box dropped, a "Valid as of"
line added and a wider leak search (every gated value). Both go through ``_print``.

The size and page caps are ``MAX_PDF_BYTES`` and ``MAX_PAGES``.
"""

from __future__ import annotations

import contextlib
import getpass
import os
import re
import shutil
import signal
import subprocess
import tempfile
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import FrameType
from typing import Literal, NoReturn

from reach_around.config.cutover import cutover_label, live_since, utc_today
from reach_around.config.engagement import engagement_rows
from reach_around.config.kit import (
    KitConfig,
    enforce_display_policy,
    pdf_excluded_values,
    resolve_metrics,
    unified_total,
)
from reach_around.config.models import SiteConfig
from reach_around.config.numbers import Numbers
from reach_around.kit.output import check_name, confine_out_dir, write_private
from reach_around.kit.pdftext import PdfTextError, extract_pages
from reach_around.kit.stage import stage_page, valid_as_of_text
from reach_around.site.kit import (
    GUARD_MIN_LENGTH,
    _gated_strings,
    _metric_rows,
    _public_values,
    _total,
    canonical,
    text_fields,
)

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PAGES = 60
RENDER_TIMEOUT = 120
PRODUCER = "Reach Around"
TITLE_MAX = 120

Isolation = Literal["off", "netns"]
Renderer = Callable[[str, Path], bytes]

# Words that belong to the gate form; a printed kit has none of them.
GATE_PHRASES = ("cf-turnstile", "challenges.cloudflare.com", "turnstile")


class PdfError(ValueError):
    """The PDF could not be made."""


class PdfLeakError(PdfError):
    """Private text was found in the rendered PDF; nothing was written."""


@dataclass(frozen=True)
class PdfResult:
    path: Path
    pages: int
    size: int


# --- the work directory --------------------------------------------------------


def _terminate(signum: int, frame: FrameType | None) -> NoReturn:
    raise SystemExit(143)


@contextlib.contextmanager
def private_workdir() -> Iterator[Path]:
    """An owner-only directory, removed on every exit path (an error, Ctrl-C,
    and SIGTERM, which is turned into an exit so the cleanup runs)."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    parent = tempfile.gettempdir()
    if runtime:
        info = os.stat(runtime) if os.path.isdir(runtime) else None
        if (
            info is not None
            and info.st_uid == os.geteuid()
            and not info.st_mode & 0o077
        ):
            parent = runtime
    work = Path(tempfile.mkdtemp(prefix="ra-pdf-", dir=parent))
    previous = None
    with contextlib.suppress(ValueError):  # not the main thread: no handler
        previous = signal.signal(signal.SIGTERM, _terminate)
    problem = ""
    try:
        yield work
    except BaseException as exc:
        problem = _remove(work)
        if problem:
            exc.add_note(problem)  # the original error stays the one raised
        raise
    else:
        problem = _remove(work)
        if problem:
            raise PdfError(problem)
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)


def _remove(work: Path) -> str:
    """Delete the work directory; the problem text if that failed, else empty.
    A failure is never hidden: the unscrubbed PDF may still be in there."""
    try:
        shutil.rmtree(work)
    except OSError as exc:
        return (
            f"the work directory {work} could not be fully removed "
            f"({exc.strerror or exc}); it may hold the unscrubbed PDF, delete it"
        )
    return ""


# --- the browser ----------------------------------------------------------------


def find_chromium(explicit: Path | None) -> Path | None:
    """The browser binary: the given path, ``RA_CHROMIUM``, or one on PATH."""
    if explicit is not None:
        return explicit
    env = os.environ.get("RA_CHROMIUM")
    if env:
        return Path(env)
    for name in (
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
    ):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def chromium_renderer(
    binary: Path | None, *, isolation: Isolation = "off", timeout: int = RENDER_TIMEOUT
) -> Renderer:
    """A renderer that prints the page with headless Chromium."""
    browser = find_chromium(binary)
    if browser is None:
        raise PdfError(
            "no Chromium found; install one, set RA_CHROMIUM, or pass --chromium"
        )

    def render(html: str, work: Path) -> bytes:
        page, out = work / "page.html", work / "out.pdf"
        for sub in ("profile", "home", "cache", "config"):
            (work / sub).mkdir(mode=0o700)
        page.write_text(html, encoding="utf-8")
        page.chmod(0o600)
        command = [
            str(browser),
            "--headless",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-sync",
            "--disable-javascript",
            "--host-resolver-rules=MAP * ~NOTFOUND",
            "--proxy-server=http://127.0.0.1:1",
            "--proxy-bypass-list=<-loopback>",
            f"--user-data-dir={work / 'profile'}",
            "--no-pdf-header-footer",
            f"--print-to-pdf={out}",
            page.as_uri(),
        ]
        if isolation == "netns":
            # a network namespace of its own; the browser's own sandbox needs
            # namespaces it cannot nest here, so it is off in this mode only
            unshare = shutil.which("unshare")
            if unshare is None:
                raise PdfError("--isolation netns needs the unshare program")
            command = [
                unshare,
                "--user",
                f"--map-user={os.getuid()}",
                "--net",
                command[0],
                "--no-sandbox",
                *command[1:],
            ]
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(work / "home"),
            "XDG_CACHE_HOME": str(work / "cache"),
            "XDG_CONFIG_HOME": str(work / "config"),
            "LANG": "C.UTF-8",
            "TZ": "UTC",
        }
        try:
            done = subprocess.run(  # noqa: S603 - fixed argv, no shell, scrubbed environment
                command,
                env=env,
                cwd=work,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PdfError(f"the browser did not finish in {timeout} seconds") from exc
        except OSError as exc:
            raise PdfError(
                f"the browser could not be started ({exc.strerror})"
            ) from exc
        if done.returncode != 0 or not out.is_file():
            raise PdfError(
                f"the browser failed (exit {done.returncode}); try --isolation off "
                "or a different --chromium"
            )
        if out.stat().st_size > MAX_PDF_BYTES:
            raise PdfError(f"the PDF is larger than {MAX_PDF_BYTES} bytes")
        return out.read_bytes()

    return render


# --- metadata scrub -------------------------------------------------------------

_LEFTOVERS = (
    b"HeadlessChrome",
    b"Mozilla",
    b"Skia",
    b"Chromium",
    b"/Creator",
    b"/ModDate",
    b"/Author",
    b"/Subject",
    b"/Keywords",
    b"/Metadata",
)


def _xref_offsets(data: bytes) -> tuple[dict[int, int], int]:
    starts = re.findall(rb"startxref\s+(\d+)", data)
    if not starts:
        raise PdfError("the PDF has no cross-reference table to rewrite safely")
    xref = int(starts[-1])
    table = re.compile(rb"xref\s*\n")
    head = table.match(data, xref)
    if head is None:
        raise PdfError("the PDF uses a cross-reference form that is not supported")
    offsets: dict[int, int] = {}
    pos = head.end()
    while True:
        sub = re.compile(rb"(\d+) (\d+)\s*\n").match(data, pos)
        if sub is None:
            break
        first, count = int(sub.group(1)), int(sub.group(2))
        pos = sub.end()
        for index in range(count):
            row = data[pos : pos + 20]
            entry = re.fullmatch(rb"(\d{10}) (\d{5}) ([nf]) ?\r?\n", row)
            if entry is None:
                raise PdfError("the PDF cross-reference table is not readable")
            if entry.group(3) == b"n":
                offsets[first + index] = int(entry.group(1))
            pos += 20
    return offsets, xref


def scrub_pdf(data: bytes, *, title: str, created: date) -> bytes:
    """Rewrite the Info dictionary to title, producer and creation date only,
    keeping every byte offset, and fail if browser or user traces remain."""
    if re.search(rb"/Annots\b|/Type\s*/Annot\b", data):
        raise PdfError("the PDF contains annotations, which are not allowed")
    if re.search(
        rb"/StructTreeRoot\b|/StructElem\b|/MarkInfo\b|/ActualText\b|/Alt\b",
        data,
    ):
        raise PdfError("the PDF contains tagged structures, which are not allowed")
    infos = re.findall(rb"/Info\s+(\d+)\s+\d+\s+R", data)
    if not infos:
        raise PdfError("the PDF has no Info dictionary to scrub")
    number = int(infos[-1])
    offsets, xref = _xref_offsets(data)
    if number not in offsets:
        raise PdfError("the PDF Info dictionary is not in its cross-reference table")
    start = offsets[number]
    stop = min([o for o in offsets.values() if o > start] + [xref])
    span = data[start:stop]
    head = re.match(rb"%d 0 obj\s*(<<)" % number, span)
    close = span.rfind(b">>")
    if (
        head is None
        or close == -1
        or not re.fullmatch(rb"\s*endobj\s*", span[close + 2 :])
    ):
        raise PdfError("the PDF Info dictionary is not in the expected form")
    first = start + head.start(1)
    last = start + close + 2
    stamp = created.strftime("%Y%m%d") + "000000Z"
    new = (
        b"<</Title <FEFF"
        + title[:TITLE_MAX].encode("utf-16-be").hex().upper().encode()
        + b">\n/Producer ("
        + PRODUCER.encode()
        + b")\n/CreationDate (D:"
        + stamp.encode()
        + b")>>"
    )
    if len(new) > last - first:
        raise PdfError("the scrubbed Info dictionary does not fit in the old one")
    out = data[:first] + new + b" " * (last - first - len(new)) + data[last:]
    for marker in _LEFTOVERS:
        if marker in out:
            raise PdfError("the PDF still holds browser metadata after the scrub")
    return out


# --- the checks on the text -------------------------------------------------------


def _expected_text(
    kit: KitConfig, numbers: Numbers | None, *, public: bool = False
) -> list[tuple[str, str]]:
    """(where, text) for everything the page must print. A headline
    number is one entry per row (platform, measure, value and date together, in
    the order the page prints them) and one for the combined total, computed
    from the same resolved values the page was built from. With ``public`` only
    the fields the display policy gives every visitor are expected."""
    found: list[tuple[str, str]] = [("kit.title", kit.title), ("kit.about", kit.about)]
    if kit.contact is not None:
        found.append(("kit.contact", kit.contact))
    for index, tag in enumerate(kit.tags):
        found.append((f"kit.tags[{index}]", tag))
    resolved = resolve_metrics(kit, numbers)
    for row in _metric_rows(resolved):
        found.append(
            (
                f"the metric row {row['platform'] or row['label']!r}",
                row["platform"] + row["label"] + row["value"] + row["as_of"],
            )
        )
    for row in () if public else engagement_rows(kit):
        where = f"the engagement row {row['platform']!r} {row['measure']!r}"
        found.append((where, row["platform"] + row["measure"]))
        if row["provenance"]:
            found.append((where, row["provenance"]))
        found.append((where, row["formula"]))
        found.append((where, row["value"] + row["period"]))
        found.append((where, row["as_of"]))
    total = _total(unified_total(resolved), resolved)
    if total is not None:
        found.append(("the metric total", total["value"]))
    if public:
        return found
    if kit.disclosure is not None:
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
            for field, text in text_fields(item):
                if field == "url":  # a link target, not printed text
                    continue
                if text:
                    found.append((f"kit.{name}[{index}].{field}", text))
    if (
        kit.inquiry is not None
    ):  # the owner details print; the form beside them does not
        found.append(("kit.inquiry.email", kit.inquiry.email))
        if kit.inquiry.pgp_fingerprint is not None:
            found.append(("kit.inquiry.pgp_fingerprint", kit.inquiry.pgp_fingerprint))
    return found


def _flat(text: str) -> str:
    """Comparison form for text a reader sees: Unicode NFKC, case-folded, with
    spaces and invisible format characters removed (so a value broken across
    lines or runs, or hidden by zero-width characters, is still found). No
    entity or percent decoding: rendered text is already plain, and decoding
    would turn '40%' followed by '20...' into something else."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        ch for ch in folded if not ch.isspace() and unicodedata.category(ch) != "Cf"
    )


def check_text(
    kit: KitConfig,
    text: str,
    numbers: Numbers | None = None,
    live_since_text: str | None = None,
    *,
    public: bool = False,
    valid_as_of_text: str | None = None,
) -> None:
    """Fail if a private value is in ``text`` or configured gated text is not.

    With ``public`` the text is the public half: every gated value (and every
    owner-report-only one) is also a leak, except one that the display policy
    already gives every visitor, and only the public fields are expected."""
    flat = _flat(text)
    for field, value in pdf_excluded_values(kit).items():
        needle = _flat(value)
        if len(value) >= GUARD_MIN_LENGTH and needle in flat:
            raise PdfLeakError(
                f"the private field {field} appears in the PDF text; nothing was "
                "written"
            )
    if public:
        allowed = _public_values(kit)
        contact = canonical(kit.contact) if kit.contact is not None else ""
        for field, value in _gated_strings(kit):
            if canonical(value) in allowed:
                continue
            if field == "kit.inquiry.email" and canonical(value) in contact:
                continue
            if _flat(value) in flat:
                raise PdfLeakError(
                    f"the gated field {field} appears in the public PDF text; "
                    "nothing was written"
                )
    for phrase in GATE_PHRASES:
        if phrase in flat:
            raise PdfLeakError(
                "text from the gate form appears in the PDF; nothing was written"
            )
    expected = _expected_text(kit, numbers, public=public)
    if live_since_text is not None:
        expected.append(("the live-since line", live_since_text))
    if valid_as_of_text is not None:
        expected.append(("the valid-as-of line", valid_as_of_text))
    for where, value in expected:
        if _flat(value) not in flat:
            raise PdfError(
                f"{where} is missing from the PDF text; the staged page may be out "
                "of date (rebuild with reach-around build"
                f"{'' if public else ' --gated-out'}) or the renderer lost it"
            )


# --- the whole thing -----------------------------------------------------------------


def build_kit_pdf(
    config: SiteConfig,
    *,
    gated_dir: Path,
    site_dir: Path,
    out_dir: Path,
    name: str,
    created: date,
    renderer: Renderer,
    overwrite: bool = False,
    protect: Sequence[Path] = (),
    numbers: Numbers | None = None,
) -> PdfResult:
    """Make the private PDF; see the module doc. ``protect`` lists more
    directories the output may not be in (the config's directory)."""
    kit = config.kit
    if kit is None or (not kit.has_gated_content and not kit.engagement):
        raise PdfError(
            "the config has no gated media kit content (partners, case studies, "
            "packages, rates, engagement, testimonials, demographics or a "
            "disclosure statement); there is nothing private to print"
        )
    enforce_display_policy(kit)
    check_name(name)
    target_dir = confine_out_dir(out_dir, [gated_dir, site_dir, *protect])
    staged = stage_page(gated_dir, site_dir)
    return _print(
        config,
        kit,
        staged.html,
        target_dir=target_dir,
        name=name,
        created=created,
        renderer=renderer,
        overwrite=overwrite,
        numbers=numbers,
        public=False,
    )


def build_public_kit_pdf(
    config: SiteConfig,
    *,
    site_dir: Path,
    out_dir: Path,
    name: str,
    created: date,
    renderer: Renderer,
    overwrite: bool = False,
    protect: Sequence[Path] = (),
    numbers: Numbers | None = None,
) -> PdfResult:
    """Make the public-half PDF (``kit pdf --public``): the page the site
    publishes at ``/kit/``, with the gate form dropped and a "Valid as of" line
    for ``created``, through the same display policy, staging, renderer, scrub,
    text check and confined private write as the private PDF. The text check also
    fails if any gated or owner-report-only value is in the result."""
    kit = config.kit
    if kit is None:
        raise PdfError("the config has no media kit; there is nothing to print")
    enforce_display_policy(kit)
    check_name(name)
    target_dir = confine_out_dir(out_dir, [site_dir, *protect])
    staged = stage_page(site_dir, site_dir, public=True, valid_as_of=created)
    return _print(
        config,
        kit,
        staged.html,
        target_dir=target_dir,
        name=name,
        created=created,
        renderer=renderer,
        overwrite=overwrite,
        numbers=numbers,
        public=True,
    )


def _print(
    config: SiteConfig,
    kit: KitConfig,
    html: str,
    *,
    target_dir: Path,
    name: str,
    created: date,
    renderer: Renderer,
    overwrite: bool,
    numbers: Numbers | None,
    public: bool,
) -> PdfResult:
    """Render, scrub, check and write one staged page. Shared by both kinds."""
    with private_workdir() as work:
        raw = renderer(html, work)
        if len(raw) > MAX_PDF_BYTES:
            raise PdfError(f"the PDF is larger than {MAX_PDF_BYTES} bytes")
        final = scrub_pdf(raw, title=kit.title, created=created)
        for trace in (str(work), os.path.expanduser("~"), getpass.getuser()):
            if len(trace) >= 4 and trace.encode() in final:
                raise PdfError("the PDF holds a path or user name from this machine")
    try:
        pages = extract_pages(final)
    except PdfTextError as exc:
        raise PdfError(f"the PDF text could not be checked: {exc}") from exc
    if len(pages) > MAX_PAGES:
        raise PdfError(f"the PDF has {len(pages)} pages, more than {MAX_PAGES}")
    day = live_since(config, utc_today())
    check_text(
        kit,
        "\n".join(pages),
        numbers,
        None if day is None else cutover_label(day),
        public=public,
        valid_as_of_text=valid_as_of_text(created) if public else None,
    )
    path = write_private(target_dir, name, final, overwrite=overwrite)
    return PdfResult(path=path, pages=len(pages), size=len(final))
