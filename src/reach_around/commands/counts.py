# SPDX-License-Identifier: Apache-2.0
"""``reach-around counts refresh --config PATH [--out numbers.json] ...``.

Also ``counts import`` (hand-kept numbers from a CSV file) and ``counts stale``
(hand-kept numbers due for an update). ``counts stale`` exits 0 when nothing is
due, 4 when something is, 2 for usage or config problems.

Exit codes of ``refresh``: 0 every source succeeded or was intentionally
skipped; 3 at least one source failed (its previous number is kept); 2 usage or
config problem; 1 the result files could not be written or another refresh
holds the lock. A ``token-health:`` line per token-holding source is printed
after the sources (docs/token-health.md); a token that is expiring changes no
exit code, one that was rejected or expired is already a failed source (3).

``counts tokens`` prints the same report from the config and the last status
file, with no network and no secret: exit 0 every token ok, 4 a token is
expiring, 3 a token is expired or was rejected, 2 usage or config problem.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from collections.abc import Callable, Mapping, MutableMapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path
from typing import TextIO

from reach_around.config import ConfigError, SiteConfig, load_site_config
from reach_around.config.cutover import utc_today
from reach_around.config.fediverse_sources import FediverseSource
from reach_around.config.imported_history import parse_imported
from reach_around.config.loader import _read
from reach_around.config.numbers import NumbersEntry, load_numbers
from reach_around.config.stale import REMIND_DAYS, due_numbers
from reach_around.counts.beacons import (
    IMPORT_NOTE,
    IMPORT_PLATFORM,
    IMPORT_RUN_ID,
    IMPORT_SOURCE,
    MAX_BEACONS_BYTES,
    BeaconsImportError,
    parse_beacons_csv,
)
from reach_around.counts.fediverse_commit import (
    SAME_DAY_KEPT,
    SNAPSHOT_IN_FUTURE,
    UNIT_NOTE_QUARANTINED,
    UnitFileTooLarge,
    UnitNote,
    abandon_unit,
    day_conflicts,
    discard_unit,
    find_stale,
    future_snapshots,
    mark_landed,
    new_generation,
    quarantine_unit,
    remove_stale,
    rows_present,
    snapshot_rows,
    status_committed,
    unit_path,
    unit_pending,
    write_unit,
)
from reach_around.counts.fediverse_refresh import FediverseResult, platform_key
from reach_around.counts.fediverse_refresh import run as run_fediverse
from reach_around.counts.fediverse_store import (
    dump_snapshots,
    load_snapshots,
    missing_endpoints,
    required_endpoints,
    snapshot_path,
)
from reach_around.counts.files import (
    LOCK_WAIT_SECONDS,
    FileLock,
    LockBusy,
    LockLost,
    Paths,
    StagedFile,
    dump_numbers,
    plan_paths,
    stage_file,
    utc_stamp,
    write_atomic,
    write_new_atomic,
)
from reach_around.counts.history import (
    API_PROVIDERS,
    MAX_BATCH,
    PRIVATE_PROVIDERS,
    REASONS,
    HistoryError,
    HistoryNotWritten,
    OldCopyRemainsError,
    Record,
    RedactDurabilityError,
    _open_parent,
    append_records,
    check_redaction_arguments,
    dump_new_history,
    find_stale_redaction_temps,
    find_unmarked_redaction_temps,
    new_run_id,
    plan_redaction,
    read_history,
    redact_records,
    remove_stale_redaction_temps,
    rewrite_metadata,
)
from reach_around.counts.history_compare import (
    COMPARE_MAX_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_NUMBERS_BYTES,
    compare_history,
    read_regular,
)
from reach_around.counts.imported_export import export_imported
from reach_around.counts.importer import (
    MAX_CSV_BYTES,
    ImportFailed,
    merge,
    parse_import,
)
from reach_around.counts.insights import Figure, InsightFailure, collect
from reach_around.counts.instagram_insights import (
    RETAIN_DAYS as INSTAGRAM_RETAIN_DAYS,
)
from reach_around.counts.instagram_insights import (
    deletion_candidates as instagram_deletion_candidates,
)
from reach_around.counts.pending import (
    Note,
    clear_pending,
    content_digest,
    live_ids,
    mark_pending,
    numbers_identity,
    pending_path,
    read_note,
    reconcile,
    require_no_stranded,
    withdraw_pending,
)
from reach_around.counts.refresh import (
    SUSPECT_KINDS,
    Outcome,
    Source,
    host_of,
    load_status,
    refresh,
    render_status,
    select,
)
from reach_around.counts.retention import RetentionPolicy, retention_warnings
from reach_around.counts.secret_sink import (
    DopplerSink,
    MetaStyleSink,
    PairStyleSink,
    ScopeBusy,
    SinkError,
    TokenHolder,
    scope_lock,
)
from reach_around.counts.token_health import (
    OK as TOKEN_OK,
)
from reach_around.counts.token_health import (
    TokenHealth,
    assess,
    render_github,
    render_json,
    render_markdown,
    render_text,
)
from reach_around.counts.token_health import (
    exit_code as token_exit_code,
)
from reach_around.counts.youtube_analytics import RETAIN_DAYS, deletion_candidates
from reach_around.net.egress import Transport
from reach_around.net.fetch import fetch_json

# What each kind of source counts, as named in the history file.
HISTORY_METRIC = {"youtube": "subscribers", "discord": "members", "matrix": "members"}

MAX_STDIN_TOKEN = 1024  # a Doppler token is far shorter

EXIT_OK = 0
EXIT_WRITE = 1
EXIT_USAGE = 2
EXIT_SOURCE_FAILED = 3
EXIT_DUE = 4
EXIT_TOKEN_WARNING = 4  # counts tokens: a token is expiring (same code as counts stale)
EXIT_REPLACED_UNSURE = 5  # history-redact: the file was replaced, durability unsure
EXIT_REWRITE = 10  # history-compare: a valid tombstone-only rewrite (reserved)


@dataclass(frozen=True)
class DopplerSpec:
    """Where the Doppler sink writes. ``repr`` never shows the API token."""

    project: str
    config: str
    token: TokenHolder = field(repr=False)


DOPPLER_TOKEN_ENV = "REACH_AROUND_DOPPLER_TOKEN"  # noqa: S105 (a name, not a value)
DOPPLER_PROJECT_ENV = "REACH_AROUND_DOPPLER_PROJECT"
DOPPLER_CONFIG_ENV = "REACH_AROUND_DOPPLER_CONFIG"


def doppler_spec(
    args: argparse.Namespace,
    env: MutableMapping[str, str],
    stdin: TextIO,
    held_token: TokenHolder | None = None,
) -> DopplerSpec:
    """Resolve the sink settings; ``ValueError`` names variables, never values.

    The API token comes from one line of stdin (preferred) or the environment,
    never argv. It is in a ``TokenHolder`` from the moment it is read, so no
    local here is a plain string for it. The environment copy is read once and
    removed here, before any network work, even when setup then fails. That
    narrows the exposure; it cannot undo what the launcher already did with the
    variable.
    """
    # The command line entry point already removed the variable before argument
    # parsing and passes it as ``held_token``; the pop covers other callers.
    from_env = TokenHolder(env.pop(DOPPLER_TOKEN_ENV, ""))
    from_env.strip()
    if held_token is not None:
        if not from_env:
            from_env.adopt(held_token)
        held_token.clear()
    project = args.doppler_project or env.get(DOPPLER_PROJECT_ENV, "")
    config = args.doppler_config or env.get(DOPPLER_CONFIG_ENV, "")
    if args.doppler_token_stdin:
        # A bounded read: one line of at most MAX_STDIN_TOKEN characters. A
        # longer line is refused, never accumulated.
        token = TokenHolder(stdin.readline(MAX_STDIN_TOKEN + 2))
        token.strip()
        from_env.clear()
        if len(token.get()) > MAX_STDIN_TOKEN:
            token.clear()
            raise ValueError("--doppler-token-stdin input is too long")
        source = "stdin"
    else:
        token = from_env
        source = DOPPLER_TOKEN_ENV
    missing = [
        label
        for label, present in (
            (source, bool(token)),
            (DOPPLER_PROJECT_ENV + " or --doppler-project", bool(project)),
            (DOPPLER_CONFIG_ENV + " or --doppler-config", bool(config)),
        )
        if not present
    ]
    if missing:
        token.clear()
        raise ValueError(f"--token-sink doppler needs: {', '.join(missing)}")
    return DopplerSpec(project, config, token)


def default_transport() -> Transport | None:
    """The real pinned HTTPS transport; ``None`` lets ``fetch_json`` build it."""
    return None


def default_sleep(seconds: float) -> None:
    """Retry back-off; a module attribute so tests can replace it."""
    time.sleep(seconds)


def run_refresh(
    config_path: Path,
    out: Path | None,
    only: list[str] | None,
    dry_run: bool,
    *,
    accept_drop: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    transport: Transport | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    lock_wait: float = LOCK_WAIT_SECONDS,
    doppler: DopplerSpec | None = None,
) -> int:
    try:
        config = load_site_config(config_path)
        if config.numbers is None or not (
            config.numbers.sources or config.numbers.fediverse
        ):
            raise ValueError("the config has no numbers.sources to refresh")
        sources = select(config.numbers, only)
        # ``--only`` names numbers sources; it never starts the fediverse collector.
        fediverse = [] if only else list(config.numbers.fediverse)
        known = {source.platform for source in config.numbers.sources}
        unknown = sorted(set(accept_drop or []) - known)
        if unknown:
            raise ValueError(
                f"--accept-drop names no configured source: {', '.join(unknown)}"
            )
        paths = plan_paths(config_path, out)
        history = history_path(config, config_path, paths)
        fediverse_file = snapshot_path(paths.target) if fediverse else None
        if fediverse_file is not None and (
            fediverse_file.is_symlink() or fediverse_file.is_dir()
        ):
            raise ValueError("the fediverse snapshot file is a symlink or a directory")
    except (ConfigError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    sinks: Sinks | None = None
    if doppler is not None:
        try:
            sinks = build_sinks(doppler, transport)
        except SinkError as exc:
            print(
                f"error: the Doppler sink is not usable ({exc.kind})", file=sys.stderr
            )
            return EXIT_USAGE
        finally:
            # The sink holds what it needs; the spec lets the token go.
            doppler.token.clear()

    if dry_run:
        for source in sources:
            print(
                f"would fetch {source.platform}: {source.kind} from {host_of(source)}"
            )
        for item in fediverse:
            print(
                f"would fetch {platform_key(item.kind)}: "
                f"{', '.join(required_endpoints([item]))}"
            )
        print("dry run: nothing fetched, nothing written")
        return EXIT_OK

    try:
        # The writer scope is taken first, so a second run fails fast instead of
        # waiting on the output lock. One lock for the whole load, fetch, merge and
        # write cycle: a second run started meanwhile waits, then reads what this
        # one wrote, so a stale copy can never replace a newer number.
        with _writer_scope(doppler), FileLock(paths.lock, wait=lock_wait):
            return _cycle(
                paths,
                sources,
                frozenset(accept_drop or []),
                os.environ if env is None else env,
                transport,
                now,
                history,
                sinks,
                _retention_policy(config),
                fediverse,
                fediverse_file,
            )
    except LockBusy:
        print(
            "error: another counts refresh holds the lock; try again shortly",
            file=sys.stderr,
        )
        return EXIT_WRITE
    except ScopeBusy:
        print(
            "error: another writer holds this Doppler project and config; "
            "nothing was fetched",
            file=sys.stderr,
        )
        return EXIT_SOURCE_FAILED
    except SinkError as exc:
        print(
            f"error: the Doppler sink lock state is not usable ({exc.kind}); "
            "nothing was fetched",
            file=sys.stderr,
        )
        return EXIT_USAGE
    except LockLost as exc:
        print(f"error: {exc}; nothing was written", file=sys.stderr)
        return EXIT_WRITE
    except OSError as exc:
        print(f"error: cannot use the output folder: {exc.strerror}", file=sys.stderr)
        return EXIT_WRITE


def _writer_scope(spec: DopplerSpec | None) -> AbstractContextManager[None]:
    """One run at a time per Doppler project and config; no-op without a sink."""
    if spec is None:
        return nullcontext()
    return scope_lock(spec.project, spec.config)


@dataclass(frozen=True)
class Sinks:
    pair: PairStyleSink  # Twitch and TikTok: a failed store fails the source
    # Instagram and Threads: a failed store prints a health line and the count
    # goes on with the old token (the session catches the sink error).
    meta: MetaStyleSink


def build_sinks(spec: DopplerSpec, transport: Transport | None) -> Sinks:
    """The three source contracts over one verified Doppler sink."""
    fetch = partial(
        fetch_json,
        transport=transport if transport is not None else default_transport(),
        sleep=default_sleep,
    )
    sink = DopplerSink(spec.project, spec.config, spec.token.get(), fetch)
    return Sinks(PairStyleSink(sink), MetaStyleSink(sink))


def _expected_digest(entries: Mapping[str, NumbersEntry]) -> str | None:
    """Digest of the numbers text this run is about to write (None if it cannot
    be made; the write would fail too, so the run can never count as committed)."""
    try:
        return content_digest(dump_numbers(entries))
    except ConfigError:
        return None


@dataclass(frozen=True)
class Staged:
    """What ``_stage_history`` decided: the records to append and the note this
    run wrote (its nonce), with what is needed to take the note back."""

    records: list[Record]
    nonce: str
    previous: Note  # the note's live content before this run listed itself
    history_before: tuple[int, ...] | None


def _history_state(history: Path) -> tuple[int, ...] | None:
    """Identity, size and modification time of the history file (``None`` when
    absent). Equal before and after a failed append means it was not touched."""
    try:
        info = os.lstat(history)
    except FileNotFoundError:
        return None
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _withdraw(
    paths: Paths, history: Path, staged: Staged, error: Exception | None = None
) -> None:
    """Take this run's id back out of the note after a failure, but only when
    the history file is exactly as it was before (nothing of this run is in it).
    When it is not, or that cannot be told, the id stays: it may be the only
    way to correct a value that did land. A write that was rolled back
    (``HistoryNotWritten``) put nothing in history, whatever its mtime says."""
    try:
        if (
            isinstance(error, HistoryNotWritten)
            or _history_state(history) == staged.history_before
        ):
            withdraw_pending(pending_path(paths.target), staged.nonce, staged.previous)
    except (OSError, HistoryError):
        pass  # the note stays; the next run compares its values


def _stage_history(
    history: Path,
    paths: Paths,
    records: list[Record],
    run_id: str,
    expect: str | None,
) -> Staged:
    """Note this run as pending, then settle days an earlier attempt recorded.

    History is written before the numbers file; if a later write fails, the
    pending note lets the next run replace the value that attempt left behind
    (see ``counts/pending.py``). Raises ``OSError`` before history is touched,
    and ``PendingError`` (a ``HistoryError``) when the note is there but cannot
    be trusted; the note is then left as it is and nothing is written.
    """
    pending = pending_path(paths.target)
    require_no_stranded(pending)
    note = read_note(pending)
    ids = live_ids(note, paths.target)  # [] when that run's numbers write landed
    before = _history_state(history)
    nonce = mark_pending(pending, ids, run_id, numbers_identity(paths.target), expect)
    staged = Staged(
        records, nonce, Note(ids, note.base, note.expect, note.nonce), before
    )
    if ids and history.exists():
        try:
            fixed = reconcile(read_history(history), ids, records)
        except (OSError, HistoryError):
            _withdraw(paths, history, staged)  # read only: history is untouched
            raise
        return Staged(fixed, nonce, staged.previous, before)
    return staged


def _settle_history(paths: Paths, nonce: str) -> None:
    """The numbers file was written, so nothing is pending any more."""
    try:
        clear_pending(pending_path(paths.target), nonce)
    except (OSError, HistoryError):
        pass  # a stale note only makes the next run compare its values


def _discard(staged: StagedFile | None) -> None:
    """Remove a staged snapshot that will not be committed (never raises)."""
    if staged is not None:
        try:
            staged.discard()
        except OSError:
            pass  # a stray dot file is harmless; the snapshot itself is untouched


def _pending_live(paths: Paths, fediverse_file: Path) -> bool:
    """True when an earlier attempt's history rows may lack their numbers write
    (a note that cannot be read counts as live: it proves nothing). The unit note
    of a snapshot whose numbers write did not land counts as live too."""
    try:
        if unit_pending(paths.target, fediverse_file):
            return True
        return bool(live_ids(read_note(pending_path(paths.target)), paths.target))
    except (OSError, HistoryError):
        return True


def _unit_state(
    paths: Paths,
    history: Path | None,
    fediverse_file: Path,
    stamp: datetime,
    run_id: str,
) -> tuple[list[Record], list[str], bool]:
    """Recompute from the files on disk whether the committed unit is complete.

    Returns the history rows the committed snapshot implies that history lacks,
    the platforms whose snapshot is dated after today, and whether EVERY part
    agrees: no staged or temp copy, no live pending note, no unsettled unit note
    (the numbers of the snapshot's run landed), no missing row, no future
    snapshot. The status ``committed: true`` is written only when this holds.
    Raises ``OSError``, ``HistoryError`` or ``ConfigError`` when a part cannot
    be read (the caller treats that as not complete)."""
    snapshots = load_snapshots(fediverse_file)
    rows = snapshot_rows(snapshots, run_id, stamp.date())
    late = future_snapshots(snapshots, stamp.date())
    missing: list[Record] = []
    if history is not None and rows:
        held = read_history(history) if history.exists() else None
        missing = [row for row in rows if not rows_present(held, [row])]
    agree = not (
        find_stale(fediverse_file)
        or _pending_live(paths, fediverse_file)
        or missing
        or late
    )
    return missing, late, agree


def _unit_complete(
    paths: Paths,
    history: Path | None,
    fediverse_file: Path,
    stamp: datetime,
    run_id: str,
) -> bool:
    """The final consistency check before ``committed: true`` (never raises)."""
    try:
        return _unit_state(paths, history, fediverse_file, stamp, run_id)[2]
    except (OSError, HistoryError, ConfigError):
        return False


def _recover(
    paths: Paths,
    history: Path | None,
    fediverse_file: Path,
    stamp: datetime,
    run_id: str,
) -> int | None:
    """Repair what an interrupted fediverse run left (table: ``fediverse_commit``).

    Runs first, under the refresh lock. The status is RECOMPUTED from the files
    (``_unit_state``), never trusted: when any part of the unit disagrees and the
    status says ``committed: true``, it is set to false BEFORE any repair step
    runs (so a crash during recovery cannot keep the stale true). Then it removes
    staged and temp copies (a staged copy is a run that never reached its rename:
    rolled back) and appends the history rows the committed snapshot implies and
    history lacks, whatever the age of the snapshot (never twice). Recovery never
    writes true; only the end of a complete run does. Returns an exit code on
    failure, ``None`` otherwise.
    """
    try:
        overflow: UnitFileTooLarge | None = None
        try:
            stale = find_stale(fediverse_file)
        except UnitFileTooLarge as exc:
            stale, overflow = [], exc  # too many to examine: fail closed below
        try:
            missing, late, agree = _unit_state(
                paths, history, fediverse_file, stamp, run_id
            )
        except (OSError, HistoryError, ConfigError):
            missing, late, agree = [], [], False  # an unreadable part is not complete
        if (not agree or overflow is not None) and status_committed(
            paths.status
        ) is True:
            write_atomic(
                paths.status,
                render_status(load_status(paths.status), [], stamp, False),
            )
        if overflow is not None:
            raise overflow  # status is false; every file stays as evidence
        # After the demotion above: a note of a transaction that never crossed
        # the commit point (its proposed snapshot is not the committed one, or its
        # staged copy is still here) is removed, so it cannot block the status.
        abandon_unit(paths.target, fediverse_file, stale)
        remove_stale(stale)
        for kind in late:
            print(
                f"{platform_key(kind)}: {SNAPSHOT_IN_FUTURE}, its history rows wait",
                file=sys.stderr,
            )
        if history is not None and missing:
            append_records(history, missing, now=stamp, skip_days=True)
    except (OSError, HistoryError, ConfigError) as exc:
        print(
            f"error: the previous run could not be repaired: {type(exc).__name__}",
            file=sys.stderr,
        )
        return EXIT_WRITE
    return None


def _same_day(
    fed: FediverseResult, history: Path, stamp: datetime, run_id: str
) -> FediverseResult | None:
    """Keep the stored snapshot of a platform whose day already has history rows
    with other values (the rule in ``fediverse_commit``); ``None`` when history
    cannot be read."""
    rows = snapshot_rows(fed.snapshots, run_id, stamp.date())
    try:
        held = read_history(history) if history.exists() else None
    except (OSError, HistoryError):
        return None
    kept = day_conflicts(held, rows) if held is not None else set()
    if not kept:
        return fed
    return FediverseResult(
        {k: v for k, v in fed.snapshots.items() if k not in kept},
        {k: v for k, v in fed.aggregates.items() if k not in kept},
        [
            Outcome(item.platform, item.kind, "skipped", None, SAME_DAY_KEPT)
            if item.platform in {platform_key(k) for k in kept}
            else item
            for item in fed.outcomes
        ],
    )


def _write_failed(exc: Exception) -> None:
    reason = exc.strerror if isinstance(exc, OSError) else str(exc)
    print(f"error: cannot write the result files: {reason}", file=sys.stderr)


def _insights(
    sources: list[Source],
    outcomes: list[Outcome],
    environment: Mapping[str, str],
    transport: Transport | None,
    moment: datetime,
    history: Path | None,
) -> tuple[list[Figure], list[InsightFailure]]:
    """Opt-in views and engagement numbers; only stored in the history file."""
    if history is None:
        return [], []
    fetch = partial(
        fetch_json,
        transport=transport if transport is not None else default_transport(),
        sleep=default_sleep,
    )
    ok = frozenset(o.platform for o in outcomes if o.status == "ok")
    return collect(list(sources), ok, environment, fetch, moment)


def fit_batch(records: list[Record]) -> tuple[list[Record], list[str]]:
    """The records to append, and the platforms whose analytics were left out.

    Within ``MAX_BATCH`` nothing changes. Over it, every private analytics record
    (YouTube Analytics, Instagram insights; the ``PRIVATE_PROVIDERS`` tags) is
    dropped (never a subscriber or another source's record) and the platforms
    that lost figures are named, so one too-large source fails alone."""
    if len(records) <= MAX_BATCH:
        return records, []
    private = [r for r in records if r.api_provider in PRIVATE_PROVIDERS]
    kept = [r for r in records if r.api_provider not in PRIVATE_PROVIDERS]
    return kept, sorted({r.platform for r in private})


def _retention_notice(history: Path, stamp: datetime) -> None:
    """Tell the owner, every run, how many private analytics records are past the
    30-day rule: YouTube Analytics (#391) and Instagram insights (#393), each on
    its own line. A count only, never a value. Removal is the history tombstone
    tool (#586); this run changes nothing."""
    try:
        records = list(read_history(history).records)
    except HistoryError:
        return  # a missing or damaged file is reported by the history work itself
    _retention_notice_for(records, stamp)


def _retention_notice_for(records: list[Record], stamp: datetime) -> None:
    today = stamp.astimezone(UTC).date()
    count = len(deletion_candidates(records, today))
    if count:
        print(
            f"youtube: retention notice: {count} analytics record(s) are past "
            f"{RETAIN_DAYS} days; remove them with the history tombstone tool (#586)",
            file=sys.stderr,
        )
    count = len(instagram_deletion_candidates(records, today))
    if count:
        print(
            f"instagram: retention notice: {count} insights record(s) are past "
            f"{INSTAGRAM_RETAIN_DAYS} days; remove them with the history tombstone "
            "tool (#586)",
            file=sys.stderr,
        )


def _retention_checks(
    history: Path, stamp: datetime, policy: RetentionPolicy | None
) -> None:
    """Both retention checks of a refresh from ONE read of the history file (the
    refresh lock is held, and a file can be large). Read-only (#620)."""
    try:
        records = list(read_history(history).records)
    except HistoryError:
        return  # a missing or damaged file is reported by the history work itself
    _retention_notice_for(records, stamp)
    if policy is not None and policy.retain_days:
        try:
            warnings = retention_warnings(records, policy, stamp.astimezone(UTC).date())
        except ValueError as exc:
            print(f"history: {exc}; the retention check was skipped", file=sys.stderr)
            return
        for warning in warnings:
            print(warning.line(), file=sys.stderr)


def _print_token_health(
    sources: list[Source], outcomes: list[Outcome], stamp: datetime
) -> None:
    """One ``token-health:`` line per token-holding source. Never a value; a
    warning here changes no exit code (a rejected token already failed)."""
    status = {
        o.platform: {"status": o.status, "detail": o.detail or ""} for o in outcomes
    }
    for item in assess(sources, status, stamp.astimezone(UTC).date()):
        print(item.line(), file=sys.stdout if item.state == TOKEN_OK else sys.stderr)


def _cycle(
    paths: Paths,
    sources: list[Source],
    accept: frozenset[str],
    environment: Mapping[str, str],
    transport: Transport | None,
    now: Callable[[], datetime],
    history: Path | None = None,
    sinks: Sinks | None = None,
    retention: RetentionPolicy | None = None,
    fediverse: list[FediverseSource] | None = None,
    fediverse_file: Path | None = None,
) -> int:
    try:
        previous = (
            dict(load_numbers(paths.target).entries) if paths.target.exists() else {}
        )
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    stamp = now()
    run_id = new_run_id(stamp)
    quarantined = False
    if fediverse_file is not None:
        # First, before any read: repair what an interrupted run left.
        repaired = _recover(paths, history, fediverse_file, stamp, run_id)
        if repaired is not None:
            return repaired
        # After the demotion in ``_recover``: a note that cannot be parsed moves
        # aside, and this run keeps the status false (the next good run settles).
        try:
            quarantined = quarantine_unit(paths.target)
        except OSError as exc:
            print(
                f"error: the previous run could not be repaired: {type(exc).__name__}",
                file=sys.stderr,
            )
            return EXIT_WRITE
        if quarantined:
            print(f"fediverse: {UNIT_NOTE_QUARANTINED}", file=sys.stderr)
    numbers, outcomes = refresh(
        sources,
        previous,
        env=environment,
        transport=transport if transport is not None else default_transport(),
        now=lambda: stamp,
        sleep=default_sleep,
        accept_drop=accept,
        token_sink=sinks.pair if sinks is not None else None,
        meta_sink=sinks.meta if sinks is not None else None,
        tiktok_sink=sinks.pair if sinks is not None else None,
    )
    fed = (
        run_fediverse(
            fediverse,
            now=stamp,
            transport=transport if transport is not None else default_transport(),
            sleep=default_sleep,
        )
        if fediverse and fediverse_file is not None
        else FediverseResult({}, {}, [])
    )
    if history is not None and fed.snapshots:
        # The same-day rule: one row set per platform and day, never rewritten.
        ruled = _same_day(fed, history, stamp, run_id)
        if ruled is None:
            print(
                "error: history not read, nothing was changed",
                file=sys.stderr,
            )
            return EXIT_WRITE
        fed = ruled
    for outcome in fed.outcomes:
        if outcome.status == "ok":
            print(f"{outcome.platform}: complete {outcome.count} posts")
        elif outcome.status == "skipped":
            print(
                f"{outcome.platform}: skipped ({outcome.detail}), "
                "history holds another value for today, the stored snapshot was kept"
            )
        else:
            print(
                f"{outcome.platform}: incomplete ({outcome.detail}), "
                "the previous snapshot was kept",
                file=sys.stderr,
            )
    for outcome in outcomes:
        if outcome.status == "ok":
            print(f"{outcome.platform}: ok {outcome.count}")
        elif outcome.status == "skipped":
            print(f"{outcome.platform}: skipped ({outcome.detail})")
        else:
            print(f"{outcome.platform}: error ({outcome.detail})", file=sys.stderr)
            if outcome.detail in SUSPECT_KINDS:
                print(
                    f"  the previous number was kept; if the change is real, rerun "
                    f"with --accept-drop {outcome.platform}",
                    file=sys.stderr,
                )
    _print_token_health(sources, outcomes, stamp)
    # The status file goes first and the numbers file last, so a failure can never
    # leave new numbers with no record of the run. If the numbers write fails, the
    # status is rewritten with ``committed: false`` (best effort) and the exit is 1.
    # A fediverse read that finished also counts as a change: the numbers file is
    # then written again (as it was when no number changed), so the pending note
    # of this run is settled the same way for both kinds of rows.
    changed = any(outcome.status == "ok" for outcome in outcomes) or bool(fed.snapshots)
    staged: Staged | None = None
    figures, insight_failures = _insights(
        sources, outcomes, environment, transport, now(), history
    )
    for failure in insight_failures:
        print(f"{failure.platform}: insights error ({failure.word})", file=sys.stderr)
    # A run that read a fediverse snapshot is ONE unit (#940) with ONE commit
    # point, the rename of the staged snapshot. The order is: recover what the last
    # run left (start of the cycle), stage the snapshot beside its file, append
    # the ordinary history rows (the pending note covers a later failure), write
    # the status as ``committed: false``, RENAME the snapshot into place, append
    # the fediverse rows derived from it, write the numbers file, settle the
    # pending note, and only then write the status as ``committed: true``. A
    # failure before the rename leaves the old snapshot byte for byte and a
    # status that never claims the commit. After it the snapshot is the truth and
    # whatever did not land is repaired by the next run. Design and crash table:
    # ``counts/fediverse_commit.py``.
    staged_snapshot: StagedFile | None = None
    proposed_digest = ""
    if fediverse_file is not None and fed.snapshots:
        try:
            snapshot_text = dump_snapshots(
                {**load_snapshots(fediverse_file), **fed.snapshots}
            )
            proposed_digest = content_digest(snapshot_text)
            staged_snapshot = stage_file(fediverse_file, snapshot_text, write_atomic)
        except (OSError, ConfigError) as exc:
            _write_failed(exc)
            return EXIT_WRITE
    if history is not None and changed:
        # History goes first: if it cannot be written, the status and numbers
        # files are left exactly as they were. Only values that were fetched and
        # accepted are recorded; a failed or skipped source leaves a gap (the
        # status file holds its result), never a zero. One value per platform,
        # metric and UTC day: a repeated run the same day keeps the first, so a
        # retry after a later failure is safe.
        fetched = [
            Record(
                platform=outcome.platform,
                metric=HISTORY_METRIC.get(outcome.kind, "followers"),
                value=outcome.count,
                as_of_utc=utc_stamp(stamp),
                source="api",
                run_id=run_id,
                # by the kind of source, never by its display slug
                api_provider=(
                    "youtube_data_api" if outcome.kind == "youtube" else None
                ),
            )
            for outcome in outcomes
            if outcome.status == "ok" and outcome.count is not None
        ] + [
            Record(
                platform=figure.platform,
                metric=figure.metric,
                value=figure.value,
                as_of_utc=figure.as_of_utc or utc_stamp(stamp),
                source="api",
                run_id=run_id,
                note=figure.note,
                api_provider=figure.api_provider,
            )
            for figure in figures
        ]
        # The fediverse aggregates are not here: their rows are derived from the
        # committed snapshot and appended after the commit point (see
        # ``fediverse_commit``), so a run that never commits leaves no row.
        # One append takes at most MAX_BATCH records. If the assembled batch is
        # over it, only the analytics figures of this run are left out (they are
        # reported as too_large); subscribers and other sources are never dropped.
        fetched, too_big = fit_batch(fetched)
        for platform in too_big:
            print(f"{platform}: insights error (too_large)", file=sys.stderr)
            insight_failures.append(InsightFailure(platform, "too_large"))
        if fetched:
            try:
                staged = _stage_history(
                    history, paths, fetched, run_id, _expected_digest(numbers)
                )
                fetched = staged.records
            except OSError as exc:
                _discard(staged_snapshot)
                _write_failed(exc)
                return EXIT_WRITE
            except HistoryError as exc:
                _discard(staged_snapshot)
                print(
                    f"error: history not written, nothing else was changed: {exc}",
                    file=sys.stderr,
                )
                return EXIT_WRITE
            try:
                kept = append_records(history, fetched, now=stamp, skip_days=True)
            except HistoryError as exc:
                _discard(staged_snapshot)
                _withdraw(paths, history, staged, exc)
                print(
                    f"error: history not written, nothing else was changed: {exc}",
                    file=sys.stderr,
                )
                return EXIT_WRITE
            if kept < len(fetched):
                print("history: today's value was already recorded; kept as it is")
    if history is not None:
        _retention_checks(history, stamp, retention)
    previous_status = load_status(paths.status)
    reported = outcomes + fed.outcomes
    # Legacy order (no snapshot in this run): status first as ``committed: true``,
    # rewritten as false if the numbers write fails. A run with a snapshot follows
    # the unit order described above: false first, true last.
    # A fediverse run that changed nothing must not say ``true`` while an earlier
    # attempt's note is still live: that status would hide the unfinished attempt.
    # A unit run (a snapshot in this run, or any state in which the unit is not
    # complete: a live note, an unsettled unit note, a missing row, a snapshot
    # dated after today) follows the unit order: ``false`` first, and ``true``
    # only after a final consistency check in this very run. An unfinished
    # attempt is never hidden by a later run that found nothing new to write.
    unit = fediverse_file is not None and (
        staged_snapshot is not None
        or quarantined
        or not _unit_complete(paths, history, fediverse_file, stamp, run_id)
    )
    try:
        write_atomic(
            paths.status,
            render_status(previous_status, reported, stamp, not unit),
        )
    except (OSError, ConfigError) as exc:
        _discard(staged_snapshot)
        _write_failed(exc)
        return EXIT_WRITE
    unit_note: UnitNote | None = None
    if changed:
        if staged_snapshot is not None:
            try:
                # The unit note, a transaction record: the digest of the proposed
                # snapshot, of the numbers it belongs to and a fresh generation
                # id. It exists before the commit point, so a crash after the
                # rename can never leave a snapshot with no trace of its numbers.
                digest = _expected_digest(numbers)
                if digest is None:
                    raise ConfigError(str(paths.target), None, "numbers not valid")
                unit_note = UnitNote(proposed_digest, digest, new_generation())
                write_unit(paths.target, unit_note)
            except (OSError, ConfigError) as exc:
                _discard(staged_snapshot)
                _write_failed(exc)
                return EXIT_WRITE
            try:
                # THE commit point: one rename. The stored snapshots of platforms
                # that failed this run stay (they were merged when staging). A
                # failure leaves the old snapshot and everything else untouched.
                staged_snapshot.commit()
            except OSError as exc:
                _discard(staged_snapshot)
                discard_unit(paths.target)  # never crossed the commit point
                _write_failed(exc)
                return EXIT_WRITE
            if history is not None and fediverse_file is not None:
                # Rows derived from the snapshot now on disk (never twice).
                rows = snapshot_rows(
                    load_snapshots(fediverse_file), run_id, stamp.date()
                )
                try:
                    if rows:
                        append_records(history, rows, now=stamp, skip_days=True)
                except (OSError, HistoryError) as exc:
                    # Committed, status still false: the next run appends them.
                    print(
                        f"error: history rows not written: {type(exc).__name__}",
                        file=sys.stderr,
                    )
                    return EXIT_WRITE
        try:
            write_atomic(paths.target, dump_numbers(numbers))
        except (OSError, ConfigError) as exc:
            if not unit:
                try:
                    write_atomic(
                        paths.status,
                        render_status(previous_status, reported, stamp, False),
                    )
                except (OSError, ConfigError):
                    pass  # the numbers file is untouched either way
            _write_failed(exc)  # a unit's status already says ``committed: false``
            return EXIT_WRITE
        if fediverse_file is not None:
            # A numbers file was written after the snapshot: the unit note is done.
            if unit_note is not None:
                try:
                    mark_landed(paths.target, unit_note)  # this generation's proof
                except OSError:
                    pass  # the note stays unsettled; a later run writes numbers
            try:
                unit_path(paths.target).unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass  # still there: unit_pending() judges it by its digest
        if staged is not None:
            _settle_history(paths, staged.nonce)
    if (
        unit
        and not quarantined
        and fediverse_file is not None
        and _unit_complete(paths, history, fediverse_file, stamp, run_id)
    ):
        # The final consistency check passed in this very run: every part of the
        # unit (snapshot, history rows, numbers, notes) agrees. Only now: true.
        try:
            write_atomic(
                paths.status, render_status(previous_status, reported, stamp, True)
            )
        except (OSError, ConfigError) as exc:
            _write_failed(exc)  # the status keeps saying ``committed: false``
            return EXIT_WRITE
    failed = any(outcome.status == "error" for outcome in reported) or bool(
        insight_failures
    )
    return EXIT_SOURCE_FAILED if failed else EXIT_OK


def _retention_policy(config: SiteConfig) -> RetentionPolicy:
    if config.numbers is None:
        return RetentionPolicy()
    return RetentionPolicy(
        retain_days=dict(config.numbers.retain_days),
        warn_days=config.numbers.retain_warn_days,
    )


def history_path(
    config: SiteConfig, config_path: Path, paths: Paths | None = None
) -> Path | None:
    """The history file named in the config, or ``None`` when the store is off.

    The path is relative to the config file's folder and must stay inside it
    once symlinks in its folders are resolved. It may not be a symlink, nor the
    config, numbers, status or lock file.
    """
    if config.numbers is None or config.numbers.history_file is None:
        return None
    named = Path(config.numbers.history_file)
    root = config_path.resolve(strict=True).parent
    try:
        parent = (root / named).parent.resolve(strict=True)
    except OSError:
        raise ValueError("the folder for numbers.history_file does not exist") from None
    if not parent.is_relative_to(root):
        raise ValueError("numbers.history_file leaves the config's folder")
    target = parent / named.name
    if target.is_symlink():
        raise ValueError("numbers.history_file must not be a symbolic link")
    if paths is not None:
        for other in (paths.config, paths.target, paths.status, paths.lock):
            if target == other or (
                target.exists() and other.exists() and target.samefile(other)
            ):
                raise ValueError(
                    "numbers.history_file must not be the config, numbers, "
                    "status or lock file"
                )
    return target


def _history_target(args: argparse.Namespace) -> Path:
    if args.file is not None:
        return Path(args.file)
    config = load_site_config(args.config)
    target = history_path(config, args.config)
    if target is None:
        raise ValueError("the config has no numbers.history_file; the store is off")
    return target


def _as_of(text: str) -> str:
    return f"{text}T00:00:00Z" if len(text) == 10 else text


def run_history_add(args: argparse.Namespace) -> int:
    try:
        target = _history_target(args)
    except (ConfigError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    provider: str | None = None
    if args.correction:
        # A correction keeps the provenance of the record it fixes (the history
        # validator refuses any other). No original: left untagged, and the
        # append then refuses it for having no earlier record.
        try:
            earlier = read_history(target).records
        except HistoryError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_WRITE
        key = (args.platform, args.metric, _as_of(args.as_of), args.source)
        provider = next(
            (r.api_provider for r in earlier if r.type == "record" and r.key == key),
            None,
        )
    record = Record(
        platform=args.platform,
        metric=args.metric,
        value=args.value,
        as_of_utc=_as_of(args.as_of),
        source=args.source,
        run_id=new_run_id(datetime.now(UTC)),
        note=args.note,
        era=args.era,
        type="correction" if args.correction else "record",
        api_provider=provider,
    )
    try:
        append_records(target, [record])
    except HistoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_WRITE
    print(f"{args.platform} {args.metric}: appended {args.value}")
    return EXIT_OK


def run_history_verify(args: argparse.Namespace) -> int:
    try:
        target = _history_target(args)
    except (ConfigError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        history = read_history(target)
    except HistoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_WRITE
    print(f"ok: {len(history.records)} records, hash chain intact")
    return EXIT_OK


def run_history_compare(args: argparse.Namespace) -> int:
    """Append-only (exit 0), a valid tombstone-only rewrite (10), else refuse (1).

    Prints counts and fixed words only: never a value, a note or a digest.
    """
    try:
        old = read_regular(args.old, COMPARE_MAX_BYTES)
        new = read_regular(args.new, COMPARE_MAX_BYTES)
        manifest = (
            read_regular(args.manifest, MAX_MANIFEST_BYTES)
            if args.manifest is not None
            else None
        )
        numbers_old = (
            read_regular(args.numbers_old, MAX_NUMBERS_BYTES)
            if args.numbers_old is not None
            else None
        )
        numbers_new = (
            read_regular(args.numbers_new, MAX_NUMBERS_BYTES)
            if args.numbers_new is not None
            else None
        )
        result = compare_history(
            old,
            new,
            allow_tombstones=args.allow_tombstones,
            manifest=manifest,
            manifest_name=args.manifest_name,
            pr_day=args.pr_day,
            numbers_old=numbers_old,
            numbers_new=numbers_new,
        )
    except HistoryError as exc:
        _say(f"refused: {exc}", sys.stderr)
        return EXIT_WRITE
    if result.mode == "append":
        _say("ok: append-only")
        return EXIT_OK
    _say(
        f"ok: tombstone-only rewrite, {result.replaced} records replaced, "
        f"{result.dropped} corrections dropped"
    )
    return EXIT_REWRITE


def _safe(text: str) -> str:
    """``text`` with every control or non-ASCII character escaped, for a terminal."""
    return "".join(
        c if " " <= c <= "~" else c.encode("unicode_escape").decode("ascii")
        for c in text
    )


def _say(message: str, stream: TextIO | None = None) -> None:
    """Print one control-safe line: a file name can hold an escape sequence."""
    print(_safe(message), file=stream if stream is not None else sys.stdout)


INSTALL_NOT_STARTED = "not-started"  # only the temporary name exists
INSTALL_LINKED = "linked"  # both names exist
INSTALL_UNLINKED = "unlinked"  # only the final name exists, not yet synced
INSTALL_SYNCED = "synced"  # only the final name exists, folder synced


class _StagedManifest:
    """The manifest of a derived redaction, written but not yet installed (#619).

    The order is: check every condition, write the content to a private
    temporary file in the same folder, replace the history file, then link the
    temporary file to its final name (never replacing a file) and remove it. The
    folder is held as a descriptor opened without following symlinks, so a
    ``redactions`` symlink is refused and nothing is created outside it.

    Residual: the history file and the manifest are two files, so a crash or a
    failure of the last step (after the history file was replaced) leaves the
    rewritten history with the manifest still under its temporary name; the
    message names a no-overwrite step that completes it. The temporary file
    and a newly created folder are fsynced before the history file is replaced.
    """

    def __init__(
        self, folder_fd: int, run_id: str, tmp_name: str, folder_path: str
    ) -> None:
        self.folder_fd = folder_fd
        # The absolute path of the redactions folder (shell-quoted), for the
        # operator-facing text only; the code works through folder_fd.
        self.folder_path = folder_path
        self.final_name = f"{run_id}.manifest"
        self.tmp_name = tmp_name
        # The last completed step of install(), for recovery guidance.
        self.state = INSTALL_NOT_STARTED

    def discard(self) -> None:
        """Remove the temporary file and close the folder."""
        try:
            os.unlink(self.tmp_name, dir_fd=self.folder_fd)
        except OSError:
            pass
        self.close()

    def close(self) -> None:
        try:
            os.close(self.folder_fd)
        except OSError:
            pass

    def install(self) -> None:
        """Give the manifest its final name.

        ``self.state`` names the last completed operation (not-started, linked,
        unlinked, synced), so a failure or an interruption at any point leaves
        an accurate record of which names exist. The folder stays open; the
        caller closes it.
        """
        os.link(
            self.tmp_name,
            self.final_name,
            src_dir_fd=self.folder_fd,
            dst_dir_fd=self.folder_fd,
            follow_symlinks=False,
        )
        self.state = INSTALL_LINKED
        os.unlink(self.tmp_name, dir_fd=self.folder_fd)
        self.state = INSTALL_UNLINKED
        os.fsync(self.folder_fd)
        self.state = INSTALL_SYNCED


def _stage_manifest(
    target: Path, plan: list[Record], args: argparse.Namespace, moment: datetime
) -> _StagedManifest:
    """Check every manifest condition and write its content, before any rewrite.

    Keys and codes only, in the bytes the rewrite check rebuilds. Raises
    ``HistoryError`` (fixed words) when the folder is a symlink or not a folder,
    the manifest already exists, or the folder cannot be written.
    """
    run_id, _, text = rewrite_metadata(
        [r.key for r in plan],
        args.reason,
        args.trigger_date,
        moment.strftime("%Y-%m-%d"),
    )
    final = f"{run_id}.manifest"
    tmp = f".{run_id}.manifest.tmp-{os.getpid()}"
    try:
        parent_fd = _open_parent(target, strict=True)
    except OSError:
        raise HistoryError("cannot open the history folder") from None
    folder_fd = -1
    try:
        try:
            os.mkdir("redactions", 0o755, dir_fd=parent_fd)
        except FileExistsError:
            pass
        else:
            # A new folder: make its entry durable before anything relies on it.
            os.fsync(parent_fd)
        folder_fd = os.open(
            "redactions",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    except OSError:
        raise HistoryError(
            "the redactions folder is a symlink, not a folder or not creatable; "
            "nothing was written"
        ) from None
    finally:
        os.close(parent_fd)
    try:
        try:
            os.stat(final, dir_fd=folder_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise HistoryError("the manifest already exists; nothing was written")
        out_fd = os.open(
            tmp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=folder_fd,
        )
        try:
            with os.fdopen(out_fd, "wb") as out:
                out.write(text.encode("ascii"))
                out.flush()
                os.fchmod(out.fileno(), 0o644)
                os.fsync(out.fileno())
            # The file is closed; now make its directory entry durable too,
            # before the history file is replaced.
            os.fsync(folder_fd)
        except OSError:
            try:
                os.unlink(tmp, dir_fd=folder_fd)
            except OSError:
                pass
            raise
    except HistoryError:
        os.close(folder_fd)
        raise
    except OSError:
        os.close(folder_fd)
        raise HistoryError("cannot write the manifest; nothing was written") from None
    folder_path = shlex.quote(
        os.path.join(os.path.abspath(target.parent), "redactions")
    )
    return _StagedManifest(folder_fd, run_id, tmp, folder_path)


def _recovery_step(staged: _StagedManifest) -> str:
    """The no-overwrite step that gives a retained manifest its final name."""
    return (
        f"in the folder {staged.folder_path} first compare any existing "
        f"{staged.final_name} with {staged.tmp_name} using cmp (never "
        "overwrite it), then run: "
        f"ln {staged.tmp_name} {staged.final_name} && rm {staged.tmp_name}"
    )


def _retain_after_interruption(staged: _StagedManifest) -> None:
    """An ambiguous stop: keep the only copy of the manifest and say what to do."""
    _say(
        "error: the redaction stopped and it is not known whether the history "
        "file was rewritten; the manifest was kept under its temporary name. "
        "Run history-verify. If the history file was not rewritten, remove "
        f"{staged.tmp_name} from the folder {staged.folder_path}. If it was "
        "rewritten, " + _recovery_step(staged),
        sys.stderr,
    )
    staged.close()


def _observed_install_state(staged: _StagedManifest) -> str:
    """Which names exist now, read through the held folder descriptor.

    An interruption can land between an operation and the line that records it,
    so the recorded state is the fallback, not the authority.
    """

    def look(name: str) -> os.stat_result | bool | None:
        try:
            return os.stat(name, dir_fd=staged.folder_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError:
            return None

    tmp, final = look(staged.tmp_name), look(staged.final_name)
    if tmp is None or final is None:
        return staged.state
    if isinstance(tmp, os.stat_result) and isinstance(final, os.stat_result):
        # Both names: ours (one file) or a final file somebody else made.
        same = (tmp.st_dev, tmp.st_ino) == (final.st_dev, final.st_ino)
        return INSTALL_LINKED if same else INSTALL_NOT_STARTED
    if isinstance(final, os.stat_result):
        return INSTALL_UNLINKED
    return INSTALL_NOT_STARTED


def _install_failure_message(staged: _StagedManifest) -> str:
    """Recovery guidance for the state install() reached (never says ln when
    the final name already exists)."""
    state = _observed_install_state(staged)
    if state == INSTALL_LINKED:
        return (
            "error: the history file was rewritten and the manifest was linked "
            f"as {staged.final_name} but the temporary name {staged.tmp_name} "
            f"was not removed; in the folder {staged.folder_path} verify the two files "
            "are identical with cmp, then run: "
            f"cmp {staged.tmp_name} {staged.final_name} && rm {staged.tmp_name}"
        )
    if state in (INSTALL_UNLINKED, INSTALL_SYNCED):
        return (
            "error: the history file was rewritten and the manifest is "
            f"installed as {staged.final_name} in the folder {staged.folder_path}, "
            "but the folder was "
            "not confirmed durable; nothing else to do: run sync on the "
            "folder or run history-verify again"
        )
    return (
        "error: the history file was rewritten but the manifest could not be "
        "installed; " + _recovery_step(staged)
    )


def _install_staged(staged: _StagedManifest) -> bool:
    """Install the manifest after the rewrite; on failure name the recovery step."""
    try:
        staged.install()
    except BaseException as stop:
        _say(_install_failure_message(staged), sys.stderr)
        if not isinstance(stop, Exception):
            raise
        return False
    finally:
        staged.close()
    _say(f"wrote the manifest redactions/{staged.final_name} (commit it with the file)")
    return True


def run_history_redact(args: argparse.Namespace) -> int:
    try:
        target = _history_target(args)
    except (ConfigError, ValueError, OSError) as exc:
        _say(f"error: {exc}", sys.stderr)
        return EXIT_USAGE
    try:
        history = read_history(target)
        plan = plan_redaction(
            history,
            platform=args.platform,
            source=args.source,
            before=args.before,
            metric=args.metric,
            include_hand_kept=args.include_hand_kept,
            provider=args.provider,
        )
        stale = find_stale_redaction_temps(target)
        if not args.confirm:
            # Counts and dates only: never a value, note or hash.
            span = (
                f"{min(r.as_of_utc for r in plan)[:10]} to "
                f"{max(r.as_of_utc for r in plan)[:10]}"
                if plan
                else "none"
            )
            _say(
                f"dry run: {len(plan)} lines would be redacted (as-of days {span}); "
                "nothing written. Add --confirm to redact."
            )
            if stale:
                _say(
                    f"{len(stale)} stale redaction temporary file(s) found "
                    "(--confirm removes them): " + ", ".join(stale)
                )
            unmarked = find_unmarked_redaction_temps(target)
            if unmarked:
                _say(
                    f"{unmarked} file(s) named like a redaction temporary have no "
                    "marker (a crash before it was written?); they are never "
                    "removed automatically; look at them and remove them by hand"
                )
            return EXIT_OK
        check_redaction_arguments(args.reason, args.trigger_date)
        removed = remove_stale_redaction_temps(target, stale)
        if removed:
            _say(f"removed {removed} stale redaction temporary file(s)")
        notices: list[str] = []
        moment = datetime.now(UTC)
        # Every manifest condition is checked and its bytes written to a private
        # temporary file BEFORE the history file is touched (#619).
        staged = (
            _stage_manifest(target, plan, args, moment)
            if (args.derived and plan)
            else None
        )
        try:
            count = redact_records(
                target,
                plan,
                reason=args.reason,
                trigger_date=args.trigger_date,
                now=moment,
                confirm=True,
                include_hand_kept=args.include_hand_kept,
                notices=notices,
                derived=args.derived,
            )
        except RedactDurabilityError:
            # The new file is in place: complete the pair, then report.
            if staged is not None:
                _install_staged(staged)
            raise
        except BaseException as stop:
            # Only a plain HistoryError positively reports that the history file
            # was not replaced (redact_records raises RedactDurabilityError for
            # every outcome after the replacement). Any other stop, an interrupt
            # included, is ambiguous: keep the staged manifest.
            if staged is not None:
                if isinstance(stop, HistoryError):
                    staged.discard()
                else:
                    _retain_after_interruption(staged)
                    if isinstance(stop, Exception):
                        return EXIT_REPLACED_UNSURE
            raise
        finally:
            for notice in notices:
                _say(notice)
        if staged is not None:
            if not count:
                staged.discard()
            elif not _install_staged(staged):
                return EXIT_REPLACED_UNSURE
    except OldCopyRemainsError as exc:
        _say(f"error: {exc}; the file is {exc.temp_name}", sys.stderr)
        _say(
            "if the retry fails too, remove that file by hand: the old values "
            "stay on disk until it is gone",
            sys.stderr,
        )
        return EXIT_REPLACED_UNSURE
    except RedactDurabilityError as exc:
        # Fixed words, never content. Exit 5: the state needs checking.
        _say(f"error: {exc}", sys.stderr)
        return EXIT_REPLACED_UNSURE
    except HistoryError as exc:
        _say(f"error: {exc}", sys.stderr)
        return EXIT_WRITE
    _say(f"redacted {count} lines; run history-verify, then commit the file")
    return EXIT_OK


def _dispatch(args: argparse.Namespace) -> int:
    if args.counts_command == "history-add":
        return run_history_add(args)
    if args.counts_command == "history-verify":
        return run_history_verify(args)
    if args.counts_command == "history-redact":
        return run_history_redact(args)
    if args.counts_command == "history-compare":
        return run_history_compare(args)
    if args.counts_command == "history-export-imported":
        return run_export_imported(args.file, args.out)
    if args.counts_command == "history-import-beacons":
        return run_beacons_import(args.config, args.csv, args.file, args.dry_run)
    if args.counts_command == "import":
        return _run_import(args)
    if args.counts_command == "stale":
        return _run_stale(args)
    if args.counts_command == "egress-check":
        return _run_egress_check(args)
    if args.counts_command == "tokens":
        return _run_tokens(args)
    return _run(args)


def _where(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--config", type=Path, help="site config naming numbers.history_file"
    )
    group.add_argument("--file", type=Path, help="the history file itself")


def _register_history(
    actions: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    add = actions.add_parser(
        "history-add",
        help="append one hand-kept value to the history file",
        description="Append a value you read yourself (a LinkedIn or Kick follower "
        "count, say). Exit 0 appended, 1 refused (duplicate, damaged file, ...), "
        "2 usage or config error. Earlier lines are never changed.",
    )
    _where(add)
    add.add_argument("--platform", required=True)
    add.add_argument("--metric", default="followers")
    add.add_argument("--value", type=int, required=True)
    add.add_argument(
        "--as-of",
        required=True,
        help="UTC time YYYY-MM-DDTHH:MM:SSZ, or a UTC date YYYY-MM-DD",
    )
    add.add_argument(
        "--source", choices=("hand_kept", "api", "imported"), default="hand_kept"
    )
    add.add_argument("--note")
    add.add_argument("--era", help="label a definition change, e.g. a vendor rule")
    add.add_argument(
        "--correction",
        action="store_true",
        help="correct an earlier record with the same platform, metric, time and "
        "source (needs --note); the original line stays",
    )
    verify = actions.add_parser(
        "history-verify",
        help="check the whole history file",
        description="Exit 0 when every line parses and the hash chain is intact, "
        "1 when the file is damaged (including a truncated last line).",
    )
    _where(verify)
    compare = actions.add_parser(
        "history-compare",
        help="check that a new history file only appends, or only tombstones",
        description="Compare an old and a new history file. Exit 0 when the new "
        "file only appends canonical records; exit 10 (reserved for this "
        "command) when, with --allow-tombstones, it is a valid tombstone-only "
        "rewrite whose manifest and published numbers also match; any other "
        "status is a refusal. Prints counts and fixed words only.",
    )
    compare.add_argument("--old", required=True)
    compare.add_argument("--new", required=True)
    compare.add_argument("--allow-tombstones", action="store_true")
    compare.add_argument("--manifest")
    compare.add_argument("--manifest-name")
    compare.add_argument("--pr-day", help="UTC date YYYY-MM-DD the pull request opened")
    compare.add_argument("--numbers-old")
    compare.add_argument("--numbers-new")
    redact = actions.add_parser(
        "history-redact",
        help="replace stored api records with tombstones (owner-run, not a refresh)",
        description="Remove the value, note and era of matching records from the "
        "working file and re-chain it. Dry run by default (prints counts only); "
        "--confirm rewrites the file atomically. Only api rows unless "
        "--include-hand-kept. A tombstone does not remove the values from git "
        "history. Exit 0 done, 1 refused, 2 usage or config error, 5 the file was "
        "replaced (or may have been) but the result needs checking: run "
        "history-verify before anything else.",
    )
    _where(redact)
    redact.add_argument("--platform")
    redact.add_argument(
        "--provider",
        choices=API_PROVIDERS,
        help="select records by their api_provider tag (never by platform name)",
    )
    redact.add_argument("--metric")
    redact.add_argument("--source", choices=("api", "hand_kept", "imported"))
    redact.add_argument(
        "--before",
        help="UTC date YYYY-MM-DD: redact rows on earlier days (default: all days)",
    )
    redact.add_argument("--reason", choices=REASONS, required=True)
    redact.add_argument(
        "--trigger-date",
        required=True,
        help="YYYY-MM-DD of the revocation, request or expiry that started the clock",
    )
    redact.add_argument("--include-hand-kept", action="store_true")
    redact.add_argument(
        "--derived",
        action="store_true",
        help="write the fixed tombstone forms the reviewed pull request route "
        "accepts (day-granular time, derived run id) and a manifest file (#619)",
    )
    mode = redact.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="the default")
    mode.add_argument("--confirm", action="store_true", help="rewrite the file")
    beacons = actions.add_parser(
        "history-import-beacons",
        help="import a Beacons Link in Bio analytics CSV into a separate history file",
        description="Strictly validate a Beacons Link in Bio analytics CSV and write "
        "only its Daily views and clicks as imported history. The destination must "
        "not already contain data. Exit 0 imported, 2 refused.",
    )
    beacons.add_argument("--csv", type=Path, required=True, help="Beacons CSV export")
    beacons.add_argument(
        "--config",
        type=Path,
        required=True,
        help="site config whose collection history must remain separate",
    )
    beacons.add_argument(
        "--file", type=Path, required=True, help="new pre-cutover history file"
    )
    beacons.add_argument("--dry-run", action="store_true")
    export = actions.add_parser(
        "history-export-imported",
        help="write the small file the media kit reads from the imported history",
        description="Read the history file made by history-import-beacons and write "
        "the separate file named by kit.imported_history: views and clicks per "
        "month, nothing per day. The site build never reads the history file. "
        "Never overwrites a file. Exit 0 written, 2 refused.",
    )
    export.add_argument(
        "--file", type=Path, required=True, help="the imported history file"
    )
    export.add_argument(
        "--out", type=Path, required=True, help="new file for kit.imported_history"
    )


def run_export_imported(source: Path, out: Path) -> int:
    """Write the kit-readable aggregate file from an importer history file."""
    try:
        if out.is_symlink() or out.exists():
            raise ConfigError(str(out), None, "refusing to overwrite; name a new file")
        if source.resolve() == out.resolve():
            raise ConfigError(str(out), None, "must differ from the history file")
        text = export_imported(read_history(source))
        write_new_atomic(out, text)
    except (ConfigError, HistoryError, OSError) as exc:
        print(
            f"error: imported history not exported: {_safe(str(exc))}", file=sys.stderr
        )
        return EXIT_USAGE
    print(f"exported {len(parse_imported(text.encode()).periods)} monthly periods")
    return EXIT_OK


def run_beacons_import(
    config_path: Path, csv_path: Path, target: Path, dry_run: bool
) -> int:
    """Import Beacons Daily rows into a dedicated, previously empty history file."""
    try:
        config = load_site_config(config_path)
        collection_history = history_path(config, config_path)
        data = _read(csv_path, MAX_BEACONS_BYTES)
        export = parse_beacons_csv(data)
        if collection_history is not None and (
            target.resolve() == collection_history.resolve()
            or (
                target.exists()
                and collection_history.exists()
                and target.samefile(collection_history)
            )
        ):
            raise BeaconsImportError(
                "destination must differ from configured numbers.history_file"
            )
        if target.exists() and target.stat().st_size:
            raise BeaconsImportError(
                "destination history file is not empty; imported data is never merged"
            )
        if target.is_symlink() or target.is_dir():
            raise BeaconsImportError("destination must be a regular file path")
        records = [
            Record(
                platform=IMPORT_PLATFORM,
                metric=metric,
                value=value,
                as_of_utc=f"{day.day.isoformat()}T00:00:00Z",
                source=IMPORT_SOURCE,
                run_id=IMPORT_RUN_ID,
                note=IMPORT_NOTE,
            )
            for day in export.days
            for metric, value in (("views", day.views), ("clicks", day.clicks))
        ]
        serialized = dump_new_history(records)
        if dry_run:
            print(
                f"verified: {export.row_count} export rows, {len(export.days)} daily "
                f"rows, {len(records)} history records; dry run, nothing written"
            )
            return EXIT_OK
        write_atomic(target, serialized)
    except (BeaconsImportError, ConfigError, HistoryError, OSError) as exc:
        print(f"error: Beacons history not imported: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(
        f"verified: {export.row_count} export rows, {len(export.days)} daily rows, "
        f"{len(records)} history records"
    )
    return EXIT_OK


def _run(args: argparse.Namespace) -> int:
    spec = None
    if args.sink_kind == "doppler":
        held = getattr(args, "doppler_held_token", None)
        args.doppler_held_token = None
        try:
            spec = doppler_spec(args, os.environ, sys.stdin, held)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
        finally:
            if held is not None:
                held.clear()
    return run_refresh(
        args.config,
        args.out,
        args.only,
        args.dry_run,
        accept_drop=args.accept_drop,
        doppler=spec,
    )


def run_import(
    config_path: Path,
    csv_path: Path,
    out: Path | None,
    dry_run: bool,
    *,
    today: date | None = None,
    lock_wait: float = LOCK_WAIT_SECONDS,
) -> int:
    """Import hand-kept numbers from ``csv_path`` into the numbers file.

    Exit 0 done (or a dry run that would succeed), 2 the file or config is
    refused (nothing written), 1 the file could not be written.
    """
    try:
        config = load_site_config(config_path)
        paths = plan_paths(config_path, out)
        history = history_path(config, config_path, paths)
        data = _read(csv_path, MAX_CSV_BYTES)
    except (ConfigError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    day = today if today is not None else utc_today()
    try:
        with FileLock(paths.lock, wait=lock_wait):
            previous = (
                dict(load_numbers(paths.target).entries)
                if paths.target.exists()
                else {}
            )
            rows = parse_import(data, config, previous, day)
            if dry_run:
                print(
                    f"dry run: {len(rows)} number(s) would be imported; nothing written"
                )
                return EXIT_OK
            merged = merge(previous, rows)
            staged = None
            if history is not None:
                # History first, as in refresh: if it cannot be written, the
                # numbers file is left as it was. One value per platform, metric
                # and UTC day; a retry the same day keeps the first.
                stamp = datetime.now(UTC)
                run_id = new_run_id(stamp)
                staged = _stage_history(
                    history,
                    paths,
                    [
                        Record(
                            platform=row.key,
                            metric=row.metric,
                            value=row.count,
                            as_of_utc=f"{row.as_of.isoformat()}T00:00:00Z",
                            source="hand_kept",
                            run_id=run_id,
                        )
                        for row in rows
                    ],
                    run_id,
                    _expected_digest(merged),
                )
                try:
                    append_records(history, staged.records, now=stamp, skip_days=True)
                except (OSError, HistoryError) as failure:
                    _withdraw(paths, history, staged, failure)
                    raise
            write_atomic(paths.target, dump_numbers(merged))
            if staged is not None:
                _settle_history(paths, staged.nonce)
    except ImportFailed as exc:
        print("error: the file was not imported; nothing was written", file=sys.stderr)
        for problem in exc.problems:
            print(f"  {problem}", file=sys.stderr)
        return EXIT_USAGE
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except HistoryError as exc:
        print(
            f"error: history not written, nothing else was changed: {exc}",
            file=sys.stderr,
        )
        return EXIT_WRITE
    except LockBusy:
        print(
            "error: another counts run holds the lock; try again shortly",
            file=sys.stderr,
        )
        return EXIT_WRITE
    except LockLost as exc:
        print(f"error: {exc}; nothing was written", file=sys.stderr)
        return EXIT_WRITE
    except OSError as exc:
        print(f"error: cannot write the numbers file: {exc.strerror}", file=sys.stderr)
        return EXIT_WRITE
    for row in rows:
        print(f"{row.key}: imported (as of {row.as_of.isoformat()})")
    return EXIT_OK


def run_stale(
    config_path: Path,
    numbers_path: Path | None,
    remind_days: int,
    as_json: bool,
    *,
    today: date | None = None,
) -> int:
    """List hand-kept numbers due for an update. Values are never printed.

    Exit 0 nothing is due, 4 something is, 2 usage or config problem.
    """
    try:
        config = load_site_config(config_path)
        path = numbers_path or config_path.parent / "numbers.json"
        numbers = load_numbers(path) if numbers_path or path.exists() else None
    except (ConfigError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    day = today if today is not None else utc_today()
    notes = due_numbers(config, numbers, day, remind_days)
    items = [
        {
            "scope": note.scope,
            "platform": note.platform,
            "id": note.item_id,
            "as_of": note.as_of.isoformat() if note.as_of else None,
            "days": note.days,
            "limit": note.limit,
            "status": "stale" if note.stale else "reminder",
            "days_over": max(0, note.days - note.limit),
        }
        for note in sorted(
            notes, key=lambda n: (-n.days, n.scope, n.platform, n.item_id)
        )
    ]
    if as_json:
        report = {
            "date": day.isoformat(),
            "remind_days": remind_days,
            "due": items,
            "stale_count": sum(1 for i in items if i["status"] == "stale"),
        }
        print(json.dumps(report, indent=2, sort_keys=True))
    elif not items:
        print("no hand-kept number is due")
    else:
        for item in items:
            print(
                f"{item['status']}: {item['platform']} ({item['scope']} {item['id']}) "
                f"as of {item['as_of']}, {item['days']} days old, limit {item['limit']}"
            )
    return EXIT_DUE if items else EXIT_OK


_TOKEN_RENDERERS: dict[str, Callable[[list[TokenHealth]], str]] = {
    "text": render_text,
    "json": render_json,
    "markdown": render_markdown,
    "github": render_github,
}


def run_tokens(
    config_path: Path,
    status_path: Path | None,
    fmt: str,
    *,
    today: date | None = None,
) -> int:
    """Token health from the config and the last status file. No network, no
    secret, no value. Exit 0 ok, 4 expiring, 3 expired or rejected, 2 usage."""
    try:
        config = load_site_config(config_path)
        path = status_path or config_path.parent / "numbers.status.json"
        status = load_status(path)
    except (ConfigError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    sources = list(config.numbers.sources) if config.numbers is not None else []
    report = assess(sources, status, today if today is not None else utc_today())
    sys.stdout.write(_TOKEN_RENDERERS[fmt](report))
    return token_exit_code(report, EXIT_SOURCE_FAILED, EXIT_TOKEN_WARNING)


MAX_ALLOWED_BYTES = 64 * 1024


def run_egress_check(config_path: Path, allowed_path: Path) -> int:
    """Check that the egress allow-list names every host the fediverse collector
    needs. No network. Exit 0 all listed (or nothing to check), 3 one or more are
    missing (their host names are printed), 2 usage or config problem."""
    try:
        config = load_site_config(config_path)
        text = _read(allowed_path, MAX_ALLOWED_BYTES).decode("utf-8", errors="strict")
    except (ConfigError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    sources = config.numbers.fediverse if config.numbers is not None else []
    missing = missing_endpoints(sources, text)
    for endpoint in missing:
        print(f"missing from the allow-list: {endpoint}", file=sys.stderr)
    if missing:
        return EXIT_SOURCE_FAILED
    print(f"egress ok: {len(required_endpoints(sources))} fediverse endpoint(s) listed")
    return EXIT_OK


def _run_egress_check(args: argparse.Namespace) -> int:
    return run_egress_check(args.config, args.allowed)


def _run_tokens(args: argparse.Namespace) -> int:
    return run_tokens(args.config, args.status, args.format)


def _run_import(args: argparse.Namespace) -> int:
    return run_import(args.config, args.csv, args.out, args.dry_run)


def _run_stale(args: argparse.Namespace) -> int:
    return run_stale(args.config, args.numbers, args.remind_days, args.json)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    counts = subparsers.add_parser(
        "counts", help="pull follower counts into a numbers file"
    )
    actions = counts.add_subparsers(dest="counts_command", required=True)
    refresh_parser = actions.add_parser(
        "refresh",
        help="fetch every numbers source and update the numbers file",
        description="Exit 0 when every source succeeded or was skipped, 3 when "
        "any source failed (its previous number is kept), 2 for usage or "
        "config errors.",
    )
    refresh_parser.add_argument(
        "--config", type=Path, required=True, help="site config file"
    )
    refresh_parser.add_argument(
        "--out",
        type=Path,
        help="numbers file to update (default: numbers.json beside the config); "
        "the status file is written beside it as <name>.status.json",
    )
    refresh_parser.add_argument(
        "--only",
        nargs="+",
        action="extend",
        metavar="SLUG",
        help="refresh only this platform slug (repeatable)",
    )
    refresh_parser.add_argument(
        "--accept-drop",
        nargs="+",
        action="extend",
        metavar="PLATFORM",
        help="accept a zero or a fall of more than 90 percent (from 1000 or "
        "more) for this platform on purpose; otherwise such a value is kept out "
        "as zero_suspect or drop_suspect",
    )
    refresh_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print hosts and kinds that would be fetched; fetch and write nothing",
    )
    refresh_parser.add_argument(
        "--token-sink",
        dest="sink_kind",
        choices=("none", "doppler"),
        default="none",
        help="where a rotated token is stored. none (default): refresh stays "
        "manual and a refresh-needed health line is printed. doppler: store the "
        "rotated value in Doppler and verify it by read-back before any success "
        "(docs/token-sink.md)",
    )
    refresh_parser.add_argument(
        "--doppler-project",
        metavar="NAME",
        help=f"Doppler project (default: ${DOPPLER_PROJECT_ENV})",
    )
    refresh_parser.add_argument(
        "--doppler-config",
        metavar="NAME",
        help=f"Doppler config, e.g. dev (default: ${DOPPLER_CONFIG_ENV})",
    )
    refresh_parser.add_argument(
        "--doppler-token-stdin",
        action="store_true",
        help="read the Doppler API token from one line of stdin instead of "
        f"${DOPPLER_TOKEN_ENV}; the token is never an argument",
    )
    import_parser = actions.add_parser(
        "import",
        help="import hand-kept numbers from a CSV file (platform,metric,value,as_of)",
        description="Strict batch import: all rows are checked first and one bad "
        "row refuses the file. as_of is a UTC day and may not be in the future. "
        "Exit 0 done, 2 refused (nothing written), 1 write failed.",
    )
    import_parser.add_argument("--config", type=Path, required=True)
    import_parser.add_argument("--csv", type=Path, required=True, help="the CSV file")
    import_parser.add_argument(
        "--out", type=Path, help="numbers file to update (default: beside the config)"
    )
    import_parser.add_argument("--dry-run", action="store_true")
    stale_parser = actions.add_parser(
        "stale",
        help="list hand-kept numbers due for an update",
        description="Prints the hand-kept numbers that are stale or old enough for "
        "a reminder, never their values. Exit 0 nothing due, 4 something due.",
    )
    stale_parser.add_argument("--config", type=Path, required=True)
    stale_parser.add_argument("--numbers", type=Path, help="numbers file to read")
    stale_parser.add_argument(
        "--remind-days",
        type=int,
        default=REMIND_DAYS,
        help=f"remind when a number is this many days old (default {REMIND_DAYS})",
    )
    stale_parser.add_argument("--json", action="store_true", help="machine-readable")
    egress_parser = actions.add_parser(
        "egress-check",
        help="check that an egress allow-list names every fediverse host",
        description="Reads the config and a file holding the workflow's "
        "allowed-endpoints value (host:443 entries separated by spaces, commas "
        "or newlines). No network. Exit 0 every needed host is listed, 3 one is "
        "missing (it is printed), 2 usage or config error.",
    )
    egress_parser.add_argument("--config", type=Path, required=True)
    egress_parser.add_argument(
        "--allowed", type=Path, required=True, help="file with the allowed endpoints"
    )
    tokens_parser = actions.add_parser(
        "tokens",
        help="report platform token health (ok, expiring, expired, rejected)",
        description="Reads the config and the last status file; no network, no "
        "secret, never a token value. Exit 0 every token ok, 4 a token is "
        "expiring, 3 a token is expired or was rejected, 2 usage or config error.",
    )
    tokens_parser.add_argument("--config", type=Path, required=True)
    tokens_parser.add_argument(
        "--status", type=Path, help="status file (default: numbers.status.json)"
    )
    tokens_parser.add_argument(
        "--format",
        choices=tuple(_TOKEN_RENDERERS),
        default="text",
        help="text (default), json, markdown (a step summary) or github "
        "(Actions annotations)",
    )
    _register_history(actions)
    counts.set_defaults(func=_dispatch)
