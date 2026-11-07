<!-- SPDX-License-Identifier: Apache-2.0 -->
# The local editor

## What it is, and who it is for

`reach-around edit` starts a small web server on your own computer. It is for a
creator who keeps a Reach-Around config (`site.yaml`) in an instance
repository and wants a browser page to change the everyday settings without
opening the file. The design is in
`docs/superpowers/specs/2026-10-25-local-editor-design.md`.

**What ships today is the server and a page with forms.** The page has one
form per group of settings (creator, look, each link, each platform item), see
[The forms](#the-forms). It cannot yet add or remove a link or a platform
item, upload an image, or undo and redo (the undo and redo routes exist; the
page has no buttons for them). For everything else, change the config by hand
or with the commands in
[Customizing](customizing.md#editing-without-breaking-the-file).

The preview frame is a placeholder. The preview listener answers with a fixed
page that says "The preview appears here." It does not build or show your site.
To see your site while you edit, run `reach-around serve` in another terminal
([Local preview](preview.md)).

## Quick start

```
reach-around edit --config site.yaml [--idle-minutes 30]
```

The command prints one line to standard output:

```
http://127.0.0.1:<port>/__edit/#t=<key>
```

(A reminder line goes to standard error.)

1. Open that link in your browser yourself. **The command never opens a
   browser**, and there is no option that makes it.
2. The key after `#t=` is random, made at start, held in memory and valid until
   the process exits. It sits after the `#`, so the browser does not send it to
   the server, put it in `Referer` or write it to a log. The page reads it into
   a variable and removes it from the address bar. A reload loses it; open the
   printed link again. Do not paste the link into a chat or show it on a shared
   screen.
3. Stop with Ctrl+C (or SIGTERM). It also stops by itself, see
   [Limits](#limits-and-known-gaps).

It listens on `127.0.0.1` only; any other host is refused at start. If the
config file is missing, is not a regular file or is larger than 8 MiB, the
command prints an error and exits with status 1.

A config path that is a symlink, or that passes through a symlinked folder, is
refused: the editor prints `error: ... unsafe config path ...` and exits with
status 1. Give the real path of the file. The path is checked again on every
read and before each save. (A link owned by root, such as `/home` pointing to
`/var/home` on some systems, is allowed.)

## What you can change today

In the browser, with the forms:

- **Creator:** name and title.
- **Look:** color mode (auto, light, dark), background dim (0 to 0.9) and the six
  theme colors.
- **Each link:** title, address, subtitle, icon, "affiliate link", "feature this
  link", and its place in the list (Move up, Move down).
- **Each platform item:** handle, address, display name, count label, follower
  count and count date, and its place in the list.

Through the server's edit route (for a client that has the key; the tests do
this) the operations are:

| Operation | What it changes |
|---|---|
| `add_link`, `remove_link`, `move_link` | a link block in a tab, or a link in a section |
| `set_link_field` | one scalar of a link: `title`, `url`, `subtitle`, `icon`, `affiliate`, `featured` |
| `set` | one of `creator.name`, `creator.title`, `creator.avatar`, `theme.mode`, `theme.background_dim`, `theme.colors.*` |
| `add_platform_item`, `remove_platform_item`, `move_platform_item`, `set_platform_item_field` | the items of a `platforms` block |

These are the functions of `reach_around.editing`, the same ones behind
`reach-around add-link`, `remove-link`, `move-link`, `set` and
`set-link-field`. The four platform operations have no command; only the
editor's route reaches them. The page does not use `add_link`, `remove_link` or
the two platform add and remove operations yet, and it never sets
`creator.avatar`. Everything else in the config (tabs, sections, other blocks,
the theme beyond the keys above, overrides) is shown read-only under "All
settings" and is edited by hand.

### Editor or hand-editing YAML

Hand-editing is the full path: it reaches every key, and a text editor with the
[JSON Schema](customizing.md#editor-help-the-json-schema) catches mistakes as
you type. The editor and the commands are the narrow path: they change one
value, keep every other byte of the file, and refuse anything they cannot
change safely. Use the editor for the everyday changes (links, platform
handles, a few theme values) and hand-editing for anything else.

## How saving, undo and outside edits behave

**Saving.** Each change runs through `editing.apply`, the same path as the
commands. It locks the file, reads it, computes the change from the file as it
is now, checks that the edited file loads and equals the old data plus exactly
that change, writes a temporary file, and renames it over the original. Only
the edited lines change; comments, blank lines and layout stay. The previous
file is kept as `site.yaml.bak`. If the result would not load, nothing is
written and the reply is a refusal. Flow style, CRLF line endings, anchors and
a dash on its own line are refused rather than rewritten.

**History.** After each write the server records a snapshot in
`.reach-around-history/` beside the config (folder mode 0700, files 0600, at
most 100 entries and 5 MiB; a symlinked folder or file is never followed). If
the snapshot cannot be stored, the change is still saved and the reply carries
a warning that undo is not available for it. Keep `.reach-around-history/` out
of git: the instance template's `.gitignore` already does. The editor does not
edit your `.gitignore` (the helper exists in `reach_around.editor_history` but
the editor never calls it).

**Undo and redo.** `POST /__edit/api/undo` and `/redo` step through the
history (see [Undo, redo and history](#undo-redo-and-history)); the browser
page has no controls for them yet.

- Undo and redo work only while the file still hashes to what the editor wrote
  last. After an outside edit they return a refusal (`outside_edit`) and change
  nothing.
- "Restore anyway" (`"anyway": true` on undo) first keeps the outside version
  as its own snapshot, so the step can itself be undone.
- A new change after an undo drops the redo line.
- A snapshot whose content no longer matches its recorded hash is not restored.
  A corrupted index is set aside as `index.json.corrupt-*` and the history
  restarts.

**Outside edits.** Every state the server returns carries `hash`, the SHA-256
of the file. An edit request names the `base` hash the client last saw and the
old value (`expect`) of what it touches. If the file has changed since but those
values are the same, the edit is applied on top of the outside change. If a
value moved, nothing is written and the reply is `409 stale` with the fresh
state. The server does not overwrite a field someone else changed. The page does
not watch the file; a client can poll `GET /__edit/api/version`, and the page
learns of an outside change when a save comes back `409 stale`.

## Safety model, in plain words

- **Only your computer can reach it.** Both listeners bind `127.0.0.1`. A
  request must name `127.0.0.1`, `localhost` or `[::1]` with the exact port of
  that listener in `Host`, or it is refused with 421. That stops a web page
  from reaching the editor through a name that was re-pointed at your machine.
- **A web page you visit cannot use it.** Every `/__edit/api/` request needs the
  session key in the `X-RA-Token` header, `GET` included. The key is not a
  cookie, so a browser never adds it by itself. A write also needs
  `Content-Type: application/json`, an `Origin` that matches, and
  `Sec-Fetch-Site: same-origin` when the browser sends it. The server never
  answers a cross-origin preflight (`OPTIONS` gets 405).
- **Text from your config cannot run as code in the page.** Config values reach
  the page as text only. The Content-Security-Policy allows scripts and styles
  from the editor itself and nothing else: no inline script, no `unsafe-*`, no
  remote address. The front end is plain files in this repository: no npm, no
  build step, no download from the web.
- **The preview cannot reach the editor.** It runs on a different port, so it
  is a different origin, and sits in an iframe with `sandbox="allow-scripts"`
  only. The preview listener allows only the editor to frame it.
- **The server writes only your config, its `.bak` and the history folder.** It
  takes no YAML text and no file path from a request, only the operations and
  arguments listed above. It serves no file by path; its static files are a
  fixed list of its own names.
- **Limits protect it from runaway requests** (below). A malformed or refused
  request does not write and does not produce a 5xx, except `500 io_error` when
  the disk itself fails during undo or redo.
- **It logs nothing about requests.** The link is printed once, to the terminal.
- **It does not defend against code running as you.** A program or browser
  extension that can read your terminal or drive your browser can edit your
  config file directly anyway. Do not run it on a machine with other users you
  do not trust: loopback ports are reachable by every local user (they still
  need the key to use the API). The link holds the key; anyone who sees it can
  edit your config while the editor runs.

## Routes

Both listeners are on random free ports.

| Method | Path | Result |
|---|---|---|
| GET | `/__edit/`, `/__edit/app.css` and the ES modules (`app.js`, `strings.js`, `token.js`, `api.js`, `store.js`, `announce.js`, `view.js`) | the static shell: the forms and the sandboxed preview frame. No key needed, and the shell holds none |
| GET | `/__edit/api/state` | the config as JSON, see below (key) |
| GET | `/__edit/api/version` | `{"version": N, "hash": "...", "build": {"ok": true}}`; `N` changes when the file changes. Polling it does not reset the idle timer (key) |
| POST | `/__edit/api/op` | runs one edit operation (key, `Content-Type: application/json`, at most 64 KiB) |
| POST | `/__edit/api/undo`, `/__edit/api/redo` | step back or forward in the history (key, JSON, same limits) |
| GET | `/__edit/api/history` | labels, times and hashes of the history, never its text (key) |
| anything else | | 404 (or 405, 415 and so on from the guard) |

The guard also knows an upload path (`/__edit/api/upload`, 8 MiB,
`application/octet-stream`). No route answers it yet, so a request that passes
the checks ends in 404.

On the preview port, `GET` and `HEAD` of `/` return the placeholder page. Other
methods are refused (405) and other paths give 404.

`GET /__edit/api/state` returns `hash` (SHA-256 of the file), `mtime`, `config`
(the whole loaded config as plain JSON, no comments; `{}` when the file does not
load), `build` (`{"ok": bool, "error": text or null}`; this only says whether
the config loads, it does not run a site build), `can_undo`, `can_redo`,
`outside_edit` (true when the file is not what the editor wrote last, so undo
would erase an outside change), `version`, `preview_url` (the preview listener,
on the same host name the page was opened with) and `limits` (`json_bytes`,
`mutations_per_minute`, `history_entries`).

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
`label` is optional (1 to 80 printable characters) and names the history entry.

`expect` holds the old value(s) of what the operation touches, per operation:
`add_link` `{"count"}` (blocks in the tab), `remove_link` and `move_link`
`{"order"}` (ids of the list), `set` and `set_link_field` `{"value"}`, the
platform operations that add, remove or move `{"items"}` (the whole list), and
`set_platform_item_field` `{"value", "handle"}`. If `base` is still the file's
hash, `expect` is not needed. If the file changed since, the change is applied
only when every `expect` value still matches the current file; otherwise nothing
is written and the reply is `409` with code `stale` and the fresh `state`.

A success is `200` with `{"state": ..., "history": {"label", "hash",
"recorded"}}`, plus `"warning"` when the history could not be stored. Every error has one shape, and never echoes a header or the key:

```
{"error": {"code": "stale", "message": "plain words", "path": "expect.value"}}
```

| Status | Code | When |
|---|---|---|
| 400 | `bad_json` | the body is not one JSON object (duplicate keys, `NaN`, bad UTF-8 and very deep nesting are refused) |
| 401, 403, 404, 405, 411, 413, 414, 415, 417, 421, 431 | the lower-case status name, such as `unauthorized`, `forbidden`, `misdirected` | the guard or the server refused before or instead of a route |
| 404 | `not_found` | the config file cannot be read (undo and redo) |
| 409 | `stale` | an `expect` value no longer matches |
| 422 | `bad_request`, `unknown_op`, `bad_argument`, `refused` | a bad field, an unknown operation or argument, or the edit core refused (also when the result would not load) |
| 429 | `too_many_requests` | more than 20 changes in a minute (a `Retry-After` header says how long); refused changes count |

### Undo, redo and history

```
POST /__edit/api/undo   {"base": "<hash from the last state>"}
POST /__edit/api/undo   {"base": "<hash>", "anyway": true}
POST /__edit/api/redo   {"base": "<hash>"}
```

`base` is the file hash the page last saw and is required. Unknown fields are
refused (422 `bad_request`); `anyway` is only for undo and must be a boolean.
A success is `200` with `{"state": ..., "history": {"label", "hash",
"can_undo", "can_redo", "recorded"}}`; `state` is the fresh state, so the page can redraw
from it. The old text goes back through the same locked write path as every
other change (backup included), and it must still load.

Undo and redo only run while the file on disk is exactly what the editor wrote
last. Any new `op` clears redo. These refusals send the error shape above, plus
the fresh `state`:

| Status | Code | When |
|---|---|---|
| 409 | `stale` (`path` is `base`) | `base` is not the file's current hash; nothing is changed |
| 409 | `outside_edit` | the file was changed outside the editor; the reply also has `"restore_anyway": true` |
| 409 | `nothing_to_undo`, `nothing_to_redo` | the history has no earlier or later state |
| 409 | `snapshot_unreadable`, `snapshot_corrupt` | the kept copy is missing or was changed |
| 409 | `history_unavailable` | the history folder cannot be used safely, or cannot be saved before the file is changed |
| 500 | `io_error` | the file or its history could not be written; check the state |
| 422 | `refused`, `too_large` | the old text would not load, or is over the size limit |
| 429 | `too_many_requests` | undo, redo and ops share the 20 a minute limit |

If the file was restored but the history could not be saved (disk full, read-only
folder), the reply is still `200` because the file did change: `history.recorded`
is `false` and a top-level `warning` says so. Nothing is lost. The next state,
undo or redo repairs the history from the hash the file now has, so it is not
reported as an outside edit. If the history cannot be saved *before* anything is
written, the reply is `409 history_unavailable` and the file is untouched. A raw
write failure is `500 io_error` with the fresh `state`; reread it before retrying.

After `outside_edit`, show the message and a "Restore anyway" button. That
button repeats the undo with `"anyway": true` and the `base` from the refusal's
`state`. The outside version is saved as a history state first, so redo brings
it back. It also works when the outside edit left a file that does not load.

`GET /__edit/api/history` returns:

```
{"entries": [{"id": 3, "hash": "...", "label": "Rename a link",
              "time": 1792000000.5, "current": true}],
 "can_undo": true, "can_redo": false, "outside_edit": false}
```

Entries are oldest first; `current` marks the state the file is at. Their
text is never sent.

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

Everything else in the config is shown read-only under "All settings" and is changed in a text editor. Adding and removing links or platform items, uploads and undo and redo buttons come in later versions.

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

## Limits and known gaps

Limits that hold today:

- At most 16 requests run at once and 32 connections are held open.
- A request that sends nothing is dropped after 10 seconds. The 16 KiB header
  limit is enforced as bytes arrive (431). The request line and all headers must
  arrive within 10 seconds in total, and a body within 30 seconds, however
  steadily the client sends.
- JSON bodies are at most 64 KiB; 20 changes a minute (undo and redo count).
- History: 100 entries and 5 MiB. The edit core works on config files up to
  1 MiB; the state route reads up to 8 MiB.
- The editor stops after 30 minutes without activity (`--idle-minutes N`
  changes it; above 0 and at most 1440). Only a request to change, undo or redo
  counts as activity (one that reached the route, even if it was then refused).
  Loading the page, reading state and polling version do not, so a session you
  only look at still ends after the idle time. After it stops, start it again;
  the key changes every run.

Known gaps (not built yet):

- Add and remove for links and platform items, drag and drop, and an undo or
  redo button in the page (the routes exist).
- A real preview: the preview listener serves a placeholder, and `build.ok` does
  not run a build.
- Image upload (the guard has the path and the size cap; no route), and any
  image slot: `editing.ASSET_KEYS` is empty and no `validate_slot` is passed, so
  the save-time asset hook is unused.
- A shutdown route or a stop button; stop with Ctrl+C or wait for the idle time.
- Watching the file for outside edits from the page.
- A log line per change (the server is silent).
- A media folder, and any image slot other than `creator.avatar`.

Hand-editing, the `reach-around` edit commands and `reach-around serve` work as
before and are not affected by the editor.
