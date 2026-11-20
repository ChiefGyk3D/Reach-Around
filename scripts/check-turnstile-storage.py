#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Measure what the invisible bot check leaves in a clean browser (#610).

Usage: python3 scripts/check-turnstile-storage.py (--live | --stub)
       [--browser chromium|firefox] [--executable PATH] [--wait SECONDS]
       [--json] [--strict]

What it does: builds a throwaway copy of the example site with the bot check on
and Cloudflare's published always-pass TEST site key, serves it from
127.0.0.1 with the build's own ``_headers`` policy applied, opens the home page
in a brand-new browser context (no cookies, no storage, no profile), waits for
the check to finish, and prints:

  - cookies (name, domain, path, lifetime, flags; never a value)
  - localStorage and sessionStorage key names, per frame origin (never values)
  - IndexedDB database names, per frame origin
  - every host the page talked to, and every host that is not this machine
  - console errors (a refused script or frame shows up here)
  - the final state of the check ("verified" or "unverified"; never the token)

``--live`` really loads Cloudflare's script, so it contacts Cloudflare. Run it
only when you mean to, with the test key this script uses, never with a
production key. ``--stub`` answers every challenges.cloudflare.com request
locally with a stand-in script (no contact with Cloudflare); it only proves the
harness works, and its numbers say nothing about Turnstile. One of the two flags
is required so a run is never an accident.

Browser: Chromium by default. Set ``RA_CHROMIUM`` or pass ``--executable`` for a
browser binary; ``--browser firefox`` uses Playwright's Firefox. Playwright must
be installed (``pip install playwright``; ``playwright install chromium``).

Exit: 0 report printed (and, with ``--strict``, nothing stored and no host
other than this machine and the Turnstile origin); 1 ``--strict`` and something
was found; 2 usage or a setup error.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
TEST_SITE_KEY = "1x00000000000000000000AA"  # Cloudflare's always-pass test key
TURNSTILE_HOST = "challenges.cloudflare.com"
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})
STUB_SCRIPT = (
    "window.turnstile={render:function(el,o){"
    "setTimeout(function(){o.callback('STUB.token-0')},50);return 'stub'}};"
)

Rules = dict[str, list[str]]


# --- pure helpers (tested without a browser) -----------------------------------


def parse_headers(text: str) -> dict[str, list[str]]:
    """A Cloudflare Pages ``_headers`` file as {path: [raw lines]}."""
    found: dict[str, list[str]] = {}
    for chunk in text.strip().split("\n\n"):
        lines = chunk.split("\n")
        found.setdefault(lines[0].strip(), []).extend(
            line.strip() for line in lines[1:] if line.strip()
        )
    return found


def _matches(pattern: str, path: str) -> bool:
    if pattern.endswith("/*"):
        return path.startswith(pattern[:-1]) or path == pattern[:-2]
    if pattern == "/*":
        return True
    return pattern == path


def headers_for(rules: Mapping[str, list[str]], path: str) -> dict[str, str]:
    """The headers Pages would send for ``path``: every matching rule in file
    order, a ``! Name`` line detaching what an earlier rule set."""
    out: dict[str, str] = {}
    for pattern, lines in rules.items():
        if not _matches(pattern, path):
            continue
        for line in lines:
            if line.startswith("! "):
                out.pop(line[2:].strip().lower(), None)
            elif ": " in line:
                name, value = line.split(": ", 1)
                out[name.lower()] = value
    return out


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def foreign_hosts(urls: list[str]) -> list[str]:
    """Hosts other than this machine, sorted, without repeats."""
    hosts = {
        host_of(u) for u in urls if urlsplit(u).scheme in ("http", "https", "ws", "wss")
    }
    return sorted(h for h in hosts if h and h not in LOCAL_HOSTS)


def storage_found(report: Mapping[str, Any]) -> bool:
    return bool(
        report["cookies"]
        or any(report["local_storage"].values())
        or any(report["session_storage"].values())
        or any(report["indexed_db"].values())
    )


def render(report: Mapping[str, Any]) -> str:
    out = [f"mode: {report['mode']}  browser: {report['browser']}"]
    out.append(f"check finished: {report['state']} (waited {report['waited']:.1f}s)")
    out.append(f"cookies: {len(report['cookies'])}")
    for c in report["cookies"]:
        out.append(
            f"  {c['name']}  domain={c['domain']} path={c['path']} "
            f"expires={c['expires']} flags={c['flags']}"
        )
    for title, key in (
        ("localStorage keys", "local_storage"),
        ("sessionStorage keys", "session_storage"),
        ("IndexedDB databases", "indexed_db"),
    ):
        out.append(f"{title}:")
        if not any(report[key].values()):
            out.append("  none")
        for origin, names in sorted(report[key].items()):
            if names:
                out.append(f"  {origin}: {', '.join(sorted(names))}")
    out.append("hosts contacted (every host, with request count):")
    for host, n in sorted(report["hosts"].items()):
        out.append(f"  {host}  x{n}")
    out.append(
        f"hosts other than this machine: {', '.join(report['foreign']) or 'none'}"
    )
    out.append(f"console errors: {len(report['console_errors'])}")
    for line in report["console_errors"][:20]:
        out.append(f"  {line}")
    if report["mode"] == "stub":
        out.append(
            "STUB RUN: no contact with Cloudflare; these numbers say nothing "
            "about Turnstile."
        )
    return "\n".join(out)


# --- the local server ----------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    root: Path
    rules: Rules

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        # The page's own count beacons: accepted and dropped, nothing is kept.
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(min(length, 4096))
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        target = (self.root / path.lstrip("/")).resolve()
        if self.root.resolve() not in (target, *target.parents):
            self.send_error(404)
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
        self.send_response(200)
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers_for(self.rules, path).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


def build_site_dir(out: Path) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from reach_around.config import load_site_config
    from reach_around.site.build import build_site

    instance = ROOT / "examples" / "instance"
    config = load_site_config(instance / "site.yaml")
    analytics = config.analytics.model_copy(
        update={
            "page_views": True,
            "turnstile_beacon": True,
            "turnstile_site_key": TEST_SITE_KEY,
        }
    )
    build_site(
        config.model_copy(update={"analytics": analytics}),
        out,
        today=date.today(),
        protect=[instance / "site.yaml"],
        assets_dir=instance,
    )


# --- the browser run -----------------------------------------------------------

_PROBE = """async () => {
  const out = {local: [], session: [], idb: []};
  try { out.local = Object.keys(localStorage); } catch (e) {}
  try { out.session = Object.keys(sessionStorage); } catch (e) {}
  try {
    if (indexedDB.databases) {
      out.idb = (await indexedDB.databases()).map((d) => d.name || "");
    }
  } catch (e) {}
  return out;
}"""


def measure(args: argparse.Namespace) -> dict[str, Any]:
    from playwright.sync_api import Route, sync_playwright

    stub = args.stub
    with tempfile.TemporaryDirectory(prefix="ra-turnstile-") as tmp:
        site = Path(tmp) / "site"
        build_site_dir(site)
        handler_cls = type(
            "H",
            (_Handler,),
            {"root": site, "rules": parse_headers((site / "_headers").read_text())},
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        urls: list[str] = []
        errors: list[str] = []
        local: dict[str, set[str]] = {}
        session: dict[str, set[str]] = {}
        idb: dict[str, set[str]] = {}
        state: str | None = None
        started = time.monotonic()
        try:
            with sync_playwright() as p:
                kind = getattr(p, args.browser)
                launch: dict[str, Any] = {}
                if args.browser == "chromium":
                    launch["args"] = ["--no-sandbox"]
                    exe = args.executable or os.environ.get("RA_CHROMIUM")
                    if exe:
                        launch["executable_path"] = exe
                elif args.executable:
                    launch["executable_path"] = args.executable
                browser = kind.launch(**launch)
                context = browser.new_context()  # empty: no profile, no storage
                context.on("request", lambda r: urls.append(r.url))
                if stub:

                    def answer(route: Route) -> None:
                        if route.request.url.split("?")[0].endswith("/api.js"):
                            route.fulfill(
                                status=200,
                                content_type="text/javascript",
                                body=STUB_SCRIPT,
                            )
                        else:
                            route.abort()

                    context.route(f"https://{TURNSTILE_HOST}/**", answer)
                page = context.new_page()
                page.on(
                    "console",
                    lambda m: (
                        errors.append(m.text[:300]) if m.type == "error" else None
                    ),
                )
                page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
                deadline = started + args.wait
                while time.monotonic() < deadline:
                    for frame in page.frames:
                        try:
                            got = frame.evaluate(_PROBE)
                        except Exception:  # noqa: S112 - a frame may be gone or opaque
                            continue
                        origin = urlsplit(frame.url).netloc or frame.url[:40]
                        local.setdefault(origin, set()).update(got["local"])
                        session.setdefault(origin, set()).update(got["session"])
                        idb.setdefault(origin, set()).update(got["idb"])
                    state = page.evaluate(
                        "window.reachAroundBeacon"
                        " ? window.reachAroundBeacon.state : null"
                    )
                    if state:
                        break
                    time.sleep(0.2)
                waited = time.monotonic() - started
                time.sleep(0.5)  # let late writes land before the final read
                saved = context.storage_state(indexed_db=True)
                for origin in saved.get("origins", []):
                    name = urlsplit(origin["origin"]).netloc
                    local.setdefault(name, set()).update(
                        item["name"] for item in origin.get("localStorage", [])
                    )
                    idb.setdefault(name, set()).update(
                        db.get("name", "") for db in origin.get("indexedDB", [])
                    )
                cookies = [
                    {
                        "name": c["name"],
                        "domain": c["domain"],
                        "path": c["path"],
                        "expires": "session" if c["expires"] == -1 else c["expires"],
                        "flags": ",".join(
                            f
                            for f, on in (
                                ("httpOnly", c["httpOnly"]),
                                ("secure", c["secure"]),
                                (f"sameSite={c['sameSite']}", True),
                            )
                            if on
                        ),
                    }
                    for c in context.cookies()
                ]
                context.close()
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    hosts: dict[str, int] = {}
    for url in urls:
        host = host_of(url)
        if host and urlsplit(url).scheme in ("http", "https", "ws", "wss"):
            hosts[host] = hosts.get(host, 0) + 1
    return {
        "mode": "stub" if stub else "live",
        "browser": args.browser,
        "state": state or "no result (timed out)",
        "waited": waited,
        "cookies": cookies,
        "local_storage": {k: sorted(v) for k, v in local.items()},
        "session_storage": {k: sorted(v) for k, v in session.items()},
        "indexed_db": {k: sorted(v) for k, v in idb.items()},
        "hosts": hosts,
        "foreign": foreign_hosts(urls),
        "console_errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--live", action="store_true", help="contact Cloudflare (test key)"
    )
    mode.add_argument("--stub", action="store_true", help="answer Turnstile locally")
    parser.add_argument(
        "--browser", choices=("chromium", "firefox"), default="chromium"
    )
    parser.add_argument("--executable", help="path to a browser binary")
    parser.add_argument("--wait", type=float, default=20.0, help="seconds to wait")
    parser.add_argument("--json", action="store_true", help="print JSON")
    parser.add_argument(
        "--strict", action="store_true", help="exit 1 if anything is stored"
    )
    args = parser.parse_args(argv)
    if not 1 <= args.wait <= 120:
        print("--wait must be from 1 to 120", file=sys.stderr)
        return 2
    try:
        report = measure(args)
    except ImportError:
        print("Playwright is not installed: pip install playwright", file=sys.stderr)
        return 2
    except Exception as err:  # noqa: BLE001 - one clear line for any setup failure
        print(f"measurement failed: {type(err).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render(report))
    if args.strict:
        other = [h for h in report["foreign"] if h != TURNSTILE_HOST]
        if storage_found(report) or other or report["state"] != "verified":
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
