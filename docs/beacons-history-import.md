<!-- SPDX-License-Identifier: Apache-2.0 -->
# Importing Beacons link-page history

Beacons offered the owner an all-time **Link in Bio analytics** CSV export. The
format below was verified from an export made on 2026-11-10. The real export is
private and must not be added to this repository.

## Verified export format

The file is UTF-8, comma-separated CSV with this exact header:

```csv
Section,Item,Detail,Views,Clicks,CTR (%)
```

It has these sections:

| Section | Item | Detail | Views | Clicks | CTR (%) |
| --- | --- | --- | --- | --- | --- |
| `Total` | `Link in Bio` | empty | integer | integer | number |
| `Daily` | date in `YYYY-MM-DD` form | empty | integer, including zero | integer | empty |
| `Links` | link title | destination URL | empty | integer | empty |
| `Social icons` | platform label | destination URL | empty | integer | empty |
| `Sources` | referrer host | empty | integer | integer | number, which may exceed 100 |
| `Devices` | device label | empty | integer | integer | number |
| `Countries` | lowercase region or locale label | empty | integer | integer | number |

The importer validates every section but stores only `Daily`. It does not store
link titles, destination URLs, referrer hosts, device labels, region labels, or
the export total. Each stored daily value has `source: imported` and
`note: Beacons`.

## Import

Choose a new private file that is separate from the history file populated by
`counts refresh`:

```text
reach-around counts history-import-beacons \
  --csv beacons-link-in-bio.csv \
  --file beacons-pre-cutover-history.jsonl \
  --dry-run

reach-around counts history-import-beacons \
  --csv beacons-link-in-bio.csv \
  --file beacons-pre-cutover-history.jsonl
```

The command accepts at most 5 MiB and 100,000 data rows. It refuses a wrong
header, unknown section, malformed date, duplicate daily date, negative or
fractional count, invalid rate, invalid UTF-8, wrong column count, CSV formula
prefix in a text field, symbolic-link input, and nonempty destination. It
checks the complete export before writing. It does not call the Worker or any
network service.

## Verify before cancelling Beacons

The success line records three counts: all export rows, `Daily` rows, and
history records. The history-record count must be twice the daily-row count,
one views record and one clicks record for every exported day.

Before cancelling Beacons, record the successful command output in the private
cutover record, run `reach-around counts history-verify --file
beacons-pre-cutover-history.jsonl`, and confirm that its record count matches
the import output. Keep the original CSV in the private instance repository.
Do not paste either file into an issue or pull request. The day-30
reconciliation in [Analytics acceptance](analytics-acceptance.md) must also
pass before cancellation.
