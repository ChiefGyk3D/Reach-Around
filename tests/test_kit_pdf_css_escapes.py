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


@pytest.mark.parametrize("sheet", HOSTILE)
def test_no_escaped_form_survives_the_filters(sheet: str) -> None:
    try:
        out = clean_css(sheet.encode(), "a stylesheet")
    except StageError:
        return
    assert "\\" not in out
    assert "http://x.test" not in out
    low = out.lower()
    assert "url(" not in low and "@import" not in low and "@font-face" not in low


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
    try:
        out = clean_css(sheet.encode(), "a stylesheet")
    except StageError:
        return
    seen = _comment_free(out).lower()
    assert "https://x.test" not in seen
    for token in ("url(", "@import", "@font-face"):
        assert token not in seen


def test_a_decoded_comment_opener_is_refused() -> None:
    with pytest.raises(StageError, match="comment"):
        clean_css(b"a{b:u/\\2a x*/rl(https://x.test/a)}", "a stylesheet")


def test_more_legitimate_escapes_still_work() -> None:
    out = clean_css(b".a\\2f b{content:\\201C hi\\201D}.w-1\\/2{width:50%}", "s")
    assert out == ".a/b{content:“hi”}.w-1/2{width:50%}"
