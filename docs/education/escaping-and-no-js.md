<!-- SPDX-License-Identifier: Apache-2.0 -->
# Escaping, link attributes, and why the page needs no JavaScript

Reach Around turns a config file into plain HTML files. This page explains
three choices in that builder and which test proves each one. It is written
for people learning from the project.

## Autoescape: text is text, never markup

A link title in your config is just a string. If someone (or a mistake) puts
`<script>alert(1)</script>` in a title, the page must show those characters,
not run them. That attack is called cross-site scripting (XSS).

The builder uses Jinja2 with `autoescape=True`. Every value placed in a
template is converted first: `<` becomes `&lt;`, `>` becomes `&gt;`, and
quotes become `&quot;` or `&#34;`. The browser then shows the text and never
treats it as a tag. This applies to titles, subtitles, section names, the
creator name, the disclosure, and URLs placed in attributes.

The environment also sets `undefined=StrictUndefined`, so a template that
mentions a missing value fails the build instead of printing an empty string.

Proof: `test_html_in_titles_is_escaped` puts a script tag in every text field
and checks that `&lt;script&gt;` appears and no `<script` appears anywhere.

URLs have a second guard at config time: they must be `https://`, so
`javascript:` and `data:` addresses never reach a template (see the config
hardening page).

## The `rel` attribute on outbound links

Every link to another site carries `rel="noopener noreferrer"`.

- `noopener` stops the page you open from getting a handle on yours through
  `window.opener`.
- `noreferrer` stops the browser from telling the destination which page the
  visitor came from.

Links marked `affiliate: true` carry `rel="sponsored noopener noreferrer"`.
`sponsored` tells search engines the link is paid or earns a commission. A page
with at least one visible affiliate link also shows your `disclosure` text, so
visitors are told. A page with none does not show it.

Proof: `test_every_external_link_has_rel_noopener` and
`test_affiliate_links_get_rel_sponsored_noopener_and_the_disclosure_is_shown`.

## No JavaScript

The pages contain no `<script>` tag. Tabs are separate pages joined by normal
links, and sections use the built-in `<details>` and `<summary>` elements, which
open and close without any code. That means the page works in text browsers,
with scripts blocked, and on slow connections, and there is no script for an
attacker to tamper with.

Proof: `test_no_script_in_any_page` checks every page of the example site.

## Safe output and repeatable builds

The builder empties the output directory before writing, so a tab you removed
does not linger as a stale page (`test_output_directory_is_cleaned_of_stale_files`).
Because that deletes files, it refuses an output directory that is, or contains,
your config file (`test_build_refuses_an_out_dir_that_is_the_config_directory_or_a_parent`).
Building twice from the same config and date gives byte-identical files
(`test_build_is_deterministic`), so a change in the output always means a change
in the input.
