# Destination Kind Tabs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry the closed `link` or `social_icon` destination kind from the site build through click collection and rollups, then show independently floored Links and Social icons report tabs.

**Architecture:** The private build manifest records a kind for every counted destination. The Worker validates that closed vocabulary in `SITES`, defaults legacy destinations to `link`, and writes the kind beside the destination in raw and marginal aggregate tables. The report loader accepts the field, defaults older exports to `link`, and splits the existing click table into two independently suppressed sections rendered from the floored report model.

**Tech Stack:** Python 3.12, pytest, TypeScript, Cloudflare Workers D1, Vitest, inline owner-report HTML.

**Spec:** Issue #438 and its lead notes in the task request.

## Global Constraints

- Tests are written and observed failing before production changes.
- Destination kind is a closed vocabulary: `link`, `social_icon`.
- Existing exports and legacy Worker variables remain readable as `link`.
- Each tab applies the existing floor and complementary suppression independently.
- Owner-report data never enters the public site output.
- Synthetic data only, American English, dark mode only.

## Review Focus

- An unknown destination kind is refused rather than folded into a valid tab.
- Social icons cannot be mislabeled as regular links by the generated manifest.
- Raw rows and every source/country daily and monthly rollup preserve the same kind.
- A below-floor social icon never becomes visible through the link tab or HTML.
- Old exports without the new column remain compatible and appear under Links.

---

### Task 1: Build manifest destination kinds

**Files:** `tests/site/test_build.py`, `src/reach_around/site/build.py`

- [ ] Add a failing build test proving regular, platform/social, and profile-card destinations receive the correct closed kind.
- [ ] Run the targeted test and record the expected failure.
- [ ] Add `destination_kinds` to the private build manifest and JSON.
- [ ] Run the targeted test green.

### Task 2: Worker collection and rollups

**Files:** `worker/test/views/sites.test.ts`, `worker/test/click.test.ts`, `worker/test/click_funnel.test.ts`, `worker/src/sites.ts`, `worker/src/click.ts`, `worker/src/clickfunnel.ts`, `worker/migrations/0016_destination_kind.sql`

- [ ] Add failing tests for closed-vocabulary validation, collection, and daily/monthly rollup preservation.
- [ ] Run the targeted Vitest files and record the expected failures.
- [ ] Add the migration and minimal Worker changes, defaulting legacy configuration to `link`.
- [ ] Run the targeted Worker tests green.

### Task 3: Floored report tabs

**Files:** `tests/test_owner_report.py`, `tests/test_report.py`, `src/reach_around/funnel/export.py`, `src/reach_around/funnel/report.py`, `src/reach_around/funnel/owner.py`, `src/reach_around/funnel/htmlout.py`, `src/reach_around/funnel/csvout.py`

- [ ] Add failing tests proving independent floors, HTML tabs, CSV parity, unknown-kind refusal, and old-export compatibility.
- [ ] Run the targeted pytest tests and record the expected failures.
- [ ] Carry kind through validation and split the floored report sections without reading raw data in the renderer.
- [ ] Run targeted tests green and perform a revert proof for every new behavior test.

### Task 4: Documentation and verification

**Files:** `docs/report.md`, `docs/analytics-design.md`, `CHANGELOG.md`

- [ ] Document the closed field, migration/export shape, compatibility, and tab suppression.
- [ ] Run targeted Python and Worker tests, full pytest, Ruff checks, format check, and plain mypy.
- [ ] Report exact results and any remaining issue slices.
