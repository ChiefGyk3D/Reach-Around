<!-- SPDX-License-Identifier: Apache-2.0 -->
# A strict CSP, template overrides, and why there is no custom JavaScript

Reach Around lets you change how your page looks. It does not let you add
your own JavaScript. This page explains why, what the security headers do,
and how overrides work. The tests named here prove each claim.

## What a Content-Security-Policy is

A Content-Security-Policy (CSP) is a list of rules the site sends to the
browser: where scripts may come from, where styles may come from, whether the
page may be put in someone else's frame. The browser enforces it. If the page
is ever tricked into containing a script the policy does not allow, the
browser refuses to run it.

Ours says, in plain language:

- Load scripts, styles, fonts and connections only from this site
  (`'self'`). Images may also come from https addresses, because creators
  link artwork hosted elsewhere.
- No plugins (`object-src 'none'`).
- Nobody may frame the page (`frame-ancestors 'none'`), which blocks
  clickjacking.
- No `<base>` tag tricks (`base-uri 'none'`), and forms post only to this
  site.

The words `unsafe-inline` and `unsafe-eval` are missing on purpose. They are
the switches that turn script protection off. `test_headers_file_has_no_unsafe_directives`
fails if either appears.

The same `_headers` file also sends `nosniff` (the browser must trust the
declared file type), a referrer policy that sends only the site name to other
sites, a permissions policy that turns off camera, microphone, location and
payment, `Cross-Origin-Opener-Policy`, and HSTS, which tells browsers to use
https only.

## Why the pages must work under that policy

A strict CSP only works if the site does not need the things it forbids. So
the pages have no `style="..."` attributes, no `<script>` with code inside,
and no `onclick=`-style handlers. Colors come from `static/theme.css`, a real
file. `test_every_page_still_passes_with_the_csp` scans every built page for
all three, and `test_csp_scanner_bites` proves the scanner catches each kind.

## Why no custom JavaScript

Custom script has two ways in: inline (needs `unsafe-inline`) or from another
host (needs that host added to `script-src`). Either one weakens the policy
for every visitor, and a pasted snippet from the internet is exactly the
kind of thing the policy is meant to stop. Trackers, chat widgets and "free"
counters are the usual snippets, and they are also how visitor data leaks.

So the rule is simple: no custom JavaScript. If you put a `.js` file in the
overrides directory, the build ignores it and prints a warning naming the
file (`test_no_custom_javascript_is_loaded`). If you override a template and
add a script tag anyway, the browser blocks it.

The one planned exception is the gate page, which needs the Turnstile script
from one named https origin. The CSP builder takes a per-path extra for that,
accepts only `'self'` or an exact https origin, and refuses `*`, `data:`,
`unsafe-inline` and `unsafe-eval`
(`test_build_csp_refuses_wildcards_and_unsafe_sources`). The relaxation is one
page wide, not site wide.

## How overrides work

Point `--overrides` at a directory:

```
my-overrides/
  templates/_footer.html   # replaces the packaged footer
  custom.css               # appended after the theme
```

- A template in `templates/` wins over the packaged one of the same name. Any
  template you do not provide falls back to the packaged one
  (`test_override_template_wins_and_missing_ones_fall_back`).
- A template name can never leave that folder: `../x.html`, an absolute path
  and a symlink pointing outside are all refused
  (`test_override_name_cannot_escape_the_directory` and its symlink siblings).
  Otherwise a template named by a stranger's config could read any file you
  own.
- Templates run in Jinja's immutable sandbox. A template is code, and without
  the sandbox `{{ cycler.__init__.__globals__.__builtins__.open(...) }}`
  reaches the file system. The sandbox refuses it
  (`test_the_globals_escape_is_a_security_error`).
- Each override template is capped at 256 KiB, like `custom.css`. The limit is
  enforced on the bytes actually read (one more than the limit is requested),
  not on the size the file claims, so a sparse or growing file cannot slip
  through. The error names the file
  (`test_template_one_byte_over_the_cap_is_refused_by_name`,
  `test_template_cap_is_enforced_on_bytes_read_not_on_stat`).
- The overrides directory, `templates/` and `custom.css` must be real, not
  symlinks (`test_symlinked_templates_directory_is_refused`). Otherwise a
  symlinked `templates/` would turn any outside folder into a trusted root.
- Files are opened with `O_NOFOLLOW` and read from the descriptor, after
  `fstat` confirms a regular file inside the directory
  (`test_template_read_uses_o_nofollow`). A swap to a symlink between the
  check and the read fails instead of leaking the target.
- `custom.css` is checked before it is used. `@import`, `expression(`,
  `behavior:`, `-moz-binding` and `javascript:` are refused, and so is
  anything over 256 KiB (`test_custom_css_with_import_or_script_url_is_refused`).
  Old browsers ran code from some of those.
- The check follows how a browser tokenizes CSS. Any backslash is refused,
  because an escape like `\2f\2a` decodes to `/*` and could open a fictitious
  comment that hides the rest from a filter while the browser keeps it active
  (`test_css_tokenizer_tricks_are_refused`). Creators rarely need escapes.
  Real comments are stripped from the raw text first, with strings respected,
  and an unterminated comment is refused.
- The check is deny-by-default for CSS functions that can fetch a URL. These
  are refused by name, case-insensitively, with or without whitespace before
  the parenthesis and with or without a vendor prefix such as `-webkit-`:
  `image()` (which takes a plain string such as `image("https://...")`),
  `image-set()`, `src()` and `cross-fade()`. So are `@import` and
  `@namespace`. The page CSP allows https images, so any of these would let a
  stylesheet make every visitor's browser contact a third party
  (`test_css_functions_that_take_a_url_are_refused`). `element()` and
  `paint()` are allowed: neither fetches anything (`element()` points at a node
  in the same document, and `paint()` only names a worklet that JavaScript
  would have to register, and there is none). A `@font-face` rule is allowed
  with a relative `url()` or `local()` source; a remote one is refused by the
  `url()` rule, and the CSP only allows fonts from this site anyway.
- `url(...)` in `custom.css` accepts only relative same-site paths. A remote
  image would send every visitor's IP address to a third party. For remote art
  use an Image block or the theme `background`; that is an explicit choice the
  creator makes. The page CSP keeps `img-src 'self' https:` for that reason.
- Header sources, directives and paths are matched with `fullmatch`, and any
  whitespace or control character is refused. A regex `$` also matches before
  a final newline, which would let a stray `\n` split a header or slip `/*`
  past its prohibition (`test_header_path_with_trailing_control_or_space_is_refused`).
- A missing directory stops the build with a clear message
  (`test_missing_override_directory_is_a_clear_error`).

### Residual risk

The checks cannot stop someone who can change the directory tree while a build
runs. Swapping a parent directory for a symlink between the check and the open
needs `openat2` to prevent, and this project does not use it. Keep the
overrides directory out of reach of untrusted users and processes, and do not
build from a directory that other accounts or build steps can write to. A
template with a legitimate subdirectory symlink inside the tree is read through
that link; the resolved location must still be inside the directory.

Use `reach-around serve --config site.yaml --overrides my-overrides` to see
changes live; the preview rebuilds when files in the directory change
(`test_preview_serve_applies_overrides`).
