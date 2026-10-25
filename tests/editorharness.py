# SPDX-License-Identifier: Apache-2.0
"""Test helpers for the local editor: a running session and a raw HTTP client."""

from __future__ import annotations

import socket
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from reach_around.editor.server import EditorSession

DEFAULT_HOST = "<default host>"
CONFIG_TEXT = "title: Example\n# a comment that must survive\n"


@dataclass
class Response:
    status: int | None
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


def raw(port: int, data: bytes, *, timeout: float = 5.0) -> Response:
    """Send raw bytes, half-close, and parse whatever comes back."""
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        try:
            sock.sendall(data)
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        chunks: list[bytes] = []
        try:
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        except OSError:
            pass
    blob = b"".join(chunks)
    head, _, body = blob.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    parts = lines[0].split(b" ")
    if len(parts) < 2 or not parts[0].startswith(b"HTTP/") or not parts[1].isdigit():
        return Response(None, {}, blob)
    headers = {}
    for line in lines[1:]:
        key, _, value = line.partition(b":")
        headers[key.decode("latin-1").lower()] = value.strip().decode("latin-1")
    return Response(int(parts[1]), headers, body)


def request(
    port: int,
    method: str,
    path: str,
    headers: Mapping[str, str] | None = None,
    body: bytes = b"",
    *,
    host: str | None = DEFAULT_HOST,
    same_origin: bool = False,
) -> Response:
    """Build one request.

    ``host=DEFAULT_HOST`` means the correct Host; ``None`` omits the header.
    """
    lines = [f"{method} {path} HTTP/1.1"]
    if host == DEFAULT_HOST:
        host = f"127.0.0.1:{port}"
    if host is not None:
        lines.append(f"Host: {host}")
    merged = dict(headers or {})
    if same_origin:
        merged.setdefault("Origin", f"http://127.0.0.1:{port}")
        merged.setdefault("Sec-Fetch-Site", "same-origin")
    for key, value in merged.items():
        lines.append(f"{key}: {value}")
    if method == "POST" and not any(k.lower() == "content-length" for k in merged):
        lines.append(f"Content-Length: {len(body)}")
    data = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body
    return raw(port, data)


@contextmanager
def running_editor(
    tmp_path: Path, *, idle_seconds: float = 1800.0, watchdog_interval: float = 0.05
) -> Iterator[EditorSession]:
    config = tmp_path / "site.yaml"
    config.write_text(CONFIG_TEXT, encoding="utf-8")
    session = EditorSession(
        config, idle_seconds=idle_seconds, watchdog_interval=watchdog_interval
    )
    session.start()
    try:
        yield session
    finally:
        session.shutdown()


def tree_snapshot(root: Path) -> dict[str, bytes]:
    """Every file under ``root`` with its bytes: a refused request writes nothing."""
    return {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


_T = TypeVar("_T")


class SeededRandom:
    """A tiny deterministic generator (SplitMix64) for reproducible fuzz input."""

    def __init__(self, seed: int) -> None:
        self._state = seed & 0xFFFFFFFFFFFFFFFF

    def _next(self) -> int:
        self._state = (self._state + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
        z = self._state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
        return z ^ (z >> 31)

    def random(self) -> float:
        return self._next() / 2**64

    def randrange(self, start: int, stop: int | None = None) -> int:
        if stop is None:
            start, stop = 0, start
        return start + self._next() % (stop - start)

    def choice(self, items: Sequence[_T]) -> _T:
        return items[self.randrange(len(items))]
