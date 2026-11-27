# SPDX-License-Identifier: Apache-2.0
"""The ``numbers.fediverse`` config: which accounts the fediverse collector reads.

Slice 4 of the fediverse collector (#940, ``docs/design/fediverse-collector.md``).
The account and the instance are config only; they are never stored in a snapshot
or a history row. There is no secret here: both providers read public data without
a token. One account per platform, so at most two entries (Bluesky and Mastodon).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
)

MAX_FEDIVERSE_SOURCES = 2


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BlueskyFediverseSource(_Base):
    """One Bluesky account: a handle or a DID, checked by the provider itself."""

    kind: Literal["bluesky"]
    # The schema keywords below only mirror what the run-time check accepts (a
    # superset: they reject no value that check accepts); the check stays the
    # authority. A handle, ``did:plc:`` and ``did:web:`` use only these characters.
    actor: str = Field(
        json_schema_extra={
            "minLength": 1,
            "maxLength": 512,
            "pattern": "^[A-Za-z0-9:._%~-]+$",
        }
    )

    @field_validator("actor")
    @classmethod
    def _actor(cls, value: str) -> str:
        from reach_around.counts.bluesky_feed import check_actor

        return check_actor(value)


class MastodonFediverseSource(_Base):
    """One account on a server that speaks the Mastodon API.

    ``acct`` is the local name (it builds the re-check link). ``account_id`` is
    optional: with it the collector skips the lookup call and so reads no
    follower count from it (the numbers sources still do).
    """

    kind: Literal["mastodon"]
    # Schema keywords are a superset of the run-time checks (see the Bluesky model).
    host: str = Field(
        json_schema_extra={
            "minLength": 1,
            "maxLength": 253,
            "pattern": "^[a-z0-9_.-]+$",
        }
    )
    acct: str = Field(
        json_schema_extra={
            "minLength": 1,
            "maxLength": 30,
            "pattern": "^[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,28}[A-Za-z0-9_])?$",
        }
    )
    account_id: StrictStr | StrictInt | None = Field(
        default=None,
        json_schema_extra={"maxLength": 20, "pattern": "^(?:0|[1-9][0-9]{0,19})$"},
    )

    @field_validator("host")
    @classmethod
    def _host(cls, value: str) -> str:
        from reach_around.counts.mastodon_feed import check_host

        return check_host(value)

    @field_validator("acct")
    @classmethod
    def _acct(cls, value: str) -> str:
        from reach_around.counts.mastodon_feed import check_acct

        return check_acct(value)

    @field_validator("account_id")
    @classmethod
    def _account_id(cls, value: str | int | None) -> str | None:
        if value is None:
            return None
        from reach_around.counts.mastodon_feed import check_account_id

        return check_account_id(value)


FediverseSource = Annotated[
    BlueskyFediverseSource | MastodonFediverseSource,
    Field(discriminator="kind"),
]


def sources_problem(sources: list[FediverseSource]) -> str | None:
    """A message when a platform is listed twice or there are too many."""
    if len(sources) > MAX_FEDIVERSE_SOURCES:
        return f"at most {MAX_FEDIVERSE_SOURCES} fediverse sources (one per platform)"
    seen: set[str] = set()
    for item in sources:
        if item.kind in seen:
            return f"the fediverse source for {item.kind!r} is listed more than once"
        seen.add(item.kind)
    return None
