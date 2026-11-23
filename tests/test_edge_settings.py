# SPDX-License-Identifier: Apache-2.0
"""The zone's edge settings as code cannot drift silently (issue #613).

deploy/edge/desired-state.json is the single source. These tests pin the owner's
decisions, keep main.tf, the provider pin and the lock file in step with it, and
cover the read-only live check without any network.

The text scans below (no apply, no zone id) are defense in depth, not proof: a
determined edit can dodge a regex. What really enforces "nothing applies" is
structural (docs/edge.md, "Structural controls").
"""

from __future__ import annotations

import copy
import ipaddress
import json
import re
import shutil
import socket
import subprocess
import sys
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


def test_main_tf_must_read_every_managed_key(root: Path) -> None:
    path = root / EDGE / "main.tf"
    path.write_text(path.read_text().replace("local.bot.ai_user", "var.x"))
    assert any("ai_user" in p for p in check.offline_problems(root))


def test_main_tf_must_not_own_the_rate_phase(root: Path) -> None:
    path = root / EDGE / "main.tf"
    path.write_text(path.read_text() + '\nresource "cloudflare_ruleset" "x" {}\n')
    assert any("cloudflare_ruleset" in p for p in check.offline_problems(root))


def test_the_real_main_tf_has_no_ruleset_resource() -> None:
    text = (REPO / EDGE / "main.tf").read_text()
    assert not re.search(r'^\s*resource\s+"cloudflare_ruleset"', text, re.M)


def test_main_tf_must_not_send_an_unmanaged_field(root: Path) -> None:
    path = root / EDGE / "main.tf"
    path.write_text(
        path.read_text().replace(
            "fight_mode  ", "enable_js   = true\n  fight_mode  ", 1
        )
    )
    assert any("enable_js" in p for p in check.offline_problems(root))


def test_every_provider_field_is_declared_managed_or_unmanaged(root: Path) -> None:
    bot = desired()["bot_management"]
    assert set(bot) == set(check.PROVIDER_FIELDS)
    unmanaged = {k for k, v in bot.items() if v == {"unmanaged": True}}
    assert "enable_js" in unmanaged and "ai_agents" in unmanaged
    assert unmanaged.isdisjoint(check.BOT_KEYS)
    data = copy.deepcopy(desired())
    del data["bot_management"]["enable_js"]
    write_desired(root, data)
    assert any("enable_js" in p for p in check.offline_problems(root))
    data = copy.deepcopy(desired())
    data["bot_management"]["enable_js"] = True  # a value where unmanaged is required
    write_desired(root, data)
    assert any("enable_js" in p for p in check.offline_problems(root))
    data = copy.deepcopy(desired())
    data["bot_management"]["brand_new_field"] = {"unmanaged": True}
    write_desired(root, data)
    assert any("brand_new_field" in p for p in check.offline_problems(root))


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


def tracked_text() -> dict[str, str]:
    git = shutil.which("git")
    assert git is not None
    out = subprocess.run(  # noqa: S603 (fixed git command, no input)
        [git, "-C", str(REPO), "ls-files", "-z"],
        capture_output=True,
        check=True,
    ).stdout
    files: dict[str, str] = {}
    for raw in out.split(b"\0"):
        if not raw:
            continue
        path = REPO / raw.decode()
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data[:4096]:
            continue  # binary
        files[raw.decode()] = data.decode("utf-8", "replace")
    return files


def test_the_text_scan_sees_the_whole_repository() -> None:
    files = tracked_text()
    assert len(files) > 100
    for name in ("deploy/edge/plan.sh", ".github/workflows/ci.yml", "docs/edge.md"):
        assert name in files


MUTATING = [
    "tofu apply",
    "tofu apply -auto-approve",
    "tofu -chdir=deploy/edge apply",
    "terraform destroy",
    "env TF_VAR_x=1 tofu apply",
    "command tofu apply",
    "bash -c 'tofu apply'",
    'sh -c "terraform -chdir=x import a b"',
    "exec  tofu   import cloudflare_ruleset.x zones/a/b",
    "tofu plan && tofu apply",
    "tofu \\\n  apply",
    "apply:\n\ttofu plan",
    "make apply",
    "make -C deploy/edge destroy",
    "TERRAFORM APPLY",
    "terragrunt run-all apply",
]


@pytest.mark.parametrize("line", MUTATING)
def test_the_mutation_scan_bites(line: str) -> None:
    assert check.mutating_lines(line), line


@pytest.mark.parametrize(
    "line", ["tofu plan -input=false", "tofu validate", "# applies the reviewed plan"]
)
def test_the_mutation_scan_allows_read_only_commands(line: str) -> None:
    assert not check.mutating_lines(line), line


def test_nothing_in_the_repository_applies_destroys_or_imports() -> None:
    """Defense in depth, not proof: a regex over every tracked text file."""
    for name, text in tracked_text().items():
        if name == "tests/test_edge_settings.py":
            continue  # its own hostile samples
        assert not check.mutating_lines(text), name


def test_the_hex_scan_bites_and_skips_placeholders() -> None:
    assert check.zone_like_ids("zone 0123456789abcdef0123456789abcde0 x")
    assert check.zone_like_ids("a" * 31 + "b")
    assert not check.zone_like_ids("0" * 32)
    assert not check.zone_like_ids("f" * 32)
    assert not check.zone_like_ids("0123456789abcdef" * 4)  # 64 hex, a hash
    assert not check.zone_like_ids("0123456789abcdef0123456789abcdef")  # doc fixture


def test_no_zone_or_account_id_is_committed() -> None:
    """Defense in depth, not proof: every tracked text file outside test fixtures."""
    fixture_dirs = {"tests", "test", "fixtures"}
    for name, text in tracked_text().items():
        if fixture_dirs & set(name.split("/")) or name.endswith(".terraform.lock.hcl"):
            continue
        assert not check.zone_like_ids(text), name


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


def test_the_doc_states_the_structural_controls_and_the_unapproved_apply() -> None:
    doc = (REPO / "docs" / "edge.md").read_text()
    assert "## Structural controls" in doc
    assert "defense in depth" in doc
    assert "NOT approved" in doc
    assert "MANUAL" in doc and "never replace" in doc.lower()
    assert "NOT approved" in (REPO / EDGE / "main.tf").read_text()


def managed_values() -> dict[str, Any]:
    return {
        k: v for k, v in desired()["bot_management"].items() if v != {"unmanaged": True}
    }


def live_bot() -> dict[str, Any]:
    return {**managed_values(), "enable_js": True, "ai_agents": {"x": 1}}


def live_rules() -> dict[str, Any]:
    rule = {k: v for k, v in desired()["rate_limit"].items() if k in check.RL_KEYS[:4]}
    rule["id"] = "abc"
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
    changed = {**live_bot(), "enable_js": False}  # unmanaged: not ours
    assert check.bot_differences(desired()["bot_management"], changed) == []
    assert check.rate_differences(desired()["rate_limit"], live_rules()) == []


def test_a_dashboard_change_is_reported() -> None:
    live = live_bot()
    live["fight_mode"] = True
    live.pop("ai_training")
    found = check.bot_differences(desired()["bot_management"], live)
    assert len(found) == 2 and any("<absent>" in f for f in found)


def other_rule() -> dict[str, Any]:
    return {"ref": "someone_else", "expression": "true", "action": "log"}


def test_other_sites_rules_are_not_ours_and_not_drift() -> None:
    rl = desired()["rate_limit"]
    live = live_rules()
    live["rules"] = [other_rule(), live["rules"][0], other_rule()]
    assert check.rate_differences(rl, live) == []
    found = check.rate_differences(rl, {"rules": [other_rule()]})
    assert found and "not present" in found[0] and "MANUAL" in found[0]


def test_our_rule_is_found_by_expression_when_the_ref_differs() -> None:
    live = live_rules()
    live["rules"][0]["ref"] = "renamed"
    found = check.rate_differences(desired()["rate_limit"], live)
    assert found and all("ref" in f for f in found)


def test_rate_rule_drift_is_reported() -> None:
    live = live_rules()
    live["rules"][0]["ratelimit"]["requests_per_period"] = 9999
    assert check.rate_differences(desired()["rate_limit"], live)
    assert check.rate_differences(desired()["rate_limit"], {"rules": []})
    assert check.rate_differences(desired()["rate_limit"], None)
    disabled = live_rules()
    disabled["rules"][0]["enabled"] = False
    assert any(
        "disabled" in f
        for f in check.rate_differences(desired()["rate_limit"], disabled)
    )


def test_a_missing_entrypoint_is_reported_not_unreadable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake(path: str, token: str) -> Any:
        if path.endswith("entrypoint"):
            raise check.NotFound("HTTP 404")
        return live_bot()

    monkeypatch.setenv(check.TOKEN_ENV, SENTINEL)
    monkeypatch.setattr(check, "api_result", fake)
    assert check.main(["--live", "--zone-id", "0" * 32]) == 1
    assert "not present" in capsys.readouterr().out


def test_a_malformed_rule_is_drift_not_a_crash() -> None:
    rl = desired()["rate_limit"]
    assert check.rate_differences(rl, {"rules": ["nope"]})
    broken = live_rules()
    broken["rules"][0]["ratelimit"] = "nope"
    assert check.rate_differences(rl, broken)


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
    seen: list[str] = []

    def fake_fetch(host: str) -> str:
        seen.append(host)
        return page["text"]

    monkeypatch.setattr(check, "fetch_robots", fake_fetch)
    args = ["--live", "--zone-id", "0" * 32, "--site-host", "Links.Example.com"]
    assert check.main(args) == 1
    page["text"] = "# BEGIN Cloudflare Managed content\nUser-agent: GPTBot\n"
    assert check.main(args) == 0
    assert seen == ["links.example.com"] * 2
    assert "managed block" in capsys.readouterr().out


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


def test_plan_script_is_executable() -> None:
    assert (REPO / EDGE / "plan.sh").stat().st_mode & 0o111


def run_plan_with_fake_tofu(tmp_path: Path, extra: dict[str, str]) -> dict[str, Any]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    dump = tmp_path / "env.json"
    fake = bindir / "tofu"
    fake.write_text(
        "#!" + sys.executable + "\n"
        "import json, os, sys\n"
        f"with open({str(dump)!r}, 'a') as h:\n"
        "    h.write(json.dumps([sys.argv[1:], dict(os.environ)]) + chr(10))\n"
    )
    fake.chmod(0o755)
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "TF_VAR_zone_id": "1" * 32,
        "CLOUDFLARE_API_TOKEN": SENTINEL,
        **extra,
    }
    run = subprocess.run(  # noqa: S603 (fixed repo script; fake tofu, no network)
        [str(REPO / EDGE / "plan.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    calls = [json.loads(line) for line in dump.read_text().splitlines()]
    return {"calls": calls, "stdout": run.stdout, "stderr": run.stderr}


def test_plan_passes_only_the_documented_token_to_tofu(tmp_path: Path) -> None:
    hostile = {
        "CLOUDFLARE_API_KEY": "k",
        "CLOUDFLARE_EMAIL": "e",
        "CLOUDFLARE_API_USER_SERVICE_KEY": "u",
        "CLOUDFLARE_API_BASE_URL": "https://attacker.invalid",
        "CLOUDFLARE_ANYTHING_ELSE": "x",
        "TF_CLI_ARGS": "-lock=false",
        "TF_CLI_ARGS_plan": "-destroy",
    }
    out = run_plan_with_fake_tofu(tmp_path, hostile)
    assert [c[0][0] for c in out["calls"]] == ["init", "plan"]
    for _argv, env in out["calls"]:
        assert env["CLOUDFLARE_API_TOKEN"] == SENTINEL
        assert env["TF_VAR_zone_id"] == "1" * 32
        extra = [k for k in env if k.startswith("CLOUDFLARE_")]
        assert extra == ["CLOUDFLARE_API_TOKEN"]
        assert not [k for k in env if k.startswith("TF_CLI_ARGS")]
    assert SENTINEL not in out["stdout"] + out["stderr"]
    assert not any("apply" in c[0] or "destroy" in c[0] for c in out["calls"])


# --- --site-host: no SSRF -----------------------------------------------------

GOOD_HOSTS = [
    "links.example.com",
    "Links.Example.COM",
    "a-b.example.org",
    "x1.y2.example.net",
]
BAD_HOSTS = [
    "",
    "localhost",
    "example",  # a single label
    "127.0.0.1",
    "1.2.3.4",
    "0x7f.0.0.1",
    "2130706433",
    "[::1]",
    "::1",
    "fe80::1",
    "example.com:443",
    "example.com:8080",
    "user@example.com",
    "user:pw@example.com",
    "https://example.com",
    "example.com/path",
    "example.com?x=1",
    "example.com#x",
    "exa mple.com",
    "example.com.",
    "-bad.example.com",
    "bad-.example.com",
    "a..example.com",
    "ex\x00ample.com",
    "ex\nample.com",
    "\u00fcber.example.com",
    "example.com\\evil.com",
    "a" * 64 + ".example.com",
    ("a." * 130) + "com",
    "example.123",
]


@pytest.mark.parametrize("host", GOOD_HOSTS)
def test_site_host_accepts_plain_hostnames(host: str) -> None:
    assert check.parse_site_host(host) == host.lower()


@pytest.mark.parametrize("host", BAD_HOSTS)
def test_site_host_refuses_everything_else(host: str) -> None:
    assert check.parse_site_host(host) is None


def fake_resolver(addresses: list[str]) -> Any:
    def getaddrinfo(host: str, port: Any, *args: Any, **kwargs: Any) -> Any:
        return [
            (
                socket.AF_INET6 if ":" in a else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (a, 443),
            )
            for a in addresses
        ]

    return getaddrinfo


BLOCKED = [
    "127.0.0.1",
    "127.255.255.254",
    "10.0.0.1",
    "172.16.5.4",
    "172.31.255.255",
    "192.168.1.1",
    "169.254.169.254",
    "100.64.0.1",
    "224.0.0.1",
    "239.255.255.250",
    "240.0.0.1",
    "255.255.255.255",
    "0.0.0.0",  # noqa: S104 (a blocked-range sample, not a bind)
    "192.0.2.1",
    "::1",
    "::",
    "fe80::1",
    "fc00::1",
    "fd12:3456::1",
    "ff02::1",
    "2001:db8::1",
    "::ffff:127.0.0.1",
    "::ffff:10.1.2.3",
    "::ffff:169.254.169.254",
    "64:ff9b::7f00:1",
    "2002:7f00:1::1",
    "2002:a9fe:a9fe::1",
]


@pytest.mark.parametrize("address", BLOCKED)
def test_a_blocked_address_is_refused(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    ipaddress.ip_address(address)  # the sample itself is well formed
    monkeypatch.setattr(check.socket, "getaddrinfo", fake_resolver([address]))
    with pytest.raises(check.Unreadable):
        check.public_addresses("links.example.com")


def test_one_bad_address_in_a_mix_refuses_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mix = ["93.184.216.34", "2606:2800:220:1::1", "127.0.0.1"]
    monkeypatch.setattr(check.socket, "getaddrinfo", fake_resolver(mix))
    with pytest.raises(check.Unreadable):
        check.public_addresses("links.example.com")
    monkeypatch.setattr(check.socket, "getaddrinfo", fake_resolver(mix[:2]))
    assert check.public_addresses("links.example.com")[0] == "93.184.216.34"


def test_an_empty_or_failing_resolution_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(check.socket, "getaddrinfo", fake_resolver([]))
    with pytest.raises(check.Unreadable):
        check.public_addresses("links.example.com")

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise socket.gaierror("no")

    monkeypatch.setattr(check.socket, "getaddrinfo", boom)
    with pytest.raises(check.Unreadable):
        check.public_addresses("links.example.com")


def test_the_robots_fetch_connects_to_the_validated_address_with_sni(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(check.socket, "getaddrinfo", fake_resolver(["93.184.216.34"]))
    connected: list[Any] = []
    wrapped: list[Any] = []

    class FakeContext:
        def wrap_socket(self, sock: Any, server_hostname: str) -> Any:
            wrapped.append(server_hostname)
            raise OSError("stop here")

    def fake_create_connection(address: Any, timeout: Any = None) -> Any:
        connected.append(address)
        return object()

    monkeypatch.setattr(check.socket, "create_connection", fake_create_connection)
    monkeypatch.setattr(check.ssl, "create_default_context", lambda: FakeContext())
    with pytest.raises(check.Unreadable):
        check.fetch_robots("links.example.com")
    assert connected == [("93.184.216.34", 443)]  # never the hostname
    assert wrapped == ["links.example.com"]  # SNI and certificate name


@pytest.mark.parametrize("status", [301, 302, 307, 403, 404])
def test_the_robots_fetch_refuses_redirects_and_other_statuses(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    monkeypatch.setattr(check.socket, "getaddrinfo", fake_resolver(["93.184.216.34"]))

    class Response:
        def __init__(self) -> None:
            self.status = status

    class Conn:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def request(self, *a: Any, **k: Any) -> None:
            pass

        def getresponse(self) -> Any:
            return Response()

        def close(self) -> None:
            pass

    monkeypatch.setattr(check, "PinnedHTTPSConnection", Conn)
    with pytest.raises(check.Unreadable):
        check.fetch_robots("links.example.com")


def test_there_is_no_robots_url_argument_any_more() -> None:
    with pytest.raises(SystemExit):
        check.main(
            ["--live", "--zone-id", "0" * 32, "--robots-url", "https://x.invalid/"]
        )


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "http://x", "a b", "ex\x00.com", "\u00fc.example.com"]
)
def test_a_bad_site_host_is_exit_2_before_any_request(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], host: str
) -> None:
    def never(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network used")

    monkeypatch.setenv(check.TOKEN_ENV, SENTINEL)
    monkeypatch.setattr(check, "api_result", never)
    monkeypatch.setattr(check, "fetch_robots", never)
    monkeypatch.setattr(check.socket, "getaddrinfo", never)
    code = check.main(["--live", "--zone-id", "0" * 32, "--site-host", host])
    out = capsys.readouterr()
    assert code == 2
    assert SENTINEL not in out.out + out.err
    assert "Traceback" not in out.err


# --- robust failure: fixed message, exit 2, no exception text -----------------


@pytest.mark.parametrize(
    "content", [None, b"{not json", b"\xff\xfe", b"[]", b'{"schema": 1}']
)
def test_a_missing_or_malformed_desired_state_is_exit_2_under_live(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    content: bytes | None,
) -> None:
    monkeypatch.setenv(check.TOKEN_ENV, SENTINEL)
    monkeypatch.setattr(check, "api_result", lambda p, t: {})
    (tmp_path / EDGE).mkdir(parents=True)
    if content is not None:
        (tmp_path / EDGE / "desired-state.json").write_bytes(content)
    monkeypatch.setattr(check, "ROOT", tmp_path)
    code = check.main(["--live", "--zone-id", "0" * 32])
    out = capsys.readouterr()
    assert code == 2
    assert "Traceback" not in out.err
    assert out.err.strip() == "UNREADABLE desired state"  # fixed message


@pytest.mark.parametrize("error", [ValueError, UnicodeError, OSError])
def test_request_construction_errors_are_unreadable_without_detail(
    monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    def bad_request(*args: Any, **kwargs: Any) -> Any:
        raise error("detail " + SENTINEL)

    monkeypatch.setattr(urllib.request, "Request", bad_request)
    with pytest.raises(check.Unreadable) as info:
        check.get(check.API + "/zones/x", SENTINEL)
    assert SENTINEL not in str(info.value)


def test_a_garbage_response_body_is_unreadable_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(check.TOKEN_ENV, SENTINEL)
    monkeypatch.setattr(check, "get", lambda url, token: b"\xff\xfe not json")
    assert check.main(["--live", "--zone-id", "0" * 32]) == 2
    assert "Traceback" not in capsys.readouterr().err
