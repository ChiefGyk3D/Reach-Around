# SPDX-License-Identifier: Apache-2.0
"""``reach-around check --config PATH [--format plain|json] [--against PATH]``.

Validates a site config and says what is wrong in short sentences that name
the tab, the block and the field, each with a fix. It is meant to be read by
someone editing ``site.yaml`` on a phone, and to feed a pull request comment.

What it prints, exactly. Every sentence comes from the closed table in
``reach_around.checkvocab``, chosen by the error's type and path; nothing is
copied from pydantic's message or from the file. The only text that comes from
the file is a short list of names that locate the problem:

* the **tab title**, reduced to letters, digits and a few plain punctuation
  marks and cut to 40 characters (or ``tab N`` when it has none);
* the **block id**, only if it has the shape the config requires, else
  ``block N``;
* the **setting name**, only if it is a name in the config schema. An unknown
  key is reported as ``(unknown setting #N)``, N being its position among its
  siblings, never by its name;
* in ``--against`` output, the same tab titles, block ids and the names of
  top-level settings that differ.

Tab titles and block ids are names that appear on the published site. They are
not values such as URLs, tokens or addresses, which are never printed. The
output is deterministic: no time, no paths, no ordering that depends on a hash.

Exit status: 0 when the config is valid, 2 when it is not (or cannot be read).
"""

from __future__ import annotations

import argparse
import functools
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from reach_around.checkvocab import say
from reach_around.config import ConfigError, SiteConfig, load_yaml
from reach_around.config.models import error_path
from reach_around.schema import build_schema

EXIT_INVALID = 2
_MAX_CHANGES = 50
_MAX_TITLE = 40
_MAX_WORDS = 12

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_WORD = re.compile(r"[A-Za-z0-9_-]{1,20}")
_FILE = re.compile(r"[A-Za-z0-9._-]{1,60}")
_TITLE_EXTRA = frozenset(" -.,!?:()+'")


# --- reducing names to something safe to print --------------------------------


def _safe_title(value: object) -> str | None:
    """A tab title reduced to letters, digits and plain punctuation, or None."""
    if not isinstance(value, str):
        return None
    kept = "".join(ch for ch in value if ch.isalnum() or ch in _TITLE_EXTRA)
    kept = " ".join(kept.split())
    if not kept:
        return None
    if len(kept) > _MAX_TITLE:
        kept = kept[:_MAX_TITLE].rstrip() + "..."
    return kept


def _safe_id(value: object) -> str | None:
    """A tab or block id if it has the shape the config requires, else None."""
    if isinstance(value, str) and _SLUG.fullmatch(value):
        return value
    return None


@functools.cache
def known_fields() -> frozenset[str]:
    """Every setting name the config schema defines, anywhere in the config."""
    names: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                names.update(key for key in props if isinstance(key, str))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(build_schema())
    return frozenset(names)


# --- a problem and how it is shown --------------------------------------------


@dataclass(frozen=True)
class Ref:
    """A tab or block: its name when it has a usable one, and its position."""

    name: str | None
    number: int

    def label(self, kind: str) -> str:
        if self.name is not None:
            return f'{kind} "{self.name}"'
        return f"{kind} {self.number}"

    def json(self, kind: str) -> str:
        return self.name if self.name is not None else f"{kind} {self.number}"


@dataclass(frozen=True)
class Problem:
    kind: str
    tab: Ref | None = None
    block: Ref | None = None
    field: str | None = None
    line: int | None = None
    limit: float | None = None
    words: tuple[str, ...] = ()
    ident: str | None = None

    def texts(self) -> tuple[str, str]:
        return say(
            self.kind,
            limit=self.limit,
            words=list(self.words),
            ident=self.ident,
        )

    def sort_key(self) -> tuple[int, int, str, str]:
        return (
            self.tab.number if self.tab else 0,
            self.block.number if self.block else 0,
            self.field or "",
            self.kind,
        )

    def sentence(self) -> str:
        message, fix = self.texts()
        parts: list[str] = []
        if self.line is not None:
            parts.append(f"Line {self.line}")
        if self.tab is not None:
            parts.append(self.tab.label("Tab"))
        if self.block is not None:
            parts.append(self.block.label("block"))
        if self.field is not None:
            kind = "field" if parts else "Setting"
            parts.append(f'{kind} "{self.field}"')
        where = ", ".join(parts)
        if where:
            return f"{where}: {message}. Fix: {fix}"
        return f"{message[:1].upper()}{message[1:]}. Fix: {fix}"

    def as_dict(self) -> dict[str, Any]:
        message, fix = self.texts()
        out: dict[str, Any] = {
            "tab": self.tab.json("tab") if self.tab else None,
            "block": self.block.json("block") if self.block else None,
            "field": self.field,
            "kind": self.kind,
            "message": message,
            "fix": fix,
        }
        if self.line is not None:
            out["line"] = self.line
        if self.limit is not None:
            out["limit"] = self.limit
        if self.words:
            out["words"] = list(self.words)
        if self.ident is not None:
            out["ident"] = self.ident
        return out


def _navigate(raw: object, loc: tuple[int | str, ...]) -> object:
    node = raw
    for part in loc:
        if isinstance(part, int) and isinstance(node, list) and 0 <= part < len(node):
            node = node[part]
        elif isinstance(part, str) and isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def _field_text(raw: object, loc: tuple[int | str, ...], start: int) -> str | None:
    """The path from ``start`` as text: known names, ``[n]`` counts from 1, and
    ``(unknown setting #N)`` for any key that is not a schema name."""
    out = ""
    for i in range(start, len(loc)):
        part = loc[i]
        if isinstance(part, int):
            out += f"[{part + 1}]"
            continue
        if part in known_fields():
            text = part
        else:
            parent = _navigate(raw, loc[:i])
            if isinstance(parent, dict) and part in parent:
                text = f"(unknown setting #{list(parent).index(part) + 1})"
            else:
                text = "(unknown setting)"
        out += ("." if out else "") + text
    return out or None


# --- file-level errors (the hardened YAML loader) ------------------------------

_LOADER_KINDS: tuple[tuple[str, str], ...] = (
    ("aliases are not allowed", "file_alias"),
    ("anchors are not allowed", "file_anchor"),
    ("merge keys", "file_merge"),
    ("duplicate key", "file_duplicate"),
    ("mapping key is not hashable", "file_key_type"),
    ("nesting is", "file_nesting"),
    ("file too large", "file_too_large"),
    ("not valid UTF-8", "file_not_utf8"),
    ("file is empty", "file_empty"),
    ("symlink", "file_symlink"),
    ("not a regular file", "file_not_regular"),
    ("cannot read file", "file_unreadable"),
    ("tag not allowed", "file_tag"),
)


def _loader_problem(error: ConfigError) -> Problem:
    for needle, kind in _LOADER_KINDS:
        if needle in error.message:
            return Problem(kind, line=error.line)
    return Problem("file_yaml", line=error.line)


# --- schema errors ------------------------------------------------------------


def _number(ctx: dict[str, Any], key: str) -> float | None:
    value = ctx.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if abs(value) < 1e9 else None


def _words(raw: str) -> tuple[str, ...]:
    found = [w for w in re.findall(r"[A-Za-z0-9_-]+", raw) if w != "or"]
    return tuple(w for w in found[:_MAX_WORDS] if _WORD.fullmatch(w))


_LIMIT_KINDS = {
    "greater_than": ("number_gt", "gt"),
    "greater_than_equal": ("number_ge", "ge"),
    "less_than": ("number_lt", "lt"),
    "less_than_equal": ("number_le", "le"),
}
_SIMPLE_KINDS = {
    "missing": "missing",
    "extra_forbidden": "unknown_setting",
    "string_type": "not_text",
    "string_unicode": "not_text",
    "int_type": "not_number",
    "int_parsing": "not_number",
    "float_type": "not_number",
    "float_parsing": "not_number",
    "finite_number": "not_ordinary_number",
    "int_from_float": "not_ordinary_number",
    "bool_type": "not_bool",
    "bool_parsing": "not_bool",
    "list_type": "not_list",
    "tuple_type": "not_list",
    "set_type": "not_list",
    "dict_type": "not_group",
    "model_type": "not_group",
    "model_attributes_type": "not_group",
    "dataclass_type": "not_group",
    "block_type": "not_block",
}


def _typed(error_type: str, ctx: dict[str, Any]) -> Problem:
    """A problem (kind and parameters only) for a pydantic error type."""
    if error_type in _SIMPLE_KINDS:
        return Problem(_SIMPLE_KINDS[error_type])
    if error_type == "string_too_short":
        if ctx.get("min_length") == 1:
            return Problem("empty")
        return Problem("too_short", limit=_number(ctx, "min_length"))
    if error_type == "string_too_long":
        return Problem("too_long", limit=_number(ctx, "max_length"))
    if error_type == "too_short":
        return Problem("too_few_items", limit=_number(ctx, "min_length"))
    if error_type == "too_long":
        return Problem("too_many_items", limit=_number(ctx, "max_length"))
    if error_type == "string_pattern_mismatch":
        pattern = str(ctx.get("pattern")).removeprefix("^").removesuffix("$")
        return Problem("id_shape" if pattern == _SLUG.pattern else "bad_shape")
    if error_type in {"literal_error", "enum"}:
        return Problem("not_in_choices", words=_words(str(ctx.get("expected", ""))))
    if error_type in _LIMIT_KINDS:
        kind, key = _LIMIT_KINDS[error_type]
        return Problem(kind, limit=_number(ctx, key))
    if error_type.startswith("date"):
        return Problem("date_form")
    return Problem("not_allowed")


# (pattern on pydantic's message, kind). Only the kind is used; nothing from
# the message is copied anywhere.
_VALUE_ERRORS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), kind)
    for pattern, kind in (
        (r"^url must start with https://", "url_scheme"),
        (r"^url must include a host name", "url_host"),
        (r"^url must not contain a user name or password", "url_credentials"),
        (r"^url must not contain spaces", "url_chars"),
        (r"^url is (longer|not valid)", "url_invalid"),
        (r"^internal link", "tab_link_form"),
        (r"^an internal tab: link cannot be an affiliate", "tab_link_affiliate"),
        (r"^must be a date written", "date_form"),
        (r"^show_until must not be before", "date_order"),
        (r"^image_alt needs an image", "alt_without_image"),
        (r"^unknown time zone", "timezone"),
        (r"^must not be empty or only whitespace", "empty"),
        (r"^must not start or end with whitespace", "space_edge"),
        (r"^must be plain text", "hidden_chars"),
        (r"^(must be an https URL or|src must be)", "image_source"),
        (r"^(must be a .* path inside|must not be inside a folder)", "image_path"),
        (r"must be a hex color", "color"),
        (r"must be a length", "length"),
        (r"^(font family|third-party font hosts|a font stack)", "font"),
        (r"^(must be at most|must not contain|background)", "style_value"),
        (r"^unknown color", "color_name"),
        (r"^(name is required when|name is only used when)", "name_for_kind"),
        (r"^as_of needs a count", "as_of_count"),
        (r"^a profile card needs", "profile_card_empty"),
        (r"^(dsn_env|a postgres store)", "db_setting"),
        (r"^address must be 20 to 128", "crypto_address"),
        (r"^address must be one plain", "email_address"),
        (r"^must be 1 to 20 characters", "spelled_address"),
        (r"^note must not contain", "note_address"),
        (r"^item ids are the block id", "platforms_id_long"),
        (r"^only the placeholders", "placeholder"),
        (
            r"(sitemap generation|user-agent|allow_all|path prefix|segments|"
            r"^duplicate (path|user-agent))",
            "robots",
        ),
    )
)


def _value_error(message: str) -> str:
    for pattern, kind in _VALUE_ERRORS:
        if pattern.search(message):
            return kind
    return "not_allowed"


# --- finding the tab and block a path points at -------------------------------


def _ref_for(items: object, index: int, namer: Any) -> Ref:
    name = None
    if isinstance(items, list) and 0 <= index < len(items):
        entry = items[index]
        if isinstance(entry, dict):
            name = namer(entry)
    return Ref(name, index + 1)


def _tab_ref(raw: object, t: int) -> Ref:
    tabs = raw.get("tabs") if isinstance(raw, dict) else None
    return _ref_for(tabs, t, lambda tab: _safe_title(tab.get("title")))


def _block_ref(raw: object, t: int, b: int) -> Ref:
    tabs = raw.get("tabs") if isinstance(raw, dict) else None
    blocks = None
    if isinstance(tabs, list) and 0 <= t < len(tabs) and isinstance(tabs[t], dict):
        blocks = tabs[t].get("blocks")
    return _ref_for(blocks, b, lambda block: _safe_id(block.get("id")))


def _context(
    raw: object, loc: tuple[int | str, ...]
) -> tuple[Ref | None, Ref | None, int]:
    """The tab, the block, and where the field path starts in ``loc``."""
    tab: Ref | None = None
    block: Ref | None = None
    start = 0
    if len(loc) >= 2 and loc[0] == "tabs" and isinstance(loc[1], int):
        t = loc[1]
        tab = _tab_ref(raw, t)
        start = 2
        if len(loc) >= 4 and loc[2] == "blocks" and isinstance(loc[3], int):
            block = _block_ref(raw, t, loc[3])
            start = 4
    return tab, block, start


_DUPLICATE_ID = re.compile(r"^duplicate id '([^']*)'")
_TAB_PROBLEM = re.compile(
    r"((?:tabs\[\d+\]\.blocks\[\d+\](?:\.\w+\[\d+\])?|creator\.socials\[\d+\])"
    r"\.url): unknown tab"
)
_PATH_TOKEN = re.compile(r"(\w+)|\[(\d+)\]")


def _loc_from_text(path: str) -> tuple[int | str, ...]:
    return tuple(int(num) if num else word for word, num in _PATH_TOKEN.findall(path))


def _tab_ids(raw: object) -> tuple[str, ...]:
    tabs = raw.get("tabs") if isinstance(raw, dict) else None
    ids = [
        ident
        for tab in (tabs if isinstance(tabs, list) else [])
        if isinstance(tab, dict) and (ident := _safe_id(tab.get("id")))
    ]
    return tuple(ids[:10])


def _model_level(raw: object, message: str) -> list[Problem]:
    """Problems from validators that look at the whole config at once."""
    dup = _DUPLICATE_ID.match(message)
    if dup:
        ident = _safe_id(dup.group(1))
        if ident:
            return [Problem("duplicate_id", ident=ident)]
        return [Problem("duplicate_id_unnamed")]
    if "is reserved for the profile card button" in message:
        return [Problem("reserved_id")]
    found: list[Problem] = []
    for match in _TAB_PROBLEM.finditer(message):
        loc = _loc_from_text(match.group(1))
        tab, block, start = _context(raw, loc)
        found.append(
            Problem(
                "tab_missing",
                tab,
                block,
                _field_text(raw, loc, start),
                words=_tab_ids(raw),
            )
        )
    if found:
        return found
    return [Problem(_value_error(message))]


def _schema_problems(raw: object, error: ValidationError) -> list[Problem]:
    problems: list[Problem] = []
    for err in error.errors(include_url=False):
        loc = error_path(tuple(err["loc"]))
        kind = str(err["type"])
        if kind == "value_error" and not loc:
            problems.extend(
                _model_level(raw, str(err["msg"]).removeprefix("Value error, "))
            )
            continue
        tab, block, start = _context(raw, loc)
        if kind == "value_error":
            base = Problem(_value_error(str(err["msg"]).removeprefix("Value error, ")))
        else:
            base = _typed(kind, err.get("ctx") or {})
        problems.append(
            Problem(
                base.kind,
                tab,
                block,
                _field_text(raw, loc, start),
                limit=base.limit,
                words=base.words,
            )
        )
    return problems


# --- what changed against another config --------------------------------------


@dataclass(frozen=True)
class Change:
    op: str
    tab: str | None = None
    block: str | None = None
    names: tuple[str, ...] = ()
    other: bool = False

    def text(self) -> str:
        label = f'Tab "{self.tab}"'
        if self.op == "tab_added":
            return f"{label} was added."
        if self.op == "tab_removed":
            return f"{label} was removed."
        if self.op == "tab_settings":
            return f"{label}: tab settings changed."
        if self.op == "blocks_reordered":
            return f"{label}: blocks were reordered."
        if self.op == "settings_changed":
            names = [*self.names, *(["another setting"] if self.other else [])]
            return f"Site settings changed: {', '.join(names)}."
        verb = {
            "block_added": "was added",
            "block_changed": "changed",
            "block_removed": "was removed",
        }[self.op]
        return f'{label}: block "{self.block}" {verb}.'

    def as_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "tab": self.tab,
            "block": self.block,
            "names": list(self.names),
            "other": self.other,
        }


def _as_dict(value: object) -> dict[Any, Any]:
    return value if isinstance(value, dict) else {}


def _tabs_by_id(raw: object) -> dict[str, dict[Any, Any]]:
    tabs = _as_dict(raw).get("tabs")
    out: dict[str, dict[Any, Any]] = {}
    for tab in tabs if isinstance(tabs, list) else []:
        ident = _safe_id(_as_dict(tab).get("id"))
        if ident is not None and ident not in out:
            out[ident] = _as_dict(tab)
    return out


def _blocks_by_id(tab: dict[Any, Any]) -> dict[str, Any]:
    blocks = tab.get("blocks")
    out: dict[str, Any] = {}
    for block in blocks if isinstance(blocks, list) else []:
        ident = _safe_id(_as_dict(block).get("id"))
        if ident is not None and ident not in out:
            out[ident] = block
    return out


def _changes(base: object, head: object) -> list[Change]:
    found: list[Change] = []
    before, after = _tabs_by_id(base), _tabs_by_id(head)
    base_top, head_top = _as_dict(base), _as_dict(head)
    differing = [
        key
        for key in {*base_top, *head_top}
        if isinstance(key, str)
        and key != "tabs"
        and base_top.get(key) != head_top.get(key)
    ]
    known = tuple(sorted(key for key in differing if key in known_fields()))
    other = len(known) != len(differing)
    for ident, tab in after.items():
        title = _safe_title(tab.get("title")) or ident
        old = before.get(ident)
        if old is None:
            found.append(Change("tab_added", title))
            continue
        rest_new = {k: v for k, v in tab.items() if k != "blocks"}
        rest_old = {k: v for k, v in old.items() if k != "blocks"}
        if rest_new != rest_old:
            found.append(Change("tab_settings", title))
        old_blocks, new_blocks = _blocks_by_id(old), _blocks_by_id(tab)
        for block_id, block in new_blocks.items():
            if block_id not in old_blocks:
                found.append(Change("block_added", title, block_id))
            elif old_blocks[block_id] != block:
                found.append(Change("block_changed", title, block_id))
        for block_id in old_blocks:
            if block_id not in new_blocks:
                found.append(Change("block_removed", title, block_id))
        common_old = [i for i in old_blocks if i in new_blocks]
        common_new = [i for i in new_blocks if i in old_blocks]
        if common_old != common_new:
            found.append(Change("blocks_reordered", title))
    for ident, tab in before.items():
        if ident not in after:
            found.append(Change("tab_removed", _safe_title(tab.get("title")) or ident))
    if known or other:
        found.append(Change("settings_changed", names=known, other=other))
    return found


# --- the command --------------------------------------------------------------


def _counts(config: SiteConfig) -> tuple[int, int]:
    return len(config.tabs), sum(len(tab.blocks) for tab in config.tabs)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def _display_name(path: Path) -> str:
    return path.name if _FILE.fullmatch(path.name) else "the config file"


_NOTE_TEXT = {
    "compare_failed": "Could not compare with the base: its config could not be read.",
}


@dataclass
class Comparison:
    changes: list[Change] = field(default_factory=list)
    more: int = 0
    note: str | None = None


def _compare(base_path: Path, raw: object) -> Comparison:
    """The changes against ``base_path``, or a note saying why not."""
    try:
        base = load_yaml(base_path)
    except ConfigError:
        return Comparison(note="compare_failed")
    if not isinstance(base, dict) or not isinstance(raw, dict):
        return Comparison(note="compare_failed")
    changes = _changes(base, raw)
    return Comparison(changes[:_MAX_CHANGES], max(0, len(changes) - _MAX_CHANGES), None)


def _run(args: argparse.Namespace) -> int:
    path: Path = args.config
    name = _display_name(path)
    problems: list[Problem] = []
    counts: tuple[int, int] | None = None
    raw: object = None
    try:
        raw = load_yaml(path)
    except ConfigError as exc:
        problems.append(_loader_problem(exc))
    else:
        try:
            counts = _counts(SiteConfig.model_validate(raw))
        except ValidationError as exc:
            problems = _schema_problems(raw, exc)
        except (ValueError, TypeError, RecursionError, OverflowError):
            problems = [Problem("check_failed")]
    seen: set[Problem] = set()
    ordered = []
    for problem in sorted(problems, key=Problem.sort_key):
        if problem not in seen:
            seen.add(problem)
            ordered.append(problem)

    comparison = _compare(args.against, raw) if args.against is not None else None

    if args.format == "json":
        report: dict[str, Any] = {
            "valid": not ordered,
            "file": name,
            "errors": [p.as_dict() for p in ordered],
        }
        if counts is not None:
            report["tabs"], report["blocks"] = counts
        if comparison is not None:
            report["changes"] = [c.text() for c in comparison.changes]
            report["change_records"] = [c.as_dict() for c in comparison.changes]
            report["more_changes"] = comparison.more
            if comparison.note:
                report["note"] = comparison.note
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        lines: list[str] = []
        if ordered:
            lines.append(f"{name} has {_plural(len(ordered), 'problem')}.")
            lines.extend(f"{i}. {p.sentence()}" for i, p in enumerate(ordered, 1))
        elif counts is not None:
            tabs, blocks = counts
            lines.append(
                f"{name} is valid: {_plural(tabs, 'tab')} and "
                f"{_plural(blocks, 'block')}."
            )
        if comparison is not None:
            lines.append("")
            if comparison.note:
                lines.append(_NOTE_TEXT[comparison.note])
            elif comparison.changes:
                lines.append("Changed since the base:")
                lines.extend(f"- {change.text()}" for change in comparison.changes)
                if comparison.more:
                    lines.append(f"- ...and {comparison.more} more.")
            else:
                lines.append("No blocks or settings changed.")
        print("\n".join(lines))
    return EXIT_INVALID if ordered else 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    check = subparsers.add_parser(
        "check",
        help="validate a site config and explain any problem in plain language",
        description=(
            "Validate a site config. Each problem is one short sentence naming "
            "the tab, block and field, with a fix. No config value is printed; "
            "only the tab title and block id (reduced to plain characters) and "
            "setting names that the schema defines. Exit status 0 when valid, "
            "2 when not."
        ),
    )
    check.add_argument("--config", type=Path, required=True, help="site config file")
    check.add_argument(
        "--format",
        choices=("plain", "json"),
        default="plain",
        help="plain sentences (default) or JSON",
    )
    check.add_argument(
        "--against",
        type=Path,
        help="another site config (the base) to list the changed tabs and blocks "
        "against; only names are printed, never values",
    )
    check.set_defaults(func=_run)
