# SPDX-License-Identifier: Apache-2.0
"""The history guard's boundary, proved offline (issue #926, follows #619).

Three things, each tested on the real shipped files:

1. The shipped CODEOWNERS example covers what it must. A small matcher with
   GitHub's rule (the last matching pattern wins) is run over the real template
   tree and over the paths a rewrite touches. This proves the file's coverage,
   not that GitHub enforces it: the live check in the template README is an
   owner step and activation stays blocked until it has been observed.
2. The README inventory lists every input the workflow and its script read.
   The workflow is parsed as YAML (expressions, env names, run text) and the
   script as Python (``env()`` names, API paths, URLs); an input that is not
   in the README table fails the test, and so does a stale row.
3. The workflow's hardening is checked structurally and by running the step
   text on real trees, not by searching for substrings.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "examples" / "instance-repo"
WORKFLOW = TEMPLATE / ".github" / "workflows" / "history-guard.yml"
GUARD_PY = TEMPLATE / "scripts" / "history-guard.py"
CODEOWNERS = TEMPLATE / ".github" / "CODEOWNERS"
README = TEMPLATE / "README.md"


# --- 1. CODEOWNERS ----------------------------------------------------------


def parse_codeowners(text: str) -> list[tuple[str, list[str]]]:
    """Rules in file order. A pattern with no owner un-owns its paths."""
    rules: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        line = line.split(" #")[0].strip()
        if not line or line.startswith("#"):
            continue
        pattern, *owners = line.split()
        rules.append((pattern, owners))
    return rules


def pattern_regex(pattern: str) -> re.Pattern[str]:
    directory = pattern.endswith("/")
    core = pattern.strip("/")
    anchored = pattern.startswith("/") or "/" in core
    out = ""
    i = 0
    while i < len(core):
        if core.startswith("**", i):
            out += ".*"
            i += 2
            continue
        char = core[i]
        out += "[^/]*" if char == "*" else "[^/]" if char == "?" else re.escape(char)
        i += 1
    head = "^" if anchored else "(?:^|.*/)"
    tail = "/.*$" if directory else "(?:/.*)?$"
    return re.compile(head + out + tail)


def owners_of(path: str, rules: list[tuple[str, list[str]]]) -> list[str]:
    result: list[str] = []
    for pattern, owners in rules:
        if pattern_regex(pattern).match(path):
            result = owners  # the last matching pattern wins
    return result


def owned(path: str, rules: list[tuple[str, list[str]]]) -> bool:
    return bool(owners_of(path, rules))


def example_rules() -> list[tuple[str, list[str]]]:
    return parse_codeowners(CODEOWNERS.read_text(encoding="utf-8"))


def trusted_sparse_paths() -> list[str]:
    steps = workflow()["jobs"]["history-guard"]["steps"]
    lines = steps[0]["with"]["sparse-checkout"].split("\n")
    return [ln.strip() for ln in lines if ln.strip()]


# What a rewrite is decided by, from the README and the guard: all must be owned.
PROTECTED = (
    ".github/history-approvers",
    ".github/CODEOWNERS",
    ".github/workflows/history-guard.yml",
    ".github/workflows/deploy.yml",
    "history/redactions/run-1.manifest",
    "scripts/history-guard.py",
    "scripts/verify-history-rewrite.sh",
    "scripts/read-lock.sh",
    "engine.lock",
    "engine.release.json",
)
# Deliberately not owned: the daily refresh appends and needs no review.
UNOWNED = ("history/social.jsonl", "numbers.json", "site.yaml", "README.md")


def test_the_example_owns_every_protected_path_and_not_the_history_file() -> None:
    rules = example_rules()
    assert rules
    for path in PROTECTED:
        assert owned(path, rules), path
    for path in UNOWNED:
        assert not owned(path, rules), path


def test_every_shipped_file_under_the_guarded_paths_has_an_owner() -> None:
    """Walk the real template tree: the owned set is exactly the guarded set."""
    rules = example_rules()
    found = {
        p.relative_to(TEMPLATE).as_posix()
        for p in TEMPLATE.rglob("*")
        if p.is_file()
    }
    assert ".github/workflows/history-guard.yml" in found
    owned_set = {p for p in found if owned(p, rules)}
    guarded = {
        p
        for p in found
        if p.startswith((".github/", "scripts/"))
        or p in {"engine.lock", "engine.release.json"}
    }
    assert owned_set == guarded
    # Every workflow file is covered, not only this one.
    for workflow_file in (TEMPLATE / ".github" / "workflows").glob("*.yml"):
        assert owned(workflow_file.relative_to(TEMPLATE).as_posix(), rules)


def test_every_file_the_workflow_runs_from_the_base_is_owned() -> None:
    rules = example_rules()
    sparse = trusted_sparse_paths()
    assert sparse
    for path in sparse:
        assert (TEMPLATE / path).is_file(), path
        assert owned(path, rules), path


def test_the_example_names_a_single_placeholder_owner() -> None:
    rules = example_rules()
    assert {tuple(owners) for _, owners in rules} == {("@OWNER-LOGIN",)}


def test_the_matcher_follows_the_last_match_wins_rule() -> None:
    # Revert proof of the matcher itself: it is not a "first match" or a
    # "any match" helper, and a later empty rule removes ownership.
    rules = parse_codeowners("/scripts/ @a\n/scripts/x.sh @b\n/scripts/y.sh\n")
    assert owners_of("scripts/x.sh", rules) == ["@b"]
    assert owners_of("scripts/z.sh", rules) == ["@a"]
    assert owners_of("scripts/y.sh", rules) == []
    assert owners_of("other/scripts/z.sh", rules) == []
    assert owners_of("scripts", rules) == []  # a trailing slash is a folder


@pytest.mark.parametrize("lost", PROTECTED)
def test_dropping_every_rule_for_a_protected_path_leaves_it_unowned(lost: str) -> None:
    # Revert proof: a CODEOWNERS file without the rules that cover the path
    # fails the coverage check for exactly that path.
    rules = [(p, o) for p, o in example_rules() if not pattern_regex(p).match(lost)]
    assert not owned(lost, rules)
    assert [p for p in PROTECTED if not owned(p, rules)]


def test_an_unowning_override_after_the_rules_is_caught() -> None:
    text = CODEOWNERS.read_text(encoding="utf-8") + "/.github/workflows/\n"
    rules = parse_codeowners(text)
    assert not owned(".github/workflows/history-guard.yml", rules)
    assert any(not owned(p, rules) for p in PROTECTED)


# --- 2. the input inventory --------------------------------------------------


def workflow() -> dict[Any, Any]:
    data: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return data


def strings(node: Any) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from strings(key)
            yield from strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from strings(item)


EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.S)
CONTEXT_PATH = re.compile(r"(?<![\w.'\"])[A-Za-z_][\w-]*(?:\.[\w*-]+)+|(?<![\w.'\"])[A-Za-z_]\w*(?![\w(.'\"])")
SHELL_VARIABLE = re.compile(r"\$\{?([A-Z][A-Z0-9_]*)\}?")


def workflow_inputs() -> set[str]:
    """Every context path, step env name and shell variable the workflow reads."""
    found: set[str] = set()
    data = workflow()
    for text in strings(data):
        for expression in EXPRESSION.findall(text):
            found.update(
                t
                for t in CONTEXT_PATH.findall(expression)
                if t not in {"true", "false", "null"}
            )
    for job in data["jobs"].values():
        for step in job["steps"]:
            found.update(step.get("env", {}))
            found.update(SHELL_VARIABLE.findall(step.get("run", "")))
    return found


def script_inputs() -> set[str]:
    """Environment names the script reads, from its syntax tree."""
    tree = ast.parse(GUARD_PY.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name in {"env", "os.environ.get", "os.getenv"} and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found.add(first.value)
        elif isinstance(node, ast.Subscript) and ast.unparse(node.value) == (
            "os.environ"
        ):
            if isinstance(node.slice, ast.Constant) and isinstance(
                node.slice.value, str
            ):
                found.add(node.slice.value)
    return found


API_PATH = re.compile(r'f?"/repos/\{repo\}/([^"?]+)')


def script_api_paths() -> set[str]:
    return set(API_PATH.findall(GUARD_PY.read_text(encoding="utf-8")))


def inventory_text() -> str:
    readme = README.read_text(encoding="utf-8")
    start = readme.index("**Trusted inputs the workflow reads.**")
    end = readme.index("**Checklist before you list the first approver.**")
    assert start < end
    return readme[start:end]


def listed() -> set[str]:
    """Every backticked name in the first column of the inventory table."""
    names: set[str] = set()
    for row in inventory_text().splitlines():
        if row.startswith("| `"):
            first = row.split("|")[1]
            names.update(re.findall(r"`([^`]+)`", first))
    return names


def test_the_inventory_table_exists_and_has_rows() -> None:
    assert len(listed()) > 15


def test_the_workflow_reads_no_input_that_the_inventory_leaves_out() -> None:
    missing = workflow_inputs() - listed()
    assert not missing, sorted(missing)


def test_the_script_reads_no_input_that_the_inventory_leaves_out() -> None:
    from_script = script_inputs()
    assert {"REPO", "GH_TOKEN", "GITHUB_STEP_SUMMARY"} <= from_script  # parser works
    assert not (from_script - listed()), sorted(from_script - listed())
    guard = load_guard()
    for constant in (guard.HISTORY, guard.NUMBERS, guard.APPROVERS):
        assert constant in listed(), constant
    for api in script_api_paths():
        assert api in listed(), api


def test_the_inventory_names_no_input_that_nothing_reads() -> None:
    guard = load_guard()
    reads = workflow_inputs() | script_inputs() | script_api_paths()
    reads |= {guard.HISTORY, guard.NUMBERS, guard.APPROVERS, "history/redactions/"}
    reads |= set(trusted_sparse_paths())
    stale = listed() - reads
    assert not stale, sorted(stale)


def test_the_inventory_says_no_secrets_vars_inputs_or_artifacts() -> None:
    text = " ".join(inventory_text().split())
    assert "no `secrets`, no `vars`, no workflow `inputs`, no artifacts" in text
    # ...and that is true of the workflow itself, read as parsed data.
    for token in workflow_inputs():
        assert token.split(".")[0] not in {"secrets", "vars", "inputs", "env"}, token
    for text_ in strings(workflow()["jobs"]):
        assert "actions/upload-artifact" not in text_
        assert "actions/download-artifact" not in text_
        assert "actions/cache" not in text_


def test_the_only_network_address_in_the_script_is_the_github_api() -> None:
    tree = ast.parse(GUARD_PY.read_text(encoding="utf-8"))
    urls = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and "://" in n.value
        and " " not in n.value
    }
    assert urls == {load_guard().API}
    assert load_guard().API == "https://api.github.com"


def test_the_inventory_checks_bite_on_an_unlisted_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Revert proof: add a secret, a var, an input and an unlisted event field to
    # a copy of the workflow and the extraction reports each of them.
    text = WORKFLOW.read_text(encoding="utf-8")
    text += (
        "      - run: echo\n"
        "        env:\n"
        "          A: ${{ secrets.TOKEN }}\n"
        "          B: ${{ vars.THING }}\n"
        "          C: ${{ inputs.x }}\n"
        "          D: ${{ github.event.pull_request.title }}\n"
        "          E: ${{ github.event.pull_request.user.login }}\n"
    )
    monkeypatch.setattr(sys.modules[__name__], "workflow", lambda: yaml.safe_load(text))
    extra = workflow_inputs() - listed()
    assert {
        "secrets.TOKEN",
        "vars.THING",
        "inputs.x",
        "github.event.pull_request.title",
        "github.event.pull_request.user.login",
    } <= extra


def test_a_shell_variable_in_a_run_step_is_an_input_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = WORKFLOW.read_text(encoding="utf-8").replace(
        'echo "dir=$dir" >> "$GITHUB_OUTPUT"',
        'echo "dir=$dir $SOME_OTHER_SETTING" >> "$GITHUB_OUTPUT"',
    )
    assert "SOME_OTHER_SETTING" in text
    monkeypatch.setattr(sys.modules[__name__], "workflow", lambda: yaml.safe_load(text))
    assert "SOME_OTHER_SETTING" in workflow_inputs() - listed()


def load_guard() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("hg_boundary_under_test", GUARD_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --- 3. hardening, structurally and by behavior -----------------------------

PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
WRITE_SCOPES = {"write", "write-all"}


def all_steps() -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = workflow()["jobs"]["history-guard"]["steps"]
    return steps


def test_no_permission_anywhere_is_a_write_and_no_job_is_a_reusable_call() -> None:
    data = workflow()
    scopes = [data["permissions"]]
    scopes += [job.get("permissions", {}) for job in data["jobs"].values()]
    for scope in scopes:
        assert isinstance(scope, dict)
        assert set(scope.values()) <= {"read"}, scope
    for job in data["jobs"].values():
        assert "uses" not in job  # a called workflow would hold its own token
        assert "secrets" not in job
        assert "environment" not in job
        assert "env" not in job
    assert "env" not in data


def test_every_action_is_pinned_by_commit_and_keeps_no_credentials() -> None:
    uses = [s for s in all_steps() if "uses" in s]
    assert len(uses) == 3
    for step in uses:
        assert PINNED.match(step["uses"]), step["uses"]
        assert step["with"]["persist-credentials"] is False
        assert step["uses"].startswith("actions/checkout@")


def test_no_run_step_holds_an_expression_and_only_the_last_gets_the_token() -> None:
    steps = all_steps()
    for step in steps:
        assert "${{" not in step.get("run", "")
        assert "continue-on-error" not in step
        assert "if" not in step  # no step can be skipped by a condition
    holders = [s["name"] for s in steps if "GH_TOKEN" in s.get("env", {})]
    assert holders == ["Guard the history file"]
    assert steps[-1]["name"] == "Guard the history file"


def test_the_head_is_checked_out_as_data_after_the_trusted_copies() -> None:
    steps = all_steps()
    names = [s["name"] for s in steps]
    head = names.index("Fetch the pull request head")
    assert names.index("Install the trusted copies and choose the engine image") < head
    pr_checkout = steps[head]["with"]
    assert pr_checkout["ref"] == "${{ github.event.pull_request.head.sha }}"
    assert pr_checkout["path"] == "pr"
    # Nothing runs from the head's folder: the only executed path is the trusted dir.
    for step in steps:
        run = step.get("run", "")
        assert "pr/scripts" not in run
        assert not re.search(r"(^|\s)(bash|python3?|sh)\s+\S*\bpr/", run)


def stage_trusted_tree(root: Path, with_release: bool) -> Path:
    """A real sparse checkout: only the listed files of the template, at the base."""
    base = root / "trusted-src"
    for rel in trusted_sparse_paths():
        if rel == "engine.release.json" and not with_release:
            continue
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(TEMPLATE / rel, target)
    return base


def run_trust_step(root: Path) -> subprocess.CompletedProcess[bytes]:
    step = next(s for s in all_steps() if s.get("id") == "trust")
    runner_temp = root / "runner-temp"
    runner_temp.mkdir(exist_ok=True)
    output = root / "github-output"
    output.write_text("", encoding="utf-8")
    env = {
        "PATH": os.environ["PATH"],
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_OUTPUT": str(output),
    }
    return subprocess.run(  # noqa: S603 - fixed argv, step text from the repo
        ["bash", "-c", step["run"]],  # noqa: S607
        cwd=root,
        env=env,
        capture_output=True,
        check=False,
    )


def installed_dir(root: Path) -> Path:
    line = (root / "github-output").read_text(encoding="utf-8").strip()
    assert line.startswith("dir=")
    return Path(line[4:])


def test_the_trust_step_installs_read_only_base_copies_not_the_pull_request(
    tmp_path: Path,
) -> None:
    base = stage_trusted_tree(tmp_path, with_release=False)
    # A hostile head checkout sits beside it: it must never be copied.
    hostile = tmp_path / "pr" / "scripts"
    hostile.mkdir(parents=True)
    (hostile / "history-guard.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    (tmp_path / "pr" / "engine.lock").write_text("HOSTILE=1\n", encoding="utf-8")
    done = run_trust_step(tmp_path)
    assert done.returncode == 0, done.stderr
    folder = installed_dir(tmp_path)
    try:
        for rel in ("scripts/history-guard.py", "scripts/read-lock.sh", "engine.lock"):
            assert (folder / rel).read_bytes() == (base / rel).read_bytes(), rel
        # Read-only: no write bit anywhere in the installed tree.
        for path in (folder, *folder.rglob("*")):
            assert path.stat().st_mode & 0o222 == 0, path
        assert not (folder / "pr").exists()
        # The workflow file and the approvers file are not installed here.
        assert not (folder / ".github").exists()
    finally:
        for path in (folder, *folder.rglob("*")):
            path.chmod(path.stat().st_mode | 0o700)


def test_the_trust_step_fails_when_a_trusted_file_is_missing(tmp_path: Path) -> None:
    base = stage_trusted_tree(tmp_path, with_release=False)
    (base / "engine.lock").unlink()
    done = run_trust_step(tmp_path)
    assert done.returncode != 0
    assert not (tmp_path / "github-output").read_text(encoding="utf-8").strip()


def test_the_trust_step_stops_a_bad_release_pair_before_any_output(
    tmp_path: Path,
) -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq is not installed")
    base = stage_trusted_tree(tmp_path, with_release=True)
    ok = run_trust_step(tmp_path)
    assert ok.returncode == 0, ok.stderr
    first = installed_dir(tmp_path)
    for path in (first, *first.rglob("*")):
        path.chmod(path.stat().st_mode | 0o700)
    # A manifest from another release: the step must fail, not just warn.
    manifest = base / "engine.release.json"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('"commit": "', '"commit": "0'),
        encoding="utf-8",
    )
    (tmp_path / "github-output").write_text("", encoding="utf-8")
    bad = run_trust_step(tmp_path)
    for leftover in (tmp_path / "runner-temp").glob("trusted.*"):
        for path in (leftover, *leftover.rglob("*")):
            path.chmod(path.stat().st_mode | 0o700)
    assert bad.returncode != 0
    assert not (tmp_path / "github-output").read_text(encoding="utf-8").strip()


def test_the_sparse_base_checkout_holds_what_the_guard_needs_to_run(
    tmp_path: Path,
) -> None:
    """The installed folder must hold every script the guard and wrapper call."""
    base = stage_trusted_tree(tmp_path, with_release=False)
    guard_text = GUARD_PY.read_text(encoding="utf-8")
    wrapper = (TEMPLATE / "scripts" / "verify-history-rewrite.sh").read_text("utf-8")
    needed = {
        m for m in re.findall(r"scripts/[\w.-]+", guard_text + wrapper)
    } | {"scripts/history-guard.py", "scripts/verify-history-rewrite.sh"}
    for rel in needed:
        if (TEMPLATE / rel).is_file():
            assert (base / rel).is_file(), f"{rel} is not in the base checkout"
    assert (base / "engine.lock").is_file()
