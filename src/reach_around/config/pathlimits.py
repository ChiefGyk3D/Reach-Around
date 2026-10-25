# SPDX-License-Identifier: Apache-2.0
"""Byte-length limits for the asset paths a config names.

The build publishes each file under a content-hashed name: ``.`` plus the hash
is added to the leaf. Linux limits one name to 255 bytes, so the leaf a config
may name is shorter by that amount, and the limit is on *encoded bytes* (a
multibyte character counts for each of its bytes). A whole path is capped too.
Checking here means the build never meets an operating-system error for a name
the config accepted.
"""

from __future__ import annotations

from reach_around.site.fingerprint import HASH_LEN

NAME_MAX = 255
# "." plus the hash, added to the leaf by ``fingerprint.hashed_name``.
FINGERPRINT_RESERVE = 1 + HASH_LEN
MAX_LEAF_BYTES = NAME_MAX - FINGERPRINT_RESERVE
MAX_PATH_BYTES = 1024


def check_path_bytes(value: str, field: str) -> str:
    """Return ``value`` or raise ``ValueError`` naming ``field``."""
    total = len(value.encode("utf-8"))
    if total > MAX_PATH_BYTES:
        raise ValueError(
            f"{field}: path is {total} bytes; at most {MAX_PATH_BYTES} are allowed"
        )
    leaf = len(value.rsplit("/", 1)[-1].encode("utf-8"))
    if leaf > MAX_LEAF_BYTES:
        raise ValueError(
            f"{field}: file name is {leaf} bytes; at most {MAX_LEAF_BYTES} are "
            f"allowed ({FINGERPRINT_RESERVE} more are added for the content hash, "
            f"and a name is limited to {NAME_MAX} bytes)"
        )
    return value
