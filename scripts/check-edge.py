#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check the zone's edge settings against deploy/edge/desired-state.json (#613).

Usage:
    python3 -I scripts/check-edge.py --offline
    python3 -I scripts/check-edge.py --live --zone-id ID
        [--site-host HOST] [--save PATH]

--offline reads only files in this repository. It fails when the desired state
breaks a rule the owner decided (AI training blocked, search and link previews
not blocked, Bot Fight Mode off until it is measured, one flat rate rule on /e/
inside the Free plan limits), when a field the provider can send is neither given
a value nor marked unmanaged, or when deploy/edge/main.tf, its provider pin and
its lock file no longer match the desired state. CI runs this.

--live reads the zone with GET requests only and fails on any difference in a
managed bot-management field, so a setting changed in the dashboard cannot stay
unnoticed. It also reports whether the expected rate rule is present in the
zone's http_ratelimit phase; any other rule there belongs to someone else and is
never drift. The token comes from the environment variable
CLOUDFLARE_EDGE_READ_TOKEN (permissions Bot Management Read and Zone WAF Read on
one zone); it is sent only in the Authorization header to api.cloudflare.com,
redirects are refused, and it is never printed. --site-host (a plain public
hostname) also fetches https://HOST/robots.txt, connecting to a validated public
address with the hostname as SNI and no redirects, and requires Cloudflare's
managed block in it. --save writes the raw settings read, for a rollback record.

Exit 0 agrees, 1 differs, 2 usage or could not read (never read as green).
"""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
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
# Every field the provider's cloudflare_bot_management resource (5.29.0) can send,
# read from `tofu providers schema -json` (optional attributes; the computed-only
# id, stale_zone_configuration and using_latest_model are never sent). API names:
# the provider spells ai_search as "aisearch". A provider bump re-reads this list.
PROVIDER_FIELDS = (
    "ai_agents",
    "ai_bots_migration_opt_out",
    "ai_bots_protection",
    "ai_search",
    "ai_training",
    "ai_user",
    "auto_update_model",
    "bm_cookie_enabled",
    "bot_preference_sync_enabled",
    "cf_robots_variant",
    "content_bots_protection",
    "crawler_protection",
    "enable_js",
    "fight_mode",
    "is_robots_txt_managed",
    "jsd_api_results_enabled",
    "optimize_wordpress",
    "sbfm_definitely_automated",
    "sbfm_likely_automated",
    "sbfm_static_resource_protection",
    "sbfm_verified_bots",
    "suppress_session_score",
)
PROVIDER_ATTR = {"ai_search": "aisearch"}  # API name -> provider attribute
UNMANAGED = {"unmanaged": True}
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


OVERRIDE_VARS = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
)
OVERRIDE_MESSAGE = "REFUSED proxy or CA override variable is set"


def override_variables_set(environ: Any) -> list[str]:
    """Names of proxy or trust-store override variables present in environ."""
    return [name for name in OVERRIDE_VARS if name in environ]


class NotFound(Unreadable):
    """HTTP 404 from the API."""


def offline_problems(root: Path) -> list[str]:
    edge = root / EDGE
    problems: list[str] = []
    try:
        desired = json.loads((edge / "desired-state.json").read_text("utf-8"))
        main_tf = (edge / "main.tf").read_text("utf-8")
        versions = (edge / "versions.tf").read_text("utf-8")
        lock = (edge / ".terraform.lock.hcl").read_text("utf-8")
        manifest = json.loads((edge / "provider-fields.json").read_text("utf-8"))
    except (OSError, ValueError) as exc:
        return [f"cannot read deploy/edge files ({type(exc).__name__})"]
    if not isinstance(desired, dict) or desired.get("schema") != 1:
        return ["desired-state.json: schema must be 1"]
    bot = desired.get("bot_management")
    rl = desired.get("rate_limit")
    if not isinstance(bot, dict) or not isinstance(rl, dict):
        return ["desired-state.json: bot_management and rate_limit are required"]

    if set(bot) != set(PROVIDER_FIELDS):
        problems.append(
            "bot_management keys differ from the provider's fields: "
            f"{sorted(set(bot) ^ set(PROVIDER_FIELDS))}"
        )
    for key in PROVIDER_FIELDS:
        if key not in bot:
            continue
        if key in BOT_KEYS:
            if bot[key] not in BOT_KEYS[key]:
                allowed = sorted(str(a) for a in BOT_KEYS[key])
                problems.append(f"bot_management.{key} must be one of {allowed}")
        elif bot[key] != UNMANAGED:
            problems.append(f'bot_management.{key} must be {{"unmanaged": true}}')
    if set(rl) != set(RL_KEYS):
        problems.append(f"rate_limit keys differ: {sorted(set(rl) ^ set(RL_KEYS))}")
    else:
        problems += rate_limit_problems(rl)

    code = "\n".join(
        line for line in main_tf.splitlines() if not line.lstrip().startswith("#")
    )
    for key in PROVIDER_FIELDS:
        attr = PROVIDER_ATTR.get(key, key)
        sets_it = re.search(rf"^\s*{re.escape(attr)}\s*=", code, re.M) is not None
        if key in BOT_KEYS:
            if f"local.bot.{key}" not in code:
                problems.append(f"main.tf does not read bot_management.{key}")
        elif sets_it:
            problems.append(f"main.tf sends the unmanaged field {key}")
    if re.search(r'^\s*resource\s+"cloudflare_ruleset"', code, re.M):
        problems.append(
            "main.tf must not own the http_ratelimit phase (cloudflare_ruleset): "
            "on a shared zone that would replace other sites' rules"
        )
    if "NOT approved" not in main_tf:
        problems.append("main.tf header must say the apply path is NOT approved")
    if "desired-state.json" not in main_tf:
        problems.append("main.tf does not read desired-state.json")

    problems += manifest_problems(manifest, set(bot), PIN.search(versions))
    problems += wording_problems(root, desired)

    pin = PIN.search(versions)
    locked = re.search(r'version\s*=\s*"(\d+\.\d+\.\d+)"', lock)
    if pin is None:
        problems.append("versions.tf: provider is not pinned to an exact version")
    elif locked is None or locked.group(1) != pin.group(1):
        problems.append("lock file version differs from the provider pin")
    return problems


def manifest_problems(manifest: Any, desired_keys: set[str], pin: Any) -> list[str]:
    """The reviewed provider-schema manifest must agree with the other two lists."""
    if not isinstance(manifest, dict) or not isinstance(
        manifest.get("sendable_fields"), list
    ):
        return ["provider-fields.json: not a provider manifest"]
    fields = set(manifest["sendable_fields"])
    problems: list[str] = []
    if fields != set(PROVIDER_FIELDS):
        problems.append(
            "provider-fields.json (manifest) and PROVIDER_FIELDS differ: "
            f"{sorted(fields ^ set(PROVIDER_FIELDS))}"
        )
    if fields != desired_keys:
        problems.append(
            "provider-fields.json (manifest) and desired-state.json differ: "
            f"{sorted(fields ^ desired_keys)}"
        )
    if pin is not None and manifest.get("provider_version") != pin.group(1):
        problems.append("provider-fields.json (manifest) version differs from the pin")
    return problems


_NEVER_SENT = re.compile(r"\b(?:never|not)\s+(?:be\s+)?sen[dt]\b", re.I)


def wording_problems(root: Path, desired: Any) -> list[str]:
    """No text may claim an unmanaged field is never sent: that is unverified."""
    texts = {"desired-state.json": json.dumps(desired), "main.tf": ""}
    for name, path in (
        ("main.tf", root / EDGE / "main.tf"),
        ("docs/edge.md", root / "docs" / "edge.md"),
    ):
        try:
            texts[name] = path.read_text("utf-8")
        except OSError:
            texts.setdefault(name, "")
    return [
        f"{name}: claims fields are never sent by an apply; that is unverified"
        for name, text in texts.items()
        if _NEVER_SENT.search(text)
    ]


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
    if token is not None and override_variables_set(os.environ):
        # A proxy or a replaced trust store could read the token in transit.
        raise Unreadable(OVERRIDE_MESSAGE)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: Any, **kwargs: Any) -> None:
            return None

    try:
        request = urllib.request.Request(url, method="GET")  # noqa: S310 (https checked)
        if token is not None:
            request.add_header("Authorization", f"Bearer {token}")
        opener = urllib.request.build_opener(
            NoRedirect,
            urllib.request.ProxyHandler({}),  # never an environment proxy
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )
        with opener.open(request, timeout=TIMEOUT) as response:  # noqa: S310
            body: bytes = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NotFound("HTTP 404") from None
        extra = ""
        if exc.code == 429:
            extra = f", Retry-After {exc.headers.get('Retry-After', 'unknown')}"
        raise Unreadable(f"HTTP {exc.code}{extra}") from None
    except (ValueError, OSError, http.client.HTTPException) as exc:
        # ValueError covers UnicodeError. Only the type name is kept: the text of
        # an exception can carry the URL or header values.
        raise Unreadable(type(exc).__name__) from None
    if len(body) > MAX_BYTES:
        raise Unreadable("response too large")
    return body


HOST_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
HOST_RE = re.compile(rf"{HOST_LABEL}(?:\.{HOST_LABEL})+")
WELL_KNOWN_NAT64 = ipaddress.ip_network("64:ff9b::/96")
SIX_TO_FOUR = ipaddress.ip_network("2002::/16")


def parse_site_host(value: str) -> str | None:
    """A plain public-looking hostname, lowercased, or None. No IP literals."""
    host = value.lower()
    if len(host) > 253 or not HOST_RE.fullmatch(host):
        return None
    if not re.search(r"[a-z]", host.rsplit(".", 1)[1]):
        return None  # an all-numeric last label is an IP-like name
    return host


def blocked_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return blocked_address(ip.ipv4_mapped)
        if ip in WELL_KNOWN_NAT64:
            return blocked_address(ipaddress.IPv4Address(ip.packed[-4:]))
        if ip in SIX_TO_FOUR:
            return blocked_address(ipaddress.IPv4Address(ip.packed[2:6]))
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def public_addresses(host: str) -> list[str]:
    """Resolve host; refuse if ANY address is not a public unicast address."""
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except (OSError, ValueError):
        raise Unreadable("cannot resolve the site host") from None
    found: list[str] = []
    for info in infos:
        text = str(info[4][0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(text)
        except ValueError:
            raise Unreadable("unusable address for the site host") from None
        if blocked_address(ip):
            raise Unreadable("the site host resolves to a non-public address")
        if text not in found:
            found.append(text)
    if not found:
        raise Unreadable("the site host has no address")
    return found


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to one validated address; the hostname stays the Host and SNI name."""

    def __init__(self, host: str, address: str, timeout: float) -> None:
        super().__init__(host, 443, timeout=timeout)
        self._address = address

    def connect(self) -> None:
        sock = socket.create_connection((self._address, 443), self.timeout)
        try:
            self.sock = ssl.create_default_context().wrap_socket(
                sock, server_hostname=self.host
            )
        except BaseException:
            try:
                sock.close()
            except Exception:  # noqa: S110 (best effort; the original error is raised)
                pass
            raise


def fetch_robots(host: str) -> str:
    """GET https://host/robots.txt with no redirects, from a validated address."""
    last = Unreadable("no address worked")
    for address in public_addresses(host):
        conn = PinnedHTTPSConnection(host, address, TIMEOUT)
        try:
            conn.request(
                "GET",
                "/robots.txt",
                headers={
                    "User-Agent": "reach-around-check-edge",
                    "Accept": "text/plain",
                },
            )
            response = conn.getresponse()
            if response.status != 200:  # a redirect is refused, never followed
                raise Unreadable(f"robots.txt answered HTTP {response.status}")
            body = response.read(MAX_BYTES + 1)
        except (ValueError, OSError, http.client.HTTPException) as exc:
            last = Unreadable(type(exc).__name__)
            continue
        except Unreadable as exc:
            raise exc from None
        finally:
            conn.close()
        if len(body) > MAX_BYTES:
            raise Unreadable("response too large")
        return body.decode("utf-8", "replace")
    raise last


_JOINED = re.compile(r"\\\r?\n\s*")
_TOOLS = "|".join(["tofu", "opentofu", "terraform", "terragrunt"])
_VERBS = "|".join(["apply", "destroy", "import"])
_MUTATING = (
    re.compile(rf"\b(?:{_TOOLS})\b.*\b(?:{_VERBS})\b", re.I),
    re.compile(r"\bmake\b(?:\s+-\S+)*(?:\s+\S+)*?\s+(?:apply|destroy)\b", re.I),
    re.compile(r"^\s*(?:apply|destroy)\s*:", re.I),
)


def mutating_lines(text: str) -> list[str]:
    """Lines that run a state-changing infrastructure command (a regex scan)."""
    joined = _JOINED.sub(" ", text)
    return [
        line.strip()
        for line in joined.splitlines()
        if any(pattern.search(line) for pattern in _MUTATING)
    ]


_HEX32 = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{32}(?![0-9A-Za-z])")
DOC_PLACEHOLDER = "0123456789abcdef0123456789abcdef"


def zone_like_ids(text: str) -> list[str]:
    """32-hex strings that look like a zone or account id (placeholders aside)."""
    return [m for m in _HEX32.findall(text) if len(set(m)) > 1 and m != DOC_PLACEHOLDER]


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
        if v != UNMANAGED and live.get(k) != v
    ]


def rate_differences(desired: dict[str, Any], live: Any) -> list[str]:
    """Is our rule present and right? Other rules in the phase are not ours."""
    rules = live.get("rules") if isinstance(live, dict) else None
    ours = [r for r in rules or [] if isinstance(r, dict)]
    rule = next((r for r in ours if r.get("ref") == desired["ref"]), None)
    if rule is None:
        rule = next(
            (r for r in ours if r.get("expression") == desired["expression"]), None
        )
    if rule is None:
        return [
            f"rate_limit: expected rule {desired['ref']!r} is not present in the "
            "http_ratelimit phase; add it as a MANUAL step (docs/edge.md) and "
            "never replace the phase, other sites' rules live there"
        ]
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


def load_desired() -> dict[str, Any]:
    data = json.loads((ROOT / EDGE / "desired-state.json").read_text("utf-8"))
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("bot_management"), dict)
        or not isinstance(data.get("rate_limit"), dict)
        or not set(RL_KEYS) <= set(data["rate_limit"])
    ):
        raise ValueError("shape")
    result: dict[str, Any] = data
    return result


def live(args: argparse.Namespace) -> int:
    token = os.environ.get(TOKEN_ENV, "")
    zone = args.zone_id or ""
    if not token or not re.fullmatch(r"[0-9a-f]{32}", zone):
        print(f"usage: need {TOKEN_ENV} and --zone-id (32 hex)", file=sys.stderr)
        return 2
    if override_variables_set(os.environ):
        print(OVERRIDE_MESSAGE, file=sys.stderr)
        return 2
    site = None
    if args.site_host is not None:
        site = parse_site_host(args.site_host)
        if site is None:
            print("usage: --site-host must be a plain hostname", file=sys.stderr)
            return 2
    try:
        desired = load_desired()
    except (OSError, ValueError):
        print("UNREADABLE desired state", file=sys.stderr)
        return 2
    try:
        bot = api_result(f"/zones/{zone}/bot_management", token)
        try:
            entry = api_result(
                f"/zones/{zone}/rulesets/phases/http_ratelimit/entrypoint", token
            )
        except NotFound:
            entry = {"rules": []}  # no ruleset in the phase yet
        robots = fetch_robots(site) if site else None
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
    parser.add_argument("--site-host")
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
