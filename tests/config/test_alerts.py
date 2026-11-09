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


def test_channels_without_events_is_off() -> None:
    assert worker_value(Alerts(channels=["discord"])) is None
    assert worker_value(Alerts(events=["kit_unlocked"])) is None


def test_worker_value_is_the_json_the_worker_parses() -> None:
    a = Alerts(channels=["discord", "email"], events=["kit_unlocked"])
    v = worker_value(a)
    assert v is not None
    assert json.loads(v) == {
        "channels": ["discord", "email"],
        "events": ["kit_unlocked"],
        "visitor_email": "full",
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
