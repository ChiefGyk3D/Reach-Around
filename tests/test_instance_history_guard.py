# SPDX-License-Identifier: Apache-2.0
"""The instance template's history guard (issue #619): the workflow keeps its
promises, the label and approver logic is exact, and a labeled tombstone-only
rewrite is accepted end to end while every other rewrite is refused. The GitHub
API is faked; the real engine compare runs."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import types
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from reach_around.cli import main as engine_main
from reach_around.counts.history import (
    Record,
    append_records,
    plan_redaction,
    read_history,
    redact_records,
    rewrite_metadata,
)

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "examples" / "instance-repo"
WORKFLOW = TEMPLATE / ".github" / "workflows" / "history-guard.yml"
GUARD_PY = TEMPLATE / "scripts" / "history-guard.py"
WRAPPER = TEMPLATE / "scripts" / "verify-history-rewrite.sh"
GIT = shutil.which("git") or "/usr/bin/git"
BASH = "/bin/bash"

NOW = datetime(2026, 12, 31, 12, 0, 0, tzinfo=UTC)
REDACT_AT = datetime(2026, 11, 25, 12, 0, 0, tzinfo=UTC)
SECRET = 424242424


def load_guard() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("history_guard_under_test", GUARD_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = load_guard()


# --- the workflow ----------------------------------------------------------


def workflow() -> dict[Any, Any]:
    data: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return data


def test_the_triggers_are_exactly_pull_request_with_five_types() -> None:
    data = workflow()
    # YAML 1.1 reads the key `on` as True.
    triggers: dict[str, Any] = data["on"] if "on" in data else data[True]
    assert set(triggers) == {"pull_request"}
    assert set(triggers["pull_request"]["types"]) == {
        "opened",
        "synchronize",
        "reopened",
        "labeled",
        "unlabeled",
    }
    assert triggers["pull_request"]["branches"] == ["main"]


def test_one_job_named_history_guard_that_cannot_write_and_holds_no_secret() -> None:
    data = workflow()
    assert list(data["jobs"]) == ["history-guard"]
    job = data["jobs"]["history-guard"]
    assert job["name"] == "History guard"
    assert data["permissions"] == {"contents": "read"}
    assert job["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
        "actions": "read",  # the workflow runs of the head commit (head binding)
    }
    body = yaml.safe_dump(data["jobs"])  # data only: comments are not read
    for bad in (": write", "secrets.", "continue-on-error", "environment", "paths"):
        assert bad not in body, bad
    assert "paths" not in yaml.safe_dump(data["on" if "on" in data else True])
    assert data["concurrency"]["cancel-in-progress"] is True
    assert "pull_request.number" in data["concurrency"]["group"]


def test_scripts_and_the_approvers_come_from_the_base_commit_not_the_pull_request() -> (
    None
):
    steps = workflow()["jobs"]["history-guard"]["steps"]
    base = steps[0]["with"]
    assert base["ref"] == "${{ github.event.pull_request.base.sha }}"
    assert "scripts/history-guard.py" in base["sparse-checkout"]
    guard_step = steps[-1]
    assert guard_step["run"].startswith(
        'python3 -I "$TRUSTED/scripts/history-guard.py"'
    )
    assert "pr/scripts" not in guard_step["run"]
    assert all("${{" not in step.get("run", "") for step in steps)
    for step in steps:
        if "uses" in step:
            assert step["with"]["persist-credentials"] is False


def test_the_untrusted_names_reach_the_script_only_through_env() -> None:
    step = workflow()["jobs"]["history-guard"]["steps"][-1]
    assert step["env"]["HEAD_REF"] == "${{ github.event.pull_request.head.ref }}"
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"  # noqa: S105 - not a secret


def test_the_approvers_file_ships_empty() -> None:
    text = (TEMPLATE / ".github" / "history-approvers").read_text(encoding="utf-8")
    assert guard.parse_approvers(text) == frozenset()


# --- pure rules ------------------------------------------------------------


def ev(
    kind: str, login: str, when: str, ident: int, label: str = "history-tombstone"
) -> dict[str, Any]:
    return {
        "id": ident,
        "event": kind,
        "actor": {"login": login},
        "created_at": when,
        "label": {"name": label},
    }


LABELS = [{"name": "history-tombstone"}]
OLD_ENOUGH = "2026-11-20T00:00:00Z"
NOW_G = datetime(2026, 11, 25, 12, 0, 0, tzinfo=UTC)
HEAD_BEFORE_LABEL = datetime(2026, 11, 19, 0, 0, 0, tzinfo=UTC)


def auth(**over: Any) -> tuple[Any, ...]:
    args: dict[str, Any] = {
        "events": [ev("labeled", "Owner", OLD_ENOUGH, 7)],
        "labels": LABELS,
        "approvers": frozenset({"owner"}),
        "now": NOW_G,
        "same_repo": True,
        "head_ref": "redact/old-rows",
        "head_changed": HEAD_BEFORE_LABEL,
    }
    args.update(over)
    result: tuple[Any, ...] = guard.authorize(**args)
    return result


def test_a_listed_applier_with_an_old_label_on_a_redact_branch_is_authorized() -> None:
    assert auth() == (
        7,
        "owner",
        "2026-11-20T00:00:00+00:00",
        "2026-11-19T00:00:00+00:00",
    )


@pytest.mark.parametrize(
    "changed",
    [
        datetime(2026, 11, 20, 0, 0, 0, tzinfo=UTC),  # the very instant of the label
        datetime(2026, 11, 20, 0, 0, 1, tzinfo=UTC),  # a second after it
        datetime(2026, 11, 25, 11, 0, 0, tzinfo=UTC),  # long after, an hour ago
    ],
)
def test_a_label_applied_before_the_head_last_changed_is_refused(
    changed: datetime,
) -> None:
    with pytest.raises(guard.Refused, match="head changed"):
        auth(head_changed=changed)


@pytest.mark.parametrize(
    "over",
    [
        {"labels": []},  # no label now
        {"labels": [{"name": "history-tombstones"}]},  # look-alike
        {"labels": [{"name": "History-Tombstone"}]},
        {"events": []},
        {"events": [ev("labeled", "someone", OLD_ENOUGH, 7)]},  # not listed
        {"events": [ev("labeled", "github-actions[bot]", OLD_ENOUGH, 7)]},
        {"events": [ev("labeled", "Owner", "2026-11-25T00:00:00Z", 7)]},  # too young
        {
            "events": [
                ev("labeled", "Owner", OLD_ENOUGH, 7),
                ev("unlabeled", "Owner", "2026-11-21T00:00:00Z", 8),
            ]
        },
        {
            "events": [
                ev("labeled", "Owner", OLD_ENOUGH, 7),
                ev("labeled", "someone", "2026-11-21T00:00:00Z", 8),
            ]
        },
        {"events": [ev("labeled", "Owner", OLD_ENOUGH, 7, label="other")]},
        {"approvers": frozenset()},
        {"same_repo": False},
        {"head_ref": "counts/refresh"},
        {"head_ref": "feature/x"},
        {"head_ref": "redact"},
    ],
)
def test_every_other_label_situation_is_refused(over: dict[str, Any]) -> None:
    with pytest.raises(guard.Refused):
        auth(**over)


def test_the_last_event_counts_even_when_the_list_is_out_of_order() -> None:
    events = [
        ev("unlabeled", "Owner", "2026-11-21T00:00:00Z", 8),
        ev("labeled", "Owner", OLD_ENOUGH, 7),
    ]
    with pytest.raises(guard.Refused):
        auth(events=events)
    relabeled = [*events, ev("labeled", "Owner", "2026-11-22T00:00:00Z", 9)]
    assert auth(events=relabeled)[0] == 9


def test_an_approver_promoted_after_labeling_is_judged_by_the_file_now() -> None:
    events = [ev("labeled", "newcomer", OLD_ENOUGH, 7)]
    with pytest.raises(guard.Refused):
        auth(events=events)
    assert auth(events=events, approvers=frozenset({"newcomer"}))[1] == "newcomer"


def test_the_approvers_file_parser_is_strict() -> None:
    assert guard.parse_approvers("# c\n\nOwner # me\nother-1\n") == {"owner", "other-1"}
    for bad in ("two words", "-lead", "a" * 40, "bot[bot]", "o/wner", "@owner"):
        with pytest.raises(guard.Refused):
            guard.parse_approvers(bad + "\n")


def pr(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "state": "open",
        "base": {"ref": "main", "repo": {"full_name": "me/inst"}},
        "head": {"ref": "counts/refresh", "repo": {"full_name": "me/inst"}},
        "user": {"login": "github-actions[bot]"},
    }
    base.update(over)
    return base


def test_only_an_open_bot_refresh_pull_request_of_this_repository_blocks() -> None:
    assert guard.refresh_pr_blocks(pr(), "me/inst")
    fork = pr(head={"ref": "counts/refresh", "repo": {"full_name": "x/inst"}})
    other = pr(user={"login": "someone"})
    elsewhere = pr(base={"ref": "dev", "repo": {"full_name": "me/inst"}})
    closed = pr(state="closed")
    renamed = pr(head={"ref": "counts/other", "repo": {"full_name": "me/inst"}})
    for item in (fork, other, elsewhere, closed, renamed):
        assert not guard.refresh_pr_blocks(item, "me/inst")


def change(
    status: str, path: str, old: str = "100644", new: str = "100644"
) -> tuple[str, str, str, str]:
    return (status, path, old, new)


MANIFEST_PATH = "history/redactions/redact-20261125-abcdef.manifest"
GOOD = [
    change("M", "history/social.jsonl"),
    change("M", "numbers.json"),
    change("A", MANIFEST_PATH, "000000"),
]


def test_the_allowed_paths_are_exactly_three() -> None:
    assert guard.check_paths(GOOD) == MANIFEST_PATH
    assert guard.check_paths([GOOD[0], GOOD[2]]) == MANIFEST_PATH
    deleted = change("D", "numbers.json", "100644", "000000")
    assert guard.check_paths([GOOD[0], deleted, GOOD[2]]) == MANIFEST_PATH


@pytest.mark.parametrize(
    "changed",
    [
        [*GOOD, change("M", "site.yaml")],
        [*GOOD, change("M", ".github/history-approvers")],
        [*GOOD, change("M", "scripts/history-guard.py")],
        [*GOOD, change("M", ".github/workflows/history-guard.yml")],
        [*GOOD, change("A", "history/redactions/second.manifest", "000000")],
        [*GOOD, change("M", MANIFEST_PATH)],
        [GOOD[0], GOOD[1]],  # no manifest
        [GOOD[2]],  # no history file
        [change("M", "history/social.jsonl", "100644", "120000"), GOOD[2]],  # symlink
        [change("M", "history/social.jsonl", "100644", "100755"), GOOD[2]],
        [change("D", "history/social.jsonl", "100644", "000000"), GOOD[2]],
        [GOOD[0], change("A", "history/redactions/x.txt", "000000")],
        [GOOD[0], change("A", "history/redactions/../x.manifest", "000000")],
        [GOOD[0], change("A", "history/redactions/sub/x.manifest", "000000")],
        [GOOD[0], change("A", MANIFEST_PATH, "000000", "120000")],
        [GOOD[0], change("M", "numbers.json", "100644", "120000"), GOOD[2]],
    ],
)
def test_any_other_change_set_is_refused(
    changed: list[tuple[str, str, str, str]],
) -> None:
    with pytest.raises(guard.Refused):
        guard.check_paths(changed)


def test_the_raw_diff_parser_reads_modes_and_refuses_junk() -> None:
    raw = (
        b":100644 100644 aaaaaaa bbbbbbb M\0history/social.jsonl\0"
        b":000000 100644 0000000 ccccccc A\0history/redactions/r.manifest\0"
    )
    assert guard.parse_raw_diff(raw) == [
        ("M", "history/social.jsonl", "100644", "100644"),
        ("A", "history/redactions/r.manifest", "000000", "100644"),
    ]
    with pytest.raises(guard.Refused):
        guard.parse_raw_diff(b":100644 M\0x\0")


class FakeReply:
    def __init__(self, body: bytes, link: str = "") -> None:
        self.body = body
        self.headers = {"Link": link}

    def read(self, _: int) -> bytes:
        return self.body

    def __enter__(self) -> FakeReply:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def test_every_page_is_read_and_a_foreign_or_failed_page_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = {
        f"{guard.API}/e?per_page=100": FakeReply(
            b'[{"id":1}]', f'<{guard.API}/e?page=2>; rel="next"'
        ),
        f"{guard.API}/e?page=2": FakeReply(b'[{"id":2}]'),
    }
    monkeypatch.setattr(
        guard.urllib.request, "urlopen", lambda req, timeout: pages[req.full_url]
    )
    assert guard.api_get("/e?per_page=100", "t") == [{"id": 1}, {"id": 2}]
    pages[f"{guard.API}/e?per_page=100"] = FakeReply(
        b'[{"id":1}]', '<https://evil.example/e?page=2>; rel="next"'
    )
    with pytest.raises(guard.Refused):
        guard.api_get("/e?per_page=100", "t")

    def boom(req: Any, timeout: int) -> Any:
        raise guard.urllib.error.URLError("down")

    monkeypatch.setattr(guard.urllib.request, "urlopen", boom)
    with pytest.raises(guard.Refused):
        guard.api_get("/e", "t")
    forever = FakeReply(b"[]", f'<{guard.API}/e>; rel="next"')
    monkeypatch.setattr(guard.urllib.request, "urlopen", lambda req, timeout: forever)
    with pytest.raises(guard.Refused):
        guard.api_get("/e", "t")


def test_a_next_link_must_keep_the_endpoint_path_and_only_change_the_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = f"{guard.API}/repos/me/inst/pulls/5/commits?per_page=100"
    same_path = f"{guard.API}/repos/me/inst/pulls/5/commits?per_page=100&page=2"
    other = (
        f"{guard.API}/repos/me/other/pulls/5/commits?page=2",  # another repository
        f"{guard.API}/repos/me/inst/issues/5/events?page=2",  # another endpoint
        f"{guard.API}/repos/me/inst/pulls/5/commits/../../x?page=2",
        "http://api.github.com/repos/me/inst/pulls/5/commits?page=2",  # not https
        "https://api.github.com:444/repos/me/inst/pulls/5/commits?page=2",
        "https://user@api.github.com/repos/me/inst/pulls/5/commits?page=2",
    )
    pages: dict[str, FakeReply] = {same_path: FakeReply(b'[{"id":2}]')}
    monkeypatch.setattr(
        guard.urllib.request, "urlopen", lambda req, timeout: pages[req.full_url]
    )
    pages[first] = FakeReply(b'[{"id":1}]', f'<{same_path}>; rel="next"')
    assert guard.api_get("/repos/me/inst/pulls/5/commits?per_page=100", "t") == [
        {"id": 1},
        {"id": 2},
    ]
    for bad in other:
        pages[first] = FakeReply(b'[{"id":1}]', f'<{bad}>; rel="next"')
        pages[bad] = FakeReply(b'[{"id":99}]')  # must never be fetched
        with pytest.raises(guard.Refused):
            guard.api_get("/repos/me/inst/pulls/5/commits?per_page=100", "t")


def test_the_actions_listing_object_is_paged_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = f"{guard.API}/r/actions/runs?per_page=1"
    pages = {
        first: FakeReply(
            b'{"total_count":2,"workflow_runs":[{"id":1}]}',
            f'<{guard.API}/r/actions/runs?page=2>; rel="next"',
        ),
        f"{guard.API}/r/actions/runs?page=2": FakeReply(
            b'{"total_count":2,"workflow_runs":[{"id":2}]}'
        ),
    }
    monkeypatch.setattr(
        guard.urllib.request, "urlopen", lambda req, timeout: pages[req.full_url]
    )
    got = guard.api_get("/r/actions/runs?per_page=1", "t")
    assert isinstance(got, dict) and got["total_count"] == 2
    assert [r["id"] for r in got["workflow_runs"]] == [1, 2]
    # A plain object (the ref listing) is one answer, as before.
    pages[first] = FakeReply(b'{"object":{"sha":"x"}}')
    assert guard.api_get("/r/actions/runs?per_page=1", "t") == {"object": {"sha": "x"}}
    # An endless chain of pages hits the cap and refuses.
    forever = FakeReply(
        b'{"total_count":9,"workflow_runs":[{"id":1}]}',
        f'<{guard.API}/r/actions/runs>; rel="next"',
    )
    monkeypatch.setattr(guard.urllib.request, "urlopen", lambda req, timeout: forever)
    with pytest.raises(guard.Refused):
        guard.api_get("/r/actions/runs", "t")
    # A shape that changes between pages refuses.
    mixed = {
        first: FakeReply(
            b'{"total_count":2,"workflow_runs":[{"id":1}]}',
            f'<{guard.API}/r/actions/runs?page=2>; rel="next"',
        ),
        f"{guard.API}/r/actions/runs?page=2": FakeReply(b'[{"id":2}]'),
    }
    monkeypatch.setattr(
        guard.urllib.request, "urlopen", lambda req, timeout: mixed[req.full_url]
    )
    with pytest.raises(guard.Refused):
        guard.api_get("/r/actions/runs?per_page=1", "t")


# --- the wrapper's exit mapping -------------------------------------------


def stub_runtime(folder: Path, status: int) -> dict[str, str]:
    bin_dir = folder / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(f"#!/bin/sh\nexit {status}\n", encoding="utf-8")
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return env


def wrapper_tree(folder: Path) -> Path:
    tree = folder / "tree"
    (tree / "scripts").mkdir(parents=True)
    shutil.copy(WRAPPER, tree / "scripts" / WRAPPER.name)
    lock = tree / "scripts" / "read-lock.sh"
    lock.write_text(
        "#!/bin/sh\necho example.test/engine@sha256:" + "0" * 64 + "\n",
        encoding="utf-8",
    )
    lock.chmod(0o755)
    return tree


@pytest.mark.parametrize(
    "status,expect_ok,mode",
    [(0, True, "append"), (10, True, "rewrite")]
    + [(s, False, "") for s in (1, 2, 3, 5, 9, 11, 124, 125, 126, 127, 137, 143)],
)
def test_the_wrapper_accepts_only_status_0_and_10(
    tmp_path: Path, status: int, expect_ok: bool, mode: str
) -> None:
    tree = wrapper_tree(tmp_path)
    (tmp_path / "o").write_bytes(b"")
    (tmp_path / "n").write_bytes(b"")
    result = subprocess.run(  # noqa: S603 - fixed argv, stub runtime on PATH
        [
            BASH,
            str(tree / "scripts" / WRAPPER.name),
            str(tmp_path / "o"),
            str(tmp_path / "n"),
        ],
        env=stub_runtime(tmp_path, status),
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is expect_ok
    assert (f"mode={mode}" in result.stdout) is expect_ok
    if not expect_ok:
        assert "mode=" not in result.stdout


def test_the_wrapper_refuses_a_link_an_unknown_option_and_a_missing_input(
    tmp_path: Path,
) -> None:
    tree = wrapper_tree(tmp_path)
    real = tmp_path / "real"
    real.write_bytes(b"")
    link = tmp_path / "link"
    link.symlink_to(real)
    script = str(tree / "scripts" / WRAPPER.name)
    env = stub_runtime(tmp_path, 0)
    for args in (
        [str(link), str(real)],
        [str(real), str(tmp_path / "missing")],
        [str(real), str(real), "--bogus"],
        [str(real), str(real), "--manifest", str(link)],
    ):
        result = subprocess.run(  # noqa: S603 - fixed argv
            [BASH, script, *args], env=env, capture_output=True, text=True, check=False
        )
        assert result.returncode != 0, args
        assert "mode=" not in result.stdout


def test_the_wrapper_runs_the_engine_offline_read_only_and_bounded() -> None:
    text = WRAPPER.read_text(encoding="utf-8")
    for flag in (
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "no-new-privileges:true",
        "--pids-limit 64",
        "--memory 512m",
        ":/in:ro",
        "timeout 120",
        "rc=0",
        "|| rc=$?",
        'case "$rc" in',
    ):
        assert flag in text, flag


# --- the whole guard, end to end, with a real git repository --------------


def rec(**over: Any) -> Record:
    fields: dict[str, Any] = {
        "platform": "youtube",
        "metric": "subscribers",
        "value": 10,
        "as_of_utc": "2026-10-01T09:00:00Z",
        "source": "api",
        "run_id": "run-1",
    }
    fields.update(over)
    return Record(**fields)


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603 - fixed argv in a temp repo
        [GIT, "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


NUMBERS_BASE = (
    '{"platforms":{"threads":{"as_of":"2026-10-02T09:00:00Z","count":'
    + str(SECRET)
    + '},"youtube":{"as_of":"2026-10-30T09:00:00Z","count":50030}},"version":1}\n'
)
NUMBERS_HEAD = (
    '{"platforms":{"youtube":{"as_of":"2026-10-30T09:00:00Z","count":50030}},'
    '"version":1}\n'
)


class World:
    """A repository with a base commit and a rewrite on a redact/ branch."""

    def __init__(self, tmp_path: Path, *, source: str = "api") -> None:
        self.dir = tmp_path / "pr"
        self.dir.mkdir()
        (self.dir / "history").mkdir()
        (self.dir / ".github").mkdir()
        path = self.dir / "history" / "social.jsonl"
        for n in range(1, 31):
            append_records(
                path,
                [rec(as_of_utc=f"2026-10-{n:02d}T09:00:00Z", value=50000 + n)],
                now=NOW,
            )
        append_records(
            path,
            [
                rec(
                    platform="threads",
                    metric="followers",
                    as_of_utc="2026-10-02T09:00:00Z",
                    source=source,
                    value=SECRET,
                )
            ],
            now=NOW,
        )
        (self.dir / "numbers.json").write_text(NUMBERS_BASE, encoding="utf-8")
        (self.dir / ".github" / "history-approvers").write_text(
            "owner\n", encoding="utf-8"
        )
        git(self.dir, "init", "-q", "-b", "main")
        git(self.dir, "add", ".")
        git(self.dir, "commit", "-q", "-m", "base")
        self.base = git(self.dir, "rev-parse", "HEAD")
        git(self.dir, "switch", "-q", "-c", "redact/old-rows")
        hand = source != "api"
        plan = plan_redaction(
            read_history(path),
            platform="threads",
            source=source,
            include_hand_kept=hand,
        )
        redact_records(
            path,
            plan,
            reason="platform_expiry",
            trigger_date="2026-10-20",
            now=REDACT_AT,
            confirm=True,
            include_hand_kept=hand,
            derived=True,
        )
        run_id, _, text = rewrite_metadata(
            [r.key for r in plan], "platform_expiry", "2026-10-20", "2026-11-25"
        )
        (self.dir / "history" / "redactions").mkdir()
        manifest = self.dir / "history" / "redactions" / f"{run_id}.manifest"
        manifest.write_text(text, encoding="ascii")
        (self.dir / "numbers.json").write_text(NUMBERS_HEAD, encoding="utf-8")
        self.head = ""
        self.commit()

    def commit(self) -> None:
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-q", "-m", "head")
        self.head = git(self.dir, "rev-parse", "HEAD")

    def write(self, path: str, text: str) -> None:
        target = self.dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.commit()


@pytest.fixture
def trusted(tmp_path: Path) -> Path:
    folder = tmp_path / "trusted"
    (folder / "scripts").mkdir(parents=True)
    return folder


def run_of(
    head: str,
    created: str,
    *,
    number: int = 5,
    repo: str = "me/inst",
    event: str = "pull_request",
) -> dict[str, Any]:
    """A workflow run as the API lists it."""
    return {
        "head_sha": head,
        "event": event,
        "created_at": created,
        "head_repository": {"full_name": repo},
        "pull_requests": [{"number": number}],
    }


class Api:
    """The faked GitHub answers, shared by one guard run."""

    def __init__(self, world: World) -> None:
        self.events = [ev("labeled", "owner", "2026-11-20T00:00:00Z", 7)]
        self.labels: list[dict[str, Any]] = [{"name": "history-tombstone"}]
        self.pulls: list[dict[str, Any]] = []
        # The head was pushed (its first pull_request run) before the label.
        self.runs: list[dict[str, Any]] = [run_of(world.head, "2026-11-19T00:00:00Z")]
        self.total: int | None = None  # None: the listing is complete
        self.tip = world.base
        self.calls = 0
        self.seen: dict[str, int] = {}
        # kind ("events" or "tip") and how often it was read -> a replacement.
        self.hook: Callable[[str, int], Any] | None = None

    def get(self, path: str, token: str) -> Any:
        self.calls += 1
        answer: Any
        if "/events" in path:
            kind, answer = "events", self.events
        elif "/labels" in path:
            kind, answer = "labels", self.labels
        elif "/pulls" in path:
            kind, answer = "pulls", self.pulls
        elif "/actions/runs" in path:
            kind = "runs"
            total = len(self.runs) if self.total is None else self.total
            answer = {"total_count": total, "workflow_runs": self.runs}
        elif "/git/ref/heads/main" in path:
            kind, answer = "tip", {"object": {"sha": self.tip}}
        else:
            raise AssertionError(path)
        self.seen[kind] = self.seen.get(kind, 0) + 1
        if self.hook is not None:
            replaced = self.hook(kind, self.seen[kind])
            if replaced is not None:
                return replaced
        return answer


CLOCK = datetime(2026, 11, 25, 12, 0, 0, tzinfo=UTC)


def run_guard(
    monkeypatch: pytest.MonkeyPatch,
    world: World,
    api: Api,
    trusted_dir: Path,
    *,
    head_repo: str = "me/inst",
    head_ref: str = "redact/old-rows",
    clock: datetime = CLOCK,
) -> int:
    for name, value in {
        "GH_TOKEN": "t",
        "REPO": "me/inst",
        "PR_NUMBER": "5",
        "HEAD_SHA": world.head,
        "BASE_SHA": world.base,
        "HEAD_REPO": head_repo,
        "HEAD_REF": head_ref,
        "PR_CREATED_AT": "2026-11-25T08:00:00Z",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(guard, "api_get", api.get)
    monkeypatch.setattr(guard, "utcnow", lambda: clock)

    def engine(_: Path, *args: str) -> tuple[bool, str]:
        # The wrapper's own work: name the inputs and the manifest.
        argv = ["counts", "history-compare", "--old", args[0], "--new", args[1]]
        rest = list(args[2:])
        argv += rest
        if "--manifest" in rest:
            argv += ["--manifest-name", Path(rest[rest.index("--manifest") + 1]).name]
        code = engine_main(argv)
        return code in (0, 10), {0: "append", 10: "rewrite"}.get(code, "")

    monkeypatch.setattr(guard, "run_compare", engine)
    return int(guard.guard(trusted_dir, world.dir))


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def refused(
    monkeypatch: pytest.MonkeyPatch,
    world: World,
    api: Api,
    trusted_dir: Path,
    **kw: Any,
) -> None:
    with pytest.raises(guard.Refused):
        run_guard(monkeypatch, world, api, trusted_dir, **kw)


def test_a_labeled_tombstone_only_rewrite_is_accepted_and_logged(
    monkeypatch: pytest.MonkeyPatch,
    world: World,
    trusted: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run_guard(monkeypatch, world, Api(world), trusted) == 0
    out = capsys.readouterr().out
    assert "ACCEPTED" in out and "label_event=7" in out
    # Short commit ids and counts; no login and no full commit id.
    assert f"head={world.head[:7]}" in out and f"base={world.base[:7]}" in out
    assert world.head not in out and world.base not in out
    assert "owner" not in out and "approver" not in out
    assert str(SECRET) not in out


def test_a_pull_request_that_leaves_the_history_alone_passes_without_the_api(
    monkeypatch: pytest.MonkeyPatch, world: World, trusted: Path
) -> None:
    git(world.dir, "switch", "-q", "-c", "other", world.base)
    world.write("site.yaml", "x: 2\n")
    api = Api(world)
    assert run_guard(monkeypatch, world, api, trusted) == 0
    assert api.calls == 0


def test_a_rewrite_without_the_label_or_with_a_forged_one_is_refused(
    monkeypatch: pytest.MonkeyPatch, world: World, trusted: Path
) -> None:
    api = Api(world)
    api.labels = []
    refused(monkeypatch, world, api, trusted)
    forged = Api(world)
    forged.events = [ev("labeled", "attacker", "2026-11-20T00:00:00Z", 7)]
    refused(monkeypatch, world, forged, trusted)
    removed = Api(world)
    removed.events.append(ev("unlabeled", "owner", "2026-11-21T00:00:00Z", 8))
    refused(monkeypatch, world, removed, trusted)


def test_a_young_label_a_fork_or_the_wrong_branch_is_refused(
    monkeypatch: pytest.MonkeyPatch, world: World, trusted: Path
) -> None:
    young = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)
    refused(monkeypatch, world, Api(world), trusted, clock=young)
    refused(monkeypatch, world, Api(world), trusted, head_repo="x/inst")
    refused(monkeypatch, world, Api(world), trusted, head_ref="feature/x")
    refused(monkeypatch, world, Api(world), trusted, head_ref="counts/refresh")


def test_an_open_bot_refresh_pull_request_blocks_the_rewrite(
    monkeypatch: pytest.MonkeyPatch, world: World, trusted: Path
) -> None:
    api = Api(world)
    api.pulls = [pr()]
    refused(monkeypatch, world, api, trusted)
    other = Api(world)
    other.pulls = [pr(user={"login": "someone"})]
    assert run_guard(monkeypatch, world, other, trusted) == 0


def test_a_moved_main_is_refused(
    monkeypatch: pytest.MonkeyPatch, world: World, trusted: Path
) -> None:
    api = Api(world)
    api.tip = "f" * 40
    refused(monkeypatch, world, api, trusted)


def test_the_base_approvers_file_decides_not_the_pull_requests(
    monkeypatch: pytest.MonkeyPatch, world: World, trusted: Path
) -> None:
    # The pull request adds its author to the list in its own head commit.
    world.write(".github/history-approvers", "owner\nattacker\n")
    api = Api(world)
    api.events = [ev("labeled", "attacker", "2026-11-20T00:00:00Z", 7)]
    refused(monkeypatch, world, api, trusted)


@pytest.mark.parametrize(
    "path,text",
    [
        ("site.yaml", "x: 1\n"),
        (".github/history-approvers", "owner\nattacker\n"),
        ("scripts/history-guard.py", "print(1)\n"),
        ("history/redactions/other.manifest", "history-tombstone-manifest 1\n"),
    ],
)
def test_a_rewrite_that_also_changes_another_path_is_refused(
    monkeypatch: pytest.MonkeyPatch, world: World, trusted: Path, path: str, text: str
) -> None:
    world.write(path, text)
    refused(monkeypatch, world, Api(world), trusted)


def test_a_rewrite_that_changes_a_kept_value_is_refused_with_every_gate_open(
    monkeypatch: pytest.MonkeyPatch, world: World, trusted: Path
) -> None:
    target = world.dir / "history" / "social.jsonl"
    data = target.read_bytes()
    assert b'"value":50001' in data
    # Rebuild the chain by hand so only the value differs.
    items = [json.loads(x) for x in data.splitlines()]
    kept = next(i for i, o in enumerate(items) if o.get("type") != "tombstone")
    items[kept]["value"] = 99999
    prev = "0" * 64
    out = []
    for item in items:
        line = json.dumps(
            {**item, "prev": prev}, sort_keys=True, separators=(",", ":")
        ).encode()
        out.append(line)
        prev = hashlib.sha256(line).hexdigest()
    target.write_bytes(b"\n".join(out) + b"\n")
    world.commit()
    refused(monkeypatch, world, Api(world), trusted)


def test_a_surviving_published_number_makes_the_rewrite_fail(
    monkeypatch: pytest.MonkeyPatch, world: World, trusted: Path
) -> None:
    world.write("numbers.json", NUMBERS_BASE)
    refused(monkeypatch, world, Api(world), trusted)


@pytest.mark.parametrize("source", ["hand_kept", "imported"])
def test_a_tombstone_over_hand_kept_or_imported_rows_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, trusted: Path, source: str
) -> None:
    other = World(tmp_path, source=source)
    refused(monkeypatch, other, Api(other), trusted)


def test_a_pure_append_needs_no_label(
    monkeypatch: pytest.MonkeyPatch, world: World, trusted: Path
) -> None:
    git(world.dir, "switch", "-q", "-c", "append", world.base)
    path = world.dir / "history" / "social.jsonl"
    append_records(path, [rec(as_of_utc="2026-11-01T09:00:00Z", value=1)], now=NOW)
    world.commit()
    api = Api(world)
    api.labels = []
    api.events = []
    assert run_guard(monkeypatch, world, api, trusted) == 0


def test_the_label_state_changing_during_the_run_is_refused(
    monkeypatch: pytest.MonkeyPatch, world: World, trusted: Path
) -> None:
    api = Api(world)

    def flip(kind: str, count: int) -> Any:
        if kind == "events" and count == 2:
            return [ev("labeled", "owner", "2026-11-20T00:00:01Z", 9)]
        return None

    api.hook = flip
    refused(monkeypatch, world, api, trusted)


def test_main_moving_during_the_run_is_refused(
    monkeypatch: pytest.MonkeyPatch, world: World, trusted: Path
) -> None:
    api = Api(world)

    def move(kind: str, count: int) -> Any:
        if kind == "tip" and count == 2:
            return {"object": {"sha": "e" * 40}}
        return None

    api.hook = move
    refused(monkeypatch, world, api, trusted)


def test_main_moving_during_an_append_comparison_is_refused(
    monkeypatch: pytest.MonkeyPatch, world: World, trusted: Path
) -> None:
    git(world.dir, "switch", "-q", "-c", "append", world.base)
    path = world.dir / "history" / "social.jsonl"
    append_records(path, [rec(as_of_utc="2026-11-01T09:00:00Z", value=1)], now=NOW)
    world.commit()
    api = Api(world)
    api.labels, api.events = [], []
    assert run_guard(monkeypatch, world, api, trusted) == 0  # main stays put

    moving = Api(world)
    moving.labels, moving.events = [], []

    def move(kind: str, count: int) -> Any:
        if kind == "tip" and count == 2:  # the read just before accepting
            return {"object": {"sha": "e" * 40}}
        return None

    moving.hook = move
    refused(monkeypatch, world, moving, trusted)


# --- the approval is bound to the head commit ------------------------------


def test_a_label_kept_across_a_new_head_is_refused_until_it_is_applied_again(
    monkeypatch: pytest.MonkeyPatch, world: World, trusted: Path
) -> None:
    # The label was applied to the first head and has aged more than 24 hours.
    api = Api(world)
    assert run_guard(monkeypatch, world, api, trusted) == 0
    # A different, engine-valid head is pushed (its first run is after the
    # label); the label stays exactly as it was.
    api.runs = [run_of(world.head, "2026-11-24T23:00:00Z")]
    refused(monkeypatch, world, api, trusted)
    # Applied again on the new head: still refused until it has aged 24 hours.
    api.events += [
        ev("unlabeled", "owner", "2026-11-24T23:10:00Z", 20),
        ev("labeled", "owner", "2026-11-24T23:20:00Z", 21),
    ]
    refused(monkeypatch, world, api, trusted)
    refused(
        monkeypatch,
        world,
        api,
        trusted,
        clock=datetime(2026, 11, 25, 23, 19, 59, tzinfo=UTC),
    )
    later = datetime(2026, 11, 25, 23, 20, 1, tzinfo=UTC)
    assert run_guard(monkeypatch, world, api, trusted, clock=later) == 0


def test_the_head_change_time_is_the_pull_requests_own_run_not_another_one() -> None:
    head, other = "a" * 40, "b" * 40

    def changed(runs: list[dict[str, Any]], total: int | None = None, **kw: Any) -> Any:
        return guard.head_changed_at(
            kw.get("events", []),
            {
                "total_count": len(runs) if total is None else total,
                "workflow_runs": runs,
            },
            repo="me/inst",
            number="5",
            head=head,
        )

    first = datetime(2026, 11, 1, tzinfo=UTC)
    assert (
        changed(
            [run_of(head, "2026-11-02T00:00:00Z"), run_of(head, "2026-11-01T00:00:00Z")]
        )
        == first
    )
    for runs in (
        [],  # nothing found: cannot be determined
        [run_of(other, "2026-11-01T00:00:00Z")],  # another commit
        [run_of(head, "2026-11-01T00:00:00Z", number=6)],  # another pull request
        [run_of(head, "2026-11-01T00:00:00Z", repo="fork/inst")],  # a fork
        [run_of(head, "2026-11-01T00:00:00Z", event="push")],  # not a PR run
        [run_of(head, "2026-11-01T00:00:00")],  # no time zone
    ):
        with pytest.raises(guard.Refused):
            changed(runs)
    with pytest.raises(guard.Refused):  # a listing that is not complete
        changed([run_of(head, "2026-11-01T00:00:00Z")], total=2)
    forced = ev("head_ref_force_pushed", "x", "2026-11-03T00:00:00Z", 30)
    forced.pop("label")
    assert changed([run_of(head, "2026-11-01T00:00:00Z")], events=[forced]) == datetime(
        2026, 11, 3, tzinfo=UTC
    )


def test_an_undeterminable_head_time_or_a_later_force_push_refuses(
    monkeypatch: pytest.MonkeyPatch, world: World, trusted: Path
) -> None:
    none = Api(world)
    none.runs = []
    refused(monkeypatch, world, none, trusted)
    partial = Api(world)
    partial.total = 2
    refused(monkeypatch, world, partial, trusted)
    forced = Api(world)
    push = ev("head_ref_force_pushed", "x", "2026-11-21T00:00:00Z", 30)
    push.pop("label")
    forced.events.append(push)  # after the label at 2026-11-20
    refused(monkeypatch, world, forced, trusted)


def test_the_head_binding_is_checked_again_on_the_second_read(
    monkeypatch: pytest.MonkeyPatch, world: World, trusted: Path
) -> None:
    api = Api(world)

    def pushed(kind: str, count: int) -> Any:
        if kind == "runs" and count == 2:  # a push lands during the run
            return {
                "total_count": 1,
                "workflow_runs": [run_of(world.head, "2026-11-25T11:59:00Z")],
            }
        return None

    api.hook = pushed
    refused(monkeypatch, world, api, trusted)


def test_the_guard_main_fails_closed_and_prints_fixed_words(
    monkeypatch: pytest.MonkeyPatch,
    world: World,
    trusted: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def explode(*_: Any) -> int:
        raise KeyError("x")

    monkeypatch.setattr(guard, "guard", explode)
    monkeypatch.setattr(
        sys, "argv", ["g", "--trusted", str(trusted), "--pr", str(world.dir)]
    )
    assert guard.main() == 1
    assert "unexpected failure" in capsys.readouterr().out
    assert timedelta(hours=24) == guard.COOLING


def test_a_head_that_was_changed_away_and_restored_counts_from_the_restore() -> None:
    head = "a" * 40
    runs = {
        "total_count": 1,
        "workflow_runs": [run_of(head, "2026-11-01T00:00:00Z")],
    }
    for kind in ("head_ref_force_pushed", "head_ref_deleted", "head_ref_restored"):
        move = ev(kind, "x", "2026-11-05T00:00:00Z", 40)
        move.pop("label")
        when = guard.head_changed_at(
            [move], runs, repo="me/inst", number="5", head=head
        )
        assert when == datetime(2026, 11, 5, tzinfo=UTC), kind
    # A label applied between the first run and the restore does not authorize.
    with pytest.raises(guard.Refused):
        auth(
            events=[ev("labeled", "Owner", "2026-11-03T00:00:00Z", 1)],
            head_changed=datetime(2026, 11, 5, tzinfo=UTC),
        )


# --- the workflow states its limits and checks its own copy ----------------

STATEMENT = "not a sufficient merge gate on its own"


def squash(text: str) -> str:
    return " ".join(text.replace("#", " ").lower().split())


def test_the_readme_and_the_workflow_say_it_is_not_a_sufficient_gate() -> None:
    readme = squash((TEMPLATE / "README.md").read_text(encoding="utf-8"))
    header = squash(WORKFLOW.read_text(encoding="utf-8"))
    for text in (readme, header):
        assert STATEMENT in text
        # CODEOWNERS plus code-owner review is the primary boundary.
        assert "primary boundary is codeowners plus code-owner review" in text
        assert "require review from code owners" in text
        assert "administrators included" in text
        # The ruleset workflow is not available on Team plans: an upgrade path.
        assert "require workflows to pass before merging" in text
        assert "not available on a team plan" in text
        assert "enterprise" in text
        # The label is an intent record, not a gate GitHub enforces.
        assert "intent record" in text
        assert "24 hour cooling-off signal" in text
        # The solo owner cannot approve their own pull request.
        assert "does not let an author approve their own pull request" in text
        assert "who reviews when you are the only person" in text
        # The old model is gone.
        assert "only accepted trust boundary" not in text
    assert "checklist before you list the first approver" in readme
    # The checklist: CODEOWNERS present, branch rule, and an observed block.
    assert ".github/codeowners` is present" in readme
    assert "you observed it block" in readme
    assert "do not add any approver login until you have seen the block" in readme
    assert "independent code-owner approval" in readme
    assert "optional, recommended: live gate g1" in readme
    assert "not required for this boundary" in readme
    assert (
        "gate g5"
        not in readme.split("checklist before you list")[1].split("what is verified")[0]
    )
    assert "actions: read` is wide" in (TEMPLATE / "README.md").read_text("utf-8")
    assert "no narrower" in readme


def test_the_readme_lists_the_solo_owner_options_with_their_limits() -> None:
    readme = squash((TEMPLATE / "README.md").read_text(encoding="utf-8"))
    section = readme.split("who reviews when you are the only person")[1]
    section = section.split("defense in depth (not a boundary)")[0]
    for part in (
        "second github account that you control",
        "github app or bot as the reviewer",
        "a team that contains only you does not help",
        "administrator bypass",
        "does not protect against",
        "your own discipline",
        "unverified",
    ):
        assert part in section, part
    assert "recommendation: option 1" in section


def test_the_codeowners_example_covers_every_protected_path() -> None:
    lines = (TEMPLATE / ".github" / "CODEOWNERS").read_text("utf-8").splitlines()
    rules = [ln.split() for ln in lines if ln.strip() and not ln.startswith("#")]
    assert rules and all(len(r) == 2 and r[1] == "@OWNER-LOGIN" for r in rules)
    patterns = [r[0] for r in rules]

    def covered(path: str) -> bool:
        return any(
            path == p.lstrip("/")
            or (p.endswith("/") and path.startswith(p.lstrip("/")))
            for p in patterns
        )

    # What the guard reads or accepts as a rewrite, and what the README lists.
    protected = [
        guard.HISTORY,
        guard.APPROVERS,
        ".github/workflows/history-guard.yml",
        ".github/CODEOWNERS",
        "history/redactions/run-1.manifest",
        "scripts/history-guard.py",
        "scripts/verify-history-rewrite.sh",
        "engine.lock",
        "engine.release.json",
    ]
    for path in protected:
        assert covered(path), path
    assert guard.MANIFEST.match("history/redactions/run-1.manifest")
    readme = (TEMPLATE / "README.md").read_text("utf-8")
    assert ".github/CODEOWNERS" in readme


def compare_step() -> dict[str, Any]:
    steps = workflow()["jobs"]["history-guard"]["steps"]
    return next(s for s in steps if "differs" in s["name"])


def run_compare_step(
    tmp_path: Path, trusted_text: str, running_text: str, ref: str
) -> int:
    rel = Path(".github/workflows/history-guard.yml")
    for folder, text in (("trusted-src", trusted_text), ("running-src", running_text)):
        target = tmp_path / folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    step = compare_step()
    done = subprocess.run(  # noqa: S603 - fixed argv, step text from the repo
        ["bash", "-c", step["run"]],  # noqa: S607
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"], "WORKFLOW_REF": ref},
        capture_output=True,
        check=False,
    )
    return done.returncode


def test_the_job_fails_when_the_running_workflow_differs_from_the_base_copy(
    tmp_path: Path,
) -> None:
    ref = "me/inst/.github/workflows/history-guard.yml@refs/pull/5/merge"
    assert run_compare_step(tmp_path, "same\n", "same\n", ref) == 0
    assert run_compare_step(tmp_path, "same\n", "exit 0\n", ref) != 0
    assert run_compare_step(tmp_path, "same\n", "same\n", "me/inst/other.yml@x") != 0
    steps = workflow()["jobs"]["history-guard"]["steps"]
    names = [s["name"] for s in steps]
    # Before anything of the pull request is read, right after the base fetch.
    assert names.index(compare_step()["name"]) < names.index(
        "Fetch the pull request head"
    )
    assert ".github/workflows/history-guard.yml" in steps[0]["with"]["sparse-checkout"]
    assert steps[1]["with"].get("ref") is None  # the merge ref: what is running
