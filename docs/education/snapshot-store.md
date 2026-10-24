# The snapshot store

Reach Around keeps the numbers it collects in a small SQL database. This page
explains the rules that keep that history honest.

## Snapshots, aggregates and the day

Every number is one of two kinds.

- A **snapshot** is a reading at an instant, such as a follower count. It can
  be read many times a day. Storage keeps every reading; a read returns the
  latest reading of a day that is still open. The latest wins because an
  end-of-day count is what a person means by "that day's followers", and
  because a corrected reading should replace a bad one without anyone editing
  history. When the day closes in its own zone, it freezes: a fetched snapshot
  for a closed day is refused. A backfill you import or type yourself is still
  allowed.
- An **aggregate** is a total for a day that has finished, such as stream
  minutes or tips. It is written only after that day completes, so a half-day
  total is never stored as final.

Late data happens: a VOD shows up after midnight, a tip is confirmed a day
later. An aggregate may therefore be revised for a short **settle window**
(three local days by default). After that the day is **sealed** and further
changes are refused. Each revision is a new row, so the earlier values stay
visible in the history.

Every row also names the **time zone** its day was computed in. Daylight
saving makes some days 23 or 25 hours long, and an owner can change zone;
recording the zone on the row means old history never gets silently
reinterpreted.

A failed collection writes no number at all. A missing value is "stale since",
never a zero.

## Migrations

The schema changes through numbered SQL files. The runner takes a lock, so two
processes cannot migrate at once; checks each applied file's SHA-256 so an
edited migration is refused; refuses a database newer than the program; runs
each file in one transaction with its version row; and never rolls back
(there are no down migrations). Before migrating a SQLite store it copies the
file to `<name>.pre-<version>`; keep a backup of your own as well.
