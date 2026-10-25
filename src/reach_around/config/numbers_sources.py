# SPDX-License-Identifier: Apache-2.0
"""The ``numbers`` config: where each platform's follower count comes from.

Credentials are never in the config. A keyed source names the ENVIRONMENT
VARIABLE that holds the secret (supplied by ``doppler run`` or the CI secret
store); the variable name is validated, the value is read only at fetch time.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from reach_around.net.egress import EgressBlocked, validate_hostname

MAX_SOURCES = 50

_SLUG = r"^[a-z0-9][a-z0-9-]{0,39}$"
_ENV_NAME = r"^[A-Z][A-Z0-9_]{1,63}$"
_HANDLE = re.compile(
    r"^(?=.{4,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
_DID = re.compile(r"^did:[a-z]{3,10}:[A-Za-z0-9._:-]{1,200}$")
_ACCT = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_GITHUB_USER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_INVITE = re.compile(r"^[A-Za-z0-9-]{2,32}$")
_ALIAS = re.compile(r"^#[A-Za-z0-9._=/+-]{1,100}:([A-Za-z0-9.-]{3,253})$")
_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_TWITCH_LOGIN = re.compile(r"^[A-Za-z0-9_]{3,25}$")

Platform = Annotated[str, StringConstraints(pattern=_SLUG)]
EnvName = Annotated[str, StringConstraints(pattern=_ENV_NAME)]


class _Source(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: Platform


class BlueskySource(_Source):
    kind: Literal["bluesky"]
    actor: str

    @field_validator("actor")
    @classmethod
    def _actor(cls, value: str) -> str:
        if not (_HANDLE.match(value) or _DID.match(value)):
            raise ValueError("actor must be a handle like name.bsky.social or a DID")
        return value


class MastodonSource(_Source):
    """Any server with the Mastodon API (Pixelfed and others included)."""

    kind: Literal["mastodon"]
    host: str
    acct: str

    @field_validator("host")
    @classmethod
    def _host(cls, value: str) -> str:
        try:
            return validate_hostname(value)
        except EgressBlocked:
            raise ValueError(
                "host must be a public host name (no IP address, no localhost, "
                "no port or path)"
            ) from None

    @field_validator("acct")
    @classmethod
    def _acct(cls, value: str) -> str:
        if not _ACCT.match(value):
            raise ValueError("acct must be the local user name, without a leading @")
        return value


class GithubSource(_Source):
    kind: Literal["github"]
    user: str

    @field_validator("user")
    @classmethod
    def _user(cls, value: str) -> str:
        if not _GITHUB_USER.match(value):
            raise ValueError("user is not a valid GitHub user name")
        return value


class DiscordSource(_Source):
    kind: Literal["discord"]
    invite: str

    @field_validator("invite")
    @classmethod
    def _invite(cls, value: str) -> str:
        if not _INVITE.match(value):
            raise ValueError("invite must be the invite code, not a URL")
        return value


class MatrixSource(_Source):
    kind: Literal["matrix"]
    room: str

    @field_validator("room")
    @classmethod
    def _room(cls, value: str) -> str:
        found = _ALIAS.match(value)
        if not found:
            raise ValueError("room must be a public alias like #room:example.org")
        try:
            validate_hostname(found.group(1))
        except EgressBlocked:
            raise ValueError(
                "the alias server must be a public host name with no port"
            ) from None
        return value


class YoutubeSource(_Source):
    kind: Literal["youtube"]
    channel_id: str
    api_key_env: EnvName

    @field_validator("channel_id")
    @classmethod
    def _channel(cls, value: str) -> str:
        if not _CHANNEL_ID.match(value):
            raise ValueError("channel_id must be the UC... channel id")
        return value


class TwitchSource(_Source):
    kind: Literal["twitch"]
    login: str
    client_id_env: EnvName
    client_secret_env: EnvName

    @field_validator("login")
    @classmethod
    def _login(cls, value: str) -> str:
        if not _TWITCH_LOGIN.match(value):
            raise ValueError("login must be a Twitch login name")
        return value

    @model_validator(mode="after")
    def _distinct(self) -> TwitchSource:
        if self.client_id_env == self.client_secret_env:
            raise ValueError("client_id_env and client_secret_env must differ")
        return self


Source = Annotated[
    BlueskySource
    | MastodonSource
    | GithubSource
    | DiscordSource
    | MatrixSource
    | YoutubeSource
    | TwitchSource,
    Field(discriminator="kind"),
]


class NumbersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sources: list[Source] = Field(min_length=1, max_length=MAX_SOURCES)

    @model_validator(mode="after")
    def _one_source_per_platform(self) -> NumbersConfig:
        seen: set[str] = set()
        for source in self.sources:
            if source.platform in seen:
                raise ValueError(f"platform {source.platform!r} has two sources")
            seen.add(source.platform)
        return self
