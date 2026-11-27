# SPDX-License-Identifier: Apache-2.0
"""The instance template carries ``numbers.fediverse.json`` (#942).

The refresh script, the strict validator and the refresh workflow's artifact
allow-list must carry the fediverse snapshot the engine writes beside the numbers
file, check it with the same size and key rules the engine uses, and carry nothing
else the collector leaves in the folder (the unit note, its ``.bad`` copy, staged
files)."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from reach_around.config.fediverse import MAX_INTERACTIONS, MAX_POSTS
from reach_around.config.import_guard import _NAME_PARTS, _NAME_WHOLE
from reach_around.counts import bluesky_feed, fediverse_store, mastodon_feed

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "examples" / "instance-repo"
VALIDATOR = TEMPLATE / "scripts" / "validate-numbers.py"
WORKFLOW = TEMPLATE / ".github" / "workflows" / "refresh-counts.yml"
SCRIPT = TEMPLATE / "scripts" / "refresh-counts.sh"
README = TEMPLATE / "README.md"
NAME = "numbers.fediverse.json"
BASH = shutil.which("bash") or "/bin/bash"


def _snap(kind: str = "bluesky", **over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "platform": kind,
        "as_of": "2026-11-20",
        "followers": 120,
        "posts": 40,
        "interactions": [{"boosts": 1, "favorites": 2, "replies": 3}],
        "complete": True,
    }
    base.update(over)
    return base


def _file(*snaps: dict[str, object]) -> dict[str, object]:
    return {"version": 1, "platforms": {str(s["platform"]): s for s in snaps}}


GOOD = _file(_snap("bluesky"), _snap("mastodon"))


def _run(
    src: Path, out: Path, *, fediverse: bool = True
) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, "-I", str(VALIDATOR), "--in", str(src), "--out", str(out)]
    if fediverse:
        argv.insert(3, "--fediverse")
    return subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603


def _put(tmp: Path, data: object) -> Path:
    path = tmp / "in.json"
    path.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
    return path


def test_a_good_snapshot_is_written_in_the_engines_own_format(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    done = _run(_put(tmp_path, GOOD), out)
    assert done.returncode == 0, done.stderr
    expected = fediverse_store.dump_snapshots(
        {"bluesky": _snap("bluesky"), "mastodon": _snap("mastodon")}
    )
    assert out.read_text(encoding="utf-8") == expected


def test_an_engine_file_with_no_posts_in_the_window_passes(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    snap = _snap("mastodon", interactions=[])
    assert _run(_put(tmp_path, _file(snap)), out).returncode == 0


# One entry per refusal: (name, document). Each must fail the validator, and the
# engine's own guard must agree (see the parity test below for the snapshot ones).
FILE_BAD: dict[str, object] = {
    "not json": b"{nope",
    "not utf8": b"\xff\xfe",
    "array": [],
    "extra top key": {**GOOD, "account": "x"},
    "missing version": {"platforms": {}},
    "wrong version": {"version": 2, "platforms": {}},
    "bool version": {"version": True, "platforms": {}},
    "platforms not object": {"version": 1, "platforms": []},
    "unknown platform key": _file(_snap("bluesky")) | {"platforms": {"other": {}}},
    "key and platform differ": {
        "version": 1,
        "platforms": {"bluesky": _snap("mastodon")},
    },
    "duplicate key": b'{"version": 1, "version": 1, "platforms": {}}',
    "nan": b'{"version": 1, "platforms": {}, "x": NaN}',
}

SNAP_BAD: dict[str, dict[str, object]] = {
    "extra key": _snap(extra=1),
    "handle key": _snap(handle="x"),
    "actor key": _snap(actor="x"),
    "did key": _snap(userId=1),
    "account string": _snap(reason="alice.example.com"),
    "at address": _snap(reason="at://did:plc:abc"),
    "email": _snap(reason="a@b.co"),
    "url": _snap(reason="https://x"),
    "long string": _snap(reason="a" * 41),
    "space in string": _snap(reason="a b"),
    "float followers": _snap(followers=1.5),
    "bool followers": _snap(followers=True),
    "none followers": _snap(followers=None),
    "negative followers": _snap(followers=-1),
    "huge followers": _snap(followers=10**12 + 1),
    "bad date": _snap(as_of="2026-02-30"),
    "date shape": _snap(as_of="2026-2-3"),
    "date type": _snap(as_of=20261120),
    "complete not bool": _snap(complete=1),
    "incomplete": _snap(complete=False, reason="timeout"),
    "complete with reason": _snap(reason="timeout"),
    "no interactions": {k: v for k, v in _snap().items() if k != "interactions"},
    "interactions not list": _snap(interactions={}),
    "too many rows": _snap(
        interactions=[{"boosts": 0, "favorites": 0, "replies": 0}] * (MAX_POSTS + 1)
    ),
    "row extra key": _snap(
        interactions=[{"boosts": 1, "favorites": 1, "replies": 1, "id": 1}]
    ),
    "row missing key": _snap(interactions=[{"boosts": 1, "favorites": 1}]),
    "row big count": _snap(
        interactions=[{"boosts": MAX_INTERACTIONS + 1, "favorites": 0, "replies": 0}]
    ),
    "row not dict": _snap(interactions=[1]),
    "platform wrong": _snap(platform="other"),
}


@pytest.mark.parametrize("name", sorted(FILE_BAD))
def test_a_bad_file_is_refused_and_nothing_is_written(
    name: str, tmp_path: Path
) -> None:
    out = tmp_path / "out.json"
    done = _run(_put(tmp_path, FILE_BAD[name]), out)
    assert done.returncode == 1, name
    assert "refused" in done.stderr
    assert not out.exists()


@pytest.mark.parametrize("name", sorted(SNAP_BAD))
def test_a_bad_snapshot_is_refused_and_the_engine_agrees(
    name: str, tmp_path: Path
) -> None:
    snap = SNAP_BAD[name]
    out = tmp_path / "out.json"
    done = _run(_put(tmp_path, {"version": 1, "platforms": {"bluesky": snap}}), out)
    assert done.returncode == 1, name
    assert not out.exists()
    # The engine's own check refuses the same snapshot (the stored form is a
    # complete, anonymous aggregate only).
    assert fediverse_store._valid("bluesky", snap) is False, name  # noqa: SLF001


def test_a_deeply_nested_value_is_refused(tmp_path: Path) -> None:
    nested: object = 1
    for _ in range(12):
        nested = {"a": nested}
    out = tmp_path / "out.json"
    snap = _snap(interactions=[nested])
    assert _run(_put(tmp_path, _file(snap)), out).returncode == 1


def test_one_bad_platform_refuses_the_whole_file(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    both = _file(_snap("bluesky"), _snap("mastodon", handle="x"))
    assert _run(_put(tmp_path, both), out).returncode == 1
    assert not out.exists()


def test_an_oversized_file_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    big = json.dumps(GOOD).encode() + b" " * fediverse_store.MAX_SNAPSHOT_BYTES
    done = _run(_put(tmp_path, big), out)
    assert done.returncode == 1 and "larger" in done.stderr


def test_a_file_at_the_limit_is_read(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    raw = json.dumps(GOOD).encode()
    padded = raw + b" " * (fediverse_store.MAX_SNAPSHOT_BYTES - len(raw))
    assert _run(_put(tmp_path, padded), out).returncode == 0


def test_a_symbolic_link_input_is_refused(tmp_path: Path) -> None:
    real = _put(tmp_path, GOOD)
    link = tmp_path / "link.json"
    link.symlink_to(real)
    out = tmp_path / "out.json"
    assert _run(link, out).returncode == 1
    assert not out.exists()


def test_the_plain_numbers_check_still_refuses_a_snapshot(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    assert _run(_put(tmp_path, GOOD), out, fediverse=False).returncode == 1


def _load_validator() -> Any:
    spec = importlib.util.spec_from_file_location("validate_numbers_tpl", VALIDATOR)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_validators_limits_are_the_engines_limits() -> None:
    mod = _load_validator()
    assert mod.FEDIVERSE_MAX_BYTES == fediverse_store.MAX_SNAPSHOT_BYTES
    assert mod.FEDIVERSE_MAX_POSTS == MAX_POSTS
    assert mod.FEDIVERSE_MAX_INTERACTIONS == MAX_INTERACTIONS
    assert mod.FEDIVERSE_MAX_PROFILE == bluesky_feed.MAX_COUNT
    assert mod.FEDIVERSE_MAX_PROFILE == mastodon_feed.MAX_COUNT
    assert mod.FEDIVERSE_MAX_WORD == bluesky_feed.MAX_WORD == mastodon_feed.MAX_WORD
    assert mod.FEDIVERSE_MAX_DEPTH == bluesky_feed.MAX_DEPTH == mastodon_feed.MAX_DEPTH
    assert tuple(mod.NAME_PARTS) == tuple(_NAME_PARTS)
    assert frozenset(mod.NAME_WHOLE) == frozenset(_NAME_WHOLE)
    assert frozenset(mod.FEDIVERSE_KINDS) == frozenset(fediverse_store.KINDS)
    assert frozenset(mod.FEDIVERSE_TOP_KEYS) == bluesky_feed._TOP_KEYS  # noqa: SLF001
    assert frozenset(mod.FEDIVERSE_ROW_KEYS) == bluesky_feed._ROW_KEYS  # noqa: SLF001


# ---- the refresh script -----------------------------------------------------

FAKE_DOCKER = r"""#!/bin/bash
if [ "$1" = "--version" ]; then echo "Docker version 27"; exit 0; fi
data=""; hist=""
for a in "$@"; do
  case "$a" in
    *:/data) data="${a%%:/data}" ;;
    *:/config/history) hist="${a%%:/config/history}" ;;
  esac
done
case " $* " in
  *" tokens "*) exit 0 ;;
  *" history-verify "*) exit 0 ;;
  *" refresh "*)
    # What the engine started from, for the test to read back.
    if [ -e "$data/numbers.fediverse.json" ]; then
      cp "$data/numbers.fediverse.json" "$FAKE_SEEN"
    fi
    printf '%s' "$FAKE_NUMBERS" > "$data/numbers.json"
    if [ -n "${FAKE_SNAPSHOT:-}" ]; then
      printf '%s' "$FAKE_SNAPSHOT" > "$data/numbers.fediverse.json"
    fi
    # What a collector run leaves besides the snapshot.
    printf 'u' > "$data/.numbers.json.unit"
    printf 'b' > "$data/.numbers.json.unit.bad"
    printf 's' > "$data/.numbers.fediverse.json.0123456789abcdef.staged"
    printf 'p' > "$data/.numbers.json.pending"
    exit "${FAKE_EXIT:-0}" ;;
esac
exit 1
"""

NUMBERS = (
    '{"version": 1, "platforms": {"x": {"count": 5, "as_of": "2026-11-09T00:00:00Z"}}}'
)


def _fixture(tmp: Path) -> tuple[Path, dict[str, str]]:
    root = tmp / "inst"
    shutil.copytree(TEMPLATE, root)
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    env.pop("GITHUB_OUTPUT", None)
    env["FAKE_NUMBERS"] = NUMBERS
    env["FAKE_SEEN"] = str(tmp / "seen.json")
    return root, env


def _refresh(
    root: Path, env: dict[str, str], tmp: Path, **extra: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, a copy of the template
        [
            BASH,
            str(root / "scripts" / "refresh-counts.sh"),
            "--workdir",
            str(tmp / "w"),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**env, **extra},
    )


def test_the_snapshot_is_installed_next_to_numbers_json_and_only_it(
    tmp_path: Path,
) -> None:
    root, env = _fixture(tmp_path)
    r = _refresh(root, env, tmp_path, FAKE_SNAPSHOT=json.dumps(GOOD))
    assert r.returncode == 0, r.stderr
    text = (root / NAME).read_text(encoding="utf-8")
    assert text == fediverse_store.dump_snapshots(
        {"bluesky": _snap("bluesky"), "mastodon": _snap("mastodon")}
    )
    assert (root / NAME).stat().st_mode & 0o777 == 0o644
    assert (root / "numbers.json").is_file()
    # None of the collector's own files crosses into the repository folder.
    names = {p.name for p in root.iterdir()}
    assert not [
        n for n in names if n.endswith((".unit", ".bad", ".staged", ".pending"))
    ]
    assert not [n for n in names if n.startswith(".numbers")]


def test_a_snapshot_the_check_refuses_changes_nothing(tmp_path: Path) -> None:
    root, env = _fixture(tmp_path)
    bad = json.dumps(_file(_snap("bluesky", handle="x")))
    r = _refresh(root, env, tmp_path, FAKE_SNAPSHOT=bad)
    assert r.returncode == 1
    assert "fediverse snapshot failed the check" in r.stderr
    assert not (root / NAME).exists()
    assert not (root / "numbers.json").exists()


def test_the_committed_snapshot_is_what_the_engine_starts_from(tmp_path: Path) -> None:
    root, env = _fixture(tmp_path)
    old = fediverse_store.dump_snapshots({"bluesky": _snap("bluesky", followers=7)})
    (root / NAME).write_text(old, encoding="utf-8")
    r = _refresh(root, env, tmp_path)
    assert r.returncode == 0, r.stderr
    assert Path(env["FAKE_SEEN"]).read_text(encoding="utf-8") == old
    # No new snapshot from the engine: the committed one stays as it was.
    assert (root / NAME).read_text(encoding="utf-8") == old


def test_a_run_without_a_snapshot_installs_none(tmp_path: Path) -> None:
    root, env = _fixture(tmp_path)
    r = _refresh(root, env, tmp_path)
    assert r.returncode == 0, r.stderr
    assert not (root / NAME).exists()


def test_a_symbolic_link_snapshot_in_the_repository_is_refused(tmp_path: Path) -> None:
    root, env = _fixture(tmp_path)
    target = tmp_path / "t.json"
    target.write_text("{}", encoding="utf-8")
    (root / NAME).symlink_to(target)
    r = _refresh(root, env, tmp_path, FAKE_SNAPSHOT=json.dumps(GOOD))
    assert r.returncode == 2
    assert "must not be a symbolic link" in r.stderr
    assert not (root / "numbers.json").exists()


def test_a_failed_source_still_installs_the_good_snapshot(tmp_path: Path) -> None:
    root, env = _fixture(tmp_path)
    r = _refresh(root, env, tmp_path, FAKE_SNAPSHOT=json.dumps(GOOD), FAKE_EXIT="3")
    assert r.returncode == 0, r.stderr
    assert (root / NAME).is_file()


# ---- the workflow ------------------------------------------------------------


def _jobs() -> dict[str, Any]:
    return dict(yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"])


def test_the_snapshot_crosses_in_the_artifact() -> None:
    steps = _jobs()["refresh"]["steps"]
    up = next(s for s in steps if "upload-artifact@" in str(s.get("uses")))
    paths = str(up["with"]["path"]).split()
    assert NAME in paths and "numbers.json" in paths and "history/social.jsonl" in paths
    # Nothing of the collector's own files is listed.
    assert not [
        p for p in paths if p.endswith((".unit", ".bad", ".staged", ".pending"))
    ]
    assert not [p for p in paths if "*" in p]


def _check_step() -> str:
    steps = _jobs()["publish"]["steps"]
    return str(
        next(s["run"] for s in steps if "validate-numbers.py" in s.get("run", ""))
    )


def test_the_publish_job_checks_the_snapshot_again_and_allows_only_known_sets() -> None:
    check = _check_step()
    assert "--fediverse" in check and NAME in check
    # The allow-list case names every combination, with and without the snapshot.
    assert "./numbers.fediverse.json ./numbers.json " in check
    assert '"./numbers.json "' in check
    full = "./history ./history/social.jsonl ./numbers.fediverse.json ./numbers.json "
    assert full in check
    assert 'find "$in" -type l' in check
    # The check writes a fresh copy, and runs before the commit.
    runs = [s.get("run", "") for s in _jobs()["publish"]["steps"]]
    verify_at = next(i for i, r in enumerate(runs) if "--fediverse" in r)
    commit_at = next(i for i, r in enumerate(runs) if "commit -m" in r)
    assert verify_at < commit_at


def test_the_snapshot_is_proposed_with_the_numbers_and_nothing_else() -> None:
    steps = _jobs()["publish"]["steps"]
    propose = next(s["run"] for s in steps if "commit -m" in s.get("run", ""))
    assert f"git add {NAME}" in propose
    assert NAME in propose.split("git status --porcelain", 1)[1].splitlines()[0]
    assert "git add -A" not in propose and "git add ." not in propose
    # A pull request on the bot branch may change the snapshot too.
    assert f'"numbers.json", "{NAME}", "history/social.jsonl"' in propose


def test_the_egress_check_has_nothing_to_check_in_the_template() -> None:
    """The template's refresh job has no allowed-endpoints list (no runtime egress
    firewall step), so ``counts egress-check`` has nothing to read and is not a
    step. The README says so, and says where the check belongs if an instance adds
    such a step."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "allowed-endpoints" not in text and "harden-runner" not in text
    readme = README.read_text(encoding="utf-8")
    assert "counts egress-check" in readme and NAME in readme


def test_the_build_mounts_the_snapshot_beside_the_numbers_file() -> None:
    text = (TEMPLATE / "scripts" / "validate.sh").read_text(encoding="utf-8")
    assert f"numbers.json {NAME}" in text
