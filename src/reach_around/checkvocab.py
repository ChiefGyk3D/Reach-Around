# SPDX-License-Identifier: Apache-2.0
"""The closed vocabulary of ``reach-around check``.

Every problem ``check`` reports is one ``kind`` from this table plus a few
typed parameters (a number, a list of plain words, an id). The sentence and the
fix are looked up here, never copied from the config file or from pydantic.

The private-instance template ships ``scripts/render-comment.py``, which builds
the pull request comment from the JSON ``check`` writes. It carries its own copy
of this table (it cannot import the engine) and refuses any kind that is not in
it. ``tests/test_check_command.py`` fails if the two copies differ.
"""

from __future__ import annotations

DOCS = "docs/config.md in the Reach Around repository"
SLUG_FIX = (
    "Use only lowercase letters, digits and hyphens, starting with a letter or "
    "digit, at most 40 characters (for example my-link)."
)
URL_FIX = "Write the full address, for example https://example.com/page."
GROUP = "should be a group of settings (name: value lines), not a single value"
TAB_LINK_FIX = "Write tab: and the tab id, for example tab:links."
ALIAS_FIX = "Write the value out where it is used; aliases and anchors are not allowed."
NOT_ALLOWED_FIX = f"See {DOCS} for what it accepts."

# kind -> (sentence, fix). {limit} is a number, {words} a list of plain words
# ("a, b or c"), {ident} an id.
VOCAB: dict[str, tuple[str, str]] = {
    # --- types and limits (pydantic error types)
    "missing": (
        "is missing",
        f"Add the missing setting. The settings are listed in {DOCS}.",
    ),
    "unknown_setting": (
        "is not a setting Reach Around knows",
        f"Check the spelling, or delete the line. Allowed settings are in {DOCS}.",
    ),
    "empty": ("is empty", "Write something, or delete the line."),
    "too_short": (
        "is too short (at least {limit} characters)",
        "Make it longer.",
    ),
    "too_long": ("is too long (at most {limit} characters)", "Shorten it."),
    "too_few_items": ("needs at least {limit} item(s)", "Add an entry to the list."),
    "too_many_items": ("has too many items (at most {limit})", "Remove some entries."),
    "id_shape": ("must be lowercase letters, digits and hyphens only", SLUG_FIX),
    "bad_shape": ("is not written in an allowed form", f"See {DOCS} for the form."),
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
    "not_group": (GROUP, "Write the settings on indented lines under its name."),
    "not_block": (
        "is not a block: " + GROUP,
        "Each block starts with an id and a title, then its other settings.",
    ),
    "number_gt": ("must be more than {limit}", "Change the number to fit."),
    "number_ge": ("must be at least {limit}", "Change the number to fit."),
    "number_lt": ("must be less than {limit}", "Change the number to fit."),
    "number_le": ("must be at most {limit}", "Change the number to fit."),
    "not_allowed": ("has a value that is not allowed", NOT_ALLOWED_FIX),
    # --- custom validators
    "url_scheme": ("the web address must start with https://", URL_FIX),
    "url_host": ("the web address needs a site name", URL_FIX),
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
        TAB_LINK_FIX,
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
        "must be a web address starting with https:// or a path to a picture "
        "in this repository",
        "Put the picture in the media folder and write media/photo.png, or paste "
        "a full https:// address.",
    ),
    "image_path": (
        "must be a path to a picture file in this repository",
        "Write a relative path such as media/photo.png, with a picture file "
        "ending, and no .. or leading /.",
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
        f"See the list of color names under theme in {DOCS}.",
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
        "dsn_env holds the NAME of an environment variable, in capitals, never "
        "the password itself.",
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
    "robots": ("the robots setting is not allowed", f"See robots in {DOCS}."),
    # --- validators that look at the whole config
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
    # --- the file itself
    "file_alias": (
        "the file uses an alias (a * reference)",
        ALIAS_FIX,
    ),
    "file_anchor": ("the file uses an anchor (an & label)", ALIAS_FIX),
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
        "Check the indentation and the colons near this line; every setting is "
        "written name: value.",
    ),
    "check_failed": (
        "the config could not be checked",
        f"Compare it with the starter site.yaml and {DOCS}.",
    ),
}


def join_words(words: list[str]) -> str:
    """``a``, ``a or b``, ``a, b or c``."""
    if len(words) > 1:
        return ", ".join(words[:-1]) + " or " + words[-1]
    return words[0] if words else ""


def number_text(value: float) -> str:
    return f"{value:g}"


def say(
    kind: str,
    *,
    limit: float | None = None,
    words: list[str] | None = None,
    ident: str | None = None,
) -> tuple[str, str]:
    """The sentence and the fix for ``kind`` with its parameters filled in."""
    message, fix = VOCAB[kind]
    values = {
        "limit": "the limit" if limit is None else number_text(limit),
        "words": join_words(words or []) or "the allowed words",
        "ident": ident or "",
    }
    # str.replace, not format: the texts hold literal braces ("{tab}").
    for name, text in values.items():
        message = message.replace("{" + name + "}", text)
        fix = fix.replace("{" + name + "}", text)
    return message, fix
