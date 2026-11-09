# SPDX-License-Identifier: Apache-2.0
"""Owner alerts (issue #252): which channels and events notify the owner.

Default OFF. The section only chooses; it holds no destination and no secret.
Webhook URLs, tokens and the Matrix room come from the Worker's bindings (see
docs/alerts.md). The Worker reads the section as one JSON value in the plain
variable ``ALERTS_CONFIG``; ``worker_value`` renders exactly that value, so the
deploy and the Worker agree (a Worker test pins the same names).
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AlertChannel = Literal["discord", "matrix", "email"]
AlertEventName = Literal[
    "kit_unlocked",
    "lead_captured",
    "gate_failed_repeatedly",
    "analytics_health",
]
VisitorEmail = Literal["full", "masked", "none"]

# The Worker's own limits (worker/src/alerts/config.ts).
DEFAULT_MAX_PER_MINUTE = 5
MAX_PER_MINUTE_CEILING = 30


# The Worker's host rules (worker/src/alerts/validate.ts; a test keeps the lists equal).
MAX_ALLOWED_HOSTS = 4
PRIVATE_SUFFIXES = (
    "localhost", "local", "internal", "localdomain", "lan", "home", "corp",
    "intranet", "private", "test", "invalid", "example", "onion", "arpa", "home.arpa",
)  # fmt: skip
WILDCARD_DNS_SUFFIXES = (
    "nip.io", "sslip.io", "xip.io", "traefik.me", "localtest.me", "lvh.me",
    "localhost.run", "lacolhost.com", "vcap.me", "fbi.com", "1u.ms", "rebind.it",
    "rbndr.us", "rebind.network", "ssrf.cc", "local.gd", "xip.name", "nip.name",
)  # fmt: skip
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_TLD = re.compile(r"^(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$")
_QUAD = re.compile(r"(?:^|[^0-9])[0-9]{1,3}(?:[.-][0-9]{1,3}){3}(?:$|[^0-9])")


def _bad_host(host: str) -> str | None:
    """Why a Matrix homeserver host name is refused, or None. Never echoes the value."""
    if (
        host != host.lower()
        or len(host) > 253
        or host.startswith(".")
        or host.endswith(".")
    ):
        return "must be a lowercase DNS name"
    labels = host.split(".")
    if (
        len(labels) < 2
        or not all(_LABEL.match(x) for x in labels)
        or not _TLD.match(labels[-1])
    ):
        return "must be a DNS name with a letters-only last label"
    for label in labels:
        if label.isdigit() or re.fullmatch(r"0x[0-9a-f]*", label):
            return "must not embed an IP address"
        if re.fullmatch(r"[0-9a-f]{8}", label) and re.search(r"[0-9]", label):
            return "must not embed an IP address"
    if _QUAD.search(host):
        return "must not embed an IP address"
    for suffix in PRIVATE_SUFFIXES + WILDCARD_DNS_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return "must not be a private, reserved or wildcard-DNS name"
    return None


class AlertsMatrix(BaseModel):
    """The Matrix homeservers the access token may be sent to (exact match)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_hosts: list[str] = Field(default=[], max_length=MAX_ALLOWED_HOSTS)

    @field_validator("allowed_hosts")
    @classmethod
    def _hosts(cls, value: list[str]) -> list[str]:
        for host in value:
            why = _bad_host(host)
            if why is not None:
                raise ValueError(f"allowed_hosts entry {why}")
        if len(set(value)) != len(value):
            raise ValueError("list items must be unique")
        return value


class Alerts(BaseModel):
    """Which channels receive which owner alerts. Everything off by default."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    channels: list[AlertChannel] = []
    events: list[AlertEventName] = []
    # Whether the visitor's address appears in the alert: the whole address, a
    # masked form (p***@example.com) or not at all. Alerts go only to channels
    # the owner chose, but a chat room is not a secret store.
    visitor_email: VisitorEmail = "full"
    max_per_minute: int = Field(
        default=DEFAULT_MAX_PER_MINUTE,
        ge=1,
        le=MAX_PER_MINUTE_CEILING,
        strict=True,
    )

    # The homeserver the Matrix token may go to. The name authorizes the token to
    # be sent there, so it is listed here and matched exactly (docs/alerts.md).
    matrix: AlertsMatrix = AlertsMatrix()

    @model_validator(mode="after")
    def _matrix_needs_a_host(self) -> Alerts:
        if "matrix" in self.channels and not self.matrix.allowed_hosts:
            raise ValueError("the matrix channel needs matrix.allowed_hosts")
        return self

    @field_validator("channels", "events")
    @classmethod
    def _no_repeats(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("list items must be unique")
        return value

    @property
    def enabled(self) -> bool:
        """True when at least one channel and one event are chosen."""
        return bool(self.channels) and bool(self.events)


def worker_value(alerts: Alerts) -> str | None:
    """The ``ALERTS_CONFIG`` value for the Worker, or ``None`` when alerts are off."""
    if not alerts.enabled:
        return None
    value: dict[str, object] = {
        "channels": list(alerts.channels),
        "events": list(alerts.events),
        "visitor_email": alerts.visitor_email,
        "max_per_minute": alerts.max_per_minute,
    }
    if alerts.matrix.allowed_hosts:
        value["matrix"] = {"allowed_hosts": list(alerts.matrix.allowed_hosts)}
    return json.dumps(
        value,
        separators=(",", ":"),
        sort_keys=True,
    )
