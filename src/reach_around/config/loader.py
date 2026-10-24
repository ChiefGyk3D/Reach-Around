# SPDX-License-Identifier: Apache-2.0
"""Hardened YAML loader.

Refuses, with a typed error naming the file and line: duplicate keys, anchors,
aliases, merge keys, non-standard tags, oversized files, deep nesting, invalid
UTF-8, empty files, multiple documents, and symlinked paths.

Symlink choice: the given path is opened with ``O_NOFOLLOW``, so a symlink as
the final path component is refused rather than followed. A config that must
be reached through a link should be given by its real path.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import yaml
from yaml.events import AliasEvent, MappingStartEvent, SequenceStartEvent
from yaml.nodes import MappingNode, Node

_MERGE_TAG = "tag:yaml.org,2002:merge"


class ConfigError(Exception):
    """Raised for every malformed or hostile config input."""

    def __init__(self, path: str, line: int | None, message: str) -> None:
        self.path = path
        self.line = line
        self.message = message
        where = path if line is None else f"{path}:{line}"
        super().__init__(f"{where}: {message}")


class _Refusal(Exception):
    def __init__(self, line: int | None, message: str) -> None:
        self.line = line
        self.message = message
        super().__init__(message)


class _HardenedLoader(yaml.SafeLoader):
    max_depth = 64

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._depth = 0

    def compose_node(self, parent: Node | None, index: Any) -> Node | None:
        # Checked on events, before any node is built or alias expanded.
        if self.check_event(AliasEvent):
            event = self.current_event
            raise _Refusal(event.start_mark.line + 1, "aliases are not allowed")
        event = self.current_event
        if getattr(event, "anchor", None) is not None:
            raise _Refusal(event.start_mark.line + 1, "anchors are not allowed")
        nested = isinstance(event, MappingStartEvent | SequenceStartEvent)
        if nested:
            self._depth += 1
            if self._depth > self.max_depth:
                raise _Refusal(
                    event.start_mark.line + 1,
                    f"nesting is deeper than {self.max_depth} levels",
                )
        try:
            return super().compose_node(parent, index)
        finally:
            if nested:
                self._depth -= 1

    def construct_mapping(
        self, node: MappingNode, deep: bool = False
    ) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            line = key_node.start_mark.line + 1
            if key_node.tag == _MERGE_TAG:
                raise _Refusal(line, "merge keys (<<) are not allowed")
            key = self.construct_object(key_node, deep=True)
            try:
                duplicate = key in seen
            except TypeError:
                raise _Refusal(line, "mapping key is not hashable") from None
            if duplicate:
                raise _Refusal(line, f"duplicate key {key!r}")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _read(path: Path, max_bytes: int) -> bytes:
    name = str(path)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if path.is_symlink():
            raise ConfigError(name, None, "refusing to follow a symlink") from exc
        raise ConfigError(name, None, f"cannot read file: {exc.strerror}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ConfigError(name, None, "not a regular file")
        if info.st_size > max_bytes:
            raise ConfigError(name, None, f"file too large (limit {max_bytes} bytes)")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(max_bytes + 1)
    except OSError as exc:
        raise ConfigError(name, None, f"cannot read file: {exc.strerror}") from exc
    finally:
        os.close(fd)
    if len(data) > max_bytes:
        raise ConfigError(name, None, f"file too large (limit {max_bytes} bytes)")
    return data


def load_yaml(path: Path, *, max_bytes: int = 1_048_576, max_depth: int = 64) -> object:
    """Load one YAML document from ``path`` or raise ``ConfigError``."""
    name = str(path)
    data = _read(path, max_bytes)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(name, None, f"file is not valid UTF-8: {exc.reason}") from exc

    loader_cls = type("_Loader", (_HardenedLoader,), {"max_depth": max_depth})
    loader = loader_cls(text)
    try:
        node = loader.get_single_node()
        if node is None:
            raise ConfigError(name, None, "file is empty")
        return loader.construct_document(node)
    except _Refusal as refusal:
        raise ConfigError(name, refusal.line, refusal.message) from None
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark or exc.context_mark
        line = None if mark is None else mark.line + 1
        problem = exc.problem or "invalid YAML"
        if "could not determine a constructor for the tag" in problem:
            problem = f"tag not allowed: {problem.split('tag ')[-1]}"
        raise ConfigError(name, line, problem) from None
    except yaml.YAMLError as exc:
        raise ConfigError(name, None, f"invalid YAML: {exc}") from None
    except RecursionError:
        raise ConfigError(name, None, "nesting is too deep") from None
    finally:
        loader.dispose()
