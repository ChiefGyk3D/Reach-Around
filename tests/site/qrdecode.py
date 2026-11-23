# SPDX-License-Identifier: Apache-2.0
"""A small QR decoder for the tests (#713): the round-trip check.

It reads a module matrix the way a scanner does (format bits, unmask, zigzag
read, de-interleave, Reed-Solomon check, byte-mode text) and shares no code
with the encoder, so an encoder mistake cannot cancel itself out. Byte mode,
versions 1 to 10, any error correction level.
"""

from __future__ import annotations

import re

Matrix = list[list[bool]]

# (error correction level, version) -> (blocks, data codewords per block,
# blocks that carry one more data codeword, error codewords per block)
# Level bits in the format information: L 01, M 00, Q 11, H 10.
LEVEL_BITS = {"L": 1, "M": 0, "Q": 3, "H": 2}
_EC_PER_BLOCK_Q = {
    1: 13,
    2: 22,
    3: 18,
    4: 26,
    5: 18,
    6: 24,
    7: 18,
    8: 22,
    9: 20,
    10: 24,
}
_BLOCKS_Q = {1: 1, 2: 1, 3: 2, 4: 2, 5: 4, 6: 4, 7: 6, 8: 6, 9: 8, 10: 8}
_TOTAL = {
    1: 26, 2: 44, 3: 70, 4: 100, 5: 134, 6: 172, 7: 196, 8: 242, 9: 292, 10: 346,
}  # fmt: skip
_ALIGN = {
    2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}  # fmt: skip


def parse_svg(svg: str) -> tuple[Matrix, int]:
    """The dark-module matrix of an SVG drawn the way the build draws it.

    Returns the matrix without its border and the border width in modules.
    """
    box = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    assert box and box.group(1) == box.group(2)
    full = int(box.group(1))
    path = re.search(r'<path [^>]*?d="([^"]*)"', svg)
    assert path
    grid = [[False] * full for _ in range(full)]
    for x, y, run in re.findall(r"M(\d+) (\d+)h(\d+)v1h-\d+z", path.group(1)):
        for i in range(int(run)):
            grid[int(y)][int(x) + i] = True
    # the border is the all-light rim; find it from the first dark module
    first = min(min(x for x, v in enumerate(row) if v) for row in grid if any(row))
    border = first
    size = full - 2 * border
    matrix = [row[border : border + size] for row in grid[border : border + size]]
    return matrix, border


def _function_mask(version: int, size: int) -> list[list[bool]]:
    fn = [[False] * size for _ in range(size)]
    for y in range(size):
        for x in range(size):
            corner = (
                (x < 9 and y < 9)
                or (x >= size - 8 and y < 9)
                or (x < 9 and y >= size - 8)
            )
            if corner or x == 6 or y == 6:
                fn[y][x] = True
    centers = _ALIGN.get(version, [])
    for cy in centers:
        for cx in centers:
            if (cx, cy) in ((6, 6), (6, centers[-1]), (centers[-1], 6)):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    fn[cy + dy][cx + dx] = True
    if version >= 7:
        for a in range(6):
            for b in range(3):
                fn[a][size - 11 + b] = True
                fn[size - 11 + b][a] = True
    return fn


def _format_bits(m: Matrix) -> int:
    size = len(m)
    first = [(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)]
    first += [(14 - i, 8) for i in range(9, 15)]
    bits = 0
    for i, (x, y) in enumerate(first):
        bits |= int(m[y][x]) << i
    second = [(size - 1 - i, 8) for i in range(8)] + [
        (8, size - 15 + i) for i in range(8, 15)
    ]
    other = 0
    for i, (x, y) in enumerate(second):
        other |= int(m[y][x]) << i
    assert bits == other, "the two copies of the format information differ"
    return bits


def _format_decode(bits: int) -> tuple[int, int]:
    best: tuple[int, int, int] | None = None
    for data in range(32):
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        code = ((data << 10) | rem) ^ 0x5412
        dist = bin(code ^ bits).count("1")
        if best is None or dist < best[0]:
            best = (dist, data >> 3, data & 7)
    assert best is not None and best[0] == 0, "format information is not valid"
    return best[1], best[2]  # level bits, mask


def _mask(n: int, x: int, y: int) -> bool:
    return [
        (x + y) % 2 == 0,
        y % 2 == 0,
        x % 3 == 0,
        (x + y) % 3 == 0,
        (x // 3 + y // 2) % 2 == 0,
        x * y % 2 + x * y % 3 == 0,
        (x * y % 2 + x * y % 3) % 2 == 0,
        ((x + y) % 2 + x * y % 3) % 2 == 0,
    ][n]


def _gf() -> tuple[list[int], list[int]]:
    exp, log = [0] * 512, [0] * 256
    v = 1
    for i in range(255):
        exp[i], log[v] = v, i
        v <<= 1
        if v & 0x100:
            v ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


def _rs_ok(block: list[int], ec: int) -> bool:
    exp, log = _gf()
    for k in range(ec):
        acc = 0
        for c in block:
            acc = (exp[log[acc] + k] if acc else 0) ^ c
        if acc:
            return False
    return True


def decode(m: Matrix) -> tuple[bytes, int, int]:
    """Return (payload, level bits, version). Raises AssertionError if unreadable."""
    size = len(m)
    version = (size - 17) // 4
    assert size == 17 + 4 * version and 1 <= version <= 10
    level, mask = _format_decode(_format_bits(m))
    assert level == LEVEL_BITS["Q"], "this decoder reads level Q only"
    fn = _function_mask(version, size)
    bits: list[int] = []
    x = size - 1
    up = True
    while x > 0:
        if x == 6:
            x -= 1
        ys = range(size - 1, -1, -1) if up else range(size)
        for y in ys:
            for xx in (x, x - 1):
                if not fn[y][xx]:
                    bits.append(int(m[y][xx] != _mask(mask, xx, y)))
        up = not up
        x -= 2
    total = _TOTAL[version]
    words = [int("".join(map(str, bits[i * 8 : i * 8 + 8])), 2) for i in range(total)]
    nblocks, ec = _BLOCKS_Q[version], _EC_PER_BLOCK_Q[version]
    data_total = total - nblocks * ec
    short = data_total // nblocks
    long_blocks = data_total % nblocks
    lens = [short + (1 if i >= nblocks - long_blocks else 0) for i in range(nblocks)]
    blocks: list[list[int]] = [[] for _ in range(nblocks)]
    pos = 0
    for r in range(max(lens)):
        for b in range(nblocks):
            if r < lens[b]:
                blocks[b].append(words[pos])
                pos += 1
    ecs: list[list[int]] = [[] for _ in range(nblocks)]
    for _ in range(ec):
        for b in range(nblocks):
            ecs[b].append(words[pos])
            pos += 1
    data: list[int] = []
    for b in range(nblocks):
        assert _rs_ok(blocks[b] + ecs[b], ec), "Reed-Solomon check failed"
        data += blocks[b]
    stream = "".join(f"{w:08b}" for w in data)
    assert stream[:4] == "0100", "not byte mode"
    cbits = 8 if version <= 9 else 16
    n = int(stream[4 : 4 + cbits], 2)
    body = stream[4 + cbits : 4 + cbits + 8 * n]
    return bytes(int(body[i : i + 8], 2) for i in range(0, 8 * n, 8)), level, version


def read_format(m: Matrix) -> tuple[int, int]:
    """(level bits, mask) from the format information of a matrix."""
    return _format_decode(_format_bits(m))
