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
from reach_around.config.imported_history import parse_imported
from reach_around.config.loader import _read
from reach_around.config.numbers import load_numbers
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
from reach_around.counts.files import (
    LOCK_WAIT_SECONDS,
    FileLock,
    LockBusy,
    LockLost,
    Paths,
    dump_numbers,
    plan_paths,
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
    OldCopyRemainsError,
    Record,
    RedactDurabilityError,
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

EXIT_OK = 0
EXIT_WRITE = 1
EXIT_USAGE = 2
EXIT_SOURCE_FAILED = 3
EXIT_DUE = 4
EXIT_TOKEN_WARNING = 4  # counts tokens: a token is expiring (same code as counts stale)
EXIT_REPLACED_UNSURE = 5  # history-redact: the file was replaced, durability unsure


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
        token = TokenHolder(stdin.readline())
        token.strip()
        from_env.clear()
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
        if config.numbers is None or not config.numbers.sources:
            raise ValueError("the config has no numbers.sources to refresh")
        sources = select(config.numbers, only)
        known = {source.platform for source in config.numbers.sources}
        unknown = sorted(set(accept_drop or []) - known)
        if unknown:
            raise ValueError(
                f"--accept-drop names no configured source: {', '.join(unknown)}"
            )
        paths = plan_paths(config_path, out)
        history = history_path(config, config_path, paths)
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
        for warning in retention_warnings(
            records, policy, stamp.astimezone(UTC).date()
        ):
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
    changed = any(outcome.status == "ok" for outcome in outcomes)
    figures, insight_failures = _insights(
        sources, outcomes, environment, transport, now(), history
    )
    for failure in insight_failures:
        print(f"{failure.platform}: insights error ({failure.word})", file=sys.stderr)
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
        # One append takes at most MAX_BATCH records. If the assembled batch is
        # over it, only the analytics figures of this run are left out (they are
        # reported as too_large); subscribers and other sources are never dropped.
        fetched, too_big = fit_batch(fetched)
        for platform in too_big:
            print(f"{platform}: insights error (too_large)", file=sys.stderr)
            insight_failures.append(InsightFailure(platform, "too_large"))
        try:
            kept = append_records(history, fetched, now=stamp, skip_days=True)
        except HistoryError as exc:
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
    try:
        write_atomic(
            paths.status, render_status(previous_status, outcomes, stamp, True)
        )
    except (OSError, ConfigError) as exc:
        _write_failed(exc)
        return EXIT_WRITE
    if changed:
        try:
            write_atomic(paths.target, dump_numbers(numbers))
        except (OSError, ConfigError) as exc:
            try:
                write_atomic(
                    paths.status,
                    render_status(previous_status, outcomes, stamp, False),
                )
            except (OSError, ConfigError):
                pass  # the numbers file is untouched either way
            _write_failed(exc)
            return EXIT_WRITE
    failed = any(outcome.status == "error" for outcome in outcomes) or bool(
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


def _safe(text: str) -> str:
    """``text`` with every control or non-ASCII character escaped, for a terminal."""
    return "".join(
        c if " " <= c <= "~" else c.encode("unicode_escape").decode("ascii")
        for c in text
    )


def _say(message: str, stream: TextIO | None = None) -> None:
    """Print one control-safe line: a file name can hold an escape sequence."""
    print(_safe(message), file=stream if stream is not None else sys.stdout)


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
        try:
            count = redact_records(
                target,
                plan,
                reason=args.reason,
                trigger_date=args.trigger_date,
                confirm=True,
                include_hand_kept=args.include_hand_kept,
                notices=notices,
            )
        finally:
            for notice in notices:
                _say(notice)
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
    if args.counts_command == "history-export-imported":
        return run_export_imported(args.file, args.out)
    if args.counts_command == "history-import-beacons":
        return run_beacons_import(args.config, args.csv, args.file, args.dry_run)
    if args.counts_command == "import":
        return _run_import(args)
    if args.counts_command == "stale":
        return _run_stale(args)
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
            if history is not None:
                # History first, as in refresh: if it cannot be written, the
                # numbers file is left as it was. One value per platform, metric
                # and UTC day; a retry the same day keeps the first.
                stamp = datetime.now(UTC)
                run_id = new_run_id(stamp)
                append_records(
                    history,
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
                    now=stamp,
                    skip_days=True,
                )
            write_atomic(paths.target, dump_numbers(merge(previous, rows)))
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
