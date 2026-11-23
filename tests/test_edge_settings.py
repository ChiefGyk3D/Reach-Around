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
FILES = (
    "desired-state.json",
    "main.tf",
    "versions.tf",
    ".terraform.lock.hcl",
    "provider-fields.json",
)
OVERRIDE_VARS = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
)


def load(path: Path, name: str) -> ModuleType:
    spec = spec_from_loader(name, SourceFileLoader(name, str(path)))
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check: Any = load(REPO / "scripts" / "check-edge.py", "check_edge")
SENTINEL = "sentinel-token-value-123456"


@pytest.fixture(autouse=True)
def clean_ambient_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in OVERRIDE_VARS:
        monkeypatch.delenv(name, raising=False)


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


def test_unmanaged_wording_never_claims_a_field_is_not_sent(root: Path) -> None:
    for claim in ("never sent", "are never sent by an apply", "not sent"):
        data = copy.deepcopy(desired())
        data["_comment"] = f"unmanaged fields are {claim}"
        write_desired(root, data)
        assert any("sent" in p for p in check.offline_problems(root)), claim
    write_desired(root, desired())
    main_tf = root / EDGE / "main.tf"
    original = main_tf.read_text()
    main_tf.write_text(original + "\n# An unmanaged field is never sent.\n")
    assert any("sent" in p for p in check.offline_problems(root))
    main_tf.write_text(original)
    (root / "docs").mkdir()
    shutil.copy(REPO / "docs" / "edge.md", root / "docs" / "edge.md")
    assert check.offline_problems(root) == []
    doc = root / "docs" / "edge.md"
    doc.write_text(doc.read_text() + "\nThose fields would never send.\n")
    assert any("sent" in p or "send" in p for p in check.offline_problems(root))


def test_the_real_files_state_that_sending_is_unverified() -> None:
    for path in (
        REPO / EDGE / "desired-state.json",
        REPO / EDGE / "main.tf",
        REPO / "docs" / "edge.md",
    ):
        assert "unverified" in path.read_text(), path.name


def manifest() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (REPO / EDGE / "provider-fields.json").read_text()
    )
    return data


def test_the_manifest_matches_the_checker_and_the_desired_state_independently(
    root: Path,
) -> None:
    m = manifest()
    assert m["provider_version"] == "5.29.0"
    assert "providers schema -json" in m["command"]
    assert set(m["sendable_fields"]) == set(check.PROVIDER_FIELDS)
    assert set(m["sendable_fields"]) == set(desired()["bot_management"])
    # drop a field from the checker's list AND the desired state only: the
    # manifest, generated from the provider schema, still notices
    data = copy.deepcopy(desired())
    del data["bot_management"]["suppress_session_score"]
    write_desired(root, data)
    reduced = tuple(f for f in check.PROVIDER_FIELDS if f != "suppress_session_score")
    saved = check.PROVIDER_FIELDS
    check.PROVIDER_FIELDS = reduced
    try:
        assert any("manifest" in p for p in check.offline_problems(root))
    finally:
        check.PROVIDER_FIELDS = saved


def test_the_manifest_version_must_match_the_pin(root: Path) -> None:
    path = root / EDGE / "provider-fields.json"
    data = json.loads(path.read_text())
    data["provider_version"] = "5.30.0"
    path.write_text(json.dumps(data))
    assert any("manifest" in p and "version" in p for p in check.offline_problems(root))


def test_a_missing_or_garbled_manifest_fails_the_offline_check(root: Path) -> None:
    path = root / EDGE / "provider-fields.json"
    path.write_text("{garbled")
    assert check.offline_problems(root)
    path.unlink()
    assert check.offline_problems(root)


gen: Any = load(REPO / "scripts" / "gen-provider-fields.py", "gen_provider_fields")


def schema(attributes: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider_schemas": {
            "registry.opentofu.org/cloudflare/cloudflare": {
                "resource_schemas": {
                    "cloudflare_bot_management": {"block": {"attributes": attributes}}
                }
            }
        }
    }


def test_the_generator_reads_sendable_fields_from_a_schema() -> None:
    attrs = {
        "zone_id": {"type": "string", "required": True},
        "id": {"type": "string", "computed": True},
        "using_latest_model": {"type": "bool", "computed": True},
        "aisearch": {"type": "string", "optional": True, "computed": True},
        "enable_js": {"type": "bool", "optional": True, "computed": True},
        "bot_preference_sync_enabled": {"type": "bool", "optional": True},
    }
    result = gen.build_manifest(schema(attrs), "5.29.0", "cmd")
    assert result["sendable_fields"] == [
        "ai_search",
        "bot_preference_sync_enabled",
        "enable_js",
    ]
    assert result["computed_only"] == ["id", "using_latest_model"]
    assert result["provider_version"] == "5.29.0"
    assert result["command"] == "cmd"


def test_the_generator_refuses_a_schema_without_the_resource() -> None:
    with pytest.raises(ValueError):
        gen.build_manifest({"provider_schemas": {}}, "5.29.0", "cmd")


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


def test_get_never_follows_a_redirect_and_ignores_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    redirect = [h for h in seen if isinstance(h, type) and h.__name__ == "NoRedirect"]
    assert len(redirect) == 1
    assert (
        redirect[0]().redirect_request(None, None, 302, "", {}, "https://x.invalid/")
        is None
    )
    proxies = [h for h in seen if isinstance(h, urllib.request.ProxyHandler)]
    assert (
        len(proxies) == 1 and vars(proxies[0])["proxies"] == {}
    )  # no environment proxy
    https = [h for h in seen if isinstance(h, urllib.request.HTTPSHandler)]
    assert len(https) == 1  # an explicit TLS handler, not the implicit default


def test_the_real_opener_has_no_proxy_handler_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=check.ssl.create_default_context()),
    )
    # an empty ProxyHandler installs no *_open methods, so it is left out, and the
    # default environment-reading one is not added beside it
    assert not [
        h
        for h in vars(opener)["handlers"]
        if isinstance(h, urllib.request.ProxyHandler)
    ]


@pytest.mark.parametrize("name", OVERRIDE_VARS)
def test_a_proxy_or_ca_override_refuses_before_any_socket(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    def no_socket(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a socket was opened")

    for target in ("create_connection", "getaddrinfo", "socket"):
        monkeypatch.setattr(check.socket, target, no_socket)
    monkeypatch.setattr(urllib.request, "build_opener", no_socket)
    monkeypatch.setenv(check.TOKEN_ENV, SENTINEL)
    monkeypatch.setenv(name, "/somewhere/else")
    code = check.main(["--live", "--zone-id", "0" * 32])
    out = capsys.readouterr()
    assert code == 2
    assert out.err.strip() == "REFUSED proxy or CA override variable is set"
    assert SENTINEL not in out.out + out.err
    with pytest.raises(check.Unreadable):  # the same guard inside get()
        check.get(check.API + "/zones/x", SENTINEL)


def test_the_public_robots_fetch_is_not_blocked_by_the_override_guard() -> None:
    assert check.override_variables_set({}) == []
    assert check.override_variables_set({"HTTPS_PROXY": "x"}) == ["HTTPS_PROXY"]


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
    env = {"PATH": "/usr/bin:/bin"}
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


ALLOWED_TOFU_ENV = {
    "PATH",
    "HOME",
    "TMPDIR",
    "TF_DATA_DIR",
    "TF_VAR_zone_id",
    "TF_CLI_CONFIG_FILE",
    "CLOUDFLARE_API_TOKEN",
    "_",  # set by bash itself for the program it starts
    "SHLVL",  # added by bash itself, no secret
    "LC_CTYPE",  # added by bash itself, no secret
}


def make_fake_tofu(home: Path, dump: Path, mode: int = 0o755) -> Path:
    bindir = home / ".local" / "bin"
    bindir.mkdir(parents=True)
    fake = bindir / "tofu"
    fake.write_text(
        "#!" + sys.executable + "\n"
        "import json, os, sys\n"
        f"with open({str(dump)!r}, 'a') as h:\n"
        "    row = [sys.argv[1:], dict(os.environ), os.getcwd()]\n"
        "    h.write(json.dumps(row) + chr(10))\n"
    )
    fake.chmod(mode)
    return fake


def plan_env(home: Path, extra: dict[str, str]) -> dict[str, str]:
    # TOFU_BIN pins the fake: a real tofu must never run from these tests.
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "TOFU_BIN": str(home / ".local" / "bin" / "tofu"),
        "TF_VAR_zone_id": "1" * 32,
        "CLOUDFLARE_API_TOKEN": SENTINEL,
        **extra,
    }


def run_plan(
    tmp_path: Path, extra: dict[str, str], mode: int = 0o755
) -> tuple[subprocess.CompletedProcess[str], list[Any]]:
    home = tmp_path / "home"
    dump = tmp_path / "env.json"
    make_fake_tofu(home, dump, mode)
    run = subprocess.run(  # noqa: S603 (fixed repo script; fake tofu, no network)
        [str(REPO / EDGE / "plan.sh")],
        env=plan_env(home, extra),
        capture_output=True,
        text=True,
        check=False,
    )
    calls = (
        [json.loads(x) for x in dump.read_text().splitlines()] if dump.exists() else []
    )
    return run, calls


def test_plan_gives_tofu_only_the_allow_listed_environment(tmp_path: Path) -> None:
    hostile = {
        "TF_CLI_CONFIG_FILE": "/nonexistent/evil.tfrc",
        "TF_PLUGIN_CACHE_DIR": "/nonexistent/evil-cache",
        "TF_DATA_DIR": "/nonexistent/evil-data",
        "TF_VAR_other": "x",
        "TF_LOG": "TRACE",
        "TF_LOG_PATH": "/nonexistent/leak.log",
        "HTTPS_PROXY": "http://proxy.invalid:3128",
        "https_proxy": "http://proxy.invalid:3128",
        "ALL_PROXY": "http://proxy.invalid:3128",
        "SSL_CERT_FILE": "/nonexistent/evil-ca.pem",
        "SSL_CERT_DIR": "/nonexistent/evil-cas",
        "CLOUDFLARE_API_KEY": "k",
        "CLOUDFLARE_EMAIL": "e",
        "CLOUDFLARE_API_USER_SERVICE_KEY": "u",
        "CLOUDFLARE_API_BASE_URL": "https://attacker.invalid",
        "TF_CLI_ARGS": "-lock=false",
        "TF_CLI_ARGS_plan": "-destroy",
        "LD_PRELOAD": "/nonexistent/evil.so",
        "BASH_ENV": "/nonexistent/evil.sh",
        "ENV": "/nonexistent/evil.sh",
        "OPENTOFU_THING": "x",
    }
    run, calls = run_plan(tmp_path, hostile)
    assert run.returncode == 0, run.stderr
    assert [c[0][0] for c in calls] == ["init", "plan"]
    for _argv, env, cwd in calls:
        assert set(env) <= ALLOWED_TOFU_ENV, sorted(set(env) - ALLOWED_TOFU_ENV)
        assert env["CLOUDFLARE_API_TOKEN"] == SENTINEL
        assert env["TF_VAR_zone_id"] == "1" * 32
        assert env["PATH"] == "/usr/bin:/bin"
        assert env["HOME"] != str(tmp_path / "home")  # a fresh scratch home
        assert env["TF_CLI_CONFIG_FILE"] == str(REPO / EDGE / "tofu.rc")
        scratch = Path(env["HOME"]).parent
        assert Path(env["TF_DATA_DIR"]).parent == scratch == Path(env["TMPDIR"]).parent
        assert Path(cwd) == REPO / EDGE
    assert not Path(calls[0][1]["HOME"]).parent.exists()  # removed again
    assert SENTINEL not in run.stdout + run.stderr
    assert not any(a in ("apply", "destroy", "import") for c in calls for a in c[0])


def test_plan_refuses_a_relative_tofu_bin(tmp_path: Path) -> None:
    run, calls = run_plan(tmp_path, {"TOFU_BIN": "tofu"})
    assert run.returncode == 65 and calls == []


def test_plan_ignores_a_tofu_earlier_on_path(tmp_path: Path) -> None:
    evil = tmp_path / "evil"
    evil.mkdir()
    marker = tmp_path / "evil-ran"
    (evil / "tofu").write_text(f"#!/bin/sh\ntouch {marker}\n")
    (evil / "tofu").chmod(0o755)
    run, calls = run_plan(tmp_path, {"PATH": f"{evil}:/usr/bin:/bin"})
    assert run.returncode == 0, run.stderr
    assert not marker.exists()
    assert len(calls) == 2


@pytest.mark.parametrize("mode", [0o777, 0o757])
def test_plan_refuses_a_binary_writable_by_others(tmp_path: Path, mode: int) -> None:
    run, calls = run_plan(tmp_path, {}, mode)
    assert run.returncode == 65
    assert calls == []
    assert "no trusted tofu" in run.stderr
    assert SENTINEL not in run.stdout + run.stderr


def test_plan_refuses_a_binary_in_a_directory_writable_by_others(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    make_fake_tofu(home, tmp_path / "env.json")
    (home / ".local" / "bin").chmod(0o777)  # no sticky bit
    run = subprocess.run(  # noqa: S603 (fixed repo script; fake tofu, no network)
        [str(REPO / EDGE / "plan.sh")],
        env=plan_env(home, {}),
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 65
    assert not (tmp_path / "env.json").exists()


def test_plan_refuses_a_malformed_zone_id(tmp_path: Path) -> None:
    run, calls = run_plan(tmp_path, {"TF_VAR_zone_id": "not-a-zone"})
    assert run.returncode == 64
    assert calls == []


def test_plan_usage_and_exit_codes_pass_through(tmp_path: Path) -> None:
    run, calls = run_plan(tmp_path, {})
    assert run.returncode == 0 and len(calls) == 2


def test_the_reviewed_cli_config_installs_only_the_locked_provider() -> None:
    text = (REPO / EDGE / "tofu.rc").read_text()
    code = "\n".join(x for x in text.splitlines() if not x.lstrip().startswith("#"))
    assert "dev_overrides" not in code
    assert "filesystem_mirror" not in code and "network_mirror" not in code
    assert "plugin_cache_dir" not in code
    assert 'include = ["registry.opentofu.org/cloudflare/cloudflare"]' in code
    assert "direct" in code
    assert code.count("{") == code.count("}") > 0


def test_the_doc_does_not_overclaim_the_plan_environment() -> None:
    doc = (REPO / "docs" / "edge.md").read_text()
    assert "passes only `CLOUDFLARE_API_TOKEN` to the tool" not in doc
    assert "allow-list" in doc and "tofu.rc" in doc


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
