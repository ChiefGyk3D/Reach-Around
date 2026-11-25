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
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import FrameType
from typing import Literal, NoReturn

from reach_around.config.blocks import platform_name
from reach_around.config.channels import PEOPLE_KEYS
from reach_around.config.cutover import cutover_label, live_since, utc_today
from reach_around.config.engagement import percent_text
from reach_around.config.imported_history import ImportedHistory, expected_text
from reach_around.config.kit import (
    DISPLAY_POLICY,
    KitConfig,
    enforce_display_policy,
    pdf_excluded_values,
    policy_fingerprint,
    summary_total,
)
from reach_around.config.measures import (
    REGISTRY,
    Measure,
    compile_measures,
    visible,
)
from reach_around.config.models import SiteConfig
from reach_around.config.numbers import Numbers, source_platforms
from reach_around.config.trust import (
    LEGEND_HEADING,
    LEGEND_SENTENCES,
    LEGEND_SENTENCES_GATED,
    demographic_trust,
)
from reach_around.funnel.floor import shown_count
from reach_around.kit.binding import (
    AnchoredReadError,
    GatedLockError,
    check_no_pending_commit,
    gated_lock,
    read_anchored,
)
from reach_around.kit.output import check_name, confine_out_dir, write_private
from reach_around.kit.pdftext import PdfTextError, extract_pages
from reach_around.kit.stage import stage_page, valid_as_of_text
from reach_around.site.kit import (
    GATED_POLICY_FILE,
    GUARD_MIN_LENGTH,
    POLICY_FILE,
    _analytics_rows,
    _case_rows,
    _change_text,
    _community,
    _compact_number,
    _engagement_rows,
    _gated_strings,
    _imported_block,
    _is_stale,
    _metric_rows,
    _post_trust,
    _public_values,
    _total,
    audience_bases,
    canonical,
    demographic_withheld,
    live_block,
    platform_counts,
    section_cards,
    text_fields,
)

OUT_OF_DATE = "(out of date)"
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
            # recent Chromium tags PDFs by default; the scrub refuses tagged output
            "--disable-pdf-tagging",
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


_FIELD_OF_BLOCK = {
    "headline": "metrics",
    "tile": "analytics",
    "derived": "engagement",
    "live": "live",
    "fediverse": "fediverse",
    "channels": "channels",
}


def private_measure_strings(kit: KitConfig, numbers: Numbers | None) -> dict[str, str]:
    """The text a page would print for every figure of a private measure, keyed
    by the field it came from (the key starts with that field's name). Private
    measures are never expected; these must be absent from the PDF text.

    The forms are the ones the templates render: a headline row (platform, label,
    value), an analytics panel (the sync line, "Followers" with its compact value
    and the real 30-day text, each tile's label with its compact value), an
    engagement row (measure name and value), a live card row (label and
    value) and a fediverse card row (label and figure). A labeled form is specific
    enough to search for whatever its length; a bare number is searched for only
    from ``GUARD_MIN_LENGTH`` characters. Owner-defined labels are searched for alone
    unless a visible measure prints the same word."""
    measures = compile_measures(kit, numbers, None)
    shown_labels = {m.label.casefold() for m in measures if m.visibility != "private"}
    shown_labels |= {entry.label.casefold() for entry in REGISTRY.values()}
    followers = {
        m.platform: m.value
        for m in measures
        if m.block == "tile" and m.key == "followers" and m.value is not None
    }
    found: dict[str, str] = {}
    for m in measures:
        if m.visibility != "private":
            continue
        field = _FIELD_OF_BLOCK[m.block]
        # the platform is part of the key: two platforms may share a key and a label
        key = f"{field}.{m.platform}.{m.key}.{m.label}"
        name = platform_name(m.platform)
        if m.value is not None:
            text = ""
            if m.block == "derived":
                text = (
                    percent_text(m.value)
                    if m.unit == "percent"
                    else shown_count(m.value)
                )
                found[f"{key} (row)"] = m.label + text
                found[f"{key} (platform row)"] = name + m.label + text
                # the row exactly as printed: the owner-supplied mark and the
                # interactions note sit between the measure name and the value
                lead = f"{m.provenance}." if m.provenance else ""
                note = f"{m.note}." if m.note else ""
                found[f"{key} (printed row)"] = name + m.label + lead + note + text
            elif m.block == "live":
                # a live card row prints the label and the shown count
                text = shown_count(m.value)
                found[f"{key} (live row)"] = m.label + text
            elif m.block == "fediverse":
                # a fediverse card row prints the label and the figure: the
                # follower count keeps the floor, a post count or per-post
                # median/mean prints as it is
                text = shown_count(m.value) if m.key == "followers" else f"{m.value:,}"
                found[f"{key} (fediverse row)"] = m.label + text
            elif m.block == "channels":
                # a channel card row prints the label and the figure: a count of
                # people keeps the floor, the rest prints as typed
                text = shown_count(m.value) if m.key in PEOPLE_KEYS else f"{m.value:,}"
                found[f"{key} (channel row)"] = m.label + text
            elif m.block == "tile" and m.key == "followers_change_30d":
                change = _change_text(followers.get(m.platform, 0), m.value)
                if change:
                    found[f"{key} (change)"] = change
            elif m.block == "tile":
                text = _compact_number(m.value)
                found[f"{key} (tile)"] = m.label + text
            else:
                text = shown_count(m.value)
                found[f"{key} (platform row)"] = name + m.label + text
                found[f"{key} (row)"] = m.label + text
            if m.key != "followers_change_30d":
                for bare in {f"{m.value:,}", str(m.value), text}:
                    if len(bare) >= GUARD_MIN_LENGTH:
                        found[f"{key} ({bare})"] = bare
        if m.block == "tile" and m.key == "followers" and m.as_of is not None:
            found[f"{key} (sync line)"] = (
                f"Synced with {m.source_note} on {m.as_of.isoformat()}"
            )
            found[f"{key} (source)"] = f"Typed by the owner from {m.source_note}"
        owner_label = m.block == "headline" or m.key == "analytics_stat"
        if (
            owner_label
            and len(m.label) >= GUARD_MIN_LENGTH
            and m.label.casefold() not in shown_labels
        ):
            found[f"{key} (label)"] = m.label
    return found


def check_policy_record(record: Path, what: str, lock_fd: int | None = None) -> None:
    """Refuse a staged page whose policy record is missing or differs from the
    current policy: the page was built before the display policy changed, so it
    may still hold figures the policy now keeps out. Rebuild, then print."""
    recorded: str | None
    try:
        if lock_fd is not None:
            # read through the held directory of the locked gated directory
            recorded = read_anchored(record, 4096, lock_fd)[:256].decode(
                "ascii", errors="replace"
            )
        elif record.is_symlink() or not record.is_file():
            recorded = None
        else:
            recorded = record.read_bytes()[:256].decode("ascii", errors="replace")
    except (OSError, AnchoredReadError):
        recorded = None
    if recorded is None or recorded.strip() != policy_fingerprint():
        raise PdfError(
            f"the {what} was built under a different display policy, or has no "
            "policy record; nothing was written. Rebuild the site first "
            f"(reach-around build{' --gated-out' if what == 'gated page' else ''}), "
            "then print the PDF"
        )


def _measures(
    kit: KitConfig, numbers: Numbers | None, config: SiteConfig | None, public: bool
) -> list[Measure]:
    """The measures the page for this audience prints. With a config each carries
    its trust label; without one the page is expected to print none."""
    api = source_platforms(config) if config is not None else None
    allowed = frozenset({"public"} if public else {"public", "gated"})
    return visible(compile_measures(kit, numbers, api), allowed)


def _expected_text(
    kit: KitConfig,
    numbers: Numbers | None,
    *,
    public: bool = False,
    imported: ImportedHistory | None = None,
    live_day: date | None = None,
    platform_counts: Mapping[str, list[int]] | None = None,
    config: SiteConfig | None = None,
    days: Sequence[date] = (),
    exact: list[tuple[str, str]] | None = None,
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
    measures = _measures(kit, numbers, config, public)
    # With the config the provenance of every number is known, and so is the
    # label the page must print beside it (#626). Without it none is expected.
    labeled = False

    def is_old(as_of: date | None, platform: str | None) -> bool:
        """Stale on every day the page may have been built on, so a build on
        either side of the limit never makes the check wrong."""
        return bool(
            config is not None
            and days
            and as_of is not None
            and all(_is_stale(as_of, day, config, platform) for day in days)
        )

    def put(where: str, text: str) -> None:
        """An expected label text: checked with its case, too (API is not Api)."""
        found.append((where, text))
        if exact is not None:
            exact.append((where, text))

    shown = [m for m in measures if m.block == "headline" and m.value is not None]
    for m, row in zip(shown, _metric_rows(measures, config), strict=True):
        text = _trust_text(row["trust"])
        labeled = labeled or bool(text)
        where = f"the metric row {row['platform'] or row['label']!r}"
        found.append(
            (where, row["platform"] + row["label"] + row["value"] + text + row["as_of"])
        )
        if text:
            if exact is not None:
                exact.append((where, row["value"] + text))
            if is_old(m.as_of, m.platform):
                put(where, row["as_of"] + "UTC" + OUT_OF_DATE)
    derived = [m for m in measures if m.block == "derived"]
    for entry, row in zip(
        derived, _engagement_rows(measures, config, None), strict=True
    ):
        where = f"the engagement row {row['platform']!r} {row['measure']!r}"
        found.append((where, row["platform"] + row["measure"]))
        if row["provenance"]:
            found.append((where, row["provenance"]))
        found.append((where, row["formula"]))
        found.append((where, row["value"] + row["period"]))
        found.append((where, row["as_of"]))
        text = _trust_text(row["trust"]) if config is not None else ""
        if text:
            labeled = True
            old = OUT_OF_DATE if is_old(entry.as_of, entry.platform) else ""
            put(where, row["as_of"] + "UTC" + text + old)
    total = _total(
        summary_total(measures), measures, None, config, None, kit.audience_summary
    )
    community = _community(measures)
    if community is not None:
        found.append(
            ("the community members line", community["text"] + " " + community["note"])
        )
    if total is not None:
        # Anchored on the date line printed under the number, so the range
        # sentence (which also prints the total) cannot satisfy it.
        found.append(
            (
                "the metric total",
                total["headline"] + "Figures dated" + total["oldest"] + "UTC",
            )
        )
        if total["range_text"]:
            found.append(("the audience range", total["range_text"]))
        # The summary block's own sentences (#627): the bounds, the largest
        # audience, the overlap sentence and the labeled estimate.
        for where, key in (
            ("the audience summary bounds", "bounds_text"),
            ("the largest single audience", "largest"),
            ("the overlap sentence", "overlap"),
            ("the audience summary caveat", "caveat"),
            ("the audience estimate", "estimate"),
        ):
            if total[key]:
                put(where, total[key])
        if total["estimate"]:
            put("the audience estimate method", total["estimate_method"])
        text = _trust_text(total["trust"])
        if text:
            labeled = True
            old = (
                OUT_OF_DATE
                if any(is_old(m.as_of, m.platform) for m in shown if m.in_summary)
                else ""
            )
            put("the metric total label", text + old)
    # The analytics tiles (#397): the sync line, the followers with the change
    # beside them, and each stat. Anchored on the sync line, which no headline
    # number row prints, so an unrelated row cannot satisfy them. A count under
    # the privacy floor is expected as "Under 5".
    for card in _analytics_rows(measures, None):
        where = f"the analytics row {card['platform_name']!r}"
        change = (
            f"{card['delta']} last 30 days"
            if card["delta"]
            else "30-day change not shown"
        )
        found.append(
            (
                where,
                f"Synced with {card['synced_with']} on {card['as_of']}"
                f"Followers{card['followers']}{change}",
            )
        )
        for stat in card["stats"]:
            found.append((where, stat["label"] + stat["value"]))
        if config is not None:
            labeled = True
            # The line follows the last tile, which anchors it to its panel.
            tile = (
                card["stats"][-1]["label"] + card["stats"][-1]["value"]
                if card["stats"]
                else f"Followers{card['followers']}{change}"
            )
            old = (
                OUT_OF_DATE
                if is_old(date.fromisoformat(card["as_of"]), card["platform"])
                else ""
            )
            put(where, tile + _trust_text(card["trust"]) + old)
    # The platform cards (#628): each card's name, its "No numbers yet" line or
    # its numbers with their labels, from the same measures the page was built from.
    for card in section_cards(
        kit.sections, measures, config, None, live_day, kit.fediverse, kit.channels
    ):
        where = f"the platform card {card['name']!r}"
        found.append((where, card["name"] + (card["account"] or "")))
        if card["empty"]:
            found.append((where, card["no_numbers"]))
        else:
            labeled = labeled or config is not None
            lead = card["headline"]
            found.append((where, lead["value"] + lead["label"]))
            for row in card["rows"]:
                found.append((where, row["label"] + row["value"]))
        # The fediverse lines (#630): the server note, the re-check link as a
        # plain URL (a PDF is read on paper) and each "not provided" line.
        if card["server_note"]:
            found.append((where, card["server_note"]))
        lead_link = card["headline"]["recheck"] if card["headline"] else ""
        for url in {r["recheck"] for r in card["rows"] if r["recheck"]} | {
            link for link in (card["recheck"], lead_link) if link
        }:
            found.append((where, url))
        for line in card["not_provided"]:
            found.append((where, line))
    # The live block (#629): each platform's seven fields, or its empty line.
    live = live_block(kit.live, measures, config, None, live_day)
    for card in live["cards"] if live else []:
        where = f"the live block {card['name']!r}"
        found.append((where, card["name"]))
        if card["empty"]:
            found.append((where, card["no_numbers"]))
            continue
        labeled = labeled or config is not None
        for row in card["rows"]:
            found.append((where, row["label"] + row["value"]))
    # Only the public page prints recent posts; the gated page has no such section.
    if config is not None and public:
        for platform, trust in _post_trust(kit.recent_posts).items():
            labeled = True
            put(f"the recent posts of {platform!r}", platform + _trust_text(trust))
    # The block "Before Reach Around" prints where the display policy puts it.
    if imported is not None and (not public or kit.imported_history_public):
        found.extend(expected_text(imported, live_day))
        block = _imported_block(imported, live_day)
        if config is not None and block is not None:
            labeled = True
            put("the imported history label", _trust_text(block["trust"]))
    if public:
        return _with_legend(found, labeled, exact, True)
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
        # Policy-aware: a field the table no longer gives the gated page is not
        # expected (a private engagement figure is checked as absent instead).
        if DISPLAY_POLICY[name] != "gated":
            continue
        for index, item in enumerate(getattr(kit, name)):
            withheld = (
                demographic_withheld(
                    item,
                    audience_bases(item.platform, measures, platform_counts),
                )
                if name == "demographics"
                else frozenset()
            )
            for field, text in text_fields(item):
                if field in {"url", "thumbnails"}:  # targets, not printed text
                    continue
                if any(field.startswith(group) for group in withheld):
                    continue  # a group held back for the privacy floor
                if text:
                    found.append((f"kit.{name}[{index}].{field}", text))
            if name == "case_studies" and config is not None:
                labeled = True
                (case,) = _case_rows([item])
                put(f"kit.{name}[{index}].trust", _trust_text(case["trust"]))
            if name == "demographics" and config is not None:
                labeled = True
                old = OUT_OF_DATE if is_old(item.as_of, item.platform) else ""
                put(
                    f"kit.{name}[{index}].trust",
                    f"As of {item.as_of.isoformat()} UTC"
                    + _trust_text(demographic_trust(item.as_of).view())
                    + old,
                )
            if name == "case_studies":
                for field, label in (
                    ("views", "Views"),
                    ("likes", "Likes"),
                    ("comments", "Comments"),
                ):
                    value = getattr(item, field)
                    found.append(
                        (f"kit.{name}[{index}].{field}", label + shown_count(value))
                    )
    if (
        kit.inquiry is not None
    ):  # the owner details print; the form beside them does not
        found.append(("kit.inquiry.email", kit.inquiry.email))
        if kit.inquiry.pgp_fingerprint is not None:
            found.append(("kit.inquiry.pgp_fingerprint", kit.inquiry.pgp_fingerprint))
    return _with_legend(found, labeled, exact)


def _trust_text(trust: Mapping[str, str] | None) -> str:
    """What a labeled number prints: the label, then the method line."""
    return "" if trust is None else trust["text"] + trust["method"]


def _with_legend(
    found: list[tuple[str, str]],
    labeled: bool,
    exact: list[tuple[str, str]] | None,
    public: bool = False,
) -> list[tuple[str, str]]:
    """The legend is expected once any labeled number is (#626)."""
    if labeled:
        sentences = LEGEND_SENTENCES if public else LEGEND_SENTENCES_GATED
        lines = [("the trust legend heading", LEGEND_HEADING)]
        lines += [("the trust legend", sentence) for sentence in sentences]
        found.extend(lines)
        if exact is not None:
            exact.extend(lines)
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


def _flat_exact(text: str) -> str:
    """Like ``_flat`` but case-sensitive."""
    folded = unicodedata.normalize("NFKC", text)
    return "".join(
        ch for ch in folded if not ch.isspace() and unicodedata.category(ch) != "Cf"
    )


def _short_gated_strings(kit: KitConfig) -> list[tuple[str, str]]:
    """Gated prose omitted from the substring guard because it is too short."""
    found: list[tuple[str, str]] = []
    if kit.disclosure is not None and len(kit.disclosure) < GUARD_MIN_LENGTH:
        found.append(("kit.disclosure", kit.disclosure))
    for name in (
        "partners",
        "case_studies",
        "services",
        "rates",
        "testimonials",
        "demographics",
    ):
        for index, item in enumerate(getattr(kit, name)):
            for field, value in text_fields(item):
                if name == "demographics" and field == "platform":
                    continue  # public vocabulary, as in the long-value guard
                if field != "url" and value and len(value) < GUARD_MIN_LENGTH:
                    found.append((f"kit.{name}[{index}].{field}", value))
    if kit.inquiry is not None:
        for field, value in kit.inquiry.model_dump().items():
            if isinstance(value, str) and value and len(value) < GUARD_MIN_LENGTH:
                found.append((f"kit.inquiry.{field}", value))
    return found


def _contains_exact(text: str, value: str) -> bool:
    """Match a short value as text of its own, not inside another word."""
    visible = unicodedata.normalize("NFKC", text).casefold()
    visible = "".join(ch for ch in visible if unicodedata.category(ch) != "Cf")
    needle = unicodedata.normalize("NFKC", value).casefold()
    needle = "".join(ch for ch in needle if unicodedata.category(ch) != "Cf")
    pattern = re.escape(needle).replace(r"\ ", r"\s+")
    return re.search(r"(?<!\w)" + pattern + r"(?!\w)", visible) is not None


def _public_pdf_values(kit: KitConfig, numbers: Numbers | None) -> set[str]:
    """Public values, including the resolved figures rendered in this PDF."""
    allowed = _public_values(kit)
    measures = _measures(kit, numbers, None, True)
    for row in _metric_rows(measures):
        allowed.add(canonical(row["value"]))
        if row["as_of"]:
            allowed.add(canonical(row["as_of"]))
    total = _total(summary_total(measures), measures)
    if total is not None:
        allowed.add(canonical(total["value"]))
        allowed.add(canonical(total["sum"]))
    return allowed


def check_text(
    kit: KitConfig,
    text: str,
    numbers: Numbers | None = None,
    live_since_text: str | None = None,
    *,
    public: bool = False,
    valid_as_of_text: str | None = None,
    imported: ImportedHistory | None = None,
    live_day: date | None = None,
    platform_counts: Mapping[str, list[int]] | None = None,
    config: SiteConfig | None = None,
    check_days: Sequence[date] = (),
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
    for field, value in private_measure_strings(kit, numbers).items():
        if _flat(value) in flat:
            raise PdfLeakError(
                f"a figure of the private field {field} appears in the PDF text; "
                "nothing was written"
            )
    if public:
        allowed = _public_pdf_values(kit, numbers)
        contact = canonical(kit.contact) if kit.contact is not None else ""
        for field, value in _gated_strings(kit, imported):
            if canonical(value) in allowed:
                continue
            if field == "kit.inquiry.email" and canonical(value) in contact:
                continue
            if _flat(value) in flat:
                raise PdfLeakError(
                    f"the gated field {field} appears in the public PDF text; "
                    "nothing was written"
                )
        for field, value in _short_gated_strings(kit):
            if canonical(value) in allowed:
                continue
            if field == "kit.inquiry.email" and canonical(value) in contact:
                continue
            if _contains_exact(text, value):
                raise PdfLeakError(
                    f"the gated field {field} appears in the public PDF text; "
                    "nothing was written"
                )
    for phrase in GATE_PHRASES:
        if phrase in flat:
            raise PdfLeakError(
                "text from the gate form appears in the PDF; nothing was written"
            )
    exact: list[tuple[str, str]] = []
    expected = _expected_text(
        kit,
        numbers,
        public=public,
        imported=imported,
        live_day=live_day,
        platform_counts=platform_counts,
        config=config,
        days=tuple(dict.fromkeys(check_days)),
        exact=exact,
    )
    if live_since_text is not None:
        expected.append(("the live-since line", live_since_text))
    if valid_as_of_text is not None:
        expected.append(("the valid-as-of line", valid_as_of_text))
    # Label words keep their case: "API" is not "Api", and a "(out of date)"
    # mark is not satisfied by other text (#626).
    exact_flat = _flat_exact(text)
    for where, value in exact:
        if _flat_exact(value) not in exact_flat:
            raise PdfError(
                f"{where} is missing from the PDF text with its label as written; "
                "the staged page may be out of date or the renderer lost it"
            )
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
    imported: ImportedHistory | None = None,
) -> PdfResult:
    """Make the private PDF; see the module doc. ``protect`` lists more
    directories the output may not be in (the config's directory)."""
    kit = config.kit
    if kit is None or (
        not kit.has_gated_content
        and not any(
            m.block == "derived" for m in _measures(kit, numbers, config, False)
        )
    ):
        raise PdfError(
            "the config has no gated media kit content (partners, case studies, "
            "packages, rates, engagement, testimonials, demographics or a "
            "disclosure statement); there is nothing private to print"
        )
    enforce_display_policy(kit)
    check_policy_record(gated_dir / GATED_POLICY_FILE, "gated page")
    check_name(name)
    target_dir = confine_out_dir(out_dir, [gated_dir, site_dir, *protect])
    # a shared lock on the gated directory while the page and its policy record
    # are read, so a build cannot replace them in between, and a directory a
    # build left half replaced is refused
    try:
        with gated_lock(gated_dir, exclusive=False) as lock_fd:
            try:
                check_no_pending_commit(gated_dir, lock_fd)
            except ValueError as exc:
                raise PdfError(str(exc)) from exc
            check_policy_record(gated_dir / GATED_POLICY_FILE, "gated page", lock_fd)
            staged = stage_page(gated_dir, site_dir)
    except GatedLockError as exc:
        raise PdfError(str(exc)) from exc
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
        imported=imported,
        public=False,
        forbidden=[gated_dir, site_dir, *protect],
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
    imported: ImportedHistory | None = None,
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
    check_policy_record(site_dir / POLICY_FILE, "public page")
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
        imported=imported,
        public=True,
        forbidden=[site_dir, *protect],
    )


def _link_page_counts(
    config: SiteConfig, numbers: Numbers | None, created: date
) -> dict[str, list[int]]:
    """The link page's counts, resolved on both days the staged page may have
    used, so a group the page withheld is not expected in the text."""
    found: dict[str, list[int]] = {}
    for day in {utc_today(), created}:
        for platform, counts in platform_counts(config, numbers, day).items():
            found.setdefault(platform, []).extend(counts)
    return found


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
    imported: ImportedHistory | None,
    public: bool,
    forbidden: Sequence[Path] = (),
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
        imported=imported,
        live_day=day,
        platform_counts=_link_page_counts(config, numbers, created),
        config=config,
        check_days=(utc_today(), created),
    )
    path = write_private(
        target_dir, name, final, overwrite=overwrite, forbidden=forbidden
    )
    return PdfResult(path=path, pages=len(pages), size=len(final))
