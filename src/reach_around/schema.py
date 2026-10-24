# SPDX-License-Identifier: Apache-2.0
"""JSON Schema of the site config, generated from the pydantic models.

The output is stable: keys sorted, ``required`` lists sorted, fixed indent, a
final newline, nothing that depends on the time, the machine or the run. The
committed copy lives at ``docs/site.schema.json``.
"""

from __future__ import annotations

import json
from typing import Any

from reach_around.config import SiteConfig

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def _normalize(node: Any) -> Any:
    if isinstance(node, dict):
        out = {key: _normalize(value) for key, value in node.items()}
        required = out.get("required")
        if isinstance(required, list) and all(isinstance(x, str) for x in required):
            out["required"] = sorted(required)
        return out
    if isinstance(node, list):
        return [_normalize(item) for item in node]
    return node


def build_schema() -> dict[str, Any]:
    schema = _normalize(SiteConfig.model_json_schema())
    schema["$schema"] = SCHEMA_DIALECT
    schema["title"] = "Reach Around site config"
    return dict(schema)


def render_schema() -> str:
    """The schema as text: sorted keys, two-space indent, trailing newline."""
    return (
        json.dumps(build_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )


def schema_matches(text: str) -> bool:
    """True when ``text`` is exactly what ``render_schema`` produces now."""
    return text == render_schema()
