# SPDX-License-Identifier: Apache-2.0
"""Render the media kit: public pages, the gated page, and the wall between them.

Two audiences, two output roots. The public pages (``kit/index.html`` and
``kit/retry/index.html``) join the Pages output and are rendered only from
``public_projection``. The gated page is rendered from ``gated_projection`` into
a *separate* directory (``--gated-out``) that is never part of the Pages output;
the Worker, not Pages, serves it after the gate. The rule about which field goes
where is the display policy in ``reach_around.config.kit``, never a template.

The wall is structural and checked at build time:

* the gated directory may not be, contain or sit inside the public output;
* the gated page has a file name no public file may use;
* before anything is written, every gated string (partner, case study, rate and
  demographic text) is searched for in every public file, as written and as
  HTML-escaped. A hit fails the build (``KitLeakError``). Strings shorter than
  ``GUARD_MIN_LENGTH`` are skipped, since a short word would match ordinary text;
  the structural separation, not this search, is what keeps them out.

Not here, on purpose: the Turnstile script and the CSP exception it needs are a
separate task (Plan 3, Task 2A). The widget slot is markup only, so this build
makes no third-party request and ``_headers`` is unchanged.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from jinja2 import Environment

from reach_around.config.blocks import platform_name
from reach_around.config.kit import (
    KitConfig,
    ResolvedMetric,
    UnifiedTotal,
    gated_projection,
    kit_warnings,
    public_projection,
)
from reach_around.config.models import SiteConfig
from reach_around.config.numbers import Numbers

KIT_PAGE = Path("kit") / "index.html"
RETRY_PAGE = Path("kit") / "retry" / "index.html"
# The gated page's name inside the gated directory. No public file may use it.
GATED_FILE = Path("kit.html")

# Placeholder until the Worker route exists (D1, #263); then this becomes
# "/k/gate" in the change that adds the route. Until then a post lands on the
# static retry page, which fails closed.
GATE_ACTION = "/kit/retry/"

GUARD_MIN_LENGTH = 6


class KitLeakError(ValueError):
    """Gated text was found in the public output; the build is refused."""


@dataclass(frozen=True)
class KitRender:
    public: dict[Path, str]
    gated: str | None
    warnings: list[str]


def has_gate_form(kit: KitConfig) -> bool:
    """The gate form, the retry page and the gated page exist together, and only
    when the kit has gated content (which the config allows only with
    ``features.kit_gate`` on)."""
    return kit.has_gated_content


def public_paths(config: SiteConfig) -> set[Path]:
    """Every HTML file the kit adds to the public output."""
    if config.kit is None:
        return set()
    paths = {KIT_PAGE}
    if has_gate_form(config.kit):
        paths.add(RETRY_PAGE)
    return paths


# --- the wall ----------------------------------------------------------------


def check_roots(out_dir: Path, gated_out: Path) -> None:
    """Refuse a gated directory that is, contains or sits inside the public one."""
    out, gated = out_dir.resolve(), gated_out.resolve()
    if gated == out or out in gated.parents or gated in out.parents:
        raise ValueError(
            f"gated output directory {gated_out} is the public output directory "
            f"{out_dir}, inside it or around it; the gated page must never be "
            "published, so choose a separate directory"
        )


def _gated_strings(kit: KitConfig) -> list[tuple[str, str]]:
    """(where, text) for every gated string long enough to search for."""
    found: list[tuple[str, str]] = []
    for name in ("partners", "case_studies", "rates", "demographics"):
        for index, item in enumerate(getattr(kit, name)):
            for field, value in item.model_dump().items():
                if isinstance(value, str) and len(value) >= GUARD_MIN_LENGTH:
                    found.append((f"kit.{name}[{index}].{field}", value))
    return found


def check_no_gated_text(files: Mapping[Path, bytes], kit: KitConfig | None) -> None:
    """Raise ``KitLeakError`` if a gated file name or string is in ``files``."""
    if GATED_FILE in files:
        raise KitLeakError(f"the public output would contain {GATED_FILE}")
    if kit is None:
        return
    needles = [
        (where, variant.encode("utf-8"))
        for where, text in _gated_strings(kit)
        for variant in {text, html.escape(text), html.escape(text, quote=False)}
    ]
    for rel, data in sorted(files.items()):
        for where, needle in needles:
            if needle in data:
                raise KitLeakError(
                    f"gated text from {where} would appear in the public file "
                    f"{rel.as_posix()}; gated text may only go to the gated "
                    "output. Reword the public text or the gated entry."
                )


def write_gated(gated_out: Path, page: str | None) -> None:
    """Write the gated page, or remove a stale one. Touches no other file."""
    target = gated_out / GATED_FILE
    if page is None:
        if target.is_file() or target.is_symlink():
            target.unlink()
        return
    gated_out.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")


# --- content -----------------------------------------------------------------


def _metric_rows(metrics: list[ResolvedMetric]) -> list[dict[str, str]]:
    return [
        {
            "platform": "" if m.platform == "other" else platform_name(m.platform),
            "label": m.label,
            "value": f"{m.value:,}",
            "as_of": m.as_of.isoformat() if m.as_of is not None else "",
        }
        for m in metrics
        if m.value is not None
    ]


def _total(total: UnifiedTotal, metrics: list[ResolvedMetric]) -> dict[str, Any] | None:
    if total.total is None:
        return None
    dates = [m.as_of for m in metrics if m.in_total and m.value is not None and m.as_of]
    return {
        "value": f"{total.total:,}",
        "counted": total.counted,
        "oldest": min(dates).isoformat() if dates else "",
    }


def _content(projection: dict[str, Any]) -> dict[str, Any]:
    metrics: list[ResolvedMetric] = projection["metrics"]
    return {
        "title": projection["title"],
        "about": projection["about"],
        "metrics": _metric_rows(metrics),
        "total": _total(projection["unified_total"], metrics),
    }


def _categories(kit: KitConfig) -> str:
    names = [
        label
        for label, items in (
            ("past partners", kit.partners),
            ("case studies", kit.case_studies),
            ("rates", kit.rates),
            ("audience details", kit.demographics),
        )
        if items
    ]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def plan_warnings(
    config: SiteConfig, numbers: Numbers | None, today: date
) -> list[str]:
    kit = config.kit
    if kit is None:
        return []
    found = kit_warnings(kit, numbers, today)
    if config.features.kit_gate and not kit.has_gated_content:
        found.append(
            "features.kit_gate is on but the kit has no partners, case studies, "
            "rates or demographics; no gate form or gated page is built"
        )
    if has_gate_form(kit) and kit.turnstile_site_key is None:
        found.append(
            "kit.turnstile_site_key is not set; the gate form has an empty widget slot"
        )
    return found


BaseContext = Callable[[str, str], dict[str, Any]]


def render_kit(
    env: Environment,
    config: SiteConfig,
    numbers: Numbers | None,
    today: date,
    base: BaseContext,
) -> KitRender:
    """Render the kit. ``base(root, tab_name)`` is the context every page shares
    (navigation, titles, footer flags); the kit adds its own content."""
    kit = config.kit
    if kit is None:
        return KitRender({}, None, [])
    warnings = plan_warnings(config, numbers, today)
    public = _content(public_projection(kit, numbers))
    form: dict[str, Any] | None = None
    if has_gate_form(kit):
        form = {
            "action": GATE_ACTION,
            "site_key": kit.turnstile_site_key,
            "categories": _categories(kit),
        }
    pages: dict[Path, str] = {}
    ctx = base("../", "Media kit")
    pages[KIT_PAGE] = env.get_template("kit.html").render(
        **{**ctx, "root": "../", "kit": public, "form": form}
    )
    if form is not None:
        ctx = base("../../", "Try again")
        pages[RETRY_PAGE] = env.get_template("kit_retry.html").render(
            **{**ctx, "root": "../../", "kit": public, "form": form, "noindex": True}
        )
    gated: str | None = None
    if has_gate_form(kit):
        data = gated_projection(kit, numbers)
        content = _content(data)
        content.update(
            partners=data["partners"],
            case_studies=data["case_studies"],
            rates=data["rates"],
            demographics=[
                {"label": d.label, "value": d.value, "as_of": d.as_of.isoformat()}
                for d in data["demographics"]
            ],
        )
        ctx = base("/", "Media kit details")
        gated = env.get_template("kit_gated.html").render(
            **{**ctx, "root": "/", "kit": content, "noindex": True}
        )
    return KitRender(pages, gated, warnings)
