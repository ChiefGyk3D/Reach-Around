# SPDX-License-Identifier: Apache-2.0
"""JPEG scrubbing: every segment is classified, including those between scans."""

from __future__ import annotations

import pytest

from reach_around.kit.stage import StageError, _scrub_jpeg


def seg(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + (len(payload) + 2).to_bytes(2, "big") + payload


SOI, EOI = b"\xff\xd8", b"\xff\xd9"
JFIF = seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
SOF = seg(0xC2, b"\x08\x00\x01\x00\x01\x01\x01\x11\x00")
DHT = seg(0xC4, b"\x00" + bytes(16))
DQT = seg(0xDB, b"\x00" + bytes(64))
SOS = seg(0xDA, b"\x01\x01\x00\x00\x3f\x00")
EXIF = seg(0xE1, b"Exif\x00\x00SECRET-EXIF-CANARY")
COM = seg(0xFE, b"SECRET-COM-CANARY")
SCAN1 = b"\x12\x34\xff\x00\x56"
SCAN2 = b"\x9a\xbc\xff\x00\xde"


def test_segments_between_scans_are_scrubbed() -> None:
    src = SOI + JFIF + SOF + DHT + SOS + SCAN1 + EXIF + COM + SOS + SCAN2 + EOI
    out = _scrub_jpeg(src)
    assert b"SECRET-EXIF-CANARY" not in out and b"SECRET-COM-CANARY" not in out
    assert out == SOI + JFIF + SOF + DHT + SOS + SCAN1 + SOS + SCAN2 + EOI


def test_an_end_marker_pair_inside_a_segment_payload_neither_truncates_nor_leaks() -> (
    None
):
    trap = seg(0xE1, b"Exif\x00\x00" + b"\xff\xd9" + b"SECRET-AFTER-TRAP")
    src = SOI + JFIF + SOF + SOS + SCAN1 + trap + SOS + SCAN2 + EOI
    out = _scrub_jpeg(src)
    assert b"SECRET-AFTER-TRAP" not in out
    assert out.endswith(SCAN2 + EOI) and out.count(SOS) == 2  # not truncated


def test_bytes_after_the_end_marker_are_gone() -> None:
    out = _scrub_jpeg(SOI + SOF + SOS + SCAN1 + EOI + EXIF + b"tail")
    assert out.endswith(EOI) and b"SECRET" not in out and b"tail" not in out


def test_a_baseline_jpeg_is_unchanged() -> None:
    src = SOI + JFIF + DQT + seg(0xC0, b"\x08\x00\x01\x00\x01\x01\x01\x11\x00")
    src += DHT + seg(0xDD, b"\x00\x04") + SOS + SCAN1 + EOI
    assert _scrub_jpeg(src) == src


def test_restart_markers_inside_entropy_data_are_preserved() -> None:
    scan = b"\x01\x02\xff\xd0\x03\x04\xff\xd7\x05\xff\x00\xff\xff"
    src = SOI + SOF + SOS + scan + EOI
    assert _scrub_jpeg(src) == SOI + SOF + SOS + scan[:-2] + EOI


def test_a_missing_end_marker_is_refused() -> None:
    with pytest.raises(StageError, match="end"):
        _scrub_jpeg(SOI + SOF + SOS + SCAN1)
    with pytest.raises(StageError, match="end"):
        _scrub_jpeg(SOI + SOF + SOS + SCAN1 + EXIF)


@pytest.mark.parametrize("marker", [0x02, 0x50, 0xC8, 0xF0, 0xFD, 0xD8, 0x80])
def test_an_unclassified_marker_is_refused(marker: int) -> None:
    with pytest.raises(StageError, match="marker"):
        _scrub_jpeg(SOI + SOF + seg(marker, b"x") + SOS + SCAN1 + EOI)
    with pytest.raises(StageError, match="marker"):
        _scrub_jpeg(SOI + SOF + SOS + SCAN1 + seg(marker, b"x") + SOS + SCAN2 + EOI)
