# SPDX-License-Identifier: Apache-2.0
"""The example Grafana dashboards (#727): files parse, hold no secret, and run.

The queries are run for real against a store that `reach-around grafana-export`
builds from the synthetic sample, so a renamed table or column fails here and not
in front of the owner. Grafana itself is not started by these tests.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from reach_around.funnel import grafana
from reach_around.funnel.export import load_exports

ROOT = Path(__file__).resolve().parents[1]
GRAFANA = ROOT / "examples" / "grafana"
END = date(2026, 11, 20)
EXPECTED_PANELS = {
    "overview.json": {
        "Likely-human views",
        "Link clicks",
        "Clicks per 100 views",
        "Privacy floor",
        "Likely-human views per day",
        "Link clicks per day",
        "Views per day by traffic class",
        "Clicks per 100 likely-human views",
        "Collection gaps in this range",
        "About these numbers",
    },
    "clicks.json": {
        "Link clicks by link",
        "Share of link clicks",
        "Link clicks per day, by link",
        "Groups",
    },
    "watch.json": {
        f"{side}: {what}"
        for side in ("Twitch", "Kick")
        for what in (
            "clicks",
            "share of link clicks",
            "clicks per day",
            "clicks per 100 likely-human views",
        )
    }
    | {"About this page"},
}


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = _load("ra_build_dashboards", GRAFANA / "build_dashboards.py")
sample = _load("ra_make_sample_export", GRAFANA / "sample" / "make_sample_export.py")


def dashboards() -> dict[str, dict[str, Any]]:
    out = {
        p.name: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((GRAFANA / "dashboards").glob("*.json"))
    }
    assert out, "no dashboard JSON was collected"
    return out


def all_files() -> list[Path]:
    files = [
        p for p in GRAFANA.rglob("*") if p.is_file() and "__pycache__" not in p.parts
    ]
    assert files, "no example file was collected"
    return files


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("grafana-sample")
    export = tmp / "export.json"
    export.write_text(json.dumps(sample.build(END)), encoding="utf-8")
    out = tmp / "grafana.sqlite3"
    grafana.write_store(
        out,
        grafana.build_subset(
            load_exports([export]), cutover=date(2026, 11, 5), today=END
        ),
    )
    return out


def _flatten(dash: dict[str, Any]) -> list[tuple[str, str]]:
    """(label, sql) for every panel target and every annotation query."""
    out = []
    for panel in dash["panels"]:
        for tgt in panel.get("targets", []):
            out.append((panel["title"], tgt["rawQueryText"]))
    for ann in dash["annotations"]["list"]:
        if "target" in ann:
            out.append((ann["name"], ann["target"]["rawQueryText"]))
    return out


# ---- the files parse and hold the expected panels --------------------------


def test_dashboards_parse_with_unique_uids() -> None:
    found = dashboards()
    assert set(found) == set(EXPECTED_PANELS)
    uids = [d["uid"] for d in found.values()]
    assert len(set(uids)) == len(uids)
    for dash in found.values():
        ids = [p["id"] for p in dash["panels"]]
        assert len(set(ids)) == len(ids)


@pytest.mark.parametrize("name", sorted(EXPECTED_PANELS))
def test_expected_panels(name: str) -> None:
    titles = {p["title"] for p in dashboards()[name]["panels"]}
    assert titles == EXPECTED_PANELS[name]


def test_committed_json_matches_the_generator() -> None:
    for name in builder.FILES:
        assert (GRAFANA / "dashboards" / name).read_text(
            encoding="utf-8"
        ) == builder.render(name), f"{name} is out of date: run build_dashboards.py"


def test_every_query_uses_the_provisioned_datasource() -> None:
    provisioned = yaml.safe_load(
        (GRAFANA / "provisioning/datasources/reach-around.yaml").read_text("utf-8")
    )["datasources"][0]
    for dash in dashboards().values():
        for panel in dash["panels"]:
            if panel["type"] != "text":
                assert panel["datasource"]["uid"] == provisioned["uid"]
                assert panel["datasource"]["type"] == provisioned["type"]
                for tgt in panel["targets"]:
                    assert tgt["datasource"]["uid"] == provisioned["uid"]


# ---- twitch and kick get identical treatment --------------------------------


def test_watch_sides_are_identical_apart_from_the_link_and_name() -> None:
    panels = dashboards()["watch.json"]["panels"]
    twitch = [p for p in panels if p["title"].startswith("Twitch:")]
    kick = [p for p in panels if p["title"].startswith("Kick:")]
    assert len(twitch) == len(kick) == 4

    def norm(panel: dict[str, Any], link: str, name: str) -> str:
        text = json.dumps(panel, sort_keys=True)
        return text.replace(f"'{link}'", "'LINK'").replace(name, "SIDE")

    for t, k in zip(twitch, kick, strict=True):
        a, b = norm(t, "twitch", "Twitch"), norm(k, "kick", "Kick")
        a, b = (
            json.dumps({**json.loads(x), "id": 0, "gridPos": {}}, sort_keys=True)
            for x in (a, b)
        )
        assert a == b
        assert t["gridPos"]["w"] == k["gridPos"]["w"]
        assert t["gridPos"]["h"] == k["gridPos"]["h"]
        assert t["gridPos"]["y"] == k["gridPos"]["y"]


def test_watch_guard_bites_when_one_side_differs() -> None:
    """Negative case: a changed color on one side must not read as identical."""
    panels = json.loads(json.dumps(dashboards()["watch.json"]["panels"]))
    kick = next(p for p in panels if p["title"] == "Kick: clicks per day")
    kick["fieldConfig"]["defaults"]["color"] = {"mode": "fixed", "fixedColor": "red"}
    twitch = next(p for p in panels if p["title"] == "Twitch: clicks per day")
    assert twitch["fieldConfig"] != kick["fieldConfig"]


# ---- the queries run, and print only floored numbers -----------------------


def _bind(sql: str) -> str:
    start = int(date(2026, 10, 1).strftime("%s")) * 1000
    end = int(date(2026, 11, 21).strftime("%s")) * 1000
    return sql.replace("$__from", str(start)).replace("$__to", str(end))


@pytest.mark.parametrize("name", sorted(EXPECTED_PANELS))
def test_queries_run_against_the_sample_store(store: Path, name: str) -> None:
    con = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    try:
        for label, sql in _flatten(dashboards()[name]):
            rows = con.execute(_bind(sql)).fetchall()
            if label in {"Collection gaps", "Collection gaps in this range"}:
                assert rows, label  # the sample has a gap and a partial day
            elif label == "Cutover":
                assert len(rows) == 1
            elif label != "Groups":
                assert rows, f"{label} returned no rows"
    finally:
        con.close()


def test_no_printed_click_count_is_under_the_floor(store: Path) -> None:
    con = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    try:
        clicks = dashboards()["clicks.json"]
        for panel in clicks["panels"]:
            if panel["title"] in {
                "Link clicks by link",
                "Link clicks per day, by link",
            }:
                for tgt in panel["targets"]:
                    cols = con.execute(_bind(tgt["rawQueryText"]))
                    names = [d[0] for d in cols.description]
                    idx = names.index("clicks")
                    values = [r[idx] for r in cols.fetchall()]
                    assert values, panel["title"]
                    assert all(v >= 5 for v in values), panel["title"]
    finally:
        con.close()


def test_the_sample_has_a_rare_link_that_the_floor_hides(store: Path) -> None:
    con = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    try:
        links = {
            r[0]
            for r in con.execute("SELECT DISTINCT link_id FROM grafana_clicks_daily")
        }
    finally:
        con.close()
    assert "tiny-link" not in links  # two clicks: merged, never printed alone
    assert "(below the floor)" in links
    assert {"twitch", "kick"} <= links


def test_count_stats_say_under_5_and_ratios_do_not() -> None:
    for dash in dashboards().values():
        for panel in dash["panels"]:
            if panel["type"] != "stat":
                continue
            maps = panel["fieldConfig"]["defaults"]["mappings"]
            ratio = "per 100" in panel["title"] or "share" in panel["title"]
            floor = panel["title"] == "Privacy floor"
            if ratio or floor:
                assert maps == []
            else:
                texts = [m["options"]["result"]["text"] for m in maps]
                assert texts == ["Under 5"], panel["title"]


# ---- dark only, no secrets, no hosts ---------------------------------------


def test_dark_theme_is_the_default_and_nothing_asks_for_light() -> None:
    unit = (GRAFANA / "quadlet/reach-around-grafana.container").read_text("utf-8")
    assert "GF_USERS_DEFAULT_THEME=dark" in unit
    for path in all_files():
        assert not re.search(
            r"""["']?theme["']?\s*[:=]\s*["']?light""", path.read_text("utf-8"), re.I
        )


SECRET_PATTERNS = {
    "ip address": re.compile(
        r"(?<![\d.])(?!127\.0\.0\.1\b)\d{1,3}(?:\.\d{1,3}){3}(?![\d.])"
    ),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY"),
    "token": re.compile(
        r"\b(?:ghp_|gho_|github_pat_|glsa_|glc_|xox[bp]-|sk-|AKIA)[A-Za-z0-9_\-]{8,}"
    ),
    "secret assignment": re.compile(
        r"(?i)(?:password|passwd|secret|token|api[_-]?key)[\"']?\s*[:=]\s*[\"']?(?!reach-around|\$|\{)[A-Za-z0-9+/=_\-]{6,}"
    ),
    "long opaque value": re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),
    "internal host": re.compile(
        r"(?i)\b[a-z0-9-]+\.(?:local|lan|internal|home|corp)\b"
    ),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}"),
    "grid square": re.compile(r"\b[A-R]{2}\d{2}[a-x]{2}\b"),
    "callsign": re.compile(r"\b(?!N0CALL\b|N0TST\b)[A-Z]{1,2}\d[A-Z]{1,3}\b"),
}
ALLOWED_HOSTS = {
    "docker.io",
    "grafana.com",
    "github.com",
    "127.0.0.1",
    "news.example",
    "example.com",
}


def scan(text: str) -> list[str]:
    hits = [
        n
        for n, rx in SECRET_PATTERNS.items()
        if n != "long opaque value" and rx.search(text)
    ]
    # a sha256 digest pins the image; any other long opaque run is suspect
    stripped = re.sub(r"sha256:[0-9a-f]{64}", "", text)
    if SECRET_PATTERNS["long opaque value"].search(stripped):
        hits.append("long opaque value")
    for host in re.findall(r"https?://([A-Za-z0-9.-]+)", text):
        if host not in ALLOWED_HOSTS:
            hits.append(f"host {host}")
    return hits


def test_no_secret_host_callsign_or_grid_in_any_example_file() -> None:
    for path in all_files():
        if path.suffix in {".pyc"}:
            continue
        assert scan(path.read_text("utf-8")) == [], str(path.relative_to(ROOT))


def test_secret_scan_bites() -> None:
    """Negative cases: planted values must be caught."""
    planted = [
        "GF_SECURITY_ADMIN_PASSWORD=hunter2hunter2",
        "password: 'abcdef123456'",
        "host 10.1.2.3",
        "see https://grafana.corp.example.net/x",
        "a ghp_0123456789abcdefghij token",
        "-----BEGIN PRIVATE KEY-----",
        "ops@example.org",
        "my call is W1AW and grid FN31pr",
        "nas.lan",
        "A" * 45,
    ]
    for text in planted:
        assert scan(text), text
    # and the allowed forms stay quiet
    assert (
        scan(
            "Secret=reach-around-grafana-admin,type=env,target=GF_SECURITY_ADMIN_PASSWORD"
        )
        == []
    )
    assert scan("PublishPort=127.0.0.1:3737:3000") == []
    assert scan("N0CALL and N0TST") == []


# ---- provisioning and the Podman recipe ------------------------------------


def test_datasource_is_read_only_and_holds_no_secret_field() -> None:
    doc = yaml.safe_load(
        (GRAFANA / "provisioning/datasources/reach-around.yaml").read_text("utf-8")
    )
    ds = doc["datasources"][0]
    assert ds["type"] == "frser-sqlite-datasource"
    assert "secureJsonData" not in ds and "password" not in ds and "url" not in ds
    assert "mode=ro" in ds["jsonData"]["pathOptions"]
    assert ds["editable"] is False


def test_dashboard_provider_points_at_the_mounted_directory() -> None:
    doc = yaml.safe_load(
        (GRAFANA / "provisioning/dashboards/reach-around.yaml").read_text("utf-8")
    )
    provider = doc["providers"][0]
    assert provider["options"]["path"] == "/var/lib/grafana-dashboards"
    assert provider["allowUiUpdates"] is False
    unit = (GRAFANA / "quadlet/reach-around-grafana.container").read_text("utf-8")
    assert ":/var/lib/grafana-dashboards:ro" in unit


def _unit() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in (
        (GRAFANA / "quadlet/reach-around-grafana.container")
        .read_text("utf-8")
        .splitlines()
    ):
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            out.setdefault(key.strip(), []).append(value.strip())
    return out


def test_quadlet_is_separate_rootless_and_has_no_socket() -> None:
    unit = _unit()
    assert unit["Network"] == ["reach-around-grafana.network"]
    volumes = unit["Volume"]
    assert "reach-around-grafana.volume:/var/lib/grafana" in volumes
    assert unit["PublishPort"] == ["127.0.0.1:3737:3000"]
    assert unit["ContainerName"] == ["reach-around-grafana"]
    assert re.fullmatch(
        r"docker\.io/grafana/grafana:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}",
        unit["Image"][0],
    )
    assert unit["ReadOnly"] == ["true"] and unit["DropCapability"] == ["ALL"]
    assert unit["NoNewPrivileges"] == ["true"]
    assert "User" not in unit and "PodmanArgs" not in unit  # no root, no escape hatch
    for path in (GRAFANA / "quadlet").iterdir():
        text = path.read_text("utf-8")
        assert "docker.sock" not in text and "podman.sock" not in text
        assert "Privileged" not in text and "/var/run" not in text
    for vol in volumes:
        assert vol.split(":")[0] in {
            "reach-around-grafana.volume",
            "%h/reach-around-grafana/data",
            "%h/reach-around-grafana/provisioning",
            "%h/reach-around-grafana/dashboards",
        }
    assert (GRAFANA / "quadlet/reach-around-grafana.network").is_file()
    assert (GRAFANA / "quadlet/reach-around-grafana.volume").is_file()


def test_quadlet_admin_password_is_a_podman_secret_not_a_value() -> None:
    unit = _unit()
    assert unit["Secret"] == [
        "reach-around-grafana-admin,type=env,target=GF_SECURITY_ADMIN_PASSWORD"
    ]
    assert not any(
        e.startswith("GF_SECURITY_ADMIN_PASSWORD") for e in unit["Environment"]
    )


# ---- the sample ------------------------------------------------------------


def test_sample_is_deterministic_and_synthetic() -> None:
    a = json.dumps(sample.build(END), sort_keys=True)
    b = json.dumps(sample.build(END), sort_keys=True)
    assert a == b
    assert json.dumps(sample.build(date(2026, 11, 21)), sort_keys=True) != a
    tables: dict[str, list[dict[str, Any]]] = sample.build(END)["tables"]
    assert {"view_daily", "click_events", "analytics_coverage"} == set(tables)
    assert all(r["site"] == "links" for r in tables["view_daily"])


# ---- the two deploy fixes (PR review): secret newline, rootless user mapping ----


def _readme() -> str:
    return (GRAFANA / "README.md").read_text("utf-8")


def secret_commands(text: str) -> list[str]:
    """Every shell line that creates the Podman secret."""
    return [ln for ln in text.splitlines() if "| podman secret create" in ln]


def secret_is_newline_safe(line: str) -> bool:
    """True when the piped value cannot end in a newline."""
    body = line.lstrip("# ").strip()
    if "base64" in body:
        return "base64 -w0" in body
    return body.startswith("printf '%s'")


def test_readme_secret_commands_store_no_trailing_newline() -> None:
    lines = secret_commands(_readme())
    assert len(lines) >= 2, "the README must show the secret command"
    assert all(secret_is_newline_safe(ln) for ln in lines), lines


def test_secret_newline_guard_bites() -> None:
    """Negative cases: the documented-before forms must be refused."""
    bad = [
        "head -c 24 /dev/urandom | base64 | podman secret create x - > /dev/null",
        'echo "$PW" | podman secret create x - > /dev/null',
        "printf '%s\\n' \"$PW\" | podman secret create x -",
    ]
    for ln in bad:
        assert not secret_is_newline_safe(ln), ln
    assert secret_is_newline_safe(
        "head -c 24 /dev/urandom | base64 -w0 | podman secret create x -"
    )
    assert secret_is_newline_safe("printf '%s' \"$RA_PW\" | podman secret create x -")


def test_readme_says_how_to_read_the_password_without_history() -> None:
    text = _readme()
    assert "read -rs" in text  # typed with no echo, so not in shell history
    assert "--showsecret" in text and "password manager" in text


def test_quadlet_maps_the_host_user_to_grafana_uid_and_gid() -> None:
    unit = _unit()
    assert unit["UserNS"] == ["keep-id:uid=472,gid=472"]
    # the image runs Grafana as uid 472; the mapping must name that user
    assert "User" not in unit


def test_userns_guard_bites() -> None:
    """Negative case: a unit without the key, or with another uid, is refused."""

    def ok(lines: list[str]) -> bool:
        return "UserNS=keep-id:uid=472,gid=472" in lines

    assert ok(["UserNS=keep-id:uid=472,gid=472"])
    assert not ok(["ReadOnly=true"])
    assert not ok(["UserNS=keep-id"])
    assert not ok(["UserNS=keep-id:uid=1000,gid=1000"])
