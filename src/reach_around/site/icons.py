# SPDX-License-Identifier: Apache-2.0
"""The icon slugs the stylesheet can draw.

Each slug has a file in ``static/icons/`` and an ``.icon-<slug>`` rule in
``site.css``. A slug outside this set falls back to the text label.
"""

from __future__ import annotations

ICON_SLUGS: frozenset[str] = frozenset(
    """
    twitch kick mastodon bluesky tiktok discord youtube github instagram
    threads matrix x linkedin patreon email rss link bitcoin ethereum merch
    monero solana
    """.split()
)

# A link thumbnail is a square this many CSS pixels wide. It matches the
# ``--ra-thumb`` token (2.75rem at the default 16 px) and gives the browser a
# fixed box to reserve before the image arrives.
THUMB_PX = 44
