<!-- SPDX-License-Identifier: Apache-2.0 -->
# Labeled tombstone-only rewrite of the counts history: design

Status: DRAFT for owner decision, 2026-11-23. **Daybreak rounds 1 to 3 are complete
(tables in sections 16, 17 and 18); no further round is allowed without the owner.
Build only if G1 to G8 have been run and pass.** For G2 "pass" means the experiment was
run and its result recorded: both of its outcomes are defined designs (section 12). Spec only: no code and
no workflow change come with this document. Issue: #619. Epic: #364. Follows #586
(the tombstone, decision 4 of `docs/research/history-tombstone-design.md`) and #621
(the runbook for rewriting git history of the private instance repository).

Implementation note (2026-11-26, issue #619): the compare (`counts history-compare`,
rules 1 to 13 and 16), the derived writer (`history-redact --derived`, D15), the
manifest, the `numbers.json` rule, `history-guard.yml`, `history-guard.py`,
`verify-history-rewrite.sh` and the approvers file are built with their tests. Not
built: the detective push job and off-GitHub notice (D7), the stale-refresh closer
(D13), the merge watcher and authorization digest (5.5 items 4 and 5), the GYST
reusable form (alternative H). The live gates G1, G2, G4, G5 and G6 need a scratch
GitHub repository and are NOT run: until they are, the guard's token scope
(`pull-requests: read`, `actions: read`) is unproved (G5), "branch must be up to date"
is a documented requirement rather than a proven one (G1), and which revision of a
workflow file GitHub executes for a pull request that edits it is UNVERIFIED (G1);
only these are stated as verified: the workflow must already exist on the default
branch and runs against the pull request's merge ref, the scripts and approvers come
from a base-pinned checkout and the data from a head-pinned checkout.
Approval is bound to the head: the accepted label event must come after the head last
changed (the first workflow run for that head on this pull request, or the latest
forced push, whichever is later; undeterminable is a refusal) and the 24 hours count
from that event, so a new head needs a new label. The job summary carries short commit
ids and counts, no login. The head-change time and the run listing are UNVERIFIED
against live GitHub until G5; the commit date is never used.

Claims are marked **[V]** verified (file and line read in this checkout, base
`2f26e21`) or **[I]** inferred (not run, or a GitHub behavior not exercised here).
Every **[I]** a design choice leans on is a named design gate (section 12) that must
be proved on a scratch repository before implementation starts, not a test to add
afterwards.

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
  (no "history" in `preview.yml` or `deploy.yml`) **[V]**.
* So today a human pull request can rewrite or delete any history line and merge,
  with no check that says it is not append-only. After such a rewrite merges, the
  next refresh builds on the rewritten `main` and the publish check passes against
  it **[V]**.
* What the existing check really blocks is the stale-bot-branch case: with a
  tombstone rewrite on `main`, an open `counts/refresh` pull request built on the
  old history is no longer a byte prefix of `main`, and `scripts/unmerged-history.sh`
  exits 3, so every later publish run skips with a warning until the owner closes
  that pull request (`refresh-counts.yml` lines 181-191, `unmerged-history.sh`
  lines 25-30) **[V]**.

So the real task is two-sided: **add the missing pull-request check that makes
append-only enforceable for people**, and give it **one labeled, bounded exception**
for a tombstone-only rewrite. Adding only the exception would add a bypass to a
control that does not exist yet. Recommendation in section 13.

## 2. Assets, actors and the limit of this design

Assets: the integrity of the published counts history (it feeds the media kit and
sponsor quotes, so a quiet edit is a misrepresentation to a third party); the
redaction duty (a tombstone that is claimed but did not remove the value is a
compliance failure); availability of the daily refresh.

| Actor | Can |
| --- | --- |
| Owner (admin) | everything, including editing rulesets and workflows, unless a ruleset says otherwise and admin bypass is off **[I]** |
| Collaborator with write | push branches, open pull requests, edit workflow files in their own pull request (the `pull_request` trigger runs the workflow file from the pull request: `preview.yml` lines 27-31) **[V]** |
| Collaborator with triage | apply and remove labels **[I]** |
| Fork contributor | open a pull request; the token is read-only, secrets are absent; cannot label **[I]** |
| Actions bot | the publish job holds `pull-requests: write` (`refresh-counts.yml` lines 98-100) **[V]**, which can label its own pull request **[I]** |
| Engine image | pinned by `engine.lock`; the preview job already reads the BASE copy of the lock **[V]** |

**What this design cannot do (stated plainly).** It does not protect against a
malicious or compromised `admin` or `maintain` account, and it cannot judge whether a
redaction reason is true: `reason`, `trigger_date` and the run id are assertions. For
a **sole owner** every gate here is passed by the same account that could also edit
the guard, edit the ruleset, or force-push around them. For that owner the design
gives: a mistake-proof exact compare (no changed value slips through a rewrite), a
bounded blast radius per rewrite (section 6.3, rules 12-14), an audit trail the
account can delete only by rewriting `main`, and a notification channel (section 11).
It does not give separation of duties. Only a second person (section 4) does.

## 3. Threat model

Goal of the control: no pull request changes the value, time, source or order of a
history line except by turning an existing `api` record into a valid tombstone (and
dropping that record's corrections), with the hash chain re-linked, no more than a
bounded number of records, and the redaction described in the pull request.

A label is a bypass of an append-only integrity control. Each way to abuse it:

1. **Who can add the label.** Triage and above can label **[I]**; that is wider than
   who should authorize a rewrite. Mitigation: the label is a signal only. The check
   evaluates the applier's identity at verification time against an allow-list
   (section 4).
2. **The author labels their own pull request.** With one owner this cannot be
   separated; see section 2. The content check does not depend on who labeled.
3. **Forks.** The check runs the rewrite path only when the head repository is the
   base repository (the guard `preview.yml` line 58 uses) **[V]**; a fork pull
   request that touches the history file fails.
4. **The Actions bot labels its own refresh pull request.** Refused three ways: the
   applier is not in the allow-list, the head branch is `counts/refresh`, and the head
   branch must match `redact/*`.
5. **Label timing and TOCTOU.**
   * Label removed after a green run: see design gate G2. An `unlabeled` run must
     turn the required result red; if GitHub cannot be shown to honor that, the
     design relies on the merge-time re-check of section 5.5, or the label is
     documented as an irrevocable record once a head SHA has passed. The same applies
     to every mutable input of the check (label state, label age, applier, allow-list),
     not only the label.
   * Push after the label: a new head runs `synchronize` and everything is
     recomputed from the new head's bytes.
   * **Base moves after the check** (F1): the old green result is on the head SHA,
     not on the base that will actually merge. See section 5.4; this is the most
     important timing risk and has its own gate (G1).
   * The label read is live from the API in the job, never from
     `github.event.pull_request.labels`, which is a trigger-time snapshot **[I]**.
6. **Look-alike label.** Compare the name as exact bytes against one constant.
7. **`pull_request_target`.** Never used (same rule as `preview.yml` lines 4-6) **[V]**.
8. **The pull request edits the guard.** `pull_request` runs the workflow file from
   the pull request. Scripts and the engine pin come from the BASE commit
   (`preview.yml` lines 69-80) **[V]**, but the workflow file itself, and the base
   scripts and `engine.lock` for every later run, are only as safe as the rule that
   decides who may change them. Section 11 states exactly what protects them and what
   does not.
9. **Hostile file content.** Duplicate JSON keys, floats, huge lines, a symlink in
   place of the file, a file bigger than the cap, a mode change, a submodule entry,
   a line that parses but is not canonical bytes. The engine already refuses the
   first four (`history.py` `_pairs`, `_no_float`, `MAX_LINE_BYTES`,
   `MAX_FILE_BYTES`) **[V]**; the rest are the compare's job (section 6).
   **Metadata that encodes the value** (round 3): a `run_id`, `reason`, date, stamp,
   manifest line or manifest file name that spells or encodes the redacted number. The
   compare closes this by making every permitted metadata field derivable (rule 16).
10. **Path tricks.** Only the paths in section 8 may change.
11. **A compromised or mistaken maintainer destroys eligible history** (F4):
    bounded and made visible by section 6.3 rules 12-14 and section 11; not
    prevented for a sole owner (section 2).

### What an attacker gains if the check is wrong

* Edit counts silently (inflate followers or views), then re-chain so
  `history-verify` still passes: a false number reaches the media kit and the sponsor
  quote. The central harm, and why the compare must be exact.
* Delete unfavorable rows with no trace (a "tombstone" that is really a removal, or
  one that hides a changed neighbour).
* A tombstone that still carries the value (extra field, value in `run_id`), or a
  value that survives in `numbers.json`: looks redacted, is not. Git keeps the proof.
* Resurrection: merging a stale pull request over a rewrite and restoring redacted
  values (F1).
* Denial of service: a label-gated path that wedges the refresh (section 9), or a
  compare with no resource bound used as a CI exhaustion attack (rule 13).

An attacker who can forge the label but not pass the compare can only tombstone real
`api` rows, up to the cap, with a manifest that names them. That is bounded: data is
lost, no value is invented. The content compare, not the label, is the core.

## 4. The label and who may apply it

* Name: `history-tombstone` (exact, lowercase, one constant in the script).
* **Allow-list, not a role mapping** (F5). A file in the repository,
  `.github/history-approvers` (one GitHub login per line, comments allowed,
  CODEOWNERS-governed), names who may authorize a rewrite. For a sole owner it holds
  one login. The check reads this file **from the BASE commit**, never from the pull
  request. The role of the account (admin, maintain, custom role) is not consulted:
  the GitHub permission API's fields, the legacy `permission` value versus the role
  name, and custom roles are not trusted to map to "maintain" **[I]**. A machine user
  or an organization bot typed `User` is refused simply because it is not listed.
* **Evaluated at verification time**, not at label time: the applier of the last
  effective `labeled` event must be in the allow-list as read now. Promotion or
  demotion after labeling therefore changes nothing that matters.
* **How the applier is found.** `GET /repos/{r}/issues/{n}/events` with `per_page=100`,
  following every `Link: rel="next"` page until the end (never stop at the first
  page), keep events named `labeled` or `unlabeled` with exactly the label name,
  take the chronologically last; it must be `labeled`. In parallel read
  `GET /repos/{r}/issues/{n}/labels` (also paginated) and require the label to be
  present now. A label deleted and recreated with the same name has no current
  membership until someone applies it again, and that application is a new
  `labeled` event, so the rule holds **[I]**. Any mismatch, error, or truncated
  pagination is a failure. The token permission these calls need (`pull-requests:
  read`, possibly `issues: read`) is **[I]**; gate G5 proves the minimum, and the job
  declares exactly that and no write.
* **Strong default for two-person instances** (recommended, not merely offered): a
  ruleset requiring an independent code-owner approval for `/history/`, with
  "dismiss stale approvals" and no bypass, or a protected environment
  `history-redaction` with required reviewers and prevent-self-review **[I]**. These
  are the only ways to get a second human. The environment has no deployment-branch
  restriction (its ref is `refs/pull/N/merge`) **[I]**.
* **Single owner:** the allow-list plus the cap, manifest and cooling period are
  guard rails against mistakes and a stolen low-privilege token, not against the
  owner's own account.

## 5. Which workflow reads it, and what it is bound to

### 5.1 Workflow

A new workflow `.github/workflows/history-guard.yml` in the instance template:

* Triggers: `pull_request` (`branches: [main]`, `types: [opened, synchronize,
  reopened, labeled, unlabeled]`) only. No `merge_group` (a merge queue is not
  supported by this design, section 5.4) and never `pull_request_target`. The manifest
  is a committed file (rule 12), so no `edited` trigger is needed and the pull request
  body is not read at all.
* `concurrency: group: history-guard-${{ github.event.pull_request.number }}` with
  `cancel-in-progress: true`, so a run started by a later label or push cancels the
  earlier one rather than racing it (section 5.5).
* Permissions: workflow level `contents: read`; job level `contents: read` plus the
  read scopes proved by G5. No write. No secrets. No environment by default.
* **One job**, named `History guard`, and nothing else in the workflow. No aggregate
  job: a single job gives a single check identity (F6). Branch protection or the
  ruleset requires that exact context, pinned to the GitHub Actions app
  (`integration_id`), so another app cannot post the same name **[I]**. A test scans
  every workflow in the repository and fails if another job could emit the same
  context (T28).
* No `paths:` filter, because a skipped required workflow sits pending **[I]**. The
  job always runs and exits 0 at once when the diff does not list
  `history/social.jsonl`.
* Scripts and `engine.lock` come from the BASE commit in a separate checkout, made
  read-only before the pull request's files are read, as `preview.yml` does **[V]**.
* The refresh workflow is unchanged, except the message of `unmerged-history.sh`
  gains a sentence pointing at the runbook step in section 9.

### 5.2 What is compared

OLD = `git show <base>:history/social.jsonl`; NEW = the file at head (or, in a merge
group, at the queued commit). Absent counts as empty. Both must be regular files
(mode 100644, not a symlink, not a submodule entry, no mode-only change) and within
the size cap.

### 5.3 Pin to the exact commits (F1)

The job computes and records three SHAs: `head` = `github.event.pull_request.head.sha`,
`base` = the **current tip of `main` read from the API at job time**, and the
merge-base of the two. It fails closed unless `base` equals the merge-base (the head
contains the current `main`). The check summary prints the three SHAs (no content).

### 5.4 Why that is still not enough, and what is required

A green result belongs to a head SHA. If `main` moves afterward (an incompatible
rewrite lands, `B1` becomes `B2`) and the pull request's head did not change, no
`synchronize` fires **[I]**, and GitHub can build a new merge commit from `B2` and
the old head. Requirement, proved by gate G1 and test T20 before implementation:

* **(b) Strict required status ("branch must be up to date")** in the ruleset, with
  proof that a base update makes the pull request unmergeable on the old result. The
  author then updates the branch, which creates a new head and a new run. **This is
  the only supported protection and the only one the recommendation depends on.** If
  G1 fails, the design is not built; there is no fallback.
* **(c) A head-pinned merge watcher** is kept as defense in depth, not as protection:
  merge only with `gh pr merge --match-head-commit <verified head>`, after
  recomputing the authorization digest (section 5.5) against current `main`. It is a
  procedure; a person or script can bypass it, so it never substitutes for (b).
* **(a) A merge queue is removed from the design** (round 2, finding 1). A merge
  group may contain several pull requests **[I]**, while this guard reads one label,
  one applier, one branch, one cooling period and one manifest. Aggregate comparison
  of the queued commit against its first parent cannot say which pull request
  introduced a history change, and requiring the aggregate diff to be exactly the
  rewrite paths would deny unrelated queued pull requests. Making a queue safe
  needs either proof that a group holding a rewrite always has one entry, or a guard
  that enumerates every pull request in the group, reconstructs and verifies each
  transition in queue order and applies every per-pull-request rule to the one that
  introduced it. Neither is designed or proved here, so `merge_group` is not
  a trigger, and a repository that enables a merge queue on `main` must not rely on
  this guard (T21 asserts the workflow refuses to run under `merge_group`).

### 5.5 What a green check is bound to (round 2, finding 2)

A required check attached to a head SHA cannot prove which mutable pull request
metadata it saw: the label set, the label's age and applier, the base allow-list. Two
runs with the same context can finish in either order and a manual rerun of an older
run is possible; which one branch protection treats as current is **[I]** (gate G2).
So the design claims only this:

* **Bound to the head SHA and the base:** the content compare, the committed manifest
  (rule 12), the committed `numbers.json` change and the allowed paths. These are in
  the head, so a green result for that head is a result for those bytes.
* **Not bound, enforced at check time and re-enforced at merge time:** label
  presence, applier, label age and the allow-list.

Controls, none of which is assumed to be sufficient alone:

1. The job reads all of them live from the API (never from the event payload), so a
   rerun re-evaluates current state.
2. Concurrency with `cancel-in-progress` (5.1) cancels an overlapping older run.
3. The job reads the mutable state **twice**, at the start and just before it
   reports, and fails if the two reads differ (label event id, applier, label
   present, base tip).
4. The job writes an **authorization digest** to its summary and check output:
   `sha256(head, base tip, label event id, applier, label age bucket, allow-list
   blob id, manifest blob id)`. Nothing in it is a value.
5. The merge procedure (c) recomputes the digest immediately before merging and
   refuses unless it equals the digest of the check run it is about to rely on, and
   the check run is for the exact head. This is the only binding between a success
   and the live metadata, and it is a procedure, not branch protection.
6. **G2 selects one of two defined designs** (section 12). If branch protection
   treats the live failure as authoritative, current label presence is a required
   property and the README says so. If it does not, **label authorization is
   irrevocable for the exact tuple `(head SHA, base tip, manifest blob)` after the
   cooling period**, the README and this spec claim no current-label requirement at
   merge, and revocation means closing the pull request or pushing a change (a new
   head). `cancel-in-progress` only cancels overlapping runs of one group; it does
   not revoke a completed success and a later manual rerun evaluates on its own, so it
   is not counted as revocation. The merge watcher then checks consistency of the
   tuple; it is not an enforcement that someone merging in the web UI cannot bypass.
   Content safety does not depend on either branch: a rewrite that reaches the history
   file has already passed the exact compare, the cap, the manifest and the source
   policy.

A pull request body edit is not an input to any check: the manifest is a file in the
head (rule 12).

## 6. The exact verification

Run by a new script `scripts/verify-history-rewrite.sh OLD NEW`, which runs a new
engine subcommand `reach-around counts history-compare --old OLD --new NEW
[--allow-tombstones --manifest FILE --numbers-old FILE --numbers-new FILE]` in the
pinned engine image from the BASE `engine.lock`, with the same flags as
`verify-history.sh` (no network, read-only root, no capabilities, caller's uid,
`--pids-limit`, `--memory`) and a wall-clock timeout; the inputs are mounted
read-only in separate directories. One parser (strict JSON, duplicate keys,
canonical form) is the source of truth; a shell prefix compare is not enough because
a re-chain changes every line after the first tombstone.

### 6.1 Exit-code contract and wrapper control flow (F8)

The engine's `history-compare` exits: 0 = identical or pure append; 10 = a valid
tombstone-only rewrite; every other number (1, 2, the existing 3-5 range,
`counts.py` lines 133-142 **[V]**, and any other) = refuse. 10 is reserved to this
command and documented in the CLI contract. The wrapper:

```bash
rc=0
timeout 120 "$runtime" run ... history-compare ... || rc=$?
case "$rc" in
  0)  mode=append ;;
  10) mode=rewrite ;;
  *)  echo "history compare failed (status $rc)" >&2; exit 1 ;;
esac
```

No `!` inversion and no bare command under `set -e` (either would lose or kill the
status before it is read). Only 0 and 10 are accepted; 124 (timeout), 125-127 (the
container runtime), 137 and 143 (signals) are failures, never normalized. The mode is
passed to the workflow through a step output. Test T29 runs a stub engine returning
0, 1, 2, 10, 125, 126, 127, 137, 143 and a timeout and asserts the mapping.

### 6.2 The history grammar the compare relies on (F7) [V]

From `_parse` (`history.py` line 434) **[V]**:

* A record is unique by key `(platform, metric, as_of_utc, source)`; a second record
  with that key is refused ("duplicate record").
* One entry per `(platform, metric, UTC day, source)` across records and tombstones.
* A `correction` needs an earlier record with the same key and the same
  `api_provider`. Several corrections for one key are allowed and they need **not**
  be contiguous with the record.
* A tombstone is refused if a live record with its key is in the file before it,
  or a second tombstone for that key; a record or correction after a tombstone for
  its key is refused.

So a "group" is: the one record for a key plus every correction for exactly that
key anywhere after it. The compare does not assume contiguity. On both files it
relies on these invariants (they hold when `read_history` passes), builds `replaced`
= the set of keys that are records in OLD and tombstones in NEW, and drops from OLD
every correction whose key is in `replaced`, wherever it sits. A file the engine
cannot read fails rule 1, so no ambiguous old file reaches the matching. Fixtures for
duplicate keys, non-contiguous corrections and two groups sharing a key are in T26.

### 6.3 Compare rules (all must hold)

1. Both files pass the engine's full `read_history` check, chain included (line 434).
2. Rewrite matching, by key and in order, never by line number:
   * Old entry unchanged: `new[j]` equals it in every parsed field except `prev`.
   * Old `record` whose key is in `replaced`: `new[j]` is a tombstone with the same
     key and the same `api_provider`. Every old correction with a key in `replaced`
     is skipped.
   * Old `tombstone`: carries over unchanged except `prev`.
   * Anything else (changed value, reorder, deletion without a tombstone, a tombstone
     with no old record, a correction kept after its record was tombstoned): refuse.
3. After OLD is exhausted, NEW has no entries left (a rewrite does not also append).
4. At least one record was replaced. Zero with the label: refuse.
5. Every new tombstone passes `validate_tombstone` (`history.py` line 248) **[V]**, has
   exactly `_TOMB_FIELDS` plus optional `api_provider`, **and passes rule 16**, which
   is stricter than `validate_tombstone` (that function accepts any `run_id` after
   `redact-` and any plain `trigger_date` **[V]**, so it alone is not enough).
6. **Canonical bytes, defined per mode** (F9):
   * Append mode (exit 0): the first `len(OLD)` bytes of NEW are byte-identical to OLD
     (legacy non-canonical bytes survive only here); every appended line must equal
     the engine's canonical serialization of its parsed object (`_line_bytes`).
   * Rewrite mode (exit 10): bytes before the first replaced line are identical; every
     line from there on is reconstructed and must equal the canonical serialization
     (`_line_bytes` / `_tomb_line`) **[V]**. This matches `_redact`, which re-emits
     later lines through those functions **[V]**.
7. Early cheap check: the lines before the first replaced record are byte-identical.
8. Changed paths: exactly the set in section 8, nothing else.
9. Output is counts only: replaced records, dropped corrections, manifest size.
   Never a value, note, digest or row, in stdout, stderr or the summary.
10. **Source policy** (F4, D4 reversed): a tombstone over a `hand_kept` or `imported`
    record is **refused** by this mechanism. Redacting those is a rare owner choice
    (`--include-hand-kept`) and goes through the manual reviewed route (alternative
    A). Only `api` rows may be replaced.
11. Every new line is at most 2048 bytes and NEW is within the file cap.
12. **Manifest, committed in the head, byte-derived** (round 2 finding 2, round 3).
    A rewrite pull request adds exactly one new file,
    `history/redactions/<run-id>.manifest`, where `<run-id>` is the derived id of
    rule 16. The compare **reconstructs the expected manifest bytes** from the replaced
    keys, the reason, the trigger date and the redaction day, and requires byte
    equality with the file: US-ASCII, `\n` line ends, final newline, no comment, blank
    line, trailing space or other line. Format: a first line
    `history-tombstone-manifest 1`; then `reason=<code>`, `trigger_date=<date>`,
    `redacted_day=<date>`, `count=<N>`; then one line `platform metric as_of_utc
    source` per replaced record, sorted bytewise. It holds no value. Because it is in
    the head a green check is bound to a particular manifest; the pull request body is
    not read. Existing manifests may not change or be removed; more than one new
    manifest, or one outside `history/redactions/`, fails. The file name must equal
    the derived run id exactly. The manifest stays in the tree as an audit trail (keys
    and the three codes only).
13. **Caps.** At most `HISTORY_REWRITE_MAX` records per rewrite (default 500, a
    constant in the BASE script, far below `MAX_REDACT` = 100000 **[V]**) and at most
    10 percent of OLD's records; and hard limits on input size and compare time, so
    the compare cannot exhaust CI (T32). A larger rewrite is split into several
    pull requests (each one visible) or done by the manual route.
14. **Cooling period (a notice, not an authorization).** The last effective `labeled`
    event must be at least 24 hours old; before that the job fails with the time
    remaining. After the period the owner re-runs the job by hand (no scheduled trigger
    can attach to a pull request) **[I]**. It gives time to notice through GitHub's
    notifications and the section 11 notice. It is not credited as protection against
    an authorized or compromised account, which can wait 24 hours, and repeated cooled
    pull requests can still remove all eligible history one cap at a time (section 2).
    It also lengthens the window in which a refresh pull request can appear, which rule
    15 handles.
15. **No open bot refresh pull request** (round 2 finding 4, round 3). Best-effort
    availability protection, **not** an atomic pre-merge condition and not a safety
    control (the safety against a stale bot pull request is strict up-to-date plus the
    guard, section 9).
    * **The object.** A pull request blocks only if all hold: it is open; its base ref
      is exactly `main`; its head repository equals the base repository (`head.repo.
      full_name == base.repo.full_name`, so a fork pull request that merely uses the
      branch name never matches); its head ref is exactly `counts/refresh`; and its
      author login is `github-actions[bot]` (REST `user.login`; the `gh` JSON name
      `app/github-actions` is not used) **[I]** that REST exposes exactly this.
    * **How it is read.** The REST list `GET /repos/{r}/pulls?state=open&base=main&
      per_page=100`, following every `Link: rel="next"` page to the last page, filtered
      client side by the predicate above. Never `gh pr list --head`, which matches the
      head name across forks. A truncated or failed traversal is a failure. A pull
      request that uses the branch name but fails the predicate (a fork, another
      author) does not block a rewrite; the existing publish job already stops with an
      error on such a pull request (`refresh-counts.yml` lines 169-177) **[V]**, which
      is the existing, separate handling.
    * **Check and merge.** The guard fails while a matching pull request is open. The
      merge procedure (c) re-reads it immediately before merging; if one is open it
      **closes the verified bot pull request** (the same predicate, with a fixed
      comment) and then performs the head-pinned merge.
    * **The race.** A refresh pull request that opens after that last read and before
      the merge is possible, because opening a pull request does not move `main`. The
      consequence is bounded and accepted: it cannot restore redacted values (it is no
      longer an extension of the new `main`, so the guard fails it under strict
      up-to-date), and the refresh publish step skips until it is closed, which the
      optional closer workflow (D13) does after the merge, applying the same full
      predicate, and the owner can do by hand. Availability, not integrity.
16. **Tombstone metadata is non-expressive** (round 3, High). The redacted value must
    not survive in any field the compare permits. Every field of every **new**
    tombstone (one that replaces a record in this rewrite; an older tombstone carries
    over unchanged and is exempt) and the manifest must be either equal to a field of
    the old record it replaces, or drawn from a closed vocabulary, or derived by a
    fixed function from public inputs. The compare rejects anything it cannot derive:
    * `platform`, `metric`, `as_of_utc`, `source`, `api_provider`: equal to the old
      record's (rule 2); no choice.
    * `reason`: one of `REASONS` (five codes, `history.py` **[V]**). There is no free
      text anywhere.
    * `trigger_date`: a real calendar date `YYYY-MM-DD`, exactly 10 ASCII characters,
      not after `redacted_day` and not more than 366 days before it.
    * `redacted_at_utc`: exactly `<redacted_day>T00:00:00Z`, day granularity. The
      `redacted_day` is the UTC day of the pull request's creation or the day before
      it, read from the API by the guard and passed to the compare as `--pr-day`.
    * `run_id`: exactly `redact-<redacted_day as YYYYMMDD>-<h>` where `<h>` is the
      first 6 hex digits of SHA-256 over the manifest lines after the first line
      (reason, trigger date, redacted day, count, sorted keys). It is derived from the
      replaced keys, not generated randomly and not chosen. One run id for the whole
      rewrite, equal to the manifest file name stem.
    * All new tombstones in one rewrite carry the **same** `reason`, `trigger_date`,
      `redacted_at_utc` and `run_id`.
    * Every permitted line has a fixed key order and canonical bytes (rule 6), so no
      whitespace, key order, escape or case choice carries information.
    What remains: the free choices are `reason` (5 values), `trigger_date` (up to 367
    values), `redacted_day` (2 values) and which keys are redacted. That is about 12
    bits per rewrite, **shared by all records and not per record**, and the keys are
    public timestamps. It cannot carry a value of 4096 or more, nor one number per
    record. This residual is stated, not claimed away. The compare also scans every
    added line and the manifest for the decimal digits of each replaced value as a
    token and fails on a hit (a belt, with false positives for tiny values; the
    derivation above is the control).
    **What is lost:** the exact redaction time of day, a random run id, and any free
    text reason. A human explanation goes in the pull request description and in the
    issue, outside the working tree, and the spec's claim that a tombstone holds no
    free text now has a rule behind it. The tombstone writer (`redact_records`,
    #586) must emit these forms for new tombstones, so building this includes a change
    to that code (D15); the compare accepts nothing else, so today's writer output (a
    real time of day and a random suffix) would be rejected until it is changed.

After exit 10 the job also runs `verify-history.sh` on NEW as its own step so the
failure text is the familiar one.

## 7. Fail-closed behavior

* Any unexpected condition (API error or truncated pagination, missing event,
  malformed API JSON, missing allow-list file, container failure, unknown exit code,
  timeout, SHA mismatch) is a red run, never a skip and never a pass. A rate limit
  is a red run a re-run fixes.
* No label, an ineligible applier, or an ineligible branch: the file must be exit 0
  (append-only). A rewrite without the label fails with a message naming the rule and
  the runbook, not the data.
* No `continue-on-error`. The one job is what branch protection requires.
* Failure messages never print values, notes, digests or rows.

## 8. Known working-tree and deployment copies of a redacted value (F2, round 2 finding 6)

This section lists the copies found, not a proof that there are no others (see G7).

Traced this session:

* `numbers.json` holds `{"version": 1, "platforms": {"<id>": {"count": N, "as_of":
  "...", "source": ...}}}`, one current number per platform (`validate-numbers.py`
  docstring) **[V]**. `counts refresh` writes it and, in the same run, appends the
  fetched values to the history file (`counts.py` lines 466-513) **[V]**; the history
  metric for each platform is `youtube: subscribers`, `discord: members`, `matrix:
  members` (`counts.py` line 134) **[V]**. So the most recent `api` history row for
  those platforms carries **the same number that `numbers.json` currently publishes**.
  The opt-in analytics and Instagram insight rows are stored only in the history file
  (`counts.py` line 351) **[V]** and are not in `numbers.json`.
* The built site and kit are produced in CI and deployed (the repository tracks the
  inputs, not the output), and the kit has an imported-history path
  (`tests/site/test_kit_imported_history.py`, `imported_history.py` exist **[V]**; that
  a deployed page can show a history value is **[I]**). Deployed copies, Actions
  artifacts (`retention-days: 1`, `refresh-counts.yml` line 91) **[V]**, pull request
  diffs and previews, and git history all keep the old value outside the working
  tree. A tombstone meets the duty in the working tree only (tombstone design,
  decision 6); the claim is not widened here.
* **Not established:** that no other tracked file (a template, fixture, imported-history
  input, cache) holds the value. The two-path limit below can therefore stop a
  rewrite that needs to clean such a copy; that case uses the manual route.

**Allowed changes in a rewrite pull request.** Exactly:

1. `history/social.jsonl` (the rewrite),
2. `numbers.json` (optional, removal only, atomic with the rewrite), and
3. one new `history/redactions/<run-id>.manifest` (rule 12).

**One predicate.** A published entry **matches** a replaced record when it has the
same platform id, the same `count` as the record's `value`, **and** the same `as_of`
day. The same predicate decides both what must go and what may go:

* every matching entry **must** be removed in the same pull request (a surviving
  matching entry fails the compare);
* an entry may be removed **only** if it matches a replaced record;
* NEW `numbers.json` equals OLD with exactly those entries removed: no entry added,
  no count or `as_of` changed, no other key (parsed with `validate-numbers.py`
  semantics).

**Repeated counts (round 2, finding 3).** An entry with the same platform and count
but a different `as_of` day does **not** match: it is the current published fact for
its own day, not a copy of the redacted record, so it stays. So redacting an older row
whose count equals the current published count leaves `numbers.json` unchanged, and
there is no case where an entry can neither stay nor go. (If the owner instead treats
a coinciding count as retained information, such redactions are refused and use the
manual route; that is D5's alternative, not the default.)

So redacting the latest `api` row of a platform forces the same pull request to remove
that platform's entry, which takes the number off the published page at the next
deploy. The next refresh adds a fresh value with a later `as_of`; the day's key stays
blocked in the history file. The compare has the old values in OLD and prints none.

The claim is therefore: **history tombstoning plus atomic removal of the matching
`numbers.json` entry** in the working tree. The deployed site is cleaned by the next
deploy; Actions artifacts expire after a day; git history is #621. If the owner
declines the `numbers.json` rule (D5), the claim narrows to "history tombstoning" only,
the guard fails any pull request whose matching entry is still in `numbers.json`, and
the runbook makes removing or refreshing the published number a prerequisite pull
request merged first.

## 9. Interaction with the bot pull request and the refresh

* A rewrite cannot pass the guard while a matching bot `counts/refresh` pull request
  is open (rule 15, best effort, with the exact predicate), and the merge procedure
  re-checks it and closes the verified bot pull request. Otherwise `unmerged-history.sh`
  makes every later publish run skip with a warning until that pull request is closed
  **[V]**. That skip is fail-safe for the data but an availability failure: a
  collaborator who can reopen the bot branch or pull request can hold the refresh
  down until the owner closes it. After a rewrite lands, an optional, separate,
  narrowly scoped workflow (D13) closes a stale bot pull request: trigger `push` to
  `main` when the history file changed, `pull-requests: write` only, no keys, no
  checkout of pull request code, closes only a pull request that satisfies the full
  rule 15 predicate (open, base `main`, same repository, head ref exactly
  `counts/refresh`, author the Actions bot), with a fixed comment.
* A stale bot pull request merged after the rewrite is not protected by "the guard
  sees it": a base move fires no `synchronize` **[I]**. It is protected by section 5.4
  (strict up-to-date, with the head-pinned watcher as defense in depth), under which the stale file is neither an extension of the
  new `main` nor a valid labeled rewrite and fails. Test T20.
* The rewrite changes every line after the first tombstone, so a concurrent append
  conflicts. That is the safe failure; the author redoes the redaction on the new base.
* Not chosen: the publish job closing its own stale pull request (write behavior in the
  job that holds the token, for convenience); and teaching the refresh to open a fresh
  branch on divergence (a larger change to the refresh path).

## 10. Alternatives considered

| Alternative | For | Against |
| --- | --- | --- |
| A. Defer: keep manual, reviewed rewrites (today's runbook) | zero new code | There is no machine check today (section 1); an unreviewed or mistaken rewrite merges. Review of a re-chained file is unreadable by eye. Remains the route for `hand_kept` and `imported` rows. |
| B. A CLI compare run by the owner locally | smaller; no label mechanism | The check runs where the mistake is; nothing forces it before merge. Adopted as part: the same `history-compare` command. |
| C. Require signed commits on rewrite branches | proves who authored | Says nothing about what changed; a signed malicious rewrite merges; the bot's commits are signed by GitHub. Complement only (D6). |
| D. Protected environment with required reviewers | a real second human **[I]** | Unusable for a sole owner with self-review blocked. Recommended for multi-person instances (section 4). |
| E. Guard in the refresh publish job | one place | It cannot see human pull requests and gives the bot a destructive path. Rejected. |
| F. Label only, no content compare | simplest | A free bypass of a control that does not exist yet. Rejected. |
| G. No CI path; owner force-pushes after local verification | no CI bypass | Removes the review artifact and needs a protection bypass. Rejected. |
| H. The guard as a GYST reusable workflow pinned by commit | one reviewed implementation for all instances (the owner's consolidation rule); the instance caller is small | The caller file is still editable by a pull request in the instance; does not remove the section 11 limits. Recommended as the home of the generic logic once built. |

## 11. What protects the guard itself, and the detective control (F3)

Plain statement: **nothing in a workflow can protect the guard workflow from a pull
request that is allowed to merge a change to it.** A change that weakens
`history-guard.yml`, the push detective job, `scripts/verify-history-rewrite.sh`, the
action pins and `engine.lock` together, once merged, is the new rule for every later
run, including the detective run on `push`, because that run executes the workflow as
it now exists on `main`. Only repository governance prevents the merge:

* A ruleset on `main` that requires an independent code-owner approval for
  `/.github/`, `/scripts/`, `/engine.lock` and `/history/`, dismisses stale approvals,
  forbids force-push and deletion, and has **no administrator bypass** (that admin
  bypass can be turned off for this repository is **[I]**, gate G6). The author of a
  pull request cannot approve their own, so this is a real control only when a second
  account exists.
* **Sole owner: this ruleset can only be satisfied by a bypass or a second account.**
  A sole owner cannot be protected from their own account (section 2). The honest
  options: accept the risk and rely on notification and recoverability (below), or add
  a second account (a deliberate, low-privilege co-owner whose only role is to approve
  changes to those paths).

Controls that reduce the dependence on repository content:

* The generic compare logic lives in GYST as a reusable workflow pinned by commit
  (alternative H) and in the engine image pinned by digest; the instance holds only a
  thin caller. A change to the pin in the caller is a reviewed, CODEOWNERS-governed
  change like any other, and follows the dependency cooldown rule.
* **Detective job on `push` to `main`:** re-runs the compare between
  `github.event.before` and `github.sha` for the history file and `numbers.json`,
  selecting the reusable workflow and engine digest **recorded in the commit before
  the push** (the pin in `github.event.before`, not the one just merged). The
  guarantee is only this: **when the newly merged detective workflow executes
  honestly, it selects the pin recorded by the pre-push commit.** GitHub runs the
  workflow definition from the new `main`, so a merged change to that definition can
  skip the old pin entirely; the old pin is data available to code the new commit
  controls. This is **post-merge detection** that depends on the governance above and
  is not independent of guard edits. Independence would need a workflow or dispatch
  controlled outside the pushed commit (a separate repository, called on a schedule
  or by `repository_dispatch` with its own credentials), which this design does not
  add (D7).
* **Audit record.** On a successful rewrite the detective job writes counts and SHAs
  (base, head, merge) to the job summary and sends them to the instance's existing
  notification path (the mechanism of `docs/token-health.md`: Pushover, Discord or
  Matrix) so a record exists off GitHub. GitHub offers no record that a repository
  admin cannot edit or delete: job logs expire, issue comments and tags are editable.
  What survives is the tombstone lines themselves (`reason`, `trigger_date`,
  `redacted_at_utc`) and git history of `main` once force-push and deletion are blocked
  by the ruleset. Both are stated as such.

## 12. Design gates (prove on a scratch repository before implementation starts)

These were tests in the first draft. They are prerequisites: if one fails, the design
changes, not the test.

* **G1 (F1):** Obtain green on head `H` against base `B1`; change `main` to an
  incompatible `B2` without changing `H`; prove the pull request cannot merge on the
  old result under option (b) of section 5.4. Also prove whether any base
  update fires a `pull_request` event.
* **G2 (F6, round 2 finding 2, round 3: binary).** Experiment, on a scratch
  repository whose ruleset requires the single context `History guard` (Actions app)
  plus strict up-to-date, with an allow-listed applier:
  1. Open a valid rewrite pull request, apply the label, wait out a shortened cooling
     period (a test constant), and let the guard succeed on head `H`.
  2. Remove the label. Wait for the `unlabeled` run to finish red.
  3. Without pushing: query `mergeStateStatus` and `mergeable`, then attempt the merge
     through the REST endpoint (`PUT /repos/{r}/pulls/{n}/merge`) as an admin and as a
     write user, recording the HTTP status.
  4. Repeat after each of: a manual rerun of the older green run (so it finishes last);
     a rerun of the newer red run; the label re-applied then removed while a run is in
     progress; a change of `.github/history-approvers` on `main`.
  **PASS (branch A):** in every case of steps 3 and 4 the merge is refused (status 405
  or `BLOCKED`) while the label is absent or the applier is not listed, and allowed
  again only after the label is re-applied and a new green run completes. **Design on
  PASS:** current label presence is a branch-protection-enforced requirement; the
  watcher digest is defense in depth.
  **FAIL (branch B):** in any case a merge succeeds, or the displayed required result
  is green, while the label is absent or the applier unlisted. **Design on FAIL:** the
  label is irrevocable for the tuple `(head, base tip, manifest blob)` after the
  cooling period (section 5.5 item 6); no text anywhere claims current-label presence
  at merge; revocation is closing the pull request or pushing a change.
  Either outcome is buildable. The gate is not run until the result is written into
  the README choice; a result of "not run" blocks the build. Also record: that the
  double read fails on a change, that the authorization digest differs for each
  differing input, and that a run cancelled by `cancel-in-progress` leaves no success.
* **G3 (replaced):** a merge queue is not supported. Prove that the workflow does not
  run under `merge_group` and document that a repository enabling a queue on `main`
  loses this protection (T21).
* **G4:** One pull request that changes the guard, the detective workflow, the compare
  script and the engine pin together: prove an unauthorized actor cannot merge it.
  This tests the ruleset, not the presence of a CODEOWNERS file (T23).
* **G5:** The minimum token permissions for the issue-events and label calls, and the
  exact response fields used (event name, actor login, created_at) (T24, T25).
* **G6:** Whether administrator bypass can be disabled for this repository
  configuration.
* **G7 (round 2, finding 6):** Inventory every tracked consumer and generated input of
  a history value: `git grep` the engine and instance template for readers of the
  history file and `numbers.json` (`report.py`, `imported_export.py`, the kit and site
  build), the instance's tracked data and fixtures, caches and preview artifacts.
  Output: a table of copies and whether the guard's path set covers them. Absence of a
  copy is not claimed unless the inventory shows it.
* **G8 (round 3, metadata non-retention).** Using the real tombstone writer changed per
  D15, a synthetic distinctive 9-digit value is attempted in every permitted field of a
  tombstone and of the manifest, one at a time and combined: `run_id` suffix and date
  part, `reason`, `trigger_date`, `redacted_at_utc` (every field of it), the manifest
  file name, a manifest line, comment, blank line and trailing space, every optional
  field, and key order and whitespace. **PASS:** the compare returns a refusal for
  every attempt, and for the one valid rewrite no added byte contains the digits.
  **FAIL:** any attempt is accepted: the design is not built until the derivation of
  rule 16 is fixed.

## 13. Recommendation

1. **Build it only after G1 to G8 have been run and pass (G2: its result recorded, either branch).** The exact content compare is the control;
   the label is a gate on it. Never a label alone (alternative F).
2. New `history-guard.yml` (separate workflow, `pull_request` only, read-only token,
   one job, scripts and pin from BASE) and an engine subcommand
   `counts history-compare` with exit 0 / 10, enforcing append-only on every pull
   request (closes the gap in section 1) with the one labeled exception.
3. Label `history-tombstone`; applier on the BASE-committed allow-list; head branch
   `redact/*` in the same repository, never `counts/refresh`; `api` rows only;
   a committed manifest equal to the replaced set; cap of 500 and 10 percent; no open
   `counts/refresh` pull request; 24 hour cooling as a notice only; changed paths
   `history/social.jsonl`, one new manifest and, atomically, `numbers.json` (removal
   of the matching entry only).
4. **Strict up-to-date is the protection; a merge queue is not used.** The head-pinned
   watcher recomputes the authorization digest as defense in depth. The detective push
   job (post-merge detection) and off-GitHub notice; the generic logic in GYST.
5. For any instance with a second person: require independent approval of `/history/`
   and the guard paths by ruleset. **For a sole owner the design cannot defend against
   their own account;** it prevents mistakes and bounds and records the damage, and
   says so in the README.
6. Build order: engine compare and tests (red first) -> script -> workflow -> template
   tests -> README and runbook. One pull request per layer, each revert-proved.

## 14. Tests the implementation would need

Red before green. Engine tests in `tests/test_counts_history_compare.py`; template
tests beside `tests/test_instance_counts_workflow.py`. Guard tests collect loosely and
assert strictly (including that rows were collected) with a negative case proving the
guard bites. Fixtures are synthetic.

Compare, positive:

* T1: a rewrite made by the real `redact_records` (several platforms, a record with
  non-contiguous corrections, an older tombstone, the first, a middle and the last
  record) returns 10 and the compare agrees with `read_history` on both files.
* T2: identical files and a pure append return 0.
* T3: an existing tombstone carries over unchanged except `prev`.
* T33: a rewrite that redacts the latest row of a platform and removes that platform's
  `numbers.json` entry returns 10.

Compare, hostile (each must fail; each fixture is revert-proved by making the compare
accept it and seeing the test go red):

* T4: a kept line's value changed by 1, chain re-linked correctly.
* T5: a kept line's `as_of_utc`, `source` or `api_provider` changed.
* T6: two kept lines swapped, chain re-linked.
* T7: a line deleted with no tombstone; a tombstone with no old record; a tombstone
  with a mismatched key or `api_provider`.
* T8: a tombstone with an extra field (`value`, `note`, `era`, `replaces`), a reason
  off the list, a missing `trigger_date`, a future `redacted_at_utc`, a `run_id` not
  starting `redact-`.
* T39 (round 3, High): a synthetic distinctive value (9 digits) encoded in each of:
  the `run_id` date part and suffix, the `reason` (off-list and on-list lookalike),
  `trigger_date` (a valid date spelling the digits, a date outside the 366 day window),
  `redacted_at_utc` (non-midnight seconds, a different day), the manifest file name, a
  manifest comment, blank line, trailing space or extra line, the `count` line, a key
  line out of order, the optional fields, and differing values between tombstones of
  one rewrite; all must fail. The `run_id` of a valid rewrite equals the derived value;
  a random suffix fails. The digit-token scan fails a hit, and the test shows the scan
  alone is not relied on (a hit-free encoding by arithmetic is still refused by the
  derivation). The writer-output test asserts `redact_records` emits the derived forms
  after D15.
* T9: a record still present after its tombstone; a correction kept after its record
  was tombstoned; a record appended after the rewrite.
* T10: non-canonical bytes in a rewrite (spaces, key order, `A` escapes,
  duplicate keys, a float, a blank line, a missing final newline, a line over 2048
  bytes, a file over the cap, a broken chain, a zero-replacement rewrite).
* T11: OLD or NEW a symlink, a FIFO, a directory; paths with spaces or a leading dash.
* T12: empty OLD with a non-empty NEW that is not append-only.
* T22 (F2): the tombstoned value survives in `numbers.json` (same platform and count);
  survives with a different `as_of` but the same count; the entry changed rather than
  removed; an entry added; another key; another published file holding the value.
* T26 (F7): two record groups sharing a logical key (refused by the engine; the
  compare refuses too); corrections not contiguous; a correction before its record; a
  correction of a replaced key that sits far later; a legitimate later correction of a
  different key that must survive.
* T27 (F4): every eligible `api` row tombstoned by the PR author, expected **fail**
  (cap and 10 percent); the same under the cap, expected pass, documented as the
  residual risk for a compromised maintainer; `hand_kept` and `imported` rows
  tombstoned, expected **fail**.
* T30 (F9): a pure append containing a non-canonical appended line fails; an unchanged
  legacy non-canonical prefix with a canonical append passes; a rewrite reconstructs a
  legacy non-canonical later line canonically and passes.
* T31: base history path absent, deleted, changed into a submodule entry, or a
  mode-only change: all fail.
* T32: exactly `HISTORY_REWRITE_MAX` and one more; a file at the size cap; wall-clock
  and memory limits prove the compare cannot be a CI exhaustion attack.
* T34 (F4): manifest file missing, extra key, missing key, wrong count, wrong reason.

Workflow and gates:

* T13: triggers are exactly `pull_request` with the five types, never
  `merge_group` or `pull_request_target`; concurrency is per pull request with
  `cancel-in-progress`; no `write`; no secret; no `continue-on-error`; actions pinned
  by SHA; scripts and `engine.lock` from a BASE checkout; one job; one label constant.
* T14: the label scenarios on the scratch repository: removed after green, added after
  push, applied by a Bot, by a triage-only user, by a listed user, a look-alike label
  name, a fork pull request, head branch `counts/refresh`, head branch not `redact/*`,
  a pull request that edits the guard.
* T15: label and allow-list logic as a pure function over fake API responses: API
  error, empty events, two `labeled` events by different people (last counts),
  `unlabeled` after `labeled`, a listed user demoted, an unlisted user promoted.
* T16: a rewrite pull request touching any path other than the two in section 8.
* T17: the stale bot case: a `counts/refresh` pull request built on the old history is
  refused after a rewrite lands; `unmerged-history.sh` still returns 3.
* T18: output holds counts only (no value, note or 64-hex digest in stdout, stderr or
  the summary).
* T19: the existing refresh tests pass unchanged (publish check still `cmp`).
* T20 (F1): the G1 scenario, as a repeatable script.
* T21 (F1, round 2): the workflow has no `merge_group` trigger and refuses to run if
  invoked under one; a two-pull-request group where both touch an allowed file, and a
  group where an unrelated pull request changes another path, are recorded as the
  reason the queue is unsupported (proved on the scratch repository, not built).
* T23 (F3): the G4 scenario.
* T24 (F5): issue events spanning more than one page; the last matching event on the
  last page; a truncated page chain fails.
* T25 (F5): label deleted and recreated; actor promoted or demoted after labeling; a
  custom role; a machine account typed `User` and not on the allow-list.
* T28 (F6): manual rerun ordering and duplicate check-context names on one head SHA;
  no other workflow can emit the required context.
* T35 (round 2, finding 2): overlapping runs and metadata changes: label removed or
  applied during a run, an older run finishing last, a manual rerun of an older run;
  the double read fails on a change; the authorization digest differs for each changed
  input; the manifest is read from the head, and a pull request body edit changes
  nothing.
* T36 (round 2, finding 3): a platform with `count=1000, as_of=2026-11-23` published
  and an older row `count=1000, as_of=2026-01-10` redacted: `numbers.json` unchanged
  passes; removing the current entry fails; and the same-count same-day entry must be
  removed and fails if it stays.
* T37 (round 2, finding 4): a rewrite fails while a matching open bot pull request
  exists (including one beyond the first API page); passes when none exists; the
  merge procedure re-checks (see T40).
* T38 (round 2, finding 1): the manifest file rules: a second manifest, one outside
  `history/redactions/`, an existing manifest changed or removed, a manifest with a
  value, a manifest not byte-equal to the reconstructed expectation.
* T40 (round 3, finding 3): the refresh-PR predicate: a fork pull request using the
  head name `counts/refresh` does not block; another author does not block; a pull
  request with base other than `main` does not block; a matching bot pull request on
  the last page of more than one page blocks; a failed or truncated traversal fails;
  one opening between the guard and the merge procedure is closed by the procedure
  and, if it opens after, is left to the closer (D13) without restoring any value.
* T41 (round 3, finding 2): the G2 experiment as a repeatable script that prints PASS
  or FAIL for branch A or B.
* T29 (F8): the wrapper's exit-status mapping (section 6.1).

## 15. Open owner decisions

Recommended answer first. Yes or no, or pick. MOVED marks a decision that changed since
round 1; NEW is added by round 1.

* **D1.** Enforce append-only on every pull request that touches
  `history/social.jsonl`, with the labeled tombstone-only rewrite the only exception.
  Recommended: **yes**, conditional on G1.
* **D2. (MOVED)** Authorization is a BASE-committed allow-list of GitHub logins, not a
  role mapping; label `history-tombstone`; head branch `redact/*`. For any instance with
  a second person, also require independent approval by ruleset or protected
  environment. Recommended: **yes**.
* **D3.** A new `history-guard.yml`, not `preview.yml`, one job. Recommended: **yes**.
* **D4. (MOVED, reversed)** Allow a tombstone over `hand_kept` and `imported` rows in
  this mechanism. Recommended: **no**; those redactions use the manual reviewed route.
  (Round 1 of the draft proposed yes; reversed because a self-authorizing maintainer
  could erase manual evidence.)
* **D5. (MOVED)** A rewrite may change `numbers.json` atomically, by removing the
  matching entry (section 8), and must when the redacted value is the published one.
  Recommended: **yes**. If no, the claim narrows to "history tombstoning" and the
  published number is dealt with first by an ordinary pull request.
* **D6.** Signed commits on `redact/*`. Recommended: **no for now**.
* **D7. (MOVED)** The detective push job, described as post-merge detection that depends
  on repository governance, with an off-GitHub notice. Recommended: **yes**.
* **D8.** Roll out through an `engine.lock` bump; an instance whose engine lacks
  `history-compare` fails closed. Order it so every protected pull request can still
  obtain a check: bump the engine first, add the workflow in a second pull request.
  Recommended: **yes**.
* **D9. (MOVED)** Accept that a sole owner is not protected from their own account.
  Recommended: **accept and say so in the README**; the alternative is a second,
  low-privilege approver account.
* **D10. (NEW)** Per-rewrite cap: 500 records and 10 percent. Recommended: **yes**.
* **D11. (NEW, MOVED in round 2)** A 24 hour cooling period from the label, described
  as a **notice only**, not as protection. Recommended: **yes** (the cost is a day per
  redaction; urgent legal removals use the manual route).
* **D12. (NEW, MOVED in round 2)** Merge protection is strict up-to-date (b), with the
  head-pinned watcher (c) as defense in depth. **Merge queue (a) is removed** (multi-PR
  merge groups, section 5.4). Recommended: **(b) plus (c)**. If G1 fails, do not build.
* **D13. (NEW, round 2)** The optional separate stale-refresh closer workflow
  (section 9). Recommended: **yes**, as a follow-up change after the guard. The guard
  already blocks a rewrite while a refresh pull request is open.
* **D14. (NEW, round 2)** The manifest is a committed file, kept in the tree as an
  audit trail (keys, reason, trigger date, no value), not text in the pull request body.
  Recommended: **yes**.
* **D15. (NEW, round 3)** Tombstones written for a rewrite use the derived forms of
  rule 16 (day-granular `redacted_at_utc`, derived `run_id`, closed vocabulary, no free
  text), which changes the #586 writer for new tombstones and gives up exact time of
  day, random run ids and free-text reasons. Recommended: **yes**; without it the
  compare cannot certify that a tombstone holds no value.
* **D5 (further MOVED).** Matching published entry means the same platform, count and
  `as_of` day for both mandatory and permitted removal; a same-count older row leaves
  `numbers.json` unchanged. Recommended: **yes**; the alternative is to refuse such
  redactions.

## 16. Review round 1 (Daybreak)

Rows F1 and F6 mention a merge queue; round 2 (section 17, finding 1) removed it.

| Finding | Severity | What changed, or why not |
| --- | --- | --- |
| F1 verification not bound to the merged base | Critical | Section 5.3 pins head, current `main` tip and merge-base and fails unless the head contains current `main`. Section 5.4 requires a merge queue with a `merge_group` guard, or strict up-to-date plus proof, or a head-pinned watcher re-running the guard (`--match-head-commit`); D12. Gate G1 and tests T20, T21 prove an unchanged head cannot merge on an old result. The first draft's claim that the guard sees a stale bot pull request was wrong (no event on a base move) and is withdrawn in section 9. |
| F2 `numbers.json` can retain the value | High | Traced (section 8, [V]): the latest `api` row and `numbers.json` carry the same number. Allowed paths are exactly `history/social.jsonl` and `numbers.json`; the compare requires removal-only changes to `numbers.json` that match a replaced row and fails if the value survives. Generated and deployed copies are listed and the claim is limited to the working tree. Fallback claim "history tombstoning" if D5 is declined. T22, T33. |
| F3 detective control trusts editable code | High | Section 11 no longer calls the push job independent. It uses the pinned reusable workflow and engine of the commit before the push, is classed as post-merge detection, and states that only a ruleset with independent approval and no bypass protects the guard; G4 and T23 test the ruleset, not CODEOWNERS presence. A sole owner cannot satisfy that ruleset and is told so. |
| F4 compromised maintainer destroys history | High | Sections 2 and 3.11 state the limit. Added: cap 500 and 10 percent (rule 13), manifest set-equality (12), 24 hour cooling (14), off-GitHub audit notice and tombstone lines as the record (11), `api` rows only (D4 reversed), independent approval recommended for multi-person instances. T27, T34. Not solvable for a sole owner (D9). |
| F5 label authorization underspecified | Medium | A BASE-committed allow-list replaces role mapping; evaluated at verification time; paginated events algorithm and current-label membership defined (section 4); machine users excluded by not being listed; token permissions are gate G5. T24, T25. |
| F6 required-check identity and unlabel revocation | Medium | One job and one context, pinned to the Actions app, collision scan (5.1); the unlabel question became design gate G2 with the merge-queue fallback; `merge_group` added (G3, T21); T28. |
| F7 ambiguous comparison identity | Medium | Grammar stated from `_parse` [V] (section 6.2): unique record key, one entry per day key, corrections anywhere after and not necessarily contiguous; the compare drops corrections by key set, not by position. T26. |
| F8 exit-code handling | Medium | Section 6.1: `rc=0; ... \|\| rc=$?` then a `case` accepting only 0 and 10; 10 reserved in the CLI contract; 124-127, 137, 143 are failures. T29. |
| F9 canonicality conflated | Low | Section 6.3 rule 6 split by mode: unchanged prefix byte-identical, appended lines canonical, reconstructed rewrite lines canonical, legacy bytes only in an identical prefix. T30. |

## 17. Review round 2 (Daybreak)

| Finding | Severity | What changed, or why not |
| --- | --- | --- |
| 1. A merge group can contain several pull requests | Critical | Applies. The merge queue is **removed** from the design and the recommendation (5.4, D12): the guard reads one label, applier, branch, cooling period and manifest and cannot attribute an aggregate diff to one pull request, and an exact-paths rule on the aggregate would deny unrelated queued pull requests. `merge_group` is no longer a trigger (T13), the workflow refuses to run under it (T21), and G3 now proves that and documents that enabling a queue on `main` loses this protection. Strict up-to-date (b) is the only protection, with the watcher (c) as defense in depth; if G1 fails nothing is built. |
| 2. A green check is not bound to the body or current label state | High | Applies. The manifest moved out of the pull request body into a committed file in the head (rule 12), so a green result is bound to a particular manifest; the body is read by nothing and `edited` is no longer a trigger. The label, its age, the applier and the allow-list cannot be bound to a head SHA, so section 5.5 states what the check is and is not bound to, adds per-pull-request concurrency with `cancel-in-progress`, a double read of the mutable state, and an authorization digest that the merge procedure recomputes before merging. G2 now covers overlapping runs, manual reruns and every mutable input; if GitHub does not make the newest evaluation authoritative the label is documented as an intent record verified at merge. T35. This is a procedure, not branch protection, and the spec says so. |
| 3. `numbers.json` rules can make an older-row redaction impossible | Medium | Applies. One predicate (same platform, count and `as_of` day) now decides both mandatory and permitted removal (section 8). A same-count older row leaves `numbers.json` unchanged because the current entry is the published fact for its own day; the alternative (refuse such redactions) is D5's other answer. T36. |
| 4. An open stale bot pull request can hold the refresh down | Medium | Applies. Rule 15 makes "no open `counts/refresh` pull request" a required condition of the guard in rewrite mode, repeated by the merge procedure. An optional separate closer workflow with `pull-requests: write` only handles the post-merge case (D13, section 9). T37. The hold by someone who can reopen the branch is stated as an availability limit. |
| 5. The detective workflow cannot enforce use of the pre-push pin | Medium | Applies. Section 11 now states only: when the newly merged detective workflow executes honestly, it selects the pin recorded by the pre-push commit. Independence would need an externally controlled workflow or dispatch, which is not added (D7). |
| 6. "Every other file" is not established | Low | Applies. Section 8 is renamed "Known working-tree and deployment copies"; it says absence of other copies is not established and that the two-path limit can stop a rewrite that must clean another copy (manual route). New gate G7 inventories tracked consumers and generated inputs. |

Round 1 assessment items taken: D11 is a notice, not authorization (rule 14, D11); D12
no longer offers a merge queue or the watcher alone; D5's predicate is corrected.

## 18. Review round 3 (Daybreak, the last allowed round)

| Finding | Severity | What changed, or why not |
| --- | --- | --- |
| Redacted value encoded in permitted metadata (`run_id`, `reason`, manifest) | High | Applies; confirmed against `validate_tombstone`, which accepts any `redact-` run id and any plain date **[V]**, and against the old rule that the manifest only had to repeat the tombstone's reason. New rule 16: every field of a new tombstone is equal to the old record's, drawn from a closed vocabulary, or derived (reason from the five codes; `trigger_date` in a 366 day window; `redacted_at_utc` at day granularity on the pull request's creation day or the day before; `run_id` derived from the manifest keys, not random; one run id and one set of metadata for the whole rewrite, equal to the manifest file name stem). The manifest is reconstructed byte for byte, so it has no free line, comment or whitespace (rule 12). Residual channel stated: about 12 bits per rewrite, shared by all records, public keys, not a value of 4096 or more. Lost: time of day, random run ids, free-text reasons (they go to the PR description). New gate G8 and tests T39, T38; the writer change is D15. |
| G2 has no binary pass condition | Medium | Applies. G2 is now an exact experiment (steps, observables `mergeStateStatus` and the merge endpoint status) with PASS (branch A: current label presence is enforced by branch protection) and FAIL (branch B: label authorization is irrevocable for `(head, base tip, manifest blob)` after the cooling period, with no claim of current-label presence at merge). Either result is a defined, buildable design; "not run" blocks the build. `cancel-in-progress` is no longer credited with revocation (5.5 item 6). T41. |
| Rule 15 raceable, PR identity too broad | Medium | Applies. Rule 15 now defines the object (open, base `main`, same repository, head ref exactly `counts/refresh`, author `github-actions[bot]`), reads it by explicit REST pagination and not `gh pr list --head`, and is described as best-effort availability protection. The merge procedure closes the verified bot pull request immediately before the head-pinned merge; a pull request that opens after that cannot restore values (strict up-to-date plus the guard) and is cleared by the closer (D13) under the same predicate. A fork pull request with that branch name does not block a rewrite. T40. |

## 19a. Implementation round 2 (Daybreak): open risks stated in the template

* **The workflow is not a sufficient merge gate on its own (High, architectural).** With
  a `pull_request` trigger GitHub runs the workflow file of the merge ref, so a pull
  request can replace `history-guard.yml` and keep the required check name; pinning the
  scripts to the base commit helps only after the workflow ran its checkout. Not solved
  in code. The template says so (README "History rewrites", the workflow header) and
  requires one trust boundary, decided by the owner (2026-11-26): the ruleset rule
  "Require workflows to pass before merging" pointing at a workflow in a separate
  trusted repository, so a pull request cannot replace it (organization or enterprise
  level per GitHub's page read 2026-11-26; plan availability INFERRED and UNVERIFIED;
  gates G1 and G5 not yet run). It is the sole accepted boundary. CODEOWNERS entries
  for `.github/`, `history/`, scripts, lock and approvers paths with "Require review
  from Code Owners" (author not a code owner, dismiss stale reviews on, administrators
  included) and a limit on who can push `redact/*` branches are defense in depth only,
  not a boundary (a pull request from any branch can replace the workflow, and a
  privileged author can satisfy or dismiss a review). `.github/history-approvers`
  ships empty and the README checklist demands an OBSERVED block by the external
  workflow (a test pull request that replaces the guard workflow fails the required
  check), plus G1 and G5, before any approver login is added. The High is closed in
  the template text only when that live observation is made.
  Defense in depth: the first job steps compare the running workflow file with the base
  commit's copy and fail on a difference; a replaced workflow can omit that step.
* **`actions: read` is capability-wide (Low).** It also reads the logs and artifacts of
  the repository; there is no narrower built-in permission for the run list alone. It
  is acceptable only together with the trust boundary above; G5 proves it is sufficient
  and that no write call works.
* **Crash durability (Medium, fixed).** The writer fsyncs the parent of a newly created
  `redactions` folder and the folder itself after the temporary manifest is written,
  before the history file is replaced.
* **Recovery command (Medium, fixed).** The printed step compares with `cmp` and links
  without overwriting (`ln` then `rm`); never `mv`.
* **Head restore.** A forced push, or a deleted and restored head branch, moves the
  head-change time; the Actions run listing is paged and fails closed at the page cap.

## 19. Not covered

* Rewriting git history of the instance repository (#621; a tombstone meets the duty
  in the working tree only).
* Automatic redaction timers (decision 8 of the tombstone design).
* Anything about the public engine repository's own CI.
