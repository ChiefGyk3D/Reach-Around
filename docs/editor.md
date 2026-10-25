<!-- SPDX-License-Identifier: Apache-2.0 -->
# The local editor (foundation)

`reach-around edit --config site.yaml` starts the local editor's server. This
first piece holds only the guard, the two listeners and a placeholder page; it
changes nothing in your config. Editing operations, uploads and the forms come
in later changes. The design is in `docs/superpowers/specs/2026-10-25-local-editor-design.md`.

## Starting it

```
reach-around edit --config site.yaml [--idle-minutes 30]
```

The command prints one line to the terminal:

```
http://127.0.0.1:<port>/__edit/#t=<key>
```

Open it yourself. **It never opens a browser**, and there is no option to. The
key after `#t=` is a random session key, created at start, kept in memory and
valid until the process exits. It sits after the `#` so a browser does not send
it to the server, put it in `Referer` or write it to a log. The page reads it
into a variable and removes it from the address bar. A reload loses it; open
the printed link again. Do not paste the link into a chat or a shared screen.

## What it listens on

- Two listeners, both on `127.0.0.1` only, on random free ports: the editor and
  the preview. They are different origins because the ports differ. The preview
  is a placeholder for now and is served with `sandbox allow-scripts` so it can
  never reach the editor.
- A request must name `127.0.0.1`, `localhost` or `[::1]` **with the exact
  port** in `Host`; anything else is refused with 421 (DNS rebinding).
- Every `/__edit/api/` request carries `X-RA-Token`, `GET` included.
- At most 16 requests run at once and 32 connections are held open. A request
  that sends nothing is dropped after 10 seconds.
- The 16 KiB header limit is enforced as bytes arrive, so an oversized or
  endless header is refused (431) before it is parsed or stored. The request
  line and all headers must arrive within 10 seconds in total, and a body within
  30 seconds, however steadily the client sends; a slow drip is closed.
- The editor stops by itself after 30 minutes without activity (`--idle-minutes`
  changes it; it must be above 0 and at most 1440), and on Ctrl+C.

## Routes

| Method | Path | Result |
|---|---|---|
| GET | `/__edit/`, `/__edit/app.js`, `/__edit/app.css` | the static shell |
| GET | `/__edit/api/state` | `{"hash": "<sha256 of the config text>", "config": {}}` (token) |
| anything else | | 404 (or 405, 415 and so on from the guard) |

Every editor response carries a strict `Content-Security-Policy` (no
`unsafe-*`), `X-Frame-Options: DENY`, `nosniff`, `no-referrer` and
`Cache-Control: no-store`. The server logs nothing about requests.
