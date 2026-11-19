# SPDX-License-Identifier: Apache-2.0
"""Feature switches and privacy wording for the site config.

The privacy page and the cookie notice are generated from these values, so
turning a feature on can never leave the disclosure behind.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# The Worker's own limits (worker/src/kit/leadretention.ts; a test keeps them
# equal). The deploy writes this config value to the Worker as
# LEAD_RETENTION_DAYS, so the page and the purge cannot disagree.
DEFAULT_LEAD_RETENTION_DAYS = 30
MAX_LEAD_RETENTION_DAYS = 365
# worker/src/retention.ts DEFAULT_KEEP_DAYS, used by the cron purge with no
# override (a test keeps them equal and checks the call has no window argument).
RAW_CLICK_KEEP_DAYS = 90


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
