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
import re
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

Fetch = Callable[..., dict[str, object]]


class SinkError(Exception):
    """A store failed. ``kind`` is a fixed word; no value is ever in it."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(kind)
        self.__suppress_context__ = True


class ScopeBusy(Exception):
    """Another run already owns this project and config."""


@contextmanager
def scope_lock(project: str, config: str) -> Iterator[None]:
    """Advisory single-writer lock for one project and config.

    The file lives under the XDG state directory, is named by a digest of the
    scope, and holds only this process id. It never holds a secret. The lock
    ends with the process, so a crash leaves nothing stale. It stops two runs
    of this tool; it cannot stop other writers (see docs/token-sink.md).
    """
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    folder = Path(base) / "reach-around"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = hashlib.sha256(f"{project}\0{config}".encode()).hexdigest()[:24]
    fd = os.open(
        folder / f"doppler-{name}.lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ScopeBusy from None
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
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
        if not _SCOPE_NAME.fullmatch(project) or not _SCOPE_NAME.fullmatch(config):
            raise SinkError("bad_input")
        if not token or not token.isprintable() or token != token.strip():
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
        if not 0 < len(values) <= MAX_VALUES:
            raise SinkError("bad_input")
        for name, value in values.items():
            if (
                not isinstance(name, str)
                or not _SECRET_NAME.fullmatch(name)
                or not isinstance(value, str)
                or not 0 < len(value) <= MAX_VALUE
                or not value.isprintable()
            ):
                raise SinkError("bad_input")
        body = json.dumps(
            {"project": self._project, "config": self._config, "secrets": dict(values)}
        ).encode("utf-8")
        try:
            # The answer echoes every value. It is dropped at once: never kept,
            # logged or returned. A POST is never replayed by the fetch helper.
            self._fetch(
                self._base + WRITE_PATH,
                method="POST",
                body=body,
                headers=self._headers(),
                follow_redirects=False,
            )
        except Exception:
            raise SinkError("store_failed") from None
        for name, value in values.items():
            self._verify(name, value)

    def _verify(self, name: str, expected: str) -> None:
        query = urlencode(
            {"project": self._project, "config": self._config, "name": name}
        )
        try:
            reply: dict[str, Any] = self._fetch(
                f"{self._base}{READ_PATH}?{query}",
                headers=self._headers(),
                follow_redirects=False,
            )
        except Exception:
            raise SinkError("verify_failed") from None
        raw = None
        entry = reply.get("value")
        if isinstance(entry, dict):
            raw = entry.get("raw")
        # Compare digests, then let the reply go. A restricted or masked value
        # has no raw text and cannot be verified, so it fails.
        if not isinstance(raw, str):
            raise SinkError("verify_failed")
        same = hmac.compare_digest(_digest(raw), _digest(expected))
        del raw, entry, reply
        if not same:
            raise SinkError("verify_mismatch")


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

    ``store`` returns nothing and raises on failure. The new expiry day lives in
    the config, not the secret manager, so the sink prints it as an action line
    (content-free: the secret NAME and a date).
    """

    def __init__(self, sink: SecretSink, stream: TextIO | None = None) -> None:
        self._sink = sink
        self._stream = stream

    def store(self, token: _Token, name: str, source: str, expires_on: date) -> None:
        try:
            self._sink.put({name: token.value})
        except SinkError as exc:
            _report(self._stream, f"secret-sink: source={source} kind={exc.kind}")
            raise
        except Exception:
            _report(self._stream, f"secret-sink: source={source} kind=store_failed")
            raise SinkError("store_failed") from None
        _report(
            self._stream,
            f"secret-sink: source={source} stored name={name}; set token_expires "
            f"to {expires_on.isoformat()} in the config",
        )

    def alert(self, alert: _Line) -> None:
        _report(self._stream, alert.line())
