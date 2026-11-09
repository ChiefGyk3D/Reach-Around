# SPDX-License-Identifier: Apache-2.0
"""Render the static site and the build manifest.

``build_site`` is a short pipeline of small steps: choose what is visible,
gather the static and instance files and give each a content-hashed name (see
``fingerprint``), render every page in memory against those names, check the
output directory is safe to empty, then empty it and write the files. Rendering
first means a template error never leaves a half-deleted output directory.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

from jinja2 import Environment

from reach_around.config.blocks import (
    LIVE_PLAYER_ORIGINS,
    Crypto,
    Email,
    Live,
    Platforms,
)
from reach_around.config.footer import CREDIT_URL
from reach_around.config.models import Block, Link, Section, SiteConfig, Tab
from reach_around.config.numbers import (
    Numbers,
    future_typed_dates,
    number_warnings,
)
from reach_around.config.page_title import DEFAULT_TEMPLATE, render_template
from reach_around.config.profile_card import MEDIA_KIT_ID
from reach_around.config.tablinks import is_tab_ref, parse_tab_ref
from reach_around.site.assets import collect_assets
from reach_around.site.cachepolicy import cache_patterns
from reach_around.site.fingerprint import AssetMap, fingerprint
from reach_around.site.frames import (
    SIZE_SCALES,
    SLOT_SIZES,
    check_frames,
    frame_plan,
)
from reach_around.site.inventory import InventoryEntry, build_inventory
from reach_around.site.kit import (
    GATED_FILE,
    check_gated_requests,
    check_no_gated_text,
    check_private_root,
    check_roots,
    gate_path_extras,
    public_paths,
    render_kit,
    stage_gated,
)
from reach_around.site.overrides import (
    load_custom_css,
    make_environment,
    render_headers,
    warn_about_javascript,
)
from reach_around.site.platforms import platform_rows
from reach_around.site.privacy import collected_items, intro_text, sets_cookies
from reach_around.site.redirects import render_redirects
from reach_around.site.robots import render_robots
from reach_around.site.theme import preload_fonts, theme_css
from reach_around.site.visibility import visible_blocks


@dataclass(frozen=True)
class BuildManifest:
    """What the build published: visible off-site link id -> destination URL.

    Internal ``tab:`` links are not listed: they are not click-counted.
    """

    links: dict[str, str]
    # Days the Worker keeps a lead: the same config value the privacy page
    # prints. The deploy writes it to the Worker as LEAD_RETENTION_DAYS.
    lead_retention_days: int
    # Every published file with its hash, size and cache class (see
    # ``reach_around.site.inventory``); filled in by ``build_site``.
    inventory: dict[str, InventoryEntry] = field(default_factory=dict)
    # Build warnings the caller should show (for example an old kit number).
    warnings: tuple[str, ...] = ()
    # The gated page written by this build, or None when none was (a kit with no
    # gated content removes any old one). Never part of the published site.
    gated_page: Path | None = None


def _links_in(blocks: Iterable[Block]) -> list[Link]:
    links: list[Link] = []
    for block in blocks:
        if isinstance(block, Link):
            links.append(block)
        elif isinstance(block, Section):
            links.extend(block.links)
    return links


def _page_has_frames(config: SiteConfig, blocks: Iterable[Block]) -> bool:
    """Whether a link page has an element that can carry a frame."""
    if config.theme.frame == "none":
        return False
    shown = list(blocks)
    return bool(_links_in(shown)) or _has_platforms(shown)


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


def _has_live(blocks: Iterable[Block]) -> bool:
    return any(isinstance(block, Live) for block in blocks)


# Published only for a site with a crypto block on a page; see ``build_site``.
_CRYPTO_FILES = (Path("static") / "copy.js", Path("static") / "crypto.css")
_PLATFORMS_FILES = (Path("static") / "platforms.css",)
# Published only for a site with a live block; linked from the live page only.
_LIVE_STYLE = Path("static") / "live.css"
# The live script (status badge, click-to-load) goes on every page of such a site.
_LIVE_SCRIPT = Path("static") / "live.js"
# Published only for a site with a kit.
_KIT_STYLE = Path("static") / "kit.css"
_FRAME_STYLE = Path("static") / "frame.css"


# Published only for a site with an email block; the script only if one uses it.
_EMAIL_STYLE = Path("static") / "email.css"
_EMAIL_SCRIPT = Path("static") / "email.js"


def _page_path(tab_index: int, tab: Tab) -> Path:
    if tab_index == 0:
        return Path("index.html")
    return Path(tab.id) / "index.html"


def _page_paths(config: SiteConfig, with_kit: bool = True) -> set[Path]:
    """Every HTML file the build writes (``with_kit`` adds the kit's pages)."""
    if config.page_layout == "sections":
        pages = {Path("index.html")}
    else:
        pages = {_page_path(i, tab) for i, tab in enumerate(config.tabs)}
    pages |= {Path("privacy") / "index.html", Path("404.html")}
    return pages | public_paths(config) if with_kit else pages


def _asset_url(assets: AssetMap) -> Callable[[str, str], str]:
    """``asset_url(value, root)``: a local file's hashed URL, a remote one as is.

    Config values such as an avatar may be an https URL or a path inside the
    instance; a template calls this and does not care which.
    """

    def asset_url(value: str, root: str) -> str:
        if ":" in value or value.startswith("//"):
            return value
        return root + assets(value)

    return asset_url


def _font_preloads(
    config: SiteConfig, assets: AssetMap
) -> Callable[[str], list[dict[str, str]]]:
    """``font_preloads(root)``: the head preload links for this site's fonts."""
    fonts = preload_fonts(config.theme)

    def font_preloads(root: str) -> list[dict[str, str]]:
        return [{"href": root + assets(path), "type": mime} for path, mime in fonts]

    return font_preloads


def _nav(config: SiteConfig, current: int, root: str) -> list[dict[str, Any]]:
    if config.page_layout == "sections":
        return []  # one page: no tab bar, the sections are on the page
    return [
        {
            "title": tab.title,
            "href": root if i == 0 else f"{root}{tab.id}/",
            "current": i == current,
            "live": _has_live(tab.blocks),
        }
        for i, tab in enumerate(config.tabs)
    ]


def _internal_href(
    config: SiteConfig, root: str, shown: list[list[Block]] | None = None
) -> Callable[[str], str]:
    """Resolve ``tab:<id>[#fragment]`` to the tab's path as seen from a page.

    ``root`` is what the nav uses for the first tab ("./" or "../"); the other
    tabs live at ``<prefix><id>/``. The config was validated at load, so every
    id is known.
    """
    if config.page_layout == "sections":
        return _section_href(config, root, shown)
    prefix = "" if root == "./" else root
    homes = {
        tab.id: (root if i == 0 else f"{prefix}{tab.id}/")
        for i, tab in enumerate(config.tabs)
    }

    def href(url: str) -> str:
        tab_id, fragment = parse_tab_ref(url)
        return homes[tab_id] if fragment is None else f"{homes[tab_id]}#{fragment}"

    return href


def _section_href(
    config: SiteConfig, root: str, shown: list[list[Block]] | None
) -> Callable[[str], str]:
    """``tab:`` links on the single page: the first tab is the page, the rest are
    sections reached by fragment. A browser opens a closed ``<details>`` that
    holds the fragment's target, so the link works without script.

    ``root`` is the home page as seen from the current page ("./" on the page
    itself, "../" on the privacy page, "/" on the 404 page), so a fragment keeps
    that prefix off the home page. A tab with nothing to show has no section; a
    link to it goes to the page itself rather than to a missing id."""
    first = config.tabs[0].id
    omitted = (
        set()
        if shown is None
        else {
            t.id
            for t, blocks in zip(config.tabs[1:], shown[1:], strict=True)
            if not blocks
        }
    )
    prefix = "" if root == "./" else root

    def href(url: str) -> str:
        tab_id, fragment = parse_tab_ref(url)
        if fragment is not None:
            return f"{prefix}#{fragment}"
        if tab_id == first or tab_id in omitted:
            return root
        return f"{prefix}#{tab_id}"

    return href


def _sections(config: SiteConfig, shown: list[list[Block]]) -> list[dict[str, Any]]:
    """The collapsible sections of the single page: every tab after the first
    that still has something to show. The first of them is open unless a tab
    says otherwise with ``open``."""
    out: list[dict[str, Any]] = []
    for tab, blocks in zip(config.tabs[1:], shown[1:], strict=True):
        if not blocks:
            continue
        opened = tab.open if tab.open is not None else not out
        out.append({"tab": tab, "blocks": blocks, "open": opened})
    return out


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
        disclosure=_disclosure_line(config),
        show_disclosure=_show_disclosure(config, has_affiliate),
        show_cookie_banner=sets_cookies(config),
        click_tracking=config.features.click_tracking,
        footer_credit=config.footer.credit,
        footer_credit_text=config.footer.text,
        footer_credit_url=CREDIT_URL,
        page_views=config.analytics.page_views,
        page_id=tab.id,
        view_route=config.analytics.route,
        has_crypto=_has_crypto(blocks),
        has_email=_has_email(blocks),
        has_email_script=_has_email_script(blocks),
        has_platforms=_has_platforms(blocks),
        has_live=_has_live(blocks),
        wide=_has_live(blocks),
        profile_card=_profile_card(config),
        cookie_text=intro_text(config),
        custom_css=custom_css,
        page_frames=_page_has_frames(config, blocks),
    )


def _disclosure_line(config: SiteConfig) -> str:
    return config.disclosure.line if config.disclosure else ""


def _show_disclosure(config: SiteConfig, has_affiliate: bool) -> bool:
    return config.disclosure is not None and config.disclosure.visible(has_affiliate)


def _render_sections_page(
    env: Environment,
    config: SiteConfig,
    shown: list[list[Block]],
    custom_css: bool,
) -> str:
    """The single page of ``page_layout: sections``."""
    tab = config.tabs[0]
    every = [block for blocks in shown for block in blocks]
    return env.get_template("sections.html").render(
        creator=config.creator,
        tab=tab,
        blocks=shown[0],
        sections=_sections(config, shown),
        nav=[],
        tab_href=_internal_href(config, "./", shown),
        root="",
        page_title=page_title(config, tab.title, home=True, own=tab.page_title),
        disclosure=_disclosure_line(config),
        show_disclosure=_show_disclosure(
            config, any(link.affiliate for link in _links_in(every))
        ),
        show_cookie_banner=sets_cookies(config),
        click_tracking=config.features.click_tracking,
        footer_credit=config.footer.credit,
        footer_credit_text=config.footer.text,
        footer_credit_url=CREDIT_URL,
        page_views=config.analytics.page_views,
        page_id=tab.id,
        view_route=config.analytics.route,
        has_crypto=_has_crypto(every),
        has_email=_has_email(every),
        has_email_script=_has_email_script(every),
        has_platforms=_has_platforms(every),
        profile_card=_profile_card(config),
        cookie_text=intro_text(config),
        custom_css=custom_css,
        page_frames=_page_has_frames(config, every),
    )


def _render_privacy(
    env: Environment,
    config: SiteConfig,
    shown: list[list[Block]],
    custom_css: bool,
) -> str:
    return env.get_template("privacy.html").render(
        creator=config.creator,
        nav=_nav(config, -1, "../"),
        tab_href=_internal_href(config, "../", shown),
        root="../",
        page_title=page_title(config, "Privacy", home=False, own=None),
        disclosure=_disclosure_line(config),
        show_disclosure=_show_disclosure(config, False),
        show_cookie_banner=False,
        click_tracking=config.features.click_tracking,
        footer_credit=config.footer.credit,
        footer_credit_text=config.footer.text,
        footer_credit_url=CREDIT_URL,
        page_views=False,
        has_crypto=False,
        has_email=False,
        has_email_script=False,
        has_platforms=False,
        profile_card=_profile_card(config),
        single_page=config.page_layout == "sections",
        intro=intro_text(config),
        items=collected_items(config),
        contact=config.privacy.contact,
        custom_css=custom_css,
    )


def _render_not_found(
    env: Environment,
    config: SiteConfig,
    shown: list[list[Block]],
    custom_css: bool,
) -> str:
    """The 404 page.

    Hosts serve it at arbitrary depths (``/a/b/c``), so unlike every other page
    its asset and nav links are root-relative (``root="/"``). It never loads
    click.js and carries ``noindex``.
    """
    return env.get_template("404.html").render(
        creator=config.creator,
        nav=_nav(config, -1, "/"),
        tab_href=_internal_href(config, "/", shown),
        root="/",
        page_title=page_title(config, "Not found", home=False, own=None),
        disclosure=_disclosure_line(config),
        show_disclosure=_show_disclosure(config, False),
        show_cookie_banner=sets_cookies(config),
        click_tracking=False,
        footer_credit=config.footer.credit,
        footer_credit_text=config.footer.text,
        footer_credit_url=CREDIT_URL,
        page_views=False,
        has_crypto=False,
        has_email=False,
        has_email_script=False,
        has_live_script=False,
        has_platforms=False,
        noindex=True,
        profile_card=_profile_card(config),
        cookie_text=intro_text(config),
        custom_css=custom_css,
    )


def _is_remote(value: str) -> bool:
    return ":" in value or value.startswith("//")


def _kit_base(
    config: SiteConfig, shown: list[list[Block]], custom_css: bool
) -> Callable[[str, str, bool], dict[str, Any]]:
    """The context shared by the kit pages: ``base(root, tab_name)``.

    ``root`` is ``../``, ``../../`` or (for the gated page, which the Worker
    serves at any depth) ``/``. No script, no click or view tracking and no
    profile card: the kit pages carry none of them.
    """

    def base(root: str, tab_name: str, gated: bool) -> dict[str, Any]:
        creator = config.creator
        if gated and creator.avatar is not None and _is_remote(creator.avatar):
            # The gated page makes no third-party request: a remote avatar is
            # left out there (the public pages keep it, like every other page).
            creator = creator.model_copy(update={"avatar": None})
        return {
            "creator": creator,
            "nav": _nav(config, -1, root),
            "tab_href": _internal_href(config, root, shown),
            "page_title": page_title(config, tab_name, home=False, own=None),
            "disclosure": config.disclosure,
            "show_disclosure": False,
            "show_cookie_banner": sets_cookies(config),
            "click_tracking": False,
            "footer_credit": config.footer.credit,
            "footer_credit_text": config.footer.text,
            "footer_credit_url": CREDIT_URL,
            "page_views": False,
            "has_crypto": False,
            "has_email": False,
            "has_email_script": False,
            "has_live_script": False,
            "has_platforms": False,
            "has_kit": True,
            "skip_label": "Skip to the kit",
            "profile_card": None,
            "cookie_text": intro_text(config),
            "custom_css": custom_css,
        }

    return base


def _live_path_extras(
    config: SiteConfig, shown: list[list[Block]]
) -> dict[str, dict[str, list[str]]]:
    """The CSP extra for the live page: ``frame-src`` for the two player origins.

    Only the path of a tab that shows a live block gets it, and only that one
    directive; every other page keeps the site-wide policy.
    """
    origins = [LIVE_PLAYER_ORIGINS[name] for name in sorted(LIVE_PLAYER_ORIGINS)]
    extras: dict[str, dict[str, list[str]]] = {}
    for i, (tab, blocks) in enumerate(zip(config.tabs, shown, strict=True)):
        if _has_live(blocks):
            extras["/" if i == 0 else f"/{tab.id}/"] = {"frame-src": origins}
    return extras


def _render_pages(
    env: Environment,
    config: SiteConfig,
    shown: list[list[Block]],
    custom_css: bool,
) -> dict[Path, str]:
    if config.page_layout == "sections":
        pages = {
            Path("index.html"): _render_sections_page(env, config, shown, custom_css)
        }
    else:
        pages = {
            _page_path(i, tab): _render_page(env, config, i, shown[i], custom_css)
            for i, tab in enumerate(config.tabs)
        }
    pages[Path("privacy") / "index.html"] = _render_privacy(
        env, config, shown, custom_css
    )
    pages[Path("404.html")] = _render_not_found(env, config, shown, custom_css)
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
    return BuildManifest(
        links=links, lead_retention_days=config.privacy.lead_retention_days
    )


def manifest_json(manifest: BuildManifest) -> str:
    """The manifest file text. It is never written into the published site."""
    body = {
        "links": manifest.links,
        "lead_retention_days": manifest.lead_retention_days,
    }
    return json.dumps(body, sort_keys=True, indent=2) + "\n"


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
    gated_out: Path | None = None,
    number_day: date | None = None,
) -> BuildManifest:
    """Write the site for ``today`` into ``out_dir`` and return the manifest.

    ``number_day`` is the day number ages are judged on (the stale marks and
    old-number warnings); callers pass the UTC day. It defaults to ``today``.

    The manifest is returned, never written into ``out_dir``: the caller
    decides where it goes (see ``manifest_json``). It also carries the
    inventory of published files (see ``reach_around.site.inventory``).

    Every file under ``static/`` and every instance file the config names is
    published under a content-hashed name; templates reach them through the
    ``asset`` and ``asset_url`` functions. Pages, ``404.html``, ``robots.txt``
    and ``_headers`` keep their names.

    Everything already in ``out_dir`` is deleted first, so a removed tab never
    leaves a stale page behind. ``protect`` lists paths (the config file) that
    ``out_dir`` must not be or contain. ``overrides`` is an optional directory
    with ``templates/`` and ``custom.css`` (see ``reach_around.site.overrides``).
    ``assets_dir`` is the instance directory: the files the config names (avatar,
    favicon, theme images, font files, image blocks) are copied from it to the
    same relative path in ``out_dir``. It is required when the config names any.
    ``numbers`` is the loaded numbers file; its counts fill platform blocks and
    only the resulting text is published, never the file.

    ``gated_out`` is the directory for the gated kit page (see
    ``reach_around.site.kit``). It is a separate root from ``out_dir``, which is
    all that is published; it is required when the kit has gated content, may not
    be, contain or sit inside ``out_dir``, and the build touches only the one
    file it writes there. The gated page is never written into ``out_dir``.
    """
    kit = config.kit
    if gated_out is not None:
        check_roots(out_dir, gated_out)
        check_private_root(gated_out)
        gated_root = _check_out_dir(gated_out, protect)
    if kit is not None and kit.has_gated_content and gated_out is None:
        raise ValueError(
            "the kit has gated content, which is built into a separate directory "
            "that is never published: pass --gated-out DIR (a directory outside "
            "the output directory)"
        )
    future = future_typed_dates(config, today)
    if future:
        names = ", ".join(f"{n.block_id!r} {n.platform}" for n in future)
        raise ValueError(
            f"as_of is in the future (more than one day after {today}) for: {names}"
        )
    shown = [list(visible_blocks(tab.blocks, today)) for tab in config.tabs]
    manifest = _manifest(config, shown)
    custom_css = load_custom_css(overrides)
    warn_about_javascript(overrides)
    # Static files first: pages name them by content hash, so the names must
    # exist before any page is rendered. Nothing is written yet.
    static = _static_files()
    pages_at = _page_paths(config)
    if config.kit is not None:
        taken = public_paths(config) & _page_paths(config, with_kit=False)
        if taken:
            raise ValueError(
                f"{sorted(taken)[0]} is used by both the kit and a tab; the media "
                "kit page lives at /kit/, so rename the tab (its id must not be "
                "'kit')"
            )
    for rel, data in collect_assets(config, assets_dir).items():
        if rel in static or rel in pages_at:
            raise ValueError(f"{rel} would overwrite a file the build writes itself")
        static[rel] = data
    if not config.features.click_tracking:
        static.pop(Path("static") / "click.js", None)
    if not config.analytics.page_views:
        static.pop(Path("static") / "view.js", None)
    if not any(_has_crypto(blocks) for blocks in shown):
        for rel in _CRYPTO_FILES:
            static.pop(rel, None)
    if not any(_has_email(blocks) for blocks in shown):
        static.pop(_EMAIL_STYLE, None)
    if not any(_has_email_script(blocks) for blocks in shown):
        static.pop(_EMAIL_SCRIPT, None)
    if not any(_has_platforms(blocks) for blocks in shown):
        for rel in _PLATFORMS_FILES:
            static.pop(rel, None)
    if not any(_has_live(blocks) for blocks in shown):
        static.pop(_LIVE_STYLE, None)
        static.pop(_LIVE_SCRIPT, None)
    if kit is None:
        static.pop(_KIT_STYLE, None)
    if config.theme.frame == "none":
        static.pop(_FRAME_STYLE, None)
    static[Path("static") / "theme.css"] = theme_css(config.theme).encode("utf-8")
    if custom_css is not None:
        static[Path("static") / "custom.css"] = custom_css.encode("utf-8")
    named = fingerprint(static)
    env = make_environment(overrides)
    env.globals["platform_rows"] = lambda block: platform_rows(
        block, numbers, number_day or today, config
    )
    env.globals["asset"] = named.assets
    env.globals["footer_copyright"] = config.footer.copyright_line(today.year)
    # Every page with a tab bar carries the nav badge, so every such page gets the
    # live script; the 404 page and the kit pages say False for themselves.
    env.globals["has_live_script"] = any(_has_live(blocks) for blocks in shown)
    env.globals["asset_url"] = _asset_url(named.assets)
    env.globals["font_preloads"] = _font_preloads(config, named.assets)
    plan = frame_plan(config.theme.frame)
    env.globals["frame_plan"] = plan
    env.globals["frame_sizes"] = {slot: SLOT_SIZES[slot] for slot in plan}
    env.globals["frame_scales"] = SIZE_SCALES
    files: dict[Path, bytes] = {
        page: html.encode("utf-8")
        for page, html in _render_pages(
            env, config, shown, custom_css is not None
        ).items()
    }
    kit_render = render_kit(
        env,
        config,
        numbers,
        number_day or today,
        _kit_base(config, shown, custom_css is not None),
    )
    files.update(
        {page: html.encode("utf-8") for page, html in kit_render.public.items()}
    )
    files.update(named.files)
    check_frames(
        {
            path.as_posix(): data.decode("utf-8")
            for path, data in files.items()
            if path.suffix == ".html"
        }
    )
    if kit_render.gated is not None:
        check_frames({"kit.html (gated)": kit_render.gated})
    robots = config.robots
    if robots is not None:
        if Path("robots.txt") in files:
            raise ValueError(
                "robots.txt would overwrite a file the build writes itself"
            )
        files[Path("robots.txt")] = render_robots(robots).encode("ascii")
    published = {path.as_posix() for path in files}
    socials = config.redirects.socials if config.redirects else None
    if socials is not None:
        shadowed = sorted(
            p
            for p in published
            if p in ("socials.html", "socials/index.html") or p.startswith("socials/")
        )
        if shadowed:
            raise ValueError(
                f"redirects.socials would hide {shadowed[0]}: rename the tab "
                "(its id must not be 'socials') or turn the redirect off"
            )
        files[Path("_redirects")] = render_redirects(socials).encode("ascii")
    immutable, revalidate = cache_patterns(
        published, {path.as_posix() for path in named.hashed}
    )
    files[Path("_headers")] = render_headers(
        path_extras={**_live_path_extras(config, shown), **gate_path_extras(config)},
        noindex_paths=robots.noindex_paths if robots else (),
        immutable_paths=immutable,
        revalidate_paths=revalidate,
    ).encode("utf-8")
    # The wall: nothing gated may be in the public files. Checked on the finished
    # in-memory tree, before anything on disk is touched.
    check_no_gated_text(files, kit)
    if kit_render.gated is not None:
        check_gated_requests(kit_render.gated, files)
    out = _check_out_dir(out_dir, protect)
    staged = None
    if gated_out is not None:
        # Stage the gated page before the public tree is touched, so a failure
        # to write it leaves the public tree as it was; make the public root
        # exist and compare the two roots again by identity now that both do.
        out.mkdir(parents=True, exist_ok=True)
        check_roots(out, gated_root)
        staged = stage_gated(gated_root, kit_render.gated)
    try:
        _empty(out)
        _write(out, files)
        if staged is not None:
            staged.commit()
    except BaseException:
        if staged is not None:
            staged.discard()
        raise
    return replace(
        manifest,
        gated_page=(
            gated_root / GATED_FILE
            if staged is not None and kit_render.gated is not None
            else None
        ),
        inventory=build_inventory(files, named.hashed),
        warnings=(
            *number_warnings(config, numbers, number_day or today),
            *kit_render.warnings,
        ),
    )
