# SPDX-License-Identifier: Apache-2.0
"""Hostile escape forms in stylesheet text must never reach the output."""

from __future__ import annotations

import re

import pytest

from reach_around.kit.stage import StageError, clean_css

HOSTILE = [
    # repeated and nested escape forms
    "a{background:u\\\\72l(http://x.test/a)}",
    "a{background:\\\\75rl(http://x.test/a)}",
    "a{background:\\\\\\\\75rl(http://x.test/a)}",
    "a{background:\\5c 75rl(http://x.test/a)}",
    # mixed case
    "a{background:\\5C 55RL(http://x.test/a)}",
    "@\\\\69mport 'http://x.test/a.css';",
    "@\\5c 69MPORT 'http://x.test/a.css';",
    "@\\5C\t69mport 'http://x.test/a.css';",
    # hex escapes with trailing whitespace of every kind
    "a{background:\\75 \\72 \\6c (http://x.test/a)}",
    "a{background:\\75\n\\72\t\\6c\r\n(http://x.test/a)}",
    "a{background:\\5c\f75 rl(http://x.test/a)}",
    "@\\5c 66ont-face{src:\\5c 75rl(http://x.test/f)}",
]


# Sheets from HOSTILE that decode to a fetch function are removed, not refused.
_SANITIZED = {
    "a{background:\\75 \\72 \\6c (http://x.test/a)}",
    "a{background:\\75\n\\72\t\\6c\r\n(http://x.test/a)}",
}


@pytest.mark.parametrize("sheet", HOSTILE)
def test_no_escaped_form_survives_the_filters(sheet: str) -> None:
    if sheet in _SANITIZED:
        out = clean_css(sheet.encode(), "a stylesheet")
        assert out == "a{background: none }"
        return
    with pytest.raises(StageError, match="backslash that remains"):
        clean_css(sheet.encode(), "a stylesheet")


def test_a_backslash_left_after_unescaping_is_refused() -> None:
    with pytest.raises(StageError, match="backslash"):
        clean_css(b"a{background:u\\\\72l(http://x.test/a)}", "a stylesheet")


def test_plain_escapes_still_work() -> None:
    out = clean_css(b".sm\\:flex{color:red}", "a stylesheet")
    assert out == ".sm:flex{color:red}"


def test_a_plain_remote_url_is_still_removed() -> None:
    assert "x.test" not in clean_css(b"a{b:url(http://x.test/a)}", "a stylesheet")


def _comment_free(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


COMMENT_FORMS = [
    "a{background-image:u/\\2a x*/rl(https://x.test/a)}",
    "a{background-image:u/\\2A x\\2a /rl(https://x.test/a)}",
    '@im/\\2a x*/port "https://x.test/a.css";',
    '@im/\\2f\\2a x\\2a \\2f port "https://x.test/a.css";',
    "@font-/\\2a x*/face{src:u/\\2a x*/rl(https://x.test/f)}",
    "a{background-image:u\\2f\\2a x*\\2f rl(https://x.test/a)}",
]


@pytest.mark.parametrize("sheet", COMMENT_FORMS)
def test_escapes_that_make_comment_delimiters_cannot_split_a_token(
    sheet: str,
) -> None:
    with pytest.raises(StageError, match="comment that appears after unescaping"):
        clean_css(sheet.encode(), "a stylesheet")


def test_a_decoded_comment_opener_is_refused() -> None:
    with pytest.raises(StageError, match="comment"):
        clean_css(b"a{b:u/\\2a x*/rl(https://x.test/a)}", "a stylesheet")


def test_more_legitimate_escapes_still_work() -> None:
    out = clean_css(b".a\\2f b{content:\\201C hi\\201D}.w-1\\/2{width:50%}", "s")
    assert out == ".a/b{content:“hi”}.w-1/2{width:50%}"


SPLICE_FORMS = [
    # removing the middle construct would join "/" and "*" into an opener
    b"a{b:c}/@import 'x.css';*/ d{e:f}",
    # ... or "*" and "/" into a closer
]


@pytest.mark.parametrize("sheet", SPLICE_FORMS)
def test_removing_a_construct_cannot_splice_a_comment_delimiter(sheet: bytes) -> None:
    with pytest.raises(StageError, match="comment delimiter after filtering"):
        clean_css(sheet, "a stylesheet")


def test_removal_leaves_a_separator_not_a_join() -> None:
    out = clean_css(b"a{b:u@import 'x.css';rl(y)}", "a stylesheet")
    assert "url(" not in out.lower()
    assert "u rl" in out or "u " in out


def test_the_image_function_is_refused() -> None:
    with pytest.raises(StageError):
        clean_css(b"a{background:image('https://x.test/a.png')}", "a stylesheet")
    with pytest.raises(StageError):
        clean_css(b"a{background:IMAGE ( 'https://x.test/a.png')}", "a stylesheet")


def test_background_image_property_is_not_blanket_rejected() -> None:
    out = clean_css(b"a{background-image:none}", "a stylesheet")
    assert out == "a{background-image:none}"


def test_review_example_with_doubled_backslashes_is_refused() -> None:
    sheet = "a{background:u\\\\2f**\\\\2frl(http://x.test/a)}"
    with pytest.raises(StageError, match="backslash that remains"):
        clean_css(sheet.encode(), "a stylesheet")


def test_review_example_with_single_backslashes_is_refused() -> None:
    sheet = "a{background:u\\2f**\\2frl(http://x.test/a)}"
    with pytest.raises(StageError, match="comment that appears after unescaping"):
        clean_css(sheet.encode(), "a stylesheet")


@pytest.mark.parametrize("newline", ["\n", "\r", "\r\n", "\f"])
def test_a_line_continuation_is_refused(newline: str) -> None:
    sheet = f"a{{background:u\\{newline}rl(http://x.test/a)}}"
    with pytest.raises(StageError, match="line continuation"):
        clean_css(sheet.encode(), "a stylesheet")


@pytest.mark.parametrize("sheet", [b"a{b:/url(x)* c}", b"a{b:*url(x)/ c}"])
def test_a_removed_url_leaves_a_separator(sheet: bytes) -> None:
    out = clean_css(sheet, "a stylesheet")
    assert "/*" not in out and "*/" not in out and "url(" not in out


@pytest.mark.parametrize(
    "sheet", [b"a{b:/@font-face{x:y}* c}", b"a{b:c *@import 'x.css';/ d}"]
)
def test_a_removed_rule_leaves_a_separator(sheet: bytes) -> None:
    out = clean_css(sheet, "a stylesheet")
    assert "/*" not in out and "*/" not in out
