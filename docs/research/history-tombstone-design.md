<!-- SPDX-License-Identifier: Apache-2.0 -->
# History store tombstone: design for owner decision

Status: APPROVED 2026-11-19, built in the stacked pull request for issue #586. Epic: #364.
Follows #382 and the owner's 2026-11-19 decisions in
[platform-terms-retention.md](platform-terms-retention.md). The owner's answers are
recorded in "Decisions (2026-11-19)" below. The Schema, Verifier rules and Function
interfaces sections describe what was built. The "Decisions put to the owner"
section is the question list as it was put, with its Option A / Option C wording
kept for the record.

## Why this is a design and not a patch

The issue asks for "a record that blanks a value but keeps the line and its hash".
Three facts in the current code make that more than a new record type:

1. **A blanked line changes the line's bytes.** The next line's `prev` is the
   SHA-256 of the previous line's exact bytes (`_digest(raw)` in
   `counts/history.py`). Replacing a line with a tombstone breaks the next line's
   `prev` unless the verifier is taught that a tombstone stands in for a stated
   digest.
2. **Keeping the hash keeps a handle on the value.** The next line's `prev`
   already holds `sha256(original line)`. The original line is low entropy: known
   platform, metric and date, a value of at most 10^12, and a `run_id` with 24
   random bits. That is a search space an attacker can walk. So "keeps the line's
   hash" and "keeps no recoverable value" pull against each other. Only a
   re-chain (rewriting later `prev` values) removes the handle.
3. **Git still holds the old bytes.** The private instance repository keeps every
   committed version of the file. A tombstone in the working file is a redaction
   in the current tree and nothing more. Platforms that require deletion (YouTube
   III.D.2 and III.E.4, Twitch, Kick, Meta 3.d) are met in the tree only. Social
   Blade section 13 (certified destruction) is not met by a tombstone at all; see
   the research doc.

Also, the example instance workflow refuses a history file that does not begin
with every byte of the committed one (append-only). Any in-place rewrite needs a
narrow, explicit exception there.

## What the tombstone must do

* Remove the value, the note and the era text of a stored record from the working
  file.
* Leave an auditable line: which record was redacted (platform, metric, as-of,
  source), why (an enum), which trigger date applies, and when it was done.
* Keep no personal data. The owner's own follower counts are not personal data in
  the usual sense, but a platform's end-user data or a notes field could be, so
  the tombstone stores none of it.
* Keep `counts history-verify` passing, and keep the series readers gap-honest: a
  tombstoned day is a gap, never zero.

## Schema

A new `type` value, `tombstone`. A tombstone line replaces the line it redacts,
and every later line is re-chained (Decisions 1 and 3).

```json
{"as_of_utc":"2026-10-01T09:00:00Z","metric":"subscribers","platform":"youtube","prev":"<64 hex>","reason":"platform_expiry","redacted_at_utc":"2026-11-19T12:00:00Z","run_id":"redact-20261119T120000Z-1a2b3c","source":"api","trigger_date":"2026-10-20","type":"tombstone"}
```

| Field | Meaning |
| --- | --- |
| `type` | `tombstone` |
| `platform`, `metric`, `as_of_utc`, `source` | the key of the redacted record, unchanged, so readers know which point is a gap |
| `value` | absent. Not stored, not zero |
| `reason` | one of `platform_expiry`, `platform_revocation`, `platform_request`, `user_request`, `owner_decision` (closed list, no free text) |
| `trigger_date` | `YYYY-MM-DD` of the revocation, request or expiry that started the clock; used to prove the deadline was met |
| `redacted_at_utc` | when the tombstone was written; never in the future |
| `run_id` | a `redact-...` id so a tombstone run is distinguishable from a refresh |
| `prev` | digest the chain expects at this position, same as every line |
| `api_provider` | optional; the provider tag of the record it replaces (a recognized tag, on `api` rows only), so provider-based selection still finds it. Never a value |

There is no `replaces` field: no digest of the original line is kept.

`note` and `era` are not allowed on a tombstone (free text is where personal data
hides). Unknown fields, duplicate keys and the 2 KiB line limit still apply.

Verifier rules (in `_parse` and `_parse_line`):

* A tombstone's `prev` must equal the running expected digest, like any line.
* After a tombstone, the expected digest is the digest of the tombstone line
  itself; later lines were re-chained when it was written.
* A tombstone over a key that still has a live record in the file is refused, and
  so is a second tombstone for the same key.
* A normal record or correction after a tombstone for the same key is refused:
  a redacted day cannot be re-filled from the same source and date.
* The duplicate-day rule keeps counting the tombstoned key, so a refresh the same
  day cannot silently re-store the value.

## Function interfaces

All in `src/reach_around/counts/history.py` unless noted. Shown as built, in
outline (see the module for the full signatures).

```python
REASONS = (
    "platform_expiry",
    "platform_revocation",
    "platform_request",
    "user_request",
    "owner_decision",
)


@dataclass(frozen=True)
class Tombstone:
    platform: str
    metric: str
    as_of_utc: str
    source: str
    reason: str
    trigger_date: str  # YYYY-MM-DD
    redacted_at_utc: str
    run_id: str
    api_provider: str | None = None  # provider tag of the replaced record


def plan_redaction(
    history: History,
    *,
    platform: str | None = None,
    source: str | None = None,
    before: str | None = None,
    metric: str | None = None,
    include_hand_kept: bool = False,
    provider: str | None = None,  # selects by the api_provider tag only
) -> list[Record]:
    """Pure. The records a redaction would blank. Never touches disk."""


def redact_records(
    path: Path,
    plan: Sequence[Record],
    *,
    reason: str,
    trigger_date: str,
    now: datetime | None = None,
    confirm: bool = False,
    include_hand_kept: bool = False,
) -> int:
    """Under the writers' lock file. Verifies the whole file first, writes the
    new file beside the old one, swaps it into place, and returns the number of
    records redacted. Refuses unless confirm is True."""


def tombstoned(history: History) -> frozenset[tuple[str, str, str, str]]:
    """Keys that are redacted. Series skips them and counts them as gaps."""
```

CLI (in `commands/counts.py`), after approval:

```bash
reach-around counts history-redact --config site.yaml --platform youtube \
  --source api --before 2026-10-20 --reason platform_expiry --trigger-date 2026-10-20 \
  --dry-run          # prints counts only, never values
# or select by provider tag: --provider youtube_analytics (instead of --platform)
reach-around counts history-redact ... --confirm
```

`--dry-run` is the default and prints only the number of lines and the date
range. Output never includes a value, note or hash.

## Runbook step (now in docs/social-history.md)

1. Record the trigger and its date (revocation, platform request, user request).
2. Run `history-redact --dry-run`; check the count matches expectations.
3. Run with `--confirm`.
4. Run `counts history-verify`; expect exit 0.
5. Commit the file to the private instance repository in its own pull request
   (the workflow's append-only check allows only tombstone replacements, see
   Question 4).
6. If the deadline requires true deletion (Question 6): run the separate
   history rewrite of the private repository with its own approval, backup and
   restore plan. A tombstone is not that.
7. Write down the date and what was redacted (counts only) in the instance log.

## Decisions put to the owner

Recommended answer first on each. Answer yes or no.

1. **Should the tombstone remove the original line's content from the working
   file (Option A, in-place replacement), rather than only append a marker that
   hides it (Option C)?** Recommended: YES. Option C meets "stop displaying" and
   nothing else; the value stays in the file, which fails Twitch, Kick and Meta
   deletion duties in the tree.
2. **Is it acceptable that tombstoning rewrites the file (atomic write beside the
   original, then rename) as an explicit, owner-run command, never during
   `counts refresh`?** Recommended: YES. Refresh stays append-only and its
   guarantees do not change.
3. **Re-chain every later `prev` after a tombstone (removes the brute-force handle
   described above), instead of keeping `replaces`?** Recommended: YES. It costs
   one rewrite of the later lines, which Question 2 already allows, and it leaves
   no digest of the old bytes in the file. The cost: a tombstone can then not be
   checked against the committed line the way Option A's `replaces` can; the
   audit is the `redacted_at_utc`, `reason` and `trigger_date` fields, and git.
4. **Let the instance workflow accept a history file that is not a byte-prefix of
   the committed one, but only when the change is tombstone replacements and a
   re-chain, in a pull request labeled for redaction?** Recommended: YES. Without
   it the publish job rejects every redaction. The check stays strict otherwise.
5. **Make `reason` and `trigger_date` required and the reason a closed list, with
   no free-text note on a tombstone?** Recommended: YES. Free text is where
   personal data would be copied in.
6. **Treat a tombstone as meeting the deletion duty in the working tree only, and
   keep the private-repository history rewrite as a separate approved
   operation per case (needed for Social Blade and for any deadline git history
   would break)?** Recommended: YES. It matches the research doc and avoids
   claiming a deletion that git still holds.
7. **Tombstone `api` rows only; refuse to redact `hand_kept` rows unless
   `--include-hand-kept` is passed?** Recommended: YES. Hand-kept numbers carry no
   platform API data (research doc, item 4), so redacting them is an owner choice,
   not a duty.
8. **Add a per-platform `retain_days` that makes `counts refresh` print a warning
   (not redact) when `api` rows near expiry?** Recommended: YES, as a later
   follow-up issue, not in this one. Automatic redaction on a timer is a separate
   decision and is not proposed here.

## Tests

Red before green, in `tests/test_counts_history.py`:

* a tombstoned file verifies, and the series shows a gap, not zero;
* a tombstone line contains no value, note, era or digest of the old line (Q3);
* a normal record for a tombstoned key is refused;
* a tampered later line after a re-chain fails with "the hash chain is broken";
* `redact_records` without `confirm` writes nothing; with a failing write the
  original file is unchanged (atomic rename);
* `--dry-run` output holds counts only (negative case: assert no value appears);
* the workflow check accepts a tombstone-only rewrite and refuses a rewrite that
  changes any non-tombstoned line.

## Not covered

* True deletion from git history of the private repository (Question 6).
* Social Blade certified destruction (research doc, "Social Blade").
* Automatic expiry timers (Question 8).

## Decisions (2026-11-19)

The owner approved all eight recommended answers on 2026-11-19.

1. Yes. A tombstone removes the original content in place (Option A); a marker
   that only hides it (Option C) is not used.
2. Yes. The file is rewritten atomically by an owner-run command, never during a
   refresh.
3. Yes. Later records are re-chained; no digest of the old bytes is kept, so the
   tombstone has no `replaces` field.
4. Yes. The instance workflow may accept a labeled tombstone-only rewrite. Not
   built in the code pull request; it is a follow-up change to the example
   workflow.
5. Yes. `reason` (closed list) and `trigger_date` are required; no free text.
6. Yes. A tombstone meets the deletion duty in the working tree only; a
   private-repository history rewrite stays a separate approved operation.
7. Yes. Only `api` rows are tombstoned; `hand_kept` (and `imported`) rows need
   `--include-hand-kept`.
8. Yes, later. A per-platform `retain_days` warning on `counts refresh` is a
   follow-up issue, not part of this change.

Build notes: one tombstone is written per redacted record, and the corrections
of that record are dropped with it, so `redact_records` returns the number of
records redacted. A refresh that waited on the lock through a redaction is
refused (the file was replaced) rather than appending to the old file.
