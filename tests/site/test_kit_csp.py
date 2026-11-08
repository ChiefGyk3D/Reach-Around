# SPDX-License-Identifier: Apache-2.0
"""The gate page CSP relaxation (#257): Turnstile origin, gate paths only.

Everything is synthetic. The pages that render the Turnstile widget (the kit
page and the retry page, when the kit has a gate form and a site key) get the
Turnstile origin in ``script-src`` and ``frame-src``. Every other path keeps the
site-wide strict policy byte for byte.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from test_kit_build import build, gated_doc, kit_doc, make_config

from reach_around.config import SiteConfig
from reach_around.site.kit import GATE_PATHS, TURNSTILE_ORIGIN
from reach_around.site.overrides import build_csp

ORIGIN = "https://challenges.cloudflare.com"


def header_blocks(out: Path) -> dict[str, dict[str, str]]:
    blocks: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in (out / "_headers").read_text(encoding="utf-8").splitlines():
        if not line:
            current = None
        elif not line.startswith(" "):
            current = blocks.setdefault(line, {})
        elif current is not None and ":" in line:
            name, _, value = line.strip().partition(":")
            if not name.startswith("!"):
                current[name] = value.strip()
    return blocks


def directives(csp: str) -> dict[str, str]:
    return dict(d.strip().split(" ", 1) for d in csp.split(";"))


def test_the_origin_and_paths_are_exactly_these() -> None:
    assert TURNSTILE_ORIGIN == ORIGIN
    assert GATE_PATHS == ("/kit/", "/kit/retry/")


def test_gate_paths_have_exactly_the_turnstile_origin(tmp_path: Path) -> None:
    out, _ = build(tmp_path)
    blocks = header_blocks(out)
    for path in GATE_PATHS:
        csp = blocks[path]["Content-Security-Policy"]
        assert csp == build_csp({"script-src": [ORIGIN], "frame-src": [ORIGIN]})
        d = directives(csp)
        assert d["script-src"] == f"'self' {ORIGIN}"
        assert d["frame-src"] == ORIGIN
        # nothing else widened
        assert d["connect-src"] == "'self'"
        assert d["default-src"] == "'self'"
        assert d["style-src"] == "'self'"
        assert d["frame-ancestors"] == "'none'"
        assert d["form-action"] == "'self'"
        assert d["object-src"] == "'none'"
        assert csp.count(ORIGIN) == 2


def test_no_other_path_gets_the_origin(tmp_path: Path) -> None:
    out, _ = build(tmp_path)
    blocks = header_blocks(out)
    strict = blocks["/*"]["Content-Security-Policy"]
    assert strict == build_csp()
    assert ORIGIN not in strict
    carrying = sorted(
        path
        for path, h in blocks.items()
        if ORIGIN in h.get("Content-Security-Policy", "")
    )
    assert carrying == sorted(GATE_PATHS)
    # the raw file names the origin in exactly two rule blocks, two times each
    assert (out / "_headers").read_text(encoding="utf-8").count(ORIGIN) == 4


def test_public_kit_without_a_gate_stays_strict(tmp_path: Path) -> None:
    plain = make_config(kit_doc(), features={})
    out, _ = build(tmp_path, plain, gated=False)
    text = (out / "_headers").read_text(encoding="utf-8")
    assert "challenges.cloudflare.com" not in text
    assert header_blocks(out)["/*"]["Content-Security-Policy"] == build_csp()
    assert [
        path
        for path, h in header_blocks(out).items()
        if "Content-Security-Policy" in h and path != "/*"
    ] == []


def test_no_site_key_means_no_widget_and_no_relaxation(tmp_path: Path) -> None:
    doc = gated_doc()
    del doc["turnstile_site_key"]
    out, _ = build(tmp_path, make_config(doc))
    assert "challenges.cloudflare.com" not in (out / "_headers").read_text("utf-8")


def test_a_site_without_a_kit_has_no_relaxation(tmp_path: Path) -> None:
    from reach_around.site.build import build_site

    config = SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "Example Creator"},
            "tabs": [
                {
                    "id": "main",
                    "title": "Main",
                    "blocks": [{"id": "o", "title": "O", "url": "https://e.com/"}],
                }
            ],
        }
    )
    out = tmp_path / "o"
    build_site(config, out, today=date(2027, 3, 10))
    assert "challenges.cloudflare.com" not in (out / "_headers").read_text("utf-8")


def test_the_relaxed_policy_has_no_unsafe_source(tmp_path: Path) -> None:
    out, _ = build(tmp_path)
    for path in GATE_PATHS:
        csp = header_blocks(out)[path]["Content-Security-Policy"]
        for bad in ("unsafe-inline", "unsafe-eval", "*", "http:", "data:", "blob:"):
            assert bad not in csp.replace("img-src 'self' https:", "")


def test_the_gated_page_and_public_pages_load_no_script_yet(tmp_path: Path) -> None:
    # The CSP is only the allowance. Loading the script is a separate change.
    out, gated = build(tmp_path)
    for page in (out / "kit" / "index.html", gated / "kit.html"):
        assert "challenges.cloudflare.com" not in page.read_text(encoding="utf-8")
