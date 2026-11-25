#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check the zone's edge settings against deploy/edge/desired-state.json (#613).

Usage:
    python3 -I scripts/check-edge.py --offline
    python3 -I scripts/check-edge.py --live --zone-id ID
        [--site-host HOST] [--save PATH]

--offline reads only files in this repository. It fails when the desired state
breaks a rule the owner decided (AI training blocked, search and link previews
not blocked, Bot Fight Mode off until it is measured, one flat rate rule on /e/t
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
import hashlib
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
RL_REF = "reach_around_mint_cap"  # the rule's identity in the zone
PIN = re.compile(r'version\s*=\s*"= (\d+\.\d+\.\d+)"')

# Reviewed policy. These values live here, not in the files they judge, so a change
# to a judged file cannot also change the rule it is judged by. This file and
# deploy/edge/ are owned in .github/CODEOWNERS; a change to a value below is a
# reviewed change (docs/edge.md, "Structural controls").
PROVIDER_SOURCE = "registry.opentofu.org/cloudflare/cloudflare"
MIN_TOFU_VERSION = '">= 1.9.0"'  # exact: lowering or loosening it fails the check
MANIFEST_COMMAND = (
    "tofu providers schema -json | python3 -I scripts/gen-provider-fields.py "
    "5.29.0 > deploy/edge/provider-fields.json"
)
MANIFEST_COMPUTED_ONLY = ["id", "stale_zone_configuration", "using_latest_model"]
# sha256 of the files that receive or shape the API token. plan.sh gets the token;
# tofu.rc decides which provider binary tofu installs. Any change to either needs
# the new digest recorded here in the same change, which CODEOWNERS routes to the
# owner. The digest is an integrity pin, not a proof the script is safe: the
# structural scan in launcher_problems() is a second, weaker layer.
LAUNCHER_PINS = {
    "plan.sh": "ae61dea66bfca6df395c77f76b323b5d35f1a48606d36c8bb573e754a64de03e",
    "tofu.rc": "fdbdca281d88fab3f0efa566da7689e9653a98ae98818192acf018717624ca82",
}


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

    problems += module_problems(edge)
    problems += launcher_problems(edge)
    if "NOT approved" not in main_tf:
        problems.append("main.tf header must say the apply path is NOT approved")
    if "desired-state.json" not in main_tf:
        problems.append("main.tf does not read desired-state.json")

    problems += manifest_problems(manifest, set(bot), PIN.search(versions))
    problems += wording_problems(root, desired)

    pin = PIN.search(versions)
    if pin is None:
        problems.append("versions.tf: provider is not pinned to an exact version")
    problems += lock_problems(lock, pin)
    return problems


_LOCK_HASH = re.compile(r'"(?:h1:[A-Za-z0-9+/]{43}=|zh:[0-9a-f]{64})"')


def lock_problems(lock: str, pin: Any) -> list[str]:
    """The lock file must hold exactly one entry of the reviewed shape (tokenized)."""
    try:
        blocks = hcl_blocks(lock)
    except HclError as exc:
        return [f".terraform.lock.hcl: cannot be parsed safely ({exc})"]
    if len(blocks) != 1 or blocks[0].kind != "provider" or blocks[0].children:
        return [".terraform.lock.hcl: must hold exactly one provider entry"]
    entry = blocks[0]
    attrs = dict(entry.attrs)
    problems: list[str] = []
    if entry.labels != (PROVIDER_SOURCE,):
        problems.append(f".terraform.lock.hcl: the entry must be for {PROVIDER_SOURCE}")
    if len(entry.attrs) != 3 or set(attrs) != {"version", "constraints", "hashes"}:
        return problems + [
            ".terraform.lock.hcl: the entry must set version, constraints and hashes"
        ]
    version = attrs["version"].strip('"')
    if not re.fullmatch(r'"\d+\.\d+\.\d+"', attrs["version"]):
        problems.append(".terraform.lock.hcl: version is not an exact version")
    if attrs["constraints"].strip('"') != version:
        problems.append(".terraform.lock.hcl: constraints differ from the version")
    if pin is not None and version != pin.group(1):
        problems.append(".terraform.lock.hcl: version differs from the provider pin")
    hashes = attrs["hashes"]
    items = hashes[1:-1].split(",") if hashes[:1] == "[" and hashes[-1:] == "]" else []
    items = [i.strip("\n") for i in items if i.strip("\n")]
    if not items or not all(_LOCK_HASH.fullmatch(i) for i in items):
        problems.append(".terraform.lock.hcl: hashes must be h1: and zh: digests")
    elif not any(i.startswith('"h1:') for i in items) or not any(
        i.startswith('"zh:') for i in items
    ):
        problems.append(".terraform.lock.hcl: hashes need both h1: and zh: entries")
    return problems


# plan.sh scan: a second layer behind the digest pin. It refuses what a token-
# bearing launcher has no reason to use. It cannot prove a shell script safe.
_PLAN_FORBIDDEN = re.compile(
    r"(?<![\w$-])(?:eval|source|curl|wget|nc|ncat|netcat|socat|ssh|scp|sftp|telnet|ftp"
    r"|python3?|perl|ruby|node|php|lua|awk|gawk|openssl|base64|xxd|od|dd|tee|nohup"
    r"|declare|typeset|nameref|apply|destroy|import|console|taint|workspace|state"
    r"|force-unlock|login|logout)(?![\w=-])"
    r"|/dev/(?:tcp|udp)|\+=|\$\{!|printf\s+-v|`|>>|\bexec\s+(?!\"\$@\" 3<&-)"
    r"|>\s*(?!&2\b|/dev/null\b)|\bcommand\b|\balias\b|\.\s+[/\"$]"
)
_PLAN_TOKEN_LINES = frozenset(
    {
        "HOME | CLOUDFLARE_API_TOKEN | TF_VAR_zone_id | TOFU_BIN) ;;",
        'if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then',
        'echo "plan: CLOUDFLARE_API_TOKEN is not set" >&2',
        "'IFS= read -r CLOUDFLARE_API_TOKEN <&3 || exit 70",
        "export CLOUDFLARE_API_TOKEN",
        'bash "$here" "$tofu_bin" "$@" 3<<<"$CLOUDFLARE_API_TOKEN"',
    }
)
_PLAN_RUN = re.compile(r"^\s*run_tofu\s+(\S+)")
_PLAN_VERBS = ("init", "plan")


def plan_script_problems(text: str) -> list[str]:
    """Structural scan of plan.sh: allowlisted tofu verbs, no network or eval."""
    problems: list[str] = []
    verbs: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "CLOUDFLARE_API_TOKEN" in line and line not in _PLAN_TOKEN_LINES:
            problems.append(
                f"plan.sh:{number}: the token appears on a line that is not reviewed"
            )
        hit = _PLAN_FORBIDDEN.search(line)
        if hit is not None:
            problems.append(f"plan.sh:{number}: refused construct {hit.group(0)!r}")
        run = _PLAN_RUN.match(raw)
        if run is not None:
            verbs.append(run.group(1))
    if verbs != list(_PLAN_VERBS):
        problems.append(f"plan.sh: run_tofu may only run {list(_PLAN_VERBS)}")
    return problems


def launcher_problems(edge: Path) -> list[str]:
    """plan.sh and tofu.rc must match the reviewed digest and pass the scan."""
    problems: list[str] = []
    for name, want in LAUNCHER_PINS.items():
        try:
            data = (edge / name).read_bytes()
        except OSError:
            problems.append(f"deploy/edge/{name}: cannot be read")
            continue
        if hashlib.sha256(data).hexdigest() != want:
            problems.append(
                f"deploy/edge/{name}: differs from the reviewed digest pinned in "
                "scripts/check-edge.py (LAUNCHER_PINS); a change needs the new "
                "digest recorded there in the same reviewed change"
            )
        if name == "plan.sh":
            problems += plan_script_problems(data.decode("utf-8", "replace"))
    return problems


ALLOWED_EDGE_FILES = frozenset(
    {
        ".gitignore",
        ".terraform.lock.hcl",
        "desired-state.json",
        "main.tf",
        "plan.sh",
        "provider-fields.json",
        "tofu.rc",
        "variables.tf",
        "versions.tf",
    }
)
ALLOWED_BLOCKS = frozenset({"terraform", "provider", "variable", "locals", "resource"})
# The one resource, and the exact attribute-to-key binding it must have.
RESOURCE = ("cloudflare_bot_management", "zone")


class HclError(Exception):
    """The text is outside the small HCL subset these files use."""


Token = tuple[str, str]  # (kind, text): ident, string, punct, nl
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*|[0-9][0-9.]*")
_PUNCT = frozenset("{}[]()=,.:!<>&|+-*?^~")
_ESCAPES = frozenset('"\\nt')
MAX_HCL_CHARS = 65536
MAX_DEPTH = 8


def hcl_tokens(text: str) -> list[Token]:
    """Tokens of one .tf file; raises HclError on anything outside the subset.

    Strings are single tokens, so a brace inside one never reaches the block
    parser. A string may not hold a brace at all (that also rules out the
    dollar-brace interpolation), nor a raw newline, a control character or a
    non-ASCII one. Comments start with # and run to the end of the line. A
    slash, a dollar sign outside a string, a backtick, a lone carriage return
    and the heredoc marker are refused outside comments, and so is any other
    character.
    """
    if len(text) > MAX_HCL_CHARS:
        raise HclError("file too large")
    text = text.replace("\r\n", "\n")
    if "\r" in text or "\x00" in text:
        raise HclError("carriage return or NUL character")
    tokens: list[Token] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t":
            i += 1
        elif c == "\n":
            tokens.append(("nl", c))
            i += 1
        elif c == "#":
            while i < n and text[i] != "\n":
                i += 1
        elif c == '"':
            i, value = _hcl_string(text, i + 1)
            tokens.append(("string", value))
        elif c.isascii() and (c.isalnum() or c == "_"):
            match = _IDENT.match(text, i)
            if match is None:
                raise HclError("bad identifier")
            tokens.append(("ident", match.group(0)))
            i = match.end()
        elif c in _PUNCT:
            if text.startswith("<<", i):
                raise HclError("heredoc marker")
            tokens.append(("punct", c))
            i += 1
        else:
            raise HclError("character not allowed in code")
    return tokens


def _hcl_string(text: str, start: int) -> tuple[int, str]:
    """The string body that starts at start; returns (index after the quote, body)."""
    out: list[str] = []
    i, n = start, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            return i + 1, "".join(out)
        if c == "\\":
            if i + 1 >= n or text[i + 1] not in _ESCAPES:
                raise HclError("unsupported escape in a string")
            out.append(text[i : i + 2])
            i += 2
            continue
        if c in "{}":
            raise HclError("brace inside a string")
        if not (c.isascii() and c.isprintable()):
            raise HclError("character not allowed in a string")
        out.append(c)
        i += 1
    raise HclError("unterminated string")


class Block:
    """A parsed block: kind, labels, attributes (name, compact value), children."""

    def __init__(self, kind: str, labels: tuple[str, ...]) -> None:
        self.kind = kind
        self.labels = labels
        self.attrs: list[tuple[str, str]] = []
        self.children: list[Block] = []


_CLOSERS = {"{": "}", "[": "]", "(": ")"}


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.t = tokens
        self.i = 0

    def peek(self, offset: int = 0) -> Token | None:
        j = self.i + offset
        return self.t[j] if j < len(self.t) else None

    def skip_newlines(self) -> None:
        while (tok := self.peek()) is not None and tok[0] == "nl":
            self.i += 1

    def top(self) -> list[Block]:
        blocks: list[Block] = []
        while True:
            self.skip_newlines()
            if self.peek() is None:
                return blocks
            blocks.append(self.block(0))

    def block(self, depth: int) -> Block:
        if depth > MAX_DEPTH:
            raise HclError("blocks nested too deeply")
        tok = self.peek()
        if tok is None or tok[0] != "ident":
            raise HclError("expected a block name")
        self.i += 1
        labels: list[str] = []
        while (label := self.peek()) is not None and label[0] in ("string", "ident"):
            labels.append(label[1])
            self.i += 1
        if self.peek() != ("punct", "{"):
            raise HclError("expected an opening brace")
        self.i += 1
        block = Block(tok[1], tuple(labels))
        while True:
            self.skip_newlines()
            nxt = self.peek()
            if nxt is None:
                raise HclError("unbalanced braces")
            if nxt == ("punct", "}"):
                self.i += 1
                break
            self.item(block, depth)
        # A block ends its line: anything after the closing brace on the same line
        # (another block, an attribute, a second brace) is refused.
        after = self.peek()
        if after is not None and after[0] != "nl":
            raise HclError("code after a closing brace on the same line")
        return block

    def item(self, block: Block, depth: int) -> None:
        tok = self.peek()
        if tok is None or tok[0] != "ident":
            raise HclError("expected an attribute or block name")
        if self.peek(1) == ("punct", "="):
            self.i += 2
            block.attrs.append((tok[1], self.expression()))
        else:
            block.children.append(self.block(depth + 1))

    def expression(self) -> str:
        """Tokens up to the end of the line at bracket depth 0, as compact text."""
        parts: list[str] = []
        stack: list[str] = []
        while True:
            tok = self.peek()
            if tok is None:
                break
            kind, text = tok
            self.i += 1
            if kind == "nl":
                if not stack:
                    break
                if parts and parts[-1] not in _CLOSERS and parts[-1] != "\n":
                    parts.append("\n")
                continue
            if kind == "punct" and text in _CLOSERS:
                stack.append(_CLOSERS[text])
            elif kind == "punct" and text in _CLOSERS.values():
                if not stack or stack.pop() != text:
                    raise HclError("unbalanced brackets")
                if parts and parts[-1] == "\n":
                    parts.pop()
            parts.append(f'"{text}"' if kind == "string" else text)
        if stack or not parts:
            raise HclError("unbalanced brackets or an empty value")
        return "".join(parts)


def hcl_blocks(text: str) -> list[Block]:
    """Top-level blocks of one .tf file, built from tokens (never from raw lines)."""
    return _Parser(hcl_tokens(text)).top()


def attr_map(path: str, what: str, block: Block) -> tuple[dict[str, str], list[str]]:
    found: dict[str, str] = {}
    problems: list[str] = []
    if block.children:
        problems.append(f"{path}: {what} holds something other than assignments")
    for name, value in block.attrs:
        if name in found:
            problems.append(f"{path}: {what} assigns {name} twice")
        found[name] = value
    return found, problems


EXPECTED_LOCALS = {
    "desired": 'jsondecode(file(join("/",[path.module,"desired-state.json"])))',
    "bot": "local.desired.bot_management",
}
EXPECTED_CONDITION = 'can(regex("^[0-9a-f]+$",var.zone_id))&&length(var.zone_id)==32'
_CLOUDFLARE_PIN = re.compile(
    r'\{source="cloudflare/cloudflare"\nversion="= \d+\.\d+\.\d+"\}'
)


def terraform_problems(name: str, block: Block) -> list[str]:
    """The terraform block: required_version and the one pinned provider only."""
    problems: list[str] = []
    attrs = dict(block.attrs)
    if len(block.attrs) != 1 or set(attrs) != {"required_version"}:
        problems.append(f"{name}: terraform may only set required_version")
    elif attrs["required_version"] != MIN_TOFU_VERSION:
        problems.append(
            f"{name}: terraform required_version must be exactly {MIN_TOFU_VERSION} "
            "(the reviewed minimum; lowering or loosening it is a policy change)"
        )
    kids = block.children
    if len(kids) != 1 or kids[0].kind != "required_providers" or kids[0].labels:
        problems.append(f"{name}: terraform may only hold required_providers")
        return problems
    inner = kids[0]
    if inner.children or [a for a, _ in inner.attrs] != ["cloudflare"]:
        problems.append(f"{name}: required_providers may only list cloudflare")
    elif not _CLOUDFLARE_PIN.fullmatch(inner.attrs[0][1]):
        problems.append(f"{name}: the cloudflare provider source or pin differs")
    return problems


def variable_problems(name: str, block: Block) -> list[str]:
    """The zone_id variable: description, type and one validation block."""
    if block.labels != ("zone_id",):
        return [f"{name}: the only variable allowed is zone_id"]
    problems: list[str] = []
    attrs = dict(block.attrs)
    if len(block.attrs) != 2 or set(attrs) != {"description", "type"}:
        problems.append(f"{name}: variable zone_id may only set description and type")
    elif attrs["type"] != "string" or not attrs["description"].startswith('"'):
        problems.append(f"{name}: variable zone_id must be a described string")
    kids = block.children
    if len(kids) != 1 or kids[0].kind != "validation" or kids[0].labels:
        problems.append(f"{name}: variable zone_id may only hold one validation")
        return problems
    rule = dict(kids[0].attrs)
    if (
        kids[0].children
        or len(kids[0].attrs) != 2
        or set(rule) != {"condition", "error_message"}
    ):
        problems.append(f"{name}: validation may only set condition and error_message")
    elif rule["condition"] != EXPECTED_CONDITION:
        problems.append(f"{name}: the zone_id validation differs from the reviewed one")
    return problems


def module_problems(edge: Path) -> list[str]:
    """main.tf is bound to desired-state.json by exact structure, not by substring.

    Every .tf file is tokenized (strings are atomic, so braces inside them cannot
    move a block boundary) and may hold only the allowed block kinds; the only
    resource is cloudflare_bot_management.zone, and each attribute in it must be
    assigned exactly the matching local.bot key. The terraform and variable
    blocks are checked against an allowlist of attributes. Extra resources, data
    sources, modules, provisioners, lifecycle blocks, other locals or a literal
    value all fail, and so does any text the tokenizer cannot parse.
    """
    problems: list[str] = []
    for item in sorted(edge.iterdir(), key=lambda p: p.name):
        if item.name not in ALLOWED_EDGE_FILES or not item.is_file():
            problems.append(
                f"deploy/edge/{item.name}: not an expected file (saved plans and "
                "state never belong in this directory)"
            )
    resources = 0
    variables = 0
    for tf in sorted(edge.glob("*.tf")):
        try:
            # bytes, not read_text: universal newlines would turn a lone CR into LF
            text = tf.read_bytes().decode("utf-8")
        except (OSError, ValueError):
            problems.append(f"{tf.name}: cannot be read")
            continue
        try:
            blocks = hcl_blocks(text)
        except HclError as exc:
            problems.append(
                f"{tf.name}: cannot be parsed safely ({exc}); only # comments, "
                "no heredocs, and no braces inside strings are allowed"
            )
            continue
        for block in blocks:
            kind = block.kind
            if kind not in ALLOWED_BLOCKS:
                problems.append(f"{tf.name}: block kind {kind!r} is not allowed")
            elif kind == "resource":
                resources += 1
                problems += resource_problems(tf.name, block)
            elif kind == "locals":
                got, bad = attr_map(tf.name, "locals", block)
                problems += bad
                if got != EXPECTED_LOCALS or tf.name != "main.tf":
                    problems.append(f"{tf.name}: locals differ from the reviewed ones")
            elif kind == "variable":
                variables += 1
                problems += variable_problems(tf.name, block)
            elif kind == "terraform":
                problems += terraform_problems(tf.name, block)
            elif kind == "provider" and (
                block.labels != ("cloudflare",) or block.attrs or block.children
            ):
                problems.append(
                    f"{tf.name}: provider must be an empty cloudflare block"
                )
    if resources != 1:
        problems.append(
            f"deploy/edge must hold exactly one resource, found {resources}"
        )
    if variables > 1:
        problems.append("deploy/edge must declare the zone_id variable once")
    return problems


def resource_problems(name: str, block: Block) -> list[str]:
    if block.labels != RESOURCE:
        return [
            f"{name}: the only resource allowed is {RESOURCE[0]}.{RESOURCE[1]} "
            "(a cloudflare_ruleset would own the whole http_ratelimit phase and "
            "replace other sites' rules)"
        ]
    got, problems = attr_map(name, "the resource", block)
    want = {"zone_id": "var.zone_id"}
    want |= {PROVIDER_ATTR.get(k, k): f"local.bot.{k}" for k in BOT_KEYS}
    for attr in sorted(set(want) | set(got)):
        if got.get(attr) != want.get(attr):
            problems.append(
                f"{name}: {attr} must be {want.get(attr, '<absent>')!r}, "
                f"found {got.get(attr, '<absent>')!r}"
            )
    return problems


def manifest_problems(manifest: Any, desired_keys: set[str], pin: Any) -> list[str]:
    """The reviewed provider-schema manifest must agree with the other two lists."""
    if not isinstance(manifest, dict) or not isinstance(
        manifest.get("sendable_fields"), list
    ):
        return ["provider-fields.json: not a provider manifest"]
    fields = set(manifest["sendable_fields"])
    problems: list[str] = []
    expected = {
        "schema": 1,
        "provider": PROVIDER_SOURCE,
        "resource": RESOURCE[0],
        "command": MANIFEST_COMMAND,
        "computed_only": MANIFEST_COMPUTED_ONLY,
    }
    for key, want in expected.items():
        got = manifest.get(key)
        if got != want or type(got) is not type(want):
            problems.append(
                f"provider-fields.json (manifest) {key} must be {want!r}, found {got!r}"
            )
    if set(manifest) != set(expected) | {"provider_version", "sendable_fields"}:
        problems.append("provider-fields.json (manifest) holds unexpected keys")
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
        or expression != 'http.request.uri.path eq "/e/t"'
    ):
        problems.append("rate_limit.expression must be exactly the /e/t path match")
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
    if not isinstance(count, int) or isinstance(count, bool) or not 2 <= count <= 30:
        problems.append(
            "rate_limit.requests_per_period must be an integer from 2 to 30"
        )
    if rl["ref"] != RL_REF:
        problems.append(f"rate_limit.ref must be {RL_REF!r}")
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
    by_ref = [r for r in ours if r.get("ref") == desired["ref"]]
    by_expression = [r for r in ours if r.get("expression") == desired["expression"]]
    claimants = {id(r): r for r in by_ref + by_expression}
    if len(claimants) > 1:  # duplicate ref, duplicate expression or a mix of both
        return [
            "rate_limit: more than one rule claims this rule's identity "
            f"({desired['ref']!r}); ambiguous, so not trusted"
        ]
    matches = by_ref or by_expression
    rule = matches[0] if matches else None
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
    invalid = offline_problems(ROOT)
    if invalid:
        for problem in invalid:
            print(f"INVALID {problem}", file=sys.stderr)
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
