#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check the zone's edge settings against deploy/edge/desired-state.json (#613).

Usage:
    python3 -I scripts/check-edge.py --offline
    python3 -I scripts/check-edge.py --live --zone-id ID
        [--robots-url URL] [--save PATH]

--offline reads only files in this repository. It fails when the desired state
breaks a rule the owner decided (AI training blocked, search and link previews
not blocked, Bot Fight Mode off until it is measured, one flat rate rule on /e/
inside the Free plan limits) or when deploy/edge/main.tf, its provider pin and
its lock file no longer match the desired state. CI runs this.

--live reads the zone with GET requests only and fails on any difference, so a
setting changed in the dashboard cannot stay unnoticed. The token comes from the
environment variable CLOUDFLARE_EDGE_READ_TOKEN (permissions Bot Management Read
and Zone WAF Read on one zone); it is sent only in the Authorization header to
api.cloudflare.com, redirects are refused, and it is never printed. --robots-url
also fetches that public robots.txt (no token) and requires Cloudflare's managed
block in it. --save writes the raw settings read, for a rollback record.

Exit 0 agrees, 1 differs, 2 usage or could not read (never read as green).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
EDGE = Path("deploy/edge")
API = "https://api.cloudflare.com/client/v4"
TOKEN_ENV = "CLOUDFLARE_EDGE_READ_TOKEN"  # noqa: S105 (the variable name)
MAX_BYTES = 1_048_576
TIMEOUT = 20
MANAGED_MARKER = "begin cloudflare managed content"

# Settings the owner decided (issue #613). Anything not listed here is not ours.
BOT_KEYS: dict[str, set[Any]] = {
    "ai_training": {"disallow"},
    "ai_search": {"disabled"},  # search and link-preview bots stay allowed
    "ai_user": {"disabled"},
    "ai_bots_protection": {"disabled"},
    "bot_preference_sync_enabled": {True},
    "is_robots_txt_managed": {True},
    "crawler_protection": {"disabled"},  # no link maze
    "content_bots_protection": {"disabled"},
    "fight_mode": {False},  # Bot Fight Mode deferred until beacons are measured
}
RL_KEYS = (
    "ref",
    "description",
    "expression",
    "action",
    "characteristics",
    "period",
    "requests_per_period",
    "mitigation_timeout",
)
PIN = re.compile(r'version\s*=\s*"= (\d+\.\d+\.\d+)"')


class Unreadable(Exception):
    """The live zone could not be read; the caller must not treat it as green."""


def offline_problems(root: Path) -> list[str]:
    edge = root / EDGE
    problems: list[str] = []
    try:
        desired = json.loads((edge / "desired-state.json").read_text("utf-8"))
        main_tf = (edge / "main.tf").read_text("utf-8")
        versions = (edge / "versions.tf").read_text("utf-8")
        lock = (edge / ".terraform.lock.hcl").read_text("utf-8")
    except (OSError, ValueError) as exc:
        return [f"cannot read deploy/edge files ({type(exc).__name__})"]
    if not isinstance(desired, dict) or desired.get("schema") != 1:
        return ["desired-state.json: schema must be 1"]
    bot = desired.get("bot_management")
    rl = desired.get("rate_limit")
    if not isinstance(bot, dict) or not isinstance(rl, dict):
        return ["desired-state.json: bot_management and rate_limit are required"]

    if set(bot) != set(BOT_KEYS):
        problems.append(
            f"bot_management keys differ: {sorted(set(bot) ^ set(BOT_KEYS))}"
        )
    for key, allowed in BOT_KEYS.items():
        if key in bot and bot[key] not in allowed:
            problems.append(
                f"bot_management.{key} must be one of {sorted(str(a) for a in allowed)}"
            )
    if set(rl) != set(RL_KEYS):
        problems.append(f"rate_limit keys differ: {sorted(set(rl) ^ set(RL_KEYS))}")
    else:
        problems += rate_limit_problems(rl)

    for key in bot:
        if f"local.bot.{key}" not in main_tf:
            problems.append(f"main.tf does not read bot_management.{key}")
    for key in rl:
        if f"local.rl.{key}" not in main_tf:
            problems.append(f"main.tf does not read rate_limit.{key}")
    if "desired-state.json" not in main_tf:
        problems.append("main.tf does not read desired-state.json")

    pin = PIN.search(versions)
    locked = re.search(r'version\s*=\s*"(\d+\.\d+\.\d+)"', lock)
    if pin is None:
        problems.append("versions.tf: provider is not pinned to an exact version")
    elif locked is None or locked.group(1) != pin.group(1):
        problems.append("lock file version differs from the provider pin")
    return problems


def rate_limit_problems(rl: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    expression = rl["expression"]
    if (
        not isinstance(expression, str)
        or expression != 'starts_with(http.request.uri.path, "/e/")'
    ):
        problems.append("rate_limit.expression must be exactly the /e/ path match")
    if rl["action"] != "block":
        problems.append(
            "rate_limit.action must be block (a beacon cannot solve a challenge)"
        )
    if rl["characteristics"] != ["cf.colo.id", "ip.src"]:
        problems.append(
            "rate_limit.characteristics must be cf.colo.id and ip.src (Free plan)"
        )
    if rl["period"] != 10 or rl["mitigation_timeout"] != 10:
        problems.append(
            "rate_limit.period and mitigation_timeout must be 10 (Free plan)"
        )
    count = rl["requests_per_period"]
    if not isinstance(count, int) or isinstance(count, bool) or not 10 <= count <= 200:
        problems.append(
            "rate_limit.requests_per_period must be an integer from 10 to 200"
        )
    for key in ("ref", "description"):
        if not isinstance(rl[key], str) or not rl[key]:
            problems.append(f"rate_limit.{key} must be a non-empty string")
    return problems


def get(url: str, token: str | None) -> bytes:
    """One GET, no redirects, bounded size. The token only goes to the API host."""
    if not url.startswith("https://"):
        raise Unreadable("refusing a non-https URL")
    if token is not None and not url.startswith(API + "/"):
        raise Unreadable("refusing to send the token anywhere but the API")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: Any, **kwargs: Any) -> None:
            return None

    request = urllib.request.Request(url, method="GET")  # noqa: S310 (https checked)
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=TIMEOUT) as response:  # noqa: S310
            body: bytes = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        extra = ""
        if exc.code == 429:
            extra = f", Retry-After {exc.headers.get('Retry-After', 'unknown')}"
        raise Unreadable(f"HTTP {exc.code}{extra}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise Unreadable(type(exc).__name__) from None
    if len(body) > MAX_BYTES:
        raise Unreadable("response too large")
    return body


def api_result(path: str, token: str) -> Any:
    try:
        payload = json.loads(get(API + path, token))
    except ValueError:
        raise Unreadable("response is not JSON") from None
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise Unreadable("API did not report success")
    return payload.get("result")


def bot_differences(desired: dict[str, Any], live: Any) -> list[str]:
    if not isinstance(live, dict):
        return ["bot_management: live result is not an object"]
    return [
        f"bot_management.{k}: want {v!r}, live {live.get(k, '<absent>')!r}"
        for k, v in desired.items()
        if live.get(k) != v
    ]


def rate_differences(desired: dict[str, Any], live: Any) -> list[str]:
    rules = live.get("rules") if isinstance(live, dict) else None
    if not isinstance(rules, list):
        return ["rate_limit: no http_ratelimit rules found"]
    if len(rules) != 1:
        return [f"rate_limit: want exactly 1 rule, live has {len(rules)}"]
    rule = rules[0]
    if not isinstance(rule, dict):
        return ["rate_limit: the live rule is not an object"]
    found: list[str] = []
    for key in ("expression", "action", "description", "ref"):
        if rule.get(key) != desired[key]:
            found.append(
                f"rate_limit.{key}: want {desired[key]!r}, live {rule.get(key)!r}"
            )
    limit = rule.get("ratelimit") or {}
    if not isinstance(limit, dict):
        return found + ["rate_limit.ratelimit: live value is not an object"]
    for key in ("period", "requests_per_period", "mitigation_timeout"):
        if limit.get(key) != desired[key]:
            found.append(
                f"rate_limit.{key}: want {desired[key]!r}, live {limit.get(key)!r}"
            )
    if sorted(limit.get("characteristics") or []) != sorted(desired["characteristics"]):
        found.append("rate_limit.characteristics differ")
    if rule.get("enabled", True) is not True:
        found.append("rate_limit: the rule is disabled")
    return found


def write_new_file(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def live(args: argparse.Namespace) -> int:
    token = os.environ.get(TOKEN_ENV, "")
    zone = args.zone_id or ""
    if not token or not re.fullmatch(r"[0-9a-f]{32}", zone):
        print(f"usage: need {TOKEN_ENV} and --zone-id (32 hex)", file=sys.stderr)
        return 2
    desired = json.loads((ROOT / EDGE / "desired-state.json").read_text("utf-8"))
    try:
        bot = api_result(f"/zones/{zone}/bot_management", token)
        entry = api_result(
            f"/zones/{zone}/rulesets/phases/http_ratelimit/entrypoint", token
        )
        robots = (
            get(args.robots_url, None).decode("utf-8", "replace")
            if args.robots_url
            else None
        )
    except Unreadable as exc:
        print(f"UNREADABLE {exc}", file=sys.stderr)
        return 2
    problems = bot_differences(desired["bot_management"], bot)
    problems += rate_differences(desired["rate_limit"], entry)
    if robots is not None and MANAGED_MARKER not in robots.lower():
        problems.append("robots.txt: Cloudflare's managed block is not served")
    if args.save:
        try:
            write_new_file(
                args.save,
                json.dumps({"bot_management": bot, "http_ratelimit": entry}, indent=2),
            )
        except OSError as exc:
            print(f"cannot save snapshot ({type(exc).__name__})", file=sys.stderr)
            return 2
    for problem in problems:
        print(f"DRIFT {problem}")
    if problems:
        return 1
    print("ok   live zone matches deploy/edge/desired-state.json")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--zone-id")
    parser.add_argument("--robots-url")
    parser.add_argument("--save", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    if args.offline:
        problems = offline_problems(args.root)
        for problem in problems:
            print(f"FAIL {problem}")
        if problems:
            return 1
        print("ok   deploy/edge desired state, main.tf, pin and lock agree")
        return 0
    return live(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
