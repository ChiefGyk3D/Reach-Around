# SPDX-License-Identifier: Apache-2.0
"""The pull request comment renderer: the privileged job's only parser.

``examples/instance-repo/scripts/render-comment.py`` runs in the job that can
write to the pull request, on input written by code the pull request controls.
These tests feed it forged input and check that nothing but its own fixed text
and a few reduced names can reach the comment.

Run ``python tests/test_check_renderer.py`` to regenerate the vocabulary block
in the renderer after changing ``reach_around.checkvocab`` or the schema.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pprint
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from reach_around.checkvocab import VOCAB
from reach_around.cli import main
from reach_around.commands import check
from reach_around.commands.check import known_fields

ROOT = Path(__file__).resolve().parent.parent
RENDERER = ROOT / "examples" / "instance-repo" / "scripts" / "render-comment.py"
START = "# --- generated from reach_around.checkvocab"
END = "# --- end generated"


def generated_block() -> str:
    vocab = pprint.pformat(dict(VOCAB), width=84, sort_dicts=False)
    fields = pprint.pformat(sorted(known_fields()), width=84)
    return (
        f"{START} and the config schema by ---------\n"
        "# --- tests/test_check_renderer.py (it fails if this block drifts) ---\n"
        f"VOCAB: dict[str, tuple[str, str]] = {vocab}\n"
        f"KNOWN_FIELDS: frozenset[str] = frozenset({fields})\n"
    )


def regenerate() -> None:
    text = RENDERER.read_text(encoding="utf-8")
    head = text[: text.index(START)]
    tail = text[text.index(END) :]
    RENDERER.write_text(head + generated_block() + tail, encoding="utf-8")


def load_renderer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("render_comment", RENDERER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RC = load_renderer()


def test_the_renderer_vocabulary_matches_the_engine() -> None:
    assert RC.VOCAB == {k: tuple(v) for k, v in VOCAB.items()}, "regenerate"
    assert RC.KNOWN_FIELDS == known_fields(), "regenerate"
    assert len(RC.VOCAB) > 50


# --- running it ----------------------------------------------------------------

RUN = "https://github.com/example/site/actions/runs/123"
MARKER = "<!-- reach-around-preview repo=example/site workflow=Preview -->"


def verdict(payload: Any) -> str:
    """What GitHub would record for the preview job given an honest result."""
    if isinstance(payload, dict) and payload.get("check_status") == 0:
        return "success" if payload.get("build_status") == 0 else "failure"
    return "failure"


def render(
    tmp_path: Path,
    payload: Any,
    *,
    raw: bytes | None = None,
    run_url: str | None = RUN,
    out_name: str = "comment.md",
    preview_result: str | None = None,
    repo: str = "example/site",
    workflow: str = "Preview",
) -> tuple[subprocess.CompletedProcess[str], Path]:
    source = tmp_path / "result.json"
    source.write_bytes(raw if raw is not None else json.dumps(payload).encode())
    out = tmp_path / out_name
    argv = [sys.executable, "-I", str(RENDERER), "--in", str(source), "--out", str(out)]
    argv += ["--preview-result", preview_result or verdict(payload)]
    argv += ["--repo", repo, "--workflow", workflow]
    if run_url is not None:
        argv += ["--run-url", run_url]
    result = subprocess.run(  # noqa: S603 - fixed argv
        argv, capture_output=True, text=True, check=False
    )
    return result, out


def engine_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str, *extra: str
) -> tuple[int, dict[str, Any]]:
    path = tmp_path / "cfg" / "site.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    code = main(["check", "--config", str(path), "--format", "json", *extra])
    return code, json.loads(capsys.readouterr().out)


def wrap(
    code: Any,
    report: Any,
    build: Any = None,
    repo_status: Any = 0,
    repo_files: Any = None,
) -> dict[str, Any]:
    return {
        "check_status": code,
        "build_status": build,
        "check": report,
        "repo_status": repo_status,
        "repo_files": [] if repo_files is None else repo_files,
    }


GOOD_YAML = """\
version: 1
creator: {name: Example}
tabs:
  - id: links
    title: Links
    blocks:
      - {id: home, title: Home, url: "https://example.com/"}
"""
BAD_YAML = GOOD_YAML.replace('"https://example.com/"', '"http://example.com/"')


def body(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_a_good_result_renders_the_ready_comment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, report = engine_report(tmp_path, capsys, GOOD_YAML)
    done, out = render(tmp_path, wrap(0, report, 0))
    assert done.returncode == 0, done.stderr
    text = body(out)
    assert text.splitlines()[0] == MARKER
    assert "## The preview is ready" in text
    assert "The config is valid: 1 tab and 1 block." in text
    assert f"[Run details]({RUN})" in text
    assert "Nothing is deployed yet" in text


def test_a_bad_config_renders_a_numbered_plain_sentence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, report = engine_report(tmp_path, capsys, BAD_YAML)
    assert code == 2
    done, out = render(tmp_path, wrap(2, report))
    assert done.returncode == 0, done.stderr
    text = body(out)
    assert "## This change has a problem" in text
    assert (
        "1. Tab `Links`, block `home`, field `url`: the web address must start "
        "with https\\:\\/\\/" in text
    )
    assert "Nothing was built" in text


def test_changed_blocks_are_listed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = tmp_path / "base.yaml"
    base.write_text(GOOD_YAML.replace("Home", "Old"), encoding="utf-8")
    _, report = engine_report(tmp_path, capsys, GOOD_YAML, "--against", str(base))
    done, out = render(tmp_path, wrap(0, report, 0))
    assert done.returncode == 0
    assert "Changed since the base:" in body(out)
    assert "- Tab `Links`: block `home` changed." in body(out)


def test_the_build_failure_and_the_cannot_run_comments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, report = engine_report(tmp_path, capsys, GOOD_YAML)
    _, out = render(tmp_path, wrap(0, report, 1))
    assert "## The settings are valid, but the site did not build" in body(out)
    (tmp_path / "second").mkdir()
    _, out = render(tmp_path / "second", wrap(3, None))
    assert "## The check could not run" in body(out)


def test_every_engine_kind_is_in_the_vocabulary() -> None:
    emitted = (
        {kind for _, kind in check._LOADER_KINDS}
        | {kind for _, kind in check._VALUE_ERRORS}
        | set(check._SIMPLE_KINDS.values())
        | {kind for kind, _ in check._LIMIT_KINDS.values()}
    )
    assert len(emitted) > 40  # collected loosely, asserted strictly
    assert emitted <= set(VOCAB)


# --- forged input -----------------------------------------------------------------

EVIL = "@octocat [click](http://evil.example) <b>x</b> #123 https://evil.example `"
READ_ERROR = "## The preview result could not be read"


def allowed_markup(text: str) -> None:
    """The comment holds no mention, HTML, reference or link of its own."""
    lines = text.splitlines()
    assert lines[0] == MARKER
    rest = "\n".join(lines[1:]).replace(f"[Run details]({RUN})", "")
    assert "<" not in rest
    for line in re.sub(r"`[^`\n]*`", "", rest).splitlines():
        assert "@" not in line, line
        assert "](" not in line, line
        assert not re.search(r"(?<!\\)#\d", line), line
        assert "://" not in line.replace("\\://", ""), line


def forged_error(**changes: Any) -> dict[str, Any]:
    err: dict[str, Any] = {
        "tab": "Links",
        "block": "home",
        "field": "url",
        "kind": "url_scheme",
        "message": "x",
        "fix": "y",
    }
    err.update(changes)
    return err


def forged_report(**changes: Any) -> dict[str, Any]:
    """A consistent report (problems, or valid with counts), then the forgery."""
    errors = changes.pop("errors", [forged_error()])
    rep: dict[str, Any] = {
        "valid": not errors,
        "file": "site.yaml",
        "errors": errors,
        "error_count": len(errors),
    }
    if not errors:
        rep.update(tabs=1, blocks=1)
    rep.update(changes)
    return rep


def change_record(**changes: Any) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "op": "block_changed",
        "tab": "Links",
        "block": "home",
        "names": [],
        "other": False,
    }
    rec.update(changes)
    return rec


FORGED: dict[str, Any] = {
    "unknown kind": wrap(2, forged_report(errors=[forged_error(kind="rm_rf")])),
    "kind is a sentence": wrap(2, forged_report(errors=[forged_error(kind=EVIL)])),
    "extra key": wrap(2, forged_report(extra=EVIL)),
    "extra key in error": wrap(2, forged_report(errors=[forged_error(html=EVIL)])),
    "top level extra": {**wrap(2, None), "body": EVIL},
    "missing key": {"check_status": 0},
    "status as text": wrap("0", None),
    "status out of range": wrap(300, None),
    "status bool": wrap(True, None),
    "too many errors": wrap(2, forged_report(errors=[forged_error()] * 101)),
    "long message": wrap(2, forged_report(errors=[forged_error(message="x" * 401)])),
    "bad words": wrap(2, forged_report(errors=[forged_error(words=[EVIL])])),
    "bad ident": wrap(2, forged_report(errors=[forged_error(ident=EVIL)])),
    "limit text": wrap(2, forged_report(errors=[forged_error(limit=EVIL)])),
    "limit huge": wrap(2, forged_report(errors=[forged_error(limit=1e30)])),
    "line text": wrap(2, forged_report(errors=[forged_error(line=EVIL)])),
    "bad op": wrap(
        0, forged_report(errors=[], change_records=[change_record(op=EVIL)]), 0
    ),
    "unknown change name": wrap(
        0,
        forged_report(
            errors=[],
            change_records=[change_record(op="settings_changed", names=["api_token"])],
        ),
        0,
    ),
    "note text": wrap(0, forged_report(errors=[], note=EVIL), 0),
    "object as tab": wrap(2, forged_report(errors=[forged_error(tab={"a": 1})])),
}


@pytest.mark.parametrize("label", sorted(FORGED))
def test_forged_structure_is_refused_with_the_fixed_fallback(
    tmp_path: Path, label: str
) -> None:
    done, out = render(tmp_path, FORGED[label])
    assert done.returncode == 0, done.stderr
    text = body(out)
    assert READ_ERROR in text, (label, text)
    assert "evil" not in text
    allowed_markup(text)


def test_the_forged_battery_is_collected() -> None:
    assert len(FORGED) >= 20


def ready_result(**report_changes: Any) -> dict[str, Any]:
    report_changes.setdefault("errors", [])
    return wrap(0, forged_report(**report_changes), 0)


# (label, payload, what GitHub recorded for the preview job)
CONTRADICTIONS: list[tuple[str, Any, str]] = [
    ("ready claimed, job failed", ready_result(), "failure"),
    ("ready claimed, job cancelled", ready_result(), "cancelled"),
    ("ready claimed, job skipped", ready_result(), "skipped"),
    ("problem claimed, job succeeded", wrap(2, forged_report()), "success"),
    ("status 0, report has problems", wrap(0, forged_report(), 0), "success"),
    ("status 2, report is valid", wrap(2, forged_report(errors=[])), "failure"),
    ("status 0, no report", wrap(0, None, 0), "success"),
    ("status 2, no report", wrap(2, None), "failure"),
    ("status 3, with a report", wrap(3, forged_report()), "failure"),
    (
        "valid but errors listed",
        ready_result(valid=True, errors=[forged_error()], tabs=1, blocks=1),
        "success",
    ),
    (
        "invalid but no errors",
        wrap(2, forged_report(errors=[], valid=False)),
        "failure",
    ),
    ("error count too low", wrap(2, forged_report(error_count=0)), "failure"),
    ("error count too high", wrap(2, forged_report(error_count=5)), "failure"),
    ("error count is a bool", wrap(2, forged_report(error_count=True)), "failure"),
    ("build ran after a failed check", wrap(2, forged_report(), 0), "failure"),
    ("status outside the closed set", wrap(7, None), "failure"),
    ("build status outside the set", wrap(0, forged_report(errors=[]), 9), "failure"),
    (
        "valid without counts",
        wrap(0, {**forged_report(errors=[]), "tabs": None}, 0),
        "success",
    ),
    ("valid with zero tabs", ready_result(tabs=0), "success"),
    (
        "counts on a report with problems",
        wrap(2, forged_report(tabs=1, blocks=1)),
        "failure",
    ),
    ("negative count", ready_result(blocks=-1), "success"),
    (
        "more changes without a full list",
        ready_result(change_records=[change_record()], more_changes=3),
        "success",
    ),
    (
        "changes and records disagree",
        ready_result(change_records=[change_record()], changes=["a", "b"]),
        "success",
    ),
]


@pytest.mark.parametrize(
    ("label", "payload", "recorded"), CONTRADICTIONS, ids=[c[0] for c in CONTRADICTIONS]
)
def test_a_result_that_contradicts_itself_or_the_job_is_never_trusted(
    tmp_path: Path, label: str, payload: Any, recorded: str
) -> None:
    done, out = render(tmp_path, payload, preview_result=recorded)
    assert done.returncode == 0, done.stderr
    text = body(out)
    assert READ_ERROR in text, (label, text)
    assert "The preview is ready" not in text
    assert "## This change has a problem" not in text


def test_the_contradiction_battery_is_collected() -> None:
    assert len(CONTRADICTIONS) >= 20


def test_an_honest_result_matches_the_recorded_verdict(tmp_path: Path) -> None:
    done, out = render(tmp_path, ready_result(), preview_result="success")
    assert "## The preview is ready" in body(out)
    (tmp_path / "b").mkdir()
    done, out = render(
        tmp_path / "b", wrap(2, forged_report()), preview_result="failure"
    )
    assert "## This change has a problem" in body(out)


def test_the_marker_names_the_repository_and_the_workflow(tmp_path: Path) -> None:
    done, out = render(
        tmp_path, wrap(3, None), repo="acme/site-private", workflow="Preview Site"
    )
    assert body(out).splitlines()[0] == (
        "<!-- reach-around-preview repo=acme/site-private workflow=Preview Site -->"
    )
    for repo, workflow in (
        ("a b/c", "Preview"),
        ("acme/site", "x -->\n@evil"),
        ("x", "P"),
    ):
        (tmp_path / "bad").mkdir(exist_ok=True)
        done, _ = render(
            tmp_path / "bad", wrap(3, None), repo=repo, workflow=workflow,
            out_name=f"c{abs(hash((repo, workflow)))}.md",
        )  # fmt: skip
        assert done.returncode == 2, (repo, workflow)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not json",
        b"[" * 100000,
        b'{"check_status": 0, "check_status": 0, "build_status": null, "check": null}',
        b'{"check_status": NaN, "build_status": null, "check": null}',
        b"\xff\xfe",
        b" " * 70000 + b"{}",
    ],
    ids=["empty", "text", "deep", "duplicate key", "nan", "binary", "oversized"],
)
def test_hostile_bytes_give_the_fixed_fallback(tmp_path: Path, raw: bytes) -> None:
    done, out = render(tmp_path, None, raw=raw)
    assert done.returncode == 0, done.stderr
    assert READ_ERROR in body(out)


def test_forged_names_cannot_form_markup(tmp_path: Path) -> None:
    errors = [
        forged_error(tab=EVIL, block=EVIL, field="url"),
        forged_error(field="api_token"),
        forged_error(field="(unknown setting #4)"),
        forged_error(field="tabs[2].blocks[1].url"),
        forged_error(field=EVIL),
        forged_error(kind="not_in_choices", words=["auto", "dark"]),
        forged_error(kind="too_long", limit=200),
        forged_error(kind="duplicate_id", ident="home"),
    ]
    done, out = render(tmp_path, wrap(2, forged_report(errors=errors)))
    text = body(out)
    assert done.returncode == 0
    assert READ_ERROR not in text
    allowed_markup(text)
    assert "api_token" not in text  # not a schema name
    assert "`octocat click(http:evil.example) bxb 123...`" in text
    assert "field `(unknown setting #4)`" in text
    assert "field `tabs[2].blocks[1].url`" in text
    assert "must be one of: auto or dark" in text.replace("\\", "")


def test_the_run_url_is_only_the_run_page(tmp_path: Path) -> None:
    payload = wrap(2, forged_report(errors=[forged_error()]))
    bad_urls = [
        "https://evil.example/x",
        "https://github.com/a/b/actions/runs/1)[x](https://evil.example",
        "http://github.com/a/b/actions/runs/1",
        "https://github.com/a/b/pull/1",
    ]
    for i, bad in enumerate(bad_urls):
        done, out = render(tmp_path, payload, run_url=bad, out_name=f"c{i}.md")
        assert done.returncode == 0
        assert "evil" not in body(out)
        assert "](" not in body(out)


def test_the_body_is_capped(tmp_path: Path) -> None:
    payload = wrap(2, forged_report(errors=[forged_error(kind="file_yaml")] * 100))
    _, out = render(tmp_path, payload)
    assert 0 < len(body(out)) <= 20000


# --- the output file --------------------------------------------------------------


def test_it_refuses_to_overwrite_or_follow_a_link(tmp_path: Path) -> None:
    payload = wrap(3, None)
    existing = tmp_path / "exists.md"
    existing.write_text("keep", encoding="utf-8")
    done, _ = render(tmp_path, payload, out_name="exists.md")
    assert done.returncode == 2
    assert existing.read_text(encoding="utf-8") == "keep"

    target = tmp_path / "target.md"
    target.write_text("keep", encoding="utf-8")
    (tmp_path / "link.md").symlink_to(target)
    done, _ = render(tmp_path, payload, out_name="link.md")
    assert done.returncode == 2
    assert target.read_text(encoding="utf-8") == "keep"

    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "linkdir").symlink_to(real)
    done, _ = render(tmp_path, payload, out_name="linkdir/c.md")
    assert done.returncode == 2
    assert not (real / "c.md").exists()


def run_with_input(source: Path, out: Path) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, "-I", str(RENDERER), "--in", str(source), "--out", str(out)]
    argv += ["--preview-result", "failure", "--repo", "example/site"]
    argv += ["--workflow", "Preview"]
    return subprocess.run(  # noqa: S603 - fixed argv
        argv, capture_output=True, text=True, check=False, timeout=20
    )


def test_a_linked_or_special_input_is_not_read(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text(json.dumps(wrap(3, None)), encoding="utf-8")
    (tmp_path / "result.json").symlink_to(real)
    out = tmp_path / "c.md"
    assert run_with_input(tmp_path / "result.json", out).returncode == 0
    assert READ_ERROR in out.read_text(encoding="utf-8")
    fifo = tmp_path / "fifo.json"
    os.mkfifo(fifo)
    out2 = tmp_path / "c2.md"
    assert run_with_input(fifo, out2).returncode == 0
    assert READ_ERROR in out2.read_text(encoding="utf-8")


if __name__ == "__main__":
    regenerate()


# --- a forbidden file in the pull request -----------------------------------------


def repo_failed(files: Any = None, **changes: Any) -> dict[str, Any]:
    """What write-result.py records when check-repo.sh found a forbidden file."""
    payload = wrap(3, None, None, repo_status=1, repo_files=files or [".env"])
    payload.update(changes)
    return payload


def test_a_forbidden_file_is_named_in_plain_language(tmp_path: Path) -> None:
    done, out = render(tmp_path, repo_failed([".env", "deploy_key.pem"]))
    assert done.returncode == 0, done.stderr
    text = body(out)
    assert "## A file that must not be committed is in this change" in text
    assert "1. File `.env`: is a file that must not be committed" in text
    assert "2. File `deploy_key.pem`:" in text
    assert "git rm --cached" in text.replace("\\", "")
    assert "Nothing was checked or built" in text
    assert READ_ERROR not in text
    allowed_markup(text)


def test_forbidden_file_names_are_reduced_and_cannot_form_markup(
    tmp_path: Path,
) -> None:
    names = [EVIL, "a" * 150, "`x`"]
    done, out = render(tmp_path, repo_failed(names))
    text = body(out)
    assert done.returncode == 0
    assert "## A file that must not be committed" in text
    allowed_markup(text)
    assert "a" * 41 not in text


def test_a_forbidden_file_with_no_readable_name_still_says_so(tmp_path: Path) -> None:
    done, out = render(tmp_path, repo_failed([], repo_files=[]))
    assert "## A file that must not be committed" in body(out)


def test_a_repository_check_that_could_not_run_is_the_generic_comment(
    tmp_path: Path,
) -> None:
    done, out = render(tmp_path, wrap(3, None, None, repo_status=3))
    assert "## The check could not run" in body(out)


REPO_CONTRADICTIONS: dict[str, tuple[Any, str]] = {
    "repo failed but the engine ran": (
        wrap(0, forged_report(errors=[]), 0, repo_status=1, repo_files=[".env"]),
        "success",
    ),
    "repo failed but a report exists": (
        wrap(3, forged_report(), None, repo_status=1, repo_files=[".env"]),
        "failure",
    ),
    "repo failed but a problem was found": (
        wrap(2, forged_report(), None, repo_status=1, repo_files=[".env"]),
        "failure",
    ),
    "repo failed but a build ran": (
        wrap(3, None, 0, repo_status=1, repo_files=[".env"]),
        "failure",
    ),
    "repo failed but the job succeeded": (repo_failed(), "success"),
    "files listed though the repo check passed": (
        wrap(2, forged_report(), None, repo_status=0, repo_files=[".env"]),
        "failure",
    ),
    "repo check did not run but the engine did": (
        wrap(0, forged_report(errors=[]), 0, repo_status=3),
        "success",
    ),
    "repo status outside the closed set": (repo_failed(repo_status=2), "failure"),
    "repo status is text": (repo_failed(repo_status="1"), "failure"),
    "repo status is a bool": (repo_failed(repo_status=True), "failure"),
    "too many files": (repo_failed(["a"] * 11), "failure"),
    "file is not text": (repo_failed([{"a": 1}]), "failure"),
    "file name too long": (repo_failed(["x" * 201]), "failure"),
    "files is not a list": (repo_failed(repo_files=".env"), "failure"),
    "repo fields missing": (
        {"check_status": 3, "build_status": None, "check": None},
        "failure",
    ),
}


@pytest.mark.parametrize("label", sorted(REPO_CONTRADICTIONS))
def test_repository_check_contradictions_are_never_trusted(
    tmp_path: Path, label: str
) -> None:
    payload, recorded = REPO_CONTRADICTIONS[label]
    done, out = render(tmp_path, payload, preview_result=recorded)
    assert done.returncode == 0, done.stderr
    text = body(out)
    assert READ_ERROR in text, (label, text)
    assert "The preview is ready" not in text


def test_the_repository_battery_is_collected() -> None:
    assert len(REPO_CONTRADICTIONS) >= 15
