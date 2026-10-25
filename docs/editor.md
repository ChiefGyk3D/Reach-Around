<!-- SPDX-License-Identifier: Apache-2.0 -->
# The local editor

`reach-around edit --config site.yaml` starts the local editor's server. The
server holds the guard, the two listeners, the state and version routes and the
edit route. Uploads, undo routes and the forms come in later changes. The design is in `docs/superpowers/specs/2026-10-25-local-editor-design.md`.

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
| GET | `/__edit/api/state` | the config as JSON, see below (token) |
| GET | `/__edit/api/version` | `{"version": N, "hash": "...", "build": {"ok": true}}`; `N` changes when the file changes. Polling it does not reset the idle timer (token) |
| POST | `/__edit/api/op` | runs one edit operation (token, `Content-Type: application/json`, at most 64 KiB) |
| anything else | | 404 (or 405, 415 and so on from the guard) |

`GET /__edit/api/state` returns `hash` (SHA-256 of the file), `mtime`, `config`
(the loaded values as plain JSON, no comments; `{}` when the file does not
load), `build` (`{"ok": bool, "error": text or null}`; for now this only says
whether the config loads), `version`, `preview_url` (the preview listener, on
the same host name the page was opened with) and `limits`.

### `POST /__edit/api/op`

```
{"base": "<hash from the last state>", "op": "set_link_field",
 "args": {"id": "first", "field": "title", "value": "New title"},
 "expect": {"value": "Old title"}, "label": "Rename a link"}
```

`op` is one of `add_link`, `remove_link`, `move_link`, `set`, `set_link_field`,
`add_platform_item`, `remove_platform_item`, `move_platform_item` and
`set_platform_item_field`. The arguments are those of the matching function in
`reach_around.editing`; an unknown argument is refused with 422. The server
never takes YAML text or a file path, and `set` only takes the keys of the
`set` command. Every change goes through `editing.apply` (file lock, double
check, `.bak` backup, atomic rename), so comments and layout outside the edit
stay as they are, and each write is recorded in `.reach-around-history/`.
`label` is optional (up to 80 plain characters).

`expect` holds the old value(s) of what the operation touches, per operation:
`add_link` `{"count"}` (blocks in the tab), `remove_link` and `move_link`
`{"order"}` (ids of the list), `set` and `set_link_field` `{"value"}`, the
platform operations that add, remove or move `{"items"}` (the whole list), and
`set_platform_item_field` `{"value", "handle"}`. If `base` is still the file's
hash, `expect` is not needed. If the file changed since, the change is applied
only when every `expect` value still matches the current file; otherwise nothing
is written and the reply is `409` with code `stale` and the fresh `state`.

A success is `200` with `{"state": ..., "history": {"label", "hash",
"recorded"}}`. Every error has one shape, and never echoes a header or the key:

```
{"error": {"code": "stale", "message": "plain words", "path": "expect.value"}}
```

| Status | Code | When |
|---|---|---|
| 400 | `bad_json` | the body is not one JSON object (duplicate keys and `NaN` are refused) |
| 409 | `stale` | an `expect` value no longer matches |
| 422 | `bad_request`, `unknown_op`, `bad_argument`, `refused` | a bad field, an unknown operation or argument, or the edit core refused (also when the result would not load) |
| 429 | `too_many_requests` | more than 20 changes in a minute (a `Retry-After` header says how long); refused changes count |

Every editor response carries a strict `Content-Security-Policy` (no
`unsafe-*`), `X-Frame-Options: DENY`, `nosniff`, `no-referrer` and
`Cache-Control: no-store`. The server logs nothing about requests.
