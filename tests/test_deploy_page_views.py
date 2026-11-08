# SPDX-License-Identifier: Apache-2.0
"""deploy-test.yml and page views (issues #260, #261): the flag is exact, the
ids are validated before they are written, a placeholder never ships, the build
carries the analytics section only when asked, and the budget gate runs the
Worker's own function. The shell is run for real, with sample values."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-test.yml"
VERIFY = ROOT / ".github" / "scripts" / "verify-page-ids.py"

D1_ID = "11111111-2222-3333-4444-555555555555"
NS_ID = "4242"
VIEW_NS = "4343"
HOST = "links-test.example.invalid"
ZONE = "example.invalid"
PAGES = '["start-here","events"]'
BASE = {
    "PATH": "/usr/bin:/bin",
    "D1_DATABASE_ID": D1_ID,
    "RATELIMIT_NAMESPACE_ID": NS_ID,
    "SITE_HOST": HOST,
    "LINK_IDS": '["alpha"]',
    "CLOUDFLARE_ACCOUNT_ID": "acct",
    "PAGES_PROJECT": "proj",
    "ZONE_NAME": ZONE,
}
ON = {"PAGE_VIEWS": "true", "PAGE_IDS": PAGES, "VIEW_LIMITER_NAMESPACE_ID": VIEW_NS}


def _workflow() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return data


def _step(job: str, name: str) -> dict[str, Any]:
    found = [s for s in _workflow()["jobs"][job]["steps"] if s.get("name") == name]
    assert len(found) == 1, name
    step: dict[str, Any] = found[0]
    return step


def _check(extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    script = _step("deploy", "Check configuration")["run"]
    return subprocess.run(  # noqa: S603 - fixed argv and sample values
        ["/bin/bash", "-c", script],
        env={**BASE, **extra},
        capture_output=True,
        text=True,
        check=False,
    )


def _write(tmp_path: Path, extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    (tmp_path / "wrangler.toml").write_text(
        (ROOT / "worker" / "wrangler.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return subprocess.run(  # noqa: S603 - fixed argv and sample values
        ["/bin/bash", "-c", _step("deploy", "Write deploy config")["run"]],
        cwd=tmp_path,
        env={**BASE, **extra},
        capture_output=True,
        text=True,
        check=False,
    )


def _out(tmp_path: Path) -> str:
    return (tmp_path / "wrangler.deploy.toml").read_text(encoding="utf-8")


# ---- off by default ----------------------------------------------------------


@pytest.mark.parametrize("flag", [{}, {"PAGE_VIEWS": ""}, {"PAGE_VIEWS": "false"}])
def test_off_changes_nothing_even_with_ids_set(
    tmp_path: Path, flag: dict[str, str]
) -> None:
    extra = {**flag, "PAGE_IDS": PAGES, "VIEW_LIMITER_NAMESPACE_ID": VIEW_NS}
    assert _check(extra).returncode == 0
    assert _write(tmp_path, extra).returncode == 0
    data = tomllib.loads(_out(tmp_path))
    assert "PAGE_IDS" not in data["vars"]
    assert [r["name"] for r in data["ratelimits"]] == ["CLICK_LIMITER"]
    assert data["triggers"]["crons"] == ["5 * * * *"]
    assert VIEW_NS not in _out(tmp_path)


@pytest.mark.parametrize(
    "bad", ["True", "TRUE", "1", "yes", "on", "tru", " true", "true\n"]
)
def test_the_flag_is_exact_anything_else_fails_closed(tmp_path: Path, bad: str) -> None:
    extra = {**ON, "PAGE_VIEWS": bad}
    checked = _check(extra)
    assert checked.returncode != 0
    assert "PAGE_VIEWS" in checked.stdout + checked.stderr


# ---- on: the deploy config -----------------------------------------------------


def test_on_writes_ids_binding_and_keeps_the_hourly_cron(tmp_path: Path) -> None:
    extra = {
        **ON,
        "VIEWS_PER_HOUR": "100",
        "BOT_VIEWS_PER_HOUR": "20",
        "ACCOUNT_VIEWS_PER_HOUR": "300",
        "ACCOUNT_BOT_VIEWS_PER_HOUR": "60",
    }
    assert _check(extra).returncode == 0
    done = _write(tmp_path, extra)
    assert done.returncode == 0, done.stderr
    data = tomllib.loads(_out(tmp_path))
    assert data["vars"]["PAGE_IDS"] == PAGES
    assert data["vars"]["VIEWS_PER_HOUR"] == "100"
    assert data["vars"]["BOT_VIEWS_PER_HOUR"] == "20"
    assert data["vars"]["ACCOUNT_VIEWS_PER_HOUR"] == "300"
    assert data["vars"]["ACCOUNT_BOT_VIEWS_PER_HOUR"] == "60"
    limiter = {r["name"]: r for r in data["ratelimits"]}
    assert limiter["VIEW_LIMITER"]["namespace_id"] == VIEW_NS
    assert limiter["VIEW_LIMITER"]["simple"] == {"limit": 5, "period": 60}
    assert limiter["CLICK_LIMITER"]["namespace_id"] == NS_ID
    # the hourly rollup and purge cron is the one already there: no new trigger
    assert data["triggers"]["crons"] == ["5 * * * *"]
    # the beacon route reaches the Worker, and the other substitutions hold
    assert data["routes"] == [{"pattern": f"{HOST}/e/*", "zone_name": ZONE}]
    assert data["d1_databases"][0]["database_id"] == D1_ID
    # the secret is never written to the file
    assert "VIEW_HASH_SECRET" not in "\n".join(
        ln for ln in _out(tmp_path).splitlines() if not ln.lstrip().startswith("#")
    )


def test_on_without_the_optional_budgets_sets_none(tmp_path: Path) -> None:
    assert _write(tmp_path, ON).returncode == 0
    data = tomllib.loads(_out(tmp_path))
    assert "VIEWS_PER_HOUR" not in data["vars"]
    assert "BOT_VIEWS_PER_HOUR" not in data["vars"]
    assert "ACCOUNT_VIEWS_PER_HOUR" not in data["vars"]


BAD_ON: list[tuple[str, dict[str, str]]] = [
    ("ids unset", {"PAGE_IDS": ""}),
    ("ids not json", {"PAGE_IDS": "start-here"}),
    ("ids empty array", {"PAGE_IDS": "[]"}),
    ("ids quote injection", {"PAGE_IDS": '["a\'b"]'}),
    ("ids upper case", {"PAGE_IDS": '["Home"]'}),
    ("ids newline smuggle", {"PAGE_IDS": '["home"]\nname = "x"'}),
    ("ids prefix junk", {"PAGE_IDS": 'x["home"]'}),
    ("ids suffix junk", {"PAGE_IDS": '["home"]x'}),
    ("ids too long", {"PAGE_IDS": '["' + "a" * 41 + '"]'}),
    ("ids leading dash", {"PAGE_IDS": '["-a"]'}),
    ("limiter unset", {"VIEW_LIMITER_NAMESPACE_ID": ""}),
    ("limiter not a number", {"VIEW_LIMITER_NAMESPACE_ID": "12ab"}),
    ("limiter newline", {"VIEW_LIMITER_NAMESPACE_ID": "12\n34"}),
    ("limiter placeholder", {"VIEW_LIMITER_NAMESPACE_ID": "1002"}),
    ("limiter equals clicks", {"VIEW_LIMITER_NAMESPACE_ID": NS_ID}),
    ("no zone", {"ZONE_NAME": ""}),
    ("views not a number", {"VIEWS_PER_HOUR": "1e3"}),
    ("views over ceiling", {"VIEWS_PER_HOUR": "100001"}),
    ("bot views negative", {"BOT_VIEWS_PER_HOUR": "-1"}),
    ("account ceiling bad", {"ACCOUNT_VIEWS_PER_HOUR": "abc"}),
    ("account bot ceiling over", {"ACCOUNT_BOT_VIEWS_PER_HOUR": "100001"}),
]


@pytest.mark.parametrize(("label", "change"), BAD_ON, ids=[b[0] for b in BAD_ON])
def test_on_with_a_bad_or_missing_value_fails_closed(
    tmp_path: Path, label: str, change: dict[str, str]
) -> None:
    extra = {**ON, **change}
    assert _check(extra).returncode != 0, label
    if (
        "ZONE_NAME" not in change
    ):  # the writer's own ZONE handling is older and separate
        assert _write(tmp_path, extra).returncode != 0, label


def test_negative_cases_do_bite() -> None:
    """The guard is not vacuous: the same values, good, pass the same script."""
    assert _check(ON).returncode == 0
    assert _check({**ON, "PAGE_IDS": ""}).returncode != 0


# ---- the build copy of the config ----------------------------------------------


def _build_script() -> str:
    return str(_step("build", "Build the site")["run"])


def test_the_build_adds_analytics_only_on_an_exact_true() -> None:
    run = _build_script()
    assert "examples/instance/site.page-views.yaml" in run
    assert "page_views: true" in run
    # the committed example is never edited
    assert "> examples/instance/site.yaml" not in run
    assert "sed -i" not in run
    env = _step("build", "Build the site")["env"]
    assert env["PAGE_VIEWS"] == "${{ vars.PAGE_VIEWS }}"


def _run_build_selector(
    tmp_path: Path, flag: str | None
) -> subprocess.CompletedProcess[str]:
    """Run only the config-selection part of the build step with stubbed tools."""
    run = _build_script()
    start = run.index("CONFIG=examples/instance/site.yaml")
    end = run.index('if [ "$USE_COUNTS"')
    inst = tmp_path / "examples" / "instance"
    inst.mkdir(parents=True, exist_ok=True)
    (inst / "site.yaml").write_text("version: 1\n", encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin"}
    if flag is not None:
        env["PAGE_VIEWS"] = flag
    script = "set -eu\n" + run[start:end] + 'printf "%s" "$CONFIG"\n'
    return subprocess.run(  # noqa: S603 - fixed argv and sample values
        ["/bin/bash", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("flag", [None, "", "false"])
def test_build_off_uses_the_example_unchanged(tmp_path: Path, flag: str | None) -> None:
    done = _run_build_selector(tmp_path, flag)
    assert done.returncode == 0
    assert done.stdout == "examples/instance/site.yaml"
    assert not (tmp_path / "examples/instance/site.page-views.yaml").exists()


def test_build_on_appends_the_analytics_section_to_a_copy(tmp_path: Path) -> None:
    done = _run_build_selector(tmp_path, "true")
    assert done.returncode == 0
    assert done.stdout == "examples/instance/site.page-views.yaml"
    copy = yaml.safe_load(
        (tmp_path / "examples/instance/site.page-views.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert copy == {"version": 1, "analytics": {"page_views": True}}
    assert (tmp_path / "examples/instance/site.yaml").read_text() == "version: 1\n"


@pytest.mark.parametrize("flag", ["True", "1", "yes", "tru"])
def test_build_refuses_a_non_exact_flag(tmp_path: Path, flag: str) -> None:
    done = _run_build_selector(tmp_path, flag)
    assert done.returncode != 0
    assert "PAGE_VIEWS" in done.stdout


def test_build_refuses_when_the_example_already_has_analytics(tmp_path: Path) -> None:
    run = _build_script()
    inst = tmp_path / "examples" / "instance"
    inst.mkdir(parents=True)
    (inst / "site.yaml").write_text(
        "analytics:\n  page_views: false\n", encoding="utf-8"
    )
    script = (
        "set -eu\n"
        + run[run.index("CONFIG=examples") : run.index('if [ "$USE_COUNTS"')]
    )
    done = subprocess.run(  # noqa: S603 - fixed argv and sample values
        ["/bin/bash", "-c", script],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "PAGE_VIEWS": "true"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode != 0
    assert "already has an analytics section" in done.stdout


def test_the_committed_example_has_no_analytics_section() -> None:
    text = (ROOT / "examples" / "instance" / "site.yaml").read_text(encoding="utf-8")
    assert not any(line.startswith("analytics:") for line in text.splitlines())


# ---- the budget gate and the id check run in the deploy job -----------------------


def test_budget_and_page_id_steps_are_conditional_tokenless_and_ordered() -> None:
    steps = _workflow()["jobs"]["deploy"]["steps"]
    names = [s.get("name") for s in steps]
    for name in ("Verify the page ids", "Check the page-view write budget"):
        step = _step("deploy", name)
        assert step["if"] == "${{ vars.PAGE_VIEWS == 'true' }}"
        assert "secrets." not in str(step) and "CLOUDFLARE" not in str(step)
        assert names.index(name) < names.index("Write deploy config")
    # the budget step sees the site list and the ceilings the Worker will load
    budget_env = _step("deploy", "Check the page-view write budget")["env"]
    for var in (
        "SITE_HOST",
        "PAGE_IDS",
        "VIEWS_PER_HOUR",
        "BOT_VIEWS_PER_HOUR",
        "ACCOUNT_VIEWS_PER_HOUR",
        "ACCOUNT_BOT_VIEWS_PER_HOUR",
    ):
        assert budget_env[var] == "${{ vars." + var + " }}"
    # the link ids are the ones derived from the manifest, as in the deploy config
    assert budget_env["LINK_IDS"] == "${{ steps.manifest.outputs.link_ids }}"
    names = [s.get("name") for s in _workflow()["jobs"]["deploy"]["steps"]]
    assert names.index("Verify the build manifest") < names.index(
        "Check the page-view write budget"
    )
    # the budget step builds the Worker's own function, never a copy of the limits
    run = _step("deploy", "Check the page-view write budget")["run"]
    assert "scripts/check-budget.ts" in run
    assert "esbuild" in run and "--no-install" in run
    # after the dependencies it needs are installed
    assert names.index("Install Worker dependencies") < names.index(
        "Check the page-view write budget"
    )


def test_budget_script_reuses_the_worker_function_and_holds_no_limits() -> None:
    script = (ROOT / "worker" / "scripts" / "check-budget.ts").read_text("utf-8")
    pure = (ROOT / "worker" / "src" / "kit" / "budgetcheck.ts").read_text("utf-8")
    assert "runBudgetCheck" in script
    assert "assessDailyBudget" in pure
    for literal in ("22_700", "22700", "100_000", "100000 *", "78120"):
        assert literal not in script
    # no limit number is redefined outside budgets.ts and pvlimits.ts
    for text in (script, pure):
        assert "PAGE_VIEW_DAILY_WRITES =" not in text
        assert "COMBINED_MARGIN =" not in text


def test_page_view_variables_are_plain_variables_not_secrets() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    for var in (
        "PAGE_VIEWS",
        "PAGE_IDS",
        "VIEW_LIMITER_NAMESPACE_ID",
        "VIEWS_PER_HOUR",
    ):
        assert "vars." + var in text
        assert "secrets." + var not in text
    # VIEW_HASH_SECRET is a Worker secret the owner sets once: no workflow line
    # (comments aside) reads or writes it.
    live = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("VIEW_HASH_SECRET" in ln for ln in live)
    for name in (
        "Apply D1 migrations",
        "Deploy the Worker",
        "Publish the site to Pages",
    ):
        body = str(_step("deploy", name))
        for var in ("PAGE_IDS", "VIEW_LIMITER_NAMESPACE_ID", "PAGE_VIEWS"):
            assert var not in body, (name, var)
    assert "GITHUB_ENV" not in str(_step("deploy", "Write deploy config"))


# ---- verify-page-ids.py -----------------------------------------------------------


def _verify(
    tmp_path: Path, pages: dict[str, str], ids: str
) -> subprocess.CompletedProcess[str]:
    site = tmp_path / "site-out"
    site.mkdir()
    for rel, body in pages.items():
        target = site / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return subprocess.run(  # noqa: S603 - fixed argv and sample values
        ["/usr/bin/env", "python3", "-I", str(VERIFY), str(site)],
        env={"PATH": "/usr/bin:/bin", "PAGE_IDS": ids},
        capture_output=True,
        text=True,
        check=False,
    )


def _page(page_id: str) -> str:
    return f'<body data-page-id="{page_id}" data-view-route="/e/v/">'


def test_page_ids_match(tmp_path: Path) -> None:
    done = _verify(
        tmp_path,
        {"index.html": _page("start-here"), "events/index.html": _page("events")},
        PAGES,
    )
    assert done.returncode == 0
    assert done.stdout == ""


def test_a_built_tab_missing_from_page_ids_is_an_error(tmp_path: Path) -> None:
    done = _verify(
        tmp_path,
        {"index.html": _page("start-here"), "events/index.html": _page("events")},
        '["start-here"]',
    )
    assert done.returncode == 1
    assert "events" in done.stdout and "::error::" in done.stdout


def test_an_unbuilt_listed_id_is_only_a_warning(tmp_path: Path) -> None:
    done = _verify(tmp_path, {"index.html": _page("start-here")}, PAGES)
    assert done.returncode == 0
    assert done.stdout.startswith("::warning::")


def test_a_build_without_page_views_is_an_error(tmp_path: Path) -> None:
    done = _verify(tmp_path, {"index.html": "<body>"}, PAGES)
    assert done.returncode == 1


@pytest.mark.parametrize(
    "ids", ["", "not json", '"home"', '["Home"]', '["a b"]', "[1]", '["a\\nb"]']
)
def test_bad_page_ids_value_is_refused_without_echo(tmp_path: Path, ids: str) -> None:
    done = _verify(tmp_path, {"index.html": _page("home")}, ids)
    assert done.returncode == 1
    assert ids.strip() == "" or ids not in done.stdout


def test_a_hostile_built_id_cannot_forge_a_workflow_command(tmp_path: Path) -> None:
    done = _verify(tmp_path, {"index.html": _page("x::error::boom")}, PAGES)
    assert done.returncode == 1
    assert "boom" not in done.stdout
