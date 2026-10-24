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
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

from jinja2 import Environment

from reach_around.config.blocks import Crypto, Email, Platforms
from reach_around.config.models import Block, Link, Section, SiteConfig, Tab
from reach_around.config.numbers import Numbers
from reach_around.config.footer import CREDIT_URL
from reach_around.config.page_title import DEFAULT_TEMPLATE, render_template
from reach_around.config.profile_card import MEDIA_KIT_ID
from reach_around.config.tablinks import is_tab_ref, parse_tab_ref
from reach_around.site.assets import collect_assets
from reach_around.site.overrides import (
    load_custom_css,
    make_environment,
    render_headers,
    warn_about_javascript,
)
from reach_around.site.platforms import platform_rows
from reach_around.site.privacy import collected_items, intro_text, sets_cookies
from reach_around.site.robots import render_robots
from reach_around.site.theme import theme_css
from reach_around.site.visibility import visible_blocks


@dataclass(frozen=True)
class BuildManifest:
    """What the build published: visible off-site link id -> destination URL.

    Internal ``tab:`` links are not listed: they are not click-counted.
    """

    links: dict[str, str]


def _links_in(blocks: Iterable[Block]) -> list[Link]:
    links: list[Link] = []
    for block in blocks:
        if isinstance(block, Link):
            links.append(block)
        elif isinstance(block, Section):
            links.extend(block.links)
    return links


def _has_crypto(blocks: Iterable[Block]) -> bool:
    return any(isinstance(block, Crypto) for block in blocks)


def _has_email(blocks: Iterable[Block]) -> bool:
    return any(isinstance(block, Email) for block in blocks)


def _has_email_script(blocks: Iterable[Block]) -> bool:
    """An email block needs the script unless it opted out of it."""
    return any(
        isinstance(block, Email) and block.reveal != "never_js" for block in blocks
    )


def _has_platforms(blocks: Iterable[Block]) -> bool:
    return any(isinstance(block, Platforms) for block in blocks)


# Published only for a site with a crypto block on a page; see ``build_site``.
_CRYPTO_FILES = (Path("static") / "copy.js", Path("static") / "crypto.css")
_PLATFORMS_FILES = (Path("static") / "platforms.css",)


# Published only for a site with an email block; the script only if one uses it.
_EMAIL_STYLE = Path("static") / "email.css"
_EMAIL_SCRIPT = Path("static") / "email.js"


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


def _internal_href(config: SiteConfig, root: str) -> Callable[[str], str]:
    """Resolve ``tab:<id>[#fragment]`` to the tab's path as seen from a page.

    ``root`` is what the nav uses for the first tab ("./" or "../"); the other
    tabs live at ``<prefix><id>/``. The config was validated at load, so every
    id is known.
    """
    prefix = "" if root == "./" else root
    homes = {
        tab.id: (root if i == 0 else f"{prefix}{tab.id}/")
        for i, tab in enumerate(config.tabs)
    }

    def href(url: str) -> str:
        tab_id, fragment = parse_tab_ref(url)
        return homes[tab_id] if fragment is None else f"{homes[tab_id]}#{fragment}"

    return href


def page_title(
    config: SiteConfig, tab_name: str, *, home: bool, own: str | None
) -> str:
    """Text for <title>: per-tab override, else the template, else the defaults."""
    if own is not None:
        return own
    creator = config.creator
    template = config.title_template
    if template is None:
        if creator.title is None:
            template = DEFAULT_TEMPLATE
        elif home:
            return creator.title
        else:
            template = "{tab} - {site}"
    return render_template(
        template,
        tab=tab_name,
        creator=creator.name,
        site=creator.title or creator.name,
    )


def _profile_card(config: SiteConfig) -> dict[str, Any] | None:
    card = config.profile_card
    if card is None:
        return None
    return {
        "title": card.title or config.creator.name,
        "tags": card.tags,
        "button": card.button,
        "button_id": MEDIA_KIT_ID,
    }


def _render_page(
    env: Environment,
    config: SiteConfig,
    index: int,
    blocks: Sequence[Block],
    custom_css: bool,
) -> str:
    tab = config.tabs[index]
    root = "" if index == 0 else "../"
    has_affiliate = any(link.affiliate for link in _links_in(blocks))
    return env.get_template("tab.html").render(
        creator=config.creator,
        tab=tab,
        blocks=blocks,
        nav=_nav(config, index, root or "./"),
        tab_href=_internal_href(config, root or "./"),
        root=root,
        page_title=page_title(config, tab.title, home=index == 0, own=tab.page_title),
        disclosure=config.disclosure,
        show_disclosure=has_affiliate and bool(config.disclosure),
        show_cookie_banner=sets_cookies(config),
        click_tracking=config.features.click_tracking,
        footer_credit=config.footer.credit,
        footer_credit_text=config.footer.text,
        footer_credit_url=CREDIT_URL,
        has_crypto=_has_crypto(blocks),
        has_email=_has_email(blocks),
        has_email_script=_has_email_script(blocks),
        has_platforms=_has_platforms(blocks),
        profile_card=_profile_card(config),
        cookie_text=intro_text(config),
        custom_css=custom_css,
    )


def _render_privacy(env: Environment, config: SiteConfig, custom_css: bool) -> str:
    return env.get_template("privacy.html").render(
        creator=config.creator,
        nav=_nav(config, -1, "../"),
        tab_href=_internal_href(config, "../"),
        root="../",
        page_title=page_title(config, "Privacy", home=False, own=None),
        disclosure=config.disclosure,
        show_disclosure=False,
        show_cookie_banner=False,
        click_tracking=config.features.click_tracking,
        footer_credit=config.footer.credit,
        footer_credit_text=config.footer.text,
        footer_credit_url=CREDIT_URL,
        has_crypto=False,
        has_email=False,
        has_email_script=False,
        has_platforms=False,
        profile_card=_profile_card(config),
        intro=intro_text(config),
        items=collected_items(config),
        contact=config.privacy.contact,
        custom_css=custom_css,
    )


def _render_not_found(env: Environment, config: SiteConfig, custom_css: bool) -> str:
    """The 404 page.

    Hosts serve it at arbitrary depths (``/a/b/c``), so unlike every other page
    its asset and nav links are root-relative (``root="/"``). It never loads
    click.js and carries ``noindex``.
    """
    return env.get_template("404.html").render(
        creator=config.creator,
        nav=_nav(config, -1, "/"),
        tab_href=_internal_href(config, "/"),
        root="/",
        page_title=page_title(config, "Not found", home=False, own=None),
        disclosure=config.disclosure,
        show_disclosure=False,
        show_cookie_banner=sets_cookies(config),
        click_tracking=False,
        has_crypto=False,
        has_email=False,
        has_email_script=False,
        has_platforms=False,
        noindex=True,
        profile_card=_profile_card(config),
        cookie_text=intro_text(config),
        custom_css=custom_css,
    )


def _render_pages(
    env: Environment,
    config: SiteConfig,
    shown: list[list[Block]],
    custom_css: bool,
) -> dict[Path, str]:
    pages = {
        _page_path(i, tab): _render_page(env, config, i, shown[i], custom_css)
        for i, tab in enumerate(config.tabs)
    }
    pages[Path("privacy") / "index.html"] = _render_privacy(env, config, custom_css)
    pages[Path("404.html")] = _render_not_found(env, config, custom_css)
    return pages


def _manifest(config: SiteConfig, shown: list[list[Block]]) -> BuildManifest:
    links = {
        link.id: link.url
        for blocks in shown
        for link in _links_in(blocks)
        if not is_tab_ref(link.url)
    }
    for blocks in shown:
        for block in blocks:
            if isinstance(block, Platforms):
                links.update(
                    (ident, entry.url)
                    for ident, entry in zip(block.item_ids, block.items, strict=True)
                    if not is_tab_ref(entry.url)
                )
    card = config.profile_card
    if card is not None and card.button is not None:
        links[MEDIA_KIT_ID] = card.button.url
    return BuildManifest(links=links)


def manifest_json(manifest: BuildManifest) -> str:
    """The manifest file text. It is never written into the published site."""
    return json.dumps({"links": manifest.links}, sort_keys=True, indent=2) + "\n"


# Packaged subfolders of static/ and the file endings each one publishes.
_STATIC_FOLDERS: dict[str, tuple[str, ...]] = {
    "icons": (".svg",),
    "fonts": (".woff2", "OFL.txt"),
}


def _static_files() -> dict[Path, bytes]:
    root = resources.files("reach_around.site") / "static"
    files: dict[Path, bytes] = {}
    for item in sorted(root.iterdir(), key=lambda i: i.name):
        if item.is_file():
            files[Path("static") / item.name] = item.read_bytes()
        elif item.name in _STATIC_FOLDERS:
            wanted = _STATIC_FOLDERS[item.name]
            for child in sorted(item.iterdir(), key=lambda i: i.name):
                if child.is_file() and child.name.endswith(wanted):
                    files[Path("static") / item.name / child.name] = child.read_bytes()
    return files


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
    overrides: Path | None = None,
    assets_dir: Path | None = None,
    numbers: Numbers | None = None,
) -> BuildManifest:
    """Write the site for ``today`` into ``out_dir`` and return the manifest.

    The manifest is returned, never written into ``out_dir``: the caller
    decides where it goes (see ``manifest_json``).

    Everything already in ``out_dir`` is deleted first, so a removed tab never
    leaves a stale page behind. ``protect`` lists paths (the config file) that
    ``out_dir`` must not be or contain. ``overrides`` is an optional directory
    with ``templates/`` and ``custom.css`` (see ``reach_around.site.overrides``).
    ``assets_dir`` is the instance directory: the files the config names (avatar,
    favicon, theme images, font files, image blocks) are copied from it to the
    same relative path in ``out_dir``. It is required when the config names any.
    ``numbers`` is the loaded numbers file; its counts fill platform blocks and
    only the resulting text is published, never the file.
    """
    shown = [list(visible_blocks(tab.blocks, today)) for tab in config.tabs]
    manifest = _manifest(config, shown)
    env = make_environment(overrides)
    env.globals["platform_rows"] = lambda block: platform_rows(block, numbers)
    custom_css = load_custom_css(overrides)
    warn_about_javascript(overrides)
    files: dict[Path, bytes] = {
        page: html.encode("utf-8")
        for page, html in _render_pages(
            env, config, shown, custom_css is not None
        ).items()
    }
    files.update(_static_files())
    for rel, data in collect_assets(config, assets_dir).items():
        if rel in files:
            raise ValueError(f"{rel} would overwrite a file the build writes itself")
        files[rel] = data
    if not config.features.click_tracking:
        files.pop(Path("static") / "click.js", None)
    if not any(_has_crypto(blocks) for blocks in shown):
        for rel in _CRYPTO_FILES:
            files.pop(rel, None)
    if not any(_has_email(blocks) for blocks in shown):
        files.pop(_EMAIL_STYLE, None)
    if not any(_has_email_script(blocks) for blocks in shown):
        files.pop(_EMAIL_SCRIPT, None)
    if not any(_has_platforms(blocks) for blocks in shown):
        for rel in _PLATFORMS_FILES:
            files.pop(rel, None)
    files[Path("static") / "theme.css"] = theme_css(config.theme).encode("utf-8")
    if custom_css is not None:
        files[Path("static") / "custom.css"] = custom_css.encode("utf-8")
    robots = config.robots
    files[Path("_headers")] = render_headers(
        noindex_paths=robots.noindex_paths if robots else ()
    ).encode("utf-8")
    if robots is not None:
        if Path("robots.txt") in files:
            raise ValueError(
                "robots.txt would overwrite a file the build writes itself"
            )
        files[Path("robots.txt")] = render_robots(robots).encode("ascii")
    out = _check_out_dir(out_dir, protect)
    _empty(out)
    _write(out, files)
    return manifest
