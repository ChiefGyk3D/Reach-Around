# SPDX-License-Identifier: Apache-2.0
"""Safe, line-preserving edits of a site config.

No YAML library rewrites the file. The real parser (PyYAML's composer) only
*locates* nodes by line and column; the edit then splices whole lines of the
original text, so every byte outside the edit is untouched: comments, blank
lines, quoting and indentation included.

Only plain block-style layouts are supported. Anything else (flow style,
CRLF line endings, a dash on its own line) is refused with a message instead
of being rewritten.

Every edit is checked twice before anything is written: the edited text must
load with the real, hardened loader and models, and the loaded data must equal
the original data with exactly the requested change applied. If either check
fails, nothing is written.
"""

from __future__ import annotations

import copy
import datetime
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from reach_around.config import ConfigError, load_site_config, load_yaml
from reach_around.config.models import check_https_url
from reach_around.site.theme import COLOR_KEYS, check_image_path

MAX_BYTES = 1_048_576
LOCK_TIMEOUT = 10.0  # seconds to wait for another edit's lock
LOCK_POLL = 0.05

SET_KEYS: tuple[str, ...] = (
    "creator.name",
    "creator.title",
    "creator.avatar",
    "theme.background_dim",
    "theme.mode",
    *(f"theme.colors.{key}" for key in COLOR_KEYS),
)


class EditError(Exception):
    """A refused edit. The message is shown to the user as ``error: ...``."""


@dataclass(frozen=True)
class Result:
    path: Path
    old: str
    new: str
    written: bool
    backup: Path | None


Compute = Callable[[str, dict[str, Any]], tuple[str, dict[str, Any]]]


# ---- path and file handling ----


def resolve_config(path: Path, *, force_path: bool) -> Path:
    if path.is_symlink():
        if not force_path:
            raise EditError(
                f"{path}: refusing to edit a symlink; give the real path "
                "or pass --force-path"
            )
        path = path.resolve()
    resolved = path.resolve()
    if not force_path and not resolved.is_relative_to(Path.cwd().resolve()):
        raise EditError(
            f"{path}: outside the working tree; run from the config's "
            "folder or pass --force-path"
        )
    return resolved


def _decode(path: Path, data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise EditError(f"{path}: file is not valid UTF-8") from None
    if "\r" in text:
        raise EditError(f"{path}: CRLF line endings are not supported; convert first")
    return text


@dataclass(frozen=True)
class _Snapshot:
    """Identity and content of the config as it was read."""

    dev: int
    ino: int
    size: int
    mtime_ns: int
    sha256: str


def _read_fd(fd: int, path: Path) -> bytes:
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise EditError(f"{path}: not a regular file")
        if info.st_size > MAX_BYTES:
            raise EditError(f"{path}: file too large (limit {MAX_BYTES} bytes)")
        data = b""
        while len(data) <= MAX_BYTES:
            chunk = os.pread(fd, 65536, len(data))
            if not chunk:
                break
            data += chunk
    except OSError as exc:
        raise EditError(f"{path}: cannot read file: {exc.strerror}") from exc
    if len(data) > MAX_BYTES:
        raise EditError(f"{path}: file too large (limit {MAX_BYTES} bytes)")
    return data


def _snapshot(fd: int, data: bytes) -> _Snapshot:
    info = os.fstat(fd)
    return _Snapshot(
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        hashlib.sha256(data).hexdigest(),
    )


def _open_locked(path: Path) -> int:
    """Open ``path`` (never through a symlink) and hold an exclusive lock.

    The lock is on the config's own descriptor. A writer replaces the file by
    rename, so after the lock is granted the path is checked to still name
    this very file; if not, the new file is opened and locked instead. Waiting
    for the lock is bounded by ``LOCK_TIMEOUT`` in total.
    """
    deadline = time.monotonic() + LOCK_TIMEOUT
    for _ in range(20):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            raise EditError(f"{path}: cannot open: {exc.strerror}") from exc
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise EditError(
                        f"{path}: another reach-around edit is running or the "
                        "file is locked; nothing was written"
                    ) from None
                time.sleep(LOCK_POLL)
            except OSError as exc:
                os.close(fd)
                raise EditError(f"{path}: cannot lock: {exc.strerror}") from exc
        try:
            held, named = os.fstat(fd), os.stat(path)
        except OSError as exc:
            os.close(fd)
            raise EditError(f"{path}: cannot lock: {exc.strerror}") from exc
        if (held.st_dev, held.st_ino) == (named.st_dev, named.st_ino):
            return fd
        os.close(fd)
    raise EditError(f"{path}: the file keeps being replaced; try again")


def _changed(path: Path) -> EditError:
    return EditError(
        f"{path}: the file changed while editing; nothing was written, "
        "run the command again"
    )


def _verify_unchanged(path: Path, snap: _Snapshot) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise _changed(path) from None
    try:
        data = _read_fd(fd, path)
        now = _snapshot(fd, data)
    finally:
        os.close(fd)
    if now != snap:
        raise _changed(path)


def _during_backup(path: Path) -> None:
    """Seam inside the backup step, before its rename (tests hook it)."""


def _before_replace(path: Path) -> None:
    """Seam after the backup, right before the final check (tests hook it)."""


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _check_candidate(path: Path, text: str) -> dict[str, Any]:
    """Write ``text`` to a temp file beside ``path`` and load it for real."""
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        try:
            load_site_config(tmp)
            loaded = load_yaml(tmp)
        except ConfigError as exc:
            raise EditError(
                f"{path}: the edit would make the config invalid: {exc.message}"
            ) from None
    finally:
        tmp.unlink(missing_ok=True)
    if not isinstance(loaded, dict):
        raise EditError(f"{path}: the edit would not leave a mapping")
    return loaded


def _stage(target: Path, text: str, mode: int) -> Path:
    """Write ``text`` to a synced temp file beside ``target``; return its path."""
    fd, name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp


def _commit(tmp: Path, target: Path) -> None:
    """Rename ``tmp`` over ``target`` and sync the folder; drop ``tmp`` on failure."""
    try:
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_dir(target.parent)


def _write_backup(path: Path, text: str, mode: int) -> Path:
    backup = path.with_name(path.name + ".bak")
    try:
        tmp = _stage(backup, text, mode)
        try:
            _during_backup(path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        _commit(tmp, backup)
    except OSError as exc:
        raise EditError(
            f"{backup}: cannot write backup (the previous backup and the "
            f"config are unchanged): {exc.strerror or exc}"
        ) from exc
    return backup


def apply(
    config: Path,
    compute: Compute,
    *,
    dry_run: bool,
    backup: bool,
    force_path: bool,
) -> Result:
    """Run one edit end to end. Raises ``EditError``; never half-writes.

    The config is locked from the read to the rename, so two of our commands
    run one after the other. Against any other writer, the file's identity and
    hash are re-checked right before the rename; a change is refused.
    """
    path = resolve_config(config, force_path=force_path)
    lock = _open_locked(path)
    try:
        data = _read_fd(lock, path)
        snap = _snapshot(lock, data)
        try:
            load_site_config(path)  # the file must be valid before it is edited
            raw = load_yaml(path)
        except ConfigError as exc:
            raise EditError(str(exc)) from None
        if not isinstance(raw, dict):
            raise EditError(f"{path}: the config is not a mapping")
        old = _decode(path, data)
        new, expected = compute(old, raw)
        if _check_candidate(path, new) != expected:
            raise EditError(
                f"{path}: internal check failed: the edited file does not match "
                "the requested change; nothing was written (please report this)"
            )
        if dry_run:
            return Result(path, old, new, False, None)
        mode = stat.S_IMODE(os.fstat(lock).st_mode)
        try:
            staged = _stage(path, new, mode)
        except OSError as exc:
            raise EditError(f"{path}: cannot write: {exc.strerror or exc}") from exc
        try:
            # Everything slow comes first. The check is the last step before the
            # rename, so the window for a writer that ignores the lock is tiny
            # (it cannot be closed on POSIX).
            saved = _write_backup(path, old, mode) if backup else None
            _before_replace(path)
            _verify_unchanged(path, snap)
            try:
                _commit(staged, path)
            except OSError as exc:
                raise EditError(f"{path}: cannot write: {exc.strerror or exc}") from exc
        finally:
            staged.unlink(missing_ok=True)
        return Result(path, old, new, True, saved)
    finally:
        os.close(lock)


# ---- locating nodes ----


def _compose(text: str) -> MappingNode:
    node = yaml.compose(text, Loader=yaml.SafeLoader)
    if not isinstance(node, MappingNode):
        raise EditError("the config is not a mapping")
    return node


def _get(node: MappingNode, key: str) -> tuple[Node, Node] | None:
    for key_node, value_node in node.value:
        if isinstance(key_node, ScalarNode) and key_node.value == key:
            return key_node, value_node
    return None


def _scalar(node: MappingNode, key: str) -> str | None:
    found = _get(node, key)
    if found is not None and isinstance(found[1], ScalarNode):
        return str(found[1].value)
    return None


def _block_end(text: str, lines: list[str], node: Node) -> int:
    """First line after ``node`` (the line of the next token, or EOF)."""
    if node.end_mark.index >= len(text):
        return len(lines)
    return int(node.end_mark.line)


def _is_gap(line: str) -> bool:
    stripped = line.strip()
    return stripped == "" or stripped.startswith("#")


@dataclass(frozen=True)
class _Item:
    start: int  # first line, comments directly above included
    first: int  # the "- " line
    end: int  # last content line, inclusive
    indent: int  # column of the dash
    keycol: int  # column of the item's first key


def _items(text: str, lines: list[str], seq: Node, floor: int) -> list[_Item]:
    if not isinstance(seq, SequenceNode):
        raise EditError("expected a list in the config")
    if seq.flow_style:
        raise EditError(
            "this list is written in flow style ([...]); edit it by hand or "
            "rewrite it as a block list"
        )
    items: list[_Item] = []
    previous_end = floor
    for index, node in enumerate(seq.value):
        if not isinstance(node, MappingNode) or node.flow_style:
            raise EditError(
                "an item is written in flow style ({...}); edit it by hand or "
                "rewrite it as a block mapping"
            )
        first = node.start_mark.line
        match = re.fullmatch(r"( *)-( +)", lines[first][: node.start_mark.column])
        if match is None:
            raise EditError(
                f"line {first + 1}: unsupported layout; each list item must "
                "start with '- key: value' on one line"
            )
        indent = len(match[1])
        if index + 1 < len(seq.value):
            boundary = seq.value[index + 1].start_mark.line
        else:
            boundary = _block_end(text, lines, seq)
        end = boundary - 1
        while end > first and _is_gap(lines[end]):
            end -= 1
        start = first
        while (
            start - 1 > previous_end
            and lines[start - 1].lstrip().startswith("#")
            and len(lines[start - 1]) - len(lines[start - 1].lstrip()) == indent
        ):
            start -= 1
        items.append(_Item(start, first, end, indent, node.start_mark.column))
        previous_end = end
    return items


def _finish(old: str, lines: list[str]) -> str:
    """Join lines; keep the original's final-newline habit."""
    out = "".join(
        line if line.endswith("\n") or i == len(lines) - 1 else line + "\n"
        for i, line in enumerate(lines)
    )
    if old.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    if not old.endswith("\n") and out.endswith("\n"):
        out = out[:-1]
    return out


@dataclass(frozen=True)
class _Seq:
    node: SequenceNode
    floor: int  # line of the "blocks:" or "links:" key


def _tab_blocks(root: MappingNode, tab_id: str) -> _Seq:
    tabs = _get(root, "tabs")
    if tabs is None or not isinstance(tabs[1], SequenceNode):
        raise EditError("the config has no tabs list")
    for tab in tabs[1].value:
        if isinstance(tab, MappingNode) and _scalar(tab, "id") == tab_id:
            blocks = _get(tab, "blocks")
            if blocks is None or not isinstance(blocks[1], SequenceNode):
                raise EditError(f"tab {tab_id!r} has no blocks list")
            return _Seq(blocks[1], blocks[0].start_mark.line)
    known = ", ".join(
        str(_scalar(t, "id")) for t in tabs[1].value if isinstance(t, MappingNode)
    )
    raise EditError(f"no tab with id {tab_id!r}; tabs are: {known}")


def _find_link(root: MappingNode, link_id: str) -> tuple[_Seq, int]:
    """The list that holds link ``link_id`` and its index in that list."""
    tabs = _get(root, "tabs")
    if tabs is not None and isinstance(tabs[1], SequenceNode):
        for tab in tabs[1].value:
            if not isinstance(tab, MappingNode):
                continue
            if _scalar(tab, "id") == link_id:
                raise EditError(f"{link_id!r} is a tab, not a link")
            blocks = _get(tab, "blocks")
            if blocks is None or not isinstance(blocks[1], SequenceNode):
                continue
            holders = [_Seq(blocks[1], blocks[0].start_mark.line)]
            for block in blocks[1].value:
                inner = _get(block, "links") if isinstance(block, MappingNode) else None
                if inner is not None and isinstance(inner[1], SequenceNode):
                    holders.append(_Seq(inner[1], inner[0].start_mark.line))
            for holder in holders:
                for index, item in enumerate(holder.node.value):
                    if isinstance(item, MappingNode) and _scalar(item, "id") == link_id:
                        if _get(item, "links") is not None:
                            raise EditError(f"{link_id!r} is a section, not a link")
                        if _get(item, "url") is None:
                            raise EditError(f"{link_id!r} is not a link")
                        return holder, index
    raise EditError(f"no link with id {link_id!r}")


def all_ids(raw: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for tab in raw.get("tabs", []):
        found.add(tab["id"])
        for block in tab.get("blocks", []):
            found.add(block["id"])
            for link in block.get("links", []):
                found.add(link["id"])
    return found


def _holder_list(raw: dict[str, Any], link_id: str) -> list[dict[str, Any]]:
    for tab in raw["tabs"]:
        if any(b["id"] == link_id for b in tab["blocks"]):
            return list(tab["blocks"])
        for block in tab["blocks"]:
            if any(x["id"] == link_id for x in block.get("links", [])):
                return list(block["links"])
    raise EditError(f"no link with id {link_id!r}")


def _set_holder(raw: dict[str, Any], link_id: str, new: list[dict[str, Any]]) -> None:
    for tab in raw["tabs"]:
        if any(b["id"] == link_id for b in tab["blocks"]):
            tab["blocks"] = new
            return
        for block in tab["blocks"]:
            if any(x["id"] == link_id for x in block.get("links", [])):
                block["links"] = new
                return


# ---- scalar text ----

_PLAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._/:?=&%~+@,-]*")


def check_text(value: str, what: str) -> str:
    if not value or any(not ch.isprintable() for ch in value):
        raise EditError(f"{what} must be non-empty printable text on one line")
    if value != value.strip():
        raise EditError(f"{what} must not start or end with whitespace")
    return value


def scalar_text(value: str) -> str:
    """A YAML scalar for ``value``: plain when that is safe, else double-quoted."""
    if (
        _PLAIN.fullmatch(value)
        and ": " not in value
        and not value.endswith(" ")
        and yaml.safe_load(value) == value
    ):
        return value
    return json.dumps(value, ensure_ascii=False)


def make_slug(title: str, taken: set[str]) -> str:
    folded = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")[:36].strip("-")
    base = base or "link"
    candidate, number = base, 1
    while candidate in taken:
        number += 1
        candidate = f"{base}-{number}"
    return candidate


# ---- the edits ----


def add_link(
    text: str,
    raw: dict[str, Any],
    *,
    tab: str,
    link: dict[str, Any],
    position: int | None,
) -> tuple[str, dict[str, Any]]:
    lines = text.splitlines(keepends=True)
    holder = _tab_blocks(_compose(text), tab)
    items = _items(text, lines, holder.node, holder.floor)
    if not items:
        raise EditError(f"tab {tab!r} has no blocks to copy the layout from")
    count = len(items)
    where = count + 1 if position is None else position
    if not 1 <= where <= count + 1:
        raise EditError(f"--position must be between 1 and {count + 1}")
    sample = items[0]
    pad = " " * sample.indent
    keys = " " * sample.keycol
    gap = " " * (sample.keycol - sample.indent - 1)
    block: list[str] = []
    for index, (key, value) in enumerate(link.items()):
        rendered = "true" if value is True else scalar_text(str(value))
        prefix = f"{pad}-{gap}" if index == 0 else keys
        block.append(f"{prefix}{key}: {rendered}\n")
    at = items[-1].end + 1 if where == count + 1 else items[where - 1].start
    if at > 0 and not lines[at - 1].endswith("\n"):
        lines[at - 1] += "\n"
    lines[at:at] = block
    expected = copy.deepcopy(raw)
    target = next(t for t in expected["tabs"] if t["id"] == tab)
    target["blocks"].insert(where - 1, dict(link))
    return _finish(text, lines), expected


def remove_link(
    text: str, raw: dict[str, Any], *, link_id: str
) -> tuple[str, dict[str, Any]]:
    lines = text.splitlines(keepends=True)
    holder, index = _find_link(_compose(text), link_id)
    items = _items(text, lines, holder.node, holder.floor)
    if len(items) == 1:
        raise EditError(
            f"{link_id!r} is the only item in its list; removing it would leave "
            "an empty list, so edit the file by hand"
        )
    item = items[index]
    del lines[item.start : item.end + 1]
    expected = copy.deepcopy(raw)
    current = _holder_list(expected, link_id)
    _set_holder(expected, link_id, [x for x in current if x["id"] != link_id])
    return _finish(text, lines), expected


def move_link(
    text: str, raw: dict[str, Any], *, link_id: str, position: int
) -> tuple[str, dict[str, Any]]:
    lines = text.splitlines(keepends=True)
    holder, index = _find_link(_compose(text), link_id)
    items = _items(text, lines, holder.node, holder.floor)
    if not 1 <= position <= len(items):
        raise EditError(f"--position must be between 1 and {len(items)}")
    out = _reordered(lines, items, index, position)
    expected = copy.deepcopy(raw)
    current = _holder_list(expected, link_id)
    entry = current.pop(index)
    current.insert(position - 1, entry)
    _set_holder(expected, link_id, current)
    return _finish(text, out), expected


def _reordered(
    lines: list[str], items: list[_Item], index: int, position: int
) -> list[str]:
    """``lines`` with item ``index`` moved to 1-based ``position``.

    The gaps between items stay in their slots; only the item chunks move.
    """
    chunks = [lines[i.start : i.end + 1] for i in items]
    chunks = [
        c[:-1] + [c[-1] + "\n"] if not c[-1].endswith("\n") else c for c in chunks
    ]
    moved = chunks.pop(index)
    chunks.insert(position - 1, moved)
    out = lines[: items[0].start]
    cursor = items[0].start
    for slot, item in enumerate(items):
        out.extend(lines[cursor : item.start])  # the gap before this slot
        out.extend(chunks[slot])
        cursor = item.end + 1
    out.extend(lines[cursor:])
    return out


LINK_FIELDS: tuple[str, ...] = (
    "title",
    "url",
    "subtitle",
    "icon",
    "affiliate",
    "featured",
)
_LINK_ORDER = (
    "id",
    "title",
    "url",
    "subtitle",
    "icon",
    "image",
    "image_alt",
    "affiliate",
    "featured",
    "show_from",
    "show_until",
)
_LINK_BOOLS = ("affiliate", "featured")
_LINK_OPTIONAL = ("subtitle", "icon")


def _value_end_line(value: Node) -> int:
    """The last line a value occupies (a block value ends before its mark)."""
    mark = value.end_mark
    return int(mark.line if mark.column > 0 else mark.line - 1)


def _set_field(
    lines: list[str],
    node: MappingNode,
    span: _Item,
    order: tuple[str, ...],
    field: str,
    shown: str | None,
    what: str,
) -> None:
    """Replace, add or (``shown`` is None) delete one scalar key of an item.

    Only the lines of that key change. A replaced value keeps any comment after
    it; a new key goes right after the last key that normally comes before it,
    at the item's own indentation.
    """
    found = _get(node, field)
    if found is not None:
        key_node, old = found
        simple = (
            isinstance(old, ScalarNode)
            and old.value != ""
            and old.style in (None, '"', "'")
            and old.start_mark.line == old.end_mark.line == key_node.start_mark.line
            and lines[old.start_mark.line][: key_node.start_mark.column].strip()
            in ("", "-")
        )
        if not simple:
            raise EditError(f"{what} has an empty or multi-line value; edit it by hand")
        row = old.start_mark.line
        if shown is None:
            if row == span.first:
                raise EditError(
                    f"{what} is on the line that starts the item; edit it by hand"
                )
            del lines[row]
            return
        lines[row] = (
            lines[row][: old.start_mark.column]
            + shown
            + lines[row][old.end_mark.column :]
        )
        return
    if shown is None:
        return
    earlier = order[: order.index(field)]
    after = span.end
    ends = [
        _value_end_line(pair[1])
        for name in earlier
        if (pair := _get(node, name)) is not None
    ]
    if ends:
        after = max(ends)
    if not lines[after].endswith("\n"):
        lines[after] += "\n"
    lines[after + 1 : after + 1] = [f"{' ' * span.keycol}{field}: {shown}\n"]


def _raw_link(raw: dict[str, Any], link_id: str) -> dict[str, Any]:
    for tab in raw["tabs"]:
        for block in tab["blocks"]:
            if block["id"] == link_id:
                found: dict[str, Any] = block
                return found
            for inner in block.get("links", []):
                if inner["id"] == link_id:
                    found = inner
                    return found
    raise EditError(f"no link with id {link_id!r}")


def parse_link_field(field: str, value: str | bool | None) -> tuple[str | None, Any]:
    """The YAML text and Python value of one link field; text None clears it."""
    if field not in LINK_FIELDS:
        raise EditError(
            f"{field!r} cannot be set on a link; allowed fields: "
            + ", ".join(LINK_FIELDS)
        )
    if field in _LINK_BOOLS:
        if isinstance(value, bool):
            flag = value
        elif value in ("true", "false"):
            flag = value == "true"
        else:
            raise EditError(f"{field} must be true or false")
        return ("true" if flag else "false"), flag
    if field in _LINK_OPTIONAL and (value is None or value == ""):
        return None, None
    if not isinstance(value, str):
        raise EditError(f"{field} must be text")
    check_text(value, field)
    return scalar_text(value), value


def set_link_field(
    text: str,
    raw: dict[str, Any],
    *,
    link_id: str,
    field: str,
    value: str | bool | None,
) -> tuple[str, dict[str, Any]]:
    """Set one scalar field of the link ``link_id``; only that line changes.

    ``subtitle`` and ``icon`` are cleared by an empty value. ``affiliate`` and
    ``featured`` take true or false; false on a key that is absent writes
    nothing, so the file does not fill up with defaults.
    """
    shown, python_value = parse_link_field(field, value)
    lines = text.splitlines(keepends=True)
    holder, index = _find_link(_compose(text), link_id)
    items = _items(text, lines, holder.node, holder.floor)
    node = holder.node.value[index]
    if not isinstance(node, MappingNode):  # _items already refused anything else
        raise EditError(f"{link_id!r} is not a block mapping")
    if python_value is False and _get(node, field) is None:
        return text, copy.deepcopy(raw)
    _set_field(
        lines, node, items[index], _LINK_ORDER, field, shown, f"{link_id}.{field}"
    )
    expected = copy.deepcopy(raw)
    target = _raw_link(expected, link_id)
    if shown is None:
        target.pop(field, None)
    else:
        target[field] = python_value
    return _finish(text, lines), expected


PLATFORM_FIELDS: tuple[str, ...] = ("handle", "url", "name", "label", "count", "as_of")
_PLATFORM_ORDER = ("platform", "name", "handle", "url", "label", "count", "as_of")
_PLATFORM_REQUIRED = ("platform", "handle", "url")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_COUNT = re.compile(r"[0-9]{1,12}")


def _find_platforms(root: MappingNode, block_id: str) -> _Seq:
    """The ``items`` list of the platforms block ``block_id``."""
    tabs = _get(root, "tabs")
    if tabs is None or not isinstance(tabs[1], SequenceNode):
        raise EditError("the config has no tabs list")
    for tab in tabs[1].value:
        blocks = _get(tab, "blocks") if isinstance(tab, MappingNode) else None
        if blocks is None or not isinstance(blocks[1], SequenceNode):
            continue
        for block in blocks[1].value:
            if isinstance(block, MappingNode) and _scalar(block, "id") == block_id:
                found = _get(block, "items")
                if found is None or not isinstance(found[1], SequenceNode):
                    raise EditError(f"{block_id!r} is not a platforms block")
                return _Seq(found[1], found[0].start_mark.line)
    raise EditError(f"no block with id {block_id!r}")


def _raw_platform_items(raw: dict[str, Any], block_id: str) -> list[dict[str, Any]]:
    for tab in raw["tabs"]:
        for block in tab["blocks"]:
            if block["id"] == block_id and isinstance(block.get("items"), list):
                items: list[dict[str, Any]] = block["items"]
                return items
    raise EditError(f"no block with id {block_id!r}")


def _item_number(index: object, size: int) -> int:
    """Check a 1-based item number; return it as a 0-based index."""
    if isinstance(index, bool) or not isinstance(index, int) or not 1 <= index <= size:
        raise EditError(f"the item number must be between 1 and {size}")
    return index - 1


def _blank(value: object) -> bool:
    return value is None or value == ""


def parse_platform_field(field: str, value: object) -> tuple[str | None, Any]:
    """The YAML text and Python value of one platform item field.

    Text None clears the key. A blank ``count`` is cleared, never turned into 0.
    """
    if field == "count":
        if _blank(value):
            return None, None
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise EditError("count must be a whole number")
        digits = str(value)
        if not _COUNT.fullmatch(digits):
            raise EditError("count must be a whole number, 0 or more")
        return str(int(digits)), int(digits)
    if field == "as_of":
        if _blank(value):
            return None, None
        day = value if isinstance(value, datetime.date) else None
        if isinstance(value, str) and _DAY.fullmatch(value):
            try:
                day = datetime.date.fromisoformat(value)
            except ValueError:
                day = None
        if not isinstance(day, datetime.date) or isinstance(day, datetime.datetime):
            raise EditError("as_of must be a date written YYYY-MM-DD")
        return day.isoformat(), day
    if field in ("name", "label") and _blank(value):
        return None, None
    if not isinstance(value, str):
        raise EditError(f"{field} must be text")
    check_text(value, field)
    return scalar_text(value), value


def _platform_parts(item: dict[str, Any]) -> list[tuple[str, str, Any]]:
    """(key, YAML text, Python value) of a new item, in the usual key order."""
    unknown = [k for k in item if k not in _PLATFORM_ORDER]
    if unknown:
        raise EditError(
            f"{unknown[0]!r} cannot be set on a platform item; allowed keys: "
            + ", ".join(_PLATFORM_ORDER)
        )
    for key in _PLATFORM_REQUIRED:
        if _blank(item.get(key)):
            raise EditError(f"{key} is required for a platform item")
    parts: list[tuple[str, str, Any]] = []
    for key in _PLATFORM_ORDER:
        if key not in item:
            continue
        shown, python_value = parse_platform_field(key, item[key])
        if shown is None:
            continue
        parts.append((key, shown, python_value))
    return parts


def add_platform_item(
    text: str,
    raw: dict[str, Any],
    *,
    block: str,
    item: dict[str, Any],
    position: int | None,
) -> tuple[str, dict[str, Any]]:
    """Add an item to the platforms block ``block`` (``position`` is 1-based)."""
    parts = _platform_parts(item)
    lines = text.splitlines(keepends=True)
    holder = _find_platforms(_compose(text), block)
    items = _items(text, lines, holder.node, holder.floor)
    count = len(items)
    where = count + 1 if position is None else position
    if not 1 <= where <= count + 1:
        raise EditError(f"--position must be between 1 and {count + 1}")
    sample = items[0]
    pad = " " * sample.indent
    keys = " " * sample.keycol
    gap = " " * (sample.keycol - sample.indent - 1)
    rows = [
        f"{pad}-{gap}{key}: {shown}\n" if n == 0 else f"{keys}{key}: {shown}\n"
        for n, (key, shown, _value) in enumerate(parts)
    ]
    at = items[-1].end + 1 if where == count + 1 else items[where - 1].start
    if at > 0 and not lines[at - 1].endswith("\n"):
        lines[at - 1] += "\n"
    lines[at:at] = rows
    expected = copy.deepcopy(raw)
    _raw_platform_items(expected, block).insert(
        where - 1, {key: value for key, _shown, value in parts}
    )
    return _finish(text, lines), expected


def remove_platform_item(
    text: str, raw: dict[str, Any], *, block: str, index: int
) -> tuple[str, dict[str, Any]]:
    """Remove item ``index`` (1-based); the last item cannot be removed."""
    lines = text.splitlines(keepends=True)
    holder = _find_platforms(_compose(text), block)
    items = _items(text, lines, holder.node, holder.floor)
    which = _item_number(index, len(items))
    if len(items) == 1:
        raise EditError(
            f"{block!r} has only one item; a platforms block needs at least one, "
            "so remove the whole block by hand"
        )
    span = items[which]
    del lines[span.start : span.end + 1]
    expected = copy.deepcopy(raw)
    del _raw_platform_items(expected, block)[which]
    return _finish(text, lines), expected


def move_platform_item(
    text: str, raw: dict[str, Any], *, block: str, index: int, position: int
) -> tuple[str, dict[str, Any]]:
    """Move item ``index`` to ``position`` (both 1-based) within its block."""
    lines = text.splitlines(keepends=True)
    holder = _find_platforms(_compose(text), block)
    items = _items(text, lines, holder.node, holder.floor)
    which = _item_number(index, len(items))
    if not 1 <= position <= len(items):
        raise EditError(f"--position must be between 1 and {len(items)}")
    out = _reordered(lines, items, which, position)
    expected = copy.deepcopy(raw)
    current = _raw_platform_items(expected, block)
    current.insert(position - 1, current.pop(which))
    return _finish(text, out), expected


def set_platform_item_field(
    text: str,
    raw: dict[str, Any],
    *,
    block: str,
    index: int,
    field: str,
    value: object,
) -> tuple[str, dict[str, Any]]:
    """Set one field of item ``index`` (1-based) of a platforms block.

    ``name``, ``label``, ``count`` and ``as_of`` are cleared by a blank value:
    the line is removed, so a blank count is never written as 0.
    """
    if field not in PLATFORM_FIELDS:
        raise EditError(
            f"{field!r} cannot be set on a platform item; allowed fields: "
            + ", ".join(PLATFORM_FIELDS)
        )
    shown, python_value = parse_platform_field(field, value)
    lines = text.splitlines(keepends=True)
    holder = _find_platforms(_compose(text), block)
    items = _items(text, lines, holder.node, holder.floor)
    which = _item_number(index, len(items))
    node = holder.node.value[which]
    if not isinstance(node, MappingNode):  # _items already refused anything else
        raise EditError(f"item {index} is not a block mapping")
    present = {k: _get(node, k) is not None for k in ("count", "as_of")}
    if field == "count" and shown is None and present["as_of"]:
        raise EditError("clear as_of before count: a date needs a count")
    if field == "as_of" and shown is not None and not present["count"]:
        raise EditError("as_of needs a count; set the count first")
    _set_field(
        lines,
        node,
        items[which],
        _PLATFORM_ORDER,
        field,
        shown,
        f"item {index} {field}",
    )
    expected = copy.deepcopy(raw)
    target = _raw_platform_items(expected, block)[which]
    if shown is None:
        target.pop(field, None)
    else:
        target[field] = python_value
    return _finish(text, lines), expected


def _check_avatar(value: str) -> None:
    """The avatar rules of the config: an https URL, or a local image path.

    These are the functions the ``creator.avatar`` model field itself uses, so
    the editor and the build cannot disagree about what a valid avatar is.
    """
    try:
        if "://" in value or value.startswith("//"):
            check_https_url(value)
        else:
            check_image_path(value)
    except ValueError as exc:
        raise EditError(
            f"creator.avatar must be an https URL or a local image path: {exc}"
        ) from None


def parse_set_value(key: str, value: str) -> tuple[str, Any]:
    """The YAML text and the Python value for ``set KEY VALUE``."""
    if key not in SET_KEYS:
        raise EditError(
            f"{key!r} cannot be set from the command line; allowed keys: "
            + ", ".join(SET_KEYS)
        )
    if key == "theme.background_dim":
        try:
            number = float(value)
        except ValueError:
            raise EditError(f"{key} must be a number such as 0.4") from None
        if not math.isfinite(number):
            raise EditError(f"{key} must be a finite number")
        shown = format(number, ".6f").rstrip("0")
        if shown.endswith("."):
            shown += "0"
        return shown, float(shown)
    check_text(value, key)
    if key == "creator.avatar":
        _check_avatar(value)
    return scalar_text(value), value


def set_value(
    text: str, raw: dict[str, Any], *, key: str, value: str
) -> tuple[str, dict[str, Any]]:
    shown, python_value = parse_set_value(key, value)
    parts = key.split(".")
    lines = text.splitlines(keepends=True)
    node: MappingNode = _compose(text)
    depth = 0
    while depth < len(parts) - 1:
        found = _get(node, parts[depth])
        if found is None:
            break
        child = found[1]
        if not isinstance(child, MappingNode):
            raise EditError(f"{'.'.join(parts[: depth + 1])} is not a mapping")
        if child.flow_style:
            raise EditError(
                f"{'.'.join(parts[: depth + 1])} is written in flow style "
                "({...}); edit it by hand or rewrite it as a block mapping"
            )
        node = child
        depth += 1
    leaf = _get(node, parts[depth]) if depth == len(parts) - 1 else None
    if leaf is not None:
        old = leaf[1]
        simple = (
            isinstance(old, ScalarNode)
            and old.value != ""
            and old.style in (None, '"', "'")
            and old.start_mark.line == old.end_mark.line
        )
        if not simple:
            raise EditError(f"{key} has an empty or multi-line value; edit it by hand")
        row = old.start_mark.line
        lines[row] = (
            lines[row][: old.start_mark.column]
            + shown
            + lines[row][old.end_mark.column :]
        )
    else:
        first = node.value[0][0] if node.value else None
        if first is None:
            raise EditError(f"{'.'.join(parts[:depth])} is empty; edit it by hand")
        base = " " * first.start_mark.column
        added = []
        for level, part in enumerate(parts[depth:]):
            last = depth + level == len(parts) - 1
            tail = f" {shown}" if last else ""
            added.append(f"{base}{'  ' * level}{part}:{tail}\n")
        end = _block_end(text, lines, node) - 1
        while end > node.start_mark.line and _is_gap(lines[end]):
            end -= 1
        if not lines[end].endswith("\n"):
            lines[end] += "\n"
        lines[end + 1 : end + 1] = added
    expected = copy.deepcopy(raw)
    cursor = expected
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = python_value
    return _finish(text, lines), expected
