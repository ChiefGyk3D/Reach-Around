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
     PATH, HOME, TMPDIR, LANG and LC_ALL, plus only the FIXED canonical
     credential names of the source kinds the config lists (for example
     YOUTUBE_API_KEY for a youtube source, written or not). A name written in
     the config (api_key_env and the like) is never forwarded: it is only
     checked, and a name that is not a plain variable name or starts with
     ACTIONS_, GITHUB_ or RUNNER_ stops the run, so the runner's tokens (OIDC
     request variables included) can never be handed on. The engine itself
     refuses any non-canonical name. Keyless sources (Mastodon,
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
# The only credential variables the engine reads, by source kind. Keep equal to
# the constants in reach_around.config.numbers_sources (a test checks it).
CANONICAL_ENV = {
    "youtube": ("YOUTUBE_API_KEY",),
    "twitch": (
        "TWITCH_CLIENT_ID",
        "TWITCH_CLIENT_SECRET",
        "TWITCH_USER_ACCESS_TOKEN",
        "TWITCH_USER_REFRESH_TOKEN",
    ),
    "instagram": ("INSTAGRAM_ACCESS_TOKEN",),
    "threads": ("THREADS_ACCESS_TOKEN",),
    "tiktok": (
        "TIKTOK_ACCESS_TOKEN",
        "TIKTOK_REFRESH_TOKEN",
        "TIKTOK_CLIENT_KEY",
        "TIKTOK_CLIENT_SECRET",
    ),
}
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


MERGE_TAG = "tag:yaml.org,2002:merge"


def strict_data(text: str) -> object:
    """Load ``text`` as the engine's hardened loader would accept it.

    Raises ValueError on an anchor, alias, merge key, duplicate key or more than
    one document (checked on events and nodes before anything is expanded), so
    no decision about what to forward rests on a config the engine refuses."""
    documents = 0
    for event in yaml.parse(text, Loader=yaml.SafeLoader):
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
            raise ValueError("anchor")
        if isinstance(event, yaml.DocumentStartEvent):
            documents += 1
    if documents != 1:
        raise ValueError("documents")
    root = yaml.compose(text, Loader=yaml.SafeLoader)
    stack = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, yaml.MappingNode):
            seen: set[tuple[str, str]] = set()
            for key, value in node.value:
                if key.tag == MERGE_TAG:
                    raise ValueError("merge")
                ident = (key.tag, str(getattr(key, "value", "")))
                if not isinstance(key, yaml.ScalarNode) or ident in seen:
                    raise ValueError("key")
                seen.add(ident)
                stack.append(value)
        elif isinstance(node, yaml.SequenceNode):
            stack.extend(node.value)
    return yaml.safe_load(text)


def env_names(config: Path) -> tuple[list[str], list[str]] | None:
    """(names written in the config, canonical names to forward); None if
    unreadable. Only the second list is ever handed to the engine.

    The engine validates the whole config afterwards (duplicate keys, aliases
    and all); this only reads names to decide what to hand it. A config the
    engine would refuse yields no usable names here either, because the
    engine is run with those names alone.
    """
    try:
        if config.stat().st_size > MAX_CONFIG_BYTES:
            return None
        data = strict_data(config.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return None
    block = data.get("numbers") if isinstance(data, dict) else None
    sources = block.get("sources") if isinstance(block, dict) else None
    names: list[str] = []
    canonical: list[str] = []
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, dict):
            continue
        kind = source.get("kind")
        if isinstance(kind, str):
            canonical.extend(CANONICAL_ENV.get(kind, ()))
        for key in ENV_KEYS:
            if key in source:
                names.append(str(source[key]))
    return names, canonical


def unsafe_name(name: str) -> bool:
    return not ENV_NAME.match(name) or name.startswith(FORBIDDEN_PREFIXES)


def allowed_env(names: list[str]) -> dict[str, str]:
    keep = set(BASE_ENV) | set(names)
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

    found = env_names(config)
    if found is None:
        return fail(
            "The config could not be read as plain YAML (no anchors, aliases, "
            "merge keys, duplicate keys or extra documents)."
        )
    names, canonical = found
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
        env=allowed_env(canonical),
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
