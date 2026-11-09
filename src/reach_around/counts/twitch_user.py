# SPDX-License-Identifier: Apache-2.0
"""Twitch followers total from a broadcaster user token, with one refresh.

API facts (VERIFIED = read on the vendor page on 2026-11-09; INFERRED = not on
a page this code could read):

* VERIFIED https://dev.twitch.tv/docs/api/reference/#get-channel-followers:
  ``GET https://api.twitch.tv/helix/channels/followers`` "Requires a user
  access token that includes the moderator:read:followers scope"; query
  ``broadcaster_id`` is required, ``first`` is 1 to 100; the response field
  ``total`` is "The total number of users that follow this broadcaster"; a 401
  means the token lacks the scope or is not valid. The page lists no
  ``moderator_id`` parameter and no 403 row.
* VERIFIED https://dev.twitch.tv/docs/authentication/refresh-tokens/: refresh is
  a POST to ``https://id.twitch.tv/oauth2/token``, form encoded, with
  ``client_id``, ``client_secret``, ``grant_type=refresh_token`` and
  ``refresh_token``; the answer holds ``access_token``, ``expires_in``,
  ``refresh_token``, ``scope`` and ``token_type``. The page says the refresh
  token "may change" (it does NOT say single use), so the new one is always
  handed to the sink. A 400 "Invalid refresh token" or a 401 means the owner must
  authorize again. Twitch recommends refreshing on a 401, not ahead of time.
* INFERRED (page not readable): the Retry-After header on a 429 (the rate-limit
  page answered 403 to the reader); the generic fetch layer bounds the wait.

Rules: no secret is written to disk by this module; the rotated pair goes to a
``TokenSink``. The shipped sink prints only environment variable NAMES and an
instruction. Errors and alerts carry fixed words and names, never a value.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, TextIO
from urllib.parse import urlencode, urlsplit

from reach_around.config.numbers_sources import (
    CLIENT_ID_ENV,
    CLIENT_SECRET_ENV,
    REFRESH_TOKEN_ENV,
    USER_TOKEN_ENV,
    TwitchSource,
)
from reach_around.net.fetch import FetchError

API_HOST = "api.twitch.tv"
ID_HOST = "id.twitch.tv"
TOKEN_URL = f"https://{ID_HOST}/oauth2/token"
USERS_URL = f"https://{API_HOST}/helix/users"
FOLLOWERS_URL = f"https://{API_HOST}/helix/channels/followers"
_HOSTS = frozenset({API_HOST, ID_HOST})
MAX_TOKEN_LEN = 512

Fetch = Callable[..., dict[str, object]]


class TwitchUserError(Exception):
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

    ``kind`` is one of ``refresh_needed`` (token rejected, no refresh token
    configured), ``refresh_failed`` (Twitch refused the refresh; authorize
    again), ``rotated_unstored`` (a new pair exists only in memory).
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
    manager. The owner decided agents never touch the secret store, so only the
    no-op sink ships. A sink must never log or print the values.
    """

    def store(self, pair: TokenPair, names: TokenNames, source: str) -> None: ...

    def alert(self, alert: HealthAlert) -> None: ...


class NoopSink:
    """Prints the secret NAMES and what the owner must do; keeps no value."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream

    def _out(self) -> TextIO:
        return self._stream if self._stream is not None else sys.stderr

    def store(self, pair: TokenPair, names: TokenNames, source: str) -> None:
        self.alert(
            HealthAlert(source, "rotated_unstored", (names.access, names.refresh))
        )
        print(
            f"  action needed: Twitch rotated the token pair for {source}. This run "
            f"did not store it. Store the new values under {names.access} and "
            f"{names.refresh} in the secret manager before the next run, or re-run "
            "the one-time authorization (docs/twitch-source.md).",
            file=self._out(),
        )

    def alert(self, alert: HealthAlert) -> None:
        print(alert.line(), file=self._out())


def _check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in _HOSTS or parts.port:
        raise TwitchUserError("bad_host")


def _token(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_TOKEN_LEN
        or any(not ch.isprintable() or ch.isspace() for ch in value)
    ):
        raise TwitchUserError("missing_field")
    return value


def check_names(source: TwitchSource) -> None:
    """The credential names must be the canonical ones (defense in depth: the
    config model already enforces it; this holds if a source was built another
    way). Raises a fixed word and never echoes a name."""
    if (
        source.client_id_env != CLIENT_ID_ENV
        or source.client_secret_env != CLIENT_SECRET_ENV
    ):
        raise TwitchUserError("bad_env_name")


def _env_secret(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value:
        raise TwitchUserError("missing_credential")
    if len(value) > MAX_TOKEN_LEN or any(
        not ch.isprintable() or ch.isspace() for ch in value
    ):
        raise TwitchUserError("invalid_credential")
    return value


def user_token_configured(source: TwitchSource, env: Mapping[str, str]) -> bool:
    check_names(source)
    return bool(env.get(USER_TOKEN_ENV))


class _Session:
    def __init__(
        self,
        source: TwitchSource,
        env: Mapping[str, str],
        fetch: Fetch,
        sink: TokenSink | None,
    ) -> None:
        check_names(source)
        self.source = source
        self.fetch = fetch
        # Refresh rotates the token pair. With no sink that stores it (the
        # shipped no-op sink discards it), a refresh would break the next run.
        self.can_refresh = source.auto_refresh
        if self.can_refresh and (sink is None or isinstance(sink, NoopSink)):
            raise TwitchUserError("refresh_needs_sink")
        self.sink: TokenSink = sink if sink is not None else NoopSink()
        self.client_id = _env_secret(env, CLIENT_ID_ENV)
        self.client_secret = (
            _env_secret(env, CLIENT_SECRET_ENV) if self.can_refresh else ""
        )
        self.access = _env_secret(env, USER_TOKEN_ENV)
        self.refresh_value = env.get(REFRESH_TOKEN_ENV, "") if self.can_refresh else ""
        self.refreshed = False

    def _alert(self, kind: str) -> None:
        self.sink.alert(
            HealthAlert(self.source.platform, kind, (USER_TOKEN_ENV, REFRESH_TOKEN_ENV))
        )

    def _refresh(self) -> None:
        if self.refreshed:
            raise TwitchUserError("token_rejected")
        if not self.can_refresh or not self.refresh_value:
            # No refresh request is made: the owner refreshes by hand.
            self._alert("refresh_needed")
            raise TwitchUserError("token_expired")
        _check_url(TOKEN_URL)
        form = urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_value,
            }
        ).encode("ascii")
        self.refreshed = True
        try:
            reply = self.fetch(
                TOKEN_URL,
                method="POST",
                body=form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                follow_redirects=False,
            )
            pair = TokenPair(
                _token(reply.get("access_token")), _token(reply.get("refresh_token"))
            )
        except (FetchError, TwitchUserError):
            self._alert("refresh_failed")
            raise TwitchUserError("refresh_failed") from None
        self.access = pair.access_token
        self.refresh_value = pair.refresh_token
        try:
            self.sink.store(
                pair,
                TokenNames(USER_TOKEN_ENV, REFRESH_TOKEN_ENV),
                self.source.platform,
            )
        except Exception:  # a sink failure must not lose this run's number
            self._alert("rotated_unstored")

    def get(self, url: str) -> dict[str, object]:
        _check_url(url)
        for _ in range(2):
            try:
                return self.fetch(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.access}",
                        "Client-Id": self.client_id,
                    },
                    follow_redirects=False,
                )
            except FetchError as exc:
                if exc.kind != "http_401":
                    raise
                self._refresh()
        raise TwitchUserError("token_rejected")


def followers_with_user_token(
    source: TwitchSource,
    env: Mapping[str, str],
    fetch: Fetch,
    sink: TokenSink | None,
) -> object:
    """The raw ``total`` value; the caller validates it as a count."""
    session = _Session(source, env, fetch, sink)
    user = session.get(USERS_URL + "?" + urlencode({"login": source.login}))
    data = user.get("data")
    first = data[0] if isinstance(data, list) and data else None
    user_id = first.get("id") if isinstance(first, dict) else None
    if not isinstance(user_id, str) or not (user_id.isascii() and user_id.isdigit()):
        raise TwitchUserError("missing_field")
    reply = session.get(
        FOLLOWERS_URL + "?" + urlencode({"broadcaster_id": user_id, "first": "1"})
    )
    if "total" not in reply:
        raise TwitchUserError("missing_field")
    return reply["total"]
