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
- `custom.css` is checked before it is used. `@import`, remote `url(...)`
  targets, `expression(`, `behavior:`, `-moz-binding` and `javascript:` are
  refused, and so is anything over 256 KiB
  (`test_custom_css_with_import_or_script_url_is_refused`). Old browsers ran
  code from some of those, and `@import` or a remote URL lets another site
  watch your visitors load the page.
- A missing directory stops the build with a clear message
  (`test_missing_override_directory_is_a_clear_error`).

Use `reach-around serve --config site.yaml --overrides my-overrides` to see
changes live; the preview rebuilds when files in the directory change
(`test_preview_serve_applies_overrides`).
