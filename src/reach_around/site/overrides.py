# SPDX-License-Identifier: Apache-2.0
"""Instance overrides and the strict security headers.

A creator can put a directory next to the config with:

* ``templates/``: files here win over the packaged templates of the same name;
* ``custom.css``: appended after the theme as ``static/custom.css``.

Custom JavaScript is deliberately not supported. It would force the
Content-Security-Policy to allow inline or foreign script, which is the one
thing the policy exists to prevent. Any ``.js`` file in the overrides
directory is ignored with a warning.

This module also builds the ``_headers`` file that Cloudflare Pages reads.
``build_csp`` takes an optional per-directive extra so a later page (the gate,
which needs the Turnstile script) can relax the policy narrowly. An extra
source must be ``'self'`` or an exact https origin; wildcards, ``data:`` and
the ``unsafe-*`` keywords are refused.
"""

from __future__ import annotations

import errno
import logging
import os
import re
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jinja2 import (
    BaseLoader,
    ChoiceLoader,
    Environment,
    PackageLoader,
    StrictUndefined,
    TemplateNotFound,
)
from jinja2.sandbox import ImmutableSandboxedEnvironment

from reach_around.config.models import Block
from reach_around.site.markdown import render_markdown

LOG = logging.getLogger("reach_around.site.overrides")

MAX_CUSTOM_CSS_BYTES = 256 * 1024
_SCRIPT_SUFFIXES = frozenset({".js", ".mjs", ".cjs"})


class OverridesError(ValueError):
    """An override or a header setting is unsafe or unusable."""


# ---------------------------------------------------------------- templates


def _read_confined(path: Path, root: Path, limit: int | None = None) -> bytes:
    """Read a regular file without following a symlink at the last component.

    The file is opened with ``O_NOFOLLOW``, so a swap to a symlink between an
    earlier check and this open fails instead of reading the target. Reading
    goes through the descriptor, which ``fstat`` confirms is a regular file,
    and (where ``/proc`` exists) whose real location is inside ``root``.
    Swapping a parent directory for a symlink cannot be fully prevented
    without ``openat2``; the docs say the overrides directory must not be
    writable by untrusted users while a build runs.
    """
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise OverridesError(
                f"{path.name} is a symlink, which is not allowed in overrides"
            ) from exc
        raise OverridesError(f"cannot open {path.name}: {exc.strerror}") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OverridesError(f"{path.name} must be a regular file")
        proc = f"/proc/self/fd/{fd}"
        if os.path.exists(proc):
            actual = Path(os.path.realpath(proc))
            if root not in actual.parents:
                raise OverridesError(
                    f"{path.name} resolves outside the overrides directory"
                )
        chunks = []
        remaining = limit
        while True:
            want = 65536 if remaining is None else min(65536, remaining)
            if want == 0:
                break
            chunk = os.read(fd, want)
            if not chunk:
                break
            chunks.append(chunk)
            if remaining is not None:
                remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


class _ConfinedLoader(BaseLoader):
    """Load templates from one directory and never from outside it.

    A name is refused if it is absolute, contains a backslash, a NUL or a
    ``..`` segment, or if the file it names resolves (through symlinks)
    outside the directory.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(os.path.realpath(root))

    def get_source(
        self, environment: Environment, template: str
    ) -> tuple[str, str, Any]:
        if (
            not template
            or template.startswith("/")
            or "\\" in template
            or "\0" in template
            or ".." in template.split("/")
        ):
            raise OverridesError(f"template name {template!r} is not allowed")
        real = Path(os.path.realpath(self.root.joinpath(*template.split("/"))))
        if self.root != real and self.root not in real.parents:
            raise OverridesError(
                f"template {template!r} resolves outside the overrides directory"
            )
        if not real.is_file():
            raise TemplateNotFound(template)
        path = self.root.joinpath(*template.split("/"))
        try:
            source = _read_confined(path, self.root).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OverridesError(f"template {template!r} must be UTF-8") from exc
        mtime = real.stat().st_mtime_ns

        def uptodate() -> bool:
            try:
                return real.stat().st_mtime_ns == mtime
            except OSError:
                return False

        return source, str(real), uptodate


def check_overrides_dir(overrides: Path) -> Path:
    """Return the resolved overrides directory or raise a clear error."""
    if not overrides.exists() and not overrides.is_symlink():
        raise OverridesError(f"overrides directory {overrides} does not exist")
    if overrides.is_symlink():
        raise OverridesError(
            f"overrides directory {overrides} is a symlink, which is not allowed; "
            "point --overrides at the real directory"
        )
    if not overrides.is_dir():
        raise OverridesError(f"overrides path {overrides} is not a directory")
    return overrides.resolve()


def block_kind(block: Block) -> str:
    """Name the partial under ``templates/blocks/`` that renders ``block``."""
    return type(block).__name__.lower()


def make_environment(overrides: Path | None) -> Environment:
    """Build the template environment, looking in ``overrides/templates`` first."""
    loaders: list[BaseLoader] = []
    if overrides is not None:
        root = check_overrides_dir(overrides)
        templates = root / "templates"
        if templates.is_symlink():
            raise OverridesError(
                "overrides/templates is a symlink, which is not allowed"
            )
        loaders.append(_ConfinedLoader(templates))
    loaders.append(PackageLoader("reach_around.site", "templates"))
    # Templates are creator-supplied code. The immutable sandbox refuses
    # attribute walks such as ``cycler.__init__.__globals__`` that would
    # otherwise reach the interpreter.
    env = ImmutableSandboxedEnvironment(
        loader=ChoiceLoader(loaders),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.globals["block_kind"] = block_kind
    # render_markdown returns Markup that is already escaped, so autoescape
    # stays on in the template.
    env.filters["markdown"] = render_markdown
    return env


def warn_about_javascript(overrides: Path | None) -> list[Path]:
    """Warn about every script file in ``overrides``; none is ever loaded."""
    if overrides is None:
        return []
    root = check_overrides_dir(overrides)
    found = sorted(
        p.relative_to(root)
        for p in root.rglob("*")
        if p.suffix.lower() in _SCRIPT_SUFFIXES and p.is_file()
    )
    for rel in found:
        LOG.warning(
            "ignored %s: custom JavaScript is not supported (it would weaken "
            "the Content-Security-Policy)",
            rel,
        )
    return found


# ---------------------------------------------------------------------- CSS

_COMMENT = re.compile(r"/\*.*?(?:\*/|$)", re.DOTALL)
_ESCAPE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})[ \t\n\r\f]?|(.))", re.DOTALL)
_URL_OPEN = re.compile(r"url\s*\(", re.IGNORECASE)
_URL = re.compile(
    r"""url\s*\(\s*(?:"([^"]*)"|'([^']*)'|([^)"'\s]*))\s*\)""", re.IGNORECASE
)
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_FORBIDDEN: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern, re.IGNORECASE))
    for label, pattern in (
        ("@import", r"@\s*import"),
        ("expression()", r"expression\s*\("),
        ("behavior:", r"behavior\s*:"),
        ("-moz-binding", r"-moz-binding"),
        ("a javascript: string", r"javascript\s*:"),
        ("image-set()", r"image-set\s*\("),
        ("src()", r"(?<![\w-])src\s*\("),
    )
)


def _decode_escapes(text: str) -> str:
    def one(match: re.Match[str]) -> str:
        if match.group(1) is not None:
            code = int(match.group(1), 16)
            return chr(code) if 0 < code <= 0x10FFFF else "�"
        return match.group(2)

    return _ESCAPE.sub(one, text)


def _check_css(css: str) -> None:
    decoded = _decode_escapes(css)
    variants = (
        _COMMENT.sub(" ", decoded),
        _COMMENT.sub("", decoded),
    )
    for text in variants:
        for label, pattern in _FORBIDDEN:
            if pattern.search(text):
                raise OverridesError(f"custom.css is refused: it contains {label}")
        for opener in _URL_OPEN.finditer(text):
            match = _URL.match(text, opener.start())
            if match is None:
                raise OverridesError("custom.css is refused: malformed url(")
            target = next(g for g in match.groups() if g is not None).strip()
            if target.startswith(("//", "\\")):
                raise OverridesError(
                    "custom.css is refused: url() must be relative or https"
                )
            if _SCHEME.match(target) and not target.lower().startswith("https://"):
                raise OverridesError(
                    "custom.css is refused: url() must be relative or https"
                )


def load_custom_css(overrides: Path | None) -> str | None:
    """Return the validated ``custom.css`` text, or None when there is none."""
    if overrides is None:
        return None
    root = check_overrides_dir(overrides)
    path = root / "custom.css"
    if path.is_symlink():
        raise OverridesError("custom.css is a symlink, which is not allowed")
    if not path.exists():
        return None
    data = _read_confined(path, root, MAX_CUSTOM_CSS_BYTES + 1)
    if len(data) > MAX_CUSTOM_CSS_BYTES:
        raise OverridesError("custom.css is refused: it is larger than 256 KiB")
    try:
        css = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OverridesError("custom.css must be UTF-8 text") from exc
    _check_css(css)
    return css


# ------------------------------------------------------------------ headers

_BASE_CSP: dict[str, tuple[str, ...]] = {
    "default-src": ("'self'",),
    "script-src": ("'self'",),
    "style-src": ("'self'",),
    "img-src": ("'self'", "https:"),
    "font-src": ("'self'",),
    "connect-src": ("'self'",),
    "object-src": ("'none'",),
    "frame-ancestors": ("'none'",),
    "base-uri": ("'none'",),
    "form-action": ("'self'",),
}
# Directives a per-path extra may add sources to. The rest stay locked.
_EXTENDABLE = frozenset(
    {
        "script-src",
        "style-src",
        "img-src",
        "font-src",
        "connect-src",
        "frame-src",
        "form-action",
    }
)
_ORIGIN = re.compile(
    r"^https://[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::\d{1,5})?$"
)
_PATH = re.compile(r"^/[A-Za-z0-9._~/*:-]*$")

_OTHER_HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Strict-Transport-Security", "max-age=31536000; includeSubDomains"),
)


def _check_source(directive: str, source: str) -> None:
    if source != "'self'" and not _ORIGIN.match(source):
        raise OverridesError(
            f"CSP source {source!r} for {directive} is refused: use 'self' or an "
            "exact https origin (no wildcards, data:, nonces or unsafe-* keywords)"
        )


def build_csp(extra: Mapping[str, Sequence[str]] | None = None) -> str:
    """Return the Content-Security-Policy value, with optional narrow extras."""
    policy = {name: list(sources) for name, sources in _BASE_CSP.items()}
    for directive, sources in (extra or {}).items():
        if directive not in _EXTENDABLE:
            raise OverridesError(f"CSP directive {directive!r} cannot be extended")
        if isinstance(sources, str) or not sources:
            raise OverridesError(f"CSP extra for {directive} must be a list of sources")
        target = policy.setdefault(directive, [])
        for source in sources:
            _check_source(directive, source)
            if source not in target:
                target.append(source)
    return "; ".join(f"{name} {' '.join(srcs)}" for name, srcs in policy.items())


def render_headers(
    path_extras: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
) -> str:
    """Return the Cloudflare Pages ``_headers`` file.

    ``path_extras`` maps a path pattern to a CSP extra for that path only. A
    path block first detaches the site-wide policy (``! Header``) and then sets
    its own, because Pages otherwise sends both and the stricter one wins.
    """
    lines = [
        "/*",
        f"  Content-Security-Policy: {build_csp()}",
        *(f"  {name}: {value}" for name, value in _OTHER_HEADERS),
    ]
    for path, extra in (path_extras or {}).items():
        if path == "/*" or not _PATH.match(path):
            raise OverridesError(f"header path {path!r} is not allowed")
        lines += [
            "",
            path,
            "  ! Content-Security-Policy",
            f"  Content-Security-Policy: {build_csp(extra)}",
        ]
    return "\n".join(lines) + "\n"
