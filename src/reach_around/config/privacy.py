# SPDX-License-Identifier: Apache-2.0
"""Feature switches and privacy wording for the site config.

The privacy page and the cookie notice are generated from these values, so
turning a feature on can never leave the disclosure behind.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from reach_around.config.support import check_plain

# The Worker's own limits (worker/src/kit/leadretention.ts; a test keeps them
# equal). The deploy writes this config value to the Worker as
# LEAD_RETENTION_DAYS, so the page and the purge cannot disagree.
DEFAULT_LEAD_RETENTION_DAYS = 30
MAX_LEAD_RETENTION_DAYS = 365
# worker/src/retention.ts DEFAULT_KEEP_DAYS, used by the cron purge with no
# override (a test keeps them equal and checks the call has no window argument).
RAW_CLICK_KEEP_DAYS = 90
# worker/src/clickfunnel.ts MONTHS_KEPT: months a monthly total is kept (a test
# reads the Worker source and keeps the two equal).
MONTHLY_KEEP_MONTHS = 25
# The Worker's own default when GEO_LEVEL is unset (worker/src/view.ts). The
# privacy notice reads ``analytics.geo_level`` when the config has one and falls
# back to this; PR #793 changes the Worker default to region.
DEFAULT_GEO_LEVEL = "city"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Features(_Model):
    """Which data-collecting features the site uses."""

    click_tracking: bool = True
    kit_gate: bool = False


class Privacy(_Model):
    """Retention periods and optional creator-supplied wording."""

    contact: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = (
        None
    )
    intro: Annotated[str, StringConstraints(min_length=1, max_length=2000)] | None = (
        None
    )
    # Who the privacy notice names as the controller. Required, with ``contact``,
    # when the kit gate or alerts are on (see ``missing_notice_fields``).
    controller_name: (
        Annotated[str, StringConstraints(min_length=1, max_length=200)] | None
    ) = None
    # Optional creator-supplied sentences appended to the fixed notice. Empty by
    # default; plain text only.
    dpa_note: Annotated[str, StringConstraints(max_length=500)] = ""
    turnstile_storage_note: Annotated[str, StringConstraints(max_length=500)] = ""
    # The Worker deletes raw click rows at one fixed window and no deploy
    # variable changes it, so the only honest value is the Worker's own; any
    # other number would make the privacy page promise what the code does not do.
    retention_days: int = Field(
        default=RAW_CLICK_KEEP_DAYS, ge=RAW_CLICK_KEEP_DAYS, le=RAW_CLICK_KEEP_DAYS
    )
    # The ONE lead retention setting: the privacy page prints it and the deploy
    # writes it to the Worker as LEAD_RETENTION_DAYS. Strict: a quoted number or
    # a boolean is a mistake, not a retention.
    lead_retention_days: int = Field(
        default=DEFAULT_LEAD_RETENTION_DAYS,
        ge=1,
        le=MAX_LEAD_RETENTION_DAYS,
        strict=True,
    )

    @field_validator("controller_name", "contact", "dpa_note", "turnstile_storage_note")
    @classmethod
    def _plain_one_line(cls, value: str | None) -> str | None:
        """Plain text on one line: no control, hidden or bidirectional marks."""
        if value is not None:
            check_plain(value)
        return value


def missing_notice_fields(
    privacy: Privacy, *, kit_gate: bool, alerts_enabled: bool
) -> list[str]:
    """Names of the privacy fields the notice needs but the config leaves out.

    The notice names a controller and a contact; with the kit gate or alerts on
    the site holds or forwards personal data, so both are required then.
    Returns field names only, never a value.
    """
    if not (kit_gate or alerts_enabled):
        return []
    missing: list[str] = []
    if not privacy.controller_name:
        missing.append("privacy.controller_name")
    if not privacy.contact:
        missing.append("privacy.contact")
    return missing
