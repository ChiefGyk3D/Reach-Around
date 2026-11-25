# SPDX-License-Identifier: Apache-2.0
"""The alerts config section (#252): default off, closed lists, Worker value."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from reach_around.config.alerts import (
    DEFAULT_MAX_PER_MINUTE,
    MAX_PER_MINUTE_CEILING,
    Alerts,
    worker_value,
)

ROOT = Path(__file__).resolve().parents[2]


def test_default_is_off() -> None:
    a = Alerts()
    assert not a.enabled
    assert worker_value(a) is None


@pytest.mark.parametrize(
    "one_sided",
    [{"channels": ["discord"]}, {"events": ["kit_unlocked"]}],
)
def test_one_sided_alerts_validator_message_names_no_value(
    one_sided: dict[str, object],
) -> None:
    with pytest.raises(ValidationError) as err:
        Alerts(**one_sided)  # type: ignore[arg-type]
    # Only the validator's own message is checked here. Pydantic's error dicts
    # carry the rejected input; the loader tests below check what is shown.
    text = " ".join(e["msg"] for e in err.value.errors())
    assert "channels and events" in text
    assert "discord" not in text and "kit_unlocked" not in text


def test_visitor_email_defaults_to_masked() -> None:
    assert Alerts().visitor_email == "masked"
    v = worker_value(Alerts(channels=["discord"], events=["kit_unlocked"]))
    assert v is not None
    assert json.loads(v)["visitor_email"] == "masked"
    full = Alerts(channels=["discord"], events=["kit_unlocked"], visitor_email="full")
    assert Alerts.model_validate(full.model_dump()).visitor_email == "full"


def test_worker_value_is_the_json_the_worker_parses() -> None:
    a = Alerts(channels=["discord", "email"], events=["kit_unlocked"])
    v = worker_value(a)
    assert v is not None
    assert json.loads(v) == {
        "channels": ["discord", "email"],
        "events": ["kit_unlocked"],
        "visitor_email": "masked",
        "max_per_minute": DEFAULT_MAX_PER_MINUTE,
    }


@pytest.mark.parametrize(
    "bad",
    [
        {"channels": ["slack"]},
        {"events": ["everything"]},
        {"channels": ["discord", "discord"]},
        {"visitor_email": "yes"},
        {"max_per_minute": 0},
        {"max_per_minute": MAX_PER_MINUTE_CEILING + 1},
        {"max_per_minute": "5"},
        {"webhook": "https://example.com"},
    ],
)
def test_bad_values_are_refused(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Alerts(**bad)  # type: ignore[arg-type]


def test_matrix_needs_an_exact_allowed_host() -> None:
    with pytest.raises(ValidationError):
        Alerts(channels=["matrix"], events=["kit_unlocked"])
    a = Alerts(
        channels=["matrix"],
        events=["kit_unlocked"],
        matrix={"allowed_hosts": ["matrix.example.org"]},  # type: ignore[arg-type]
    )
    v = worker_value(a)
    assert v is not None
    assert json.loads(v)["matrix"] == {"allowed_hosts": ["matrix.example.org"]}


@pytest.mark.parametrize(
    "host",
    [
        "Matrix.Example.org",
        "127.0.0.1",
        "127.0.0.1.nip.io",
        "10-0-0-1.example.org",
        "7f000001.example.org",
        "x.sslip.io",
        "host.local",
        "localhost",
        "https://matrix.example.org",
        "matrix.example.org:8448",
        "nas",
    ],
)
def test_bad_allowed_hosts_are_refused(host: str) -> None:
    with pytest.raises(ValidationError):
        Alerts(matrix={"allowed_hosts": [host]})  # type: ignore[arg-type]


def test_too_many_or_repeated_hosts_are_refused() -> None:
    many = [f"h{i}.example.org" for i in range(5)]
    with pytest.raises(ValidationError):
        Alerts(matrix={"allowed_hosts": many})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Alerts(matrix={"allowed_hosts": ["a.example.org", "a.example.org"]})  # type: ignore[arg-type]


def test_host_denylists_match_the_worker() -> None:
    from reach_around.config.alerts import PRIVATE_SUFFIXES, WILDCARD_DNS_SUFFIXES

    src = (ROOT / "worker/src/alerts/validate.ts").read_text(encoding="utf-8")

    def ts_list(name: str) -> set[str]:
        m = re.search(name + r"[^=]*= \[(.*?)\];", src, re.S)
        assert m
        return set(re.findall(r'"([a-z0-9.-]+)"', m.group(1)))

    assert ts_list("PRIVATE_SUFFIXES") == set(PRIVATE_SUFFIXES)
    assert ts_list("WILDCARD_DNS_SUFFIXES") == set(WILDCARD_DNS_SUFFIXES)


def test_the_lists_match_the_worker() -> None:
    src = (ROOT / "worker/src/alerts/types.ts").read_text(encoding="utf-8")
    cfg = (ROOT / "worker/src/alerts/config.ts").read_text(encoding="utf-8")

    def ts_list(name: str) -> set[str]:
        m = re.search(name + r": readonly \w+\[\] = \[(.*?)\]", src)
        assert m
        return set(re.findall(r'"([a-z_]+)"', m.group(1)))

    from typing import get_args

    from reach_around.config.alerts import AlertChannel, AlertEventName

    assert ts_list("CHANNEL_IDS") == set(get_args(AlertChannel))
    assert ts_list("EVENT_KINDS") == set(get_args(AlertEventName))
    assert f"DEFAULT_MAX_PER_MINUTE = {DEFAULT_MAX_PER_MINUTE}" in cfg
    assert f"MAX_PER_MINUTE_CEILING = {MAX_PER_MINUTE_CEILING}" in cfg


def test_build_manifest_carries_alerts_only_when_on() -> None:
    from reach_around.config.alerts import Alerts
    from reach_around.site.build import BuildManifest, manifest_json

    off = BuildManifest(links={}, lead_retention_days=30)
    assert "alerts_config" not in manifest_json(off)
    on = BuildManifest(
        links={},
        lead_retention_days=30,
        alerts_config=worker_value(
            Alerts(channels=["discord"], events=["kit_unlocked"])
        ),
    )
    assert json.loads(json.loads(manifest_json(on))["alerts_config"])["channels"] == [
        "discord"
    ]


SENTINEL_HOST = "customer-9f31.example.org"
CREDENTIAL_LIKE_HOST = "tok-live-9f31a7c2d4e8b6c0.example.org"
ONE_SIDED_ALERTS = (
    "\nalerts:\n  channels: [discord]\n  matrix:\n    allowed_hosts:\n"
    f"      - {SENTINEL_HOST}\n      - {CREDENTIAL_LIKE_HOST}\n"
)


def _one_sided_config(tmp_path: Path) -> Path:
    base = ROOT / "examples/instance-variants/minimal"
    text = (base / "site.yaml").read_text(encoding="utf-8")
    cfg = tmp_path / "site.yaml"
    cfg.write_text(text + ONE_SIDED_ALERTS, encoding="utf-8")
    return cfg


def _assert_no_input_shown(shown: str) -> None:
    for value in (SENTINEL_HOST, CREDENTIAL_LIKE_HOST, "discord", "allowed_hosts'"):
        assert value not in shown
    assert "input_value" not in shown and "input=" not in shown
    assert "{'channels'" not in shown


def test_loader_error_for_one_sided_alerts_shows_no_input_values(
    tmp_path: Path,
) -> None:
    from reach_around.config import ConfigError, load_site_config

    with pytest.raises(ConfigError) as err:
        load_site_config(_one_sided_config(tmp_path))
    shown = str(err.value)
    assert "channels and events" in shown
    _assert_no_input_shown(shown)


def test_build_error_for_one_sided_alerts_shows_no_input_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from reach_around.cli import main

    out = tmp_path / "out"
    cfg = _one_sided_config(tmp_path)
    assert main(["build", "--config", str(cfg), "--out", str(out)]) == 1
    seen = capsys.readouterr()
    assert "channels and events" in seen.err
    _assert_no_input_shown(seen.err + seen.out)


def _branch_accepts(branch: dict[str, object], doc: dict[str, list[str]]) -> bool:
    props = branch.get("properties", {})
    assert isinstance(props, dict)
    for name, rule in props.items():
        if name not in doc:
            continue
        n = len(doc[name])
        if n < rule.get("minItems", 0) or n > rule.get("maxItems", n):
            return False
    required = branch.get("required", [])
    assert isinstance(required, list)
    return all(name in doc for name in required)


@pytest.mark.parametrize(
    ("doc", "ok"),
    [
        ({}, True),
        ({"channels": [], "events": []}, True),
        ({"channels": ["discord"], "events": ["kit_unlocked"]}, True),
        ({"channels": ["discord"]}, False),
        ({"events": ["kit_unlocked"]}, False),
        ({"channels": ["discord"], "events": []}, False),
        ({"channels": [], "events": ["kit_unlocked"]}, False),
    ],
)
def test_json_schema_refuses_one_sided_alerts(
    doc: dict[str, list[str]], ok: bool
) -> None:
    from reach_around.schema import build_schema

    # the schema made now, and the committed copy (test_schema checks they match)
    published = json.loads(
        (ROOT / "docs" / "site.schema.json").read_text(encoding="utf-8")
    )
    for schema in (build_schema(), published):
        branches = schema["$defs"]["Alerts"]["anyOf"]
        assert any(_branch_accepts(b, doc) for b in branches) is ok
    # the Python model agrees with the schema on every case
    if ok:
        Alerts(**doc)  # type: ignore[arg-type]
    else:
        with pytest.raises(ValidationError):
            Alerts(**doc)  # type: ignore[arg-type]
