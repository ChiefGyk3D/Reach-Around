# Owner alerts (Worker)

The Worker can tell you when something happens on the media kit gate:
someone unlocked the kit, a new lead was captured, or the gate keeps failing.
You choose the channels (Discord, Matrix, email) and the events. Everything is
**off** until you turn it on, and nothing is sent to a channel you did not set up.

Slack or Teams later means one more adapter behind the same small interface
(`worker/src/alerts/types.ts`, `Channel`). They are not built.

## Turn it on

In `site.yaml`:

```yaml
alerts:
  channels: [discord, matrix]          # discord, matrix, email
  events: [kit_unlocked, lead_captured]  # also: gate_failed_repeatedly
  visitor_email: full                  # full, masked or none
  max_per_minute: 5                    # per channel, 1 to 30
  matrix:
    allowed_hosts: [matrix.example.org]  # required for the matrix channel
```

The section only chooses. It holds no URL and no secret. The build records the
choice in the build manifest (`alerts_config`, present only when alerts are on),
`verify-manifest.py` re-checks it, and the deploy (`deploy-test.yml` and the
private deploy example) writes it to the Worker as one plain variable,
`ALERTS_CONFIG` (`reach_around.config.alerts.worker_value` renders the exact
value). The secrets below are not part of it: set them in the secret manager. Unset, empty,
malformed or with an unknown key, the Worker sends nothing.

## Per-channel setup

Secrets live in your secret manager and reach the Worker as bindings at run
time. Never put them in the repo, in `wrangler.toml` or in GitHub secrets. The
binding names:

| Channel | Binding | Kind | What it is |
| --- | --- | --- | --- |
| Discord | `ALERT_DISCORD_WEBHOOK` | secret | The channel's incoming webhook URL |
| Matrix | `ALERT_MATRIX_ACCESS_TOKEN` | secret | Access token of the account that posts |
| All | `ALERT_DEDUP_KEY` | secret | At least 32 random bytes. Keys the dedup hash (HMAC-SHA-256). Without it dedup is off (counted as `dedupDisabled`); there is no built-in key |
| Matrix | `ALERT_MATRIX_SERVER` | secret | `https://` and a host name, nothing else. It must also be in `alerts.matrix.allowed_hosts` |
| Matrix | `ALERT_MATRIX_HOMESERVER` | variable | Deprecated plain-variable fallback, used only when `ALERT_MATRIX_SERVER` is unset; the same `allowed_hosts` rule applies |
| Matrix | `ALERT_MATRIX_ROOM_ID` | variable | The room id (`!abc:example.org`), not an alias |
| Email | `ALERT_EMAIL_TO`, `ALERT_EMAIL_PROVIDER` | variables | Not usable yet, see below |

**Discord.** Create an incoming webhook for a private channel and store its URL
as `ALERT_DISCORD_WEBHOOK`. The Worker accepts only
`https://discord.com/api/webhooks/<id>/<token>` (and the `discordapp.com`, `ptb`
and `canary` forms): no other host, port, query or credentials. Messages are
sent with mentions turned off, so no text can ping anyone.

**Matrix.** Use a dedicated account that is a member of a private room. The
Worker sends one `m.room.message` (plain `m.text`, no HTML) with a fresh
transaction id. The homeserver and the access token are ONE unit: the server
name decides where the token goes, so keep both in the secret manager
(`ALERT_MATRIX_SERVER` next to `ALERT_MATRIX_ACCESS_TOKEN`) and list the host in
`alerts.matrix.allowed_hosts`. The Worker sends the token only to a host that
matches an entry there exactly (no subdomain or suffix match). Changing the host
name therefore authorizes sending the token to the new host: treat a change to
that list, or to the secret, as a change of who receives the token.

The host must be `https://` and a public DNS name. These are refused even when
listed: an IP address in any notation, a name that embeds one in any label
(`127.0.0.1.example.org`, `10-0-0-1.example.org`, `7f000001.example.org`),
`localhost`, a single label, a private or reserved suffix (`.local`, `.internal`,
`.lan`, `.home.arpa`, ...), and the well-known wildcard-DNS services (`nip.io`,
`sslip.io`, `xip.io`, `traefik.me`, `localtest.me`, `lvh.me` and similar).

**Residual risk.** A Worker cannot pin DNS or inspect the address a name
resolves to, so DNS rebinding cannot be fully excluded. A Worker also cannot
reach a private network by itself, which limits what a hostile resolution can
touch, but the token would still be sent to wherever the name points. Use a
homeserver you control, on a domain you control, with DNSSEC if you can, and
a token for a dedicated account that can only post to one room.

**Email.** The provider interface and a disabled no-op ship, but no provider
adapter does. Email is the next piece (a provider such as Mailgun is one
`EmailProvider` implementation and one registry entry). Until then an `email`
channel is counted as not configured and sends nothing.

Redirects are never followed on any channel, every call has a short timeout, and
the answer body is never read.

## What an alert contains

| Event | When | Text |
| --- | --- | --- |
| `kit_unlocked` | Someone passed the gate | "the media kit was unlocked by `<address>` at `YYYY-MM-DD HH:MM UTC`." |
| `lead_captured` | A new address was stored | "new media kit lead `<address>` at `<time> UTC`." |
| `gate_failed_repeatedly` | The gate failed several times in a short span | The count and the time. No visitor data. |
| `inquiry_received` | Someone sent the business inquiry form ([media kit](kit.md#the-business-inquiry-form)) | "new business inquiry at `<time> UTC`", then the name, the address (shown per `visitor_email`, or "not given"), the brand, the budget and the details, each neutralized and capped (80, 80, 40 and 800 characters; an empty one reads "not given"). |
| `analytics_health` | An analytics health condition started or cleared (below) | A fixed sentence for one condition, a UTC time and at most one count. No visitor data, no site, link or page name. |

Never in an alert: IP address, user agent, cookie, token, country, lead id or
hash. The event type has no field for them, so they cannot be passed by mistake.
The inquiry event carries the five answers the visitor typed into the form and
nothing else; the formatter neutralizes each of them.

### Is the visitor's address in the alert?

Yes by default (`visitor_email: full`), because "who looked" is the point of the
alert. Choose `masked` (`p***@example.com`) or `none` ("a visitor") if your chat
room is shared or not fully private. A chat room is not a secret store: Discord,
Matrix and your mail provider keep what you send them under their own rules and
your own retention. The address is not written to the Worker's logs or to the
status counters in any mode.

### Hostile text

Only a normalized address (lowercase ASCII) is rendered; anything else in
that slot is shown as "a visitor". Visitor text is length-capped, control and
invisible characters (bidi overrides, zero-width, tags) are replaced by a space,
`@` becomes `(at)`, `<` and `>` are removed, every URI scheme loses its colon
(`mailto:`, `tel:`, `javascript:`), a dot between letters or digits becomes
`(dot)` so no host name, `www.` form or IP address becomes a link (an address
reads `name(at)example(dot)com`), and for Discord the markdown characters are
backslash-escaped. One message is always one line, and a
hard cap applies to the message and to the request body.

## Analytics health alerts

Silent failure is the main risk of a counting system, so the hourly cron checks
a few counters and tells you when one goes wrong and once more when it recovers.
Turn it on with the event name `analytics_health` (it uses the same channels,
secrets, dedup, per-minute limit and daily budget as every other alert; there
are no new secret names):

```yaml
alerts:
  channels: [discord]
  events: [analytics_health]
```

Nothing is sent while all is well. A condition is reported once when it starts
and once when it clears, not every hour. The state is kept in `view_meta`
(`health:<condition>`) only after a channel took the message, so a message
nobody received is tried again on the next hourly run, within the dedup, minute
and daily limits.

| Condition | Fires when | Clears when |
| --- | --- | --- |
| `rollup_overdue` | the rollup heartbeat (`rollup_last_ok`) is older than 3 hours | the heartbeat is fresh again |
| `rollup_stale` | a site's `rolled_through` checkpoint is more than 2 UTC days behind today | the checkpoint has caught up |
| `zero_views` | a site counted likely-human views in the 24 hours before and none in the last 24, and that site is not switched off (`VIEWS_KILL`, or its id in `VIEWS_KILL_SITES`). Per site, but the alert names no site | views are counted again. If the hourly counters are gone (the 48 hour purge, a new database) there is no data: an active alert is kept, and cleared only when traffic is seen or after 7 days |
| `view_budget_hit` | the account-wide hourly view budget was reached in the last 3 hours (further views were dropped) | three hours pass without it |
| `alert_budget_near` | today's alert budget is at least 80 percent used | the next UTC day |

Design rules, each pinned by a test (`worker/test/health.test.ts`):

- **Content-free.** The text is a fixed sentence per condition, a UTC time and,
  for the two rollup conditions, one count of hours or days. No site id, page,
  link, URL, label or visitor field exists in the event type. A hostile or
  unknown condition word is never echoed.
- **Counters only.** It reads the heartbeat and the hourly counters in
  `view_meta` and the alert day counter. It never reads `view_events`,
  `click_events`, a lead or any raw table.
- **UTC.** Days and hours are UTC.
- **Quiet and bounded.** At most 3 channel deliveries per cron run (a message
  goes to every configured channel, so with two channels one condition uses two
  of the three; the rest wait for the next hour). The step costs two metered D1
  queries when idle and uses at most 8 of the run's `QUERY_BUDGET`; it is
  skipped under `QUERY_BUDGET` 12. Before each message one query is held back
  for recording it, and the state is written right after a channel took the
  message, so an exhausted allowance can delay an alert but never makes an
  already delivered one repeat on later hours. If that write itself fails, the
  run sends nothing more (the dispatcher's dedup covers only ten minutes).
  Known bounded limit: a crash or a failed state write between delivery and the
  write can repeat that alert once per hourly run until the condition clears or
  the daily alert budget (`DAILY_ALERT_BUDGET`, default 20) is spent: at most one
  repeat per condition per hour.
- **Per channel.** The state of a condition records which channels took the
  current message (`health:<condition>` = `<since ms>:<firing|clearing>:<channels>`).
  If Discord took it and Matrix failed, the next hour sends to Matrix only, and a
  channel that took it is never sent it again. A channel that never takes it is
  given up on after 24 hours. What counts as taken comes only from the receipt of
  that one send, never from shared counters, so an unrelated alert in the same
  Worker cannot mark a health alert as delivered.
- **Fail closed.** The step never throws into the cron. Whatever happens to
  an alert (channel down, budget spent, D1 error), collection, the purges and
  the rollup run as before.
- **Unknown is not an alert.** A missing or malformed input (a fresh install with
  no heartbeat yet, a counter that does not parse) leaves that condition alone.

Limits you should know. The checks run inside the same cron they watch: if the
cron or the Worker does not run at all, nothing can alert. An external probe of
the link page and the event routes is the answer to that and is not part of the
Worker (see the runbook, [failure-modes.md](failure-modes.md)). `zero_views`
needs earlier traffic, and a site with a view only every few days will trip it;
use it on a site with steady traffic or leave the event off. `view_budget_hit`
reads the account-wide ceiling (`ACCOUNT_VIEWS_PER_HOUR`), not one site's own
budget. Stale social numbers (hand-kept or failed refresh) are reported by
`reach-around counts stale` (exit 4) and the `token-health:` lines, not by the
Worker.

## Collection coverage and gaps

The same hourly step writes one row per UTC day into `analytics_coverage`
(migration `0014_health.sql`): 24 letters, one per hour, for the hour that just
ended. `o` collecting, `k` the view kill switch was on when sampled, `q` the
account view budget had been reached, `p` some sites (not all) were switched off by `VIEWS_KILL_SITES`, `-` no sample (the Worker did not run). `k` means `VIEWS_KILL` or every configured site named. Coverage is **account-wide, not per site**: the row cannot say which site `p` was, so a report for one site shows a `p` day as partial with the reason `site_kill_switch` and says coverage is account-wide. An exported table with no rows is "not monitored", never "complete".
The row holds no count and no visitor data and is written whether or not alerts
are on. The migration is a plain `CREATE TABLE`: a pre-existing table of that
name makes it fail instead of being accepted. The health step also checks the
table definition on every run (strict, keyed by day, 24 closed letters); if it
is wrong the step runs degraded: it writes no coverage and sends no alert, and
logs one fixed `log`/`failed` line. Fix by dropping the wrong table and applying
migration 0014. `reach-around report` and `grafana-export` read it (export the table like
`view_daily`) and show a day with no collection as a gap with a reason (`outage`,
`quota`, `kill_switch`), never as a zero. A quiet hour that was collected is a
real zero and stays one. See [report.md](report.md#gaps) and
[grafana.md](grafana.md).

## Limits and failures

- The per-minute limit, the daily budget and the dedup live in the Worker's D1
  database (migration `0013_alerts.sql`: `alert_quota` and `alert_dedup`), one
  conditional statement each, so several isolates share them and cannot
  overshoot. Apply the migration before enabling alerts; it must be applied after `0011_click_funnel.sql`, in number order, which `wrangler d1 migrations apply` does; if the tables are
  unusable the Worker sends nothing (fail closed) and counts it.
- Per channel, at most `max_per_minute` messages a minute (a fixed UTC minute
  window). The last slot of the minute carries one "more alerts are arriving"
  notice instead of an alert, so the notice counts inside the limit; the next
  minute's first message says how many were held back (that count is kept in
  isolate memory, best effort). With `max_per_minute: 1` there is no notice.
- The daily ceiling is the kit's `DAILY_ALERT_BUDGET` (default 20 per UTC day).
  It counts events that obtained at least one send slot, that is attempted
  sends, once per event whatever the number of channels. An event with no
  usable destination (a channel enabled but its bindings missing or invalid),
  one that every channel's minute limit refuses, one that is a repeat, and one
  whose admission fails part way all cost nothing: whatever was taken (dedup
  claim, minute slots) is given back. A refused event is dropped and counted.
- The same event for the same address inside 10 minutes is sent once. D1 holds
  only a keyed hash (HMAC-SHA-256 under `ALERT_DEDUP_KEY`, cut to 128 bits after
  the digest) for those 10 minutes, never the address and nothing a D1 snapshot
  alone can reverse, at most 500 rows; the table is pruned on every write. A
  failed admission releases the claim, so a retry is not mistaken for a repeat.
- Input is bounded: the address is cut to 254 characters before it is
  normalized or hashed, and only a normalized address of at most 254 characters
  is rendered.
- An alert costs a handful of D1 writes (dedup, daily count, one per channel
  that sends), about five, which the kit's worst-case write estimate
  (`WRITES_PER_OP.alert`) still counts as one. At the default budget of 20 that
  is about 100 writes a day.
- A channel that fails or times out never blocks the others and never reaches
  the visitor. Alerts run after the response (`ctx.waitUntil`).
- Failures are counted per channel by a closed class (`rejected` for 4xx,
  `server` for 5xx, `timeout`, `redirect`, `network`, `unavailable`) with no
  message content, URL or secret. The kit logger records only `alert` with an
  outcome.

## Dry run

No alert is sent in tests or in a build. To check a setup without a real
message, run the Worker tests (`npm test` in `worker/`): they use a mocked
`fetch` and assert the exact request each adapter would make. To confirm a
real destination, turn on one event and one channel on a test deployment and
trigger it once yourself.

## For other features

The brand inquiry form (#429, built as `inquiry_received`) and token health alerts (#390) reuse this interface
(analytics health, #401, is built the same way): they add an event kind to `types.ts`, a
line to `format.ts` and a name to the config lists, and call `scheduleAlert`.
