# SPDX-License-Identifier: Apache-2.0
import json
import re
from datetime import date
from importlib import resources
from pathlib import Path

from assetpaths import asset_file
from reach_around.config import load_site_config
from reach_around.site.build import build_site, manifest_json

TODAY = date(2027, 3, 10)
ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "instance" / "site.yaml"

ANCHOR = re.compile(r"<a\b[^>]*>", re.IGNORECASE)
ATTR = re.compile(r'([a-zA-Z-]+)="([^"]*)"')


def build(out: Path) -> dict[str, str]:
    manifest = build_site(
        load_site_config(EXAMPLE), out, today=TODAY, assets_dir=EXAMPLE.parent
    )
    return manifest.links


def link_anchors(out: Path) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for page in sorted(out.rglob("*.html")):
        for tag in ANCHOR.findall(page.read_text(encoding="utf-8")):
            attrs = dict(ATTR.findall(tag))
            if "link" in attrs.get("class", "").split():
                found.append(attrs)
    return found


def test_every_link_block_has_ping_and_real_href(tmp_path: Path) -> None:
    links = build(tmp_path)
    anchors = link_anchors(tmp_path)
    assert len(anchors) == len(links) > 0
    for attrs in anchors:
        link_id = attrs["data-link-id"]
        assert attrs["ping"] == f"/e/c/{link_id}"
        assert attrs["href"] == links[link_id]


def test_click_js_is_the_only_script(tmp_path: Path) -> None:
    build(tmp_path)
    pages = sorted(tmp_path.rglob("*.html"))
    assert pages
    for page in pages:
        text = page.read_text(encoding="utf-8")
        scripts = re.findall(r"<script\b[^>]*>", text, re.IGNORECASE)
        if page.name == "404.html":
            # Never click-counted: no script at all (see test_not_found.py).
            assert scripts == []
            continue
        assert len(scripts) == 1
        attrs = dict(ATTR.findall(scripts[0]))
        assert re.search(r"static/click\.[0-9a-f]{10}\.js$", attrs["src"])
        assert "defer" in scripts[0]
        assert re.search(r"<script\b[^>]*>\s*</script>", text, re.IGNORECASE)
    assert asset_file(tmp_path, "static/click.js").is_file()


def test_click_js_contains_no_eval_no_document_write_and_no_inline_handlers() -> None:
    source = (resources.files("reach_around.site") / "static" / "click.js").read_text(
        encoding="utf-8"
    )
    assert "sendBeacon" in source  # the guard is reading the real file
    assert not re.search(r"\beval\s*\(", source)
    assert "new Function" not in source
    assert "document.write" not in source
    assert "innerHTML" not in source
    assert not re.search(r"\.setAttribute\(\s*['\"]on", source)
    assert not re.search(r"\bon[a-z]+\s*=\s*['\"]", source)


def test_inline_handler_guard_bites() -> None:
    bad = "el.setAttribute('onclick', 'x()'); eval('1'); document.write('x')"
    assert re.search(r"\beval\s*\(", bad)
    assert "document.write" in bad
    assert re.search(r"\.setAttribute\(\s*['\"]on", bad)


def test_pages_have_no_inline_handlers(tmp_path: Path) -> None:
    build(tmp_path)
    for page in sorted(tmp_path.rglob("*.html")):
        text = page.read_text(encoding="utf-8")
        assert not re.search(r"<[^>]+\son[a-z]+\s*=", text, re.IGNORECASE)


def test_ping_paths_use_only_ids_from_the_manifest(tmp_path: Path) -> None:
    manifest = build_site(
        load_site_config(EXAMPLE), tmp_path, today=TODAY, assets_dir=EXAMPLE.parent
    )
    ids = set(json.loads(manifest_json(manifest))["links"])
    pings: list[str] = []
    for page in sorted(tmp_path.rglob("*.html")):
        pings += re.findall(r'\bping="([^"]*)"', page.read_text(encoding="utf-8"))
    assert pings
    for ping in pings:
        match = re.fullmatch(r"/e/c/([a-z0-9-]+)", ping)
        assert match and match.group(1) in ids
