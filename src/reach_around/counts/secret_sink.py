# SPDX-License-Identifier: Apache-2.0
"""A secret-manager sink for rotated collector tokens (issue 464).

``SecretSink.put`` stores values and returns only after a verified store: a
write, then a read back compared by SHA-256 digest in memory. It never prints,
logs, returns or raises a value, and never writes one to disk. Failure is a
``SinkError`` whose ``kind`` is a fixed word.

``DopplerSink`` talks to the Doppler REST API (https://api.doppler.com):
``POST /v3/configs/config/secrets`` to write and
``GET /v3/configs/config/secret`` to read one back (docs.doppler.com, read
2026-11-19). The write answer echoes every value; it is parsed by the fetch
helper and dropped here, never stored, logged or returned. Project, config and
secret names are injected, never hard-coded. The API token comes from the
caller (an environment variable or stdin, never argv).

Two failure modes the caller must know. The write is not replayed after an
ambiguous outcome (a timeout may have stored it), so any failure is reported as
failure even if the manager did keep the value. And a write that lands but fails
verification leaves the NEW value in the manager: the run keeps using the old
token it already holds, and the owner must check the secret (see the docs).
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import pwd
import re
import stat
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Protocol, TextIO
from urllib.parse import urlencode

API_BASE = "https://api.doppler.com"
WRITE_PATH = "/v3/configs/config/secrets"
READ_PATH = "/v3/configs/config/secret"

_SECRET_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,127}")
_SCOPE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
MAX_VALUE = 8192
MAX_VALUES = 8
# The request is UTF-8 JSON (no ASCII escaping), so the largest legal batch is
# eight values of 8192 four-byte characters plus a small envelope. The answer
# echoes every value and may escape it (up to 12 bytes per character), so its
# cap is sized for that and for the echo holding each value twice (raw and
# computed), well above the default fetch cap.
MAX_BODY = MAX_VALUES * MAX_VALUE * 4 + 4096
MAX_RESPONSE = 2_097_152

Fetch = Callable[..., dict[str, object]]


class SinkError(Exception):
    """A store failed. ``kind`` is a fixed word; no value is ever in it."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(kind)
        self.__suppress_context__ = True


class TokenHolder:
    """Holds one API token so no frame needs a plain string for it.

    The text is never in ``repr``, ``str``, a pickle or a copy, and there is no
    ``__dict__``. ``clear`` releases it. Code that must send the token takes it
    with ``get`` inside one expression and never names it in a local.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str = "") -> None:
        self._value = value

    def get(self) -> str:
        return self._value

    def clear(self) -> None:
        self._value = ""

    def strip(self) -> None:
        self._value = self._value.strip()

    def adopt(self, other: TokenHolder) -> None:
        """Move ``other``'s text here (stripped); ``other`` is cleared."""
        self._value = other._value.strip()
        other._value = ""

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return "TokenHolder(<hidden>)"

    __str__ = __repr__

    def __reduce__(self) -> tuple[Any, ...]:
        raise TypeError("a token holder cannot be copied or pickled")

    def __copy__(self) -> TokenHolder:
        raise TypeError("a token holder cannot be copied or pickled")

    def __deepcopy__(self, memo: object) -> TokenHolder:
        raise TypeError("a token holder cannot be copied or pickled")


class ScopeBusy(Exception):
    """Another run already owns this project and config."""


def _passwd_home() -> str:
    """The effective user's home from the passwd database, not the environment.

    No passwd entry, or an empty or relative path, is ``unsafe_state``.
    """
    try:
        home = pwd.getpwuid(os.geteuid()).pw_dir
    except KeyError:
        home = ""
    if not isinstance(home, str) or not home or not os.path.isabs(home):
        raise SinkError("unsafe_state")
    return home


def lock_dir() -> Path:
    """The one lock folder for every writer of this user."""
    return Path(_passwd_home()) / ".local" / "state" / "reach-around"


def lock_file(project: str, config: str) -> Path:
    name = hashlib.sha256(f"{project}\0{config}".encode()).hexdigest()[:24]
    return lock_dir() / f"doppler-{name}.lock"


def _open_state_dir(folder: Path) -> int:
    """Open the lock folder safely; ``SinkError('unsafe_state')`` if not ours."""
    try:
        folder.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.mkdir(folder, 0o700)
        except FileExistsError:
            pass
        fd = os.open(
            folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
    except OSError:
        raise SinkError("unsafe_state") from None
    info = os.fstat(fd)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        os.close(fd)
        raise SinkError("unsafe_state")
    return fd


@contextmanager
def scope_lock(project: str, config: str) -> Iterator[None]:
    """Advisory single-writer lock for one project and config.

    The folder comes from the passwd database, never from the environment, so
    every run of this user takes the same lock. The folder and file are checked
    (owner, mode, a plain file with one link) before anything is written. The
    file is named by a digest of the scope and holds only this process id. It
    never holds a secret. The lock ends with the process, so a crash leaves
    nothing stale. It stops two runs of this tool on this host and account; it
    cannot stop other writers (see docs/token-sink.md).
    """
    path = lock_file(project, config)
    dfd = _open_state_dir(path.parent)
    try:
        try:
            fd = os.open(
                path.name,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=dfd,
            )
        except OSError:
            raise SinkError("unsafe_state") from None
    finally:
        os.close(dfd)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise SinkError("unsafe_state")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ScopeBusy from None
        try:
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode())
        except OSError:
            raise SinkError("unsafe_state") from None
        yield
    finally:
        os.close(fd)


class SecretSink(Protocol):
    """Where a rotated credential goes."""

    def put(self, values: Mapping[str, str]) -> None:
        """Store ``values`` (secret name to value) and verify the store.

        Returns only once every value reads back identical. Raises
        ``SinkError`` otherwise.
        """
        ...


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


class DopplerSink:
    """Store, then read back and compare by digest. Holds the API token."""

    def __init__(
        self,
        project: str,
        config: str,
        token: str,
        fetch: Fetch,
    ) -> None:
        valid = bool(
            _SCOPE_NAME.fullmatch(project)
            and _SCOPE_NAME.fullmatch(config)
            and token
            and token.isprintable()
            and token == token.strip()
        )
        if not valid:
            token = ""  # no raw token stays in a frame the error can reach
            raise SinkError("bad_input")
        self._project = project
        self._config = config
        self._token = token
        self._fetch = fetch
        self._base = API_BASE

    def __repr__(self) -> str:
        return "DopplerSink(project=<set>, config=<set>, token=<hidden>)"

    __str__ = __repr__

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def put(self, values: Mapping[str, str]) -> None:
        # The work (and every value-bearing local) lives in ``_attempt``, which
        # returns only a fixed word. The error is raised here, outside any
        # handler, from a frame that holds no value, so nothing a traceback can
        # reach carries one.
        kind = ""
        try:
            kind = self._attempt(values)
        finally:
            values = {}  # on every exit, interrupts included
        if kind:
            raise SinkError(kind)

    def _attempt(self, values: Mapping[str, str]) -> str:
        """Store and verify; return ``""`` on success, else a failure word.

        Every local that holds a value is cleared on every exit, an interrupt
        included, so no frame a propagating exception reaches keeps one.
        """
        body = b""
        name = value = ""
        try:
            if not 0 < len(values) <= MAX_VALUES:
                return "bad_input"
            for name, value in values.items():
                if (
                    not isinstance(name, str)
                    or not _SECRET_NAME.fullmatch(name)
                    or not isinstance(value, str)
                    or not 0 < len(value) <= MAX_VALUE
                    or not value.isprintable()
                ):
                    return "bad_input"
            body = json.dumps(
                {
                    "project": self._project,
                    "config": self._config,
                    "secrets": dict(values),
                },
                ensure_ascii=False,
            ).encode("utf-8")
            if len(body) > MAX_BODY:
                return "bad_input"
            try:
                # The answer echoes every value. It is dropped at once: never
                # kept, logged or returned. A POST is never replayed.
                self._fetch(
                    self._base + WRITE_PATH,
                    method="POST",
                    body=body,
                    headers=self._headers(),
                    follow_redirects=False,
                    max_bytes=MAX_RESPONSE,
                )
            except Exception:
                return "store_failed"
            for name, value in values.items():
                kind = self._verify(name, value)
                if kind:
                    return kind
            return ""
        finally:
            values = {}
            body = b""
            name = value = ""

    def _verify(self, name: str, expected: str) -> str:
        query = urlencode(
            {"project": self._project, "config": self._config, "name": name}
        )
        reply: dict[str, Any] = {}
        entry: Any = None
        raw: Any = None
        try:
            try:
                reply = self._fetch(
                    f"{self._base}{READ_PATH}?{query}",
                    headers=self._headers(),
                    follow_redirects=False,
                )
            except Exception:
                return "verify_failed"
            entry = reply.get("value")
            if isinstance(entry, dict):
                raw = entry.get("raw")
            # Compare digests, then let the reply go. A restricted or masked
            # value has no raw text and cannot be verified, so it fails.
            if not isinstance(raw, str):
                return "verify_failed"
            same = hmac.compare_digest(_digest(raw), _digest(expected))
            return "" if same else "verify_mismatch"
        finally:
            raw = entry = None
            reply = {}
            expected = ""


class _Pair(Protocol):
    @property
    def access_token(self) -> str: ...

    @property
    def refresh_token(self) -> str: ...


class _Names(Protocol):
    @property
    def access(self) -> str: ...

    @property
    def refresh(self) -> str: ...


class _Line(Protocol):
    def line(self) -> str: ...


def _report(stream: TextIO | None, text: str) -> None:
    print(text, file=stream if stream is not None else sys.stderr)


class PairStyleSink:
    """The Twitch and TikTok ``TokenSink`` contract on top of a ``SecretSink``.

    ``store`` returns exactly ``True`` after a verified store, ``False`` on any
    failure (the source then prints its ``rotated_unstored`` health line and
    fails the source, and the old token stays in use).
    """

    def __init__(self, sink: SecretSink, stream: TextIO | None = None) -> None:
        self._sink = sink
        self._stream = stream

    def store(self, pair: _Pair, names: _Names, source: str) -> bool:
        try:
            self._sink.put(
                {names.access: pair.access_token, names.refresh: pair.refresh_token}
            )
        except SinkError as exc:
            _report(self._stream, f"secret-sink: source={source} kind={exc.kind}")
            return False
        except Exception:
            _report(self._stream, f"secret-sink: source={source} kind=store_failed")
            return False
        return True

    def alert(self, alert: _Line) -> None:
        _report(self._stream, alert.line())


class _Token(Protocol):
    @property
    def value(self) -> str: ...


class MetaStyleSink:
    """The Instagram and Threads ``MetaTokenSink`` contract.

    ``store`` returns nothing and raises ``SinkError`` on failure. The Meta
    session catches that error, prints its ``refresh_store_failed`` health line,
    drops the new token and goes on to read the count with the old one, which a
    Meta refresh does not retire. So a failed store does NOT fail an Instagram
    or Threads source; only Twitch and TikTok do (their old token is retired).
    The new expiry day lives in the config, not the secret manager, so the sink
    prints it as an action line (content-free: the secret NAME and a date).
    """

    def __init__(self, sink: SecretSink, stream: TextIO | None = None) -> None:
        self._sink = sink
        self._stream = stream

    def store(self, token: _Token, name: str, source: str, expires_on: date) -> bool:
        kind = ""
        try:
            self._sink.put({name: token.value})
        except SinkError as exc:
            kind = exc.kind
        except Exception:
            kind = "store_failed"
        if kind:
            # Raised outside the handler: no chained error, no value in a frame.
            _report(self._stream, f"secret-sink: source={source} kind={kind}")
            raise SinkError(kind)
        _report(
            self._stream,
            f"secret-sink: source={source} stored name={name}; set token_expires "
            f"to {expires_on.isoformat()} in the config",
        )
        return True

    def alert(self, alert: _Line) -> None:
        _report(self._stream, alert.line())
