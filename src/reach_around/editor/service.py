# SPDX-License-Identifier: Apache-2.0
"""What the editor's state, version and op routes do, apart from the HTTP.

``EditorService`` turns one request into one call of an edit function in
``reach_around.editing`` and runs it through ``editing.apply``. It never builds
YAML text: the arguments are checked against a fixed table (unknown keys are
refused), the edit is computed from the file as it is *now*, under the file
lock, and the history store records the change afterwards (design 9).

``base`` is the hash the page last saw and ``expect`` the old value(s) of what
the operation touches. When the file moved on but every ``expect`` value still
matches, the edit composes with the outside change and is applied. When one no
longer matches (or is missing), nothing is written and the reply is 409
``stale`` with the fresh state (design 9.2).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import math
import os
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from reach_around import editing
from reach_around.config import ConfigError, load_site_config, load_yaml
from reach_around.editor.guard import JSON_CAP
from reach_around.editor_history import MAX_ENTRIES, History, HistoryError

MUTATIONS_PER_MINUTE = 20
RATE_WINDOW_SECONDS = 60.0
MAX_LABEL = 80
_HASH = re.compile(r"[0-9a-f]{64}")
_STATE_TRIES = 3


class RouteError(Exception):
    """A refusal with a status and the fixed error shape of design 8.1."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        path: str = "",
        *,
        state: dict[str, Any] | None = None,
        headers: list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.path = path
        self.state = state
        self.headers = headers or []

    def body(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "error": {"code": self.code, "message": self.message, "path": self.path}
        }
        if self.state is not None:
            body["state"] = self.state
        return body


class _Stale(Exception):
    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def json_safe(value: Any) -> Any:
    """Plain JSON data for a loaded YAML value (dates become ISO text)."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return str(value)


def _canon(value: Any) -> str:
    return json.dumps(json_safe(value), sort_keys=True, separators=(",", ":"))


# ---- the request body --------------------------------------------------------------


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError("duplicate key")
        seen[key] = value
    return seen


def _no_constant(name: str) -> Any:
    raise ValueError(name)


def parse_json_object(data: bytes) -> dict[str, Any]:
    """The body as an object; duplicate keys, NaN and deep nesting are refused."""
    try:
        parsed = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_no_duplicates,
            parse_constant=_no_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise RouteError(
            400, "bad_json", "The request body is not valid JSON."
        ) from None
    if not isinstance(parsed, dict):
        raise RouteError(400, "bad_json", "The request body must be a JSON object.")
    return parsed


# ---- the operation table -----------------------------------------------------------

_STR, _INT, _SCALAR, _NUMBER_OR_TEXT, _CELL, _LINK, _ITEM = (
    "str",
    "int",
    "scalar",
    "number or text",
    "cell",
    "link",
    "item",
)


@dataclass(frozen=True)
class OpSpec:
    required: dict[str, str]
    optional: dict[str, str] = field(default_factory=dict)
    expect: tuple[str, ...] = ()


OPS: dict[str, OpSpec] = {
    "add_link": OpSpec(
        {"tab": _STR, "link": _LINK}, {"position": _INT}, expect=("count",)
    ),
    "remove_link": OpSpec({"id": _STR}, expect=("order",)),
    "move_link": OpSpec({"id": _STR, "position": _INT}, expect=("order",)),
    "set": OpSpec({"key": _STR, "value": _NUMBER_OR_TEXT}, expect=("value",)),
    "set_link_field": OpSpec(
        {"id": _STR, "field": _STR, "value": _SCALAR}, expect=("value",)
    ),
    "add_platform_item": OpSpec(
        {"block": _STR, "item": _ITEM}, {"position": _INT}, expect=("items",)
    ),
    "remove_platform_item": OpSpec({"block": _STR, "index": _INT}, expect=("items",)),
    "move_platform_item": OpSpec(
        {"block": _STR, "index": _INT, "position": _INT}, expect=("items",)
    ),
    "set_platform_item_field": OpSpec(
        {"block": _STR, "index": _INT, "field": _STR, "value": _CELL},
        expect=("value", "handle"),
    ),
}

_NEW_LINK_TEXT = ("id", "title", "url", "subtitle", "icon")
_NEW_LINK_FLAGS = ("affiliate", "featured")
_NEW_ITEM_KEYS = ("platform", *editing.PLATFORM_FIELDS)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _kind_ok(kind: str, value: object) -> bool:
    if kind == _STR:
        return isinstance(value, str)
    if kind == _INT:
        return _is_int(value)
    if kind == _SCALAR:
        return value is None or isinstance(value, (str, bool))
    if kind == _NUMBER_OR_TEXT:
        return isinstance(value, str) or _is_int(value) or isinstance(value, float)
    if kind == _CELL:
        return value is None or isinstance(value, str) or _is_int(value)
    return isinstance(value, dict)


def _bad(path: str, message: str, code: str = "bad_argument") -> RouteError:
    return RouteError(422, code, message, path)


def _check_args(spec: OpSpec, args: object) -> dict[str, Any]:
    if not isinstance(args, dict):
        raise _bad("args", "args must be an object.")
    allowed = {**spec.required, **spec.optional}
    for name in args:
        if name not in allowed:
            raise _bad(
                f"args.{name}", f"{name!r} is not an argument of this operation."
            )
    for name in spec.required:
        if name not in args:
            raise _bad(f"args.{name}", f"{name!r} is required.")
    for name, value in args.items():
        kind = allowed[name]
        if not _kind_ok(kind, value):
            raise _bad(f"args.{name}", f"{name!r} must be of the kind: {kind}.")
    if "link" in args:
        _check_new_link(args["link"])
    if "item" in args:
        _check_new_item(args["item"])
    checked: dict[str, Any] = args
    return checked


def _check_new_link(link: dict[str, Any]) -> None:
    for name, value in link.items():
        if name in _NEW_LINK_TEXT:
            ok = isinstance(value, str)
        elif name in _NEW_LINK_FLAGS:
            ok = isinstance(value, bool)
        else:
            raise _bad(f"args.link.{name}", f"{name!r} cannot be set on a new link.")
        if not ok:
            raise _bad(f"args.link.{name}", f"{name!r} has the wrong kind of value.")
    for name in ("title", "url"):
        if not link.get(name):
            raise _bad(f"args.link.{name}", f"{name!r} is required.")


def _check_new_item(item: dict[str, Any]) -> None:
    for name, value in item.items():
        if name not in _NEW_ITEM_KEYS:
            raise _bad(f"args.item.{name}", f"{name!r} cannot be set on an item.")
        if not _kind_ok(_CELL, value):
            raise _bad(f"args.item.{name}", f"{name!r} has the wrong kind of value.")


# ---- finding the current values (for expect) ---------------------------------------


class _Gone(Exception):
    """The thing the operation names is not in the current file."""


def _tabs(raw: dict[str, Any]) -> list[dict[str, Any]]:
    tabs = raw.get("tabs")
    return [t for t in tabs if isinstance(t, dict)] if isinstance(tabs, list) else []


def _holder(raw: dict[str, Any], link_id: str) -> list[dict[str, Any]]:
    for tab in _tabs(raw):
        blocks = [b for b in tab.get("blocks") or [] if isinstance(b, dict)]
        if any(b.get("id") == link_id for b in blocks):
            return blocks
        for block in blocks:
            inner = [x for x in block.get("links") or [] if isinstance(x, dict)]
            if any(x.get("id") == link_id for x in inner):
                return inner
    raise _Gone


def _link(raw: dict[str, Any], link_id: str) -> dict[str, Any]:
    for entry in _holder(raw, link_id):
        if entry.get("id") == link_id:
            return entry
    raise _Gone


def _items(raw: dict[str, Any], block_id: str) -> list[dict[str, Any]]:
    for tab in _tabs(raw):
        for block in tab.get("blocks") or []:
            if (
                isinstance(block, dict)
                and block.get("id") == block_id
                and isinstance(block.get("items"), list)
            ):
                return [i for i in block["items"] if isinstance(i, dict)]
    raise _Gone


def _item(raw: dict[str, Any], block_id: str, index: int) -> dict[str, Any]:
    items = _items(raw, block_id)
    if not 1 <= index <= len(items):
        raise _Gone
    return items[index - 1]


def _dotted(raw: dict[str, Any], key: str) -> Any:
    cursor: Any = raw
    for part in key.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


def current_expect(op: str, a: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    """The values ``expect`` is compared with, read from the current file."""
    if op == "add_link":
        for tab in _tabs(raw):
            if tab.get("id") == a["tab"]:
                return {"count": len(tab.get("blocks") or [])}
        raise _Gone
    if op in ("remove_link", "move_link"):
        return {"order": [x.get("id") for x in _holder(raw, a["id"])]}
    if op == "set":
        return {"value": _dotted(raw, a["key"])}
    if op == "set_link_field":
        return {"value": _link(raw, a["id"]).get(a["field"])}
    if op in ("add_platform_item", "remove_platform_item", "move_platform_item"):
        return {"items": _items(raw, a["block"])}
    item = _item(raw, a["block"], a["index"])
    return {"value": item.get(a["field"]), "handle": item.get("handle")}


# ---- computing the edit ------------------------------------------------------------


def _new_link(a: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    given: dict[str, Any] = a["link"]
    for name in _NEW_LINK_TEXT:
        if name in given:
            editing.check_text(given[name], name)
    link: dict[str, Any] = {
        "id": given.get("id")
        or editing.make_slug(given["title"], editing.all_ids(raw)),
        "title": given["title"],
        "url": given["url"],
    }
    for name in ("subtitle", "icon"):
        if given.get(name):
            link[name] = given[name]
    for name in _NEW_LINK_FLAGS:
        if given.get(name) is True:
            link[name] = True
    return link


def _set_text(value: str | int | float) -> str:
    return value if isinstance(value, str) else str(value)


def build_edit(op: str, a: dict[str, Any]) -> editing.Compute:
    """The edit function call for ``op``, as ``apply`` wants it."""

    def run(text: str, raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if op == "add_link":
            return editing.add_link(
                text,
                raw,
                tab=a["tab"],
                link=_new_link(a, raw),
                position=a.get("position"),
            )
        if op == "remove_link":
            return editing.remove_link(text, raw, link_id=a["id"])
        if op == "move_link":
            return editing.move_link(text, raw, link_id=a["id"], position=a["position"])
        if op == "set":
            return editing.set_value(
                text, raw, key=a["key"], value=_set_text(a["value"])
            )
        if op == "set_link_field":
            return editing.set_link_field(
                text, raw, link_id=a["id"], field=a["field"], value=a["value"]
            )
        if op == "add_platform_item":
            return editing.add_platform_item(
                text, raw, block=a["block"], item=a["item"], position=a.get("position")
            )
        if op == "remove_platform_item":
            return editing.remove_platform_item(
                text, raw, block=a["block"], index=a["index"]
            )
        if op == "move_platform_item":
            return editing.move_platform_item(
                text, raw, block=a["block"], index=a["index"], position=a["position"]
            )
        return editing.set_platform_item_field(
            text,
            raw,
            block=a["block"],
            index=a["index"],
            field=a["field"],
            value=a["value"],
        )

    return run


# ---- the service -------------------------------------------------------------------


class EditorService:
    """State, version and the single write path of one editing session."""

    def __init__(
        self,
        config: Path,
        read_bytes: Callable[[Path], bytes | None],
        *,
        clock: Callable[[], float] = time.monotonic,
        mutation_limit: int = MUTATIONS_PER_MINUTE,
        mutation_window: float = RATE_WINDOW_SECONDS,
    ) -> None:
        self.config = config
        self._read = read_bytes
        self._clock = clock
        self._limit = mutation_limit
        self._window = mutation_window
        self._stamps: deque[float] = deque()
        self._rate_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._version_lock = threading.Lock()
        self._seen: str | None = None
        self._counter = 0

    # -- rate limit (design 8.5) --

    def rate_check(self) -> int | None:
        """None if a mutation may run now (and counts it), else seconds to wait."""
        now = self._clock()
        with self._rate_lock:
            while self._stamps and now - self._stamps[0] >= self._window:
                self._stamps.popleft()
            if len(self._stamps) >= self._limit:
                return max(1, math.ceil(self._stamps[0] + self._window - now))
            self._stamps.append(now)
            return None

    # -- reading --

    def _clean(self, message: str) -> str:
        shown = message.replace(str(self.config), self.config.name)
        return shown.replace(str(self.config.parent), ".")

    def _observe(self, digest: str) -> int:
        with self._version_lock:
            if digest != self._seen:
                self._seen = digest
                self._counter += 1
            return self._counter

    def _load(self) -> tuple[str, dict[str, Any], str | None]:
        """(hash, config as JSON data, build error), consistent with one read."""
        first = b""
        for _ in range(_STATE_TRIES):
            data = self._read(self.config)
            if data is None:
                raise RouteError(404, "not_found", "The config file cannot be read.")
            first = data
            config: dict[str, Any] = {}
            error: str | None = None
            try:
                load_site_config(self.config)
                loaded = load_yaml(self.config)
                if isinstance(loaded, dict):
                    config = json_safe(loaded)
            except (ConfigError, OSError) as exc:
                error = self._clean(str(exc))
            if self._read(self.config) == first:
                break
        return hashlib.sha256(first).hexdigest(), config, error

    def version(self) -> dict[str, Any]:
        digest, _config, error = self._load()
        return {
            "version": self._observe(digest),
            "hash": digest,
            "build": {"ok": error is None},
        }

    def state(self, preview_host: str, preview_port: int) -> dict[str, Any]:
        digest, config, error = self._load()
        try:
            mtime = os.stat(self.config).st_mtime
        except OSError:
            mtime = 0.0
        return {
            "hash": digest,
            "mtime": mtime,
            "config": config,
            "build": {"ok": error is None, "error": error},
            "version": self._observe(digest),
            "preview_url": f"http://{preview_host}:{preview_port}/",
            "limits": {
                "json_bytes": JSON_CAP,
                "mutations_per_minute": self._limit,
                "history_entries": MAX_ENTRIES,
            },
        }

    # -- writing --

    def run_op(
        self, body: bytes, preview_host: str, preview_port: int
    ) -> dict[str, Any]:
        request = parse_json_object(body)
        base = request.get("base")
        if not isinstance(base, str) or _HASH.fullmatch(base) is None:
            raise _bad(
                "base", "base must be the hash from the last state.", "bad_request"
            )
        op = request.get("op")
        if not isinstance(op, str):
            raise _bad("op", "op must be text.", "bad_request")
        spec = OPS.get(op)
        if spec is None:
            raise _bad("op", "That operation does not exist.", "unknown_op")
        args = _check_args(spec, request.get("args"))
        expect = self._check_expect_shape(spec, request.get("expect"))
        label = self._label(request.get("label"), op)
        unknown = set(request) - {"base", "op", "args", "expect", "label"}
        if unknown:
            name = sorted(unknown)[0]
            raise _bad(name, f"{name!r} is not a field of a request.", "bad_request")
        with self._write_lock:
            return self._apply(
                base, op, args, expect, label, preview_host, preview_port
            )

    @staticmethod
    def _check_expect_shape(spec: OpSpec, expect: object) -> dict[str, Any]:
        if expect is None:
            return {}
        if not isinstance(expect, dict):
            raise _bad("expect", "expect must be an object.", "bad_request")
        for name in expect:
            if name not in spec.expect:
                raise _bad(
                    f"expect.{name}", f"{name!r} is not expected here.", "bad_request"
                )
        checked: dict[str, Any] = expect
        return checked

    @staticmethod
    def _label(label: object, op: str) -> str:
        if label is None:
            return op.replace("_", " ").capitalize()
        if (
            not isinstance(label, str)
            or not 1 <= len(label) <= MAX_LABEL
            or not label.isprintable()
        ):
            raise _bad("label", "label must be short plain text.", "bad_request")
        return label

    def _apply(
        self,
        base: str,
        op: str,
        args: dict[str, Any],
        expect: dict[str, Any],
        label: str,
        preview_host: str,
        preview_port: int,
    ) -> dict[str, Any]:
        spec = OPS[op]
        edit = build_edit(op, args)

        def compute(text: str, raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            if sha256_text(text) != base:
                self._require_unchanged(op, args, spec, expect, raw)
            return edit(text, raw)

        try:
            result = editing.apply(
                self.config, compute, dry_run=False, backup=True, force_path=True
            )
        except _Stale as stale:
            raise RouteError(
                409,
                "stale",
                "This changed while you were editing. Your change was not saved.",
                stale.path,
                state=self.state(preview_host, preview_port),
            ) from None
        except editing.EditError as exc:
            raise RouteError(422, "refused", self._clean(str(exc))) from None
        warning: str | None = None
        recorded = False
        if result.written and result.old != result.new:
            try:
                History(self.config).record_write(result.old, result.new, label)
                recorded = True
            except HistoryError:
                warning = "Saved, but undo is not available for this change."
        reply: dict[str, Any] = {
            "state": self.state(preview_host, preview_port),
            "history": {
                "label": label,
                "hash": sha256_text(result.new),
                "recorded": recorded,
            },
        }
        if warning is not None:
            reply["warning"] = warning
        return reply

    @staticmethod
    def _require_unchanged(
        op: str,
        args: dict[str, Any],
        spec: OpSpec,
        expect: dict[str, Any],
        raw: dict[str, Any],
    ) -> None:
        try:
            current = current_expect(op, args, raw)
        except _Gone:
            raise _Stale("expect") from None
        for name in spec.expect:
            if name not in expect or _canon(expect[name]) != _canon(current[name]):
                raise _Stale(f"expect.{name}")
