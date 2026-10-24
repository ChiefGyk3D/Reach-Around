# SPDX-License-Identifier: Apache-2.0
"""Hostile-input tests for overrides (security fix round for Task 6D)."""

import os
from pathlib import Path
from typing import Any

import pytest
from jinja2 import StrictUndefined
from jinja2.exceptions import SecurityError, UndefinedError
from jinja2.sandbox import ImmutableSandboxedEnvironment

from reach_around.site.overrides import (
    OverridesError,
    load_custom_css,
    make_environment,
)


@pytest.fixture
def overrides(tmp_path: Path) -> Path:
    path = tmp_path / "overrides"
    (path / "templates").mkdir(parents=True)
    return path


# ------------------------------------------------------------- 1. sandbox

_SANDBOX_ESCAPES = [
    "{{ cycler.__init__.__globals__.__builtins__.open('%s','w').write('owned') }}",
    "{{ ''.__class__ }}",
    "{{ ''.__class__.__mro__ }}",
    "{{ ''.__class__.__mro__[1].__subclasses__() }}",
    "{{ lipsum.__globals__ }}",
    "{{ range.__globals__ }}",
    "{{ open('%s', 'w') }}",
    "{{ os.system('id') }}",
    "{{ __import__('os') }}",
]


@pytest.mark.parametrize("template", _SANDBOX_ESCAPES)
def test_template_code_execution_is_refused(
    overrides: Path, tmp_path: Path, template: str
) -> None:
    target = tmp_path / "owned.txt"
    source = template % target if "%s" in template else template
    (overrides / "templates" / "evil.html").write_text(source)
    env = make_environment(overrides)
    with pytest.raises((SecurityError, UndefinedError)):
        env.get_template("evil.html").render()
    assert not target.exists()


def test_the_globals_escape_is_a_security_error(
    overrides: Path, tmp_path: Path
) -> None:
    target = tmp_path / "owned.txt"
    (overrides / "templates" / "evil.html").write_text(_SANDBOX_ESCAPES[0] % target)
    env = make_environment(overrides)
    with pytest.raises(SecurityError):
        env.get_template("evil.html").render()
    assert not target.exists()


def test_environment_is_an_immutable_sandbox() -> None:
    env = make_environment(None)
    assert isinstance(env, ImmutableSandboxedEnvironment)
    assert env.autoescape
    assert env.undefined is StrictUndefined


# ----------------------------------------------- 2. symlinked roots/targets


def test_symlinked_overrides_directory_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / "templates").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(OverridesError, match="symlink"):
        make_environment(link)


def test_symlinked_templates_directory_is_refused(
    overrides: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "tab.html").write_text("SECRET")
    (overrides / "templates").rmdir()
    (overrides / "templates").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OverridesError, match="symlink"):
        make_environment(overrides)


def test_symlinked_custom_css_is_refused(overrides: Path) -> None:
    inside = overrides / "real.css"
    inside.write_text("a{}")
    (overrides / "custom.css").symlink_to(inside)
    with pytest.raises(OverridesError, match="symlink"):
        load_custom_css(overrides)


def test_symlinked_subdirectory_pointing_outside_is_refused(
    overrides: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "tab.html").write_text("SECRET")
    (overrides / "templates" / "sub").symlink_to(outside, target_is_directory=True)
    env = make_environment(overrides)
    with pytest.raises(OverridesError):
        env.get_template("sub/tab.html")


# ------------------------------------------------------------ 3. TOCTOU


def _record_flags(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    flags: list[int] = []
    real_open = os.open

    def recording(path: Any, flag: int, *args: Any, **kwargs: Any) -> int:
        flags.append(flag)
        return real_open(path, flag, *args, **kwargs)

    monkeypatch.setattr(os, "open", recording)
    return flags


def test_template_read_uses_o_nofollow(
    overrides: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (overrides / "templates" / "_footer.html").write_text("<footer>X</footer>")
    flags = _record_flags(monkeypatch)
    env = make_environment(overrides)
    assert "X" in env.get_template("_footer.html").render()
    assert any(f & os.O_NOFOLLOW and f & os.O_CLOEXEC for f in flags)


def test_custom_css_read_uses_o_nofollow(
    overrides: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (overrides / "custom.css").write_text("a{}")
    flags = _record_flags(monkeypatch)
    assert load_custom_css(overrides) == "a{}"
    assert any(f & os.O_NOFOLLOW and f & os.O_CLOEXEC for f in flags)


def _swap_before_open(
    monkeypatch: pytest.MonkeyPatch, victim: Path, secret: Path
) -> None:
    real_open = os.open

    def swapping(path: Any, flag: int, *args: Any, **kwargs: Any) -> int:
        if Path(path) == victim or Path(os.path.realpath(path)) == victim.resolve():
            victim.unlink()
            victim.symlink_to(secret)
        return real_open(path, flag, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping)


def test_template_swapped_for_a_symlink_after_the_check_is_not_read(
    overrides: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = tmp_path / "secret.html"
    secret.write_text("EXTERNAL-SECRET")
    victim = overrides / "templates" / "_footer.html"
    victim.write_text("ORIGINAL")
    _swap_before_open(monkeypatch, victim, secret)
    env = make_environment(overrides)
    try:
        rendered = env.get_template("_footer.html").render()
    except OverridesError:
        return
    assert "EXTERNAL-SECRET" not in rendered


def test_custom_css_swapped_for_a_symlink_after_the_check_is_not_read(
    overrides: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = tmp_path / "secret.css"
    secret.write_text(".external-secret{}")
    victim = overrides / "custom.css"
    victim.write_text("a{}")
    _swap_before_open(monkeypatch, victim, secret)
    try:
        css = load_custom_css(overrides)
    except OverridesError:
        return
    assert css is None or "external-secret" not in css
