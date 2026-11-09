# SPDX-License-Identifier: Apache-2.0
"""Instagram and Threads follower counts from a long-lived user token.

API facts (VERIFIED = read on the vendor page on 2026-11-09, no live call made;
INFERRED = not stated on a page this code could read):

* VERIFIED https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started
  : ``GET https://graph.instagram.com/v27.0/me`` takes ``fields`` and an
  ``access_token`` query parameter; ``followers_count`` is a field of the app
  user's professional account ("number of followers of the app user's Instagram
  professional account"). The page shows no Authorization header and does not
  list the permission for this field (the research note names
  ``instagram_business_basic``: INFERRED for this field).
* VERIFIED https://developers.facebook.com/docs/threads/insights : ``GET
  https://graph.threads.com/v1.0/<THREADS_USER_ID>/threads_insights`` with a
  required ``metric``; ``followers_count`` is a total-value metric, so the answer
  holds ``total_value.value``; it takes no ``since`` or ``until``. The example
  sends the token as the ``access_token`` field. INFERRED: the path segment
  ``me`` in place of the user id (not shown on the page); set ``user_id`` to
  avoid relying on it.
* VERIFIED https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login
  : refresh is ``GET https://graph.instagram.com/refresh_access_token`` with
  ``grant_type=ig_refresh_token`` and ``access_token``; the long-lived token must
  be at least 24 hours old, unexpired, and ``instagram_business_basic`` granted;
  the answer holds ``access_token``, ``token_type`` and ``expires_in`` (seconds);
  an unrefreshed token expires after 60 days and can no longer be refreshed. The
  page documents no error body for refresh.
* VERIFIED https://developers.facebook.com/documentation/threads/get-started/long-lived-tokens
  : refresh is ``GET https://graph.threads.com/refresh_access_token`` with
  ``grant_type=th_refresh_token`` and ``access_token``; same 24 hour and 60 day
  rules; the answer holds ``access_token``, ``token_type``, ``expires_in``. The
  page uses only ``graph.threads.com`` (never ``graph.threads.net``).
* INFERRED (not stated on any page read): that an ``Authorization`` header is
  accepted in place of the query parameter. Both vendor pages show the token in
  the query string only, so this module does the same and relies on the fetch
  layer, whose errors carry a fixed word and never a URL, header or body.
* VERIFIED https://developers.facebook.com/docs/graph-api/overview/rate-limiting
  : calls in 24 hours = 4800 x impressions (Threads floors impressions at 10);
  throttling is signaled by error codes (for example 4, 17, 613, 80002) and the
  page mentions no Retry-After header. One run makes one or two calls. The fetch
  layer honors a Retry-After on a 429 up to its cap if one is ever sent.
* INFERRED: the answer's content type. Graph hosts have been seen to answer
  ``text/javascript`` for JSON, so that type is accepted for these two hosts only.

Rules: no secret is written to disk here. A refreshed token goes to a
``MetaTokenSink``; the shipped sink prints only the secret NAME, the new expiry
day (not a secret) and an instruction. No redirect is followed. Errors and alerts
carry fixed words and names, never a token, URL or response body.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Protocol, TextIO
from urllib.parse import urlencode, urlsplit

from reach_around.config.numbers_sources import InstagramSource, ThreadsSource
from reach_around.net.fetch import FetchError

INSTAGRAM_HOST = "graph.instagram.com"
THREADS_HOST = "graph.threads.com"
INSTAGRAM_VERSION = "v27.0"
THREADS_VERSION = "v1.0"
MAX_TOKEN_LEN = 512
MAX_BODY = 65_536
FETCH_TIMEOUT = 10.0
MAX_EXPIRES_IN = 400 * 86_400
EXTRA_JSON_TYPES = frozenset({"text/javascript"})

Fetch = Callable[..., dict[str, object]]
MetaSource = InstagramSource | ThreadsSource


class MetaError(Exception):
    """Fixed-word failure; same ``kind`` contract as SourceError."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(kind)


@dataclass(frozen=True)
class NewToken:
    """A refreshed token. ``repr`` never shows the value."""

    value: str = field(repr=False)


@dataclass(frozen=True)
class HealthAlert:
    """Content-free token health alert (the shape issue 390 consumes).

    ``kind``: ``refresh_needed`` (inside the refresh window; refresh by hand,
    ``days_left`` says how long is left), ``refresh_refused`` (too young,
    expired, revoked or any refusal),
    ``token_expired`` (the recorded expiry day has passed), ``token_rejected``
    (the platform refused the token), ``rotated_unstored`` (a new token exists
    only in memory).
    """

    source: str
    kind: str
    names: tuple[str, ...]
    days_left: int | None = None

    def line(self) -> str:
        text = (
            f"token-health: source={self.source} kind={self.kind} "
            f"names={','.join(self.names)}"
        )
        if self.days_left is not None:
            text += f" days_left={self.days_left}"
        return text


class MetaTokenSink(Protocol):
    """Where a refreshed token goes.

    Future hook (NOT implemented): a sink that writes the token to the secret
    manager. Agents and this code never touch the secret store, so only the
    no-op sink ships. A sink must never log or print the value.
    """

    def store(
        self, token: NewToken, name: str, source: str, expires_on: date
    ) -> None: ...

    def alert(self, alert: HealthAlert) -> None: ...


class NoopSink:
    """Prints the secret NAME and what the owner must do; keeps no value."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream

    def _out(self) -> TextIO:
        return self._stream if self._stream is not None else sys.stderr

    def store(self, token: NewToken, name: str, source: str, expires_on: date) -> None:
        self.alert(HealthAlert(source, "rotated_unstored", (name,)))
        print(
            f"  action needed: the token for {source} was refreshed. This run did "
            f"not store it. Store the new value under {name} in the secret manager "
            f"and set token_expires to {expires_on.isoformat()} in the config "
            "(docs/instagram-threads-sources.md).",
            file=self._out(),
        )

    def alert(self, alert: HealthAlert) -> None:
        print(alert.line(), file=self._out())


def check_url(url: str, host: str) -> None:
    """Refuse anything but plain https to exactly ``host``."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise MetaError("bad_host") from None
    if (
        parts.scheme != "https"
        or parts.hostname != host
        or port is not None
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise MetaError("bad_host")


_CREDENTIAL_NAMES = {
    "instagram": "INSTAGRAM_ACCESS_TOKEN",  # noqa: S105
    "threads": "THREADS_ACCESS_TOKEN",  # noqa: S105
}


def credential_name(source: MetaSource) -> str:
    """The one variable NAME this source may read, fixed by its kind.

    Nothing in the config can change it. Anything else is refused with the fixed
    word ``bad_credential_name`` before the environment is read.
    """
    name = _CREDENTIAL_NAMES.get(source.kind)
    if name is None or source.access_token_env != name:
        raise MetaError("bad_credential_name")
    return name


def token_configured(source: MetaSource, env: Mapping[str, str]) -> bool:
    return bool(env.get(credential_name(source)))


def _token_value(value: object, kind: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_TOKEN_LEN
        or not value.isascii()
        or any(not ch.isprintable() or ch.isspace() for ch in value)
    ):
        raise MetaError(kind)
    return value


def _host(source: MetaSource) -> str:
    return INSTAGRAM_HOST if isinstance(source, InstagramSource) else THREADS_HOST


class _Session:
    def __init__(
        self,
        source: MetaSource,
        env: Mapping[str, str],
        fetch: Fetch,
        sink: MetaTokenSink,
        today: date,
    ) -> None:
        self.source = source
        self.fetch = fetch
        self.sink = sink
        self.today = today
        self.host = _host(source)
        name = credential_name(source)
        self.token = _token_value(
            env.get(name, ""),
            "missing_credential" if not env.get(name) else "invalid_credential",
        )

    def _alert(self, kind: str, days_left: int | None = None) -> None:
        self.sink.alert(
            HealthAlert(
                self.source.platform,
                kind,
                (self.source.access_token_env,),
                days_left,
            )
        )

    def _get(self, path: str, params: Mapping[str, str]) -> dict[str, object]:
        url = f"https://{self.host}{path}?" + urlencode(
            {**params, "access_token": self.token}
        )
        check_url(url, self.host)
        return self.fetch(
            url,
            follow_redirects=False,
            max_bytes=MAX_BODY,
            timeout=FETCH_TIMEOUT,
            extra_json_types=EXTRA_JSON_TYPES,
        )

    def maybe_refresh(self) -> None:
        s = self.source
        if s.token_expires is None:
            return
        days_left = (s.token_expires - self.today).days
        if days_left < 0:
            self._alert("token_expired")
            raise MetaError("token_expired")
        if days_left > s.refresh_within_days:
            return
        if not s.auto_refresh:
            # No sink can store a new token, so asking for one would lose it and
            # invalidate nothing but the owner's time. Report; the owner refreshes
            # by hand (docs/instagram-threads-sources.md).
            self._alert("refresh_needed", days_left)
            return
        grant = (
            "ig_refresh_token" if isinstance(s, InstagramSource) else "th_refresh_token"
        )
        try:
            reply = self._get(
                "/refresh_access_token", {"grant_type": grant}
            )  # once per run, never retried here
            new = _token_value(reply.get("access_token"), "missing_field")
            seconds = reply.get("expires_in")
            if type(seconds) is not int or not 0 < seconds <= MAX_EXPIRES_IN:
                raise MetaError("missing_field")
        except (FetchError, MetaError):
            # Too young (under 24 hours), expired, revoked, throttled: the old
            # token may still work today, so go on and read the count.
            self._alert("refresh_refused")
            return
        self.token = new
        expires_on = self.today + timedelta(seconds=seconds)
        try:
            self.sink.store(NewToken(new), s.access_token_env, s.platform, expires_on)
        except Exception:  # a sink failure must not lose this run's number
            self._alert("rotated_unstored")

    def read(self, path: str, params: Mapping[str, str]) -> dict[str, object]:
        try:
            return self._get(path, params)
        except FetchError as exc:
            if exc.kind in ("http_400", "http_401"):
                self._alert("token_rejected")
                raise MetaError("token_rejected") from None
            raise


def follower_total(
    source: MetaSource,
    env: Mapping[str, str],
    fetch: Fetch,
    sink: MetaTokenSink | None,
    today: date,
) -> object:
    """The raw followers value; the caller validates it as a count."""
    credential_name(source)
    if source.auto_refresh and sink is None:
        # Automatic refresh only with a sink the caller supplies; the default
        # NoopSink cannot keep a token, so this setting is refused without one.
        raise MetaError("refresh_sink_required")
    session = _Session(
        source, env, fetch, sink if sink is not None else NoopSink(), today
    )
    session.maybe_refresh()
    if isinstance(source, InstagramSource):
        reply = session.read(f"/{INSTAGRAM_VERSION}/me", {"fields": "followers_count"})
        if "followers_count" not in reply:
            raise MetaError("missing_field")
        return reply["followers_count"]
    user = source.user_id or "me"
    reply = session.read(
        f"/{THREADS_VERSION}/{user}/threads_insights", {"metric": "followers_count"}
    )
    data = reply.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("name") == "followers_count":
                total = item.get("total_value")
                if isinstance(total, dict) and "value" in total:
                    return total["value"]
    raise MetaError("missing_field")
