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
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from reach_around.config import ConfigError, load_site_config, load_yaml
from reach_around.site.theme import COLOR_KEYS

MAX_BYTES = 1_048_576

SET_KEYS: tuple[str, ...] = (
    "creator.name",
    "creator.title",
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
    this very file; if not, the new file is opened and locked instead.
    """
    for _ in range(20):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            raise EditError(f"{path}: cannot open: {exc.strerror}") from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
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


def _before_replace(path: Path) -> None:
    """Seam between validating the edit and the final check (tests hook it)."""


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


def _replace_durably(target: Path, text: str, mode: int) -> None:
    """Temp file in the same folder, fsync, rename over ``target``, fsync folder.

    On any failure before the rename ``target`` is untouched and the temp file
    is removed.
    """
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
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_dir(target.parent)


def _write_backup(path: Path, text: str, mode: int) -> Path:
    backup = path.with_name(path.name + ".bak")
    try:
        _replace_durably(backup, text, mode)
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
        _before_replace(path)
        _verify_unchanged(path, snap)
        saved = _write_backup(path, old, mode) if backup else None
        try:
            _replace_durably(path, new, mode)
        except OSError as exc:
            raise EditError(f"{path}: cannot write: {exc.strerror or exc}") from exc
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
    expected = copy.deepcopy(raw)
    current = _holder_list(expected, link_id)
    entry = current.pop(index)
    current.insert(position - 1, entry)
    _set_holder(expected, link_id, current)
    return _finish(text, out), expected


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
