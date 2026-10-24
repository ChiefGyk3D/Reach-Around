# How the click counter works

Reach Around can count how often each link on a page gets clicked. This page
explains what that costs your visitors in privacy (very little), and how the
small piece of server code behind it is built so it cannot be turned against
them. It is written for people learning from the project.

## What is stored

Each counted click becomes one row with four fields:

| Field | What it holds |
| --- | --- |
| `id` | A running number the database assigns. |
| `ts` | When the click happened (a timestamp). |
| `link_id` | Which link was clicked, such as `example-one`. |
| `country` | A two-letter country code such as `DE`, or empty. |

## What is not stored

- No IP address. The table has no column for one, so there is nowhere to put it.
- No user agent, no referrer, no cookie, no account, no name, no email.
- The country, when present, comes from a header Cloudflare already adds. It is
  kept only if it is exactly two capital letters.

The visitor's address is used for one thing: telling one visitor from another
for rate limiting (below). It is handed to the rate limiter and never written
to the database or to a log.

## Why there is no redirect route

A common pattern for click tracking is `/go?url=...`, where the server sends
the visitor on to the address you gave it. Attackers love that pattern: they
mail out a link on your trusted domain that bounces people to a bad site. This
is called an open redirect.

Reach Around does not have such a route. The page's links point straight at
their real destinations. The browser tells the counter about the click on the
side (using the standard `ping` link attribute, or a tiny beacon on browsers
without it), and the counter answers with nothing. There is no code path that
reads a URL from a request and sends anyone to it.

## The allowlist

The only route that does anything is `POST /e/c/<link id>`. The id must be on
a list of known ids, which the site build produces from your config and hands
to the Worker as the `LINK_IDS` setting. Anything else is ignored: an id that
is not on the list, an empty id, or a whole URL pasted in place of an id. The
list is checked as exact text, so odd names such as `__proto__` match nothing.

This means a stranger cannot fill your database with made-up link names.

## Bots, wrong origins and bursts

After the id check, a request must still pass three more tests before anything
is written, in this order:

1. User agent: a missing or empty one, or one that looks like a crawler or
   command-line tool, is ignored.
2. Origin: the request must say it came from your site, through the `Ping-From`
   or `Origin` header, and the host must equal the configured `SITE_HOST`
   exactly. `links.example.test.evil.example` does not pass.
3. Rate limit: each client gets 20 checks per minute from Cloudflare's Workers
   Rate Limiting binding. The 21st within a minute is ignored.

Cheap checks come first, so junk traffic is dropped before it spends any
rate-limit budget.

These checks are a speed bump, not a lock. Headers can be forged by anyone
who tries. The goal is that casual noise and simple bots do not distort the
numbers, and that nobody can learn anything or cause harm by poking at it.

## Every answer looks the same

Whether a click was counted or ignored, the answer is the same: HTTP status 204
with no body and no extra data. That includes wrong paths, wrong methods and
internal errors. A prober cannot tell which check rejected them, so the
endpoint is useless for guessing valid ids or testing bypasses.

The Worker is also write-only. It has no route that returns clicks, counts or
any stored data. You read your numbers through your own Cloudflare account.

## Try it

The tests in `worker/test/click.test.ts` cover each rule above. From the
`worker/` directory, run `npm ci && npm test`.
