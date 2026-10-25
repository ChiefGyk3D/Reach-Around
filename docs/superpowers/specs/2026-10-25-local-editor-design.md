# Reach Around: local visual editor (design)

Status: **draft for the maintainer's approval**. Design only; no code is written by this document. Written 2026-10-25 for issue #158, phase 3 of #103 ("Easy editing"). The security analysis is the addendum in `docs/security/threat-model.md` (T-22 to T-36).

How to read this: **[Recommended]** marks a default I chose where the brainstorming process would have asked the maintainer a question. Every one of them is listed again in section 17 with its alternative, so the maintainer can overrule it in one pass. **[Exists]** means the code is on `main` today. **[New]** means it has to be built.

## 1. Intent and why

The site is configured by one YAML file. That is safe, reviewable and comment-friendly, but a non-technical creator will not hand-edit YAML, and the maintainer himself would rather click than count indentation. Issue #103 lays out five phases. Phases 1 (JSON Schema and the `add-link`, `remove-link`, `move-link`, `set` commands) and 2 (edit from anywhere through the instance repo) are done or in hand. This is phase 3: `reach-around edit`, a form editor that runs on the owner's own computer, next to a live preview, and writes the same YAML through the same safe edit core.

The editor exists to let a person change the site without learning YAML. It does not change what the deployed site is. The deployed site stays static. Nothing in this design adds a route, a script, a dependency or a byte to `reach-around build` output.

## 2. Goals

1. A creator can add, remove, reorder and edit links, change theme colors and mode, upload an avatar, and add platform entries with follower counts, without seeing YAML.
2. Every save goes through the existing edit core, so comments, blank lines, quoting and ordering outside the edit stay byte for byte, the file is locked while it is written, and a backup is kept.
3. The build is the validator. If the config would not build, the editor refuses the save and says why in plain words.
4. A preview at phone, tablet and desktop widths is always on screen and updates after each save.
5. Every change can be undone and redone, including after the editor is restarted.
6. The editor works with no network at all.
7. A hostile web page, a hostile browser tab or a hostile config file cannot use the editor to read or write anything it should not (threat model addendum).
8. A keyboard-only user and a screen-reader user can do everything a mouse user can.

## 3. Non-goals and anti-goals

Non-goals:

- A hosted or multi-user editor. That is phase 4 of #103 and needs its own design (authentication in front of it, not inside it).
- Editing every field. The editor covers the fields in section 7; anything else is still YAML, and the editor tells the user how to open the file.
- Editing templates, `custom.css`, CSP extras or the overrides directory. Those are code-like and stay in a text editor.
- A rich text or Markdown editor. Block text is plain text in the existing config.
- Collecting analytics, and any network call from the editor.
- Replacing the commands. They stay, and the editor calls the same functions.

Anti-goals (a failure even if it works):

- The editor is reachable from anything but the owner's own machine.
- Any web page the owner happens to visit can change the config.
- The editor rewrites the owner's YAML in a different style, drops comments, or introduces anchors or tags.
- Editor code or assets appear in a production build or the container image's runtime path.
- A front-end build chain (npm, bundlers) enters the repository.
- A secret is read, shown, logged or sent. The editor never touches instance secrets (section 13).

## 4. Who uses it

| Person | What they do | What they need |
|---|---|---|
| Non-technical creator | Runs a launcher (a desktop shortcut or one command their helper set up), changes links and colors, closes the window. May never have seen a terminal. | Plain-language errors, undo, nothing that can break the site permanently, large click targets, no jargon |
| The maintainer | Quick edits at a desk; drag to reorder; checks phone width before pushing | Speed, keyboard shortcuts, the same diff he would see from the commands |
| A helper (friend, volunteer) | Sets up the instance repo and the launcher for the creator | A documented install, a way to see the YAML diff |

The editor edits the file on disk. It does not commit or push. The creator or helper commits through whatever flow the instance repo uses (#78, phase 2 of #103). The editor shows the plain-language diff of the unsaved-to-git changes only if the config sits in a git working tree (optional, section 17 D-12).

## 5. User journeys

Each journey names the operation (section 8.3) it sends. "Saved" always means: written to disk, backed up, rebuilt, and shown in the preview.

**J1. Add a link.** The user selects a tab, clicks "Add link", fills Title and URL (required), optionally Subtitle, Icon (a picker of the shipped icon names), Affiliate. The form checks the URL as the user types (https only, using the same rules as the loader). Save sends `add_link`. The new link appears at the end of the tab, or at the position chosen with the "Insert after" selector. If the build rejects it, nothing is written and the error appears next to the field it names when the loader gave a path, otherwise at the top.

**J2. Reorder.** In the tab's list each link has a drag handle and "Move up" and "Move down" buttons; keyboard users use the buttons or focus the handle and press Space then arrows (section 11). A drop sends `move_link` with the link id and the 1-based position. One drop is one operation and one undo step.

**J3. Change colors.** A "Colors" panel shows the six theme colors (bg, text, accent, on_accent, surface, border) as a color input plus a hex text box, and the mode (light, dark, auto). Changing a color sends `set` with `theme.colors.<key>`. Contrast between text and bg, and on_accent and accent, is shown as a pass or fail beside the field (computed in the browser from the typed hex; advisory only, the server does not enforce it). Debounced: one operation after the user stops changing a value for 600 ms or leaves the field, so dragging a color picker does not create fifty undo steps.

**J4. Upload an avatar.** "Choose picture" opens the file picker (or accepts a drop). The browser reads the file locally, shows a crop-free preview, and posts the bytes. The server checks them (section 10), stores them under a name it picks, and reports the stored path. The UI then sends `set` with `creator.avatar` and that path as one combined action so the file is not left unreferenced if the second step fails. The old avatar file is left on disk (undo needs it) and is listed under "Unused pictures" for later cleanup.

**J5. Add an item to a platforms block with a follower count.** The user opens the platforms block, clicks "Add platform", picks the platform from a list, types handle, URL (pre-filled from a per-platform pattern, editable), optional display name, label (followers, subscribers, and so on, from the schema enum), count and "as of" date. The count field accepts digits only, and a blank count is saved as no count at all, never as `0` (the existing rule in `docs/customizing.md`). Entering a count sets "as of" to today unless the user changes it. Sends `add_platform_item`.

**J6. Preview at three widths.** The preview pane has three buttons: phone (390 px, matching `/__preview` today), tablet (768 px), desktop (1280 px, scaled to fit). The choice is remembered per browser tab in memory only. The preview is the built site served from the preview origin (section 6). A link inside the preview that leaves the site opens nothing; the editor intercepts it (the iframe sandbox forbids navigation, section 6).

**J7. Undo and redo.** Ctrl+Z, Ctrl+Shift+Z and visible buttons. Undo restores the previous file text through the same locked, atomic write path. Each undo step is labeled in words ("Moved Merch above Newsletter"). The history survives a restart (section 9).

**J8. Someone else edited the file while the editor is open.** A banner appears: "The file changed outside the editor." The editor reloads the data; unsaved form fields are kept if they do not conflict. Details in section 9.

**J9. The config is broken at start.** The editor opens in a read-only state showing the same build error the preview shows, with the line when known, and a button "Open undo history" if a backup exists, so a bad hand edit can be reverted without the terminal.

## 6. Architecture

### 6.1 Overview

```
 browser tab (editor UI)                         browser iframe (preview)
 http://127.0.0.1:E/__edit/                       http://127.0.0.1:P/   (separate port = separate origin)
        |  fetch + X-RA-Token                           ^
        v                                                |
 +---------------------------- one Python process, reach-around edit ----------------------------+
 | editor server (E)  ----> edit core (editing.py) ----> site.yaml  (flock, backup, atomic rename) |
 |        |                         ^                                                              |
 |        v                         | validate = load_site_config + collect_assets                 |
 |  history store                   |                                                              |
 | preview server (P) <---- build_site into temp dir (existing Preview class)                    |
 +-------------------------------------------------------------------------------------------------+
```

- **Loopback only.** Both listeners bind `127.0.0.1` (or `::1` when asked). The existing `_is_loopback` refusal is reused; `0.0.0.0`, LAN addresses and names that resolve to anything but loopback are refused at start. [Exists: `preview.py`.]
- **Same engine.** The editor is `reach_around.editor`, a sibling module of `reach_around.preview`. It reuses the `Preview` class for building, watching and serving the built site (port P). It is a new command `reach-around edit --config site.yaml [--port N] [--overrides DIR]`. `serve` keeps working and is not changed in behavior.
- **Two listeners, two origins.** The editor UI (port E) and the preview (port P) are different origins because the ports differ. The preview shows content the owner wrote, including text a shared example config could have planted. Keeping it on its own origin and inside a sandboxed iframe means that content can never call the editor API or read the editor's memory (T-34).
- **Writes go through the existing safe edit core.** The editor never builds YAML text itself. Each request is translated to one function in `editing.py` and run through `editing.apply(...)`, which locks the file, composes with the YAML composer only to locate nodes, splices whole lines, checks twice (loads with the hardened loader, and compares the loaded data to "original plus exactly this change"), backs up, fsyncs, re-checks the file did not change, and renames. [Exists.]
- **Build as validator.** `apply` already refuses a candidate that fails `load_site_config`. The editor adds one more validation hook (section 9.4): after the config loads, the asset slots of the candidate are checked with the same code `collect_assets` uses, so a path to a missing or malformed image is refused at save time, not discovered at the next preview build. The full site build still runs after the write, in the preview; if it fails the editor shows the error and offers one-click undo.
- **No new YAML writer.** The existing operations cover add, remove, move and `set` of a fixed key list. The editor needs four additions to `editing.py` (section 6.3). They follow the same pattern (locate by composer, splice lines, pass the double check). Nothing in the editor adds YAML text on its own.

### 6.2 How the UI is served

- The UI is static files in `src/reach_around/editor_assets/` (HTML, CSS, ES modules, an icon sprite). They live outside the site package data, like `preview_assets/`, so they cannot reach a production build. The same `importlib.resources` read the preview uses serves them. Nothing is generated or minified at install time.
- **No CDN, no external font, no analytics, no remote image.** The page has no `https:` URL in it (a test greps for it, section 15).
- **No inline script, no inline style, no `eval`.** The shell HTML references `app.js` as `<script type="module" src>` and `app.css` as a stylesheet. Dynamic styling uses CSS classes and CSS custom properties set through the CSSOM (`element.style.setProperty`), which a strict `style-src 'self'` allows.
- **Strict CSP** on every editor response:

```
default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:;
connect-src 'self'; frame-src http://127.0.0.1:<P> http://localhost:<P> http://[::1]:<P>;
form-action 'none'; base-uri 'none'; object-src 'none'; frame-ancestors 'none';
sandbox allow-same-origin allow-scripts allow-forms allow-modals allow-downloads
```

  The `frame-src` value lists only the preview origin in the form the user opened (one entry, not three; the three above are the possible forms). `blob:` images show an uploaded file before and after upload without a second request. `frame-ancestors 'none'` plus `X-Frame-Options: DENY` stops clickjacking (T-30). The response also carries `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-origin` and `Cache-Control: no-store`. The `sandbox` directive on the document is optional hardening; whether it breaks file picking needs checking in the browser tests (D-15).
- **Plain ES modules, no build step, no npm.** About ten files, each under 400 lines, loaded by the browser's module loader. Drag and drop uses the browser's native drag events plus a keyboard path built on buttons. No library is required for the MVP. If a library is ever added it is vendored, hash-pinned, license-listed and covered by T-33 and the source-grep tests in section 15. [Recommended: none.]
- **Rendering rule for the UI.** All values from the config (titles, handles, URLs) enter the page with `textContent` or `setAttribute` on known attributes, never `innerHTML` (T-32). A test greps the editor sources for `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write` and `eval`.

### 6.3 Edit operations the core needs

Existing [Exists]: `add_link`, `remove_link`, `move_link`, `set_value` over `SET_KEYS` (creator name and title, theme background dim and mode, the six theme colors). New [New], each a function in `editing.py` with the same shape and the same double check:

| Operation | What it edits | Notes |
|---|---|---|
| `set_link_field` | One scalar field of an existing link: title, url, subtitle, icon, affiliate, featured | Located by link id. Refuses multi-line or flow-style values the way `set_value` does |
| `add_platform_item`, `remove_platform_item`, `move_platform_item` | An item of a `platforms` block | Item list is block style; same refusals as links. `remove` refuses to empty the list (min length 1) |
| `set_platform_item_field` | handle, url, name, label, count, as_of of one item | `count` omitted from the file when blank; never written as `0` |
| `set` key `creator.avatar` | The avatar path | Added to `SET_KEYS` with the path rules of the asset pipeline |

Anything the core refuses today (flow style, CRLF, a dash on its own line, anchors) stays refused, and the editor shows "This part of the file is written in a style the editor will not rewrite. Open it in a text editor" with the line number.

### 6.4 Process model

One process, three threads of interest: the editor server, the preview server (existing), and the watcher (existing, polling every 500 ms). The editor and preview servers each use `ThreadingHTTPServer` with a cap on concurrent connections (section 8.5). All writes are serialized by a single in-process mutex in addition to the file lock, so two requests from one tab cannot interleave their history bookkeeping.

## 7. What the editor can change

MVP: link fields, platform items, theme colors and mode, background dim, creator name and title. Phase 3: avatar upload (and later favicon and the image slots). Everything else: read-only display plus "edit in a text editor".

The form fields and their limits come from the JSON Schema (`reach_around.schema.build_schema()`), served to the UI, so the editor's idea of "valid" is not a second copy that can drift. The server is still the authority: the schema in the browser is only for early hints.

## 8. HTTP API

### 8.1 Rules for every request

- Methods: `GET`, `POST` only. `PUT`, `PATCH`, `DELETE`, `OPTIONS` get 405. There is no CORS, so no `Access-Control-*` header is ever sent and `OPTIONS` is not answered.
- `Host` must be `127.0.0.1:<E>`, `localhost:<E>` or `[::1]:<E>` **including the port** (stricter than the preview today, which accepts any port); otherwise 421.
- `Origin`, when present, must equal the scheme, host and port of the request; it must be present on every `POST`. `Sec-Fetch-Site`, when present, must be `same-origin` (or `none` for the first navigation). Otherwise 403.
- Every `/__edit/api/*` request, `GET` included, carries `X-RA-Token`. Missing or wrong: 401, with no detail, compared with `hmac.compare_digest`.
- `POST` bodies: `Content-Type` exactly `application/json` (or `application/octet-stream` for the upload route); anything else 415. `Content-Length` is required (411 if absent; chunked transfer is refused). Caps in 8.5.
- Responses are JSON with `Content-Type: application/json`, `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`.
- Errors have a fixed shape: `{"error": {"code": "...", "message": "plain words", "path": "tabs[0].blocks[2].url"}}`. Messages never echo a request header or the token.

### 8.2 Route table

| Method | Path | Auth | Body | Effect |
|---|---|---|---|---|
| GET | `/__edit/` | none (Host/Origin checks only) | none | The shell HTML. Contains no config data and no token |
| GET | `/__edit/app.js`, `/__edit/*.js`, `/__edit/app.css`, `/__edit/icons.svg` | none | none | Static editor assets, from an allowlist of file names |
| GET | `/__edit/api/state` | token | none | Config as JSON (the parsed values, no comments), file hash, mtime, build status or error, whether undo or redo is available, preview URL, limits |
| GET | `/__edit/api/schema` | token | none | The JSON Schema from `build_schema()` |
| GET | `/__edit/api/version` | token | none | A counter that changes when the file or the build changes (the UI polls it every 500 ms; polls do not reset the idle timer) |
| POST | `/__edit/api/op` | token | JSON `{"base": "<hash>", "op": "add_link", "args": {...}, "expect": {...}, "label": "..."}` | Runs one edit operation through `editing.apply`; returns the new state and the history entry. Refused with 409 `stale` when `expect` no longer matches (section 9.2) |
| POST | `/__edit/api/upload` | token | raw bytes, `Content-Type: application/octet-stream`, header `X-RA-Kind: avatar` (one of a fixed list) | Checks and stores one image; returns `{"path": "media/avatar-3f9c1a07.png", "bytes": N}`. Does not edit the config |
| POST | `/__edit/api/undo` | token | JSON `{"base": "<hash>"}` | Restores the previous snapshot if the file still equals the editor's last write |
| POST | `/__edit/api/redo` | token | JSON `{"base": "<hash>"}` | The inverse of undo |
| GET | `/__edit/api/history` | token | none | Labels, times and hashes of the history entries (not their contents) |
| POST | `/__edit/api/history/restore` | token | JSON `{"id": N, "base": "<hash>"}` | Restores a chosen older snapshot (this is itself undoable) |
| GET | `/__edit/api/media` | token | none | Names, sizes and referenced-or-not flag of files in the media folder |
| POST | `/__edit/api/shutdown` | token | none | Stops both servers and deletes the temp build directory. The UI offers "Done" |

Preview origin P (a separate listener):

| Method | Path | Auth | Effect |
|---|---|---|---|
| GET, HEAD | built site paths | Host and Origin checks, no token (it serves only the public build output) | The existing preview behavior. Responses add `Content-Security-Policy: frame-ancestors http://127.0.0.1:<E> ...` so only the editor may frame it, and `Cross-Origin-Resource-Policy: same-site` is not used (see T-34) |

### 8.3 Operations accepted by `/api/op`

`add_link`, `remove_link`, `move_link`, `set`, `set_link_field`, `add_platform_item`, `remove_platform_item`, `move_platform_item`, `set_platform_item_field`. Arguments are exactly those of the matching core function; unknown keys are refused (422). The server never accepts raw YAML text, a file path other than a media path produced by `/api/upload`, or a free-form key for `set` (the key must be in `SET_KEYS`).

### 8.4 The token

256 random bits (`secrets.token_urlsafe(32)`), created at start, kept in memory, valid until the process exits. It is delivered once, in the URL **fragment**: the process prints `http://127.0.0.1:<E>/__edit/#t=<token>` (and opens it in the browser only if started with `--open`; the default opens nothing). A fragment is not sent to the server, not placed in `Referer`, and not logged by the server. The page reads it, keeps it in a JavaScript variable (not `localStorage`, `sessionStorage`, a cookie or a URL), and calls `history.replaceState` to remove it from the address bar at once. A reload loses it; the user then opens the printed URL again (or the launcher does). That is the cost of keeping it out of storage. Details and residue in T-24.

### 8.5 Caps

| Item | Cap | Result when exceeded |
|---|---|---|
| JSON body | 64 KiB | 413 |
| Upload body | 8 MiB (the asset pipeline's `MAX_ASSET_BYTES`), and 2 MiB for `avatar` | 413 |
| Request line and headers | 16 KiB | 431 |
| Concurrent connections per listener | 16 | extra connections wait; none queue beyond 32 |
| Mutating requests | 20 per minute, 5 uploads per minute | 429 with `Retry-After` |
| Read timeout per request | 10 s for headers, 30 s for the body | connection closed |
| History entries kept | 100 | oldest dropped |
| Media folder files created by the editor | 200 | 409 |

## 9. State, concurrency, undo, redo, backup

### 9.1 Who else can write the file

The creator may have the YAML open in a text editor, a git pull may replace it, a second `reach-around add-link` may run, or two editor tabs may be open. The edit core's lock covers other `reach-around` commands. It does not stop an editor that ignores locks, so the core re-checks identity and hash right before the rename (the window is tiny and cannot be closed on POSIX; documented in `editing.py`). [Exists.]

### 9.2 Detecting and handling an external change

- The watcher already polls the config every 500 ms. The editor adds the file's SHA-256 to its state. The UI polls `/api/version`; when the hash differs from the one the UI holds, it shows the banner in J8 and re-fetches `/api/state`.
- Every `/api/op` carries `base`, the hash the UI last saw, and `expect`, the old value(s) of the field(s) the operation touches (for `move_link`, the current order of the list; for `set`, the old value; for `add_link`, the tab's current link count). The server evaluates the operation on the **current** file under the lock, not on the UI's copy. Then:
  - If `base` equals the current hash: apply.
  - If `base` differs but every `expect` value still matches the current file: the external change did not touch this field. Apply (this is the "merge": operations are semantic, by id and key, so they compose with an unrelated edit).
  - If an `expect` value no longer matches (someone else changed this very field or list): refuse with 409 `stale` and return the fresh state; the UI shows "This changed while you were editing. Your change was not saved. [Show what changed] [Try again]". Never overwrite.
- The editor never edits from its in-memory copy of the text. It always reads the file fresh under the lock inside `apply`.

### 9.3 History, undo, redo

- Before the first write of a session and after every write, the editor keeps a snapshot of the file text in `<config dir>/.reach-around-history/` as `NNNN-<hash8>.yaml` plus a `index.json` (labels, times, hashes). Files are mode 0600 (inherited from the config's mode if stricter), the directory 0700, written with the same stage, fsync and rename helpers as the backup. A `.gitignore` line is suggested in the instance template; the editor refuses to run if the directory would be a symlink or outside the config's folder.
- This extends, and does not replace, the existing single `site.yaml.bak`, which keeps working for the commands.
- **Undo** writes the previous snapshot's text through the locked write path (a `restore` operation that skips the "equals original plus one change" check but still requires the text to load with the hardened loader and build). It is allowed only when the current file hash equals the hash the editor produced last. Otherwise the file was edited elsewhere and undo would destroy that edit: refused with an explanation and a "restore anyway" choice that first takes a snapshot of the current text so even that is undoable.
- **Redo** works the same way and is cleared by any new operation.
- The history is capped at 100 entries and at 5 MiB in total (config files are under 1 MiB by the core's `MAX_BYTES`); oldest entries go first.
- Config files contain no secrets by design (section 13), so snapshots hold the same data class as the config itself.

### 9.4 Validation order for a write

1. Request checks (8.1).
2. Operation arguments checked against the schema-derived rules.
3. Lock, read, `load_site_config` on the current file.
4. Compute the edit; load the candidate with the real loader; compare with "original plus exactly the change". [Exists in `apply`.]
5. [New] Run the asset-slot checks on the candidate (every referenced image exists under the config folder and passes `check_content`).
6. Backup (`.bak`), history snapshot, re-check hash, atomic rename, fsync the folder. [Exists, plus the snapshot.]
7. The watcher sees the change and rebuilds into a fresh temp directory; the result (ok or error) is reported by `/api/version` and `/api/state`.

## 10. Uploads

- Only `POST /api/upload`, only the kinds in a fixed list (`avatar` in phase 3; `favicon` and image-block slots later). The kind selects the allowed formats and the size cap.
- The client's file name is never used, not even as a suffix. The server sniffs the type from the bytes: it picks the format by magic number, then runs `check_content` for that format (signature plus the structural facts the asset pipeline already checks), and for SVG runs the allowlist screen (no DOCTYPE or ENTITY, no script, no event handlers, no external reference). Mismatch or failure: 422 with a plain message.
- The stored name is `media/<kind>-<8 random hex>.<ext>` where `<ext>` comes from the sniffed format. The file is written to a temp name in the same folder with `O_EXCL | O_NOFOLLOW`, mode 0644, synced, then renamed. The media folder is opened by directory file descriptor and never followed through a symlink, using the same one-component-at-a-time open the asset pipeline uses.
- The upload route does not touch the config. Referencing the file is a second operation (`set creator.avatar`), and the browser sends both back to back; if the second fails the UI offers "Remove the uploaded file".
- Metadata (EXIF GPS, camera serials) is **not** stripped in the MVP; the editor shows a warning when it finds an EXIF block in a JPEG ("this picture may contain your location") and the decision on stripping is D-08 in the threat model.
- The editor never fetches a remote image. A URL avatar (still accepted by the loader) can be typed in the form but is not downloaded.

## 11. Accessibility

Target: WCAG 2.2 AA for the editor UI itself. The preview shows the site, whose accessibility is the site's own concern.

- Everything works with keyboard only. Reordering has a non-drag path: each item has "Move up", "Move down", and "Move to position" controls, and the drag handle is a real `button` with `aria-grabbed`-free semantics (a live region announces "Moved Merch to position 2 of 5"). Drag and drop is an enhancement, never the only way.
- Real form controls with associated `<label>`s; errors tied to fields with `aria-describedby` and `aria-invalid`; a polite live region for "Saved", "Could not save: ...", "Undone: ...".
- Focus is never lost on save, and returns to the control that triggered a dialog on close. Visible focus ring that passes 3:1 contrast. Target size at least 24 by 24 CSS px, 44 for touch-oriented layouts.
- Color is never the only signal (pass or fail contrast uses text and an icon). Respects `prefers-reduced-motion` (no animated reorder) and `prefers-color-scheme`.
- Page `lang`, a single `h1`, landmarks (`header`, `main`, `nav`), and a skip link to the preview.
- Zoom to 400% reflows to one column; the preview pane stacks below the form.
- Tested with an automated checker in the browser tests (for example axe-core run from the test suite only, never shipped; see D-14 on whether that counts as a dev dependency) and by a manual screen-reader pass recorded in the pull request for each phase.

## 12. Internationalization

- All user-visible strings live in one module (`strings.js`) keyed by id, American English, so a translation is a second file, not a code change. No string is built by concatenating fragments; placeholders are named.
- Layout uses CSS logical properties (`margin-inline-start`) so right-to-left works without a rewrite. `dir` follows `lang`.
- Dates are shown with `Intl.DateTimeFormat` and stored as ISO `YYYY-MM-DD` only (the loader's rule). Numbers: the follower count field accepts digits and strips grouping characters the browser's locale adds; the server accepts only a strict non-negative integer.
- Text the creator writes (titles, names) can be any language the loader accepts. The editor does not transliterate or normalize beyond what the core's `check_text` does (it refuses control characters).
- Phase 1 ships English only. Translation is not a goal of #158.

## 13. Secrets and what the editor reads

The editor reads and writes only the config file, the history folder, the media folder and files it created. It does not read environment variables other than its own flags, `.env` files, Doppler, the Worker's secrets, the collectors' credentials, the Cloudflare token or anything under the instance's deploy folders. The config schema holds no secret by design; if a future field did, it would be excluded from `/api/state` by an allowlist (the state endpoint is built from the schema's field list, not by dumping everything). The page never displays an environment variable and the server never echoes a header. (T-29.)

## 14. Offline behavior, packaging, size

- **Offline.** No network call is made by the editor server or the UI. A test starts the server with outbound traffic blocked and with a canary listener, drives a full session, and asserts zero connections. The preview shows the site as built; remote images named in the config (`img-src https:` in the site's own CSP) would be fetched by the preview iframe. The preview origin therefore serves a stricter policy (`img-src 'self' data:`) in the editor, and remote images show as a placeholder with a note. [Recommended; D-06.]
- **Packaging.** `src/reach_around/editor_assets/` and `src/reach_around/editor.py` ship in the wheel under package data, separate from `site/`. The container image's `build` entrypoint is unchanged and does not start the editor. `reach-around edit` in the container is unsupported (the build container has no network and no TTY by design) and the command says so.
- **Size budget.** Editor assets: at most 150 KiB uncompressed in total for the MVP (HTML, CSS, JS, SVG sprite), at most 250 KiB after phase 4. A test fails the build above the budget. No fonts: system font stack.
- **Platforms.** Linux and macOS first (the edit core uses `fcntl.flock` and `O_NOFOLLOW`). Windows is out of scope until the core supports it (D-13).

## 15. Test strategy

1. **Unit (pytest, no browser).** One test module per concern: `tests/editor/test_guard.py` (Host, Origin, Sec-Fetch-Site, token, content type, method, caps, DNS-rebinding table), `test_ops.py` (each operation: result equals the command's result byte for byte; unknown keys refused), `test_stale.py` (the three cases in 9.2), `test_history.py` (undo, redo, external edit refused, cap, permissions), `test_upload.py` (magic-number mismatch, renamed text file, polyglot, oversize, SVG with script, name collision, symlinked media folder), `test_paths.py` (config and media symlink and `..` cases), `test_csp.py` (header present on every editor response; no `unsafe-*`), `test_sources.py` (greps the editor sources: no `innerHTML`, `eval`, `document.write`, inline `on*=` attributes, `http(s)://` URLs, `import` from a URL).
2. **Core additions.** The new `editing.py` operations get the same hostile-input tests as the existing ones in `tests/test_edit.py` and `tests/test_edit_durability.py`, including the comment-preservation and "no anchors or tags introduced" checks.
3. **Browser tests (playwright, already a dev dependency).** Driven through the quiet launcher: a test helper that starts the editor on an ephemeral port in a temp directory with `--no-open`, sets `BROWSER=true`-style neutral environment, blocks navigation to `mailto:` and `tel:` and any non-loopback URL through a route handler, and never opens the system browser or a protocol handler. The existing guard `tests/test_browser_tests_never_navigate.py` is extended: a browser test may not call `webbrowser.open`, may not pass `--open`, and every page it opens must be on `127.0.0.1`. Scenarios: J1 to J9, a keyboard-only reorder, a screen-reader-name check with the accessibility tree, the phone, tablet and desktop buttons, a second tab making an external edit.
4. **Mutation checks.** Each security test is paired with a step that removes the control and proves the test turns red, as the threat model's item 48 already requires: delete the token check, delete the Origin check, widen the Host allowlist, accept a `.php` upload, skip the magic-number check, skip the `expect` comparison, drop `frame-ancestors`. These run in review, recorded in the pull request, as in the earlier plans.
5. **Fuzz.** The request parser and the operation argument decoder get a small property-based test (hypothesis is a dev dependency decision, D-14; otherwise a seeded random-bytes loop) that asserts: never a 500, never a write on a refused request, file unchanged after any refused request.
6. **Gate.** `pytest`, `ruff`, `mypy` as today; the editor package is typed. The CI uses the GYST python-ci caller already in place; no new workflow is needed, only a job step for browser tests if the existing job does not already install playwright browsers (the plan task checks).

## 16. Phased delivery

Sizes are for one developer who knows the codebase: S = under 2 days, M = 2 to 5 days, L = 1 to 2 weeks.

| Phase | Scope | Size | Exit criteria |
|---|---|---|---|
| 0. Core additions | `set_link_field`, platform item operations, `creator.avatar` in `SET_KEYS`, asset-slot validation hook in `apply`, history store | M | Unit tests green; commands keep their behavior; mutation checks recorded |
| 1. MVP: preview editor with forms for existing blocks | `reach-around edit`, both listeners, guard, token, CSP, state and op routes, forms for link fields and the platforms block, colors and mode, three-width preview, undo and redo, idle timeout | L | J1, J3 (colors), J5, J6, J7, J8, J9 pass in a browser; threat model T-22 to T-36 verifications for items shipped are green; docs page `docs/editor.md` |
| 2. Drag and drop | Pointer drag with the keyboard equivalents, reorder across blocks inside a tab, announcements | S to M | J2 by mouse and by keyboard; no new server routes |
| 3. Uploads | `/api/upload`, avatar flow, unused-media list, EXIF warning | M | J4; T-27 verifications green |
| 4. Theme picker | Presets (ship two or three palettes as plain data), contrast hints, background and mascot slots reuse the upload route | M | Colors apply as a single history step; palettes pass the contrast check |
| Later | Favicon and image-block slots; git-aware diff panel; translations | S each | Not part of #158 |

Total to phase 4: roughly five to six weeks of focused work, which is about ten weeks at the maintainer's after-hours rate. The MVP alone (phases 0 and 1) is the useful release.

## 17. Open decisions (the questions I could not ask)

Each has a recommended default; none blocks starting phase 0.

| ID | Question | Recommended default | Alternative and trade-off |
|---|---|---|---|
| D-01 | Token delivery: URL fragment printed to the terminal, or a one-time code exchanged for a token? | Fragment, read once, removed from the address bar, held in memory only | Exchange code: the fragment secret lives for seconds not the session, but adds a route and a state machine for little gain on a single-user machine |
| D-02 | Does the editor open the browser itself? | No. It prints the URL. `--open` is opt-in. Tests never use it | Auto-open is friendlier to the non-technical user; a launcher shortcut script can add `--open` for them |
| D-03 | Port: fixed (8000 as `serve`) or random ephemeral? | Ephemeral for the editor, printed at start | Fixed is bookmarkable but is easier to target blindly and collides with `serve` |
| D-04 | Preview on a second port or the same port with a different hostname (`localhost` vs `127.0.0.1`)? | Second port | Hostname trick needs two allowed Hosts and is easy to mis-set; a second port is plain |
| D-05 | Iframe sandbox: `allow-scripts` only, or also `allow-same-origin`? | `allow-scripts` only (opaque origin; the site's scripts run but cannot touch storage or the parent) | Adding `allow-same-origin` makes the preview a normal origin that can read its own storage; the site does not need that |
| D-06 | Remote images in the preview: block by default, or allow? | Block, with a visible placeholder and a `--allow-remote-images` flag | Blocking keeps "no network egress" true but shows broken thumbnails for https images |
| D-07 | History location: `<config dir>/.reach-around-history/`, or the user data directory? | Beside the config, gitignored | Data dir keeps the instance repo clean but separates history from the file it belongs to and from backups |
| D-08 | Strip image metadata on upload? | Warn only in phase 3 (consistent with threat-model D-08) | Strip needs an image library or a hand-written JPEG/PNG chunk filter, and a review |
| D-09 | Undo through the editor when the file was edited outside | Refuse, offer "restore anyway" that snapshots first | Always allow: simpler but silently destroys an outside edit |
| D-10 | Idle timeout value | 30 minutes without a mutating request, configurable with `--idle-timeout`, `0` disables it | Shorter is safer, annoying during long sessions |
| D-11 | Rate limit values (20 mutations per minute, 5 uploads per minute) | As in 8.5 | Higher for power use; these are guesses to be tuned |
| D-12 | Show a git diff panel when the config is in a git work tree? | Later, not in #158 | Useful to helpers; adds a `git` subprocess to review |
| D-13 | Windows support | Not in #158 | Needs a different lock and symlink story in the core first |
| D-14 | Test-only dependencies: axe-core (npm or vendored) and hypothesis | Hypothesis yes (pip, hash-locked like the others); axe-core only as a vendored, hashed, license-listed file under `tests/` that is never packaged | Skip automated a11y checks: cheaper, weaker |
| D-15 | Add the CSP `sandbox` directive to the editor document | Try it; keep it if the file picker and downloads still work in the browser tests | Omit: the other directives already cover the threats |
| D-16 | Audience for the first release: maintainer only, or a named non-technical tester? | A named tester before phase 2 starts, to correct the journeys | Maintainer only: faster, risks building for the wrong user |
| D-17 | A launcher for non-technical users (desktop shortcut or small script) | Document one command first; a packaged launcher is a separate issue | Ship a script generator now |
| D-18 | Hosted editor (phase 4 of #103) | Out of scope; this design's token-in-memory model does not carry over and it needs authentication in front | Reuse as-is behind a tunnel: not recommended, see T-22 |

## 18. Plan outline (not a full plan)

A full writing-plans document comes after the maintainer answers section 17. Order, with the issue and board card created before each task starts:

1. **Issue and card for each task below** (no hidden work), linked to #158 and #103.
2. **Core: new operations** (`set_link_field`, platform item operations, `creator.avatar`), tests first, mutation checks. Size M.
3. **Core: asset-slot validation hook and history store** (snapshots, index, caps, permissions). Size S.
4. **Editor server skeleton**: `reach-around edit`, two loopback listeners, guard (Host with port, Origin, Sec-Fetch-Site, token, content type, caps), headers and CSP, idle timeout, shutdown. Tests for the guard before any route does real work. Size M.
5. **State, version and op routes**, including the `base` and `expect` handling and the error shape. Size M.
6. **UI shell**: HTML, CSS, `strings.js`, token handling, API client module, state store, announcer. No forms yet; renders the config read-only with the preview. Size M.
7. **Forms for existing blocks**: link fields, platform items, colors and mode. Size M.
8. **Undo, redo, history panel, stale-change handling and banner.** Size S.
9. **Browser tests through the quiet launcher** and the extended navigation guard; the accessibility pass. Size M.
10. **Docs**: `docs/editor.md`, update `docs/preview.md` and `docs/customizing.md`, update the threat model's verification names from "proposed" to "exists" as tests land. Size S.
11. **Security review of the MVP branch** on the cyber-program tier (final whole-branch review, per the maintainer's rule), fix Critical and High before release. Size S.
12. **Release the MVP.** Then phase 2 (drag and drop), 3 (uploads, with its own review of T-27), 4 (theme picker), each as its own issue, branch and review.
