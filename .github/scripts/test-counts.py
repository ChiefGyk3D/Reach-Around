# SPDX-License-Identifier: Apache-2.0
"""Refresh the follower counts for the test deployment and warn about old ones.

Used by the `counts` job of .github/workflows/deploy-test.yml. Run it as
`python3 -I .github/scripts/test-counts.py --config C --out DIR`.

What it does:
  1. Starts DIR/numbers.json from the numbers.json committed beside the config
     (the hand-kept entries: TikTok, Instagram, X, Threads, LinkedIn and any
     other platform with no source), or from an empty valid file.
  2. Always runs `reach-around counts refresh`; the engine's own config loader
     decides whether there is a `numbers` block (its exit 2 with "no
     numbers.sources" is the no-block case, any other exit 2 is an error).
     The engine gets a small allow-listed environment, never the whole one:
     PATH, HOME, TMPDIR, LANG and LC_ALL, plus the FIXED canonical credential
     names (CANONICAL_ENV) that are set in the runner environment. Nothing is
     derived from the config: no name written in it (api_key_env and the like)
     is ever forwarded, and no parse of it decides what is forwarded. This is
     safe because the engine reads only those fixed names, and only for a
     config and source kinds it validates; a canonical secret present in the
     environment of a config the engine rejects is never read or sent. That
     removes the whole class of differences between this script's view of the
     config and the engine's. A name written in the config that is not a plain
     variable name or starts with ACTIONS_, GITHUB_ or RUNNER_ still stops the
     run (best-effort read, never used to forward). Keyless sources (Mastodon,
     Bluesky, GitHub) need nothing; a source whose variable is missing is
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
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

STALE_DAYS = 30
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
EMPTY = {"version": 1, "platforms": {}}
BASE_ENV = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
FORBIDDEN_PREFIXES = ("ACTIONS_", "GITHUB_", "RUNNER_")
ENV_KEYS = (
    "api_key_env",
    "client_id_env",
    "client_secret_env",
    "access_token_env",
    "client_key_env",
)
# The only credential variables the engine reads. Keep equal to the constants in
# reach_around.config.numbers_sources (a test checks it).
CANONICAL_ENV = (
    "YOUTUBE_API_KEY",
    "TWITCH_CLIENT_ID",
    "TWITCH_CLIENT_SECRET",
    "TWITCH_USER_ACCESS_TOKEN",
    "TWITCH_USER_REFRESH_TOKEN",
    "INSTAGRAM_ACCESS_TOKEN",
    "THREADS_ACCESS_TOKEN",
    "TIKTOK_ACCESS_TOKEN",
    "TIKTOK_REFRESH_TOKEN",
    "TIKTOK_CLIENT_KEY",
    "TIKTOK_CLIENT_SECRET",
)
NO_BLOCK = "no numbers.sources"
MAX_CONFIG_BYTES = 1_048_576


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
        if when is None or now - when <= timedelta(days=STALE_DAYS):
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


def written_names(config: Path) -> list[str] | None:
    """Env var names written in the config, for the refusal check ONLY.

    None if the file cannot be read. A config that does not parse here yields no
    names; the engine (the one authority) rejects it. Never used to forward."""
    try:
        if config.stat().st_size > MAX_CONFIG_BYTES:
            return None
        text = config.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        data = yaml.safe_load(text)
    except (yaml.YAMLError, ValueError, RecursionError):
        return []
    block = data.get("numbers") if isinstance(data, dict) else None
    sources = block.get("sources") if isinstance(block, dict) else None
    names: list[str] = []
    for source in sources if isinstance(sources, list) else []:
        if isinstance(source, dict):
            names.extend(str(source[key]) for key in ENV_KEYS if key in source)
    return names


def unsafe_name(name: str) -> bool:
    return not ENV_NAME.match(name) or name.startswith(FORBIDDEN_PREFIXES)


def allowed_env() -> dict[str, str]:
    keep = set(BASE_ENV) | set(CANONICAL_ENV)
    return {k: v for k, v in os.environ.items() if k in keep}


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

    names = written_names(config)
    if names is None:
        return fail("The config could not be read.")
    if any(unsafe_name(name) for name in names):
        return fail(
            "A source names an environment variable that is not allowed "
            "(use A-Z, 0-9 and _, and not an ACTIONS_, GITHUB_ or RUNNER_ name)."
        )
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
        env=allowed_env(),
        stderr=subprocess.PIPE,
        text=True,
    )
    sys.stderr.write(proc.stderr)
    if proc.returncode == 2 and NO_BLOCK in proc.stderr:
        print(
            "::notice::No numbers block in the config; using the hand-kept file only."
        )
    elif proc.returncode == 3:
        print(
            "::warning::At least one count source failed; its old number "
            "was kept. See the engine output above."
        )
    elif proc.returncode != 0:
        return fail(f"counts refresh stopped with exit code {proc.returncode}.")

    for line in stale_warnings(out, datetime.now(UTC)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
