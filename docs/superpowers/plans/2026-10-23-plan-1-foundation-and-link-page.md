# Reach Around Plan 1: Foundation and Link Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A tagged `v0.1.0` that builds the maintainer's link page as a static site from a validated config, tracks clicks through a small write-only Cloudflare Worker, and ships as a Docker and Podman image.

**Architecture:** A Python package (`reach_around`) loads a hardened YAML config, validates it into typed models, and renders a static site with Jinja2. A TypeScript Cloudflare Worker (`worker/`) records click events into D1 and never returns visitor data. The site is deployed to Cloudflare Pages on a test subdomain.

**Tech Stack:** Python 3.11+, PyYAML (hardened loader), pydantic v2, Jinja2, pytest, ruff, mypy; TypeScript, Cloudflare Workers and D1, wrangler, vitest; Containerfile (Docker and Podman).

**Spec:** `docs/superpowers/specs/2026-10-23-reach-around-design.md` (approved 2026-10-23, amended by PR #28). The roadmap for later phases is `docs/superpowers/plans/2026-10-23-roadmap.md`.

## Global Constraints

- Python 3.11 or newer; tests run on 3.11, 3.12 and 3.13.
- License Apache-2.0; every new source file starts with `# SPDX-License-Identifier: Apache-2.0` (TypeScript: `// SPDX-License-Identifier: Apache-2.0`).
- American English in code, docs and messages.
- The public site is static and works with no JavaScript. The only dynamic server code is the one write-only Worker. The Worker never returns visitor data (emails, clicks, leads).
- Do not store IP addresses. At most a two-letter country code from Cloudflare headers.
- No personal details of the maintainer (address, callsign, grid square, hostnames, machine identifiers, real emails) in the repository, examples, tests or logs. Examples use synthetic data and the placeholder callsign `N0CALL` if one is ever needed.
- No secrets in the repository. Credentials come from the environment or a secrets manager.
- CI calls git-your-ship-together reusable workflows pinned by commit with a version comment: `ChiefGyk3D/git-your-ship-together/.github/workflows/python-ci.yml@b9db99287236b19f0249f83b54acc5cc41126e42 # v1.24.0`. The gate job is named `CI green`.
- Every PR links its issue (`Refs #n` or `Closes #n`). Every task has an issue and a board card before work starts.
- Versions are semantic. Tags and releases start with the first container image: `v0.1.0`. While alpha, versions are `0.x`.
- Containers build from a `Containerfile`, run rootless as a non-root user, depend on no Docker socket, and pin base images by digest. Both `docker` and `podman` must build and run it.
- The README is marked alpha. Keep that banner until the maintainer removes it.
- Time zone for anything date-based defaults to `America/New_York`.
- Privacy: the site collects the bare minimum and none of it is sensitive, and says so plainly. The disclosure page and any cookie notice are generated from the features that are enabled, so they cannot claim less than the site does. A cookie notice (Preferences, Reject, Accept) is rendered only when a feature actually sets a cookie.

## Review Focus

Inputs and failure modes the spec implies but no obvious test covers, most likely first. Each has a test in the task that owns the code.

1. **Hostile config.** A YAML file with duplicate keys, anchors, aliases, merge keys, a huge size or deep nesting is refused with the key path, never half-loaded. (Task 2)
2. **Open redirect and link-id forgery.** The click endpoint accepts only link ids that the build manifest lists. An unknown id, a URL passed as an id, or an empty id records nothing and reveals nothing. No route redirects to a caller-supplied URL. (Task 8)
3. **HTML and script in text fields.** A link title or subtitle containing `<script>`, quotes or unicode is escaped everywhere it is rendered, and a `javascript:` or `data:` URL is rejected at config time. (Tasks 3 and 6)
4. **Schedule boundaries.** A link with `show_until` today still shows all day Eastern Time and disappears the next day, including across the daylight-saving change. (Task 5)
5. **Bot and burst clicks.** Bot user agents, a missing user agent, a wrong origin and a burst from one client record no events or are limited, and the endpoint still returns the same empty response. (Task 8)

---

## File Structure

```
pyproject.toml                       project metadata, tool config, console script
requirements-dev.in / .txt           pinned dev dependencies with hashes
Containerfile                        OCI image (Task 10)
compose.yaml, deploy/podman/*.container   Docker/Podman run files (Task 10)
.github/workflows/ci.yml             GYST caller + podman build job
.github/workflows/deploy-test.yml    manual deploy to the test subdomain (Task 11)
src/reach_around/__init__.py         __version__
src/reach_around/cli.py              argparse entry: --version, build
src/reach_around/config/loader.py    hardened YAML loader, ConfigError
src/reach_around/config/models.py    pydantic models and load_site_config
src/reach_around/site/visibility.py  schedule logic
src/reach_around/site/build.py       renders the static site and manifest
src/reach_around/site/privacy.py     builds the disclosure from enabled features
src/reach_around/site/templates/*.html, static/site.css, static/click.js
worker/                              Cloudflare Worker (TypeScript)
examples/instance/site.yaml          synthetic example config
docs/config.md, docs/education/*.md  config reference, security education pages
tests/                               pytest
```

---

### Task 1: Package scaffold and CI

**Files:**
- Create: `pyproject.toml`, `requirements-dev.in`, `requirements-dev.txt`, `.gitignore`, `src/reach_around/__init__.py`, `src/reach_around/cli.py`, `tests/test_cli.py`, `.github/workflows/ci.yml`, `.github/dependabot.yml`

**Interfaces:**
- Produces: `reach_around.__version__: str`; `reach_around.cli.main(argv: list[str] | None = None) -> int`; console script `reach-around`.

- [ ] **Step 1: Write the failing test** in `tests/test_cli.py`: `test_version_flag_prints_version_and_returns_zero` calls `main(["--version"])`, captures stdout with `capsys`, asserts it equals `f"reach-around {__version__}\n"` and the return value is `0`. A second test, `test_no_arguments_prints_help_and_returns_two`, asserts `main([])` returns `2`.
- [ ] **Step 2: Run** `pytest tests/test_cli.py -v`. Expected: FAIL, import error.
- [ ] **Step 3: Implement** the package: `__version__ = "0.0.0"`, an argparse `main` with subcommands (`build` added in Task 6), `pyproject.toml` with a `src` layout, `requires-python = ">=3.11"`, dependencies `PyYAML`, `pydantic>=2`, `Jinja2`, tool config for ruff, mypy (strict) and pytest.
- [ ] **Step 4: Add CI.** `.github/workflows/ci.yml` follows the SAFO caller (job `ci`, GYST python-ci at the pinned commit, `python-versions: '["3.11", "3.12", "3.13"]'`, ruff, mypy, pytest with coverage, `smoke-command: reach-around --version`, `docker-build: false` until Task 10, hashed dev requirements). Add dependabot for pip, GitHub Actions and npm (`/worker`).
- [ ] **Step 5: Run** `pytest -q && ruff check . && ruff format --check . && mypy`. Expected: all pass.
- [ ] **Step 6: Commit** `feat: scaffold package, CLI and CI`.

### Task 2: Hardened YAML loader

**Files:**
- Create: `src/reach_around/config/__init__.py`, `src/reach_around/config/loader.py`, `tests/config/test_loader.py`

**Interfaces:**
- Produces: `class ConfigError(Exception)` with attributes `path: str`, `line: int | None`, `message: str`; `load_yaml(path: Path, *, max_bytes: int = 1_048_576, max_depth: int = 64) -> object`.

- [ ] **Step 1: Write failing tests**, one per refusal, each asserting `ConfigError` and that `error.message` names the problem: `test_duplicate_key_refused`, `test_anchor_refused`, `test_alias_refused`, `test_merge_key_refused`, `test_oversized_file_refused` (file of `max_bytes + 1`), `test_deep_nesting_refused` (65 levels), `test_non_mapping_root_is_allowed_to_load_but_models_reject_it` (loader returns the list), `test_valid_file_loads` (a small mapping returns a dict).
- [ ] **Step 2: Run** `pytest tests/config/test_loader.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement `load_yaml`** with a `yaml.SafeLoader` subclass that detects duplicate keys, anchors, aliases and merge keys while composing, and checks size before parsing and depth during composition. Mirror the refusals documented under "Refused YAML" in SAFO's `docs/board-yaml.md`. Errors carry the file name and, where PyYAML provides it, the line.
- [ ] **Step 4: Run tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add a hardened YAML loader`.

### Task 3: Site config models

**Files:**
- Create: `src/reach_around/config/models.py`, `tests/config/test_models.py`

**Interfaces:**
- Consumes: `load_yaml`, `ConfigError` (Task 2).
- Produces: pydantic models `Link`, `Section`, `Tab`, `Social`, `Creator`, `SiteConfig`; `load_site_config(path: Path) -> SiteConfig`.
  - `Link(id: str, title: str, url: str, subtitle: str | None = None, icon: str | None = None, affiliate: bool = False, show_from: date | None = None, show_until: date | None = None)`
  - `Section(id: str, title: str, subtitle: str | None = None, collapsed: bool = True, links: list[Link])`
  - `Tab(id: str, title: str, blocks: list[Link | Section])`
  - `Social(name: str, url: str, icon: str)`; `Creator(name: str, avatar: str | None, socials: list[Social])`
  - `SiteConfig(version: Literal[1], creator: Creator, tabs: list[Tab], disclosure: str | None, timezone: str = "America/New_York")`

- [ ] **Step 1: Write failing tests** with the spec's exact values: `test_https_urls_only` (a `javascript:alert(1)` and a `data:text/html,x` URL each raise), `test_ids_are_slugs` (`^[a-z0-9][a-z0-9-]{0,39}$`; `"Has Space"` and `"../x"` raise), `test_ids_are_unique_across_the_whole_site`, `test_show_until_not_before_show_from`, `test_at_least_one_tab`, `test_unknown_keys_refused` (pydantic `extra="forbid"`), `test_unknown_timezone_refused`, `test_example_config_loads` (loads `examples/instance/site.yaml`).
- [ ] **Step 2: Run** `pytest tests/config/test_models.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the models and `load_site_config(path)`, which calls `load_yaml` then `SiteConfig.model_validate`, converting pydantic errors into `ConfigError` that carries the dotted key path.
- [ ] **Step 4: Create `examples/instance/site.yaml`**: synthetic creator "Example Creator", two tabs, one collapsible section, one affiliate link, one scheduled link. No real accounts or addresses.
- [ ] **Step 5: Run tests.** Expected: PASS.
- [ ] **Step 6: Commit** `feat: add validated site config models`.

### Task 4: Config reference and education page

**Files:**
- Create: `docs/config.md`, `docs/education/config-hardening.md`

- [ ] **Step 1: Write `docs/config.md`**: every field in Task 3, its type, default and an example, with the refusal rules from Task 2.
- [ ] **Step 2: Write `docs/education/config-hardening.md`**: why a hardened loader refuses duplicate keys, aliases and merge keys, why URLs are https-only, and which test proves each. Plain language, for readers learning from the project.
- [ ] **Step 3: Verify** every code sample in the docs loads: add `tests/test_docs_examples.py::test_config_doc_example_validates`, which extracts the first fenced `yaml` block from `docs/config.md` and loads it with `load_site_config`. Run `pytest tests/test_docs_examples.py -v`. Expected: PASS.
- [ ] **Step 4: Commit** `docs: add the config reference and hardening page`.

### Task 5: Schedule visibility

**Files:**
- Create: `src/reach_around/site/__init__.py`, `src/reach_around/site/visibility.py`, `tests/site/test_visibility.py`

**Interfaces:**
- Consumes: `Link` (Task 3).
- Produces: `is_visible(link: Link, today: date) -> bool`; `visible_blocks(blocks: Sequence[Link | Section], today: date) -> list[Link | Section]` (drops a section whose links are all hidden).

- [ ] **Step 1: Write failing tests**: `test_visible_on_show_from_day`, `test_visible_through_show_until_day`, `test_hidden_the_day_after_show_until`, `test_hidden_before_show_from`, `test_no_dates_always_visible`, `test_section_with_no_visible_links_is_dropped`, and `test_eastern_day_across_dst_change` which asserts that the caller-supplied `today` is used as is. The conversion from a UTC instant to the Eastern date happens in `today_in(timezone: str, now: datetime) -> date`; test it at the spring change (`2027-03-14T04:59:00Z` is still the 13th, `2027-03-14T05:00:00Z` is the 14th) and the fall change (`2026-11-01T03:59:00Z` is still Oct 31, `2026-11-01T04:00:00Z` is Nov 1). Eastern is UTC-5 in winter and UTC-4 in summer, so midnight Eastern falls at those instants.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `is_visible`, `visible_blocks` and `today_in` using `zoneinfo`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add schedule-aware link visibility`.

### Task 6: Static site builder

**Files:**
- Create: `src/reach_around/site/build.py`, `src/reach_around/site/templates/base.html`, `tab.html`, `src/reach_around/site/static/site.css` (minimal for now; Task 7 designs it), `tests/site/test_build.py`, `tests/site/golden/`

**Interfaces:**
- Consumes: `SiteConfig`, `visible_blocks`.
- Produces: `build_site(config: SiteConfig, out_dir: Path, *, today: date) -> BuildManifest`; `@dataclass(frozen=True) class BuildManifest: links: dict[str, str]` mapping link id to destination URL; the builder also writes `manifest.json` into `out_dir`. CLI: `reach-around build --config PATH --out DIR`.

- [ ] **Step 1: Write failing tests**: `test_each_tab_has_its_own_page` (tab `socials` renders to `socials/index.html`, the first tab to `index.html`); `test_html_in_titles_is_escaped` (a title of `<script>alert(1)</script>` appears as `&lt;script&gt;` and no `<script>` tag appears in any page except the optional click script); `test_page_works_without_javascript` (no `<script>` in output except the one external `click.js` tag, and the page's links all have real `href`s); `test_affiliate_links_get_rel_sponsored_noopener_and_the_disclosure_is_shown`; `test_sections_use_details_summary`; `test_scheduled_link_hidden_by_today`; `test_manifest_lists_only_visible_links`; `test_golden_output_for_example_config` (compare to `tests/site/golden/`; regenerate with `pytest --update-golden`, implemented as a flag in `conftest.py`).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement `build_site`** with a Jinja2 environment using `autoescape=True` and `StrictUndefined`, rendering one page per tab, writing `static/` files and `manifest.json`. Add the `build` subcommand to `cli.py`, with `today` taken from `today_in(config.timezone, now)`.
- [ ] **Step 4: Run** `pytest tests/site -v`. Expected: PASS.
- [ ] **Step 5: Add `docs/education/escaping-and-no-js.md`** explaining autoescape, rel attributes and why the page needs no JavaScript.
- [ ] **Step 6: Commit** `feat: render the static link page from config`.

### Task 6A: Privacy page and cookie disclosure

**Files:**
- Create: `src/reach_around/site/privacy.py`, `src/reach_around/site/templates/privacy.html`, `src/reach_around/site/templates/_footer.html`, `tests/site/test_privacy.py`, `docs/education/privacy-by-construction.md`
- Modify: `src/reach_around/config/models.py` (add `Features` and `Privacy`), `templates/base.html`, `build.py`, `docs/config.md`

**Interfaces:**
- Consumes: `SiteConfig` (Task 3).
- Produces: `Features(click_tracking: bool = True, kit_gate: bool = False)`; `Privacy(contact: str | None = None, intro: str | None = None, retention_days: int = 90, lead_retention_days: int = 365)`; `SiteConfig.features: Features` and `SiteConfig.privacy: Privacy`; `collected_items(config: SiteConfig) -> list[Disclosure]` with `Disclosure(what: str, why: str, kept: str, sensitive: bool)`; `sets_cookies(config: SiteConfig) -> bool`.

- [ ] **Step 1: Write failing tests**: `test_default_disclosure_lists_click_counts_and_says_no_ip_address` (click tracking on: one item whose text contains "country" and "no IP address"); `test_no_item_is_marked_sensitive`; `test_no_cookies_when_gate_off` (`sets_cookies` is `False`, the built pages contain no `document.cookie` and no cookie banner); `test_gate_on_adds_email_and_cookie_items_and_renders_the_banner_with_preferences_reject_accept` (the gate item names the email, the retention in days and the cookie's purpose); `test_banner_text_comes_from_config_when_set` (an `intro` of `"Cookies, sadly not the chocolate chip kind."` appears escaped); `test_every_page_links_to_privacy_in_the_footer`; `test_privacy_page_states_the_retention_numbers_from_config`.
- [ ] **Step 2: Run** `pytest tests/site/test_privacy.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `collected_items` and `sets_cookies` from the enabled features, render `/privacy/index.html` and the footer link on every page, and render the cookie banner partial only when `sets_cookies` is true. The default wording is neutral and plain; a creator replaces it through `privacy.intro`. The maintainer's own wording lives in his private instance config, never as the engine default.
- [ ] **Step 4: Add the education page** `docs/education/privacy-by-construction.md`: why the disclosure is generated, what is and is not collected, and how the test stops the page from claiming less than the site does.
- [ ] **Step 5: Run** `pytest tests/site -v`. Expected: PASS.
- [ ] **Step 6: Commit** `feat: generate the privacy disclosure from enabled features`.

### Task 6B: Theme in the config

**Files:**
- Modify: `src/reach_around/config/models.py`, `templates/base.html`, `static/site.css`
- Create: `src/reach_around/site/theme.py`, `tests/site/test_theme.py`

**Interfaces:**
- Consumes: `SiteConfig` (Task 3).
- Produces: `Theme(colors: dict[str, str] = {}, fonts: FontStack, background: str | None = None, column_width: str = "560px", radius: str = "1rem", button_style: Literal["pill", "rounded", "square"] = "pill", mode: Literal["auto", "light", "dark"] = "auto")`; `SiteConfig.theme: Theme`; `theme_css(theme: Theme) -> str` returning a `:root { --ra-... }` block.

- [ ] **Step 1: Write failing tests**: `test_color_values_are_validated` (hex, `rgb()`, `hsl()` and named colors pass; `red; background:url(x)`, `}`, `url(`, `expression(` and any `;` or `{` raise `ConfigError`); `test_only_known_token_names_are_emitted` (an unknown color key is refused); `test_fonts_are_system_stacks_or_local_files` (an `https://` font URL is refused, because no third-party font hosts means no visitor data leaks; a path inside the instance directory is allowed); `test_background_is_https_or_relative_asset`; `test_theme_css_overrides_defaults_in_the_built_page`; `test_mode_dark_forces_dark_tokens`.
- [ ] **Step 2: Run** `pytest tests/site/test_theme.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the models and `theme_css`, emitting only an allowlisted set of custom-property names, and render the block in `base.html` as an external `theme.css` file (no inline style).
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: make the look configurable through a validated theme`.

### Task 6C: Layouts and block types

**Files:**
- Modify: `src/reach_around/config/models.py`, `templates/tab.html`, `static/site.css`
- Create: `src/reach_around/site/markdown.py`, `templates/blocks/*.html`, `tests/site/test_blocks.py`

**Interfaces:**
- Consumes: `Tab`, `Section`, `Link`.
- Produces: new block models `Text(id: str, markdown: str)`, `Image(id: str, src: str, alt: str, link: str | None = None)`, `Divider(id: str)`; `Tab.layout` and `Section.layout: Literal["list", "grid", "cards"] = "list"`; `Creator.header: Literal["compact", "centered", "hero"] = "compact"`; `render_markdown(text: str) -> str` supporting only bold, italic, https links, lists and line breaks.

- [ ] **Step 1: Write failing tests**: `test_markdown_escapes_raw_html` (`<script>` and `<img onerror=...>` come out escaped); `test_markdown_links_must_be_https`; `test_image_requires_alt_text`; `test_image_src_is_https_or_relative_asset`; `test_unknown_layout_or_header_is_refused`; `test_grid_and_cards_layouts_render_the_same_links_as_list` (same hrefs, different container class); `test_block_ids_stay_unique_across_the_site`; `test_golden_output_for_each_header_variant`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** the models, the restricted markdown renderer (no raw HTML pass-through) and the block templates. Every new block must keep the page working without JavaScript.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add text, image and divider blocks and layout variants`.

### Task 6D: Instance overrides and a strict CSP

**Files:**
- Modify: `src/reach_around/site/build.py`, `src/reach_around/cli.py`
- Create: `src/reach_around/site/overrides.py`, `tests/site/test_overrides.py`

**Interfaces:**
- Produces: CLI option `reach-around build --overrides DIR`; `make_environment(overrides: Path | None) -> jinja2.Environment` that looks in `DIR/templates` before the packaged templates; an optional `DIR/custom.css` appended after the theme; the build writes a `_headers` file with a strict Content-Security-Policy.

- [ ] **Step 1: Write failing tests**: `test_override_template_wins_and_missing_ones_fall_back`; `test_override_name_cannot_escape_the_directory` (`../x.html` and an absolute path are refused); `test_custom_css_is_appended_after_the_theme`; `test_no_custom_javascript_is_loaded` (a `custom.js` in the overrides directory is ignored and the build warns); `test_headers_file_has_a_strict_csp` (contains `default-src 'self'`, `script-src 'self'`, `style-src 'self'`, `frame-ancestors 'none'`, `base-uri 'none'`, `form-action 'self'` and contains neither `unsafe-inline` nor `unsafe-eval`); `test_pages_use_no_inline_style_or_script`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** the overrides loader and the `_headers` writer. Custom JavaScript is deliberately not supported in v0.1: it would weaken the CSP, and the phase 3 gate will need its own explicit, narrow relaxation for the Turnstile script.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: support instance template overrides and ship a strict CSP`.

### Task 6E: Local preview server

**Files:**
- Create: `src/reach_around/preview.py`, `src/reach_around/site/templates/_preview.html` (never part of a production build), `tests/test_preview.py`
- Modify: `src/reach_around/cli.py`

**Interfaces:**
- Produces: `reach-around serve --config PATH [--overrides DIR] [--port 8000]`; `serve(config: Path, overrides: Path | None, host: str = "127.0.0.1", port: int = 8000) -> None`. Rebuilds when the config, theme, templates or overrides change, and serves `/__preview`, a page showing the site at desktop and 390 px phone width side by side.

- [ ] **Step 1: Write failing tests**: `test_serve_refuses_a_non_loopback_host` (`0.0.0.0` and a LAN address raise); `test_rebuilds_when_the_config_changes` (touch and edit the config; the served page reflects it within 2 seconds); `test_preview_page_is_not_in_a_production_build` (the output of `build` has no `__preview`); `test_path_traversal_returns_404` (`/../../etc/passwd`, `/%2e%2e/`); `test_a_broken_config_shows_the_error_instead_of_crashing`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** with the standard library `http.server`, bound to loopback only, polling file modification times. This is a local development tool; it is never deployed and never public.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add a loopback-only live preview`.

### Task 6F: Customization guide and examples

**Files:**
- Create: `docs/customizing.md`, `examples/instance-variants/{minimal,dark-hero,grid-cards}/site.yaml`, `tests/test_example_variants.py`

- [ ] **Step 1: Write the failing test** `test_every_example_variant_validates_and_builds`: loads each variant, builds it, and asserts the escaping, no-inline-script and no-third-party-host checks from Task 6 hold for every output page.
- [ ] **Step 2: Run.** Expected: FAIL (no variants).
- [ ] **Step 3: Write the variants and `docs/customizing.md`**: the layers from least to most work (edit links, change the theme, change layouts, override templates and CSS), the preview command, and what is deliberately not allowed (custom JavaScript, third-party fonts and scripts) and why.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `docs: add the customization guide and example variants`.

### Task 7: Design pass (visual review gate)

**Files:**
- Modify: `src/reach_around/site/static/site.css`, `templates/*.html`
- Create: `docs/design.md`, `tests/site/test_layout.py`

This task is reviewed by the maintainer by screenshot before it merges: it is the "make it pop" requirement.

- [ ] **Step 1: Write failing layout tests** (`pytest` with Playwright, marked `visual`, skipped unless `--run-visual`): `test_no_horizontal_scroll_at_360px` (`document.documentElement.scrollWidth <= 360`), `test_tab_bar_never_clips_a_tab_at_360px` (every tab's bounding box is inside the viewport or the bar is horizontally scrollable with a visible edge cue), `test_desktop_column_is_centered_at_1440px`, `test_contrast_meets_wcag_aa` (computed text and background of the link button and the tab bar).
- [ ] **Step 2: Run** `pytest tests/site/test_layout.py --run-visual -v`. Expected: FAIL.
- [ ] **Step 3: Design** the default look through the theme tokens from Task 6B (so every choice is something another creator can change), with a dark-mode override under `prefers-color-scheme`, two shipped presets (`dark-hero` and `light-clean`), a desktop column of about 560 px with artwork in the margins, full width on phone, and the icon row wrapping cleanly. Use the frontend-design guidance. Document tokens in `docs/design.md`.
- [ ] **Step 4: Run** the visual tests. Expected: PASS. Take screenshots at 1440 px and 390 px of each tab and attach them to the PR for the maintainer.
- [ ] **Step 5: Commit** `feat: design pass for the link page`.

### Task 8: Worker scaffold and click endpoint

**Files:**
- Create: `worker/package.json`, `worker/wrangler.toml`, `worker/tsconfig.json`, `worker/migrations/0001_click_events.sql`, `worker/src/index.ts`, `worker/src/click.ts`, `worker/test/click.test.ts`

**Interfaces:**
- Consumes: the build manifest's link ids, provided to the Worker as the secret-free variable `LINK_IDS` (a JSON array of strings).
- Produces: `POST /e/c/:linkId` handled by `handleClick(request: Request, env: Env): Promise<Response>`, which always returns `204` with an empty body. Table `click_events(id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, link_id TEXT NOT NULL, country TEXT)` with an index on `ts`.

- [ ] **Step 1: Write failing tests** in `worker/test/click.test.ts` using vitest with the Workers pool and a local D1: `records a click for a listed link id` (one row, `link_id` equal, `country` two letters or null, no IP column exists); `unknown id records nothing and returns 204`; `a URL passed as an id records nothing`; `empty id records nothing`; `bot user agent records nothing` (`Googlebot/2.1`, `curl/8`, and a missing UA); `wrong origin records nothing` (the request's `Ping-From` or `Origin` host is not the configured `SITE_HOST`); `burst from one client is limited` (the 21st request inside a minute from one client key records nothing, using the Workers rate-limiting binding or a test double); `response is identical for accepted and ignored requests` (same status, same headers that matter, empty body).
- [ ] **Step 2: Run** `npm test` in `worker/`. Expected: FAIL.
- [ ] **Step 3: Implement** `handleClick` and the router in `index.ts`, applying the checks in this order: route and method, id allowlist, user agent, origin, rate limit, insert. Every path returns the same `204`. Add the migration and `wrangler.toml` with the D1 binding, `SITE_HOST` and `LINK_IDS` variables, and a cron trigger placeholder for retention (implemented in Task 9).
- [ ] **Step 4: Run** `npm test`. Expected: PASS.
- [ ] **Step 5: Write `docs/education/click-endpoint.md`**: what is and is not stored, why there is no redirect route, how the allowlist and rate limit work.
- [ ] **Step 6: Commit** `feat: add the write-only click endpoint`.

### Task 9: Click retention and the site's tracking attributes

**Files:**
- Create: `worker/src/retention.ts`, `worker/test/retention.test.ts`, `src/reach_around/site/static/click.js`, `tests/site/test_click_attrs.py`
- Modify: `worker/wrangler.toml`, `templates/tab.html`

**Interfaces:**
- Consumes: Task 8 route and `click_events`.
- Produces: `purgeOldEvents(env: Env, nowMs: number, keepDays?: number): Promise<number>` (default 90 days, returns deleted rows).

- [ ] **Step 1: Write failing tests**: worker `deletes events older than 90 days and keeps newer ones` and `keepDays is configurable`; site `every outbound link has a ping attribute pointing at /e/c/<id>` and `links still carry their real href`, and `click.js is a single external file that sends a beacon only for links whose browser lacks ping support`.
- [ ] **Step 2: Run** worker and site tests. Expected: FAIL.
- [ ] **Step 3: Implement** `purgeOldEvents`, a scheduled handler in `index.ts`, the `ping` attribute in the templates, and `click.js` as progressive enhancement (feature-detect `ping`; fall back to `navigator.sendBeacon`). Links must work with the Worker unreachable.
- [ ] **Step 4: Run** both test suites. Expected: PASS.
- [ ] **Step 5: Commit** `feat: purge old clicks and wire tracking attributes`.

### Task 10: Container image (Docker and Podman)

**Files:**
- Create: `Containerfile`, `.dockerignore`, `compose.yaml`, `deploy/podman/reach-around.container`, `docs/containers.md`, `tests/test_container_smoke.sh`
- Modify: `.github/workflows/ci.yml` (turn on `docker-build: true`, `dockerfile: Containerfile`, add a `podman` job), `README.md` status table

**Interfaces:**
- Produces: an image whose entry point is `reach-around`; running it with `build --config /config/site.yaml --out /out` produces the site.

- [ ] **Step 1: Write the smoke test** `tests/test_container_smoke.sh`: builds with `${CONTAINER_ENGINE:-docker}`, runs it read-only as a non-root user with `examples/instance` mounted at `/config` and an empty `/out`, and asserts that `/out/index.html` exists, the process exit code is `0`, and `id -u` inside the container is not `0`.
- [ ] **Step 2: Run** `CONTAINER_ENGINE=docker bash tests/test_container_smoke.sh`. Expected: FAIL (no `Containerfile`).
- [ ] **Step 3: Implement** a multi-stage `Containerfile`: pinned base image by digest, hashed dependency install, non-root user, no Docker-socket use, writable paths limited to `/out` and `/tmp`. Add `compose.yaml` with `read_only: true`, and a Podman Quadlet `.container` unit. `docs/containers.md` shows `docker compose`, `podman compose`, and the Quadlet install.
- [ ] **Step 4: Run** the smoke test with `docker`, then with `podman`. Expected: both PASS. Add the `podman` CI job that runs the same script.
- [ ] **Step 5: Commit** `feat: add a rootless Docker and Podman image`.

### Task 11: Deploy to the test subdomain

**Files:**
- Create: `.github/workflows/deploy-test.yml`, `docs/deploy.md`

The maintainer owns the Cloudflare account steps. The workflow and docs are ours.

- [ ] **Step 1: Write `docs/deploy.md`**: the exact one-time steps for the maintainer (create the Pages project and D1 database, create a scoped API token with only the permissions the workflow needs, choose the test hostname, add the DNS record) and what each setting does.
- [ ] **Step 2: Implement `deploy-test.yml`** as a manual `workflow_dispatch`: build the site, apply D1 migrations, deploy the Worker and `wrangler pages deploy` the site. Permissions are `contents: read`. The Cloudflare token comes from a repository secret, pinned actions are hash-pinned, and the workflow refuses to run from any ref other than `main`.
- [ ] **Step 3: Verify** with `actionlint` (and zizmor through GYST's workflow lint): `actionlint .github/workflows/deploy-test.yml`. Expected: no findings.
- [ ] **Step 4: Commit** `ci: add a manual deploy to the test subdomain`.

### Task 12: Release v0.1.0

**Files:**
- Modify: `pyproject.toml` and `src/reach_around/__init__.py` (version `0.1.0`), `CHANGELOG.md`, `README.md` status table
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Write the failing check**: `tests/test_version.py::test_version_matches_pyproject` reads `pyproject.toml` and asserts equality with `__version__`.
- [ ] **Step 2: Implement** the version bump and `CHANGELOG.md`, and add `release.yml` calling GYST `container-release.yml` and `verify-published.yml` pinned to the v1.24.0 commit. The workflow runs on a `v*` tag.
- [ ] **Step 3: Verify** CI is green on `main`. Tag `v0.1.0` (the maintainer has authorized tagging once green) and confirm the published image runs the smoke test under both Docker and Podman.
- [ ] **Step 4: Commit** `chore: release v0.1.0`.

---

## Self-review notes

- Spec coverage: the privacy disclosure and cookie notice (6A); the link page, tabs, collapsible sections, affiliate disclosure, schedule (Tasks 3, 5, 6, 7); click tracking and the write-only Worker (8, 9); Docker and Podman, versions and tags (10, 12); Cloudflare Pages on a test subdomain (11); CI on GYST (1); education pages (4, 6, 8). Later spec sections (collectors, store, gate, kit, PDF, sinks, exports) are in the roadmap, not here.
- One deliberate change from the spec: clicks are tracked with the `ping` attribute plus a beacon fallback, not a redirect route. This removes the open-redirect risk and keeps links working if the Worker is down. The spec amendment to record this is in the roadmap.
