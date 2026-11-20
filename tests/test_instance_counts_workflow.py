# SPDX-License-Identifier: Apache-2.0
"""The instance template's scheduled counts refresh keeps its promises: a daily
schedule, minimal permissions, secrets only through ``env:``, a suspect-value
guard that is never overridden, and actions pinned by commit."""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "examples" / "instance-repo"
WORKFLOW = TEMPLATE / ".github" / "workflows" / "refresh-counts.yml"
SCRIPT = TEMPLATE / "scripts" / "refresh-counts.sh"

SHA_USE = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+(\.\d+)*$")
USES_LINE = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S.*?)\s*$", re.MULTILINE)
ALLOWED_PERMISSIONS = {
    "refresh": {"contents": "read"},
    "publish": {"contents": "write", "pull-requests": "write"},
}
KEY_NAMES = {"YOUTUBE_API_KEY", "TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET"}


def problems(text: str, script: str = "") -> list[str]:
    """Guard problems in a refresh workflow's text. Empty means clean."""
    found: list[str] = []
    data: dict[Any, Any] = yaml.safe_load(text)
    # YAML 1.1 reads the key `on` as True.
    triggers = data.get("on", data.get(True)) or {}
    if not triggers.get("schedule"):
        found.append("no schedule")
    if "workflow_dispatch" not in triggers:
        found.append("no workflow_dispatch")
    for bad in ("pull_request_target", "pull_request", "push", "workflow_run"):
        if bad in triggers:
            found.append(f"trigger {bad}")
    if data.get("permissions") != {"contents": "read"}:
        found.append("workflow permissions are not exactly contents: read")
    for line in USES_LINE.findall(text):
        if not SHA_USE.match(line):
            found.append(f"unpinned action: {line}")
    for name, job in data["jobs"].items():
        perms = job.get("permissions")
        if perms != ALLOWED_PERMISSIONS.get(name):
            found.append(f"{name}: permissions are {perms}")
        if "env" in job:
            found.append(f"{name}: job-level env")
        if "environment" in job:
            found.append(f"{name}: environment")
        for step in job["steps"]:
            run = step.get("run", "")
            if "${{" in run:
                found.append(f"{name}: expression inside run")
            if "secrets." in run:
                found.append(f"{name}: secret inside run")
    everything = text + script
    if "--accept-drop" in everything or "accept_drop" in everything:
        found.append("accept-drop is used")
    if "continue-on-error" in text:
        found.append("continue-on-error")
    if re.search(r"--force(?!-with-lease)", text):
        found.append("unconditional force push")
    return found


def test_the_refresh_workflow_is_clean() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    assert problems(WORKFLOW.read_text(encoding="utf-8"), script) == []


def test_the_schedule_is_daily() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    crons = [item["cron"] for item in data[True]["schedule"]]
    assert len(crons) == 1
    minute, hour, dom, month, dow = crons[0].split()
    assert (dom, month, dow) == ("*", "*", "*")
    assert minute.isdigit() and hour.isdigit()
    # Off the top of the hour, where scheduled runs are most often delayed.
    assert minute not in ("0", "00")


def test_secrets_reach_only_the_refresh_step_through_env() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    holders = []
    for job in data["jobs"].values():
        for step in job["steps"]:
            if "secrets." in yaml.safe_dump(step):
                holders.append(step["name"])
                assert "secrets." not in str(step.get("with", {}))
                names = {k for k, v in step["env"].items() if "secrets." in str(v)}
                assert names == KEY_NAMES
    assert holders == ["Refresh the counts"]


def _jobs() -> dict[str, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return dict(data["jobs"])


def test_the_job_with_the_keys_cannot_write_and_the_writer_has_no_keys() -> None:
    jobs = _jobs()
    assert set(jobs) == {"refresh", "publish"}
    assert jobs["refresh"]["permissions"] == {"contents": "read"}
    assert jobs["publish"]["permissions"] == {
        "contents": "write",
        "pull-requests": "write",
    }
    assert jobs["publish"]["needs"] == "refresh"
    publish_text = yaml.safe_dump(jobs["publish"])
    assert "secrets." not in publish_text
    for name in KEY_NAMES:
        assert name not in publish_text


def test_both_jobs_keep_the_main_only_guard_and_no_stored_credentials() -> None:
    for name, job in _jobs().items():
        assert "refs/heads/main" in str(job.get("if")), name
        checkouts = [
            s
            for s in job["steps"]
            if str(s.get("uses", "")).startswith("actions/checkout@")
        ]
        assert checkouts, name
        for step in checkouts:
            assert step["with"]["persist-credentials"] is False, name


def test_the_numbers_cross_the_jobs_as_an_artifact_and_are_checked_twice() -> None:
    jobs = _jobs()
    refresh_steps = jobs["refresh"]["steps"]
    publish_steps = jobs["publish"]["steps"]
    ups = [s for s in refresh_steps if "upload-artifact@" in str(s.get("uses"))]
    downs = [s for s in publish_steps if "download-artifact@" in str(s.get("uses"))]
    assert len(ups) == 1 and len(downs) == 1
    assert ups[0]["with"]["name"] == downs[0]["with"]["name"]
    assert ups[0]["with"]["if-no-files-found"] == "error"
    # The validator runs in the refresh job (through the script) and again in
    # publish, after the download and before the commit.
    assert "validate-numbers.py" in SCRIPT.read_text(encoding="utf-8")
    runs = [s.get("run", "") for s in publish_steps]
    validate_at = next(i for i, r in enumerate(runs) if "validate-numbers.py" in r)
    commit_at = next(i for i, r in enumerate(runs) if "commit -m" in r)
    down_at = publish_steps.index(downs[0])
    assert down_at < validate_at <= commit_at


def test_the_push_is_a_leased_force_and_the_existing_pr_is_vetted() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    assert "--force-with-lease=refs/heads/counts/refresh:" in code
    assert not re.search(r"--force(?!-with-lease)", code)
    assert "ls-remote" in code
    # The existing pull request must be ours: same repository, the Actions
    # bot as author, and numbers.json as the only changed file.
    assert "isCrossRepository" in code
    assert "github-actions" in code
    assert '"numbers.json", "history/social.jsonl"' in code
    assert "Refresh follower counts" in code


def test_failure_of_one_source_does_not_hide_the_others() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    # Exit 3 (a source failed) still lets the good numbers through; the job
    # reports it red afterwards.
    assert re.search(r"\b3\)", script)
    assert "numbers.json" in script


def test_the_guard_bites() -> None:
    good = WORKFLOW.read_text(encoding="utf-8")
    sha = re.search(r"actions/checkout@[0-9a-f]{40} # v[\d.]+", good)
    assert sha
    mutations = {
        "no schedule": good.replace("  schedule:", "  other:", 1),
        "unpinned": good.replace(sha.group(0), "actions/checkout@main"),
        "pull_request_target": good.replace(
            "workflow_dispatch:", "pull_request_target:", 1
        ),
        "extra permission": good.replace(
            "      pull-requests: write",
            "      pull-requests: write\n      actions: write",
        ),
        "secret in run": good.replace(
            "run: scripts/check-repo.sh",
            "run: echo ${{ secrets.YOUTUBE_API_KEY }}",
        ),
        "bare force push": good.replace(
            "--force-with-lease=refs/heads/counts/refresh:", "--force "
        ),
        "write on the key job": good.replace(
            "      contents: read # the keys are in this job",
            "      contents: write # the keys are in this job",
            1,
        ),
        "accept-drop": good + "\n# --accept-drop youtube\n",
    }
    for label, mutated in mutations.items():
        assert mutated != good, label
        assert problems(mutated), label


HISTORY = "history/social.jsonl"
VERIFY = TEMPLATE / "scripts" / "verify-history.sh"


def test_the_history_file_is_mounted_verified_and_installed_by_the_script() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    assert "history" in script and "social.jsonl" in script
    assert ":/config/history" in script
    assert "verify-history.sh" in script
    # The history is checked before numbers.json is written, and a link is refused.
    assert script.index("verify-history.sh") < script.index("validate-numbers.py")
    assert "history must not be a symbolic link" in script


def test_the_history_verifier_uses_the_engine_offline_and_read_only() -> None:
    text = VERIFY.read_text(encoding="utf-8")
    assert "counts history-verify" in text
    assert "--network none" in text
    assert "--read-only" in text and "--cap-drop ALL" in text
    assert ":ro" in text
    assert "read-lock.sh" in text


def test_the_history_crosses_the_jobs_and_is_checked_again_before_the_commit() -> None:
    jobs = _jobs()
    up = next(
        s for s in jobs["refresh"]["steps"] if "upload-artifact@" in str(s.get("uses"))
    )
    assert HISTORY in str(up["with"]["path"]) and "numbers.json" in str(
        up["with"]["path"]
    )
    runs = [s.get("run", "") for s in jobs["publish"]["steps"]]
    verify_at = next(i for i, r in enumerate(runs) if "verify-history.sh" in r)
    commit_at = next(i for i, r in enumerate(runs) if "commit -m" in r)
    assert verify_at < commit_at
    check = next(r for r in runs if "verify-history.sh" in r)
    # Append-only: the new file must start with the old one.
    assert "cmp" in check
    # Nothing but the two known files may cross.
    assert HISTORY in check and "numbers.json" in check


def test_the_history_is_staged_with_the_numbers_and_nothing_else() -> None:
    jobs = _jobs()
    propose = next(
        s["run"] for s in jobs["publish"]["steps"] if "commit -m" in s.get("run", "")
    )
    assert f"git add numbers.json {HISTORY}" in propose or (
        "git add numbers.json" in propose and HISTORY in propose
    )
    assert "git add -A" not in propose and "git add ." not in propose
    assert HISTORY in propose.split("git status --porcelain", 1)[1].splitlines()[0]


BASH = shutil.which("bash") or "/bin/bash"
GIT = shutil.which("git") or "/usr/bin/git"

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
  *" tokens "*)
    printf '%s\n' "$*" >> "${FAKE_LOG:-/dev/null}"
    case " $* " in
      *" --format github "*) printf '::warning title=Token health::instagram token expiring, 3 days left; renew it soon\n' ;;
      *" --format markdown "*) printf '### Token health\n' ;;
    esac
    exit "${FAKE_TOKENS_EXIT:-0}" ;;
  *" history-verify "*) exit 0 ;;
  *" refresh "*)
    printf '%s' "$FAKE_NUMBERS" > "$data/numbers.json"
    for f in $FAKE_HISTORY_FILES; do printf 'row\n' >> "$hist/$f"; done
    # A redaction or an edit of the repository's file while the engine runs.
    case "${FAKE_CONCURRENT:-}" in
      replace)
        printf 'redacted\n' > "$FAKE_TARGET.new"
        mv -f "$FAKE_TARGET.new" "$FAKE_TARGET" ;;
      append) printf 'edited\n' >> "$FAKE_TARGET" ;;
    esac
    exit 0 ;;
esac
exit 1
"""


def _refresh_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "inst"
    shutil.copytree(TEMPLATE, root)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    env.pop("GITHUB_OUTPUT", None)
    return root, env


def _run_refresh(
    root: Path,
    env: dict[str, str],
    files: str,
    tmp: Path,
    extra: dict[str, str] | None = None,
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
        env={
            **env,
            "FAKE_HISTORY_FILES": files,
            **(extra or {}),
            "FAKE_NUMBERS": '{"version": 1, "platforms": {"x": {"count": 5, '
            '"as_of": "2026-11-09T00:00:00Z"}}}',
        },
    )


def test_a_refresh_that_writes_only_the_known_history_file_installs_it(
    tmp_path: Path,
) -> None:
    root, env = _refresh_fixture(tmp_path)
    r = _run_refresh(root, env, "social.jsonl", tmp_path)
    assert r.returncode == 0, r.stderr
    assert (root / "history" / "social.jsonl").read_text() == "row\n"
    assert '"count": 5' in (root / "numbers.json").read_text()


@pytest.mark.parametrize("how", ["replace", "append"])
def test_a_history_file_changed_during_the_refresh_is_not_overwritten(
    tmp_path: Path, how: str
) -> None:
    root, env = _refresh_fixture(tmp_path)
    (root / "history").mkdir(exist_ok=True)
    target = root / "history" / "social.jsonl"
    target.write_text("old\n")
    numbers_before = (
        (root / "numbers.json").read_bytes()
        if (root / "numbers.json").exists()
        else None
    )
    r = _run_refresh(
        root,
        env,
        "social.jsonl",
        tmp_path,
        {"FAKE_CONCURRENT": how, "FAKE_TARGET": str(target)},
    )
    assert r.returncode != 0
    assert "history file changed while the refresh ran" in r.stderr
    assert "nothing was overwritten" in r.stderr
    # The concurrent change (a redaction, say) survives; the refresh did not undo it.
    assert target.read_text() == ("redacted\n" if how == "replace" else "old\nedited\n")
    after = (
        (root / "numbers.json").read_bytes()
        if (root / "numbers.json").exists()
        else None
    )
    assert after == numbers_before


def test_a_history_file_created_during_the_refresh_is_not_overwritten(
    tmp_path: Path,
) -> None:
    root, env = _refresh_fixture(tmp_path)
    (root / "history").mkdir(exist_ok=True)
    target = root / "history" / "social.jsonl"
    r = _run_refresh(
        root,
        env,
        "social.jsonl",
        tmp_path,
        {"FAKE_CONCURRENT": "replace", "FAKE_TARGET": str(target)},
    )
    assert r.returncode != 0
    assert target.read_text() == "redacted\n"


def test_an_unchanged_history_file_is_still_extended(tmp_path: Path) -> None:
    root, env = _refresh_fixture(tmp_path)
    (root / "history").mkdir(exist_ok=True)
    target = root / "history" / "social.jsonl"
    target.write_text("old\n")
    r = _run_refresh(root, env, "social.jsonl", tmp_path)
    assert r.returncode == 0, r.stderr
    assert target.read_text() == "old\nrow\n"


def test_the_final_move_waits_for_the_history_lock_and_refuses_if_it_is_held(
    tmp_path: Path,
) -> None:
    flock = shutil.which("flock")
    if flock is None:
        pytest.skip("flock is not installed")
    root, env = _refresh_fixture(tmp_path)
    (root / "history").mkdir(exist_ok=True)
    target = root / "history" / "social.jsonl"
    target.write_text("old\n")
    fd = os.open(root / "history" / ".social.jsonl.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)  # a redaction holds this while it runs
        r = _run_refresh(
            root,
            env,
            "social.jsonl",
            tmp_path,
            {"HISTORY_LOCK_WAIT_SECONDS": "1"},
        )
    finally:
        os.close(fd)
    assert r.returncode != 0
    assert "locked by another run" in r.stderr
    assert target.read_text() == "old\n"


def test_a_change_made_while_the_move_waits_for_the_lock_is_still_caught(
    tmp_path: Path,
) -> None:
    if shutil.which("flock") is None:
        pytest.skip("flock is not installed")
    root, env = _refresh_fixture(tmp_path)
    (root / "history").mkdir(exist_ok=True)
    target = root / "history" / "social.jsonl"
    target.write_text("old\n")
    fd = os.open(root / "history" / ".social.jsonl.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)

    def redact_then_release() -> None:
        time.sleep(2.0)  # the script has copied the file and is waiting for the lock
        (root / "history" / "new").write_text("redacted\n")
        os.replace(root / "history" / "new", target)
        fcntl.flock(fd, fcntl.LOCK_UN)

    worker = threading.Thread(target=redact_then_release)
    worker.start()
    try:
        r = _run_refresh(
            root,
            env,
            "social.jsonl",
            tmp_path,
            {"HISTORY_LOCK_WAIT_SECONDS": "30"},
        )
    finally:
        worker.join()
        os.close(fd)
    assert r.returncode != 0
    assert "history file changed while the refresh ran" in r.stderr
    assert target.read_text() == "redacted\n"


def test_the_engines_history_lock_file_is_tolerated_and_not_carried(
    tmp_path: Path,
) -> None:
    root, env = _refresh_fixture(tmp_path)
    r = _run_refresh(root, env, "social.jsonl .social.jsonl.lock", tmp_path)
    assert r.returncode == 0, r.stderr
    assert (root / "history" / "social.jsonl").read_text() == "row\n"
    # The lock file in the repository folder is the script's own, empty one; the
    # engine's copy (which holds "row") was not carried.
    assert (root / "history" / ".social.jsonl.lock").read_bytes() == b""


def test_any_other_file_in_the_history_folder_is_refused_before_anything_is_written(
    tmp_path: Path,
) -> None:
    for files in ("social.jsonl other.jsonl", "other.jsonl"):
        root, env = _refresh_fixture(tmp_path / files.replace(" ", "_"))
        before = (
            (root / "numbers.json").read_bytes()
            if (root / "numbers.json").exists()
            else None
        )
        r = _run_refresh(root, env, files, tmp_path / files.replace(" ", "_"))
        assert r.returncode != 0, files
        assert "other.jsonl" in r.stderr and "history/social.jsonl" in r.stderr
        assert not (root / "history" / "social.jsonl").exists()
        after = (
            (root / "numbers.json").read_bytes()
            if (root / "numbers.json").exists()
            else None
        )
        assert after == before


def test_redaction_leftovers_and_the_lock_file_are_git_ignored(tmp_path: Path) -> None:
    repo = tmp_path / "ig"
    (repo / "history").mkdir(parents=True)
    shutil.copy(TEMPLATE / ".gitignore", repo / ".gitignore")
    _git(repo, "init", "-q")

    def ignored(name: str) -> bool:
        r = subprocess.run(  # noqa: S603 - fixed argv in a temp repo
            [GIT, "check-ignore", "-q", "--", name], cwd=repo, check=False
        )
        return r.returncode == 0

    for name in (
        "history/.social.jsonl.redact-0a1b2c3d.tmp",
        "history/.social.jsonl.redact-0a1b2c3d.mark",
        "history/.social.jsonl.lock",
    ):
        assert ignored(name), name
    for name in ("history/social.jsonl", "numbers.json", "history/notes.txt"):
        assert not ignored(name), name


GUARD = TEMPLATE / "scripts" / "unmerged-history.sh"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603 - fixed argv in a temp repo
        [GIT, "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _repo_with_branch(tmp_path: Path, main_rows: str, branch_rows: str | None) -> Path:
    repo = tmp_path / "r"
    (repo / "history").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "history" / "social.jsonl").write_text(main_rows, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "main")
    _git(repo, "branch", "bot")
    if branch_rows is not None:
        _git(repo, "switch", "-q", "bot")
        (repo / "history" / "social.jsonl").write_text(branch_rows, encoding="utf-8")
        _git(repo, "commit", "-qam", "bot rows")
        _git(repo, "switch", "-q", "main")
    return repo


def _guard(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv
        [BASH, str(GUARD), "bot"], cwd=repo, capture_output=True, text=True, check=False
    )


def test_a_bot_branch_holding_unmerged_history_rows_is_not_replaced(
    tmp_path: Path,
) -> None:
    repo = _repo_with_branch(tmp_path, "a\n", "a\nb\n")
    r = _guard(repo)
    assert r.returncode == 3, r.stderr
    assert "unmerged" in r.stderr


def test_a_bot_branch_with_nothing_unmerged_may_be_replaced(tmp_path: Path) -> None:
    same = _repo_with_branch(tmp_path / "a", "a\n", None)
    assert _guard(same).returncode == 0
    # main moved past the branch: the branch's rows are all in main already
    behind = _repo_with_branch(tmp_path / "b", "a\n", None)
    (behind / "history" / "social.jsonl").write_text("a\nc\n", encoding="utf-8")
    _git(behind, "commit", "-qam", "more")
    assert _guard(behind).returncode == 0


def test_the_workflow_runs_the_unmerged_guard_before_replacing_the_branch() -> None:
    publish = _jobs()["publish"]["steps"]
    run = next(s["run"] for s in publish if "commit -m" in s.get("run", ""))
    assert "unmerged-history.sh" in run
    assert run.index("unmerged-history.sh") < run.index("push --force-with-lease")


def _path_without(tool: str, tmp: Path) -> str:
    """A PATH holding every program of the real PATH except ``tool``."""
    shim = tmp / "nopath"
    shim.mkdir()
    for folder in os.environ["PATH"].split(os.pathsep):
        if not os.path.isdir(folder):
            continue
        for entry in os.scandir(folder):
            link = shim / entry.name
            if (
                entry.name != tool
                and not link.exists()
                and os.access(entry.path, os.X_OK)
            ):
                link.symlink_to(entry.path)
    return str(shim)


def test_without_flock_the_history_is_not_installed(tmp_path: Path) -> None:
    """The move cannot be made safe against a redaction without the lock."""
    root, env = _refresh_fixture(tmp_path)
    (root / "history").mkdir(exist_ok=True)
    target = root / "history" / "social.jsonl"
    target.write_text("old\n")
    numbers = root / "numbers.json"
    numbers_before = numbers.read_bytes() if numbers.exists() else None
    env["PATH"] = f"{tmp_path / 'bin'}:{_path_without('flock', tmp_path)}"
    r = _run_refresh(root, env, "social.jsonl", tmp_path)
    assert r.returncode != 0
    assert "flock" in r.stderr
    assert "nothing was overwritten" in r.stderr
    assert target.read_text() == "old\n"
    assert (numbers.read_bytes() if numbers.exists() else None) == numbers_before


def test_numbers_json_is_not_installed_when_the_history_changes_before_the_lock(
    tmp_path: Path,
) -> None:
    """A redaction between the first history check and the lock leaves both
    outputs alone: numbers.json is installed only together with the history."""
    real_flock = shutil.which("flock")
    if real_flock is None:
        pytest.skip("flock is not installed")
    root, env = _refresh_fixture(tmp_path)
    (root / "history").mkdir(exist_ok=True)
    target = root / "history" / "social.jsonl"
    target.write_text("old\n")
    numbers = root / "numbers.json"
    numbers.write_text('{"version": 1, "platforms": {}}\n')
    numbers_before = numbers.read_bytes()
    # The shim runs when the script reaches the lock, which is after the first
    # history check: it redacts (replaces the file), then takes the real lock.
    shim = tmp_path / "bin" / "flock"
    shim.write_text(
        "#!/bin/bash\n"
        f'printf "redacted\\n" > "{target}.new"\n'
        f'mv -f "{target}.new" "{target}"\n'
        f'exec "{real_flock}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    r = _run_refresh(root, env, "social.jsonl", tmp_path)
    assert r.returncode != 0
    assert "history file changed while the refresh ran" in r.stderr
    assert "nothing was overwritten" in r.stderr
    assert target.read_text() == "redacted\n"
    assert numbers.read_bytes() == numbers_before  # the message is true
    assert list(root.glob(".numbers.*")) == []  # no staged leftover


def _mv_shim(tmp_path: Path, fail_on: str | None) -> Path:
    """A `mv` ahead of the real one in PATH: it logs each destination and fails
    the move whose destination ends with `fail_on`."""
    real_mv = shutil.which("mv") or "/bin/mv"
    log = tmp_path / "mv.log"
    shim = tmp_path / "bin" / "mv"
    cond = f'case "${{!#}}" in *"{fail_on}") exit 1 ;; esac\n' if fail_on else ""
    shim.write_text(
        f'#!/bin/bash\nprintf "%s\\n" "${{!#}}" >> "{log}"\n'
        f'{cond}exec "{real_mv}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return log


def _installed_fixture(tmp_path: Path) -> tuple[Path, dict[str, str], Path, Path]:
    if shutil.which("flock") is None:
        pytest.skip("flock is not installed")
    root, env = _refresh_fixture(tmp_path)
    (root / "history").mkdir(exist_ok=True)
    target = root / "history" / "social.jsonl"
    target.write_text("old\n")
    numbers = root / "numbers.json"
    numbers.write_text('{"version": 1, "platforms": {}}\n')
    return root, env, target, numbers


def test_a_failed_numbers_move_leaves_the_new_history_and_the_old_numbers(
    tmp_path: Path,
) -> None:
    root, env, target, numbers = _installed_fixture(tmp_path)
    before = numbers.read_bytes()
    _mv_shim(tmp_path, "/numbers.json")
    r = _run_refresh(root, env, "social.jsonl", tmp_path)
    assert r.returncode != 0
    assert "numbers.json could not be installed after the history file was" in r.stderr
    assert "nothing was overwritten" not in r.stderr  # the history WAS installed
    assert target.read_text() == "old\nrow\n"
    assert numbers.read_bytes() == before
    assert list(root.glob(".numbers.*")) == []  # the trap removed the staged file


def test_a_failed_history_move_installs_neither_and_leaves_no_staged_file(
    tmp_path: Path,
) -> None:
    root, env, target, numbers = _installed_fixture(tmp_path)
    before = numbers.read_bytes()
    _mv_shim(tmp_path, "/social.jsonl")
    r = _run_refresh(root, env, "social.jsonl", tmp_path)
    assert r.returncode != 0
    assert "history file could not be installed; nothing was overwritten" in r.stderr
    assert target.read_text() == "old\n"
    assert numbers.read_bytes() == before
    assert list(root.glob(".numbers.*")) == []
    assert list((root / "history").glob(".social.??????")) == []


def test_the_history_is_installed_before_numbers_json_and_numbers_keeps_mode_0644(
    tmp_path: Path,
) -> None:
    root, env, target, numbers = _installed_fixture(tmp_path)
    log = _mv_shim(tmp_path, None)
    r = _run_refresh(root, env, "social.jsonl", tmp_path)
    assert r.returncode == 0, r.stderr
    moves = [Path(x).name for x in log.read_text().split()]
    assert moves.index("social.jsonl") < moves.index("numbers.json"), moves
    assert numbers.stat().st_mode & 0o777 == 0o644


def test_the_token_report_is_annotations_plus_a_step_summary_and_never_changes_the_exit(
    tmp_path: Path,
) -> None:
    root, env = _refresh_fixture(tmp_path)
    summary = tmp_path / "summary.md"
    log = tmp_path / "docker.log"
    for code in ("0", "4", "3", "127"):
        summary.write_text("", encoding="utf-8")
        log.write_text("", encoding="utf-8")
        done = _run_refresh(
            root,
            env,
            "",
            tmp_path / f"w{code}",
            {
                "GITHUB_STEP_SUMMARY": str(summary),
                "FAKE_LOG": str(log),
                "FAKE_TOKENS_EXIT": code,
            },
        )
        assert done.returncode == 0, done.stderr
        assert "::warning title=Token health::" in done.stdout
        assert "### Token health" in summary.read_text(encoding="utf-8")
    calls = log.read_text(encoding="utf-8")
    assert "--network none" in calls and "--read-only" in calls
    assert ":/data:ro" in calls
    assert "-e " not in calls  # no key reaches the report container


def test_the_token_report_runs_without_a_step_summary(tmp_path: Path) -> None:
    root, env = _refresh_fixture(tmp_path)
    env.pop("GITHUB_STEP_SUMMARY", None)
    done = _run_refresh(root, env, "", tmp_path, {"FAKE_TOKENS_EXIT": "4"})
    assert done.returncode == 0, done.stderr
    assert "::warning title=Token health::" in done.stdout
