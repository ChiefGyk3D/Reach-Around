<!-- SPDX-License-Identifier: Apache-2.0 -->
# Wiki sources

This folder holds the page sources for the public Reach Around wiki on GitHub. The files here are the source of truth. Pages are reviewed in pull requests like code, and then copied to the wiki. Do not edit pages in the GitHub wiki editor, because those edits skip review and the next copy will overwrite them.

Tracked in epic #795. The first pages came from issue #802.

## How the sources work

- One Markdown file per page. The file name is the page name: `Glossary.md` is the page "Glossary".
- Link between pages by page name, without the extension and without a folder: `[Glossary](Glossary)`. Link to a heading with `[text](Concepts#how-it-stays-up)`.
- `_Sidebar.md` is the wiki sidebar. Link only pages that exist. List planned pages as plain text, and turn them into links in the same pull request that adds them.
- Plain Markdown only. No HTML beyond comments, no images yet, no scripts. Plain Markdown stays readable in any theme, including dark ones.
- Every file starts with the SPDX comment line, as other files in the repository do.
- Say whether a feature is on the main branch. Describe what exists as present. Describe what only an open draft pull request adds as "planned". The project is pre-release and nothing is deployed, and every page must say so where it matters.
- Mark any statement you cannot check in the repository with an HTML comment: `<!-- TODO-verify: what needs checking -->`. A reviewer resolves each one before the page is published.

## Scrub rules

A page must not contain any of the following. Check each before you open a pull request.

1. Anything from private security work: details of abuse controls, thresholds, counters or limits for measurement, or how the bot check works. The wiki says only that measurement has limits and a configurable bot check.
2. Any list or count of unfixed findings, open threats or audit items. Say that hardening is tracked, and nothing more.
3. Hostnames. The only hosts allowed are github.com/ChiefGyk3D/Reach-Around and chiefgyk3d.com. Use `example.com` and `links.example.com` in examples.
4. A real amateur radio callsign or grid square. If an example needs one, use N0CALL, N0TST or FN31pr.
5. An employer name, a work account name, or any personal detail about a contributor.
6. Secrets and identifiers: tokens, account ids, database ids, zone ids, keys, wallet addresses and the names of real secret-manager projects.
7. Private repository names or contents, internal board links, and agent or chat session links.
8. Real visitor, lead or partner data, rates, affiliate identifiers and partner names.
9. Machine names, serial numbers and local paths. Use neutral paths.
10. Claims about another company or product. A comparison is a separate page that needs sources and review.

Style: American English, plain and specific sentences, no marketing tone, no em dashes, no emojis.

## Propose a page

1. Find or open an issue under epic #795 that names the page. Every change needs an issue first.
2. Branch from `main` and add or edit files in this folder only.
3. Write from the repository's own docs and code, and read the code when a doc looks old. Mark anything you cannot verify with a TODO-verify comment.
4. Update `_Sidebar.md` so the page is linked.
5. Open a pull request, link the issue with `Refs #NNN`, and add a line `no-changelog: wiki sources only` to the body, since these files change no behavior.
6. A reviewer checks the scrub rules, the links and every TODO-verify comment. Security and privacy pages get an extra security review before they publish.
