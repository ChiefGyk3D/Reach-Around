# SPDX-License-Identifier: Apache-2.0
"""Hostile-input tests for overrides (security fix round for Task 6D)."""

import os
from pathlib import Path
from typing import Any

import pytest
from jinja2 import StrictUndefined
from jinja2.exceptions import SecurityError, UndefinedError
from jinja2.sandbox import ImmutableSandboxedEnvironment

from reach_around.cli import main
from reach_around.site.overrides import (
    MAX_OVERRIDE_TEMPLATE_BYTES,
    OverridesError,
    build_csp,
    load_custom_css,
    make_environment,
    render_headers,
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


# ------------------------------------------------------- 6. header injection

_BAD_TAILS = ["\n", "\r", "\t", " ", "\0", " ", "\r\n", "\x0b", "\x85"]


@pytest.mark.parametrize("tail", _BAD_TAILS)
def test_csp_source_with_trailing_control_or_space_is_refused(tail: str) -> None:
    with pytest.raises(OverridesError):
        build_csp({"script-src": ["https://example.com" + tail]})
    with pytest.raises(OverridesError):
        build_csp({"script-src": ["'self'" + tail]})
    with pytest.raises(OverridesError):
        build_csp({"script-src": [tail + "https://example.com"]})


@pytest.mark.parametrize("tail", _BAD_TAILS)
def test_csp_directive_name_with_trailing_control_is_refused(tail: str) -> None:
    with pytest.raises(OverridesError):
        build_csp({"script-src" + tail: ["https://example.com"]})


@pytest.mark.parametrize("tail", _BAD_TAILS)
def test_header_path_with_trailing_control_or_space_is_refused(tail: str) -> None:
    good = {"script-src": ["https://a.example.com"]}
    for path in ("/*" + tail, "/gate/*" + tail, tail + "/gate/*", "/ga" + tail + "te"):
        with pytest.raises(OverridesError):
            render_headers({path: good})


def test_site_wide_path_is_refused_with_and_without_whitespace() -> None:
    good = {"script-src": ["https://a.example.com"]}
    for path in ("/*", "/*\n", "/* ", "/*\r\n"):
        with pytest.raises(OverridesError):
            render_headers({path: good})


def test_rendered_headers_contain_only_expected_newlines() -> None:
    text = render_headers({"/gate/*": {"script-src": ["https://a.example.com"]}})
    assert "\r" not in text and "\0" not in text and " " not in text
    for line in text.split("\n"):
        assert line == "" or line.startswith(("/", "  "))
    assert text.count("Content-Security-Policy:") == 2


def test_csp_port_must_be_ascii_digits() -> None:
    with pytest.raises(OverridesError):
        build_csp({"script-src": ["https://example.com:\u0663\u0663"]})


# ------------------------------------------------------ CLI, hostile inputs

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "instance" / "site.yaml"


def _hostile_dir(tmp_path: Path, name: str) -> Path:
    """Build one hostile overrides directory by name."""
    base = tmp_path / name
    (base / "templates").mkdir(parents=True)
    outside = tmp_path / f"{name}-outside"
    outside.mkdir()
    (outside / "tab.html").write_text("SECRET")
    (outside / "x.css").write_text("a{}")
    if name == "symlinked-root":
        link = tmp_path / "link"
        link.symlink_to(base, target_is_directory=True)
        return link
    if name == "symlinked-templates":
        (base / "templates").rmdir()
        (base / "templates").symlink_to(outside, target_is_directory=True)
    elif name == "symlinked-css":
        (base / "custom.css").symlink_to(outside / "x.css")
    elif name == "sandbox-escape":
        (base / "templates" / "_footer.html").write_text(
            "{{ cycler.__init__.__globals__.__builtins__.open('/nonexistent/x') }}"
        )
    elif name == "css-escape":
        (base / "custom.css").write_text(
            "a{--x:\\2f\\2a; background:url(//attacker.example/pixel)}"
        )
    elif name == "css-remote":
        (base / "custom.css").write_text(
            "a{background:url(https://attacker.example/p)}"
        )
    elif name == "css-unterminated":
        (base / "custom.css").write_text("a{color:red} /*")
    return base


@pytest.mark.parametrize(
    "name",
    [
        "symlinked-root",
        "symlinked-templates",
        "symlinked-css",
        "sandbox-escape",
        "css-escape",
        "css-remote",
        "css-unterminated",
    ],
)
def test_cli_build_refuses_hostile_overrides_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    hostile = _hostile_dir(tmp_path, name)
    code = main(
        [
            "build",
            "--config",
            str(EXAMPLE),
            "--out",
            str(tmp_path / "out"),
            "--overrides",
            str(hostile),
        ]
    )
    err = capsys.readouterr().err
    assert code == 1
    assert err.startswith("error: ")
    assert len(err.strip().splitlines()) == 1
    assert "Traceback" not in err
    assert not (tmp_path / "out").exists()


# ------------------------------------------------- 4 and 5. custom.css rules


def _css(overrides: Path, text: str) -> str | None:
    (overrides / "custom.css").write_text(text)
    return load_custom_css(overrides)


@pytest.mark.parametrize(
    "css",
    [
        "a{--x:\\2f\\2a; background:url(//attacker.example/pixel)}",
        "a { background: \\75rl(https://attacker.example/x) }",
        "a { background: u\\rl(x.png) }",
        "a { background: u/**/rl(//attacker.example/x) }",
        "a { background: u/**/rl(https://attacker.example/x) }",
        "@\\69mport 'x.css';",
        "a { color: red } /* never closed",
        "a { color: red } /*",
        'a{content:"/*"; background:url(//attacker.example/x)} b{content:"*/"}',
        "/* c */ a { background: url(//attacker.example/x) }",
        "a { content: '\\201C' }",
        "a { /* \\ */ color: red }",
        "/* x */ @import 'x.css';",
    ],
)
def test_css_tokenizer_tricks_are_refused(overrides: Path, css: str) -> None:
    with pytest.raises(OverridesError):
        _css(overrides, css)


def test_backslash_error_says_why(overrides: Path) -> None:
    with pytest.raises(OverridesError, match="backslash"):
        _css(overrides, "a { content: '\\201C' }")


def test_forbidden_word_inside_a_comment_is_allowed_outside_it_is_not(
    overrides: Path,
) -> None:
    assert _css(overrides, "/* @import 'x.css'; expression(1) */ a{color:red}")
    with pytest.raises(OverridesError):
        _css(overrides, "@import 'x.css'; /* fine */ a{color:red}")


@pytest.mark.parametrize(
    "css",
    [
        "a { background: url(https://attacker.example/pixel) }",
        'a { background: url("https://attacker.example/pixel") }',
        "a { background: url('HTTPS://attacker.example/pixel') }",
        "a { background: url(//attacker.example/pixel) }",
        'a { background: url("ht\ttps://attacker.example/pixel") }',
        'a { background: url("/\t/attacker.example/pixel") }',
        'a { background: url("/\n/attacker.example/pixel") }',
        "a { background: url(mailto:x@example.com) }",
    ],
)
def test_custom_css_url_must_be_relative_and_same_site(
    overrides: Path, css: str
) -> None:
    with pytest.raises(OverridesError, match="relative"):
        _css(overrides, css)


def test_remote_url_error_points_to_the_image_block(overrides: Path) -> None:
    with pytest.raises(OverridesError, match="Image block"):
        _css(overrides, "a { background: url(https://attacker.example/x) }")


@pytest.mark.parametrize(
    "css",
    [
        "a { background: url(img/bg.png) }",
        "a { background: url('../img/bg.png') }",
        'a { background: url("/img/bg.png") }',
        "/* @import 'x.css' */ a { color: red }",
        'a::before { content: "quote" }',
    ],
)
def test_custom_css_relative_urls_and_plain_css_are_accepted(
    overrides: Path, css: str
) -> None:
    # a comment is replaced by one space (the build rewrites and publishes the
    # same text that was checked); everything else is unchanged
    expected = css.replace("/* @import 'x.css' */", " ")
    assert _css(overrides, css) == expected


# ----------------------------------------- CSS functions that can fetch a URL

_FETCHING_FUNCTIONS = [
    "image",
    "image-set",
    "-webkit-image-set",
    "-moz-image-set",
    "src",
    "cross-fade",
    "-webkit-cross-fade",
]


@pytest.mark.parametrize("quote", ['"', "'"])
@pytest.mark.parametrize("gap", ["", " ", "\n"])
@pytest.mark.parametrize("name", _FETCHING_FUNCTIONS)
@pytest.mark.parametrize("case", [str.lower, str.upper, str.title])
def test_css_functions_that_take_a_url_are_refused(
    overrides: Path, name: str, gap: str, quote: str, case: Any
) -> None:
    arg = f"{quote}https://evil.example/x{quote}"
    with pytest.raises(OverridesError, match="refused"):
        _css(overrides, f"a{{background:{case(name)}{gap}({arg})}}")


def test_image_function_with_string_argument_is_refused(overrides: Path) -> None:
    with pytest.raises(OverridesError, match=r"image\(\)"):
        _css(overrides, 'a{background:image("https://evil.example/x")}')


def test_function_names_hidden_by_comments_are_refused(overrides: Path) -> None:
    for css in (
        'a{background:ima/**/ge("https://evil.example/x")}',
        'a{background:cross-/**/fade("https://evil.example/x")}',
    ):
        with pytest.raises(OverridesError):
            _css(overrides, css)


@pytest.mark.parametrize(
    "rule", ['@namespace "http://x.example/ns";', "@namespace url(x);"]
)
def test_namespace_rule_is_refused(overrides: Path, rule: str) -> None:
    with pytest.raises(OverridesError, match="@namespace"):
        _css(overrides, rule)


def test_font_face_with_remote_src_is_refused(overrides: Path) -> None:
    with pytest.raises(OverridesError, match="relative"):
        _css(
            overrides,
            "@font-face{font-family:x;src:url(https://evil.example/f.woff2)}",
        )


@pytest.mark.parametrize(
    "css",
    [
        "@font-face{font-family:x;src:url(fonts/x.woff2) format('woff2')}",
        "@font-face{font-family:x;src:local(Arial)}",
        "a{background:linear-gradient(red,blue)}",
        "a{background-image:none;image-rendering:pixelated;object-fit:cover}",
        "a{background:element(#some-id)}",
        "a{background:paint(my-worklet)}",
        "a{--src:1;--image:2;color:var(--src)}",
    ],
)
def test_non_fetching_css_is_still_accepted(overrides: Path, css: str) -> None:
    assert _css(overrides, css) == css


# ------------------------------------------------- override template size cap


def _template_with_bytes(overrides: Path, size: int) -> None:
    (overrides / "templates" / "_footer.html").write_bytes(b"x" * size)


def test_template_exactly_at_the_cap_is_loaded(overrides: Path) -> None:
    _template_with_bytes(overrides, MAX_OVERRIDE_TEMPLATE_BYTES)
    env = make_environment(overrides)
    rendered = env.get_template("_footer.html").render()
    assert len(rendered) == MAX_OVERRIDE_TEMPLATE_BYTES


def test_template_one_byte_over_the_cap_is_refused_by_name(overrides: Path) -> None:
    _template_with_bytes(overrides, MAX_OVERRIDE_TEMPLATE_BYTES + 1)
    env = make_environment(overrides)
    with pytest.raises(OverridesError, match=r"_footer\.html.*256 KiB"):
        env.get_template("_footer.html")


def test_huge_sparse_template_is_not_read_past_the_cap(
    overrides: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    victim = overrides / "templates" / "_footer.html"
    with victim.open("wb") as handle:
        handle.truncate(512 * 1024 * 1024)  # sparse: no disk used
    total = 0
    real_read = os.read

    def counting(fd: int, n: int) -> bytes:
        nonlocal total
        chunk = real_read(fd, n)
        total += len(chunk)
        return chunk

    monkeypatch.setattr(os, "read", counting)
    env = make_environment(overrides)
    with pytest.raises(OverridesError, match=r"_footer\.html.*256 KiB"):
        env.get_template("_footer.html")
    assert total <= MAX_OVERRIDE_TEMPLATE_BYTES + 1


def test_template_cap_is_enforced_on_bytes_read_not_on_stat(
    overrides: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _template_with_bytes(overrides, MAX_OVERRIDE_TEMPLATE_BYTES + 1)
    real_fstat = os.fstat

    class _ZeroSize:
        def __init__(self, st: os.stat_result) -> None:
            self._st = st

        def __getattr__(self, name: str) -> Any:
            return 0 if name == "st_size" else getattr(self._st, name)

    real_stat = os.stat
    monkeypatch.setattr(os, "fstat", lambda fd: _ZeroSize(real_fstat(fd)))
    monkeypatch.setattr(
        os, "stat", lambda p, *a, **kw: _ZeroSize(real_stat(p, *a, **kw))
    )
    env = make_environment(overrides)
    with pytest.raises(OverridesError, match="256 KiB"):
        env.get_template("_footer.html")


# ------------------------------------------ legacy IE filters (progid, expr)

_IE_FETCH = "AlphaImageLoader(src='https://evil.example/p.png')"


@pytest.mark.parametrize(
    "css",
    [
        f'a{{-ms-filter:"progid:DXImageTransform.Microsoft.{_IE_FETCH}"}}',
        f"a{{filter:progid:DXImageTransform.Microsoft.{_IE_FETCH}}}",
        f'a{{filter:"PROGID:DXImageTransform.Microsoft.{_IE_FETCH}"}}',
        f"a{{filter: ProgId : DXImageTransform.Microsoft.{_IE_FETCH}}}",
        f"a{{filter:prog/**/id:DXImageTransform.Microsoft.{_IE_FETCH}}}",
        f"a{{filter:progid/* c */:DXImageTransform.Microsoft.{_IE_FETCH}}}",
        f"a{{filter:progid\n:DXImageTransform.Microsoft.{_IE_FETCH}}}",
        f"a{{filter:DXImageTransform.Microsoft.{_IE_FETCH}}}",
        f"a{{filter:{_IE_FETCH}}}",
        "a{width:expression(alert(1))}",
        "a{width:EXPRESSION (alert(1))}",
        "a{width:expr/**/ession(alert(1))}",
    ],
)
def test_legacy_ie_filter_and_expression_are_refused(overrides: Path, css: str) -> None:
    with pytest.raises(OverridesError, match="refused"):
        _css(overrides, css)


@pytest.mark.parametrize(
    "css",
    [
        "a{filter:blur(2px)}",
        "a{filter:drop-shadow(0 0 2px black) grayscale(50%)}",
        "a{-webkit-filter:blur(2px);backdrop-filter:blur(4px)}",
        "a{filter:none}",
    ],
)
def test_modern_css_filters_are_still_accepted(overrides: Path, css: str) -> None:
    assert _css(overrides, css) == css
