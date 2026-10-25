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
| GET | `/__edit/`, `/__edit/app.css` and the ES modules (`app.js`, `strings.js`, `token.js`, `api.js`, `store.js`, `announce.js`, `view.js`) | the static shell: a read-only view of the settings beside the sandboxed preview |
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

## The forms

The page has one form per group of settings. Nothing is saved until you press that form's Save button.

| Form | Fields |
|---|---|
| Creator | name, title |
| Look | color mode (auto, light, dark), background dim (0 to 0.9), the six theme colors |
| Each link | title, address, subtitle, icon, "affiliate link", "feature this link" |
| Each platform item | handle, address, display name, count label, follower count, count date |

Everything else in the config is shown read-only under "All settings" and is changed in a text editor. Adding and removing links or platform items, uploads, undo and redo come in later versions.

How a save works:

- Save compares each box with the saved value and sends one change for each box you edited, one after the other, each with the hash the last reply gave as `base` and the old value as `expect`. Boxes you did not touch send nothing. The page is rebuilt from the reply only after a successful save, so typing is never wiped under your hands.
- A refused value (`422`) shows the server's message next to that box, marks it invalid, moves focus to it and keeps what you typed. Earlier boxes of the same save stay saved.
- If the field changed in the file while you were editing (`409 stale`), nothing is saved, the forms show what the file holds now, and the page says so.
- Too many changes (`429`) says how many seconds to wait, from `Retry-After`.
- If the site does not build, a note above the forms shows the build error from the state.
- Optional boxes (subtitle, icon, name, label, count, count date) are removed from the file when left empty. A title, address or handle cannot be empty, and a color or the creator title cannot be removed once set; the server refuses and says so.
- Links and platform items move with the Move up and Move down buttons (drag and drop is not part of this version). The button keeps focus where it moved to, and the move is announced ("Moved X to position 2 of 5").
- Every control has a visible label and a hint or error tied to it with `aria-describedby`; errors use text, not color alone. Results are spoken through the polite live region.

The code is `store.js` (state, the operation bodies and the saver, with no DOM), `view.js` (the forms; values go in only with `textContent`, `.value` and a fixed list of attributes) and `app.js` (wiring). Tests: `tests/editor/test_forms_sources.py` (source guards), `test_forms_logic.py` with `forms_logic.mjs` (Node, stubs and a small fake DOM) and `test_forms_contract.py` (the shipped modules against a real editor server). The Node tests need `node` 20 or later and are skipped without it; with `CI` set they fail instead.
