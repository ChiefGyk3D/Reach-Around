# SPDX-License-Identifier: Apache-2.0
"""Refresh the follower counts for the test deployment and warn about old ones.

Used by the `counts` job of .github/workflows/deploy-test.yml. Run it as
`python3 -I .github/scripts/test-counts.py --config C --out DIR`.

What it does:
  1. Starts DIR/numbers.json from the numbers.json committed beside the config
     (the hand-kept entries: TikTok, Instagram, X, Threads, LinkedIn and any
     other platform with no source), or from an empty valid file.
  2. If the config has a top-level `numbers:` block, runs
     `reach-around counts refresh` on it. Keyless sources (Mastodon, Bluesky,
     GitHub) need nothing; keyed sources read the variables the config names
     from this process's environment, and a source whose variable is missing is
     skipped by the engine. Exit 3 from the engine (a source failed; the good
     numbers and the old values are kept) is a warning here, because the
     deployment of the rest of the page should not stop for one platform.
     Any other engine exit stops the run.
  3. Warns (never fails) for every entry whose as_of is more than 30 days old.

Nothing from the numbers file is echoed except a platform slug that matches
[a-z0-9-]{1,40} and a whole number of days.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

STALE_DAYS = 30
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
EMPTY = {"version": 1, "platforms": {}}


def fail(message: str) -> int:
    print(f"::error::{message}")
    return 1


def parse_when(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def stale_warnings(path: Path, now: datetime) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    platforms = data.get("platforms") if isinstance(data, dict) else None
    if not isinstance(platforms, dict):
        return []
    lines: list[str] = []
    odd = 0
    for key, entry in sorted(platforms.items()):
        when = parse_when(entry.get("as_of")) if isinstance(entry, dict) else None
        if when is None or (now - when).days <= STALE_DAYS:
            continue
        if not SLUG.match(str(key)):
            odd += 1
            continue
        lines.append(
            f"::warning::The {key} number is {(now - when).days} days old "
            f"(over {STALE_DAYS}). Update it in numbers.json."
        )
    if odd:
        lines.append(f"::warning::{odd} numbers.json entries are old and oddly named.")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--engine", default="reach-around")
    args = parser.parse_args(argv)

    config: Path = args.config
    seed = config.parent / "numbers.json"
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / "numbers.json"
    if config.is_symlink() or seed.is_symlink():
        return fail("The config and numbers.json must not be symbolic links.")
    if seed.is_file():
        shutil.copyfile(seed, out)
    else:
        out.write_text(json.dumps(EMPTY), encoding="utf-8")

    text = config.read_text(encoding="utf-8")
    if re.search(r"^numbers:", text, re.MULTILINE):
        proc = subprocess.run(  # noqa: S603 - fixed argv; the config path is ours
            [
                args.engine,
                "counts",
                "refresh",
                "--config",
                str(config),
                "--out",
                str(out),
            ],
            check=False,
            env=os.environ.copy(),
        )
        if proc.returncode == 3:
            print(
                "::warning::At least one count source failed; its old number "
                "was kept. See the engine output above."
            )
        elif proc.returncode != 0:
            return fail(f"counts refresh stopped with exit code {proc.returncode}.")
    else:
        print(
            "::notice::No numbers block in the config; using the hand-kept file only."
        )

    for line in stale_warnings(out, datetime.now(UTC)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
