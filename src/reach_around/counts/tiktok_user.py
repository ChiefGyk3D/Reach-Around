# SPDX-License-Identifier: Apache-2.0
"""TikTok follower total from a user token, with one optional refresh.

API facts (VERIFIED = read on the vendor page on 2026-11-09, no live call made;
INFERRED = not stated on a page this code could read):

* VERIFIED https://developers.tiktok.com/doc/tiktok-api-v2-get-user-info :
  ``GET https://open.tiktokapis.com/v2/user/info/`` with a required ``fields``
  query parameter and ``Authorization: Bearer <user token>``; ``follower_count``
  (int64) needs the scope ``user.info.stats``; the answer holds ``data.user``
  and an ``error`` object whose ``code`` is ``"ok"`` on success.
* VERIFIED https://developers.tiktok.com/doc/oauth-refresh-a-user-access-token :
  refresh is ``POST https://open.tiktokapis.com/v2/oauth/token/``, form encoded,
  with ``client_key``, ``client_secret``, ``grant_type=refresh_token`` and
  ``refresh_token``; the answer holds ``access_token``, ``expires_in``,
  ``refresh_token``, ``refresh_expires_in``, ``open_id``, ``scope`` and
  ``token_type``.
* VERIFIED https://developers.tiktok.com/doc/oauth-user-access-token-management :
  access token lives 2 hours, refresh token 180 days; every refresh returns a
  different refresh token and a replaced one stops working, so the new pair is
  always handed to the sink (and is the only usable pair afterwards).
* VERIFIED https://developers.tiktok.com/doc/tiktok-api-v2-error-handling :
  ``access_token_invalid`` is HTTP 401; ``scope_not_authorized`` is also HTTP
  401. The fetch layer keeps no body, so both read as ``http_401`` here: one
  refresh is tried, then the fixed word ``token_rejected``.
* INFERRED: that the 401 for a missing scope is indistinguishable from an
  expired token without reading the body (this code never keeps a body).

Rules: no secret is written to disk by this module; the rotated pair goes to a
``TokenSink`` whose ``store`` must return exactly ``True``. Errors and alerts
carry fixed words and variable NAMES, never a value, URL or response body. The
token travels only in the ``Authorization`` header. No redirect is followed.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, TextIO
from urllib.parse import urlencode, urlsplit

from reach_around.config.numbers_sources import (
    TIKTOK_ACCESS_ENV,
    TIKTOK_CLIENT_KEY_ENV,
    TIKTOK_CLIENT_SECRET_ENV,
    TIKTOK_REFRESH_ENV,
    TiktokSource,
)
from reach_around.net.fetch import FetchError

HOST = "open.tiktokapis.com"
INFO_URL = f"https://{HOST}/v2/user/info/?fields=follower_count"
TOKEN_URL = f"https://{HOST}/v2/oauth/token/"
MAX_TOKEN_LEN = 512
MAX_BODY = 65_536
FETCH_TIMEOUT = 10.0

Fetch = Callable[..., dict[str, object]]


class TiktokError(Exception):
    """Fixed-word failure; same ``kind`` contract as SourceError."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(kind)


@dataclass(frozen=True)
class TokenPair:
    """A token pair. ``repr`` never shows a value."""

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)


@dataclass(frozen=True)
class TokenNames:
    """The environment variable NAMES that hold the pair. Safe to print."""

    access: str
    refresh: str


@dataclass(frozen=True)
class HealthAlert:
    """Content-free token health alert (shape for issue 390).

    ``kind``: ``refresh_needed`` (token rejected and no refresh is possible),
    ``refresh_failed`` (TikTok refused the refresh; authorize again),
    ``rotated_unstored`` (a new pair exists only in memory),
    ``token_rejected`` (still refused after one refresh).
    """

    source: str
    kind: str
    names: tuple[str, ...]

    def line(self) -> str:
        return (
            f"token-health: source={self.source} kind={self.kind} "
            f"names={','.join(self.names)}"
        )


class TokenSink(Protocol):
    """Where a rotated pair goes.

    Future hook (NOT implemented): a sink that writes the pair to the secret
    manager. Agents never touch the secret store, so only the no-op sink ships.
    A sink must never log or print the values.
    """

    def store(self, pair: TokenPair, names: TokenNames, source: str) -> bool:
        """Persist the pair; return exactly ``True`` once it is kept.

        Any other result or any exception counts as not persisted and fails
        the collection.
        """
        ...

    def alert(self, alert: HealthAlert) -> None: ...


class NoopSink:
    """Prints the secret NAMES and what the owner must do; keeps no value."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream

    def _out(self) -> TextIO:
        return self._stream if self._stream is not None else sys.stderr

    def store(self, pair: TokenPair, names: TokenNames, source: str) -> bool:
        self.alert(
            HealthAlert(source, "rotated_unstored", (names.access, names.refresh))
        )
        print(
            f"  action needed: TikTok rotated the token pair for {source}. This "
            f"run did not store it. Store the new values under {names.access} "
            f"and {names.refresh} in the secret manager, or authorize again "
            "(docs/tiktok-source.md).",
            file=self._out(),
        )
        return False  # nothing was kept

    def alert(self, alert: HealthAlert) -> None:
        print(alert.line(), file=self._out())


def check_url(url: str) -> None:
    """Refuse anything but plain https to exactly the TikTok API host."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise TiktokError("bad_host") from None
    if (
        parts.scheme != "https"
        or parts.hostname != HOST
        or port is not None
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise TiktokError("bad_host")


def check_source(source: TiktokSource) -> None:
    """Use-time check of names and types, before any environment read.

    ``model_copy`` and ``model_construct`` skip validation, so the fixed names
    and the boolean are required again here. Fixed words, no value echoed.
    """
    if (
        source.access_token_env != TIKTOK_ACCESS_ENV
        or source.client_key_env != TIKTOK_CLIENT_KEY_ENV
        or source.client_secret_env != TIKTOK_CLIENT_SECRET_ENV
    ):
        raise TiktokError("bad_credential_name")
    if type(source.auto_refresh) is not bool:
        raise TiktokError("bad_auto_refresh")


def _usable(value: str) -> bool:
    return bool(value) and (
        len(value) <= MAX_TOKEN_LEN
        and value.isascii()
        and all(ch.isprintable() and not ch.isspace() for ch in value)
    )


def _token(value: object) -> str:
    if not isinstance(value, str) or not _usable(value):
        raise TiktokError("missing_field")
    return value


def token_configured(source: TiktokSource, env: Mapping[str, str]) -> bool:
    check_source(source)
    return bool(env.get(TIKTOK_ACCESS_ENV))


class _Session:
    def __init__(
        self,
        source: TiktokSource,
        env: Mapping[str, str],
        fetch: Fetch,
        sink: TokenSink | None,
    ) -> None:
        check_source(source)
        self.source = source
        self.fetch = fetch
        self.can_refresh = source.auto_refresh is True
        if self.can_refresh and (sink is None or isinstance(sink, NoopSink)):
            # The no-op sink discards the rotated pair, so a refresh would
            # break the next run (the old refresh token stops working).
            raise TiktokError("refresh_needs_sink")
        self.sink: TokenSink = sink if sink is not None else NoopSink()
        value = env.get(TIKTOK_ACCESS_ENV, "")
        if not value:
            raise TiktokError("missing_credential")
        if not _usable(value):
            raise TiktokError("invalid_credential")
        self.access = value
        # Only an enabled refresh reads the other three variables.
        self.refresh_value = env.get(TIKTOK_REFRESH_ENV, "") if self.can_refresh else ""
        self.client_key = env.get(TIKTOK_CLIENT_KEY_ENV, "") if self.can_refresh else ""
        self.client_secret = (
            env.get(TIKTOK_CLIENT_SECRET_ENV, "") if self.can_refresh else ""
        )
        self.refreshed = False

    def _alert(self, kind: str) -> None:
        # A sink is an untrusted boundary: its failure is dropped unformatted.
        try:
            self.sink.alert(
                HealthAlert(
                    self.source.platform, kind, (TIKTOK_ACCESS_ENV, TIKTOK_REFRESH_ENV)
                )
            )
        except Exception:  # noqa: S110
            pass

    def _call(self, url: str, **kw: object) -> dict[str, object]:
        """The only fetch site. Anything but a FetchError becomes a fixed word.

        The unexpected exception is dropped in the except block and the word is
        raised outside it, so no cause or context can carry a header or body.
        """
        failed = False
        try:
            return self.fetch(
                url,
                follow_redirects=False,
                max_bytes=MAX_BODY,
                timeout=FETCH_TIMEOUT,
                **kw,
            )
        except FetchError:
            raise
        except Exception:
            failed = True
        if failed:
            raise TiktokError("fetch_failed")
        raise TiktokError("fetch_failed")  # unreachable; keeps the checker calm

    def _refresh(self) -> None:
        if self.refreshed:
            self._alert("token_rejected")
            raise TiktokError("token_rejected")
        usable = (
            self.can_refresh
            and _usable(self.refresh_value)
            and _usable(self.client_key)
            and _usable(self.client_secret)
        )
        if not usable:
            # No refresh request is made: the owner refreshes by hand.
            self._alert("refresh_needed")
            raise TiktokError("token_expired")
        check_url(TOKEN_URL)
        form = urlencode(
            {
                "client_key": self.client_key,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_value,
            }
        ).encode("ascii")
        self.refreshed = True
        pair: TokenPair | None = None
        try:
            reply = self._call(
                TOKEN_URL,
                method="POST",
                body=form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            pair = TokenPair(
                _token(reply.get("access_token")), _token(reply.get("refresh_token"))
            )
        except (FetchError, TiktokError):
            pair = None
        if pair is None:
            self._alert("refresh_failed")
            raise TiktokError("refresh_failed")
        # Store first. Only a pair the sink confirmed (receipt exactly True)
        # replaces the one in use; the sink's exception is never kept.
        kept = False
        try:
            kept = (
                self.sink.store(
                    pair,
                    TokenNames(TIKTOK_ACCESS_ENV, TIKTOK_REFRESH_ENV),
                    self.source.platform,
                )
                is True
            )
        except Exception:
            kept = False
        if not kept:
            self._alert("rotated_unstored")
            raise TiktokError("refresh_store_failed")
        self.access = pair.access_token
        self.refresh_value = pair.refresh_token

    def get_info(self) -> dict[str, object]:
        check_url(INFO_URL)
        for _ in range(2):
            expired = False
            try:
                return self._call(
                    INFO_URL, headers={"Authorization": f"Bearer {self.access}"}
                )
            except FetchError as exc:
                if exc.kind != "http_401":
                    raise
                expired = True
            if expired:
                self._refresh()
        self._alert("token_rejected")
        raise TiktokError("token_rejected")


def follower_total(
    source: TiktokSource,
    env: Mapping[str, str],
    fetch: Fetch,
    sink: TokenSink | None,
) -> object:
    """The raw ``follower_count``; the caller validates it as a count."""
    session = _Session(source, env, fetch, sink)
    reply = session.get_info()
    err = reply.get("error")
    if isinstance(err, dict) and err.get("code") not in (None, "ok"):
        raise TiktokError("api_error")
    data = reply.get("data")
    user = data.get("user") if isinstance(data, dict) else None
    if not isinstance(user, dict) or "follower_count" not in user:
        raise TiktokError("missing_field")
    return user["follower_count"]
