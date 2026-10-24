# SPDX-License-Identifier: Apache-2.0
"""Feature switches and privacy wording for the site config.

The privacy page and the cookie notice are generated from these values, so
turning a feature on can never leave the disclosure behind.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


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
    retention_days: int = Field(default=90, ge=1, le=3650)
    lead_retention_days: int = Field(default=365, ge=1, le=3650)
