# SPDX-License-Identifier: Apache-2.0
"""Regenerate the known-answer QR vectors in test_support_qr.py (#713).

Not a test and not run in CI; the reference package is not a dependency. Run it
by hand in a throwaway virtualenv outside the repository:

    python -m venv /path/to/scratch-venv
    /path/to/scratch-venv/bin/pip install --require-hashes -r /path/to/req.txt
    /path/to/scratch-venv/bin/pip install --no-deps -e .
    /path/to/scratch-venv/bin/python tests/site/gen_qr_vectors.py > KNOWN.txt

where req.txt holds the one line

    qrcode==8.2 \\
        --hash=sha256:16e64e0716c14960108e85d853062c9e8bba5ca8252c0b4d0231b9df4060ff4f

(the sha256 of qrcode-8.2-py3-none-any.whl as downloaded from PyPI). It prints
the ``KNOWN`` list: for each (version, mask) in versions 1, 5, 9 and 10 the first
hash-derived text whose matrix from this repository's encoder equals the
reference package's matrix for that mask, error correction level Q, no border. Texts
always hold lowercase letters, so the reference stays in byte mode.
"""

from __future__ import annotations

import hashlib
import importlib
from typing import Any

from reach_around.site.qr import qr_matrix

# Imported by name so the repository's type check does not need the package.
qrcode: Any = importlib.import_module("qrcode")

ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
LENGTHS = {1: (6, 11), 5: (40, 60), 9: (110, 130), 10: (131, 151)}


def text_for(version: int, attempt: int) -> str:
    """A repeatable pseudo-random text from a hash counter (no random module)."""
    low, high = LENGTHS[version]
    stream = b""
    block = 0
    while len(stream) < 2 * high:
        stream += hashlib.sha256(f"{version}:{attempt}:{block}".encode()).digest()
        block += 1
    n = low + stream[0] % (high - low + 1)
    return "".join(ALPHABET[b % len(ALPHABET)] for b in stream[1 : n + 1])


def reference(text: str, version: int, mask: int) -> list[list[bool]]:
    code = qrcode.QRCode(
        version=version,
        error_correction=qrcode.constants.ERROR_CORRECT_Q,
        border=0,
        mask_pattern=mask,
    )
    code.add_data(text)
    code.make(fit=False)
    return [[bool(v) for v in row] for row in code.get_matrix()]


def main() -> None:
    chosen: dict[tuple[int, int], tuple[str, list[list[bool]]]] = {}
    for version in LENGTHS:
        for attempt in range(600):
            text = text_for(version, attempt)
            matrix = qr_matrix(text)
            if (len(matrix) - 17) // 4 != version:
                continue
            for mask in range(8):
                if reference(text, version, mask) == matrix:
                    chosen.setdefault((version, mask), (text, matrix))
    print("KNOWN: list[tuple[int, int, str, list[str]]] = [")
    for (version, mask), (text, matrix) in sorted(chosen.items()):
        rows = [
            format(sum(1 << (len(r) - 1 - i) for i, v in enumerate(r) if v), "x")
            for r in matrix
        ]
        print(f'    ({version}, {mask}, "{text}", [')
        for i in range(0, len(rows), 6):
            print("        " + ", ".join(f'"{r}"' for r in rows[i : i + 6]) + ",")
        print("    ]),")
    print("]")


if __name__ == "__main__":
    main()
