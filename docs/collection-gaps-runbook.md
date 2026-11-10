<!-- SPDX-License-Identifier: Apache-2.0 -->
# Collection gaps: response and recovery

A collection gap means that some or all events were not recorded. It is unknown
time, not a measured zero. Reports and dashboards must keep the gap visible,
name its reason and label every total or ratio whose window crosses it.

## First response

1. Record the first and last affected UTC hour. Do not add zero rows or copy a
   nearby value into the missing interval.
2. Identify the reason from the coverage log: `outage`, `quota`, `kill_switch`
   or `site_kill_switch`.
3. Preserve the current data and coverage log before changing a limit,
   restoring data or turning collection back on.
4. Correct the cause, then confirm that a new complete hour is recorded.
5. Rebuild the report and dashboard export. Confirm that the affected interval
   remains a labeled gap and that figures spanning it carry the gap label.

## Kill switches

A kill switch deliberately stops collection during an incident. Before using
one, note its UTC start time and scope. After the incident, remove the switch,
verify one complete collection interval and record the UTC end time. Do not
rewrite the stopped interval as normal operation. It remains a `kill_switch`
gap, or `site_kill_switch` when only part of the collection scope was stopped.

## Quota

When a collection quota is reached, excess events may never have been stored.
Treat the affected interval as a `quota` gap. Check whether the traffic was
expected, whether an abusive source caused the increase and whether the current
limit still fits the available capacity. Raise a limit only after confirming
that its worst case is affordable. A later successful run does not recover the
dropped events.

## Outage and restore

For an outage, restore the collector first and verify new data before repairing
older storage. Restore only from a known good backup and keep the damaged copy
until verification is complete. Compare record counts and coverage boundaries,
then rebuild derived exports.

A restore may recover stored events, but it cannot prove what happened after
the backup or while collection was unavailable. Keep those intervals labeled
`outage`. Never invent records to make a calendar continuous.

## Closeout

Close the incident only after collection is current, the coverage reason is
correct, no gap is displayed as zero, and every figure whose window crosses the
gap says so. Record what changed and what remains unknowable.
