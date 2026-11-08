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
| `source` | `api` (fetched) or `hand_kept` (typed by you) |
| `run_id` | which run wrote it; fetched runs share one id per refresh |
| `prev` | SHA-256 (hex) of the previous line's bytes, without the newline; 64 zeros for the first line |
| `note` | optional text, up to 200 characters |
| `era` | optional label for a definition break (see below) |
| `type` | omitted for a normal record, or `correction` |

Unknown fields, duplicate keys, nesting, floats, `NaN`, control characters,
lines over 2 KiB and files over 256 MiB are refused. Retention is unbounded
append: the 25-month rule belongs to link-page traffic rollups, not to this file.

A failed or skipped source writes nothing. The day stays a gap. A gap is never
stored or reported as zero.

### Definition breaks (`era`)

When a platform changes what a number means, tag the records on either side so
nobody reads a jump as growth. Examples recorded in the research notes: Kick
`active_subscribers_count` began to include gifted subscriptions on 2026-08-11,
and the YouTube `views` definition changes around 2026-08-24 (Data API) and
2026-08-27 (Analytics). Add the tag with `--era` when you type a value, or
mark a fetched record with a correction (below).

## Append-only guarantees

* An append opens the file in append mode, takes an exclusive `flock`, reads and
  checks the whole file, then writes the new lines in one write and `fsync`s.
  Two writers at once take turns; neither loses a line.
* It refuses a file that is already damaged, a duplicate
  (`platform`, `metric`, `as_of_utc`, `source`), a future time, and bad fields.
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

Your daily workflow runs `counts refresh`, then commits the changed history file
(with `numbers.json`) to the private instance repo using the narrow credential
you already use for that, and pushes. Run `reach-around counts history-verify`
in that workflow before the commit so a damaged file is never committed over a
good one. Commit with a plain fast-forward push; never force-push the history.
Set `history_file` to a path inside the repo checkout (relative paths are
relative to the config file).

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

## Reading it in code

`reach_around.counts.history.read_history(path)` validates the whole file and
returns the records. `history.series(platform, metric)` gives the points with
corrections applied, `by_day()` the latest point of each UTC day, and
`gaps(first, last)` the days with no point. The growth display is separate work.
