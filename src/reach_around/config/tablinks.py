# SPDX-License-Identifier: Apache-2.0
"""Internal tab links: ``tab:<id>`` and ``tab:<id>#<fragment>``.

A link or social ``url`` may point at another tab of the same site. The scheme
is exactly lowercase ``tab:``; the tab id is a slug and the optional fragment is
1 to 40 characters of ``[a-z0-9-]``. Nothing else is accepted, so no other
scheme can ride in through this form.
"""

from __future__ import annotations

import re

_REF = re.compile(r"tab:([a-z0-9][a-z0-9-]{0,39})(?:#([a-z0-9-]{1,40}))?")


def is_tab_ref(value: str) -> bool:
    """True if ``value`` uses the ``tab:`` scheme in any letter case."""
    return value[:4].lower() == "tab:"


def parse_tab_ref(value: str) -> tuple[str, str | None]:
    """Return ``(tab_id, fragment)`` or raise ``ValueError`` with the reason."""
    if not value.startswith("tab:"):
        raise ValueError("internal links must use the lowercase scheme tab:")
    match = _REF.fullmatch(value)
    if match is None:
        raise ValueError(
            "internal link must look like tab:<tab-id> or tab:<tab-id>#<fragment>"
            " (fragment: 1 to 40 characters of a-z, 0-9 and -)"
        )
    return match.group(1), match.group(2)
