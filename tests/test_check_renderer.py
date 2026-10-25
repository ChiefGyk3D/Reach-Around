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


def render(
    tmp_path: Path,
    payload: Any,
    *,
    raw: bytes | None = None,
    run_url: str | None = RUN,
    out_name: str = "comment.md",
) -> tuple[subprocess.CompletedProcess[str], Path]:
    source = tmp_path / "result.json"
    source.write_bytes(raw if raw is not None else json.dumps(payload).encode())
    out = tmp_path / out_name
    argv = [sys.executable, "-I", str(RENDERER), "--in", str(source), "--out", str(out)]
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


def wrap(code: Any, report: Any, build: Any = None) -> dict[str, Any]:
    return {"check_status": code, "build_status": build, "check": report}


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
    assert text.splitlines()[0] == "<!-- reach-around-preview -->"
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
    assert lines[0] == "<!-- reach-around-preview -->"
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
    rep: dict[str, Any] = {"valid": False, "file": "site.yaml", "errors": []}
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
    "bad op": wrap(0, forged_report(change_records=[change_record(op=EVIL)]), 0),
    "unknown change name": wrap(
        0,
        forged_report(
            change_records=[change_record(op="settings_changed", names=["api_token"])]
        ),
        0,
    ),
    "note text": wrap(0, forged_report(note=EVIL), 0),
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
