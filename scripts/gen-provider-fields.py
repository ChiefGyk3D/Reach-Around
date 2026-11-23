#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write deploy/edge/provider-fields.json from the provider's own schema (#613).

    cd deploy/edge && tofu init -backend=false -input=false -lockfile=readonly
    tofu providers schema -json \\
        | python3 -I ../../scripts/gen-provider-fields.py 5.29.0 > provider-fields.json

Reads the JSON on stdin; nothing else, no network, no token. The output lists
the optional attributes of cloudflare_bot_management (the fields a configuration
can set) and the computed-only ones. scripts/check-edge.py --offline compares the
manifest, its own field list and desired-state.json against each other, so a
field cannot be forgotten in two places at once. Regenerate and review the
diff on every provider bump.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

PROVIDER = "registry.opentofu.org/cloudflare/cloudflare"
RESOURCE = "cloudflare_bot_management"
COMMAND = (
    "tofu providers schema -json | python3 -I scripts/gen-provider-fields.py 5.29.0"
    " > deploy/edge/provider-fields.json"
)
API_NAME = {"aisearch": "ai_search"}  # provider attribute -> REST field name


def build_manifest(schema: Any, version: str, command: str) -> dict[str, Any]:
    try:
        attributes = schema["provider_schemas"][PROVIDER]["resource_schemas"][RESOURCE][
            "block"
        ]["attributes"]
    except (KeyError, TypeError):
        raise ValueError(f"schema has no {RESOURCE} resource") from None
    sendable: list[str] = []
    computed_only: list[str] = []
    for name, spec in attributes.items():
        if spec.get("required"):
            continue  # zone_id: an input, not a setting
        if spec.get("optional"):
            sendable.append(API_NAME.get(name, name))
        else:
            computed_only.append(name)
    return {
        "schema": 1,
        "provider": PROVIDER,
        "provider_version": version,
        "resource": RESOURCE,
        "command": command,
        "sendable_fields": sorted(sendable),
        "computed_only": sorted(computed_only),
    }


def main() -> int:
    version = sys.argv[1] if len(sys.argv) > 1 else ""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        print("usage: gen-provider-fields.py VERSION < schema.json", file=sys.stderr)
        return 2
    try:
        manifest = build_manifest(json.load(sys.stdin), version, COMMAND)
    except ValueError:
        print("the input is not the expected provider schema", file=sys.stderr)
        return 2
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
