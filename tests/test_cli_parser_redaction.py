# SPDX-License-Identifier: Apache-2.0
"""The command line never repeats an argument back, and never takes a prefix for a flag.

argparse echoes the offending text in several errors, and a mistyped
`leads delete --email-f partner@example.test` would put an address on stderr, where it
can reach a log. It also accepts `--ye` for `--yes` unless abbreviations are off. Both
hold for the top-level parser and every subparser (they take the top parser's class)."""

from __future__ import annotations

import pytest

from reach_around.cli import main

ADDR = "partner@example.test"

ARGVS = [
    # an unknown option on the top parser and on each leads subparser
    ["--bogus", ADDR],
    ["leads", "--bogus", ADDR],
    ["leads", "delete", "--bogus", ADDR],
    ["leads", "show", "--bogus", ADDR],
    ["leads", "delete", "--checklist", "--bogus", ADDR],
    # an invalid choice (a command name, a leads action) holding the address
    [ADDR],
    ["leads", ADDR],
    # an abbreviated flag followed by the address, never taken for the full flag
    ["leads", "delete", "--ye", ADDR],
    ["leads", "delete", "--email-f", ADDR],
    ["leads", "show", "--email-f", ADDR],
    ["leads", "delete", "--legacy-d", ADDR],
    ["leads", "delete", "--fini", ADDR],
]


@pytest.mark.parametrize("argv", ARGVS)
def test_an_error_never_repeats_the_argument_and_a_prefix_is_not_a_flag(
    capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    with pytest.raises(SystemExit) as info:
        main(argv)
    assert info.value.code == 2
    captured = capsys.readouterr()
    assert "partner" not in captured.out + captured.err, argv
    assert "example.test" not in captured.out + captured.err, argv


def test_abbreviation_is_off_for_every_subcommand() -> None:
    from reach_around.cli import _parser

    parser = _parser()
    stack = [parser]
    seen = 0
    while stack:
        p = stack.pop()
        assert p.allow_abbrev is False, p.prog
        seen += 1
        for action in p._actions:
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict):
                stack.extend(v for v in choices.values() if hasattr(v, "allow_abbrev"))
    assert seen > 5
