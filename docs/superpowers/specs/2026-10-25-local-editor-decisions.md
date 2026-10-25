# Local editor: the maintainer's decisions

Answers to the decision list (section 17) of [the design](2026-10-25-local-editor-design.md), given on 2026-10-24. The build follows these. Where an answer differs from the design's recommended default, it says so and names what else must change.

| ID | Question | Decision |
|---|---|---|
| D-01 | How a request proves it comes from the owner's tab | Session token in the URL fragment, kept only in memory, sent as the `X-RA-Token` header on every request |
| D-02 | Open the browser by itself | Never. `--open` is opt-in; tests never use it |
| D-03 | Port | A random free port each run |
| D-04 | Preview serving | On a second port, so the preview never shares an origin with the editor |
| D-05 | Preview frame sandbox | `sandbox="allow-scripts"` only, without `allow-same-origin` |
| D-06 | Remote images in the preview | Blocked by default, with an allow flag |
| D-07 | Undo history location | A history folder beside the config, gitignored, owner-only permissions |
| D-08 | Photo metadata on upload | **Strip automatically.** This differs from the recommended default (warn only) and from threat-model decision D-08 (warn, do not strip); see below |
| D-09 | Undo after an outside edit | Refuse, with a "restore anyway" button that snapshots the outside version first |
| D-10 | Idle timeout | 30 minutes |
| D-11 | Rate limits | 20 changes and 5 uploads per minute (tune after real use) |
| D-12 | Diff panel | Later, not in the first version |
| D-13 | Platforms | Linux and macOS; no Windows for now |
| D-14 | Test tools | Hypothesis, and axe-core as a vendored, hashed, test-only file |
| D-15 | CSP `sandbox` directive on preview pages | Try it; keep it only if the preview still works in the browsers checked |
| D-16 | Non-technical tester | A real non-technical person the maintainer names before phase 2; no name goes in any repository file |
| D-17 | Launcher | One documented command first; a packaged launcher is a separate issue |
| D-18 | Hosted editor | None from this design |

## What D-08 changes

The design recommended warning about location and camera data in uploaded photos and not rewriting the file. The maintainer chose to strip metadata automatically. To keep the rest of the design true:

- Stripping must be done by a narrow, tested routine for the formats the asset pipeline already accepts (JPEG, PNG, WebP), removing EXIF, XMP and IPTC data and nothing else, writing a new file under a server-chosen name, never modifying the original in place.
- It must not silently change pixels, color profile intent or orientation: apply the orientation to the pixels first, or keep the orientation tag and strip only the identifying tags. The tests must compare the decoded image before and after.
- A new dependency is allowed only if it is pure Python or already vendored and license-checked; otherwise write a minimal stripper for each format. Decide in the plan; record the choice here.
- The threat-model row that said "warn, do not strip" must be updated when the upload phase is built, and the upload UI must tell the user what was removed.
- Strip on upload only; images already referenced by the config are not rewritten.

## Order of work

Unchanged from the design's plan outline: core edit operations, asset-slot hook and history store, server skeleton and guard tests, state and operation routes, UI shell, forms, undo and stale handling, browser tests with the no-navigation guard, docs, a cyber-tier security review, release.
