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
```

The section only chooses. It holds no URL and no secret. The deploy gives the
Worker the same choice as one plain variable, `ALERTS_CONFIG`
(`reach_around.config.alerts.worker_value` renders the exact value). Unset, empty,
malformed or with an unknown key, the Worker sends nothing.

## Per-channel setup

Secrets live in your secret manager and reach the Worker as bindings at run
time. Never put them in the repo, in `wrangler.toml` or in GitHub secrets. The
binding names:

| Channel | Binding | Kind | What it is |
| --- | --- | --- | --- |
| Discord | `ALERT_DISCORD_WEBHOOK` | secret | The channel's incoming webhook URL |
| Matrix | `ALERT_MATRIX_ACCESS_TOKEN` | secret | Access token of the account that posts |
| Matrix | `ALERT_MATRIX_HOMESERVER` | variable | `https://` and a host name, nothing else |
| Matrix | `ALERT_MATRIX_ROOM_ID` | variable | The room id (`!abc:example.org`), not an alias |
| Email | `ALERT_EMAIL_TO`, `ALERT_EMAIL_PROVIDER` | variables | Not usable yet, see below |

**Discord.** Create an incoming webhook for a private channel and store its URL
as `ALERT_DISCORD_WEBHOOK`. The Worker accepts only
`https://discord.com/api/webhooks/<id>/<token>` (and the `discordapp.com`, `ptb`
and `canary` forms): no other host, port, query or credentials. Messages are
sent with mentions turned off, so no text can ping anyone.

**Matrix.** Use a dedicated account that is a member of a private room. The
Worker sends one `m.room.message` (plain `m.text`, no HTML) with a fresh
transaction id. The homeserver must be `https://` and a public DNS name: an IP
address in any notation, `localhost`, a single label, or a private or reserved
suffix (`.local`, `.internal`, `.lan`, `.home.arpa`, ...) is refused.

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

Never in an alert: IP address, user agent, cookie, token, country, lead id or
hash. The event type has no field for them, so they cannot be passed by mistake.

### Is the visitor's address in the alert?

Yes by default (`visitor_email: full`), because "who looked" is the point of the
alert. Choose `masked` (`p***@example.com`) or `none` ("a visitor") if your chat
room is shared or not fully private. A chat room is not a secret store: Discord,
Matrix and your mail provider keep what you send them under their own rules and
your own retention. The address is not written to the Worker's logs or to the
status counters in any mode.

### Hostile text

Visitor text is length-capped, control and invisible characters (bidi
overrides, zero-width, tags) are replaced by a space, `@` becomes `(at)`,
`<` and `>` are removed, `://` is broken so no link forms, and for Discord the
markdown characters are backslash-escaped. One message is always one line, and a
hard cap applies to the message and to the request body.

## Limits and failures

- Per channel, at most `max_per_minute` messages a minute. The first message
  held back in a minute sends one notice, and the next minute's first message
  says how many were held back. The window lives in the Worker isolate's memory,
  so it is a flood brake per isolate, not an exact quota. The kit's daily
  `DAILY_ALERT_BUDGET` stays the daily ceiling.
- The same event for the same address inside 10 minutes is sent once.
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

The brand inquiry form (#429), token health alerts (#390) and analytics health
alerts (#401) reuse this interface: they add an event kind to `types.ts`, a
line to `format.ts` and a name to the config lists, and call `scheduleAlert`.
