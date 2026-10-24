# SPDX-License-Identifier: Apache-2.0
"""One function per platform kind: fetch, parse, return a validated count.

Fixed providers use fixed hosts. Configurable hosts (Mastodon, Matrix) were
validated as public names when the config loaded, and the transport checks
every resolved address again at connect time.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import quote, urlencode, urlsplit

from reach_around.config.numbers_sources import (
    BlueskySource,
    DiscordSource,
    GithubSource,
    MastodonSource,
    MatrixSource,
    TwitchSource,
    YoutubeSource,
)
from reach_around.net.egress import EgressBlocked, validate_hostname
from reach_around.net.fetch import FetchError
from reach_around.numbers import NumbersError, check_count

# kind -> status word when a fetch is intentionally skipped (exit code 0)
SKIP_USER_AUTH = "needs_user_token"
SKIP_HIDDEN = "hidden_subscriber_count"

Fetch = Callable[..., dict[str, object]]


class SourceSkip(Exception):
    """The source cannot give a number by design; not a failure."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class SourceError(Exception):
    """The source answered, but not with a usable count."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(kind)


@dataclass(frozen=True)
class Context:
    fetch: Fetch
    env: Mapping[str, str]


def _count(payload: Mapping[str, object], field: str) -> int:
    if field not in payload:
        raise SourceError("missing_field")
    try:
        return check_count(payload[field])
    except NumbersError:
        raise SourceError("bad_count") from None


def _digit_count(value: object) -> int:
    """A count the API sends as a string of digits (YouTube) or as an int."""
    if (
        isinstance(value, str)
        and value.isascii()
        and value.isdigit()
        and len(value) <= 11
    ):
        value = int(value)
    try:
        return check_count(value)
    except NumbersError:
        raise SourceError("bad_count") from None


def _secret(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value:
        raise SourceError("missing_credential")
    if len(value) > 512 or any(not ch.isprintable() or ch.isspace() for ch in value):
        raise SourceError("invalid_credential")
    return value


def _object(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise SourceError("missing_field")
    return value


def _first(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = payload.get(key)
    if not isinstance(value, list) or not value or not isinstance(value[0], dict):
        raise SourceError("missing_field")
    return value[0]


def bluesky(source: BlueskySource, ctx: Context) -> int:
    url = "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor=" + quote(
        source.actor, safe=""
    )
    return _count(ctx.fetch(url), "followersCount")


def mastodon(source: MastodonSource, ctx: Context) -> int:
    url = f"https://{source.host}/api/v1/accounts/lookup?acct=" + quote(
        source.acct, safe=""
    )
    return _count(ctx.fetch(url), "followers_count")


def github(source: GithubSource, ctx: Context) -> int:
    url = "https://api.github.com/users/" + quote(source.user, safe="")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    return _count(ctx.fetch(url, headers=headers), "followers")


def discord(source: DiscordSource, ctx: Context) -> int:
    url = (
        "https://discord.com/api/v10/invites/"
        + quote(source.invite, safe="")
        + "?with_counts=true"
    )
    return _count(ctx.fetch(url), "approximate_member_count")


def _homeserver(server: str, ctx: Context) -> str:
    """The client API host for ``server``, following .well-known if published."""
    try:
        well_known = ctx.fetch(f"https://{server}/.well-known/matrix/client")
    except FetchError as exc:
        if exc.kind == "http_404":
            return server
        raise
    base = _object(well_known, "m.homeserver").get("base_url")
    if not isinstance(base, str):
        raise SourceError("bad_well_known")
    try:
        parts = urlsplit(base)
        if (
            parts.scheme != "https"
            or parts.port not in (None, 443)
            or parts.hostname is None
            or parts.username is not None
            or parts.query
            or parts.fragment
            or parts.path not in ("", "/")
        ):
            raise SourceError("bad_well_known")
        return validate_hostname(parts.hostname)
    except (ValueError, EgressBlocked):
        raise SourceError("bad_well_known") from None


def matrix(source: MatrixSource, ctx: Context) -> int:
    alias = source.room
    server = alias.split(":", 1)[1].lower()
    host = _homeserver(server, ctx)
    resolved = ctx.fetch(
        f"https://{host}/_matrix/client/v3/directory/room/" + quote(alias, safe="")
    )
    room_id = resolved.get("room_id")
    if (
        not isinstance(room_id, str)
        or not room_id.startswith("!")
        or len(room_id) > 255
    ):
        raise SourceError("missing_field")
    try:
        summary = ctx.fetch(
            f"https://{host}/_matrix/client/v1/room_summary/"
            + quote(room_id, safe="")
            + "?"
            + urlencode({"via": server})
        )
    except FetchError as exc:
        if exc.kind in ("http_401", "http_403", "http_404"):
            raise SourceError("room_not_public") from None
        raise
    return _count(summary, "num_joined_members")


def youtube(source: YoutubeSource, ctx: Context) -> int:
    key = _secret(ctx.env, source.api_key_env)
    url = "https://www.googleapis.com/youtube/v3/channels?" + urlencode(
        {"part": "statistics", "id": source.channel_id}
    )
    # the key travels in a header, never in the URL
    payload = ctx.fetch(url, headers={"x-goog-api-key": key})
    stats = _object(_first(payload, "items"), "statistics")
    if stats.get("hiddenSubscriberCount") is True:
        raise SourceSkip(SKIP_HIDDEN)
    if "subscriberCount" not in stats:
        raise SourceError("missing_field")
    return _digit_count(stats["subscriberCount"])


def twitch(source: TwitchSource, ctx: Context) -> int:
    client_id = _secret(ctx.env, source.client_id_env)
    client_secret = _secret(ctx.env, source.client_secret_env)
    form = urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        }
    ).encode("ascii")
    token_reply = ctx.fetch(
        "https://id.twitch.tv/oauth2/token",
        method="POST",
        body=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    token = token_reply.get("access_token")
    if not isinstance(token, str) or not token or len(token) > 512:
        raise SourceError("missing_field")
    headers = {"Authorization": f"Bearer {token}", "Client-Id": client_id}
    user = ctx.fetch(
        "https://api.twitch.tv/helix/users?" + urlencode({"login": source.login}),
        headers=headers,
    )
    user_id = _first(user, "data").get("id")
    if not isinstance(user_id, str) or not (user_id.isascii() and user_id.isdigit()):
        raise SourceError("missing_field")
    try:
        followers = ctx.fetch(
            "https://api.twitch.tv/helix/channels/followers?"
            + urlencode({"broadcaster_id": user_id, "first": "1"}),
            headers=headers,
        )
    except FetchError as exc:
        # Twitch has asked for a user token (moderator:read:followers) on this
        # route; app credentials cannot get one. Say so; do not guess another route.
        if exc.kind in ("http_401", "http_403"):
            raise SourceSkip(SKIP_USER_AUTH) from None
        raise
    return _count(followers, "total")


PROVIDERS: dict[str, Callable[..., int]] = {
    "bluesky": bluesky,
    "mastodon": mastodon,
    "github": github,
    "discord": discord,
    "matrix": matrix,
    "youtube": youtube,
    "twitch": twitch,
}
