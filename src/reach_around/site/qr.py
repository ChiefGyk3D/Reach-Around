# SPDX-License-Identifier: Apache-2.0
"""A small QR Code encoder for the support page (#713).

Byte mode, error correction level Q, versions 1 to 10 (up to 151 ASCII
characters), written for one job: turn an already validated payment address
into an SVG at build time. It reads no file, opens no socket and uses no
third-party package. The standard is ISO/IEC 18004; the block tables below are
the level Q rows of its tables and the tests decode the output with an
independent reader, so a wrong table fails there.
"""

from __future__ import annotations

import hashlib

# version -> (first group blocks, data codewords each, second group blocks,
# data codewords each, error codewords per block)
_BLOCKS: dict[int, tuple[int, int, int, int, int]] = {
    1: (1, 13, 0, 0, 13),
    2: (1, 22, 0, 0, 22),
    3: (2, 17, 0, 0, 18),
    4: (2, 24, 0, 0, 26),
    5: (2, 15, 2, 16, 18),
    6: (4, 19, 0, 0, 24),
    7: (2, 14, 4, 15, 18),
    8: (4, 18, 2, 19, 22),
    9: (4, 16, 4, 17, 20),
    10: (6, 19, 2, 20, 24),
}
_ALIGN: dict[int, tuple[int, ...]] = {
    1: (),
    2: (6, 18),
    3: (6, 22),
    4: (6, 26),
    5: (6, 30),
    6: (6, 34),
    7: (6, 22, 38),
    8: (6, 24, 42),
    9: (6, 26, 46),
    10: (6, 28, 50),
}
_LEVEL_Q = 3  # the two format bits for level Q
BORDER = 4  # quiet zone, in modules
# Dark modules on an amber tile (owner decision D10, #688): a scanner needs the
# contrast, the page does not need a white block. The tile is the brand amber
# (--ra-brand-amber in tokens.css; a test keeps the two equal) and stays one
# small image, never a page-wide fill.
TILE = "#fbb03b"
INK = "#0b0e14"

_EXP = [0] * 512
_LOG = [0] * 256


def _init_field() -> None:
    value = 1
    for i in range(255):
        _EXP[i] = value
        _LOG[value] = i
        value <<= 1
        if value & 0x100:
            value ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_field()


def _mul(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _rs_remainder(data: list[int], degree: int) -> list[int]:
    gen = [1]
    for i in range(degree):
        nxt = [0] * (len(gen) + 1)
        for j, coef in enumerate(gen):
            nxt[j] ^= coef
            nxt[j + 1] ^= _mul(coef, _EXP[i])
        gen = nxt
    rem = [0] * degree
    for byte in data:
        factor = byte ^ rem[0]
        rem = [*rem[1:], 0]
        for i in range(degree):
            rem[i] ^= _mul(gen[i + 1], factor)
    return rem


def _capacity(version: int) -> int:
    g1, d1, g2, d2, _ = _BLOCKS[version]
    return g1 * d1 + g2 * d2


def _codewords(payload: bytes, version: int) -> list[int]:
    bits: list[int] = []

    def put(value: int, count: int) -> None:
        bits.extend((value >> (count - 1 - i)) & 1 for i in range(count))

    put(0b0100, 4)
    put(len(payload), 8 if version <= 9 else 16)
    for byte in payload:
        put(byte, 8)
    room = _capacity(version) * 8
    put(0, min(4, room - len(bits)))
    put(0, -len(bits) % 8)
    data = [int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0
    while len(data) < _capacity(version):
        data.append(0xEC if pad % 2 == 0 else 0x11)
        pad += 1
    return data


def _interleave(data: list[int], version: int) -> list[int]:
    g1, d1, g2, d2, ec = _BLOCKS[version]
    blocks: list[list[int]] = []
    pos = 0
    for size in [d1] * g1 + [d2] * g2:
        blocks.append(data[pos : pos + size])
        pos += size
    ecs = [_rs_remainder(block, ec) for block in blocks]
    out: list[int] = []
    for i in range(max(len(b) for b in blocks)):
        out.extend(block[i] for block in blocks if i < len(block))
    for i in range(ec):
        out.extend(block[i] for block in ecs)
    return out


class _Grid:
    def __init__(self, version: int) -> None:
        self.size = 17 + 4 * version
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.fixed = [[False] * self.size for _ in range(self.size)]
        self.version = version

    def put(self, x: int, y: int, dark: bool) -> None:
        self.dark[y][x] = dark
        self.fixed[y][x] = True

    def patterns(self) -> None:
        size = self.size
        for i in range(size):
            self.put(6, i, i % 2 == 0)
            self.put(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        self.put(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        spots = _ALIGN[self.version]
        for cy in spots:
            for cx in spots:
                if (cx, cy) in ((6, 6), (6, spots[-1]), (spots[-1], 6)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.put(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
        self.format(0)  # reserve the areas
        if self.version >= 7:
            rem = self.version
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = (self.version << 12) | rem
            for i in range(18):
                a, b = size - 11 + i % 3, i // 3
                self.put(a, b, bool((bits >> i) & 1))
                self.put(b, a, bool((bits >> i) & 1))

    def format(self, mask: int) -> None:
        data = (_LEVEL_Q << 3) | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = ((data << 10) | rem) ^ 0x5412
        size = self.size

        def bit(i: int) -> bool:
            return bool((bits >> i) & 1)

        for i in range(6):
            self.put(8, i, bit(i))
        self.put(8, 7, bit(6))
        self.put(8, 8, bit(7))
        self.put(7, 8, bit(8))
        for i in range(9, 15):
            self.put(14 - i, 8, bit(i))
        for i in range(8):
            self.put(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.put(8, size - 15 + i, bit(i))
        self.put(8, size - 8, True)

    def place(self, codewords: list[int]) -> None:
        bits = [(w >> (7 - i)) & 1 for w in codewords for i in range(8)]
        n = 0
        right = self.size - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(self.size):
                for j in range(2):
                    x = right - j
                    y = self.size - 1 - vert if ((right + 1) & 2) == 0 else vert
                    if not self.fixed[y][x] and n < len(bits):
                        self.dark[y][x] = bool(bits[n])
                        n += 1
            right -= 2


def _masked(mask: int, x: int, y: int) -> bool:
    """Whether mask pattern ``mask`` flips the module at column x, row y."""
    return (
        (x + y) % 2 == 0,
        y % 2 == 0,
        x % 3 == 0,
        (x + y) % 3 == 0,
        (x // 3 + y // 2) % 2 == 0,
        x * y % 2 + x * y % 3 == 0,
        (x * y % 2 + x * y % 3) % 2 == 0,
        ((x + y) % 2 + x * y % 3) % 2 == 0,
    )[mask]


def _penalty(m: list[list[bool]]) -> int:
    size = len(m)
    score = 0
    lines = [row for row in m] + [[m[y][x] for y in range(size)] for x in range(size)]
    for line in lines:
        run = 1
        for i in range(1, size + 1):
            if i < size and line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run = 1
        text = "".join("1" if v else "0" for v in line)
        for pat in ("10111010000", "00001011101"):
            score += 40 * sum(
                text.startswith(pat, i) for i in range(len(text) - len(pat) + 1)
            )
    for y in range(size - 1):
        for x in range(size - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(sum(row) for row in m)
    score += 10 * (abs(dark * 20 - size * size * 10) // (size * size))
    return score


def qr_matrix(text: str) -> list[list[bool]]:
    """The module matrix (True is dark, no border) for ASCII ``text``."""
    if not text:
        raise ValueError("QR text is empty")
    if not text.isascii():
        raise ValueError("QR text must be ASCII")
    payload = text.encode("ascii")
    for version in range(1, 11):
        # mode and count bits first, then the payload
        if len(payload) * 8 + 4 + (8 if version <= 9 else 16) <= _capacity(version) * 8:
            break
    else:
        raise ValueError("QR text is too long (151 characters at most)")
    words = _interleave(_codewords(payload, version), version)
    best: tuple[int, list[list[bool]]] | None = None
    for mask in range(8):
        grid = _Grid(version)
        grid.patterns()
        grid.place(words)
        for y in range(grid.size):
            for x in range(grid.size):
                if not grid.fixed[y][x] and _masked(mask, x, y):
                    grid.dark[y][x] = not grid.dark[y][x]
        grid.format(mask)
        score = _penalty(grid.dark)
        if best is None or score < best[0]:
            best = (score, grid.dark)
    if best is None:
        raise AssertionError("no mask was tried")
    return best[1]


def qr_svg(text: str) -> str:
    """An SVG of the QR code for ``text``: one rect, one path, a 4 module border."""
    matrix = qr_matrix(text)
    full = len(matrix) + 2 * BORDER
    parts: list[str] = []
    for y, row in enumerate(matrix):
        x = 0
        while x < len(row):
            if not row[x]:
                x += 1
                continue
            start = x
            while x < len(row) and row[x]:
                x += 1
            parts.append(f"M{start + BORDER} {y + BORDER}h{x - start}v1h-{x - start}z")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {full} {full}" '
        f'shape-rendering="crispEdges"><rect width="{full}" height="{full}" '
        f'fill="{TILE}"/><path fill="{INK}" d="{"".join(parts)}"/></svg>\n'
    )


def qr_file_name(position: int, svg: str) -> str:
    """The file name for a QR code: its position and a digest of its SVG bytes.

    The digest covers the finished file, so any change to the payload, the
    drawing or the colors is a new name. A cached copy can then never be
    served for different bytes: the files can be cached forever while the page
    itself revalidates.
    """
    digest = hashlib.sha256(svg.encode("ascii")).hexdigest()[:32]
    return f"qr-{position}-{digest}.svg"
