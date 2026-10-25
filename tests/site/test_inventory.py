# SPDX-License-Identifier: Apache-2.0
"""The published-file inventory and the purge list computed from two of them."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from assetpaths import asset
from reach_around.config import load_site_config
from reach_around.site.build import build_site
from reach_around.site.inventory import (
    MAX_ENTRIES,
    InventoryEntry,
    InventoryError,
    build_inventory,
    inventory_json,
    load_inventory,
    parse_inventory,
    purge_paths,
)

TODAY = date(2027, 3, 10)
EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "instance" / "site.yaml"
SHA_A = "a" * 64
SHA_B = "b" * 64


def entry(kind: str = "stable", sha: str = SHA_A, size: int = 1) -> InventoryEntry:
    return InventoryEntry(sha256=sha, size=size, kind=kind)


def built(tmp_path: Path) -> tuple[Path, dict[str, InventoryEntry]]:
    out = tmp_path / "out"
    manifest = build_site(
        load_site_config(EXAMPLE), out, today=TODAY, assets_dir=EXAMPLE.parent
    )
    return out, manifest.inventory


# ------------------------------------------------------------- contents


def test_the_inventory_lists_every_served_file_with_hash_size_and_class(
    tmp_path: Path,
) -> None:
    out, inventory = built(tmp_path)
    served = {
        p.relative_to(out).as_posix()
        for p in out.rglob("*")
        if p.is_file() and p.name != "_headers"
    }
    assert set(inventory) == served
    assert "_headers" not in inventory
    for rel, item in inventory.items():
        data = (out / rel).read_bytes()
        assert item.sha256 == hashlib.sha256(data).hexdigest(), rel
        assert item.size == len(data), rel


def test_classes_split_hashed_from_stable(tmp_path: Path) -> None:
    out, inventory = built(tmp_path)
    assert inventory[asset(out, "static/site.css")].kind == "hashed"
    assert inventory[asset(out, "media/mascot.svg")].kind == "hashed"
    for page in ("index.html", "404.html", "privacy/index.html"):
        assert inventory[page].kind == "stable", page
    kinds = {item.kind for item in inventory.values()}
    assert kinds == {"hashed", "stable"}
    assert all(
        item.kind == "hashed"
        for rel, item in inventory.items()
        if rel.startswith("static/")
    )


def test_inventory_json_is_sorted_and_round_trips(tmp_path: Path) -> None:
    _, inventory = built(tmp_path)
    text = inventory_json(inventory)
    data = json.loads(text)
    assert list(data) == ["files", "version"]
    assert list(data["files"]) == sorted(data["files"])
    first = next(iter(data["files"].values()))
    assert sorted(first) == ["class", "sha256", "size"]
    assert parse_inventory(text) == inventory
    assert text == inventory_json(parse_inventory(text))


def test_build_inventory_marks_only_the_named_paths_hashed() -> None:
    files = {
        Path("a.html"): b"x",
        Path("s/a.1234567890.css"): b"y",
        Path("_headers"): b"z",
    }
    inv = build_inventory(files, [Path("s/a.1234567890.css")])
    assert set(inv) == {"a.html", "s/a.1234567890.css"}
    assert inv["a.html"].kind == "stable"
    assert inv["s/a.1234567890.css"].kind == "hashed"


# ----------------------------------------------------------- hostile input


def good(**over: object) -> dict[str, object]:
    files = {"index.html": {"class": "stable", "sha256": SHA_A, "size": 3}}
    body: dict[str, object] = {"version": 1, "files": files}
    body.update(over)
    return body


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[]",
        "null",
        "{",
        '{"version": 1, "files": {}, "version": 1}',
        '{"version": 2, "files": {}}',
        '{"version": 1}',
        '{"version": 1, "files": [], "extra": 1}',
        '{"version": 1, "files": {}, "extra": 1}',
        "[" * 100000,
    ],
)
def test_the_parser_refuses_malformed_documents(text: str) -> None:
    with pytest.raises(InventoryError):
        parse_inventory(text)


@pytest.mark.parametrize(
    "path",
    [
        "../etc/passwd",
        "/abs",
        "a/../b",
        "a//b",
        "a b",
        "a\nb",
        "a%2e%2e/b",
        "",
        "a/./b",
        "ü.html",
        "a/b/",
        "x" * 0 + ".",
        "..",
    ],
)
def test_the_parser_refuses_paths_that_are_not_plain(path: str) -> None:
    body = good()
    body["files"] = {path: {"class": "stable", "sha256": SHA_A, "size": 1}}
    with pytest.raises(InventoryError):
        parse_inventory(json.dumps(body))


@pytest.mark.parametrize(
    "item",
    [
        {"class": "other", "sha256": SHA_A, "size": 1},
        {"class": "stable", "sha256": "A" * 64, "size": 1},
        {"class": "stable", "sha256": "a" * 63, "size": 1},
        {"class": "stable", "sha256": SHA_A, "size": -1},
        {"class": "stable", "sha256": SHA_A, "size": True},
        {"class": "stable", "sha256": SHA_A, "size": 1.5},
        {"class": "stable", "sha256": SHA_A},
        {"class": "stable", "sha256": SHA_A, "size": 1, "note": "x"},
        "stable",
        None,
    ],
)
def test_the_parser_refuses_bad_entries(item: object) -> None:
    body = good()
    body["files"] = {"index.html": item}
    with pytest.raises(InventoryError):
        parse_inventory(json.dumps(body))


def test_the_parser_caps_the_entry_count() -> None:
    entry_json = {"class": "stable", "sha256": SHA_A, "size": 1}
    body = {
        "version": 1,
        "files": {f"f{i}.txt": entry_json for i in range(MAX_ENTRIES + 1)},
    }
    with pytest.raises(InventoryError, match="entries"):
        parse_inventory(json.dumps(body))


def test_load_refuses_a_missing_file_a_directory_and_a_huge_file(
    tmp_path: Path,
) -> None:
    with pytest.raises(InventoryError):
        load_inventory(tmp_path / "missing.json")
    with pytest.raises(InventoryError):
        load_inventory(tmp_path)
    big = tmp_path / "big.json"
    big.write_bytes(b" " * (4 * 1024 * 1024 + 1))
    with pytest.raises(InventoryError, match="larger"):
        load_inventory(big)
    binary = tmp_path / "bin.json"
    binary.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(InventoryError):
        load_inventory(binary)


def test_load_reads_what_the_build_wrote(tmp_path: Path) -> None:
    _, inventory = built(tmp_path)
    path = tmp_path / "inv.json"
    path.write_text(inventory_json(inventory), encoding="utf-8")
    assert load_inventory(path) == inventory


# ------------------------------------------------------------ purge list


def test_a_removed_file_is_purged() -> None:
    previous = {"manifest.json": entry(), "index.html": entry()}
    current = {"index.html": entry()}
    assert "/manifest.json" in purge_paths(previous, current)


def test_a_removed_page_is_purged_at_both_urls() -> None:
    previous = {"old/index.html": entry(), "index.html": entry()}
    urls = purge_paths(previous, {"index.html": entry()})
    assert "/old/" in urls and "/old/index.html" in urls


def test_a_stable_file_whose_bytes_changed_is_purged() -> None:
    previous = {"apple-touch-icon.png": entry(sha=SHA_A)}
    current = {"apple-touch-icon.png": entry(sha=SHA_B)}
    assert "/apple-touch-icon.png" in purge_paths(previous, current)


def test_an_unchanged_stable_file_is_not_purged() -> None:
    same = {"apple-touch-icon.png": entry()}
    assert "/apple-touch-icon.png" not in purge_paths(same, same)


def test_a_new_or_kept_hashed_file_is_not_purged() -> None:
    name = "static/site.1234567890.css"
    kept = {name: entry("hashed")}
    assert name not in " ".join(purge_paths(kept, kept))
    assert "/" + name not in purge_paths({}, kept)


def test_entry_points_are_always_purged() -> None:
    current = {
        "index.html": entry(),
        "start/index.html": entry(),
        "404.html": entry(),
        "privacy/index.html": entry(),
    }
    urls = purge_paths(current, current)
    assert urls == sorted({"/", "/start/", "/404.html", "/privacy/", "/robots.txt"})


def test_the_list_is_sorted_and_has_no_duplicates() -> None:
    previous = {"a/index.html": entry(), "b.txt": entry()}
    urls = purge_paths(previous, {})
    assert urls == sorted(set(urls))


def test_a_real_rebuild_with_a_changed_icon_purges_no_hashed_file(
    tmp_path: Path,
) -> None:
    out, first = built(tmp_path)
    second = dict(first)
    renamed = asset(out, "static/site.css")
    second.pop(renamed)
    second["static/site.0000000000.css"] = entry("hashed")
    urls = purge_paths(first, second)
    # the old stylesheet is gone from the build, so it is listed; new hashes are not
    assert "/" + renamed in urls
    assert "/static/site.0000000000.css" not in urls
