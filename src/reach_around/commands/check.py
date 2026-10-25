# SPDX-License-Identifier: Apache-2.0
"""``reach-around check --config PATH [--format plain|json] [--against PATH]``.

Validates a site config and says what is wrong in short sentences that name
the tab, the block and the field, each with a fix. It is meant to be read by
someone editing ``site.yaml`` on a phone, and to be pasted into a pull request
comment, so two rules hold for every line it prints:

* **No config value is ever printed.** Messages come from fixed text chosen by
  the error's type and path, never from pydantic's own message (which can quote
  the value) and never from the file's contents. What does appear is limited to
  names the reader needs to find the spot: the tab title and the block id
  (reduced to plain characters and cut short), and a field name when it is a
  plain lowercase setting name. A name that does not look like one is replaced
  by ``(name not shown)``, so a secret pasted in the wrong place is not echoed.
* **The output is deterministic.** The same file gives the same bytes: no
  time, no paths, no ordering that depends on a hash.

Exit status: 0 when the config is valid, 2 when it is not (or cannot be read).
``--against`` lists which tabs and blocks differ from another config file (the
pull request's base); it compares structure only and never prints a value.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from reach_around.config import ConfigError, SiteConfig, load_yaml
from reach_around.config.models import error_path
from reach_around.config.profile_card import MEDIA_KIT_ID
from reach_around.config.tablinks import is_tab_ref

EXIT_INVALID = 2
_MAX_CHANGES = 50
_MAX_TITLE = 40

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_KEY = re.compile(r"[a-z][a-z0-9_]{0,29}")
_DIGIT_RUN = re.compile(r"\d{4}")
_FILE = re.compile(r"[A-Za-z0-9._-]{1,60}")
_TITLE_EXTRA = frozenset(" -.,!?:()+'")

_DOCS = "docs/config.md in the Reach Around repository"


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


def _safe_key(value: str) -> str:
    """A setting name if it looks like one; never something secret-shaped."""
    if _KEY.fullmatch(value) and not _DIGIT_RUN.search(value):
        return value
    return "(name not shown)"


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
    tab: Ref | None
    block: Ref | None
    field: str | None
    message: str
    fix: str
    line: int | None = None

    def sort_key(self) -> tuple[int, int, str, str]:
        return (
            self.tab.number if self.tab else 0,
            self.block.number if self.block else 0,
            self.field or "",
            self.message,
        )

    def sentence(self) -> str:
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
        text = f"{where}: {self.message}" if where else self.message
        return f"{text}. Fix: {self.fix}"

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "tab": self.tab.json("tab") if self.tab else None,
            "block": self.block.json("block") if self.block else None,
            "field": self.field,
            "message": self.message,
            "fix": self.fix,
        }
        if self.line is not None:
            out["line"] = self.line
        return out


def _field_text(parts: tuple[int | str, ...]) -> str | None:
    if not parts:
        return None
    out = ""
    for part in parts:
        if isinstance(part, int):
            out += f"[{part + 1}]"  # people count from 1
        else:
            out += ("." if out else "") + _safe_key(part)
    return out


# --- file-level errors (the hardened YAML loader) ------------------------------

_LOADER_MESSAGES: tuple[tuple[str, str, str], ...] = (
    (
        "aliases are not allowed",
        "uses an alias (a * reference)",
        "Write the value out where it is used; aliases and anchors are not allowed.",
    ),
    (
        "anchors are not allowed",
        "uses an anchor (an & label)",
        "Write the value out where it is used; aliases and anchors are not allowed.",
    ),
    (
        "merge keys",
        "uses a merge key (<<)",
        "Write the settings out in full; merge keys are not allowed.",
    ),
    (
        "duplicate key",
        "has a setting written twice",
        "Keep one of the two lines and delete the other.",
    ),
    (
        "mapping key is not hashable",
        "has a setting name that is not plain text",
        "Use a plain word as the setting name.",
    ),
    (
        "nesting is",
        "is nested too deeply",
        "Flatten the lines; a config never needs this many levels.",
    ),
    (
        "file too large",
        "is too large (the limit is 1 MB)",
        "Keep pictures in the media folder and out of site.yaml.",
    ),
    (
        "not valid UTF-8",
        "is not UTF-8 text",
        "Save the file again as UTF-8 text.",
    ),
    (
        "file is empty",
        "is empty",
        "Copy the starter site.yaml back and edit that.",
    ),
    (
        "symlink",
        "is a symbolic link, which is not followed",
        "Commit the real file in its place.",
    ),
    (
        "not a regular file",
        "is not a regular file",
        "Use a normal file named site.yaml.",
    ),
    (
        "cannot read file",
        "could not be read",
        "Check that the file exists at this path and is committed.",
    ),
    (
        "tag not allowed",
        "uses a YAML tag (a ! label), which is not allowed",
        "Remove the tag and write the plain value.",
    ),
)


def _loader_problem(error: ConfigError) -> Problem:
    for needle, message, fix in _LOADER_MESSAGES:
        if needle in error.message:
            return Problem(None, None, None, "The file " + message, fix, error.line)
    return Problem(
        None,
        None,
        None,
        "The file is not valid YAML",
        "Check the indentation and the colons near this line; every setting is "
        "written name: value.",
        error.line,
    )


# --- schema errors ------------------------------------------------------------

_SLUG_FIX = (
    "Use only lowercase letters, digits and hyphens, starting with a letter or "
    "digit, at most 40 characters (for example my-link)."
)
_GROUP = "should be a group of settings (name: value lines), not a single value"


def _ctx_number(ctx: dict[str, Any], key: str) -> str:
    value = ctx.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "the limit"
    return f"{value:g}"


def _expected(ctx: dict[str, Any]) -> str:
    raw = str(ctx.get("expected", ""))
    words = [w for w in re.findall(r"[A-Za-z0-9_-]+", raw) if w != "or"]
    return ", ".join(words[:-1]) + " or " + words[-1] if len(words) > 1 else raw


def _typed(error_type: str, ctx: dict[str, Any], leaf: str | None) -> tuple[str, str]:
    """Message and fix for a pydantic error type; never the input value."""
    name = leaf if leaf and leaf != "(name not shown)" else None
    if error_type == "missing":
        add = f'Add a "{name}:" line.' if name else "Add the missing setting."
        return "is missing", add + f" The settings are listed in {_DOCS}."
    if error_type == "extra_forbidden":
        return (
            "is not a setting Reach Around knows",
            f"Check the spelling, or delete the line. Allowed settings are in {_DOCS}.",
        )
    if error_type == "string_too_short":
        if ctx.get("min_length") == 1:
            return "is empty", "Write something, or delete the line."
        return (
            f"is too short (at least {_ctx_number(ctx, 'min_length')} characters)",
            "Make it longer.",
        )
    if error_type == "string_too_long":
        return (
            f"is too long (at most {_ctx_number(ctx, 'max_length')} characters)",
            "Shorten it.",
        )
    if error_type == "too_short":
        return (
            f"needs at least {_ctx_number(ctx, 'min_length')} item(s)",
            "Add an entry to the list.",
        )
    if error_type == "too_long":
        return (
            f"has too many items (at most {_ctx_number(ctx, 'max_length')})",
            "Remove some entries.",
        )
    if error_type == "string_pattern_mismatch":
        if str(ctx.get("pattern")).removeprefix("^").removesuffix("$") == _SLUG.pattern:
            return "must be lowercase letters, digits and hyphens only", _SLUG_FIX
        return "is not written in an allowed form", f"See {_DOCS} for the form."
    if error_type in {"literal_error", "enum"}:
        return (
            f"must be one of: {_expected(ctx)}",
            "Change it to one of those words, written exactly like that.",
        )
    if error_type in {"string_type", "string_unicode"}:
        return "should be text", "Write it as text; put quotes around it if needed."
    if error_type in {"int_type", "int_parsing", "float_type", "float_parsing"}:
        return "should be a number", "Write just the number, without quotes."
    if error_type in {"finite_number", "int_from_float"}:
        return "should be an ordinary number", "Write a plain number such as 0.4."
    if error_type in {"bool_type", "bool_parsing"}:
        return "should be true or false", "Write true or false, without quotes."
    if error_type in {"list_type", "tuple_type", "set_type"}:
        return (
            "should be a list",
            'Write one entry per line, each starting with "- ".',
        )
    if error_type in {
        "dict_type",
        "model_type",
        "model_attributes_type",
        "dataclass_type",
    }:
        return _GROUP, "Write the settings on indented lines under its name."
    if error_type == "block_type":
        return (
            "is not a block: " + _GROUP,
            "Each block starts with an id and a title, then its other settings.",
        )
    limits = {
        "greater_than": ("gt", "more than"),
        "greater_than_equal": ("ge", "at least"),
        "less_than": ("lt", "less than"),
        "less_than_equal": ("le", "at most"),
    }
    if error_type in limits:
        key, words = limits[error_type]
        return (
            f"must be {words} {_ctx_number(ctx, key)}",
            "Change the number to fit.",
        )
    if error_type.startswith("date"):
        return "must be a date written YYYY-MM-DD", "Write it like 2027-03-01."
    return "has a value that is not allowed", f"See {_DOCS} for what it accepts."


_URL_FIX = "Write the full address, for example https://example.com/page."

# (pattern on pydantic's message, sentence, fix). Only these fixed texts are
# ever printed for a custom validator; nothing from the message is copied.
_VALUE_ERRORS: tuple[tuple[re.Pattern[str], str, str], ...] = tuple(
    (re.compile(pattern), message, fix)
    for pattern, message, fix in (
        (
            r"^url must start with https://",
            "the web address must start with https://",
            _URL_FIX,
        ),
        (
            r"^url must include a host name",
            "the web address needs a site name",
            _URL_FIX,
        ),
        (
            r"^url must not contain a user name or password",
            "the web address must not contain a user name or password",
            "Remove everything before the @ sign. Never put a password in a link.",
        ),
        (
            r"^url must not contain spaces",
            "the web address has a space or a hidden character",
            "Remove it. Copy the address again from the browser.",
        ),
        (
            r"^url is (longer|not valid)",
            "the web address is too long or not valid",
            "Copy the address again from the browser address bar.",
        ),
        (
            r"^internal link",
            "the link to another tab is not written correctly",
            "Write tab: and the tab id, for example tab:links.",
        ),
        (
            r"^internal links must use",
            "the link to another tab is not written correctly",
            "Write tab: and the tab id, for example tab:links.",
        ),
        (
            r"^an internal tab: link cannot be an affiliate",
            "a link to another tab cannot be marked as an affiliate link",
            "Set affiliate to false, or link to a web address.",
        ),
        (
            r"^must be a date written",
            "the date must be written YYYY-MM-DD",
            "Write it like 2027-03-01.",
        ),
        (
            r"^show_until must not be before",
            "the end date is before the start date",
            "Swap the dates or correct one of them.",
        ),
        (
            r"^image_alt needs an image",
            "a picture description needs a picture",
            "Add an image line, or delete image_alt.",
        ),
        (
            r"^unknown time zone",
            "this is not a time zone name Reach Around knows",
            "Use a name like America/New_York or Europe/Paris.",
        ),
        (
            r"^must not be empty or only whitespace",
            "is empty",
            "Write some text, or delete the line.",
        ),
        (
            r"^must not start or end with whitespace",
            "starts or ends with a space",
            "Delete the extra space at the start or end.",
        ),
        (
            r"^must be plain text",
            "contains a hidden or control character",
            "Retype the text by hand instead of pasting it.",
        ),
        (
            r"^(must be an https URL or|src must be)",
            "must be a web address starting with https:// or a path to a "
            "picture in this repository",
            "Put the picture in the media folder and write media/photo.png, "
            "or paste a full https:// address.",
        ),
        (
            r"^(must be a .* path inside|must not be inside a folder)",
            "must be a path to a picture file in this repository",
            "Write a relative path such as media/photo.png, with a picture "
            "file ending, and no .. or leading /.",
        ),
        (
            r"must be a hex color",
            "is not a color",
            "Use a hex color such as #1a2b3c, or a plain color name.",
        ),
        (
            r"must be a length",
            "is not a length",
            "Write a number with a unit, such as 560px or 35rem.",
        ),
        (
            r"^(font family|third-party font hosts|a font stack)",
            "is not an allowed font",
            "Use a system font name, or a font file kept in this repository.",
        ),
        (
            r"^(must be at most|must not contain|background)",
            "is not an allowed style value",
            "Use a plain value only: no url(), braces, semicolons or new lines.",
        ),
        (
            r"^unknown color",
            "has a color name Reach Around does not know",
            f"See the list of color names under theme in {_DOCS}.",
        ),
        (
            r"^(name is required when|name is only used when)",
            "the name setting does not fit the chosen kind",
            "Set name only when the kind is other, and always then.",
        ),
        (
            r"^as_of needs a count",
            "as_of is set without a count",
            "Add the count line, or delete as_of.",
        ),
        (
            r"^a profile card needs",
            "the profile card is empty",
            "Add tags or a button, or delete profile_card.",
        ),
        (
            r"^(dsn_env|a postgres store)",
            "the database setting is not right",
            "dsn_env holds the NAME of an environment variable, in capitals, "
            "never the password itself.",
        ),
        (
            r"^address must be 20 to 128",
            "the crypto address has the wrong length or characters",
            "Copy the address again from your wallet.",
        ),
        (
            r"^address must be one plain",
            "the email address is not a plain address",
            "Write one address such as name@example.com.",
        ),
        (
            r"^must be 1 to 20 characters",
            "the spelled-out address is not allowed",
            "Use a short word with no spaces, @ or dots.",
        ),
        (
            r"^note must not contain",
            "the note contains an address",
            "Remove the address from the note.",
        ),
        (
            r"^item ids are the block id",
            "the block id is too long for a platforms block",
            "Shorten the block id.",
        ),
        (
            r"^only the placeholders",
            "has a { } placeholder that is not allowed",
            "Only {tab}, {creator} and {site} may appear in braces.",
        ),
        (
            r"(sitemap generation|user-agent|allow_all|path prefix|segments|"
            r"^duplicate (path|user-agent))",
            "the robots setting is not allowed",
            f"See robots in {_DOCS}.",
        ),
    )
)


def _value_error(message: str) -> tuple[str, str]:
    for pattern, sentence, fix in _VALUE_ERRORS:
        if pattern.search(message):
            return sentence, fix
    return "has a value that is not allowed", f"See {_DOCS} for what it accepts."


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
) -> tuple[Ref | None, Ref | None, tuple[int | str, ...]]:
    tab: Ref | None = None
    block: Ref | None = None
    rest = loc
    if len(rest) >= 2 and rest[0] == "tabs" and isinstance(rest[1], int):
        t = rest[1]
        tab = _tab_ref(raw, t)
        rest = rest[2:]
        if len(rest) >= 2 and rest[0] == "blocks" and isinstance(rest[1], int):
            block = _block_ref(raw, t, rest[1])
            rest = rest[2:]
    return tab, block, rest


_DUPLICATE_ID = re.compile(r"^duplicate id '([^']*)'")
_TAB_PROBLEM = re.compile(
    r"((?:tabs\[\d+\]\.blocks\[\d+\](?:\.\w+\[\d+\])?|creator\.socials\[\d+\])"
    r"\.url): unknown tab"
)
_PATH_TOKEN = re.compile(r"(\w+)|\[(\d+)\]")


def _loc_from_text(path: str) -> tuple[int | str, ...]:
    return tuple(int(num) if num else word for word, num in _PATH_TOKEN.findall(path))


def _tab_ids(raw: object) -> list[str]:
    tabs = raw.get("tabs") if isinstance(raw, dict) else None
    ids = [
        ident
        for tab in (tabs if isinstance(tabs, list) else [])
        if isinstance(tab, dict) and (ident := _safe_id(tab.get("id")))
    ]
    return ids[:10]


def _model_level(raw: object, message: str) -> list[Problem]:
    """Problems from validators that look at the whole config at once."""
    found: list[Problem] = []
    dup = _DUPLICATE_ID.match(message)
    if dup:
        ident = _safe_id(dup.group(1))
        what = f'The id "{ident}"' if ident else "An id"
        return [
            Problem(
                None,
                None,
                None,
                f"{what} is used more than once",
                "Give every tab, block and link its own id.",
            )
        ]
    if f"id '{MEDIA_KIT_ID}' is reserved" in message:
        return [
            Problem(
                None,
                None,
                None,
                f'The id "{MEDIA_KIT_ID}" is reserved for the profile card button',
                "Rename the block that uses it.",
            )
        ]
    ids = _tab_ids(raw)
    known = ", ".join(ids) if ids else "the id of one of your tabs"
    for match in _TAB_PROBLEM.finditer(message):
        tab, block, rest = _context(raw, _loc_from_text(match.group(1)))
        found.append(
            Problem(
                tab,
                block,
                _field_text(rest),
                "links to a tab that does not exist",
                f"Write tab: and one of your tab ids ({known}).",
            )
        )
    if found:
        return found
    sentence, fix = _value_error(message)
    sentence = sentence[0].upper() + sentence[1:]
    return [Problem(None, None, None, sentence, fix)]


def _schema_problems(raw: object, error: ValidationError) -> list[Problem]:
    problems: list[Problem] = []
    for err in error.errors(include_url=False):
        loc = error_path(tuple(err["loc"]))
        kind = str(err["type"])
        ctx = err.get("ctx") or {}
        if kind == "value_error" and not loc:
            problems.extend(
                _model_level(raw, str(err["msg"]).removeprefix("Value error, "))
            )
            continue
        tab, block, rest = _context(raw, loc)
        if kind == "value_error":
            message, fix = _value_error(str(err["msg"]).removeprefix("Value error, "))
        else:
            leaf = rest[-1] if rest and isinstance(rest[-1], str) else None
            message, fix = _typed(
                kind, ctx, _safe_key(leaf) if leaf is not None else None
            )
        problems.append(Problem(tab, block, _field_text(rest), message, fix))
    return problems


# --- what changed against another config --------------------------------------


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


def _changes(base: object, head: object) -> list[str]:
    found: list[str] = []
    before, after = _tabs_by_id(base), _tabs_by_id(head)
    base_top, head_top = _as_dict(base), _as_dict(head)
    names = sorted(
        {
            _safe_key(key)
            for key in {*base_top, *head_top}
            if isinstance(key, str)
            and key != "tabs"
            and base_top.get(key) != head_top.get(key)
        }
    )
    if "(name not shown)" in names:
        names.remove("(name not shown)")
        names.append("another setting")
    for ident, tab in after.items():
        title = _safe_title(tab.get("title")) or ident
        label = f'Tab "{title}"'
        old = before.get(ident)
        if old is None:
            found.append(f"{label} was added.")
            continue
        rest_new = {k: v for k, v in tab.items() if k != "blocks"}
        rest_old = {k: v for k, v in old.items() if k != "blocks"}
        if rest_new != rest_old:
            found.append(f"{label}: tab settings changed.")
        old_blocks, new_blocks = _blocks_by_id(old), _blocks_by_id(tab)
        for block_id, block in new_blocks.items():
            if block_id not in old_blocks:
                found.append(f'{label}: block "{block_id}" was added.')
            elif old_blocks[block_id] != block:
                found.append(f'{label}: block "{block_id}" changed.')
        for block_id in old_blocks:
            if block_id not in new_blocks:
                found.append(f'{label}: block "{block_id}" was removed.')
        common_old = [i for i in old_blocks if i in new_blocks]
        common_new = [i for i in new_blocks if i in old_blocks]
        if common_old != common_new:
            found.append(f"{label}: blocks were reordered.")
    for ident, tab in before.items():
        if ident not in after:
            title = _safe_title(tab.get("title")) or ident
            found.append(f'Tab "{title}" was removed.')
    if names:
        found.append(f"Site settings changed: {', '.join(names)}.")
    return found


# --- the command --------------------------------------------------------------


def _counts(config: SiteConfig) -> tuple[int, int]:
    return len(config.tabs), sum(len(tab.blocks) for tab in config.tabs)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def _display_name(path: Path) -> str:
    return path.name if _FILE.fullmatch(path.name) else "the config file"


def _compare(base_path: Path, raw: object) -> tuple[list[str], str | None]:
    """The changes against ``base_path``, or a note saying why not."""
    try:
        base = load_yaml(base_path)
    except ConfigError:
        return [], "Could not compare with the base: its config could not be read."
    if not isinstance(base, dict) or not isinstance(raw, dict):
        return [], "Could not compare with the base: it is not a site config."
    changes = _changes(base, raw)
    if len(changes) > _MAX_CHANGES:
        extra = len(changes) - _MAX_CHANGES
        changes = [*changes[:_MAX_CHANGES], f"...and {extra} more."]
    return changes, None


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
            problems = [
                Problem(
                    None,
                    None,
                    None,
                    "The config could not be checked",
                    f"Compare it with the starter site.yaml and {_DOCS}.",
                )
            ]
    seen: set[Problem] = set()
    ordered = []
    for problem in sorted(problems, key=Problem.sort_key):
        if problem not in seen:
            seen.add(problem)
            ordered.append(problem)

    changes: list[str] | None = None
    note: str | None = None
    if args.against is not None:
        changes, note = _compare(args.against, raw)

    if args.format == "json":
        report: dict[str, Any] = {
            "valid": not ordered,
            "file": name,
            "errors": [p.as_dict() for p in ordered],
        }
        if counts is not None:
            report["tabs"], report["blocks"] = counts
        if args.against is not None:
            report["changes"] = changes or []
            if note:
                report["note"] = note
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
        if args.against is not None:
            lines.append("")
            if note:
                lines.append(note)
            elif changes:
                lines.append("Changed since the base:")
                lines.extend(f"- {change}" for change in changes)
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
            "the tab, block and field, with a fix. No config value is printed. "
            "Exit status 0 when valid, 2 when not."
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


__all__ = ["register", "is_tab_ref"]
