<!-- SPDX-License-Identifier: Apache-2.0 -->
# Turnstile storage check

The invisible bot check (`analytics.turnstile_beacon`, issue #610) loads
Cloudflare Turnstile. Cloudflare's own pages disagree about whether Turnstile
keeps cookies or local storage in the browser. This check measures it, so the
privacy page can say exactly what was seen. Run it before the check is turned on
for a live site, and again when Cloudflare's script changes in a way you notice.

## What the harness does

`scripts/check-turnstile-storage.py`:

1. Builds a throwaway copy of the example site with the check on and Cloudflare's
   published always-pass test key (`1x00000000000000000000AA`).
2. Serves it from `127.0.0.1`, applying the build's own `_headers`, so the
   policy under test is the one that ships.
3. Opens the home page in a brand-new browser context: no cookies, no storage, no
   profile, no extensions.
4. Waits for the page script to finish (`verified` or `unverified`), reading
   storage names from every frame while it waits, then once more at the end.
5. Prints cookies (name, domain, path, lifetime, flags), localStorage and
   sessionStorage key names and IndexedDB database names per origin, every host
   contacted, the hosts that are not this machine, console errors and the final
   state. It never prints a value or the token.

It sends nothing to Cloudflare except what the browser itself sends when the
script loads. It never uses a production key.

## Setup

```sh
python3 -m venv .venv-storage-check
.venv-storage-check/bin/pip install --require-hashes -r requirements-dev.txt
.venv-storage-check/bin/playwright install chromium firefox
```

`requirements-dev.txt` is the hash-locked file that includes Playwright, so the install passes `--require-hashes`. Any venv location works (use `.venv-storage-check/bin/python` in the commands below).

## Procedure

Run each browser at least twice, from a fresh run each time (the harness always
starts clean), on the network you will use, once with your normal tracking
protection off and once with a blocker or a strict browser setting on. Keep the
output.

```sh
PYTHONPATH=src python3 scripts/check-turnstile-storage.py --live --browser chromium --json > chromium-1.json
PYTHONPATH=src python3 scripts/check-turnstile-storage.py --live --browser firefox  --json > firefox-1.json
PYTHONPATH=src python3 scripts/check-turnstile-storage.py --live --browser chromium
```

`--live` contacts Cloudflare (the test key only). To check the harness itself
without contacting Cloudflare, use `--stub`; its numbers say nothing about
Turnstile. To run it through the quiet launcher on a desktop machine, prefix the
command with the project's `safe-run.sh`.

## Reading the result

| Line | Meaning | Action |
| --- | --- | --- |
| `check finished: verified` and `token under opaque sandbox: yes` | The helper loaded, Turnstile ran inside the sandbox (no `allow-same-origin`) and a token came back | Expected for the test key |
| `token under opaque sandbox: NO` | No token arrived while the frame had an opaque origin. Turnstile may need cookies or storage that an opaque origin does not give it, or a flag the sandbox lacks | Record the console errors and the hosts. Do not add `allow-same-origin`: that puts vendor code on the site's origin. Use the fallback below |
| `check finished: unverified` or `no result` | Blocked, failed or timed out | Look at the console errors and the hosts; see the failure rows below |
| `cookies: 0`, storage `none` | Nothing kept | The privacy page can say nothing is stored by the check |
| a cookie or a storage key | Something is kept | Copy its name, domain, lifetime and where it came from into the privacy wording. Decide with the owner whether it is acceptable; if not, the check does not ship |
| `hosts other than this machine` lists more than `challenges.cloudflare.com` | More origins than the policy names | The page policy should have blocked them; look at the console errors and report it |
| console error naming a Content Security Policy | The policy refuses something Turnstile needs | Do not loosen another page. Note the directive and origin; the change belongs in `beaconframe.py` and its pinned test |

`--strict` makes the script exit 1 when anything is stored, a host other than
Turnstile is contacted, or the check did not verify. Use it in a note, not as a
gate: the answer is a decision, not a pass or fail.

## The opaque-sandbox result, and the fallback

The helper frame runs vendor code and is sandboxed with `allow-scripts` only (no
`allow-same-origin`), so it has an opaque origin and cannot reach the page that
embeds it. Cloudflare does not say whether Turnstile works there. The harness
prints `token under opaque sandbox: yes` or `NO`; keep that line with the rest.
If it says `NO` in both browsers:

1. The check is unverified for everyone (safe, nothing breaks), so it does not
   ship as it is.
2. Fallback: host the helper on a dedicated origin, such as a separate Pages
   project or a subdomain that holds no site cookies and no site storage. The
   frame then needs no sandbox flag beyond `allow-scripts allow-same-origin` for
   its own origin, and nothing on it matters to the site. The parent would check
   that origin, the exact source window and the nonce. This needs the owner's
   infrastructure (a project, a host name, a DNS record, and the ordinary pages'
   `frame-src` naming that host), so it is a decision, not a code change made
   here.
3. Never widen the sandbox on the site's own origin.

The attack stand-in (`--stub-attack`, no contact with Cloudflare) proves the
containment itself: from inside the frame it tries to read and change the parent
page, write parent and own storage, read cookies, and post forged results
without the nonce and with a wrong one. Every one must come back refused and the
final state must be `unverified`. Run it after any change to the sandbox, the
message checks or the policy.

## Also record

- The browser and version (`chromium --version`, Firefox "About").
- The date, and the script's `api.js` response size if you measure it
  (the page weight budget is in `docs/performance.md`).
- Whether the hidden frame needed a flag the sandbox does not give it (look for
  "sandbox" in the console errors). Only `allow-scripts` is given. Anything
  else (popups, forms) needs a reason and the owner's say; `allow-same-origin`
  is never the answer.
- Visitors with Global Privacy Control or Do Not Track: open the page with
  `navigator.globalPrivacyControl` true (Firefox: `privacy.globalprivacy.control.enabled`;
  Chromium: an extension or a flag that sends it) and confirm no request goes to
  `/e-frame/` or to Cloudflare. The node tests pin this in code; this confirms it in a real
  browser.

## Where the result goes

Put the measured list in the issue for the privacy wording. Do not put it in the
privacy page by hand: the wording is written from the measurement.
