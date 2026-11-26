#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The pull-request guard for the counts history file (standard library only).

Run by .github/workflows/history-guard.yml from a copy taken from the BASE
commit. Usage:

    history-guard.py --trusted DIR --pr DIR

``--trusted`` holds the base copy of scripts/ (this file and
verify-history-rewrite.sh); ``--pr`` is a full checkout of the pull request's
head with the base history fetched. Everything else comes from the environment:
GH_TOKEN, REPO, PR_NUMBER, HEAD_SHA, BASE_SHA, HEAD_REPO, HEAD_REF,
PR_CREATED_AT and the optional GITHUB_STEP_SUMMARY.

What it enforces (issue #619):

* A pull request that does not touch history/social.jsonl passes at once.
* A pull request that touches it must contain the current tip of main, and the
  file may only append (the engine's `history-compare`, exit 0).
* The one exception is a tombstone-only rewrite. It needs ALL of: the label
  `history-tombstone` present now; the last label event is a `labeled` by a
  login on .github/history-approvers as it is on the BASE commit, applied AFTER
  the head commit last changed (earliest workflow run GitHub started for this
  exact head on this pull request, or the latest forced push, whichever is
  later; undeterminable means refused); that label at least 24 hours old, so a
  new head needs the label removed and applied again; a head branch `redact/*`
  in this repository; no open counts/refresh pull request from the Actions
  bot; exactly the allowed paths
  (the history file, optionally numbers.json, one new manifest); and the engine
  returning status 10 for the exact bytes. Anything else fails. Every failure
  prints fixed words, never a value.

The label and its applier are read live from the API, twice, and must agree.
The label is a permission to attempt the rewrite; the engine's exact compare is
the control.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

HISTORY = "history/social.jsonl"
NUMBERS = "numbers.json"
APPROVERS = ".github/history-approvers"
LABEL = "history-tombstone"
BRANCH_PREFIX = "redact/"
REFRESH_BRANCH = "counts/refresh"
BOT_LOGIN = "github-actions[bot]"
COOLING = timedelta(hours=24)
API = "https://api.github.com"
MAX_PAGES = 50
HEAD_MOVES = frozenset(
    {"head_ref_force_pushed", "head_ref_deleted", "head_ref_restored"}
)
MANIFEST = re.compile(r"history/redactions/[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.manifest")
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
SHA = re.compile(r"[0-9a-f]{40}")


class Refused(Exception):
    """A reason the change is not accepted. The text is fixed words only."""


# --- pure checks -----------------------------------------------------------


def parse_approvers(text: str) -> frozenset[str]:
    """The logins in the allow-list. A bad line is refused, never skipped."""
    found: set[str] = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if not LOGIN.fullmatch(line):
            raise Refused("the approvers file has a line that is not a login")
        found.add(line.lower())
    return frozenset(found)


def last_label_event(events: list[dict[str, Any]], label: str) -> dict[str, Any] | None:
    """The chronologically last labeled or unlabeled event for exactly ``label``."""
    chosen: dict[str, Any] | None = None
    for event in events:
        if event.get("event") not in ("labeled", "unlabeled"):
            continue
        named = event.get("label")
        if not isinstance(named, dict) or named.get("name") != label:
            continue
        if chosen is None or (event["created_at"], event["id"]) >= (
            chosen["created_at"],
            chosen["id"],
        ):
            chosen = event
    return chosen


def parse_time(text: Any) -> datetime:
    if not isinstance(text, str):
        raise Refused("a GitHub API answer had an unexpected shape")
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise Refused("a GitHub API answer had an unexpected shape") from None
    if when.tzinfo is None:
        raise Refused("a GitHub API answer had an unexpected shape")
    return when


def head_changed_at(
    events: list[dict[str, Any]],
    runs: dict[str, Any],
    *,
    repo: str,
    number: str,
    head: str,
) -> datetime:
    """When the pull request's head became the current head commit.

    Two server-side sources, never a commit date (the author sets those):
    the earliest workflow run GitHub started for this exact head commit on this
    pull request (a push starts one), and the latest forced push event of the
    pull request. The later of the two is returned. When no run for the head can
    be found, or the listing is not complete, the time cannot be determined and
    the change is refused.
    """
    listed = runs.get("workflow_runs")
    total = runs.get("total_count")
    if (
        not isinstance(listed, list)
        or not isinstance(total, int)
        or total != len(listed)
    ):
        raise Refused("the runs of the head commit could not be listed completely")
    seen: list[datetime] = []
    for run in listed:
        if not isinstance(run, dict):
            raise Refused("a GitHub API answer had an unexpected shape")
        repo_of = run.get("head_repository")
        if (
            run.get("head_sha") != head
            or run.get("event") != "pull_request"
            or not isinstance(repo_of, dict)
            or repo_of.get("full_name") != repo
        ):
            continue
        linked = run.get("pull_requests")
        if not isinstance(linked, list) or not any(
            isinstance(p, dict) and p.get("number") == int(number) for p in linked
        ):
            continue
        seen.append(parse_time(run.get("created_at")))
    if not seen:
        raise Refused("the time the head commit was pushed could not be determined")
    changed = min(seen)
    # A SHA that was head, was changed away and was put back counts from the
    # restore: a forced push, or a deleted and restored head branch, moves it.
    for event in events:
        if event.get("event") in HEAD_MOVES:
            changed = max(changed, parse_time(event.get("created_at")))
    return changed


def authorize(
    *,
    events: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    approvers: frozenset[str],
    now: datetime,
    same_repo: bool,
    head_ref: str,
    head_changed: datetime,
) -> tuple[Any, ...]:
    """Raise ``Refused`` unless the rewrite is authorized. Return what was seen.

    The accepted approval is the last label event, by a listed approver, that
    happened AFTER the head commit last changed; the 24 hours count from it. A
    new head needs the label removed and applied again.
    """
    if not same_repo:
        raise Refused("a rewrite must come from a branch in this repository")
    if head_ref == REFRESH_BRANCH or not head_ref.startswith(BRANCH_PREFIX):
        raise Refused("a rewrite must come from a redact/ branch")
    if not any(item.get("name") == LABEL for item in labels):
        raise Refused(f"the {LABEL} label is not on the pull request")
    event = last_label_event(events, LABEL)
    if event is None or event.get("event") != "labeled":
        raise Refused("the last label event is not a labeled event")
    actor = event.get("actor")
    login = actor.get("login") if isinstance(actor, dict) else None
    if not isinstance(login, str) or login.lower() not in approvers:
        raise Refused("the label was not applied by a listed approver")
    when = parse_time(event.get("created_at"))
    if when <= head_changed:
        raise Refused(
            "the head changed after the label was applied; "
            "remove the label and apply it again"
        )
    if now - when < COOLING:
        raise Refused("the label is younger than 24 hours; run again later")
    return (event["id"], login.lower(), when.isoformat(), head_changed.isoformat())


def refresh_pr_blocks(pr: dict[str, Any], repo: str) -> bool:
    """An open bot counts/refresh pull request of this repository."""
    base = pr.get("base") or {}
    head = pr.get("head") or {}
    head_repo = head.get("repo") or {}
    base_repo = base.get("repo") or {}
    user = pr.get("user") or {}
    return (
        pr.get("state") == "open"
        and base.get("ref") == "main"
        and head_repo.get("full_name") == repo
        and base_repo.get("full_name") == repo
        and head.get("ref") == REFRESH_BRANCH
        and user.get("login") == BOT_LOGIN
    )


def check_paths(changed: list[tuple[str, str, str, str]]) -> str:
    """The allowed change set of a rewrite; returns the one new manifest's path."""
    manifest: str | None = None
    history = False
    for status, path, old_mode, new_mode in changed:
        if path == HISTORY:
            if status != "M" or (old_mode, new_mode) != ("100644", "100644"):
                raise Refused("the history file changed in a way a rewrite may not")
            history = True
        elif path == NUMBERS:
            if status not in ("M", "D") or old_mode != "100644":
                raise Refused("numbers.json changed in a way a rewrite may not")
            if status == "M" and new_mode != "100644":
                raise Refused("numbers.json changed in a way a rewrite may not")
        elif MANIFEST.fullmatch(path) and status == "A" and new_mode == "100644":
            if manifest is not None:
                raise Refused("a rewrite adds exactly one manifest")
            manifest = path
        else:
            raise Refused("a rewrite changes a path that is not allowed")
    if not history or manifest is None:
        raise Refused("a rewrite changes the history file and adds one manifest")
    return manifest


def parse_raw_diff(raw: bytes) -> list[tuple[str, str, str, str]]:
    """``git diff --raw -z --no-renames`` as (status, path, old mode, new mode)."""
    parts = raw.split(b"\0")
    out: list[tuple[str, str, str, str]] = []
    i = 0
    while i + 1 < len(parts) and parts[i]:
        meta = parts[i].decode("ascii").lstrip(":").split(" ")
        if len(meta) != 5:
            raise Refused("the change list is not understood")
        out.append(
            (meta[4][:1], parts[i + 1].decode("utf-8", "strict"), meta[0], meta[1])
        )
        i += 2
    return out


# --- the world -------------------------------------------------------------


def api_get(path: str, token: str) -> list[Any] | dict[str, Any]:
    """GET one API path, following every page. Any failure is a refusal."""
    url = f"{API}{path}"
    pages: list[Any] = []
    merged: dict[str, Any] | None = None
    seen_page = False
    for _ in range(MAX_PAGES):
        request = urllib.request.Request(  # noqa: S310 - fixed https host below
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "history-guard",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as reply:  # noqa: S310
                body = json.loads(reply.read(8 * 1024 * 1024))
                link = reply.headers.get("Link", "")
        except (urllib.error.URLError, ValueError, OSError):
            raise Refused("a GitHub API read failed") from None
        if isinstance(body, dict):
            runs = body.get("workflow_runs")
            if not isinstance(runs, list):
                if seen_page:
                    raise Refused("a GitHub API answer had an unexpected shape")
                return body  # a plain object: one answer, no pages
            # The Actions listing is an object holding a list: merge its pages.
            if pages or (merged is None and seen_page):
                raise Refused("a GitHub API answer had an unexpected shape")
            if merged is None:
                merged = body
            else:
                merged["workflow_runs"].extend(runs)
        else:
            if merged is not None or not isinstance(body, list):
                raise Refused("a GitHub API answer had an unexpected shape")
            pages.extend(body)
        seen_page = True
        match = re.search(r'<([^>]+)>;\s*rel="next"', link)
        if not match:
            return merged if merged is not None else pages
        url = match.group(1)
        if not url.startswith(API + "/"):
            raise Refused("the GitHub API sent an unexpected page link")
    raise Refused("a GitHub API listing was too long to read")


def git(pr: Path, *args: str) -> bytes:
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "-C", str(pr), *args],  # noqa: S607 - git from the runner PATH
            check=True,
            capture_output=True,
            timeout=120,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        raise Refused("a git read failed") from None


def git_ok(pr: Path, *args: str) -> bool:
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(pr), *args],  # noqa: S607 - git from the runner PATH
        capture_output=True,
        timeout=120,
        check=False,
    )
    return done.returncode == 0


def blob(pr: Path, sha: str, path: str) -> bytes | None:
    if not git_ok(pr, "cat-file", "-e", f"{sha}:{path}"):
        return None
    return git(pr, "show", f"{sha}:{path}")


def utcnow() -> datetime:
    return datetime.now(UTC)


def env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise Refused(f"the {name} setting is missing")
    return value


def summary(text: str) -> None:
    print(text)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as out:
            out.write(text + "\n")


def run_compare(trusted: Path, *args: str) -> tuple[bool, str]:
    script = str(trusted / "scripts" / "verify-history-rewrite.sh")
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["bash", script, *args],  # noqa: S607 - bash from the runner PATH
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    sys.stderr.write(result.stderr)
    mode = ""
    for line in result.stdout.splitlines():
        if line.startswith("mode="):
            mode = line[5:]
        else:
            print(line)  # the engine prints counts and fixed words only
    return result.returncode == 0, mode


def live_state(
    repo: str, number: str, token: str, head: str
) -> tuple[list[Any], list[Any], dict[str, Any]]:
    events = api_get(f"/repos/{repo}/issues/{number}/events?per_page=100", token)
    labels = api_get(f"/repos/{repo}/issues/{number}/labels?per_page=100", token)
    runs = api_get(
        f"/repos/{repo}/actions/runs?head_sha={head}&event=pull_request&per_page=100",
        token,
    )
    if (
        not isinstance(events, list)
        or not isinstance(labels, list)
        or not isinstance(runs, dict)
    ):
        raise Refused("a GitHub API answer had an unexpected shape")
    return events, labels, runs


def main_is_base(repo: str, token: str, base: str) -> None:
    """Refuse unless main is still exactly the base commit (read live, now)."""
    tip = api_get(f"/repos/{repo}/git/ref/heads/main", token)
    obj = tip.get("object") if isinstance(tip, dict) else None
    if not isinstance(obj, dict) or obj.get("sha") != base:
        raise Refused("main moved while the check ran; run it again")


def guard(trusted: Path, pr: Path) -> int:
    repo, number, token = env("REPO"), env("PR_NUMBER"), env("GH_TOKEN")
    head, base = env("HEAD_SHA"), env("BASE_SHA")
    if not (SHA.fullmatch(head) and SHA.fullmatch(base)):
        raise Refused("a commit id is not a full SHA")
    raw = git(pr, "diff", "--raw", "-z", "--no-renames", base, head)
    changed = parse_raw_diff(raw)
    if HISTORY not in {path for _, path, _, _ in changed}:
        print("The history file is not part of this change.")
        return 0

    # The head must contain the current tip of main (design section 5.3).
    try:
        main_is_base(repo, token, base)
    except Refused:
        raise Refused(
            "update the branch: main moved after this pull request began"
        ) from None
    if not git_ok(pr, "merge-base", "--is-ancestor", base, head):
        raise Refused("update the branch: main moved after this pull request began")

    with tempfile.TemporaryDirectory(prefix="history-guard.") as folder:
        stage = Path(folder)
        old = stage / "old"
        new = stage / "new"
        old.write_bytes(blob(pr, base, HISTORY) or b"")
        new_bytes = blob(pr, head, HISTORY)
        if new_bytes is None:
            raise Refused("the history file was removed")
        new.write_bytes(new_bytes)
        ok, mode = run_compare(trusted, str(old), str(new))
        if ok and mode == "append":
            main_is_base(repo, token, base)  # the last read before accepting
            summary("History guard: the history file only appends.")
            return 0
        return rewrite(trusted, pr, stage, changed, repo, number, token, head, base)


def rewrite(
    trusted: Path,
    pr: Path,
    stage: Path,
    changed: list[tuple[str, str, str, str]],
    repo: str,
    number: str,
    token: str,
    head: str,
    base: str,
) -> int:
    approvers_text = blob(pr, base, APPROVERS)
    if approvers_text is None:
        raise Refused("the history is not append-only and there is no approvers file")
    approvers = parse_approvers(approvers_text.decode("utf-8", "strict"))
    events, labels, runs = live_state(repo, number, token, head)
    first = authorize(
        events=events,
        labels=labels,
        approvers=approvers,
        now=utcnow(),
        same_repo=env("HEAD_REPO") == repo,
        head_ref=env("HEAD_REF"),
        head_changed=head_changed_at(events, runs, repo=repo, number=number, head=head),
    )
    manifest_path = check_paths(changed)
    pulls = api_get(f"/repos/{repo}/pulls?state=open&base=main&per_page=100", token)
    if not isinstance(pulls, list):
        raise Refused("a GitHub API answer had an unexpected shape")
    if any(refresh_pr_blocks(p, repo) for p in pulls):
        raise Refused(
            "an open counts/refresh pull request must be merged or closed first"
        )
    created = datetime.fromisoformat(env("PR_CREATED_AT").replace("Z", "+00:00"))
    extra = [
        "--allow-tombstones",
        "--pr-day",
        created.astimezone(UTC).date().isoformat(),
    ]
    (stage / "manifest-dir").mkdir()
    manifest = stage / "manifest-dir" / Path(manifest_path).name
    manifest_bytes = blob(pr, head, manifest_path)
    if manifest_bytes is None:
        raise Refused("the manifest is missing")
    manifest.write_bytes(manifest_bytes)
    extra += ["--manifest", str(manifest)]
    for name, sha, flag in (
        ("old", base, "--numbers-old"),
        ("new", head, "--numbers-new"),
    ):
        data = blob(pr, sha, NUMBERS)
        if data is not None:
            target = stage / f"numbers-{name}"
            target.write_bytes(data)
            extra += [flag, str(target)]
    ok, mode = run_compare(trusted, str(stage / "old"), str(stage / "new"), *extra)
    if not ok or mode != "rewrite":
        raise Refused(
            "the engine did not accept the change as a tombstone-only rewrite"
        )
    # Read the mutable state again: it must not have changed during the run.
    events, labels, runs = live_state(repo, number, token, head)
    again = authorize(
        events=events,
        labels=labels,
        approvers=approvers,
        now=utcnow(),
        same_repo=env("HEAD_REPO") == repo,
        head_ref=env("HEAD_REF"),
        head_changed=head_changed_at(events, runs, repo=repo, number=number, head=head),
    )
    if again != first:
        raise Refused("the label state changed while the check ran; run it again")
    main_is_base(repo, token, base)  # the last read before accepting
    event_id, _approver, labeled_at, _changed = first
    age = int((utcnow() - datetime.fromisoformat(labeled_at)).total_seconds() // 3600)
    # Short commit ids, a count and the event number; no login, no full SHA.
    summary(
        "History guard: ACCEPTED a labeled tombstone-only rewrite. "
        f"head={head[:7]} base={base[:7]} label_event={event_id} "
        f"label_age_hours={age} manifest={Path(manifest_path).name}"
    )
    print(f"::notice::History tombstone rewrite accepted (label event {event_id}).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trusted", required=True, type=Path)
    parser.add_argument("--pr", required=True, type=Path)
    args = parser.parse_args()
    try:
        return guard(args.trusted, args.pr)
    except Refused as exc:
        print(f"::error::History guard: {exc}.")
        return 1
    except Exception:  # fail closed on anything unexpected, with no detail
        print("::error::History guard: an unexpected failure; nothing was accepted.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
