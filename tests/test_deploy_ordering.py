# SPDX-License-Identifier: Apache-2.0
"""Deploy ordering and engine pinning (issue #768, findings F-10 to F-60)."""

# ruff: noqa: S603, S607

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github" / "scripts"
GUARD = SCRIPTS / "deploy-guard.py"
MIGRATIONS = SCRIPTS / "check-migrations.py"
WORKFLOWS = ROOT / ".github" / "workflows"
INSTANCE = ROOT / "examples" / "instance-repo"

V1 = "11111111-2222-3333-4444-555555555555"
V2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def run(
    script: Path, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", "-I", str(script), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )


# ---- check-migrations.py (F-10) ----------------------------------------------


def _dir(tmp_path: Path, files: dict[str, str]) -> Path:
    d = tmp_path / "migrations"
    d.mkdir()
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    return d


def test_the_real_migrations_pass() -> None:
    assert run(MIGRATIONS, str(ROOT / "worker" / "migrations")).returncode == 0


def test_the_real_leads_migrations_pass_with_no_legacy_exemption() -> None:
    folder = ROOT / "worker" / "leads-migrations"
    assert any(folder.glob("0001_*.sql"))
    assert run(MIGRATIONS, str(folder), "--no-legacy").returncode == 0


def test_no_legacy_applies_the_rule_to_the_first_migration(tmp_path: Path) -> None:
    d = _dir(tmp_path, {"0001_x.sql": "DROP TABLE leads;"})
    # The analytics folder keeps its pre-rule exemption.
    assert run(MIGRATIONS, str(d)).returncode == 0
    refused = run(MIGRATIONS, str(d), "--no-legacy")
    assert refused.returncode == 1
    assert "0001_x.sql" in refused.stdout


def test_ci_checks_the_leads_migrations_too() -> None:
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "check-migrations.py worker/leads-migrations --no-legacy" in text


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE click_events;",
        "ALTER TABLE a DROP COLUMN b;",
        "ALTER TABLE a RENAME TO b;",
        "ALTER TABLE a RENAME COLUMN b TO c;",
        "delete from view_daily;",
    ],
)
def test_a_breaking_new_migration_is_refused(tmp_path: Path, sql: str) -> None:
    d = _dir(tmp_path, {"0018_x.sql": sql})
    result = run(MIGRATIONS, str(d))
    assert result.returncode == 1
    assert "0018_x.sql" in result.stdout


def test_additive_migrations_pass_and_comments_are_ignored(tmp_path: Path) -> None:
    d = _dir(
        tmp_path,
        {
            "0018_x.sql": "-- Down: DROP TABLE t;\n/* DROP TABLE u; */\n"
            "CREATE TABLE t (a INTEGER);\n"
            "ALTER TABLE t ADD COLUMN b TEXT;\nINSERT INTO t (a) VALUES (1);\n"
        },
    )
    assert run(MIGRATIONS, str(d)).returncode == 0


def test_the_contract_annotation_allows_a_breaking_migration(tmp_path: Path) -> None:
    d = _dir(
        tmp_path,
        {"0018_x.sql": "-- migration-phase: contract\nDROP TABLE old_thing;\n"},
    )
    assert run(MIGRATIONS, str(d)).returncode == 0


def test_legacy_migrations_are_left_alone_and_bad_names_refused(tmp_path: Path) -> None:
    assert (
        run(MIGRATIONS, str(_dir(tmp_path, {"0008_x.sql": "DROP TABLE t;"}))).returncode
        == 0
    )
    other = tmp_path / "o"
    other.mkdir()
    (other / "x.sql").write_text("select 1;", encoding="utf-8")
    assert run(MIGRATIONS, str(other)).returncode == 1


# ---- deploy-guard.py (F-10, F-48, F-49) ---------------------------------------


def _json(tmp_path: Path, name: str, doc: object) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def test_active_version_picks_the_version_with_the_most_traffic(tmp_path: Path) -> None:
    doc = [
        {
            "id": "d",
            "versions": [
                {"version_id": V1, "percentage": 10},
                {"version_id": V2, "percentage": 90},
            ],
        }
    ]
    assert (
        run(GUARD, "active-version", _json(tmp_path, "s.json", doc)).stdout.strip()
        == V2
    )
    assert (
        run(
            GUARD,
            "active-version",
            _json(tmp_path, "t.json", {"versions": [{"version_id": V1}]}),
        ).stdout.strip()
        == V1
    )


def test_active_version_prints_nothing_for_junk(tmp_path: Path) -> None:
    empty = tmp_path / "e.json"
    empty.write_text("", encoding="utf-8")
    assert run(GUARD, "active-version", str(empty)).stdout.strip() == ""
    bad = _json(
        tmp_path, "b.json", {"versions": [{"version_id": "not-a-uuid; rm -rf"}]}
    )
    assert run(GUARD, "active-version", bad).stdout.strip() == ""


def test_link_ids_are_read_from_the_binding(tmp_path: Path) -> None:
    doc = {
        "resources": {
            "bindings": [
                {"type": "plain_text", "name": "LINK_IDS", "text": '["a","b"]'}
            ]
        }
    }
    assert (
        run(GUARD, "link-ids", _json(tmp_path, "v.json", doc)).stdout.strip()
        == '["a","b"]'
    )
    hostile = {"bindings": [{"name": "LINK_IDS", "text": '["a\\n::error::x"]'}]}
    assert (
        run(GUARD, "link-ids", _json(tmp_path, "h.json", hostile)).stdout.strip() == ""
    )


def test_union_keeps_old_ids_and_new_ones_and_honors_the_cap() -> None:
    result = run(GUARD, "union", '["old","both"]', '["both","new"]')
    assert result.stdout.strip() == '["old","both","new"]'
    many = json.dumps([f"a{i}" for i in range(150)], separators=(",", ":"))
    other = json.dumps([f"b{i}" for i in range(150)], separators=(",", ":"))
    assert run(GUARD, "union", many, other).returncode == 3
    assert run(GUARD, "union", '["BAD"]', "[]").returncode == 2


def test_owner_check_refuses_another_repository(tmp_path: Path) -> None:
    doc = [
        {"annotations": {"workers/message": "reach-around-owner=Someone/other run=1"}}
    ]
    path = _json(tmp_path, "l.json", doc)
    assert run(GUARD, "owner-check", path, "ChiefGyk3D/instance").returncode == 4
    assert run(GUARD, "owner-check", path, "someone/OTHER").returncode == 0
    assert (
        run(GUARD, "owner-check", str(tmp_path / "missing.json"), "a/b").returncode == 0
    )


# ---- the workflows (F-10, F-48, F-49) ------------------------------------------


def _steps(path: Path, job: str) -> list[dict[str, object]]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(doc["jobs"][job]["steps"])


def _names(steps: list[dict[str, object]]) -> list[str]:
    return [str(s.get("name", "")) for s in steps]


def _index(steps: list[dict[str, object]], fragment: str) -> int:
    for i, step in enumerate(steps):
        if fragment in str(step.get("name", "")):
            return i
    raise AssertionError(f"no step named like {fragment!r}")


def test_deploy_worker_records_probes_and_rolls_back_in_order() -> None:
    steps = _steps(WORKFLOWS / "deploy-worker.yml", "deploy")
    record = _index(steps, "Record the previous version")
    migrate = _index(steps, "Apply D1 migrations")
    deploy = _index(steps, "Deploy the Worker")
    probe = _index(steps, "Probe the deployed Worker")
    rollback = _index(steps, "Roll back the Worker")
    assert record < migrate < deploy < probe < rollback
    assert steps[rollback]["if"] and "failure()" in str(steps[rollback]["if"])
    assert "rollback" in str(steps[rollback]["run"])
    assert "reach-around-owner=" in json.dumps(steps[deploy])


def test_deploy_test_widens_before_pages_and_narrows_after() -> None:
    steps = _steps(WORKFLOWS / "deploy-test.yml", "deploy")
    widen = _index(steps, "(phase 1)")
    worker = _index(steps, "Deploy the Worker")
    pages = _index(steps, "Publish the site to Pages")
    narrow = _index(steps, "(phase 2)")
    assert widen < worker < pages < narrow
    assert "final.toml" in str(steps[narrow]["run"])
    assert _index(steps, "Roll back the Worker") > worker


def test_ci_runs_the_migration_check() -> None:
    assert "check-migrations.py" in (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")


# ---- engine release manifest (F-11) and deploy-local checks (F-60) --------------


def _instance(tmp_path: Path) -> Path:
    root = tmp_path / "inst"
    shutil.copytree(INSTANCE / "scripts", root / "scripts")
    shutil.copy(INSTANCE / "engine.lock", root / "engine.lock")
    shutil.copy(INSTANCE / "engine.release.json", root / "engine.release.json")
    return root


def _release(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(root / "scripts" / "read-lock.sh"), "--release"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_shipped_release_manifest_matches_the_shipped_lock(tmp_path: Path) -> None:
    result = _release(_instance(tmp_path))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("commit", "0" * 40),
        ("repo", "Someone/Else"),
        ("tag", "v9.9.9"),
        ("image", "ghcr.io/chiefgyk3d/reach-around:0.2.0@sha256:" + "1" * 64),
    ],
)
def test_a_mixed_release_is_refused(tmp_path: Path, field: str, value: str) -> None:
    root = _instance(tmp_path)
    path = root / "engine.release.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc[field] = value
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert _release(root).returncode == 1


def test_an_image_from_another_release_is_refused(tmp_path: Path) -> None:
    root = _instance(tmp_path)
    lock = root / "engine.lock"
    text = lock.read_text(encoding="utf-8").replace(
        "reach-around:0.2.0@", "reach-around:0.3.0@"
    )
    lock.write_text(text, encoding="utf-8")
    path = root / "engine.release.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["image"] = doc["image"].replace("reach-around:0.2.0@", "reach-around:0.3.0@")
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert _release(root).returncode == 1  # the image tag and the ref comment disagree


def test_a_missing_manifest_is_refused(tmp_path: Path) -> None:
    root = _instance(tmp_path)
    (root / "engine.release.json").unlink()
    assert _release(root).returncode == 1


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True
    ).stdout.strip()


def _deploy_local_fixture(
    tmp_path: Path, remote: str
) -> tuple[Path, Path, subprocess.CompletedProcess[str]]:
    engine = tmp_path / "engine"
    (engine / "worker" / "migrations").mkdir(parents=True)
    (engine / "worker" / "migrations" / "0007_x.sql").write_text(
        "select 1;", encoding="utf-8"
    )
    (engine / "worker" / "wrangler.toml").write_text("name = 'x'\n", encoding="utf-8")
    _git(engine, "init", "-q")
    _git(engine, "add", "-A")
    _git(engine, "commit", "-q", "-m", "x")
    _git(engine, "tag", "v0.2.0")
    commit = _git(engine, "rev-parse", "HEAD")
    _git(engine, "remote", "add", "origin", remote)
    root = _instance(tmp_path)
    lock = root / "engine.lock"
    lines = [
        line if not line.startswith("ENGINE_REF=") else f"ENGINE_REF={commit} # v0.2.0"
        for line in lock.read_text(encoding="utf-8").splitlines()
    ]
    lock.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = root / "engine.release.json"
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    doc["commit"] = commit
    manifest.write_text(json.dumps(doc), encoding="utf-8")
    settings = tmp_path / "settings.env"
    settings.write_text(
        f"ENGINE_DIR={engine}\nPRIVATE_DIR={tmp_path / 'priv'}\n"
        "CLOUDFLARE_ACCOUNT_ID=" + "0" * 32 + "\nPAGES_PROJECT=p\n"
        "D1_DATABASE_ID=00000000-0000-0000-0000-000000000000\nRATELIMIT_NAMESPACE_ID=1001\n"
        "SITE_HOST=links.example.invalid\nDOPPLER_PROJECT=p\n",
        encoding="utf-8",
    )
    settings.chmod(0o600)
    result = subprocess.run(
        ["bash", str(root / "scripts" / "deploy-local.sh"), "--dry-run", str(settings)],
        capture_output=True,
        text=True,
        check=False,
    )
    return engine, root, result


def test_deploy_local_refuses_a_checkout_of_another_repository(tmp_path: Path) -> None:
    _, _, result = _deploy_local_fixture(
        tmp_path, "https://github.com/Someone/fork.git"
    )
    assert result.returncode == 1
    assert "no remote of ENGINE_DIR" in result.stderr


def test_deploy_local_accepts_the_named_repository_past_the_pin_checks(
    tmp_path: Path,
) -> None:
    _, _, result = _deploy_local_fixture(
        tmp_path, "git@github.com:chiefgyk3d/reach-around.git"
    )
    # It gets past every pin check; the build that follows needs a container runtime.
    assert "no remote of ENGINE_DIR" not in result.stderr
    assert "do not describe one release" not in result.stderr
    assert "tag v0.2.0" not in result.stderr
    assert "migrations end" not in result.stderr


# ---- refresh-counts.sh deadline (F-59) -----------------------------------------


def test_refresh_counts_has_a_hard_deadline_and_refuses_a_larger_one() -> None:
    text = (INSTANCE / "scripts" / "refresh-counts.sh").read_text(encoding="utf-8")
    assert "timeout --kill-after=20" in text
    assert "${refresh_limit[@]+" in text
    assert "REFRESH_TIMEOUT_SECONDS" in text
    result = subprocess.run(
        ["bash", str(INSTANCE / "scripts" / "refresh-counts.sh")],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "REFRESH_TIMEOUT_SECONDS": "9999"},
        cwd=INSTANCE,
    )
    assert result.returncode == 2
    assert "REFRESH_TIMEOUT_SECONDS must be digits, 30 to 480" in result.stderr
