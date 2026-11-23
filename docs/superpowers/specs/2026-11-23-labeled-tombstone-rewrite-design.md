<!-- SPDX-License-Identifier: Apache-2.0 -->
# Labeled tombstone-only rewrite of the counts history: design

Status: DRAFT for owner decision, 2026-11-23. Spec only: no code and no workflow
change come with this document. Issue: #619. Epic: #364. Follows #586 (the
tombstone, decision 4 of `docs/research/history-tombstone-design.md`) and #621 (the
runbook for rewriting git history of the private instance repository).

Claims are marked **[V]** verified (file and line read in this checkout, base
`2f26e21`) or **[I]** inferred (not run, or a GitHub behavior not exercised here).
Every **[I]** that a design choice leans on is repeated in "Open owner decisions"
or "Tests" so it gets proved before it is trusted.

## 1. Summary

Issue #619 asks that the instance workflow accept a history rewrite when every
changed line is a tombstone and the pull request carries a specific label. Reading
the workflow shows the premise is only half right, and the design changes because
of it:

* The append-only check lives in the **publish job of `refresh-counts.yml`** and
  compares the engine's artifact with the committed file (lines 130-142) **[V]**.
  It only ever sees files the refresh job produced from `main`. A hand-made pull
  request that rewrites `history/social.jsonl` never passes through it. The only
  workflow that runs on a pull request is `preview.yml`, and it has no history step
  at all (grep for "history" in `preview.yml` and `deploy.yml`: no hit) **[V]**.
* So today a human pull request can rewrite or delete any history line and merge,
  with no check that says it is not append-only. The "rejects any in-place
  rewrite" in the issue text is true only of the bot's own path. After such a
  rewrite merges, the next refresh builds on the rewritten `main` and the publish
  check passes against it **[V]**.
* What the existing check really blocks is the stale-bot-branch case: with a
  tombstone rewrite on `main`, an open `counts/refresh` pull request built on the
  old history is no longer a byte prefix of `main`, and `scripts/unmerged-history.sh`
  exits 3, which makes the publish job skip with a warning on every run until the
  owner closes that pull request (`refresh-counts.yml` lines 181-191,
  `unmerged-history.sh` lines 25-30) **[V]**.

So the real task is two-sided: **add the missing pull-request check that makes
append-only enforceable for people**, and give it **one labeled exception**
for a tombstone-only rewrite. Adding only the exception would add a bypass to a
control that does not exist yet. Recommendation in section 12.

## 2. Assets and actors

Assets: the integrity of the published counts history (it feeds the media kit and
sponsor quotes, so a quiet edit is a misrepresentation to a third party); the
redaction duty (a tombstone that is claimed but did not remove the value is a
compliance failure); availability of the daily refresh.

Actors and what they can do **[V]** unless marked:

| Actor | Can |
| --- | --- |
| Owner (admin) | everything, including bypassing branch protection if the rule allows it |
| Collaborator with write | push branches, open pull requests, edit workflow files in their own pull request (the `pull_request` trigger runs the workflow file from the pull request: `preview.yml` lines 27-31) |
| Collaborator with triage | apply and remove labels **[I]** (GitHub's documented role table; not re-read this session) |
| Fork contributor | open a pull request; the token is read-only and secrets are absent; cannot label **[I]** |
| Actions bot (`github-actions`) | the publish job holds `pull-requests: write` (`refresh-counts.yml` lines 98-100) **[V]**, which can add a label to its own pull request **[I]** |
| Engine image | pinned by `engine.lock`; the preview job already reads the BASE copy of the lock |

## 3. Threat model

Goal of the control: no pull request changes the value, time, source or order of a
history line except by turning an existing `api` record into a valid tombstone
(and dropping that record's corrections), with the hash chain re-linked and
verifying.

A label is a bypass of an append-only integrity control. Each way to abuse it:

1. **Who can add the label.** GitHub lets anyone with triage or higher label a
   pull request **[I]**. That is wider than the people who should authorize a
   history rewrite. Mitigation: the label is a signal only. The check reads who
   applied it (section 5) and requires repository admin or maintain, read live.
2. **The pull request author labels their own pull request.** With one owner this
   cannot be separated. The label is then a speed bump and an audit record, not a
   second person. The content check in section 6 is the real control and does not
   depend on who applied the label. Stated plainly in the instance README.
3. **Forks.** The check runs only when the head repository is the base repository
   (the same guard `preview.yml` line 58 uses) **[V]**, and refuses the label on a
   fork pull request. Fork authors cannot label anyway **[I]**.
4. **The Actions bot labels its own refresh pull request.** The publish job's token
   can label **[I]**. Mitigation: refuse when the label's applier is a Bot account
   (`type == "Bot"` or a login ending `[bot]`), refuse when the head branch is
   `counts/refresh`, and require the head branch to match `redact/*`. Two
   independent refusals, so one mistake does not open it.
5. **Label timing and TOCTOU.**
   * Label applied after the check passed green: the check does not need the label
     for an append-only pull request, so nothing changes; for a rewrite the label
     was needed at check time. Re-run on `labeled` and `unlabeled` so the result
     always reflects the live label set.
   * Label removed after a green run: the old green run stays on the head SHA. A
     removed label should withdraw the approval. The `unlabeled` run must write a
     failing result under the same required check name; which run GitHub shows as
     current for a required name is **[I]** and must be proved on a scratch
     repository before the design is trusted (test T14). If it cannot be proved,
     fall back to making the check re-read the label at merge time through a merge
     queue or a required-status re-run, or accept that the content check, which is
     bound to the head SHA, is what protects the history and removal only
     revokes intent.
   * Push after the label: a new head means a new `synchronize` run that
     recomputes everything from the new head's bytes. The label authorizes "a
     tombstone-only rewrite", and the content check re-proves that on every head,
     so a pushed change cannot ride an old approval.
   * The label read must be live (API call in the job), not
     `github.event.pull_request.labels`, which is the snapshot at trigger time.
   * Applier identity is taken from the issue events list (the last `labeled`
     event for that exact name), not from `github.event.sender`, which is the
     pusher on a `synchronize` run **[I]**.
6. **Look-alike label.** Compare the label name as exact bytes against one
   constant. A label named with a homoglyph or trailing space must not match.
7. **`pull_request_target`.** Never used. It would run base-branch workflow code
   with a write token against attacker-controlled files. `preview.yml` already
   states the same rule (lines 4-6) **[V]**.
8. **The pull request edits the guard.** With `pull_request`, a pull request that
   changes `history-guard.yml` or its scripts runs its own version. Mitigations:
   scripts and the engine pin come from the BASE commit (the pattern in
   `preview.yml`, lines 69-80) **[V]**; the workflow file itself is covered by
   CODEOWNERS and required review, which the instance README already tells owners
   to set for `.github/`, `scripts/` and `engine.lock` (README lines 122-132
   **[V]**). This is a residual risk that no workflow design removes; it is
   stated, not hidden. A detective control that does not depend on the pull
   request is in section 11.
9. **Hostile file content.** Duplicate JSON keys, floats, huge lines, a symlink in
   place of the file, a file bigger than the cap, a line that parses but is not
   the engine's canonical bytes. The engine already refuses the first four
   (`history.py` `_parse_line`, `_pairs`, `MAX_LINE_BYTES`, `MAX_FILE_BYTES`) **[V]**;
   the new compare must also refuse a new line whose raw bytes differ from the
   canonical serialization (section 6, rule 6), because `_parse_line` accepts any
   spacing and key order **[V]** and the chain digest is over raw bytes.
10. **Path tricks.** The rewrite pull request may change only
    `history/social.jsonl`. Anything else, including `numbers.json`, fails.

### What an attacker gains if the check is wrong

* Edit counts silently (inflate followers or views), then re-chain so
  `history-verify` still passes: a false number reaches the media kit and the
  sponsor quote. This is the central harm, and the reason the compare must be
  exact and not a heuristic.
* Delete unfavorable rows with no trace (a "tombstone" that is really a removal,
  or a tombstone that hides a changed neighbour).
* A tombstone that still carries the value (extra field, value in `run_id`):
  looks redacted, is not. The compliance claim is false and Git keeps the proof.
* Resurrection: merging a stale pull request over a rewrite and restoring
  redacted values.
* Denial of service: a label-gated path that wedges the refresh (section 9).

An attacker who can forge the label but not pass the content compare can only
tombstone real `api` rows. That is bounded: data is lost but no value is invented.
That bound is the reason content verification, not the label, is the design's
core.

## 4. The label and who may apply it

* Name: `history-tombstone` (exact, lowercase, one constant in the script). Rejected
  alternatives: `redaction` (too general), `approved` (meaningless out of context).
* Applier: the **last `labeled` event for that name** must come from an account
  that is not a Bot and whose repository permission is `admin` or `maintain`,
  read through `GET /repos/{r}/collaborators/{login}/permission` **[I]** and the
  issue events API **[I]**. Both need only `pull-requests: read` (plus `contents:
  read`) on the job token.
* Where a stronger gate is wanted (two-person instances): put the check job under
  a protected environment `history-redaction` with required reviewers and "prevent
  self-review" **[I]**. This gives a real second human. It is optional, because a
  solo owner cannot satisfy self-review and the deployment-branch rules apply to
  `refs/pull/N/merge` rather than `main`, so the environment must have no branch
  restriction **[I]**. It is an alternative in section 10, not the default.
* CODEOWNERS on `/history/` is a complementary control for multi-person instances
  (code-owner review is required by branch protection, and the pull request author
  cannot approve their own pull request). It is not a replacement for the check,
  because it controls who approves, not what changed. Added to the README text.

## 5. Which workflow reads it

A new workflow in the instance template, `.github/workflows/history-guard.yml`:

* Trigger: `pull_request`, `branches: [main]`, `types: [opened, synchronize,
  reopened, labeled, unlabeled]`. Never `pull_request_target`.
* Permissions: workflow level `contents: read`; job level `contents: read`,
  `pull-requests: read`. No `write`. No secrets. No environment (by default).
* One job, `history-guard`, plus a tiny `ci-green`-style aggregate named
  `History guard` to require in branch protection. No `paths:` filter: a required
  check whose workflow is skipped by a path filter sits pending forever **[I]**,
  so the job always runs and exits 0 at once when
  `git diff --name-only BASE HEAD` does not list `history/social.jsonl`.
* Not placed in `preview.yml`, so label events do not rebuild the preview and
  repost its comment, and so a history-guard failure has its own name.
* Not placed in the refresh job or the publish job. Those run on `main`, on a
  schedule, over their own artifact; they cannot see a human pull request.
* Scripts come from the BASE commit in a separate checkout, made read-only before
  the pull request's files are read, exactly as `preview.yml` does **[V]**. The
  pull request's `history/social.jsonl` is read as data only.
* Skipped (and required to fail, not pass) for fork pull requests that touch the
  history file.
* The refresh workflow is unchanged, except that `unmerged-history.sh`'s message
  gains one sentence pointing at the runbook step in section 9.

## 6. The exact verification

Run by a new script, `scripts/verify-history-rewrite.sh OLD NEW`, which runs a new
engine subcommand, `reach-around counts history-compare --old OLD --new NEW
--allow-tombstones`, in the pinned engine image from the BASE `engine.lock`. The
container uses the same flags as `verify-history.sh`: no network, read-only root,
no capabilities, caller's uid, `--pids-limit`, `--memory`; OLD and NEW are mounted
read-only in separate directories. Doing it in the engine, not in shell and jq,
keeps one parser (strict JSON, duplicate keys, canonical form) as the source of
truth. A shell prefix compare is not enough: the re-chain changes every line after
the first tombstone.

Old file = `git show BASE:history/social.jsonl` (absent counts as empty). New file =
the pull request head's copy. Both are regular files, not links, within the size
cap.

Exit codes: 0 = identical or a pure byte-prefix extension (append-only); 10 = a
valid tombstone-only rewrite; anything else = refuse. The workflow reads the code:

* 0: pass, label not needed (label present: warn, the label is unused).
* 10: pass only if the label rules of section 4 hold at this moment.
* otherwise: fail.

Compare rules (all must hold; otherwise exit nonzero):

1. Both files pass the engine's full `read_history` check, including the chain
   (`_parse`, `history.py` line 434) **[V]**.
2. Walk the old entries in order with a cursor `j` over the new entries.
   Row matching is by key (platform, metric, as_of_utc, source), by position, never
   by line number, because corrections vanish and the line count shrinks:
   * Old entry kept: `new[j]` must equal it in every field except `prev`
     (the parsed fields, not just a few of them); advance both.
   * Old `record` replaced: `new[j]` must be a tombstone with the same key and the
     same `api_provider`; then every following old `correction` with that key is
     skipped; advance.
   * Old `tombstone`: must carry over unchanged except `prev`.
   * Anything else (different value, reordered, deleted with no tombstone, a
     correction kept after its record was tombstoned, a tombstone with no old
     record): refuse.
3. After the old entries are exhausted the new file must have no entries left. A
   rewrite pull request does not also append rows (appending is the refresh job's
   business, and mixing both makes the compare weaker).
4. At least one old record was replaced. Zero replacements with a label: refuse
   (nothing to authorize).
5. Every new tombstone passes `validate_tombstone` (closed reason list, plain
   `trigger_date`, `redacted_at_utc` not in the future and not before
   `trigger_date`, `redact-` run id, no `note`, `era`, `value`) **[V]**
   (`history.py` line 248). The set of fields is exactly `_TOMB_FIELDS` plus
   optional `api_provider` **[V]**.
6. Every new line's raw bytes equal the engine's canonical serialization of its
   parsed object with its `prev` (`_line_bytes` / `_tomb_line`) **[V]**. This is
   stricter than `read_history` and stops a rewrite smuggling bytes in whitespace
   or key order that the chain digest would still cover.
7. Lines before the first replaced record must be byte-identical (this falls out of
   rule 2 plus chain equality, and is asserted separately as a cheap early check).
8. Only `history/social.jsonl` changed in the pull request: `git diff --name-only
   BASE...HEAD` is exactly that path (a rename, mode change or symlink in the
   diff refuses). `numbers.json` is **not** changed by a rewrite pull request; it
   holds the latest published numbers, not history rows **[I]** (not traced into
   the engine this session; see decision D5). A redaction that must also touch
   `numbers.json` is done as a second, ordinary pull request.
9. Counts only: output and the job summary state the number of replaced records
   and the number of corrections dropped. No values, notes or digests.
10. Source policy: a tombstone over a `hand_kept` or `imported` record is allowed
    by the compare; whether the CLI path allowed it is not re-checked (the CLI's
    `--include-hand-kept` is an owner choice, not an integrity property). Open
    decision D4.
11. Size and count caps: at most `MAX_REDACT` (100000) replacements **[V]** and the
    existing file cap.

After a pass for exit 10, the job also confirms that the PR head's chain verifies
with `verify-history.sh` (already rule 1; run as its own step so the failure text
is the familiar one).

## 7. Fail-closed behavior

* Any unexpected condition (API error reading the label or the permission, missing
  event, malformed JSON from the API, container failure, an unknown exit code, a
  timeout) is a red run, never a skip and never a pass. A rate limit is a red run
  that a re-run fixes.
* No label, or a label from an ineligible account, or an ineligible branch: the
  file must be exit 0 (append-only). A rewrite without the label fails with a
  message that names the rule and the runbook, not the data.
* The job has no `continue-on-error`. Its aggregate result is what branch
  protection requires.
* Failure messages never print values, notes, digests or rows.

## 8. numbers.json

A tombstone-only pull request changes `history/social.jsonl` and nothing else.
`numbers.json` is validated by `validate-numbers.py` in the refresh path only
**[V]** (`refresh-counts.yml` line 129) and is not touched by the guard. If the
redacted value is also the current published number, the owner handles that as a
separate ordinary pull request or the next refresh. See D5.

## 9. Interaction with the bot pull request and the refresh

* Before merging a rewrite pull request, close any open `counts/refresh` pull
  request. If it is left open, `unmerged-history.sh` makes every later publish run
  skip with a warning until it is closed **[V]**. If someone merges the stale bot
  pull request after the rewrite, the guard sees a file that is neither an
  extension of the new `main` nor a labeled tombstone-only rewrite and fails it,
  so redacted values cannot be restored by that path. Runbook step to add to
  `docs/social-history.md`.
* Optional follow-up (not in the first change): the publish job could close its
  own stale `counts/refresh` pull request when `main`'s history changed
  incompatibly. Rejected for the first change: it adds write behavior to the job
  that holds the token, for a convenience.
* Merge conflicts: the rewrite changes every line after the first tombstone, so
  any concurrent append conflicts. That is the safe failure; the author rebases
  by redoing the redaction.

## 10. Alternatives considered

| Alternative | For | Against |
| --- | --- | --- |
| A. Defer: keep manual, reviewed rewrites (today's runbook) | zero new code; the human reads the diff | There is no machine check at all today (section 1), so this "keeps" nothing: an unreviewed or mistaken rewrite merges. Review of a re-chained file is unreadable by eye: every later line changes. |
| B. A CLI tool run by the owner locally (`counts history-compare` with no workflow) | smaller change; no label mechanism; no CI trust issue | The check runs where the attacker or a mistake is, on a laptop, and nothing stops merging without having run it. Useful as the same engine command the workflow calls. Adopted as a part, not as the whole. |
| C. Require signed commits on rewrite pull requests | proves who authored the commit | Does not say what the commit changes. A signed malicious rewrite still merges. A bot commit is signed by GitHub. Complements, does not replace, content verification. Open decision D6. |
| D. Protected environment with required reviewers as the gate | a real second human **[I]** | Cannot be satisfied by a solo owner with self-review blocked; ties the check to deployment-branch rules. Offered as optional hardening for multi-person instances. |
| E. Make the guard part of the refresh workflow's publish job and have it open the rewrite pull request | one place | The refresh job cannot see human pull requests; the rewrite is owner-run and uses a different branch; and it gives the bot write access to a destructive path. Rejected. |
| F. Label only, no content compare | simplest | A label on an unchecked rewrite is a free bypass of the control (section 3). Rejected. |
| G. Disallow rewrites in CI altogether; owner force-pushes after local verification | no bypass in CI | The owner already owns the rewrite via #621; this just removes the review artifact, and branch protection would have to be bypassed. Rejected. |

## 11. Defense in depth (optional, recommended)

* A detective job on `push` to `main` (in `deploy.yml` or its own file) re-runs the
  same compare between `github.event.before` and `github.sha` for the history file
  and fails loudly if it neither extends nor is a valid tombstone-only rewrite.
  This still works if a pull request edited the guard (item 8, section 3), because
  it runs from `main`'s workflow files.
* Branch protection: require `History guard`, require code-owner review for
  `/.github/`, `/scripts/`, `/engine.lock` (already advised), and do not allow
  administrators to bypass for this check **[I]** (ruleset behavior to confirm).
* The README gets a short "History rewrites" section: the label, who may apply it,
  the single-owner honesty note (item 2), the close-the-bot-PR step.

## 12. Recommendation

1. **Build it, with the content compare as the control and the label as a gate on
   it.** Do alternative F never. Take A as the fallback only if the owner does not
   want a new engine subcommand.
2. Ship a new `history-guard.yml` (separate workflow, `pull_request`, read-only
   token, scripts and pin from BASE) and an engine `counts history-compare`
   subcommand, with exit codes 0 / 10 as in section 6. Make append-only enforced
   on every pull request, not only the rewrite (this closes the gap in section 1).
3. Label `history-tombstone`; applier must be a non-Bot with `admin` or `maintain`;
   head branch `redact/*`, same repository, never `counts/refresh`.
4. Add the detective push check and the README text. Do not use an environment by
   default; document it as hardening.
5. Build order: engine compare and its tests (red first) -> script -> workflow ->
   template tests -> README and runbook text. One pull request per layer so each
   can be reviewed and revert-proved.

## 13. Tests the implementation would need

Red before green. Engine tests in `tests/test_counts_history_compare.py`; template
tests beside `tests/test_instance_counts_workflow.py`. Each guard test collects
loosely and asserts strictly, including that rows were collected, with a negative
case proving the guard bites. Fixtures are synthetic (N0CALL-style, no real
counts).

Compare, positive:

* T1: a rewrite made by the real `redact_records` over a fixture (several
  platforms, a record with corrections, an older tombstone, the first, a middle and
  the last record) returns 10, and the compare agrees with `read_history` on both
  files.
* T2: identical files and a pure append return 0.
* T3: an existing tombstone carries over unchanged except `prev`.

Compare, hostile (each must fail, and each fixture is revert-proved by changing
the compare to accept it and seeing the test go red):

* T4: one kept line's value changed by 1, chain re-linked correctly.
* T5: one kept line's `as_of_utc`, `source` or `api_provider` changed.
* T6: two kept lines swapped, chain re-linked.
* T7: a line deleted with no tombstone; a tombstone with no old record; a tombstone
  whose key does not match the old record; a tombstone with a different
  `api_provider`.
* T8: a tombstone with an extra field (`value`, `note`, `era`, `replaces`), a
  reason off the list, a missing `trigger_date`, a future `redacted_at_utc`, a
  `run_id` not starting `redact-`.
* T9: a record that is still present after its tombstone; a correction kept after
  its record was tombstoned; a record appended after the rewrite (rule 3).
* T10: non-canonical bytes: extra spaces, reordered keys, `A` escapes,
  duplicate keys, a float, a trailing blank line, a missing final newline, a line
  over 2048 bytes, a file over the cap, an invalid chain, a zero-replacement
  "rewrite".
* T11: OLD or NEW is a symlink, a FIFO, a directory; paths with spaces and
  leading dashes.
* T12: an old file that is empty with a non-empty new file that is not append-only.

Workflow (the template tests parse the YAML):

* T13: trigger is `pull_request` with the five types, never `pull_request_target`;
  no `write` permission; no secret; no `continue-on-error`; actions pinned by SHA;
  scripts come from a BASE checkout; label name is the one constant.
* T14: **proved on a scratch repository, not by unit test:** label removed after a
  green run turns the required check red; label added after the push; label added
  by a Bot; by a triage-only user; by the pull request author with admin; label
  with a look-alike name; a fork pull request; the head branch `counts/refresh`;
  a head branch not matching `redact/*`; a pull request that edits the guard
  workflow (confirm CODEOWNERS blocks it).
* T15: guard behavior as a pure function over fake API responses (a small script
  with the label and permission logic separated from the HTTP): API error, empty
  events, two `labeled` events by different people (the last one counts),
  `unlabeled` after `labeled`, a permission value that is not `admin` or
  `maintain`.
* T16: a rewrite pull request that also touches `numbers.json`, a workflow file,
  or adds another path fails.
* T17: the stale bot case: a `counts/refresh` pull request built on the old
  history is rejected by the guard after a rewrite lands, and
  `unmerged-history.sh` still returns 3.
* T18: output holds counts only (assert no value, note or 64-hex digest appears in
  stdout, stderr or the summary).
* T19: the existing refresh tests still pass unchanged (publish check still
  `cmp`).

## 14. Open owner decisions

Recommended answer first. Answer yes or no, or pick.

* **D1.** Enforce append-only on every pull request that touches
  `history/social.jsonl` (this closes the gap in section 1), with the labeled
  tombstone-only rewrite as the only exception. Recommended: **yes**. Cost: an
  unlabeled hand edit of the history file now fails; that is the point.
* **D2.** Label name `history-tombstone`; applier must be admin or maintain and not
  a Bot; head branch `redact/*`. Recommended: **yes**. Alternative: a protected
  environment with required reviewers (two-person instances only).
* **D3.** Put the check in a new `history-guard.yml` rather than in `preview.yml`.
  Recommended: **yes** (no preview rebuild on label events).
* **D4.** Allow a tombstone over `hand_kept` and `imported` rows in the compare.
  Recommended: **yes**, because the harm the compare prevents is invented or
  changed values, and the CLI already guards the choice. The alternative is to
  require a second label for non-`api` rows.
* **D5.** A rewrite pull request changes only `history/social.jsonl`, never
  `numbers.json`. Recommended: **yes**. Needs one check first: trace whether
  `numbers.json` can hold a value that is also a history row (inferred no).
* **D6.** Require signed commits on `redact/*` branches as an extra. Recommended:
  **no for now** (does not check content; adds friction); revisit if a second
  maintainer joins.
* **D7.** Add the detective push-to-`main` check (section 11). Recommended: **yes**,
  as part of the same change.
* **D8.** Ship through an `engine.lock` bump: an instance whose pinned engine lacks
  `history-compare` fails the guard closed until it bumps. Recommended: **yes**
  (fail closed beats silent pass); the README says so.
* **D9.** Accept the single-owner limit: with one admin, the label is not a second
  person, and the content compare is the protection. Recommended: **accept**, and
  say it in the README rather than imply separation of duties.

## 15. Not covered

* Rewriting git history of the instance repository (#621; a tombstone meets the
  duty in the working tree only).
* Automatic redaction timers (decision 8 of the tombstone design).
* Anything about the public engine repository's own CI.
