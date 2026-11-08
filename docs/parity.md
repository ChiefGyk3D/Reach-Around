<!-- SPDX-License-Identifier: Apache-2.0 -->
# Link parity check

`reach-around parity` compares the links in a built site with a link list you exported from the old page. It is the scripted link check in the cutover plan (issue #340, epic #246; see `docs/research/beacons-parity.md`).

```
reach-around build --config site.yaml --out out
reach-around parity --site out --expected old-links.csv
```

It makes no network call and needs no token. It reads local files only and prints counts and ids, never a URL or a label, so the output is safe to paste into a pull request.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Parity: nothing differs. |
| 1 | Differences found. The output says how many of each kind. |
| 2 | Cannot tell: the export, the redirect map or the site could not be read, or the built pages hold no anchors at all (the parser would be stale). A message on stderr says which input and which row, never the value. |

## The export (`--expected`)

The old provider's export is yours and stays out of the repository. Save the list as a `.csv` or `.json` file with one row per link:

CSV: a header row of exactly `url,label`, then one row per link.

```
url,label
https://example.com/shop,Shop
https://example.org/,Home page
```

JSON: either a list of objects, or an object with a `links` list.

```json
{"links": [{"url": "https://example.com/shop", "label": "Shop"}]}
```

Rules, all enforced:

- `url` must be an `http` or `https` URL with a host. `label` must be non-empty text. Anything else is exit 2, naming the row.
- At most 10,000 rows and 5 MiB. UTF-8 only. A JSON object that repeats a key is refused. A symlink is refused.
- Row order is the order on the old page; list it that way if you want order checked.

## What is compared

The scope is a fixed list in `src/reach_around/linkparity.py`:

- the header icon row (`a.social`, label from its screen-reader text),
- link cards and the links inside sections (`a.link`, label from the title, not the subtitle),
- the profile card button (`a.profile-button`).

The header icon row and the profile card repeat on every tab, so they are read from the root page once. If a later tab's icon row or button differs from the root page's, that is reported as "icon row differs on N pages". Link cards are read from every page, in page order (root first, then by path).

Only tab pages are read (a page with a link list or the empty-tab note). The privacy page, the media kit pages and any other page are skipped, so they cannot cause a false icon-row difference. Internal icon targets are compared by where they resolve, not by their page-relative text. If no tab page is found, the result is exit 2.

Ignored on purpose: the skip link, the tab bar, the footer links, the affiliate disclosure and any other Reach Around-only anchor. Anchors whose target is not an `http(s)` URL (internal `tab:` targets) are counted as "internal, not compared" so you can see them, but they are not matched against the export.

Before comparing, URLs are normalized: scheme and host lower-cased, the default port and the fragment dropped, an empty path read as `/`, and tracking parameters (`utm_*`, `fbclid`, `gclid`, `dclid`, `msclkid`, `igshid`, `mc_cid`, `mc_eid`) removed. Labels are compared ignoring case and extra whitespace.

## Differences it names

Each kind is a count plus the ids involved (the link's `data-link-id` when the build has click tracking on, else `page:kind-position`; `export#N` for an export row).

| Line | Meaning |
|---|---|
| missing | In the export, nowhere on the site. |
| extra | On the site, nowhere in the export. |
| changed label | Same target, different label. |
| changed target | Same label, different target. |
| redirect only | The targets differ, but your redirect map links them (see below). Counts as a difference unless `--accept-redirects`. |
| duplicate in site / in export | The same link appears more times on one side than the other. Equal numbers of copies on both sides are parity. |
| reordered | Links that match but are out of order (`--ignore-order` turns this off). |

Matching goes in this order: same target and label, then same target, then a known redirect with the same label, then same label. What is left is missing, extra or a duplicate.

## Redirects (`--redirects`)

The script never follows a redirect. If you know the old page used a URL that now redirects to the new one (or the reverse), list it in a `.csv` (`from,to`) or `.json` (a list of `{"from": ..., "to": ...}`). Either direction matches. Then a link whose only difference is that redirect is reported as "redirect only", and `--accept-redirects` accepts it.

## Not covered

- Whether a link works. The plan's second step, requesting each external URL once, is a separate manual or scripted check; this tool does not do it.
- Affiliate flags and `rel` values: the plan checks those by their own scripted step.
- Order of tabs and tab titles.
- Analytics numbers. `scripts/check-analytics.py` compares daily views and clicks with the old provider's export; see `docs/analytics-acceptance.md`.
