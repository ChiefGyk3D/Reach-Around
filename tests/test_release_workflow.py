# SPDX-License-Identifier: Apache-2.0
"""Release workflow: one repo-wide queue, and an older tag cannot publish."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def load() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return data


def test_the_concurrency_group_is_repo_wide_and_never_cancels() -> None:
    conc = load()["concurrency"]
    # A per-ref group lets two tags publish at once; the later finisher wins
    # `latest`, which may be the older tag.
    assert "github.ref" not in conc["group"]
    assert conc["group"] == "${{ github.workflow }}"
    assert conc["cancel-in-progress"] is False


SCRIPTS = ROOT / ".github" / "scripts"
CHECK = SCRIPTS / "check-release-tag.sh"
PROMOTE = SCRIPTS / "promote-release-tags.sh"
SHA = "1" * 40
OTHER_SHA = "2" * 40
DIGEST = "sha256:" + "a" * 64
IMAGE = f"ghcr.io/example/app@{DIGEST}"

# A stand-in `git` that answers only `ls-remote [--tags] [--refs] URL PATTERN...`
# from a fixture, and exits non-zero for any other argument or another URL, so a
# wrong repository, a missing --refs or a stray option is a test failure.
GIT_STUB = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_LOG"
[ "$1" = ls-remote ] || exit 99
shift
refs=0; url=""; pats=()
for a in "$@"; do
  case "$a" in
    --tags) ;;
    --refs) refs=1 ;;
    -*) exit 98 ;;
    *) if [ -z "$url" ]; then url="$a"; else pats+=("$a"); fi ;;
  esac
done
[ "$url" = "https://example.invalid/r" ] || exit 97
while IFS=$'\t' read -r sha ref; do
  [ -n "$ref" ] || continue
  if [ "$refs" = 1 ] && [[ "$ref" == *'^{}' ]]; then continue; fi
  for p in "${pats[@]}"; do
    case "$ref" in $p) printf '%s\t%s\n' "$sha" "$ref"; break ;; esac
  done
done < "$STUB_REMOTE"
"""

DOCKER_STUB = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_DOCKER_LOG"
if [ "$1" = login ]; then cat > "$STUB_DOCKER_LOGIN_STDIN"; fi
"""


def _env(
    tmp_path: Path,
    tag: str,
    remote: list[str],
    *,
    sha: str = SHA,
    points: dict[str, str] | None = None,
    annotated: tuple[str, ...] = (),
) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (("git", GIT_STUB), ("docker", DOCKER_STUB)):
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(0o755)
    lines: list[str] = []
    for name in remote:
        target = (points or {}).get(name, SHA)
        if name in annotated:
            lines.append(f"{'f' * 40}\trefs/tags/{name}")
            lines.append(f"{target}\trefs/tags/{name}^{{}}")
        else:
            lines.append(f"{target}\trefs/tags/{name}")
    (tmp_path / "remote.txt").write_text("\n".join(lines) + "\n")
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "TAG": tag,
        "REPO_URL": "https://example.invalid/r",
        "GITHUB_SHA": sha,
        "STUB_REMOTE": str(tmp_path / "remote.txt"),
        "STUB_LOG": str(tmp_path / "git.log"),
        "STUB_DOCKER_LOG": str(tmp_path / "docker.log"),
        "STUB_DOCKER_LOGIN_STDIN": str(tmp_path / "login.stdin"),
        "IMAGE": IMAGE,
        "GH_TOKEN": "synthetic-token-value",
        "ACTOR": "someone",
    }


def _run(
    tmp_path: Path,
    tag: str,
    remote: list[str],
    *,
    sha: str = SHA,
    points: dict[str, str] | None = None,
    annotated: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    env = _env(tmp_path, tag, remote, sha=sha, points=points, annotated=annotated)
    return subprocess.run(  # noqa: S603 - fixed argv, stub git
        ["/bin/bash", str(CHECK)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _promote(
    tmp_path: Path,
    tag: str,
    remote: list[str],
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    env = _env(tmp_path, tag, remote, **kwargs)
    return subprocess.run(  # noqa: S603 - fixed argv, stub git and docker
        ["/bin/bash", str(PROMOTE)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _docker_calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "docker.log"
    return log.read_text().splitlines() if log.exists() else []


def test_the_highest_tag_may_publish(tmp_path: Path) -> None:
    done = _run(tmp_path, "v0.10.0", ["v0.9.0", "v0.10.0", "v0.2.0"])
    assert done.returncode == 0, done.stderr


def test_an_older_tag_is_refused_when_a_newer_one_exists(tmp_path: Path) -> None:
    done = _run(tmp_path, "v0.9.0", ["v0.9.0", "v0.10.0"])
    assert done.returncode == 1
    assert "not the highest" in done.stderr


@pytest.mark.parametrize(
    "tag",
    [
        "v1.0.0-rc1",
        "v1.0.0-beta.2",
        "v1.0.0+build5",
        "v1.0.0-rc1+build5",
        "v1.0",
        "v01.0.0",
        "v1.00.0",
        "v1.0.007",
        "V1.0.0",
        "v1.0.0é",
        "v１.0.0",  # fullwidth digit one
        "v1.0.0 ",
        "v1.0.0\nv9.0.0",
    ],
)
def test_a_tag_outside_the_accepted_grammar_is_refused(
    tmp_path: Path, tag: str
) -> None:
    # Fails closed even when the tag would be the highest one.
    done = _run(tmp_path, tag, [tag.splitlines()[0]])
    assert done.returncode == 1, done.stdout + done.stderr
    assert "is not a plain vMAJOR.MINOR.PATCH tag" in done.stderr


def test_a_refused_tag_is_refused_even_with_a_newer_stable_release(
    tmp_path: Path,
) -> None:
    done = _run(tmp_path, "v1.0.0-rc1", ["v1.0.0-rc1", "v9.0.0"])
    assert done.returncode == 1


@pytest.mark.parametrize("tag", ["v0.0.0", "v1.2.3", "v10.20.30"])
def test_a_plain_tag_inside_the_grammar_is_accepted(tmp_path: Path, tag: str) -> None:
    assert _run(tmp_path, tag, [tag]).returncode == 0


def test_remote_tags_outside_the_grammar_never_count_as_the_highest(
    tmp_path: Path,
) -> None:
    done = _run(tmp_path, "v1.0.0", ["v1.0.0", "v9.0.0-rc1", "v09.0.0", "V9.0.0"])
    assert done.returncode == 0, done.stderr


def test_the_trigger_is_narrowed_beyond_a_bare_v_glob() -> None:
    # YAML reads the bare key `on` as the boolean True.
    tags = cast("dict[Any, Any]", load())[True]["push"]["tags"]
    assert "v*" not in tags
    assert tags == ["v[0-9]*.[0-9]*.[0-9]*"]


def test_a_tag_recreated_at_another_commit_is_refused(tmp_path: Path) -> None:
    # The run was triggered for SHA; the tag now points at OTHER_SHA.
    done = _run(tmp_path, "v2.0.0", ["v2.0.0"], points={"v2.0.0": OTHER_SHA})
    assert done.returncode == 1, done.stdout + done.stderr
    assert "not the triggering commit" in done.stderr


def test_a_deleted_tag_is_refused(tmp_path: Path) -> None:
    done = _run(tmp_path, "v2.0.0", ["v1.0.0"])
    assert done.returncode == 1
    assert "no longer exists" in done.stderr


def test_an_annotated_tag_is_compared_by_its_peeled_commit(tmp_path: Path) -> None:
    ok = _run(tmp_path, "v2.0.0", ["v2.0.0"], annotated=("v2.0.0",))
    assert ok.returncode == 0, ok.stderr
    moved = _run(
        tmp_path,
        "v2.0.0",
        ["v2.0.0"],
        annotated=("v2.0.0",),
        points={"v2.0.0": OTHER_SHA},
    )
    assert moved.returncode == 1
    assert "not the triggering commit" in moved.stderr


def test_a_malformed_event_sha_is_refused(tmp_path: Path) -> None:
    done = _run(tmp_path, "v2.0.0", ["v2.0.0"], sha="not-a-sha")
    assert done.returncode == 1


def test_the_check_queries_the_named_repository_for_tags_and_refs(
    tmp_path: Path,
) -> None:
    done = _run(tmp_path, "v1.0.0", ["v1.0.0"])
    assert done.returncode == 0, done.stderr
    calls = (tmp_path / "git.log").read_text().splitlines()
    assert (
        "ls-remote https://example.invalid/r refs/tags/v1.0.0 refs/tags/v1.0.0^{}"
        in calls
    )
    assert "ls-remote --tags --refs https://example.invalid/r refs/tags/v*" in calls


def test_both_checks_run_the_same_script_from_the_workflow() -> None:
    jobs = load()["jobs"]
    check = [
        s for s in jobs["check-version"]["steps"] if "highest" in s.get("name", "")
    ]
    assert check[0]["run"] == "bash .github/scripts/check-release-tag.sh"
    promote = jobs["promote"]["steps"][-1]
    assert promote["run"] == "bash .github/scripts/promote-release-tags.sh"
    # The shipped script, not a copy, is what the promote script calls.
    assert "check-release-tag.sh" in PROMOTE.read_text()


def test_the_release_job_waits_for_the_checks_and_writes_no_moving_tag() -> None:
    jobs = load()["jobs"]
    assert "check-version" in jobs["release"]["needs"]
    tags = jobs["release"]["with"]["tags"]
    assert "latest" not in tags
    assert "{{major}}" not in tags
    assert "type=semver,pattern={{version}}" in tags


def test_promotion_waits_for_every_job_and_keeps_least_privilege() -> None:
    data = load()
    promote = data["jobs"]["promote"]
    assert set(promote["needs"]) == {"check-version", "release", "verify"}
    assert promote["permissions"] == {"contents": "read", "packages": "write"}
    assert data["permissions"] == {"contents": "read"}


def test_promotion_retags_the_digest_after_a_passing_check(tmp_path: Path) -> None:
    done = _promote(tmp_path, "v1.2.3", ["v1.2.3", "v1.2.2"])
    assert done.returncode == 0, done.stdout + done.stderr
    calls = _docker_calls(tmp_path)
    assert any(c.startswith("login ghcr.io") and "--password-stdin" in c for c in calls)
    assert "synthetic-token-value" not in "\n".join(calls)
    create = [c for c in calls if c.startswith("buildx imagetools create")]
    assert create == [
        "buildx imagetools create --tag ghcr.io/example/app:latest "
        f"--tag ghcr.io/example/app:1.2 {IMAGE}"
    ]


def test_promotion_is_refused_when_a_newer_tag_appeared_after_the_first_check(
    tmp_path: Path,
) -> None:
    done = _promote(tmp_path, "v1.0.0", ["v1.0.0", "v2.0.0"])
    assert done.returncode == 1
    assert "not the highest" in done.stderr
    assert _docker_calls(tmp_path) == []


def test_promotion_is_refused_when_the_tag_moved(tmp_path: Path) -> None:
    done = _promote(tmp_path, "v1.0.0", ["v1.0.0"], points={"v1.0.0": OTHER_SHA})
    assert done.returncode == 1
    assert _docker_calls(tmp_path) == []


@pytest.mark.parametrize(
    "image",
    [
        "",
        "ghcr.io/example/app:latest",
        f"docker.io/x/y@{DIGEST}",
        "ghcr.io/E/x@sha256:ab",
    ],
)
def test_promotion_refuses_an_image_that_is_not_a_ghcr_digest(
    tmp_path: Path, image: str
) -> None:
    env = _env(tmp_path, "v1.0.0", ["v1.0.0"])
    env["IMAGE"] = image
    done = subprocess.run(  # noqa: S603 - fixed argv
        ["/bin/bash", str(PROMOTE)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 1
    assert _docker_calls(tmp_path) == []
