# SPDX-License-Identifier: Apache-2.0
"""A small record of history writes whose numbers file may not have followed.

History is written before the status and numbers files. If a later write fails,
the history already holds the first value of that UTC day while the numbers file
still shows the old one. A retry the same day would then keep the first history
value (one value per day) and write a different number. To keep both sides in
step, the run ids of every attempt are listed in ``.<numbers file>.pending``
before history is touched and the file is removed once the numbers file is
written. The next run reads it and, for a day that an interrupted attempt
already recorded with another value, appends a correction instead of leaving the
old value standing.

Only run ids are stored (no values). Held under the same lock as the run.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from reach_around.config.numbers import MAX_NUMBERS_BYTES
from reach_around.counts.files import write_atomic
from reach_around.counts.history import History, HistoryError, Record

MAX_IDS = 16
MAX_BYTES = 2048
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
CORRECTION_NOTE = "replaces the value of an interrupted run; the later fetch stands"


def pending_path(numbers: Path) -> Path:
    return numbers.with_name(f".{numbers.name}.pending")


class PendingError(HistoryError):
    """The pending note is there but cannot be trusted. Never quotes content.

    The note is left exactly as it is (nothing is deleted or rewritten), so no
    information is lost; a person inspects or removes it and runs again.
    """


_UNUSABLE = (
    "the pending note next to the numbers file cannot be read or is not valid; "
    "it was left as it is; inspect or remove it, then run again"
)
_FULL = (
    "the pending note already lists the most unfinished attempts it can hold; "
    "it was left as it is; check that history matches the numbers file, remove "
    "the note, then run again"
)
_EXPECT = re.compile(r"^expect ([0-9a-f]{64})$")
_BASE = re.compile(r"^base (none|[0-9a-f]{1,32}(?::[0-9a-f]{1,32}){3})$")
_NONCE = re.compile(r"^nonce ([0-9a-f]{32})$")


@dataclass(frozen=True)
class Note:
    ids: list[str]
    base: str | None  # identity of the numbers file when the latest run began
    expect: str | None = None  # digest of the text that run was about to write
    nonce: str | None = None  # names this very note; only its writer removes it


def _read_exactly(fd: int, limit: int) -> bytes | None:
    """The whole regular file behind ``fd``, or ``None`` if it is not one, is
    over ``limit`` bytes, or changed size while it was read.

    Reads in a loop until the size ``fstat`` reported (one ``os.read`` may
    return less) and then requires end of file, so a short read is never taken
    for the whole file.
    """
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        return None
    chunks: list[bytes] = []
    total = 0
    while total < info.st_size:
        chunk = os.read(fd, info.st_size - total)
        if not chunk:
            return None  # shorter than it was a moment ago
        chunks.append(chunk)
        total += len(chunk)
    if os.read(fd, 1):
        return None  # longer than it was a moment ago
    return b"".join(chunks)


def read_note(path: Path) -> Note:
    """The note; empty only when there is no note. Anything else unusable raises.

    An absent note is the only state that means "nothing pending". A note that
    cannot be opened or read, is not a regular file, is too large, is not
    ASCII, or holds a line that is neither a run id nor the digest line raises
    ``PendingError`` so the run stops before history or the note is touched.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return Note([], None)
    except OSError:
        raise PendingError(_UNUSABLE) from None
    try:
        raw = _read_exactly(fd, MAX_BYTES)
    except OSError:
        raise PendingError(_UNUSABLE) from None
    finally:
        os.close(fd)
    if not raw:
        raise PendingError(_UNUSABLE)
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeDecodeError:
        raise PendingError(_UNUSABLE) from None
    ids: list[str] = []
    base: str | None = None
    expect: str | None = None
    nonce: str | None = None
    for line in lines:
        found = _BASE.fullmatch(line)
        wanted = _EXPECT.fullmatch(line)
        named = _NONCE.fullmatch(line)
        if found is not None and base is None:
            base = found.group(1)
        elif wanted is not None and expect is None:
            expect = wanted.group(1)
        elif named is not None and nonce is None:
            nonce = named.group(1)
        elif _ID.fullmatch(line):
            ids.append(line)
        else:
            raise PendingError(_UNUSABLE)
    if len(ids) > MAX_IDS:
        raise PendingError(_UNUSABLE)
    return Note(ids, base, expect, nonce)


def read_pending(path: Path) -> list[str]:
    """The run ids listed, oldest first; raises ``PendingError`` if unusable."""
    return read_note(path).ids


def _identity_of(info: os.stat_result) -> str:
    parts = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
    return ":".join(f"{part:x}" for part in parts)


def numbers_identity(numbers: Path) -> str:
    """Which file the numbers path is right now, or ``none`` when absent.

    Device, inode, modification time (ns) and size of the file itself (nothing is
    read, no link followed). Every successful numbers write is a temp file
    renamed over the old one, so it is a new file with a new identity even when
    its bytes equal the old ones; a content digest cannot tell those apart.
    """
    try:
        return _identity_of(os.lstat(numbers))
    except FileNotFoundError:
        return "none"


def content_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _numbers_state(numbers: Path) -> tuple[str, str | None]:
    """Identity and content digest of ONE open numbers file.

    The file is opened once (no link followed) and both are taken from that
    descriptor, so they describe the same file even if the path is swapped
    meanwhile. The digest is ``None`` when the file is absent, not a plain file,
    too large or changed while read (then it cannot be the file a run wrote).
    """
    try:
        fd = os.open(numbers, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return "none", None
    except OSError:
        return numbers_identity(numbers), None
    try:
        identity = _identity_of(os.fstat(fd))
        try:
            data = _read_exactly(fd, MAX_NUMBERS_BYTES)
        except OSError:
            return identity, None
    finally:
        os.close(fd)
    if data is None:
        return identity, None
    return identity, hashlib.sha256(data).hexdigest()


def live_ids(note: Note, numbers: Path) -> list[str]:
    """The ids whose numbers write may not have happened.

    A run is shown to have committed only when ONE numbers file now holds exactly
    the text that run was about to write (``expect``, a digest) AND is a
    different file than when the run began (``base``, its identity; this
    separates a rewrite of identical bytes from an untouched file). Identity and
    digest come from one open descriptor. A file restored by other means (a
    checkout, a copy of an older version) holds other content, so it proves
    nothing and the ids stay pending.
    """
    if note.base is None or note.expect is None:
        return note.ids
    identity, digest = _numbers_state(numbers)
    if note.base != identity and note.expect == digest:
        return []
    return note.ids


def _render(
    ids: Sequence[str], base: str | None, expect: str | None, nonce: str
) -> str:
    lines = list(ids)
    if base is not None:
        lines.append(f"base {base}")
        if expect is not None:
            lines.append(f"expect {expect}")
    lines.append(f"nonce {nonce}")
    return "\n".join(lines) + "\n"


def mark_pending(
    path: Path,
    ids: Sequence[str],
    run_id: str,
    base: str | None = None,
    expect: str | None = None,
) -> str:
    """List ``run_id`` after the ids already pending; returns the note's nonce.

    The nonce is written into the note; ``clear_pending`` and ``withdraw_pending``
    act only on a note that still carries it. Raises ``OSError``.

    Raises ``PendingError`` when the list would pass ``MAX_IDS``: an id is never
    dropped to make room (it may be the only one that can be corrected), so the
    run stops and the note is left as it is.
    """
    if len(ids) + 1 > MAX_IDS:
        raise PendingError(_FULL)
    nonce = secrets.token_hex(16)
    write_atomic(path, _render([*ids, run_id], base, expect, nonce))
    return nonce


_STRANDED = (
    "a note set aside next to the numbers file (a '.take' file) was left behind "
    "by an interrupted or conflicting run; it may list unfinished runs; inspect "
    "it, merge or remove it, then run again"
)


def _take_names(path: Path) -> list[Path]:
    prefix = f"{path.name}."
    return [
        entry
        for entry in path.parent.iterdir()
        if entry.name.startswith(prefix) and entry.name.endswith(".take")
    ]


def require_no_stranded(path: Path) -> None:
    """Refuse to go on while a set-aside note is lying next to the pending note.

    A note moved aside under a ``.take`` name is never read by anything else, so
    its ids would be lost without a trace. Leaving one behind (a crash, or a
    second note that appeared while it was set aside) therefore blocks every
    later run until a person has looked at it. Raises ``PendingError``.
    """
    try:
        found = _take_names(path)
    except OSError:
        raise PendingError(_UNUSABLE) from None
    if found:
        raise PendingError(_STRANDED)


def _set_aside(path: Path) -> Path:
    return path.with_name(f"{path.name}.{secrets.token_hex(8)}.take")


def _take(path: Path, nonce: str) -> Path | None:
    """Take the note at ``path`` out of the way if it is the one named ``nonce``.

    The name is renamed to a private ``.take`` name first (one atomic step that
    moves whatever is there), and only then read. A note that is not ours
    (another process put one in its place) is linked back and left alone; it is
    never deleted. If it cannot be linked back (a newer note already took the
    name) it stays under its ``.take`` name, which blocks every later run
    (``require_no_stranded``) so the ids in it cannot be lost quietly. Returns
    the private path of our note, ``None`` when there is no note, and raises
    ``PendingError`` for one that is not ours.
    """
    taken = _set_aside(path)
    try:
        os.rename(path, taken)
    except FileNotFoundError:
        return None
    try:
        mine = read_note(taken).nonce == nonce
    except PendingError:
        mine = False
    if mine:
        return taken
    try:
        os.link(taken, path, follow_symlinks=False)  # never replaces a new note
    except FileExistsError:
        raise PendingError(_STRANDED) from None  # kept as .take; blocks next run
    os.unlink(taken)
    raise PendingError(_UNUSABLE)


def clear_pending(path: Path, nonce: str) -> None:
    """Remove the note this run wrote (``nonce``); never another process's note."""
    taken = _take(path, nonce)
    if taken is not None:
        os.unlink(taken)


def withdraw_pending(path: Path, nonce: str, previous: Note) -> None:
    """Undo ``mark_pending`` after a failure that left history untouched.

    Puts back what the note held before this run listed itself (``previous``,
    with its own base and digest), or removes the note when it listed nothing
    live. Acts only on the note that carries ``nonce``. Our own note is kept
    until the previous one is installed; if another note took the name meanwhile
    the previous one is kept under a ``.take`` name (blocking, never silent).
    """
    taken = _take(path, nonce)
    if taken is None:
        return
    if not previous.ids:
        os.unlink(taken)
        return
    fresh = path.with_name(f"{path.name}.{secrets.token_hex(8)}.new")
    write_atomic(
        fresh,
        _render(
            previous.ids, previous.base, previous.expect, previous.nonce or "0" * 32
        ),
    )
    try:
        os.link(fresh, path, follow_symlinks=False)
    except FileExistsError:
        os.rename(fresh, _set_aside(path))  # kept where the next run must look
        os.unlink(taken)
        raise PendingError(_STRANDED) from None
    except BaseException:
        os.unlink(fresh)  # our own note (taken) is still there to be found
        raise
    os.unlink(fresh)
    os.unlink(taken)


def reconcile(
    history: History, ids: Sequence[str], records: Sequence[Record]
) -> list[Record]:
    """Turn a record whose day an interrupted run recorded differently into a
    correction of that record. Everything else is returned as it is."""
    if not ids:
        return list(records)
    wanted = set(ids)
    held: dict[tuple[str, str, str, str], Record] = {}
    for item in history.records:
        if item.run_id in wanted:
            held[item.day_key] = item  # a later correction supersedes the record
    originals: dict[tuple[str, str, str, str], Record] = {
        r.day_key: r for r in history.records if r.type == "record"
    }
    out: list[Record] = []
    for new in records:
        old = held.get(new.day_key)
        if new.type != "record" or old is None or old.value == new.value:
            out.append(new)
            continue
        first = originals[new.day_key]
        out.append(
            Record(
                platform=first.platform,
                metric=first.metric,
                value=new.value,
                as_of_utc=first.as_of_utc,
                source=first.source,
                run_id=new.run_id,
                note=CORRECTION_NOTE,
                era=first.era,
                type="correction",
                api_provider=first.api_provider,
            )
        )
    return out
