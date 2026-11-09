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
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AlertChannel = Literal["discord", "matrix", "email"]
AlertEventName = Literal["kit_unlocked", "lead_captured", "gate_failed_repeatedly"]
VisitorEmail = Literal["full", "masked", "none"]

# The Worker's own limits (worker/src/alerts/config.ts).
DEFAULT_MAX_PER_MINUTE = 5
MAX_PER_MINUTE_CEILING = 30


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
    return json.dumps(
        {
            "channels": list(alerts.channels),
            "events": list(alerts.events),
            "visitor_email": alerts.visitor_email,
            "max_per_minute": alerts.max_per_minute,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
