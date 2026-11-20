# SPDX-License-Identifier: Apache-2.0
"""The hidden same-origin helper page for the invisible bot check (#610).

Off by default. On, the build adds one static page, ``/e-frame/``, that loads the
Turnstile script and is the only page whose policy allows the Turnstile origin.
Every other page keeps the strict policy, byte for byte.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from assetpaths import asset, read
from reach_around.config import SiteConfig
from reach_around.site.build import build_site
from reach_around.site.overrides import OverridesError, build_csp, render_headers

TODAY = date(2027, 3, 10)
KEY = "1x00000000000000000000AA"  # Cloudflare's published always-pass test key
ORIGIN = "https://challenges.cloudflare.com"
STATIC = resources.files("reach_around.site") / "static"
LOGIC = Path(__file__).with_name("beacon_logic.mjs")
ON: dict[str, Any] = {"turnstile_beacon": True, "turnstile_site_key": KEY}

# The helper page's policy, spelled out in full on purpose: a change to it must
# show up in a reviewed diff of this test.
HELPER_CSP = (
    "default-src 'self'; "
    f"script-src 'self' {ORIGIN}; "
    "style-src 'self'; "
    "img-src 'self' https:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'self'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    f"frame-src {ORIGIN}"
)
STRICT_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' https:; font-src 'self'; connect-src 'self'; "
    "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'self'; require-trusted-types-for 'script'"
)


def make_config(analytics: dict[str, Any] | None = None, **extra: Any) -> SiteConfig:
    data: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": [
            {
                "id": "start",
                "title": "Start",
                "blocks": [
                    {"id": "one", "title": "One", "url": "https://example.com/1"}
                ],
            },
            {
                "id": "more",
                "title": "More",
                "blocks": [
                    {"id": "two", "title": "Two", "url": "https://example.com/2"}
                ],
            },
        ],
    }
    if analytics is not None:
        data["analytics"] = analytics
    data.update(extra)
    return SiteConfig.model_validate(data)


def build(tmp: Path, analytics: dict[str, Any] | None = None, **extra: Any) -> Path:
    build_site(make_config(analytics, **extra), tmp, today=TODAY)
    return tmp


def blocks(headers: str) -> dict[str, list[str]]:
    """``_headers`` as {path: [lines]}."""
    found: dict[str, list[str]] = {}
    for chunk in headers.strip().split("\n\n"):
        lines = chunk.split("\n")
        found.setdefault(lines[0], []).extend(line.strip() for line in lines[1:])
    return found


def csp_of(headers: str, path: str) -> str:
    prefix = "Content-Security-Policy: "
    rows = [x for x in blocks(headers)[path] if x.startswith(prefix)]
    assert len(rows) == 1
    return rows[0][len(prefix) :]


# --- config ------------------------------------------------------------------


def test_off_by_default_with_no_key() -> None:
    config = make_config()
    assert config.analytics.turnstile_beacon is False
    assert config.analytics.turnstile_site_key is None


def test_on_with_a_valid_key_is_accepted() -> None:
    config = make_config(ON)
    assert config.analytics.turnstile_beacon is True
    assert config.analytics.turnstile_site_key == KEY


@pytest.mark.parametrize(
    "key",
    [
        None,
        "",
        "short",
        "has space in it 1234567890",
        "a" * 65,
        "key/with/slash-1234567890",
        "key\n1234567890123456",
        "https://challenges.cloudflare.com",
        "ключ-ключ-ключ-ключ-ключ",
        "key;1234567890123456",
    ],
)
def test_on_needs_a_plain_key(key: str | None) -> None:
    with pytest.raises(ValidationError):
        make_config({"turnstile_beacon": True, "turnstile_site_key": key})


def test_the_key_is_refused_while_the_flag_is_off() -> None:
    with pytest.raises(ValidationError, match="turnstile_beacon"):
        make_config({"turnstile_site_key": KEY})
    with pytest.raises(ValidationError, match="turnstile_beacon"):
        make_config({"turnstile_beacon": False, "turnstile_site_key": KEY})


@pytest.mark.parametrize("flag", ["true", 1, "yes", None])
def test_the_flag_is_a_strict_boolean(flag: Any) -> None:
    with pytest.raises(ValidationError):
        make_config({"turnstile_beacon": flag, "turnstile_site_key": KEY})


def test_a_non_string_key_is_refused() -> None:
    with pytest.raises(ValidationError):
        make_config({"turnstile_beacon": True, "turnstile_site_key": 12345678901234567})


def test_on_needs_something_to_protect() -> None:
    with pytest.raises(ValidationError, match="click_tracking"):
        make_config(ON, features={"click_tracking": False})
    # Either beacon is enough.
    make_config({**ON, "page_views": True}, features={"click_tracking": False})
    make_config(ON)


# --- build output, flag off ----------------------------------------------------


def test_off_output_is_identical_to_the_default(tmp_path: Path) -> None:
    default = build(tmp_path / "a")
    explicit = build(tmp_path / "b", {"turnstile_beacon": False})
    files = [p for p in sorted(default.rglob("*")) if p.is_file()]
    assert files
    assert sorted(p.relative_to(default) for p in files) == sorted(
        p.relative_to(explicit) for p in explicit.rglob("*") if p.is_file()
    )
    for path in files:
        assert path.read_bytes() == (explicit / path.relative_to(default)).read_bytes()


def test_off_has_no_page_no_script_no_header_no_reference(tmp_path: Path) -> None:
    out = build(tmp_path, {"page_views": True})
    assert not (out / "e-frame").exists()
    assert not list((out / "static").glob("beacon*"))
    headers = (out / "_headers").read_text()
    assert "e-frame" not in headers
    assert ORIGIN not in headers
    for page in out.rglob("*.html"):
        text = page.read_text()
        assert "e-frame" not in text and "beacon" not in text.lower()
        assert ORIGIN not in text


# --- build output, flag on -----------------------------------------------------


def test_on_writes_the_helper_page_and_both_scripts(tmp_path: Path) -> None:
    out = build(tmp_path, ON)
    page = out / "e-frame" / "index.html"
    assert page.is_file()
    assert read(out, "static/beacon.js") == (STATIC / "beacon.js").read_text()
    assert (
        read(out, "static/beacon-frame.js") == (STATIC / "beacon-frame.js").read_text()
    )


def test_helper_page_content(tmp_path: Path) -> None:
    out = build(tmp_path, ON)
    html = (out / "e-frame" / "index.html").read_text()
    frame_js = asset(out, "static/beacon-frame.js")
    assert '<meta name="robots" content="noindex, nofollow">' in html
    assert f'src="/{frame_js}"' in html
    assert f'src="{ORIGIN}/turnstile/v0/api.js?render=explicit"' in html
    assert f'data-sitekey="{KEY}"' in html
    assert 'data-action="beacon"' in html
    scripts = re.findall(r"<script\b[^>]*>", html)
    assert len(scripts) == 2
    for tag in scripts:
        assert " src=" in tag and " defer" in tag
    assert "<script" not in re.sub(r"<script[^>]*></script>", "", html)
    assert not re.search(r"\son[a-z]+=", html, re.IGNORECASE)
    assert not re.search(r"\sstyle=", html, re.IGNORECASE)
    assert "<a " not in html and "<link" not in html
    # The Turnstile script comes first, ours second (both deferred, in order).
    assert html.index("api.js") < html.index(frame_js)


def test_helper_path_has_its_own_narrow_policy(tmp_path: Path) -> None:
    headers = (build(tmp_path, ON) / "_headers").read_text()
    assert csp_of(headers, "/e-frame/") == HELPER_CSP
    # Detached from the site-wide policy first, so the browser gets one policy.
    assert "! Content-Security-Policy" in blocks(headers)["/e-frame/"]


def test_every_other_path_keeps_the_strict_policy(tmp_path: Path) -> None:
    on = (build(tmp_path / "on", ON) / "_headers").read_text()
    off = (build(tmp_path / "off") / "_headers").read_text()
    assert csp_of(on, "/*") == STRICT_CSP == build_csp()
    assert csp_of(off, "/*") == STRICT_CSP
    for path, rows in blocks(on).items():
        if path != "/e-frame/":
            assert not any(ORIGIN in row for row in rows), path
    assert on.count(ORIGIN) == 2  # script-src and frame-src of the one helper path
    assert on.count("frame-ancestors 'self'") == 1
    # No ordinary page needs a new directive to embed the helper: frame-src falls
    # back to default-src 'self'.
    assert "frame-src" not in csp_of(on, "/*")
    assert "default-src 'self'" in csp_of(on, "/*")
    # The ordinary-path part of the file does not change when the flag turns on.
    assert on.split("\n\n/e-frame/")[0].startswith(off.split("\n\n")[0])


def test_helper_path_is_not_indexed(tmp_path: Path) -> None:
    out = build(tmp_path, ON)
    rules = blocks((out / "_headers").read_text())
    assert "X-Robots-Tag: noindex" in rules["/e-frame"]
    assert "X-Robots-Tag: noindex" in rules["/e-frame/*"]


def test_helper_page_is_not_linked_from_any_other_page(tmp_path: Path) -> None:
    out = build(tmp_path, {**ON, "page_views": True})
    seen = 0
    for page in out.rglob("*.html"):
        if page.parent.name == "e-frame":
            continue
        seen += 1
        text = page.read_text()
        assert "e-frame" not in text
        assert ORIGIN not in text
        assert "<iframe" not in text
    assert seen >= 3
    for sitemap in out.rglob("sitemap*"):
        assert "e-frame" not in sitemap.read_text()


def test_ordinary_tab_pages_load_only_the_same_origin_script(tmp_path: Path) -> None:
    out = build(tmp_path, ON)
    hashed = asset(out, "static/beacon.js")
    home = (out / "index.html").read_text()
    more = (out / "more" / "index.html").read_text()
    assert f'<script src="{hashed}" defer></script>' in home
    assert f'<script src="../{hashed}" defer></script>' in more
    for page in (out / "index.html", out / "more" / "index.html"):
        for tag in re.findall(r"<script\b[^>]*>", page.read_text()):
            assert " src=" in tag and "https:" not in tag and "//" not in tag


def test_pages_that_count_nothing_do_not_load_it(tmp_path: Path) -> None:
    out = build(tmp_path, ON)
    for rel in ("privacy/index.html", "404.html"):
        text = (out / rel).read_text()
        assert "beacon" not in text
    page = out / "e-frame" / "index.html"
    assert asset(out, "static/beacon.js") not in page.read_text()


def test_a_tab_may_not_take_the_helper_path(tmp_path: Path) -> None:
    data = make_config(ON).model_dump(mode="json", exclude_none=True)
    data["tabs"][1]["id"] = "e-frame"
    config = SiteConfig.model_validate(data)
    with pytest.raises(ValueError, match="e-frame"):
        build_site(config, tmp_path, today=TODAY)


def test_the_helper_path_is_not_blocked_by_noindex_config(tmp_path: Path) -> None:
    out = build(tmp_path, ON, robots={"noindex_paths": ["/other/"]})
    rules = blocks((out / "_headers").read_text())
    assert "/e-frame" in rules and "/other" in rules


# --- the policy builder --------------------------------------------------------


def test_frame_ancestors_can_only_be_self() -> None:
    assert "frame-ancestors 'self'" in build_csp({"frame-ancestors": ["'self'"]})
    for bad in (
        ["'none'"],
        ["*"],
        ["https://example.com"],
        ["'self'", "https://x.com"],
    ):
        with pytest.raises(OverridesError):
            build_csp({"frame-ancestors": bad})
    assert "frame-ancestors 'none'" in build_csp()


def test_header_path_is_still_validated() -> None:
    with pytest.raises(OverridesError):
        render_headers(path_extras={"/*": {"frame-ancestors": ["'self'"]}})


# --- the scripts, under node ---------------------------------------------------


def node_or_none() -> str | None:
    found = shutil.which("node")
    if found is None and os.environ.get("CI"):
        pytest.fail("node is required in CI for the beacon script tests")
    return found


def stage(target: Path, mutate: dict[str, tuple[str, str]] | None = None) -> Path:
    """The shipped scripts (optionally mutated) laid out as the tests expect."""
    scripts = target / "src" / "reach_around" / "site" / "static"
    scripts.mkdir(parents=True)
    for name in ("beacon.js", "beacon-frame.js"):
        text = (STATIC / name).read_text(encoding="utf-8")
        if mutate and name in mutate:
            old, new = mutate[name]
            assert old in text, f"mutation target missing from {name}: {old!r}"
            text = text.replace(old, new)
        (scripts / name).write_text(text, encoding="utf-8")
    where = target / "tests" / "site"
    where.mkdir(parents=True)
    shutil.copyfile(LOGIC, where / "beacon_logic.mjs")
    return where / "beacon_logic.mjs"


def run_node(node: str, script: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv: node and a staged copy of our tests
        [node, "--test", "--test-reporter=spec", str(script)],
        cwd=script.parent,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={"PATH": os.environ.get("PATH", ""), "NODE_OPTIONS": ""},
    )


def test_the_beacon_logic_passes_under_node(tmp_path: Path) -> None:
    node = node_or_none()
    if node is None:
        pytest.skip("node is not installed; the browser-side logic was not run")
    done = run_node(node, stage(tmp_path))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "fail 0" in done.stdout
    assert "Global Privacy Control loads nothing" in done.stdout  # the table ran


@pytest.mark.parametrize(
    ("name", "old", "new"),
    [
        ("beacon.js", "event.origin !== location.origin", "false"),
        ("beacon.js", "event.source !== frame.contentWindow", "false"),
        ("beacon.js", "allow-scripts allow-same-origin", "allow-scripts"),
        ("beacon.js", "navigator.globalPrivacyControl === true", "false"),
        ("beacon.js", "dntOn(navigator.doNotTrack)", "false"),
        ("beacon.js", "TIMEOUT_MS = 5000", "TIMEOUT_MS = 500000"),
        ("beacon-frame.js", "location.origin", '"*"'),
        ("beacon-frame.js", 'retry: "never"', 'retry: "auto"'),
    ],
)
def test_the_node_tests_bite(tmp_path: Path, name: str, old: str, new: str) -> None:
    node = node_or_none()
    if node is None:
        pytest.skip("node is not installed; the browser-side logic was not run")
    done = run_node(node, stage(tmp_path, {name: (old, new)}))
    assert done.returncode != 0, f"mutation {old!r} -> {new!r} went unnoticed"


# --- source scan ---------------------------------------------------------------

BANNED = ("localStorage", "sessionStorage", "indexedDB", "document.cookie", "fetch(")


def test_scripts_use_no_storage_and_do_not_touch_links() -> None:
    for name in ("beacon.js", "beacon-frame.js"):
        text = (STATIC / name).read_text(encoding="utf-8")
        assert len(text) > 400  # loose: the file was read
        for word in BANNED:
            assert word not in text, (name, word)
        assert "preventDefault" not in text
