<!-- SPDX-License-Identifier: Apache-2.0 -->
# Social-stats history

`reach-around counts refresh` keeps only the current number in `numbers.json`.
The history store keeps every fetched and hand-kept number, so before and after
comparisons and growth figures are possible later. It is one append-only text
file. It lives in your private instance repository, never in this public repo,
and the engine only reads and appends to it. Nothing here collects data, draws
a chart or touches the network; `counts refresh` already did the fetching.

The store is off until you set `numbers.history_file` (see [config](config.md#numbers)).

## Format

One JSON object per line, UTF-8, each line ends with a newline. All times are
UTC. Whole numbers only (no floats, no `NaN`).

```json
{"as_of_utc":"2026-11-08T09:00:00Z","metric":"followers","platform":"bluesky","prev":"0000000000000000000000000000000000000000000000000000000000000000","run_id":"refresh-20261108T090000Z-1a2b3c","source":"api","value":1234}
```

| Field | Meaning |
| --- | --- |
| `platform` | lowercase slug, the same key the numbers file uses |
| `metric` | `followers` by default; `subscribers` (YouTube) and `members` (Discord, Matrix) from `counts refresh`; anything lowercase with digits and underscores for hand-kept values |
| `value` | integer from 0 to 10^12 |
| `as_of_utc` | `YYYY-MM-DDTHH:MM:SSZ`; never in the future when written |
| `source` | `api` (fetched), `hand_kept` (typed by you) or `imported` (batch import, a later feature; the value is reserved so the three stay distinguishable) |
| `run_id` | which run wrote it; fetched runs share one id per refresh |
| `prev` | SHA-256 (hex) of the previous line's bytes, without the newline; 64 zeros for the first line |
| `note` | optional text, up to 200 characters |
| `era` | optional label for a definition break (see below) |
| `type` | omitted for a normal record, or `correction` |

Unknown fields, duplicate keys, nesting, floats, `NaN`, control characters,
lines over 2 KiB and files over 256 MiB are refused. Retention is unbounded
append: the 25-month rule belongs to link-page traffic rollups, not to this file.

One normal record per platform, metric, source and UTC day. A second one for the
same day is refused; a correction of that day's record is allowed. A repeated
`counts refresh` the same day keeps the first value and says so.

A failed or skipped source writes nothing here. The day stays a gap. A gap is
never stored or reported as zero. The per-source result of each run (ok, error
class, skipped) is in the status file beside `numbers.json`.

### Definition breaks (`era`)

When a platform changes what a number means, tag the records on either side so
nobody reads a jump as growth. Examples recorded in the research notes: Kick
`active_subscribers_count` began to include gifted subscriptions on 2026-08-11,
and the YouTube `views` definition changes around 2026-08-24 (Data API) and
2026-08-27 (Analytics). Add the tag with `--era` when you type a value, or
mark a fetched record with a correction (below).

## Append-only guarantees

* An append opens the folder without following a symlink, opens the file in
  append mode (mode forced to 0600), takes an exclusive `flock`, reads and
  checks the whole file, then writes the new lines in one write and `fsync`s.
  If the write or the `fsync` fails, only the bytes this call added are cut off
  again; if even that fails the error says to run `history-verify`.
  Two writers at once take turns; neither loses a line.
  Writers (append, redact, removal of stale temporaries) also take turns through
  a small lock file, `.<file>.lock` (for example `history/.social.jsonl.lock`),
  created beside the history file with mode 0600 and never replaced or renamed,
  so its identity stays the same while a redaction replaces the history file.
  Reading and `history-verify` never create or need it. It holds no data; keep
  it out of commits (the example instance repository ignores `history/.*.lock`).
  Just before the write an append checks that the file name still refers to the
  file it locked, and checks again after the write; if something outside (a
  `git checkout` or `git pull` in the same folder) replaced the file in
  between, the append reports it instead of claiming success, and the file that
  was swapped in is left as it was.
  The same check covers the file's size: it is read to exactly the size it had
  when first looked at, and looked at again just before the write. A write by
  something that ignores the lock (it grows or shortens the locked file while
  the scan runs) is refused with "changed while it was read". The lock is
  advisory: a writer that ignores it and changes bytes in place without changing
  the size cannot be detected here; the hash chain and git history catch that.
* A refresh or import that stops after history was written leaves a small
  `.<numbers file>.pending` note (run ids and a digest of the numbers file, never
  values). If that note exists but cannot be read or is not valid, the next run
  stops before touching anything, prints a fixed message, and leaves the note
  as it is; inspect or remove it, then run again. A note left behind after the
  numbers file was written is recognized (the digest no longer matches) and
  ignored, so a committed day is never rewritten.
* It refuses a file that is already damaged, a duplicate
  (`platform`, `metric`, UTC day, `source`), a future time, and bad fields.
  A batch is all or nothing.
* The `prev` hash chain makes a changed, removed, inserted or reordered earlier
  line fail `counts history-verify` with "the hash chain is broken". The last
  line has no successor, so the chain cannot see a change to it; git history
  does (see below).
* A crash in the middle of a write leaves a last line without its newline. That
  is reported as "truncated" and every append is refused until you deal with
  it. It is never dropped silently. Repair it yourself from git:
  `git checkout -- path/to/file` restores the last committed copy, then run
  `counts history-verify`.
* A mistake is fixed by a correction record: `type: correction` with the same
  platform, metric, time and source as the line it fixes, the right value, and a
  required `note`. The original line stays. Readers use the latest correction
  for that key and mark the point as corrected.

## Commands

```bash
# Fetched values are appended by the daily refresh when history_file is set.
reach-around counts refresh --config site.yaml

# A hand-kept value (LinkedIn, Kick followers, TikTok followers). Date or UTC time.
reach-around counts history-add --config site.yaml --platform kick \
  --value 4200 --as-of 2026-11-01 --note "read from the dashboard"

# Tag a definition break, or correct a typo.
reach-around counts history-add --config site.yaml --platform kick \
  --metric subscribers --value 310 --as-of 2026-11-01 --era includes-gifted-2026-08-11
reach-around counts history-add --config site.yaml --platform kick \
  --value 4020 --as-of 2026-11-01 --correction --note "misread the digits"

# Check the whole file. Exit 0 intact, 1 damaged (incl. truncated last line).
reach-around counts history-verify --config site.yaml
```

`--file PATH` replaces `--config` where you only have the file. Hand-kept
values are written with `--source hand_kept` (the default); `--source api` is
for correcting a fetched record.

## How the instance repo commits it

`counts refresh` writes the history file in place, before it touches
`numbers.json`, so a history problem leaves every file as it was. `history_file`
must be a relative path inside the config's folder (no `..`, no symlinked
folder, not the config, numbers, status or lock file); put the config in your
private instance checkout and the file lands there.

The bundled example workflow (`examples/instance-repo`) carries the history file
when `numbers.history_file` is `history/social.jsonl`. The refresh job mounts the
history folder writable for the engine only, checks the result with
`counts history-verify` (offline, read-only), and hands it across the job
boundary beside `numbers.json`. The publish job checks it again, refuses a file
that does not begin with every byte of the committed one (append-only), and
stages it in the same pull request (and skips the run, with a warning, while the open bot branch holds history rows main does not have yet, so they are never discarded; only `history/social.jsonl` is carried, and any other file the engine writes in that folder stops the run, except the engine's lock file `history/.social.jsonl.lock`, which is tolerated and not carried), which keeps the same bot-only and
files-changed checks (`numbers.json` and `history/social.jsonl` only). If you
run the refresh by hand, run `reach-around counts history-verify` before the
commit so a damaged file is never committed over a good one, and use a plain
fast-forward push; never force-push the history.

Keep the file out of anything public: it is not read by `reach-around build`,
and a public site or artifact should only ever get floored, labeled aggregates.

## Restore from git history

Because every day is a commit, the file can be rebuilt from git:

```bash
git log --oneline -- history/social.jsonl     # find a good commit
git checkout <commit> -- history/social.jsonl # restore that version
reach-around counts history-verify --config site.yaml
```

Appends after the restored commit start from its last line. Values fetched
after that commit are not recoverable from the platforms (a past follower count
cannot be re-fetched), so restore the most recent commit that verifies.

## Redact stored values (tombstones)

Use this when a platform's terms, a revocation or a request means a stored API
value must go. It is the one command that rewrites the file, and it is
owner-run: nothing starts it for you, `counts refresh` never calls it.

Do not run a redaction and a refresh at the same time, and do not start a local
refresh (`scripts/refresh-counts.sh`) while a redaction is running or just
before you start one. Two things protect you, and neither replaces that rule:

* The writers (`history-add`, the engine's append, a redaction, removal of
  leftovers) take turns through the lock file. An append that waits for the lock
  while a redaction replaces the file is refused ("replaced while waiting"),
  not continued on the new file: run it again. An append that did not wait is
  unaffected, and so is one that creates the file. A writer that bypasses the
  lock can also be refused, when its file turns out to have been replaced. A
  redaction that cannot get the lock in time refuses.
* The example refresh script (`examples/instance-repo/scripts/refresh-counts.sh`)
  works on a scratch copy and moves the result over `history/social.jsonl` at the
  end. It records the inode and SHA-256 of the file it copied and, before
  anything is written and again under the lock, refuses with fixed words, writing
  nothing, if the file is no longer the same. It needs `flock` and refuses to
  install the history file where that is missing.
  Without that, a redaction run during a local refresh would be undone and the
  removed values would come back. If it refuses, run the refresh again.

A tombstone replaces the line of a redacted record. It keeps the key (platform,
metric, as-of time, source), a `reason` from a closed list (`platform_expiry`,
`platform_revocation`, `platform_request`, `user_request`, `owner_decision`),
the `trigger_date` that started the clock, and when it was done. For an API
record it also keeps the `api_provider` tag of the record it replaces. It holds
no value, note or era, and no free text. The corrections of a redacted record are
removed with it. Every later line is re-chained, so no digest of the old bytes
stays in the file. A redacted day is a gap in the series, never a zero, and the
same key cannot be filled again. Only `api` rows are redacted; `hand_kept` and
`imported` rows need `--include-hand-kept` (an owner choice, not a duty).

To redact by provider (for example the private analytics records), use
`--provider youtube_analytics` or `--provider instagram_insights` instead of
`--platform`. The tag on the record is
the only selector; a platform name is never used to guess a provider.
A provider selection covers only records that carry the tag. Rows written
before the tag existed have none, so for an older file also run
`--platform <slug>` to cover them.

1. Record the trigger and its date (revocation, platform request, user request).
2. Dry run (the default; prints counts only, never a value, note or hash):

   ```bash
   reach-around counts history-redact --config site.yaml --platform youtube \
     --source api --before 2026-10-20 --reason platform_expiry \
     --trigger-date 2026-10-20
   ```

3. Check the count, then add `--confirm`. The file is written beside the old
   one, fsynced and renamed into place. A failure before the rename leaves the
   original (exit 1). If the rename worked but the folder sync failed, the new
   file IS in place and its durability is uncertain: the command says so in
   fixed words and exits 5. The same exit also covers a rename that reported an
   error but may have happened, and an installed file that did not read back as
   written. Run `history-verify` before anything else.
   The swap itself is atomic where the system supports it (`renameat2` with
   `RENAME_EXCHANGE`, Linux): the new file and the old one trade names, the old
   one is checked to be the file that was locked, and if it is not (something
   outside replaced it meanwhile) the trade is undone and nothing is lost. The
   undo is trusted only after both names are checked: the history name must hold
   the file that was swapped out and the temporary name the new output. If a
   second replacement lands during the undo, nothing more is touched: the command
   says so in fixed words and exits 5, and a newer file may then sit under a
   `.<file>.redact-*.tmp` name. Look at the folder and run `history-verify`
   before anything else. Cleanup never removes such a file (see below).
   If the swap worked but the old copy, which still holds the redacted values,
   cannot be removed, the command retries once, then exits 5 and names the file
   and says plainly that the old values remain on disk until it is removed.
   Run the same command with `--confirm` again: it removes that old copy at once,
   whatever its age, because its marker proves what it is. If that fails too,
   remove the named file by hand.
   Where the system cannot do that, the redaction is refused and nothing is
   changed, because a plain rename cannot replace a file only while it is still
   the locked one. Run it on a system with `renameat2` support.
   The new file is mode 0600 and keeps the old owner and group where the
   system permits (a notice says when it could not), whatever the umask. ACLs, extended attributes
   and security labels are not copied. Redaction needs a path with no symlinks: every
   folder in the path to the file is opened without following symlinks, and a
   symlink anywhere in it (also in the dry run) is refused, so resolve the path
   yourself and pass the real one (`--file`). Reading, verifying, appending and
   `counts refresh` are unchanged and still follow a symlinked ancestor such as a
   symlinked home or checkout folder; they refuse only a symlinked last folder or
   file, as before. A plan that no longer matches the file in every
   field, or that lists a record twice, is refused.
   A crash can leave a `.<file>.redact-<8 hex>.tmp` beside the history file. It
   holds the proposed redacted output, so treat it as private. Before the swap a
   small marker file `.<file>.redact-<8 hex>.mark` records the inode and SHA-256
   of the new file and of the file it replaces. The dry run lists leftovers by
   count and name (names are printed escaped); `--confirm` removes only a file
   that is regular, yours, of the exact name pattern, and that its marker proves
   byte for byte is the redaction output (and an hour old) or the displaced old
   copy (any age). A name alone proves nothing: a file with no marker, a changed
   file, or a file under a temporary name that is not the recorded one is never
   removed. A marker whose temporary is gone is removed once it is an hour old.
   A file named like a temporary but with no marker (a crash before the marker
   was written) is never removed automatically; the dry run only says how many
   there are, by count. Look at them and remove them by hand.
   Until the recovery rerun has removed a leftover, it may hold the very values
   you are deleting: never `git add -A` or `git add history/`; add the one file
   by name. The example `.gitignore` ignores `history/.*.redact-*` (temporary and
   marker) and `history/.*.lock`, which covers a careless `git add`; copy those
   lines into your own `.gitignore` if you have not started from the example.
4. `reach-around counts history-verify --config site.yaml` must exit 0.
5. Commit the file to the private instance repository in its own pull request.
   The bundled workflow still refuses a file that is not a byte-prefix of the
   committed one; accepting a labeled tombstone-only rewrite is separate work
   (issue #586 follow-up), so until then commit this one by hand.
6. A tombstone meets the deletion duty in the working tree only. Git still
   holds the old versions. If the deadline needs true deletion (Social Blade,
   or any case where git history would break it), the private-repository
   history rewrite is a separate operation with its own approval, backup and
   restore plan.
7. Write down the date and what was redacted (counts only) in the instance log.

Running it again with the same arguments redacts 0 lines and changes nothing.

## Reading it in code

`reach_around.counts.history.read_history(path)` validates the whole file and
returns the records. `history.series(platform, metric)` gives the points with
corrections applied, `by_day()` the latest point of each UTC day, and
`gaps(first, last)` the days with no point. The growth display is separate work.
