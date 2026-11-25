# SPDX-License-Identifier: Apache-2.0
"""The threat model route inventory matches the Worker routes (issue #771).

The inventory table in docs/security/threat-model.md (under T-07) is the one
authoritative list of Worker paths. This test reads the route constants from
worker/src and fails when a path exists in one place and not the other.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SRC = ROOT / "worker" / "src"
MODEL = ROOT / "docs" / "security" / "threat-model.md"

BEGIN = "<!-- route-inventory:begin -->"
END = "<!-- route-inventory:end -->"

# `export const NAME_ROUTE = "/path"` style constants.
_CONST = re.compile(r'const\s+[A-Z_]*ROUTE\s*=\s*"(/[^"]*)"')
# `const ROUTE = /^\/e\/c\/([^/]+)$/` style patterns, one per analytics route.
_PREFIX = {
    "click.ts": ("/e/c/", "/e/c/:id"),
    "view.ts": ("/e/v/", "/e/v/:pageId"),
}


def routes_in_code(src: Path = SRC) -> set[str]:
    found: set[str] = set()
    for path in src.rglob("*.ts"):
        text = path.read_text(encoding="utf-8")
        found.update(_CONST.findall(text))
        if path.parent == src and path.name in _PREFIX:
            prefix, shown = _PREFIX[path.name]
            # Source spells each slash as backslash-slash inside a regex literal.
            pattern = prefix.replace("/", r"\\/")
            assert re.search(r"const\s+ROUTE\s*=\s*/\^" + pattern, text), (
                f"{path.name} no longer defines the {prefix} route pattern; "
                "update this test and the inventory together"
            )
            found.add(shown)
    return found


def routes_in_model() -> set[str]:
    text = MODEL.read_text(encoding="utf-8")
    assert text.count(BEGIN) == 1 and text.count(END) == 1
    block = text.split(BEGIN, 1)[1].split(END, 1)[0]
    found: set[str] = set()
    for line in block.splitlines():
        match = re.match(r"\| `([^`]+)`", line)
        if match:
            found.add(match.group(1))
    return found


def test_route_inventory_matches_the_worker_routes() -> None:
    in_code = routes_in_code()
    in_model = routes_in_model()

    assert in_code, "the scan found no routes at all; the patterns are stale"
    assert in_code - in_model == set(), "routes in code but not in the inventory"
    assert in_model - in_code == set(), "routes in the inventory but not in code"


def test_inventory_scan_catches_a_new_route(tmp_path: Path) -> None:
    # Negative case: an extra route constant must differ from the table.
    (tmp_path / "extra.ts").write_text(
        'export const NEW_ROUTE = "/z/new";\n', encoding="utf-8"
    )

    assert "/z/new" in routes_in_code(tmp_path)
    assert routes_in_code(tmp_path) != routes_in_model()


def test_prefix_pattern_drift_is_reported(tmp_path: Path) -> None:
    (tmp_path / "click.ts").write_text(
        "const ROUTE = /^\\/other$/;\n", encoding="utf-8"
    )

    with pytest.raises(AssertionError):
        routes_in_code(tmp_path)


def test_kill_switch_text_does_not_claim_a_variable_only_change() -> None:
    text = MODEL.read_text(encoding="utf-8")

    assert "without a code deploy" not in text
    assert "--keep-vars" in text


def test_threat_model_states_each_page_view_fact_once() -> None:
    text = MODEL.read_text(encoding="utf-8")

    assert text.count("| Page-view write budget:") == 1
    assert text.count("| Page-view retention (") == 1
    assert text.count("| Page-view geography:") == 1
