# Page Layout Variants Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four page-level review layouts and one build mode that publishes every layout beneath `/v/<layout>/`, while preserving classic build output exactly.

**Architecture:** Keep the existing classic renderer as the default and an untouched branch. Non-classic layouts consume tabs as ordered intent groups and render one grouped page through a dedicated template, stylesheet, and small tabs enhancement script. A build orchestration function copies complete variant builds beneath stable review paths without changing the underlying single-layout builder.

**Tech Stack:** Python 3.11, Pydantic 2, Jinja2, YAML, HTML, CSS, browser JavaScript, pytest.

**Spec:** Issue #556 as supplied in the user request on 2026-11-10.

## Global Constraints

- `classic` is the default and its generated files remain byte-identical.
- Dark-only variants, WCAG AA, mobile first, 44 CSS px targets, 8 px spacing, safe-area support, DOM and visual order aligned.
- No cookies, external requests, inline code, or third-party scripts; honor reduced motion.
- Reuse tabs, blocks, links, icons, tokens, and frame behavior.
- Tests precede implementation and each behavior has revert proof.
- Do not commit, push, or use the network.

## Review Focus

- A classic config with no `page` object must produce an identical complete output tree.
- A variant config with unsupported block types must fail clearly rather than silently drop content.
- Side-by-side roots and fingerprinted relative asset links must resolve at every `/v/<layout>/` depth.
- Tabs must expose all content when JavaScript is absent and maintain keyboard focus behavior when enhanced.
- Group size, Watch parity, configured order, and status fields must be validated at the config boundary.

---

### Task 1: Config contract and schema

**Files:**
- Modify: `src/reach_around/config/models.py`
- Test: `tests/config/test_page_variants.py`
- Regenerate: `docs/site.schema.json`

**Interfaces:**
- Produces: frozen `Page` and `PageStatus` models, `page.layout`, and validation for review-layout tab groups.
- Consumes: existing `Tab`, `Link`, `Section`, title, subtitle, and slug types.

- [ ] Write validation/default tests and verify they fail because `page` is unknown.
- [ ] Implement the minimal models and validators.
- [ ] Run the config tests and regenerate/check the schema.

### Task 2: Variant rendering and accessible assets

**Files:**
- Modify: `src/reach_around/site/build.py`, `src/reach_around/site/templates/base.html`, `src/reach_around/site/static/site.css`
- Create: `src/reach_around/site/templates/variants.html`, `src/reach_around/site/static/variants.css`, `src/reach_around/site/static/variants.js`
- Test: `tests/site/test_page_variants.py`
- Golden: `tests/site/golden_variants/**`

**Interfaces:**
- Consumes: `config.page`, visible tabs/blocks, existing link macro and asset fingerprinting.
- Produces: grouped stack, chips, bento, and progressively enhanced tabs HTML.

- [ ] Write semantic, security, responsive-source, asset, and golden tests; verify missing-feature failures.
- [ ] Add the dedicated rendering branch, template, CSS, and tabs script.
- [ ] Run targeted tests and confirm classic output snapshot stays identical.

### Task 3: Side-by-side build mode

**Files:**
- Modify: `src/reach_around/site/build.py`, `src/reach_around/cli.py`
- Test: `tests/site/test_page_variants.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `build_site` plus `Page.layout` model copies.
- Produces: `build_variants(...)` and `reach-around build --all-layout-variants` writing `/v/classic/`, `/v/stack/`, `/v/chips/`, `/v/bento/`, `/v/tabs/`.

- [ ] Write API and CLI output-tree tests and verify the option/function is missing.
- [ ] Implement safe staging/orchestration and CLI plumbing.
- [ ] Run targeted API and CLI tests.

### Task 4: Synthetic example and documentation

**Files:**
- Create: `examples/layout-variants/site.yaml`
- Modify: `docs/config.md`, `docs/design.md`, `CHANGELOG.md`
- Test: `tests/test_docs_examples.py`, `tests/test_example_variants.py`

**Interfaces:**
- Consumes: public config and CLI added above.
- Produces: documented future-live-badge hook and an owner-review example.

- [ ] Add example/build tests first and verify the missing example fails.
- [ ] Add synthetic American English example and documentation.
- [ ] Run documentation/example tests.

### Task 5: Verification and revert proof

**Files:** all changed files.

- [ ] Run new tests with the implementation, temporarily reverse each implementation slice, prove its tests fail, and restore it.
- [ ] Run `tests/site`, config/schema tests, full pytest, Ruff check, Ruff format check, and mypy with the required environment and in-worktree basetemp.
- [ ] Review the final diff against every issue requirement and report any gap.
