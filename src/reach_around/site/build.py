# SPDX-License-Identifier: Apache-2.0
"""Render the static site and the build manifest.

``build_site`` is a short pipeline of small steps: choose what is visible,
render every page in memory, check the output directory is safe to empty, then
empty it and write the files. Rendering first means a template error never
leaves a half-deleted output directory.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, StrictUndefined

from reach_around.config.models import Block, Link, Section, SiteConfig, Tab
from reach_around.site.markdown import render_markdown
from reach_around.site.theme import theme_css
from reach_around.site.visibility import visible_blocks


@dataclass(frozen=True)
class BuildManifest:
    """What the build published: visible link id -> destination URL."""

    links: dict[str, str]


def _environment() -> Environment:
    env = Environment(
        loader=PackageLoader("reach_around.site", "templates"),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.globals["block_kind"] = _block_kind
    # render_markdown returns Markup that is already escaped, so autoescape
    # stays on in the template.
    env.filters["markdown"] = render_markdown
    return env


def _block_kind(block: Block) -> str:
    """Name the partial under ``templates/blocks/`` that renders ``block``."""
    return type(block).__name__.lower()


def _links_in(blocks: Iterable[Block]) -> list[Link]:
    links: list[Link] = []
    for block in blocks:
        if isinstance(block, Link):
            links.append(block)
        elif isinstance(block, Section):
            links.extend(block.links)
    return links


def _page_path(tab_index: int, tab: Tab) -> Path:
    if tab_index == 0:
        return Path("index.html")
    return Path(tab.id) / "index.html"


def _nav(config: SiteConfig, current: int, root: str) -> list[dict[str, Any]]:
    return [
        {
            "title": tab.title,
            "href": root if i == 0 else f"{root}{tab.id}/",
            "current": i == current,
        }
        for i, tab in enumerate(config.tabs)
    ]


def _render_page(
    env: Environment,
    config: SiteConfig,
    index: int,
    blocks: Sequence[Block],
) -> str:
    tab = config.tabs[index]
    root = "" if index == 0 else "../"
    has_affiliate = any(link.affiliate for link in _links_in(blocks))
    return env.get_template("tab.html").render(
        creator=config.creator,
        tab=tab,
        blocks=blocks,
        nav=_nav(config, index, root or "./"),
        root=root,
        page_title=f"{tab.title} - {config.creator.name}",
        disclosure=config.disclosure,
        show_disclosure=has_affiliate and bool(config.disclosure),
    )


def _render_pages(config: SiteConfig, shown: list[list[Block]]) -> dict[Path, str]:
    env = _environment()
    return {
        _page_path(i, tab): _render_page(env, config, i, shown[i])
        for i, tab in enumerate(config.tabs)
    }


def _manifest(shown: list[list[Block]]) -> BuildManifest:
    return BuildManifest(
        links={link.id: link.url for blocks in shown for link in _links_in(blocks)}
    )


def _manifest_json(manifest: BuildManifest) -> str:
    return json.dumps({"links": manifest.links}, sort_keys=True, indent=2) + "\n"


def _static_files() -> dict[Path, bytes]:
    root = resources.files("reach_around.site") / "static"
    return {
        Path("static") / item.name: item.read_bytes()
        for item in sorted(root.iterdir(), key=lambda i: i.name)
        if item.is_file()
    }


def _check_out_dir(out_dir: Path, protect: Iterable[Path]) -> Path:
    """Return the resolved ``out_dir``, or raise if emptying it is unsafe."""
    out = out_dir.resolve()
    if out.exists() and not out.is_dir():
        raise ValueError(f"output path {out_dir} is not a directory")
    for path in protect:
        resolved = path.resolve()
        if out == resolved or out in resolved.parents:
            raise ValueError(
                f"output directory {out_dir} is the config directory or a parent "
                f"of the config file {path}; choose a separate directory"
            )
    if out == Path(out.anchor) or out in Path.cwd().resolve().parents:
        raise ValueError(
            f"output directory {out_dir} is the working directory or a parent of it"
        )
    return out


def _empty(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for child in directory.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def _write(out: Path, files: dict[Path, bytes]) -> None:
    for rel, data in sorted(files.items()):
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def build_site(
    config: SiteConfig,
    out_dir: Path,
    *,
    today: date,
    protect: Iterable[Path] = (),
) -> BuildManifest:
    """Write the site for ``today`` into ``out_dir`` and return the manifest.

    Everything already in ``out_dir`` is deleted first, so a removed tab never
    leaves a stale page behind. ``protect`` lists paths (the config file) that
    ``out_dir`` must not be or contain.
    """
    shown = [list(visible_blocks(tab.blocks, today)) for tab in config.tabs]
    manifest = _manifest(shown)
    files: dict[Path, bytes] = {
        page: html.encode("utf-8")
        for page, html in _render_pages(config, shown).items()
    }
    files.update(_static_files())
    files[Path("static") / "theme.css"] = theme_css(config.theme).encode("utf-8")
    files[Path("manifest.json")] = _manifest_json(manifest).encode("utf-8")
    out = _check_out_dir(out_dir, protect)
    _empty(out)
    _write(out, files)
    return manifest
