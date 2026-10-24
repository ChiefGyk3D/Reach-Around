# SPDX-License-Identifier: Apache-2.0
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


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))
