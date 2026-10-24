# SPDX-License-Identifier: Apache-2.0
import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="rewrite the golden files under tests/ instead of comparing",
    )


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))
