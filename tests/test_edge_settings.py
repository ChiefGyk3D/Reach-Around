# SPDX-License-Identifier: Apache-2.0
"""The zone's edge settings as code cannot drift silently (issue #613).

deploy/edge/desired-state.json is the single source. These tests pin the owner's
decisions, keep main.tf, the provider pin and the lock file in step with it, and
cover the read-only live check without any network.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import urllib.error
import urllib.request
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parent.parent
EDGE = Path("deploy/edge")
FILES = ("desired-state.json", "main.tf", "versions.tf", ".terraform.lock.hcl")


def load(path: Path, name: str) -> ModuleType:
    spec = spec_from_loader(name, SourceFileLoader(name, str(path)))
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = load(REPO / "scripts" / "check-edge.py", "check_edge")
SENTINEL = "sentinel-token-value-123456"


def desired() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((REPO / EDGE / "desired-state.json").read_text())
    return data


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A scratch copy of the files the offline check reads."""
    (tmp_path / EDGE).mkdir(parents=True)
    for name in FILES:
        shutil.copy(REPO / EDGE / name, tmp_path / EDGE / name)
    return tmp_path


def write_desired(root: Path, data: dict[str, Any]) -> None:
    (root / EDGE / "desired-state.json").write_text(json.dumps(data))


def test_the_repository_agrees_with_itself(capsys: pytest.CaptureFixture[str]) -> None:
    assert check.main(["--offline"]) == 0
    assert "ok" in capsys.readouterr().out


def test_the_owner_decisions_are_pinned() -> None:
    bot = desired()["bot_management"]
    assert bot["ai_training"] == "disallow"  # training crawlers blocked
    assert bot["ai_search"] == "disabled"  # search and link previews allowed
    assert bot["bot_preference_sync_enabled"] is True  # managed robots.txt
    assert bot["is_robots_txt_managed"] is True
    assert bot["fight_mode"] is False  # Bot Fight Mode deferred
    assert bot["crawler_protection"] == "disabled"
    rule = desired()["rate_limit"]
    assert rule["expression"] == 'starts_with(http.request.uri.path, "/e/")'
    assert rule["action"] == "block"
    assert rule["period"] == 10 == rule["mitigation_timeout"]  # Free plan


def mutate_bot(key: str, value: object) -> dict[str, Any]:
    data = copy.deepcopy(desired())
    data["bot_management"][key] = value
    return data


def mutate_rule(key: str, value: object) -> dict[str, Any]:
    data = copy.deepcopy(desired())
    data["rate_limit"][key] = value
    return data


def drop_bot_key() -> dict[str, Any]:
    data = copy.deepcopy(desired())
    del data["bot_management"]["ai_user"]
    return data


BAD = {
    "training allowed": mutate_bot("ai_training", "disabled"),
    "search blocked": mutate_bot("ai_search", "block"),
    "all AI blocked": mutate_bot("ai_bots_protection", "block"),
    "bot fight mode on": mutate_bot("fight_mode", True),
    "managed robots off": mutate_bot("is_robots_txt_managed", False),
    "sync off": mutate_bot("bot_preference_sync_enabled", False),
    "link maze on": mutate_bot("crawler_protection", "enabled"),
    "key dropped": drop_bot_key(),
    "regex expression": mutate_rule(
        "expression", 'http.request.uri.path matches "^/e/"'
    ),
    "whole zone": mutate_rule("expression", "true"),
    "challenge action": mutate_rule("action", "managed_challenge"),
    "longer period": mutate_rule("period", 60),
    "longer timeout": mutate_rule("mitigation_timeout", 600),
    "huge cap": mutate_rule("requests_per_period", 100000),
    "bool cap": mutate_rule("requests_per_period", True),
    "extra characteristic": mutate_rule(
        "characteristics", ["cf.colo.id", "ip.src", "x"]
    ),
}


@pytest.mark.parametrize("name", sorted(BAD))
def test_a_changed_decision_fails_the_offline_check(
    root: Path, name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    write_desired(root, BAD[name])
    assert check.main(["--offline", "--root", str(root)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_main_tf_must_read_every_desired_key(root: Path) -> None:
    path = root / EDGE / "main.tf"
    path.write_text(path.read_text().replace("local.bot.ai_user", "var.x"))
    assert any("ai_user" in p for p in check.offline_problems(root))


def test_main_tf_must_not_bypass_the_desired_state(root: Path) -> None:
    path = root / EDGE / "main.tf"
    path.write_text(path.read_text().replace("desired-state.json", "other.json"))
    assert any("desired-state.json" in p for p in check.offline_problems(root))


def test_the_lock_file_must_match_the_pin(root: Path) -> None:
    path = root / EDGE / ".terraform.lock.hcl"
    path.write_text(
        re.sub(r'version     = "[\d.]+"', 'version     = "0.0.1"', path.read_text())
    )
    assert any("lock" in p for p in check.offline_problems(root))


def test_the_provider_must_be_pinned_exactly(root: Path) -> None:
    path = root / EDGE / "versions.tf"
    path.write_text(path.read_text().replace('"= 5', '"~> 5'))
    assert any("pinned" in p for p in check.offline_problems(root))


def test_nothing_applies_and_no_zone_id_is_committed() -> None:
    plan = (REPO / EDGE / "plan.sh").read_text()
    assert "tofu plan" in plan
    assert not re.search(r"^\s*(exec\s+)?tofu\s+apply", plan, re.M)
    for workflow in (REPO / ".github" / "workflows").glob("*.yml"):
        assert "tofu apply" not in workflow.read_text(), workflow.name
    for path in (REPO / EDGE).iterdir():
        if path.is_file() and path.name != ".terraform.lock.hcl":
            assert not re.search(r"\b[0-9a-f]{32}\b", path.read_text()), path.name


def test_ci_runs_the_offline_check() -> None:
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    assert "python3 -I scripts/check-edge.py --offline" in ci


def test_the_doc_names_every_setting_and_scope() -> None:
    doc = (REPO / "docs" / "edge.md").read_text()
    for key in desired()["bot_management"]:
        assert f"`{key}`" in doc, key
    for key in desired()["rate_limit"]:
        assert f"`{key}`" in doc, key
    for scope in (
        "Bot Management Read",
        "Bot Management Write",
        "Zone WAF Read",
        "Zone WAF Write",
    ):
        assert scope in doc, scope


def live_bot() -> dict[str, Any]:
    return {**desired()["bot_management"], "enable_js": True}


def live_rules() -> dict[str, Any]:
    rule = {k: v for k, v in desired()["rate_limit"].items() if k in check.RL_KEYS[:4]}
    rule["ratelimit"] = {
        k: desired()["rate_limit"][k]
        for k in (
            "characteristics",
            "period",
            "requests_per_period",
            "mitigation_timeout",
        )
    }
    return {"rules": [rule]}


def test_live_that_agrees_has_no_differences() -> None:
    assert check.bot_differences(desired()["bot_management"], live_bot()) == []
    assert check.rate_differences(desired()["rate_limit"], live_rules()) == []


def test_a_dashboard_change_is_reported() -> None:
    live = live_bot()
    live["fight_mode"] = True
    live.pop("ai_training")
    found = check.bot_differences(desired()["bot_management"], live)
    assert len(found) == 2 and any("<absent>" in f for f in found)


def test_rate_rule_drift_is_reported() -> None:
    live = live_rules()
    live["rules"][0]["ratelimit"]["requests_per_period"] = 9999
    assert check.rate_differences(desired()["rate_limit"], live)
    live["rules"].append(live["rules"][0])
    assert "exactly 1" in check.rate_differences(desired()["rate_limit"], live)[0]
    assert check.rate_differences(desired()["rate_limit"], {"rules": []})
    assert check.rate_differences(desired()["rate_limit"], None)
    disabled = live_rules()
    disabled["rules"][0]["enabled"] = False
    assert any(
        "disabled" in f
        for f in check.rate_differences(desired()["rate_limit"], disabled)
    )


def test_get_refuses_plain_http_and_a_token_for_another_host() -> None:
    with pytest.raises(check.Unreadable):
        check.get("http://api.cloudflare.com/client/v4/x", None)
    with pytest.raises(check.Unreadable):
        check.get("https://example.invalid/robots.txt", SENTINEL)


def test_get_never_follows_a_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    def fake_build_opener(*handlers: Any) -> Any:
        seen.extend(handlers)

        class Stub:
            def open(self, *args: Any, **kwargs: Any) -> Any:
                raise urllib.error.URLError("stop")

        return Stub()

    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)
    with pytest.raises(check.Unreadable):
        check.get(check.API + "/zones/x", SENTINEL)
    assert len(seen) == 1
    assert (
        seen[0]().redirect_request(None, None, 302, "", {}, "https://x.invalid/")
        is None
    )


def test_a_failed_read_is_exit_2_and_never_prints_the_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(path: str, token: str) -> Any:
        raise check.Unreadable("HTTP 403")

    monkeypatch.setenv(check.TOKEN_ENV, SENTINEL)
    monkeypatch.setattr(check, "api_result", refuse)
    code = check.main(["--live", "--zone-id", "0" * 32])
    out = capsys.readouterr()
    assert code == 2
    assert SENTINEL not in out.out + out.err


def test_live_needs_a_token_and_a_zone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(check.TOKEN_ENV, raising=False)
    assert check.main(["--live", "--zone-id", "0" * 32]) == 2
    monkeypatch.setenv(check.TOKEN_ENV, SENTINEL)
    assert check.main(["--live", "--zone-id", "not-a-zone"]) == 2


def test_live_reports_drift_and_agreement(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    answers = {"bot_management": live_bot(), "entrypoint": live_rules()}

    def fake(path: str, token: str) -> Any:
        assert token == SENTINEL
        return answers[
            "bot_management" if path.endswith("bot_management") else "entrypoint"
        ]

    monkeypatch.setenv(check.TOKEN_ENV, SENTINEL)
    monkeypatch.setattr(check, "api_result", fake)
    saved = tmp_path / "saved.json"
    args = ["--live", "--zone-id", "0" * 32, "--save", str(saved)]
    assert check.main(args) == 0
    assert oct(saved.stat().st_mode & 0o777) == "0o600"
    assert SENTINEL not in saved.read_text()
    answers["bot_management"]["fight_mode"] = True
    assert check.main(["--live", "--zone-id", "0" * 32]) == 1
    assert "DRIFT bot_management.fight_mode" in capsys.readouterr().out


def test_a_missing_managed_robots_block_is_drift(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(check.TOKEN_ENV, SENTINEL)
    monkeypatch.setattr(
        check,
        "api_result",
        lambda path, token: live_bot() if "bot_" in path else live_rules(),
    )
    page = {"text": "User-agent: *\nDisallow: /x\n"}
    monkeypatch.setattr(check, "get", lambda url, token: page["text"].encode())
    args = [
        "--live",
        "--zone-id",
        "0" * 32,
        "--robots-url",
        "https://h.invalid/robots.txt",
    ]
    assert check.main(args) == 1
    page["text"] = "# BEGIN Cloudflare Managed content\nUser-agent: GPTBot\n"
    assert check.main(args) == 0
    assert "managed block" in capsys.readouterr().out


def test_a_malformed_rule_is_drift_not_a_crash() -> None:
    rl = desired()["rate_limit"]
    assert check.rate_differences(rl, {"rules": ["nope"]})
    broken = live_rules()
    broken["rules"][0]["ratelimit"] = "nope"
    assert check.rate_differences(rl, broken)


def test_saving_over_an_existing_file_is_exit_2_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv(check.TOKEN_ENV, SENTINEL)
    monkeypatch.setattr(
        check,
        "api_result",
        lambda path, token: live_bot() if "bot_" in path else live_rules(),
    )
    saved = tmp_path / "saved.json"
    saved.write_text("keep")
    args = ["--live", "--zone-id", "0" * 32, "--save", str(saved)]
    assert check.main(args) == 2
    assert saved.read_text() == "keep"
    assert SENTINEL not in capsys.readouterr().err


def test_plan_usage_error_is_not_the_changes_planned_exit_code() -> None:
    import os
    import subprocess

    env = {k: v for k, v in os.environ.items() if k not in ("CLOUDFLARE_API_TOKEN",)}
    env.pop("TF_VAR_zone_id", None)
    run = subprocess.run(  # noqa: S603 (fixed repo script, no token set)
        [str(REPO / EDGE / "plan.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 64  # tofu's detailed exit code 2 means changes planned
    assert "not set" in run.stderr


def test_the_doc_lists_the_open_owner_decisions() -> None:
    doc = (REPO / "docs" / "edge.md").read_text()
    assert "## Open owner decisions" in doc
    for topic in ("zone", "repository", "State", "requests_per_period"):
        assert topic in doc.split("## Open owner decisions", 1)[1], topic
