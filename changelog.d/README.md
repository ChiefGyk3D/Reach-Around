<!-- SPDX-License-Identifier: Apache-2.0 -->
# Changelog fragments

Every pull request adds exactly one file here, so parallel pull requests never edit the
same lines of `CHANGELOG.md`.

- Name: `<issue-number>-<short-slug>.md`, for example `736-changelog-fragments.md`
  (digits, a dash, lowercase words joined by dashes).
- Content: one entry in the existing style. It starts with `- `, names the issue and
  epic (`(#736, epic #246)`), is plain language, and ends with a newline. Wrap nothing;
  a sub-list is indented lines under the entry. The entry must mention its own issue
  number.
- One entry per file, one fragment per issue number. A malformed fragment or a repeated
  issue number fails `scripts/changelog-assemble.py`.
- No fragment needed? Put a line `no-changelog: <reason>` in the pull request body (or
  use the `no-changelog` label). The release pull request does this too.

Do not edit `## [Unreleased]` in `CHANGELOG.md`; it holds only a pointer comment. The
release step folds these files into the new version section (see
[docs/releasing.md](../docs/releasing.md)) and deletes them.
