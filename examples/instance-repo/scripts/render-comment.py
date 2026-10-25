#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Render the pull request comment from the preview job's result file.

THIS SCRIPT RUNS IN THE JOB THAT CAN WRITE TO THE PULL REQUEST. The workflow
checks it out from the pull request's BASE commit, so a pull request cannot
change it (the pull request's own copy of this file is not used). Its input is
untrusted: the preview job runs the pull request's code and wrote that file.

So the comment is never taken from the input. The input is data only, and this
script builds every character of the comment from the fixed text below:

* The input must match a strict schema: a size cap, no duplicate keys, exact key
  sets, integers in range, and only kinds from the closed table VOCAB. Anything
  else, and the comment says only that the result could not be read.
* A kind picks a sentence and a fix from VOCAB. The few values that go into a
  sentence are numbers, plain words and ids, each checked against a pattern.
* Names (tab titles, block ids, setting names) are reduced to letters, digits
  and a few plain punctuation marks, cut short, and shown in code spans, where
  nothing is linked or mentioned. A setting name is shown only if it is in
  KNOWN_FIELDS.
* Every fixed sentence is escaped (each ASCII punctuation mark gets a backslash)
  so that no link, mention, issue reference or HTML can form.
* Links come from the workflow run, never from the input.

Usage: render-comment.py --in FILE --out FILE [--run-url URL]
The output file is created new (never over an existing file or a symlink).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from typing import Any, NoReturn

MARKER = "<!-- reach-around-preview -->"
MAX_INPUT = 65536
MAX_BODY = 20000
MAX_ERRORS = 100
MAX_CHANGES = 60
MAX_NAME = 40

# --- generated from reach_around.checkvocab and the config schema by ---------
# --- tests/test_check_renderer.py (it fails if this block drifts) ---
VOCAB: dict[str, tuple[str, str]] = {
    "missing": (
        "is missing",
        "Add the missing setting. The settings are listed in docs/config.md "
        "in the Reach Around repository.",
    ),
    "unknown_setting": (
        "is not a setting Reach Around knows",
        "Check the spelling, or delete the line. Allowed settings are "
        "in docs/config.md in the Reach Around repository.",
    ),
    "empty": ("is empty", "Write something, or delete the line."),
    "too_short": ("is too short (at least {limit} characters)", "Make it longer."),
    "too_long": ("is too long (at most {limit} characters)", "Shorten it."),
    "too_few_items": ("needs at least {limit} item(s)", "Add an entry to the list."),
    "too_many_items": ("has too many items (at most {limit})", "Remove some entries."),
    "id_shape": (
        "must be lowercase letters, digits and hyphens only",
        "Use only lowercase letters, digits and hyphens, starting with a "
        "letter or digit, at most 40 characters (for example my-link).",
    ),
    "bad_shape": (
        "is not written in an allowed form",
        "See docs/config.md in the Reach Around repository for the form.",
    ),
    "not_in_choices": (
        "must be one of: {words}",
        "Change it to one of those words, written exactly like that.",
    ),
    "not_text": ("should be text", "Write it as text; put quotes around it if needed."),
    "not_number": ("should be a number", "Write just the number, without quotes."),
    "not_ordinary_number": (
        "should be an ordinary number",
        "Write a plain number such as 0.4.",
    ),
    "not_bool": ("should be true or false", "Write true or false, without quotes."),
    "not_list": (
        "should be a list",
        'Write one entry per line, each starting with "- ".',
    ),
    "not_group": (
        "should be a group of settings (name: value lines), not a single value",
        "Write the settings on indented lines under its name.",
    ),
    "not_block": (
        "is not a block: should be a group of settings (name: value lines), "
        "not a single value",
        "Each block starts with an id and a title, then its other settings.",
    ),
    "number_gt": ("must be more than {limit}", "Change the number to fit."),
    "number_ge": ("must be at least {limit}", "Change the number to fit."),
    "number_lt": ("must be less than {limit}", "Change the number to fit."),
    "number_le": ("must be at most {limit}", "Change the number to fit."),
    "not_allowed": (
        "has a value that is not allowed",
        "See docs/config.md in the Reach Around repository for what it accepts.",
    ),
    "url_scheme": (
        "the web address must start with https://",
        "Write the full address, for example https://example.com/page.",
    ),
    "url_host": (
        "the web address needs a site name",
        "Write the full address, for example https://example.com/page.",
    ),
    "url_credentials": (
        "the web address must not contain a user name or password",
        "Remove everything before the @ sign. Never put a password in a link.",
    ),
    "url_chars": (
        "the web address has a space or a hidden character",
        "Remove it. Copy the address again from the browser.",
    ),
    "url_invalid": (
        "the web address is too long or not valid",
        "Copy the address again from the browser address bar.",
    ),
    "tab_link_form": (
        "the link to another tab is not written correctly",
        "Write tab: and the tab id, for example tab:links.",
    ),
    "tab_link_affiliate": (
        "a link to another tab cannot be marked as an affiliate link",
        "Set affiliate to false, or link to a web address.",
    ),
    "date_form": ("the date must be written YYYY-MM-DD", "Write it like 2027-03-01."),
    "date_order": (
        "the end date is before the start date",
        "Swap the dates or correct one of them.",
    ),
    "alt_without_image": (
        "a picture description needs a picture",
        "Add an image line, or delete image_alt.",
    ),
    "timezone": (
        "this is not a time zone name Reach Around knows",
        "Use a name like America/New_York or Europe/Paris.",
    ),
    "space_edge": (
        "starts or ends with a space",
        "Delete the extra space at the start or end.",
    ),
    "hidden_chars": (
        "contains a hidden or control character",
        "Retype the text by hand instead of pasting it.",
    ),
    "image_source": (
        "must be a web address starting with https:// or a path to a "
        "picture in this repository",
        "Put the picture in the media folder and write media/photo.png, "
        "or paste a full https:// address.",
    ),
    "image_path": (
        "must be a path to a picture file in this repository",
        "Write a relative path such as media/photo.png, with a picture "
        "file ending, and no .. or leading /.",
    ),
    "color": (
        "is not a color",
        "Use a hex color such as #1a2b3c, or a plain color name.",
    ),
    "length": (
        "is not a length",
        "Write a number with a unit, such as 560px or 35rem.",
    ),
    "font": (
        "is not an allowed font",
        "Use a system font name, or a font file kept in this repository.",
    ),
    "style_value": (
        "is not an allowed style value",
        "Use a plain value only: no url(), braces, semicolons or new lines.",
    ),
    "color_name": (
        "has a color name Reach Around does not know",
        "See the list of color names under theme in docs/config.md in the "
        "Reach Around repository.",
    ),
    "name_for_kind": (
        "the name setting does not fit the chosen kind",
        "Set name only when the kind is other, and always then.",
    ),
    "as_of_count": (
        "as_of is set without a count",
        "Add the count line, or delete as_of.",
    ),
    "profile_card_empty": (
        "the profile card is empty",
        "Add tags or a button, or delete profile_card.",
    ),
    "db_setting": (
        "the database setting is not right",
        "dsn_env holds the NAME of an environment variable, in capitals, "
        "never the password itself.",
    ),
    "crypto_address": (
        "the crypto address has the wrong length or characters",
        "Copy the address again from your wallet.",
    ),
    "email_address": (
        "the email address is not a plain address",
        "Write one address such as name@example.com.",
    ),
    "spelled_address": (
        "the spelled-out address is not allowed",
        "Use a short word with no spaces, @ or dots.",
    ),
    "note_address": (
        "the note contains an address",
        "Remove the address from the note.",
    ),
    "platforms_id_long": (
        "the block id is too long for a platforms block",
        "Shorten the block id.",
    ),
    "placeholder": (
        "has a { } placeholder that is not allowed",
        "Only {tab}, {creator} and {site} may appear in braces.",
    ),
    "robots": (
        "the robots setting is not allowed",
        "See robots in docs/config.md in the Reach Around repository.",
    ),
    "duplicate_id": (
        'the id "{ident}" is used more than once',
        "Give every tab, block and link its own id.",
    ),
    "duplicate_id_unnamed": (
        "an id is used more than once",
        "Give every tab, block and link its own id.",
    ),
    "reserved_id": (
        'the id "profile-media-kit" is reserved for the profile card button',
        "Rename the block that uses it.",
    ),
    "tab_missing": (
        "links to a tab that does not exist",
        "Write tab: and one of your tab ids ({words}).",
    ),
    "file_alias": (
        "the file uses an alias (a * reference)",
        "Write the value out where it is used; aliases and anchors are not allowed.",
    ),
    "file_anchor": (
        "the file uses an anchor (an & label)",
        "Write the value out where it is used; aliases and anchors are not allowed.",
    ),
    "file_merge": (
        "the file uses a merge key (<<)",
        "Write the settings out in full; merge keys are not allowed.",
    ),
    "file_duplicate": (
        "the file has a setting written twice",
        "Keep one of the two lines and delete the other.",
    ),
    "file_key_type": (
        "the file has a setting name that is not plain text",
        "Use a plain word as the setting name.",
    ),
    "file_nesting": (
        "the file is nested too deeply",
        "Flatten the lines; a config never needs this many levels.",
    ),
    "file_too_large": (
        "the file is too large (the limit is 1 MB)",
        "Keep pictures in the media folder and out of site.yaml.",
    ),
    "file_not_utf8": (
        "the file is not UTF-8 text",
        "Save the file again as UTF-8 text.",
    ),
    "file_empty": (
        "the file is empty",
        "Copy the starter site.yaml back and edit that.",
    ),
    "file_symlink": (
        "the file is a symbolic link, which is not followed",
        "Commit the real file in its place.",
    ),
    "file_not_regular": (
        "the file is not a regular file",
        "Use a normal file named site.yaml.",
    ),
    "file_unreadable": (
        "the file could not be read",
        "Check that the file exists at this path and is committed.",
    ),
    "file_tag": (
        "the file uses a YAML tag (a ! label), which is not allowed",
        "Remove the tag and write the plain value.",
    ),
    "file_yaml": (
        "the file is not valid YAML",
        "Check the indentation and the colons near this line; every setting "
        "is written name: value.",
    ),
    "check_failed": (
        "the config could not be checked",
        "Compare it with the starter site.yaml and docs/config.md in the "
        "Reach Around repository.",
    ),
}
KNOWN_FIELDS: frozenset[str] = frozenset(
    [
        "acct",
        "actor",
        "address",
        "affiliate",
        "ai_crawlers",
        "allow_all",
        "alt",
        "analytics",
        "api_key_env",
        "as_of",
        "at",
        "avatar",
        "background",
        "background_dim",
        "block",
        "blocks",
        "body",
        "button",
        "button_style",
        "channel_id",
        "click_tracking",
        "client_id_env",
        "client_secret_env",
        "coin",
        "coins",
        "collapsed",
        "colors",
        "column_width",
        "contact",
        "count",
        "creator",
        "credit",
        "credit_text",
        "disclosure",
        "dot",
        "dsn_env",
        "favicon",
        "featured",
        "features",
        "fonts",
        "footer",
        "handle",
        "header",
        "heading",
        "host",
        "icon",
        "id",
        "image",
        "image_alt",
        "intro",
        "invite",
        "items",
        "kind",
        "kit_gate",
        "label",
        "layout",
        "lead_retention_days",
        "link",
        "links",
        "login",
        "markdown",
        "mascot",
        "mode",
        "name",
        "network",
        "noindex_paths",
        "note",
        "numbers",
        "page_title",
        "page_views",
        "path",
        "platform",
        "privacy",
        "profile_card",
        "qr",
        "radius",
        "retention_days",
        "reveal",
        "robots",
        "room",
        "route",
        "show_from",
        "show_until",
        "sitemap",
        "socials",
        "sources",
        "spell",
        "src",
        "store",
        "subtitle",
        "tabs",
        "tags",
        "theme",
        "timezone",
        "title",
        "title_template",
        "url",
        "user",
        "version",
    ]
)
# --- end generated -----------------------------------------------------------

OPS = frozenset(
    {
        "tab_added",
        "tab_removed",
        "tab_settings",
        "block_added",
        "block_changed",
        "block_removed",
        "blocks_reordered",
        "settings_changed",
    }
)
NOTES = frozenset({"compare_failed"})
NAME_EXTRA = frozenset(" -.,!?:()+'")
WORD = re.compile(r"[A-Za-z0-9_-]{1,20}")
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
RUN_URL = re.compile(
    r"https://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/actions/runs/[0-9]{1,20}"
)
FIELD_TOKEN = re.compile(
    r"\.?(?:(?P<name>[a-z][a-z0-9_]*)|\[(?P<index>[0-9]{1,4})\]"
    r"|\(unknown setting(?: #(?P<pos>[0-9]{1,4}))?\))"
)
ASCII_PUNCT = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")


class Invalid(Exception):
    """The input does not match the schema."""


def fail(message: str) -> NoReturn:
    print(f"render-comment: {message}", file=sys.stderr)
    raise SystemExit(2)


# --- reading the input -------------------------------------------------------


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise Invalid("duplicate key")
        out[key] = value
    return out


def _refuse_constant(_: str) -> NoReturn:
    raise Invalid("not a plain number")


def read_json(path: str) -> Any:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise Invalid("cannot open the input") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_INPUT:
            raise Invalid("not a regular file, or too large")
        data = os.read(fd, MAX_INPUT + 1)
    except OSError:
        raise Invalid("cannot read the input") from None
    finally:
        os.close(fd)
    if len(data) > MAX_INPUT:
        raise Invalid("too large")
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_no_duplicates,
            parse_constant=_refuse_constant,
        )
    except (ValueError, RecursionError):
        raise Invalid("not valid JSON") from None


# --- the schema --------------------------------------------------------------


def is_int(value: Any, low: int, high: int) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool) and low <= value <= high
    )


def is_str(value: Any, limit: int) -> bool:
    return isinstance(value, str) and len(value) <= limit


def as_dict(value: Any, required: set[str], optional: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise Invalid("expected an object")
    keys = set(value)
    if not required <= keys or not keys <= required | optional:
        raise Invalid("unexpected keys")
    return value


def as_list(value: Any, limit: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > limit:
        raise Invalid("expected a short list")
    return value


def check_error(raw: Any) -> dict[str, Any]:
    err = as_dict(
        raw,
        {"tab", "block", "field", "kind", "message", "fix"},
        {"line", "limit", "words", "ident"},
    )
    for key in ("tab", "block", "field"):
        if err[key] is not None and not is_str(err[key], 200):
            raise Invalid(key)
    if err["kind"] not in VOCAB:
        raise Invalid("kind")
    # message and fix are the engine's own wording; they are checked for shape
    # and never used, the wording comes from VOCAB.
    if not is_str(err["message"], 400) or not is_str(err["fix"], 400):
        raise Invalid("text")
    if "line" in err and not is_int(err["line"], 1, 10_000_000):
        raise Invalid("line")
    if "limit" in err:
        limit = err["limit"]
        if isinstance(limit, bool) or not isinstance(limit, int | float):
            raise Invalid("limit")
        if not -1e9 < limit < 1e9:
            raise Invalid("limit")
    if "words" in err:
        words = as_list(err["words"], 12)
        if not all(isinstance(w, str) and WORD.fullmatch(w) for w in words):
            raise Invalid("words")
    if "ident" in err and not (
        isinstance(err["ident"], str) and SLUG.fullmatch(err["ident"])
    ):
        raise Invalid("ident")
    return err


def check_change(raw: Any) -> dict[str, Any]:
    rec = as_dict(raw, {"op", "tab", "block", "names", "other"}, set())
    if rec["op"] not in OPS:
        raise Invalid("op")
    for key in ("tab", "block"):
        if rec[key] is not None and not is_str(rec[key], 200):
            raise Invalid(key)
    names = as_list(rec["names"], 20)
    if not all(isinstance(n, str) and n in KNOWN_FIELDS for n in names):
        raise Invalid("names")
    if not isinstance(rec["other"], bool):
        raise Invalid("other")
    return rec


def check_report(raw: Any) -> dict[str, Any]:
    rep = as_dict(
        raw,
        {"valid", "file", "errors"},
        {"tabs", "blocks", "changes", "change_records", "more_changes", "note"},
    )
    if not isinstance(rep["valid"], bool) or not is_str(rep["file"], 100):
        raise Invalid("report")
    rep["errors"] = [check_error(e) for e in as_list(rep["errors"], MAX_ERRORS)]
    for key in ("tabs", "blocks", "more_changes"):
        if key in rep and not is_int(rep[key], 0, 1_000_000):
            raise Invalid(key)
    if "changes" in rep:
        texts = as_list(rep["changes"], MAX_CHANGES)
        if not all(is_str(t, 400) for t in texts):
            raise Invalid("changes")
    if "change_records" in rep:
        rep["change_records"] = [
            check_change(r) for r in as_list(rep["change_records"], MAX_CHANGES)
        ]
    if "note" in rep and rep["note"] not in NOTES:
        raise Invalid("note")
    return rep


def check_result(raw: Any) -> dict[str, Any]:
    result = as_dict(raw, {"check_status", "build_status", "check"}, set())
    if not is_int(result["check_status"], 0, 255):
        raise Invalid("check_status")
    if result["build_status"] is not None and not is_int(
        result["build_status"], 0, 255
    ):
        raise Invalid("build_status")
    if result["check"] is not None:
        result["check"] = check_report(result["check"])
    return result


# --- building the comment ----------------------------------------------------


def esc(text: str) -> str:
    """Fixed text with every ASCII punctuation mark backslash-escaped."""
    return "".join("\\" + ch if ch in ASCII_PUNCT else ch for ch in text)


def name(value: Any) -> str:
    """A tab title or block id reduced to plain characters, as a code span."""
    text = value if isinstance(value, str) else ""
    kept = "".join(ch for ch in text if ch.isalnum() or ch in NAME_EXTRA)
    kept = " ".join(kept.split())
    if len(kept) > MAX_NAME:
        kept = kept[:MAX_NAME].rstrip() + "..."
    return f"`{kept or '?'}`"


def field(value: Any) -> str | None:
    """A field path as a code span, or None. Only schema names are kept."""
    if not isinstance(value, str) or not value:
        return None
    out = ""
    pos = 0
    while pos < len(value):
        match = FIELD_TOKEN.match(value, pos)
        if match is None:
            return None
        pos = match.end()
        if match["name"] is not None:
            if match["name"] not in KNOWN_FIELDS:
                return None
            out += ("." if out else "") + match["name"]
        elif match["index"] is not None:
            out += f"[{int(match['index'])}]"
        else:
            suffix = f" #{int(match['pos'])}" if match["pos"] else ""
            out += ("." if out else "") + f"(unknown setting{suffix})"
    return f"`{out}`"


def number(value: float) -> str:
    return f"{value:g}"


def words_text(words: list[str]) -> str:
    if len(words) > 1:
        return ", ".join(words[:-1]) + " or " + words[-1]
    return words[0] if words else "the allowed words"


def fill(template: str, err: dict[str, Any]) -> str:
    """A VOCAB text with its parameters, then escaped."""
    limit = err.get("limit")
    values = {
        "limit": "the limit" if limit is None else number(limit),
        "words": words_text(err.get("words", [])),
        "ident": err.get("ident", ""),
    }
    for key, text in values.items():
        template = template.replace("{" + key + "}", text)
    return esc(template)


def problem_line(err: dict[str, Any]) -> str:
    message, fix = VOCAB[err["kind"]]
    parts = []
    if "line" in err:
        parts.append(f"Line {err['line']}")
    if err["tab"] is not None:
        parts.append("Tab " + name(err["tab"]))
    if err["block"] is not None:
        parts.append("block " + name(err["block"]))
    shown = field(err["field"])
    if shown is not None:
        parts.append(("field " if parts else "Setting ") + shown)
    text = fill(message, err)
    if parts:
        return f"{', '.join(parts)}: {text}. Fix: {fill(fix, err)}"
    return f"{text[:1].upper()}{text[1:]}. Fix: {fill(fix, err)}"


def change_line(rec: dict[str, Any]) -> str:
    op = rec["op"]
    tab = "Tab " + name(rec["tab"])
    if op == "tab_added":
        return f"{tab} was added."
    if op == "tab_removed":
        return f"{tab} was removed."
    if op == "tab_settings":
        return f"{tab}: tab settings changed."
    if op == "blocks_reordered":
        return f"{tab}: blocks were reordered."
    if op == "settings_changed":
        names = [f"`{n}`" for n in rec["names"]]
        if rec["other"]:
            names.append("another setting")
        return f"Site settings changed: {', '.join(names)}."
    verb = {
        "block_added": "was added",
        "block_changed": "changed",
        "block_removed": "was removed",
    }[op]
    return f"{tab}: block {name(rec['block'])} {verb}."


def details(report: dict[str, Any] | None) -> list[str]:
    if report is None:
        return []
    lines: list[str] = []
    if report["errors"]:
        count = len(report["errors"])
        lines.append(f"{count} problem{'' if count == 1 else 's'} found:")
        lines.append("")
        lines.extend(
            f"{i}. {problem_line(e)}" for i, e in enumerate(report["errors"], 1)
        )
    elif "tabs" in report and "blocks" in report:
        tabs, blocks = report["tabs"], report["blocks"]
        lines.append(
            f"The config is valid: {tabs} tab{'' if tabs == 1 else 's'} and "
            f"{blocks} block{'' if blocks == 1 else 's'}."
        )
    if "change_records" in report or "note" in report:
        lines.append("")
        records = report.get("change_records", [])
        if "note" in report:
            lines.append(
                "Could not compare with the base: its config could not be read."
            )
        elif records:
            lines.append("Changed since the base:")
            lines.extend(f"- {change_line(r)}" for r in records)
            if report.get("more_changes"):
                lines.append(f"- ...and {report['more_changes']} more.")
        else:
            lines.append("No blocks or settings changed.")
    return lines


def link(run_url: str | None) -> list[str]:
    if run_url is not None and RUN_URL.fullmatch(run_url):
        return ["", f"[Run details]({run_url})"]
    return []


def build(result: dict[str, Any] | None, run_url: str | None) -> str:
    out = [MARKER]
    if result is None:
        out += [
            "## The preview result could not be read",
            "",
            "This is a problem with the check, not necessarily with your change.",
            "Nothing is deployed. Open the run details to see what happened.",
        ]
        return "\n".join([*out, *link(run_url)]) + "\n"
    check_status, build_status, report = (
        result["check_status"],
        result["build_status"],
        result["check"],
    )
    if check_status == 0 and report is not None and build_status == 0:
        out += [
            "## The preview is ready",
            "",
            "Your change is valid and the site builds.",
            "",
        ]
        out += details(report)
        out += [
            "",
            "**Look at it:** open the run details below, download the `preview`",
            "artifact from the Artifacts section, unzip it and open `index.html`.",
            "",
            "Nothing is deployed yet. Merging this pull request into `main` deploys",
            "it, after a reviewer approves.",
        ]
    elif check_status == 0 and report is not None:
        out += ["## The settings are valid, but the site did not build", ""]
        out += details(report)
        out += [
            "",
            "Nothing is deployed. The build stopped for a reason the settings check",
            "cannot see (for example a picture file that is not in the repository).",
            "Open the run details to see which file or setting.",
        ]
    elif check_status == 2:
        out += ["## This change has a problem", ""]
        out += details(report)
        out += [
            "",
            "Nothing was built and nothing is deployed. Edit `site.yaml` on this",
            "branch again; this check runs again after every save. The numbers count",
            "tabs, blocks and list entries from 1.",
        ]
    else:
        out += [
            "## The check could not run",
            "",
            "This is a problem with the check, not with your change. Nothing was",
            "built and nothing is deployed. Open the run details. The usual causes are",
            "a file that must not be committed (such as a `.env` file) or an engine",
            "version in `engine.lock` that is too old to have the `check` command.",
        ]
    body = "\n".join([*out, *link(run_url)]) + "\n"
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY].rsplit("\n", 1)[0] + "\n"
    return body


# --- writing the output ------------------------------------------------------


def write_new(path: str, text: str) -> None:
    """Create ``path`` new: not over a file, not through a symlink."""
    parent = os.path.dirname(os.path.abspath(path))
    if os.path.realpath(parent) != parent:
        fail("the output folder is, or sits under, a symbolic link")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError:
        fail("cannot create the output file (it must not exist yet)")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--in", dest="source", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--run-url")
    args = parser.parse_args()
    try:
        result: dict[str, Any] | None = check_result(read_json(args.source))
    except Invalid:
        result = None
    write_new(args.out, build(result, args.run_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
