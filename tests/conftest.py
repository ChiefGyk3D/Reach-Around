# SPDX-License-Identifier: Apache-2.0
from collections.abc import Iterator

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="rewrite the golden files under tests/ instead of comparing",
    )
    parser.addoption(
        "--run-visual",
        action="store_true",
        default=False,
        help="run the browser layout tests marked 'visual' (needs Playwright)",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "visual: browser layout tests, run only with --run-visual"
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("--run-visual"):
        return
    skip = pytest.mark.skip(reason="visual test; pass --run-visual to run")
    for item in items:
        if "visual" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def no_real_wrangler() -> Iterator[None]:
    """#856: `leads delete --yes` runs wrangler against a live database to clear
    the alert hashes. No test may reach it: the default answers a clean clear, a
    read-back of 0 and a schema with the table; a test that cares replaces it.
    It owns its own MonkeyPatch, so a test calling `monkeypatch.undo()` cannot
    undo it and reach the real call."""

    def fake(database: str, sql: str) -> tuple[int, str, str]:
        for column in ("alert_dedup_remaining", "alert_table_present"):
            if column in sql:
                return 0, f'[{{"results":[{{"{column}":0}}],"success":true}}]', ""
        return 0, '[{"results":[],"success":true,"meta":{}}]', ""

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("reach_around.commands.leads._run_wrangler", fake)
        yield


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))
