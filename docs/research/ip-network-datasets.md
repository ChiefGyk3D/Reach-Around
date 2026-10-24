# IP range datasets for the network class (issue #120)

> **Status: draft.** First-pass research by an AI agent on 2026-10-24, written for issue #120 (Plan 4 Tasks A16 and A18, open decision P-9). It has not been checked by an independent reviewer or by the maintainer. Treat every claim marked UNVERIFIED as open. Nothing here is legal advice.

All sources fetched 2026-10-24 unless noted. File sizes, line counts and headers were measured with `curl` (ranges or HEAD, a few streamed counts, nothing kept). Page text quoted "per fetch" came through a page-to-text tool and could be paraphrased in places; quotes marked "raw" were grepped from the unmodified page HTML. No dataset content is vendored in this repository. The Plan 4 file list calls this note `network-datasets.md`; this issue named it `ip-network-datasets.md`, so one of the two should be renamed when the plan is next edited.

## Summary

The Worker needs a local table that maps a visitor address to one of: residential, mobile, vpn-proxy, hosting-datacenter, tor, unknown. Cloudflare already supplies the visitor's ASN (`request.cf.asn`, availability on the maintainer's plan is still UNVERIFIED in Plan 4), so full IP-to-ASN datasets are not needed at request time. The best signal per byte is:

1. A hand-authored list of hosting ASNs (a few KB, no license problem).
2. The cloud providers' own published range files, for providers whose ranges share an ASN with non-hosting traffic (Google Cloud, Azure, Oracle). These are small (100 KB to 5 MB raw) and first-party.
3. The Tor Project bulk exit list (about 17 KB, 1,209 IPv4 lines).
4. One community VPN list, X4BNet `output/vpn` (about 11,400 prefixes, 188 KB raw), as the only broad VPN signal, accepted as incomplete.

Everything is fetched at refresh time into the owner's KV and never committed. The one hard constraint found: **the Workers Free plan limits a Cron Trigger to 10 ms of CPU** (see Platform limits). Parsing and merging a few MB of JSON cannot happen in a Free-plan scheduled handler, so on Free the refresh has to run off-Worker (the Node runner `build-netdata.mjs` in a scheduled GitHub Actions job, writing the finished blob to KV through the Cloudflare API), or the maintainer needs the Paid plan. This changes A18's default design and is the first open question.

## Platform limits (Cloudflare)

Fetched 2026-10-24.

| Limit | Free | Paid | Source |
|---|---|---|---|
| Worker size | 64 MiB uncompressed, no compressed limit stated | same | https://developers.cloudflare.com/workers/platform/limits/ |
| Memory | 128 MB | 128 MB | same |
| CPU per HTTP request | 10 ms | 5 min (default 30 s) | same |
| Cron Trigger CPU | **10 ms** | 30 s (interval under 1 h), 15 min (1 h or more) | same |
| Cron Trigger wall duration | 15 min | 15 min | same |
| Subrequests per request | 50 | 10,000 | same |
| Cron Triggers per account | 5 | 250 | same |
| KV reads per day | 100,000 | unlimited | https://developers.cloudflare.com/kv/platform/limits/ |
| KV writes per day (different keys) | 1,000 | unlimited | same |
| KV writes per key | 1 per second | 1 per second | same |
| KV max value | 25 MiB | 25 MiB | same |
| KV storage per account | 1 GB | unlimited | same |
| KV minimum `cacheTtl` | 30 s | 30 s | same |

The pricing page (https://developers.cloudflare.com/workers/platform/pricing/, per fetch) repeats the 10 ms Free CPU figure and says "Max of 15 minutes of CPU time per Cron Trigger" without a Free-specific statement; the limits page is the more specific source and is what the table uses. The plan's 0.7 MB blob is far below the KV value limit. Whether decoding a 0.7 MB `ArrayBuffer` and a 17-step binary search fit inside 10 ms of request CPU on Free is UNVERIFIED (A0 / P-10).

## Sources

For each source: URL, format, cadence, size (measured 2026-10-24), license text, redistribution inside an Apache-2.0 repository, attribution, accuracy, IPv6.

### 1. Cloud provider published ranges

None of these pages states a license or redistribution term for the range file itself (searched the documentation page and the file headers). With no stated license, copyright and terms are unclear, so the default verdict is `fetch-at-refresh`: use at runtime for the owner's own deployment, do not commit. Address lists of this kind may carry little copyright, but that is a legal question for the maintainer, not a finding.

**AWS.** https://ip-ranges.amazonaws.com/ip-ranges.json. JSON with `prefixes` (IPv4) and `ipv6_prefixes`; each entry has `ip_prefix`, `region`, `service`, `network_border_group`. Size 2,735,527 bytes; 10,682 IPv4 and 7,005 IPv6 prefixes on 2026-10-24; file stamped `createDate 2026-10-24-17-37-05`, changes several times a day (UNVERIFIED cadence; the page says only "check the publication time", plus an SNS notification topic). Services seen include `AMAZON`, `EC2`, `S3`, `CLOUDFRONT`, `ROUTE53_HEALTHCHECKS`. Page text (per fetch, https://docs.aws.amazon.com/vpc/latest/userguide/aws-ip-ranges.html): "We publish the IP address ranges for services that customers commonly use to perform egress filtering. We don't publish the IP address ranges for all services." and BYOIP ranges are not included. Use only `service == "EC2"` (plus possibly `AMAZON` minus CloudFront) for the hosting class; `AMAZON` is a superset that includes CDN and service endpoints. IPv6 yes. License: none stated. Verdict `fetch-at-refresh`. Because AWS's main ASN is already in the hand-authored ASN list, the range file adds little (see Accuracy).

**Google Cloud.** https://www.gstatic.com/ipranges/cloud.json (customer-usable ranges, per region; 116,077 bytes, 1,040 IPv4 and 95 IPv6 prefix lines). Companion `goog.json` (6,314 bytes) lists all Google-owned ranges, which includes non-Cloud services, so it is the wrong file for hosting. Per fetch of the FAQ (https://docs.cloud.google.com/compute/docs/faq): the page offers `cloud.json` and notes it "includes additional information, such as the region to which a regional external IP address is attached". Update cadence not stated; `Last-Modified` was the same day. License: only the page-level footer, "Except as otherwise noted, the content of this page is licensed under the Creative Commons Attribution 4.0 License", which covers the page, not the JSON (per fetch). Verdict `fetch-at-refresh`. IPv6 yes.

**Microsoft Azure.** Service Tags JSON, weekly, from the download page https://www.microsoft.com/en-us/download/details.aspx?id=56519 (per fetch: version 2026.10.19, published 10/20/2026, 4.6 MB; "This file is updated weekly" and "Please download the new json file every week"). The file name contains a date (`ServiceTags_Public_20261019.json`) and the download URL contains a GUID path (`download.microsoft.com/download/7/1/d/<guid>/ServiceTags_...json`, raw HTML). **The URL changes every week and must be scraped from the download page, so it cannot be a fixed string**; this conflicts with A18's "fixed https URL constants" rule. Microsoft also documents a Service Tag Discovery API (UNVERIFIED, not fetched) that needs Azure credentials, which a self-hoster may not have. Size 4,870,615 bytes (HEAD of the 2026-10-19 file). The page text says it "currently includes only IPv4 ranges" per the tool summary, but a ranged read of the file itself showed IPv6 entries (`2603:1030:...`), so IPv6 is present; trust the file. License: the page "doesn't include any terms of use or license text" (per fetch; the footer links a generic Terms of use that was not read). Verdict `fetch-at-refresh`; needs a scrape step, flagged as a supply-chain risk (T-24). The Azure file also contains service tags for Microsoft 365-style endpoints in some tags (UNVERIFIED), so choose tags carefully (compute-oriented tags only, e.g. region tags).

**Oracle Cloud.** https://docs.oracle.com/iaas/tools/public_ip_ranges.json (redirects to `/en-us/iaas/...`; 234,112 bytes; `last_updated_timestamp 2026-08-25`). JSON: `regions[].cidrs[].{cidr,tags}`, tags `OCI`, `OSN`, `OBJECT_STORAGE`. Per fetch of https://docs.oracle.com/en-us/iaas/Content/General/Concepts/addressranges.htm: "You can poll the published file to check for new IP address ranges as frequently as every 24 hours. We recommend that you poll the published file at least weekly." Documented fields are IPv4 only ("One or more IPv4 IP addresses"). License: none on the page, footer "Copyright (c) 2026, Oracle and/or its affiliates". Verdict `fetch-at-refresh`. IPv6 no (as documented).

**Cloudflare.** https://www.cloudflare.com/ips-v4 and https://www.cloudflare.com/ips-v6 (plain CIDR lists; ips-v4 is 230 bytes, 15 lines). These are Cloudflare's own proxy ranges. Visitors do not reach the Worker from them, so this list is not useful for classifying visitors. It matters only to a Worker that fetches from other origins. The page shows "Last updated: Sep 28, 2023" (per fetch) and states no license or schedule. Verdict: not needed.

**DigitalOcean.** https://www.digitalocean.com/geo/google.csv (a geofeed, RFC 8805 style: `prefix,country,region,city,postal`; 1,235 lines, 53,163 bytes; the apex host redirects with a 301 to `www`). Last-Modified 2026-10-23. Terms not located (UNVERIFIED). IPv6 not checked (UNVERIFIED). Verdict `fetch-at-refresh`. The geofeed covers addresses DigitalOcean geolocates, which matches its hosting ranges, but it is a geolocation feed, not a statement that every address is a droplet.

**Linode / Akamai.** https://geoip.linode.com/ (RFC 8805 geofeed, header comment "This file contains a self-published geofeed as defined in https://datatracker.ietf.org/doc/html/rfc8805"; 192,744 bytes, 5,508 lines, mixed IPv4 and IPv6, Last-Modified 2026-10-24). Terms not located (UNVERIFIED). Verdict `fetch-at-refresh`.

**Fastly.** https://api.fastly.com/public-ip-list (JSON `addresses` and `ipv6_addresses`, 451 bytes). These are edge ranges (a CDN), not visitor-side hosting. Not needed.

**Other providers.** Many hosts (Hetzner, OVH, Vultr, Scaleway, etc.) publish no file; they are covered by the hand-authored ASN list or by X4BNet's `datacenter` output. Not researched individually (UNVERIFIED whether any publish a file).

### 2. Tor

**Bulk exit list.** https://check.torproject.org/torbulkexitlist. Plain text, one IPv4 per line, no IPv6 (0 of 1,209 lines contain a colon). 17,120 bytes; `Last-Modified` 2026-10-24 19:46 UTC, served with an ETag and range support (so `If-None-Match` works). Cadence not stated (UNVERIFIED; the header suggests hourly or better). Terms: the Tor blog post "changes to the Tor exit list service" (https://blog.torproject.org/changes-tor-exit-list-service/, per fetch) says the bulk list "still provides a bulk list of IP addresses, with a simplified interface", that exit-policy filtering is no longer supported, and that any exit IP observed through active measurement is included "regardless of the query made". It says nothing about rate limits or a license for the list. Treat the list as a Tor Project service output with no stated license (UNVERIFIED). Verdict `fetch-at-refresh`, once per refresh from one fixed URL.

**Onionoo.** https://metrics.torproject.org/onionoo.html. Per raw HTML the footer reads that data "is freely available under a CC0 no copyright declaration" (link https://creativecommons.org/publicdomain/zero/1.0/); per fetch: "To the extent possible under law, the Tor Project has waived all copyright and related or neighboring rights in the data." Details documents list `exit_addresses` as an array of IPv4 addresses used to exit "in the past 24 hours". Clients should use `If-Modified-Since` and `Accept-Encoding: gzip`; no rate limit stated. This is the only Tor-side source with an explicit license. It is heavier than the bulk list (a full details query is large; a filtered query with `flag=Exit` and `fields=exit_addresses` would be small, UNVERIFIED, not run). Verdict: CC0, so `vendor` is permitted by the license text for Onionoo-derived data, but a Tor list goes stale in hours, so vendoring has no value. Recommended: `fetch-at-refresh` from the bulk list, with Onionoo as the documented fallback.

**Tor via firehol.** `tor_exits.ipset` and `dm_tor`, `et_tor` in firehol/blocklist-ipsets (below) are re-publications; no reason to prefer them over the first-party list.

### 3. Community VPN and datacenter lists

**X4BNet/lists_vpn.** https://github.com/X4BNet/lists_vpn. Raw URLs:
`https://raw.githubusercontent.com/X4BNet/lists_vpn/main/output/vpn/ipv4.txt` (179,405 bytes, 10,931 lines), `.../output/vpn/ipv6.txt` (8,708 bytes, 498 lines), `.../output/datacenter/ipv4.txt` (697,495 bytes, 44,584 lines), `.../output/datacenter/ipv6.txt` (159,652 bytes, 8,752 lines). Plain CIDR lines. The repo was pushed on 2026-10-24 (automated by GitHub Actions per its README; cadence UNVERIFIED, "regularly"). The old top-level `ipv4.txt` is described as "a legacy path ... (to be removed in 2026, use the above)", so use only the `output/` paths. No `LICENSE` file exists (404; the GitHub API reports license `null`), but the README's License section says: "Software in the below license corresponds to the scripts, automation, and the list itself (source files and generated output)." followed by an MIT License text, "Copyright (c) 2024 X4B (Mathew Heard)" (README, decoded from the GitHub contents API, https://github.com/X4BNet/lists_vpn/blob/main/README.md). MIT permits redistribution with the copyright and permission notice kept in all copies. README on coverage: "This list doesn't list _all_ VPNs, but should list the vast majority of common ones", "this list is strictly just known VPN networks. A small overlap with Datacenter networks is possible", and the `datacenter` list is "for VPNs and Datacenters. Anything that is 'not an eyeball network' directly", built from known VPN netblocks plus ASNs owned by datacenters and VPN providers. Verdict: the stated MIT covers the list, so `vendor` is technically allowed with the MIT notice kept; recommend `fetch-at-refresh` anyway because the lists are mutable, the underlying facts are scraped from provider data that the README does not source (UNVERIFIED provenance), and a vendored copy would go stale in weeks. Use `output/vpn` for the vpn-proxy class and optionally `output/datacenter` for hosting. IPv6 yes.

**firehol/blocklist-ipsets.** https://github.com/firehol/blocklist-ipsets. Aggregates many third-party lists as `.ipset` (single IPs) and `.netset` (CIDR). No repo license (API: `null`). Per fetch: "a few of these lists may have special licences attached"; each list belongs to its upstream source; a bundled disclaimer says "I don't give any guarantees of accuracy, completeness or even usability!" and warns about poisoning and false positives. Relevant lists: `tor_exits` (18,034 bytes, 1,248 lines), `firehol_proxies` ("all sources that track open proxies"), `firehol_anonymous`, `ip2proxy_px1lite` ("IP addresses which are used as public proxies"; sourced from a commercial vendor's LITE product, whose terms were not read, UNVERIFIED). Verdict `exclude` for vendoring (mixed unknown licenses); for refresh-time use, only the individual lists whose upstream license has been read. Open-proxy lists are short-lived by nature and the maintainer's own warning is not to block permanently. Not in the recommended set.

**client9/ipcat.** https://github.com/client9/ipcat. License MIT per the GitHub API, but the repository is **archived** (last push 2023-02-02): stale by about 3.7 years. Verdict `exclude` (stale).

**Other "vpn ip list" repos.** `ejrv/VPNs` (named in X4BNet's credits as its inspiration, manually curated), `tn3w/IPSet`, `josephrocca/is-vpn`: not fetched (UNVERIFIED existence, license and freshness). Not in scope until the maintainer names one.

**Apple iCloud Private Relay egress list.** https://mask-api.icloud.com/egress-ip-ranges.csv (rows like `172.224.226.0/27,GB,GB-EN,London,`; **12,047,791 bytes**). Linked from Apple's page https://developer.apple.com/support/prepare-your-network-for-icloud-private-relay/ ("access our latest set of IP addresses and locations directly", per fetch; no file-specific terms or cadence). It is a real VPN-like signal (Private Relay is a proxy service) but at 12 MB it exceeds A18's 8 MiB response cap and costs a lot of CPU to parse. Verdict `fetch-at-refresh` only if the maintainer raises the cap and runs the builder off-Worker; otherwise omit.

### 4. IP-to-ASN and ASN-to-organization datasets

Because `request.cf.asn` already identifies the visitor's network, these matter mainly to author the hand-written hosting and mobile ASN lists once (an offline step), not as a Worker runtime table. A runtime IP-to-ASN table would be large (millions of rows) and would duplicate what Cloudflare provides.

| Dataset | URL | Format and size | Cadence | License (quoted) | Verdict |
|---|---|---|---|---|---|
| iptoasn.com | https://iptoasn.com/ (`data/ip2asn-v4.tsv.gz` 7,013,300 B; `ip2asn-v6.tsv.gz` 2,015,648 B; `ip2asn-combined.tsv.gz` 9,028,778 B) | TSV `range_start range_end AS_number country_code AS_description`; IPv4 and IPv6 | "Updated hourly." (raw) | "Licensed under Public Domain (PDDL v1.0)" with link https://opendatacommons.org/licenses/pddl/1.0/ (raw) | PDDL permits redistribution without attribution; `vendor`-permitted, but 9 MB gz (tens of MB raw, UNVERIFIED) is too large to commit and unnecessary. Use offline to derive ASN lists. |
| RIR delegated files | NRO combined: https://ftp.ripe.net/pub/stats/ripencc/nro-stats/latest/nro-delegated-stats (57,039,771 B); five RIR FTP trees listed at https://www.nro.net/about/rirs/statistics/ | Pipe-delimited, allocations by country, not ASN-to-prefix routing data | Daily per NRO page | None stated on the NRO page (footer "(c) 2026 The Number Resource Organization", per fetch); ARIN's statistics page states none either | `exclude` for this use: 57 MB and carries registration, not routing or hosting, information. License UNVERIFIED. |
| RouteViews | https://www.routeviews.org/routeviews/, archive https://archive.routeviews.org/ | MRT RIB dumps (large) | Continuous | Per fetch: licensed "under a Creative Commons Attribution 4.0 International License"; attribution to RouteViews required; "Commercial Terms of Use apply when selling services, products, reports, or other derivative works" | Raw BGP data is too heavy; not recommended. CC BY 4.0 is compatible with redistribution with attribution, but this is not what the Worker needs. |
| CAIDA RouteViews prefix2as | https://www.caida.org/catalog/datasets/routeviews-prefix2as/ | Tab lines: prefix, length, AS | Daily | The page names no license; points to the "CAIDA Acceptable Use Agreement (AUA) for Publicly Accessible Datasets" (not read); asks users to cite the dataset and report publications (per fetch) | `exclude` (AUA unread, reporting duty, cannot assume redistribution). |
| RIPE RIS | https://www.ripe.net/ (documentation page not found; the URL tried returned 404) | MRT dumps | n/a | UNVERIFIED (page not reached) | Not evaluated; same size objection as RouteViews. |
| MaxMind GeoLite2 ASN | https://dev.maxmind.com/geoip/geolite2-free-geolocation-data/ , EULA https://www.maxmind.com/en/geolite2/eula | `.mmdb` or CSV | Not stated on the page that was read (sapics lists "Twice weekly", second hand) | EULA, per fetch: "a non-exclusive, non-transferable limited license to access and use the Services for your own internal business purposes"; "you will not disclose the Services to any third party without notifying MaxMind of the anticipated disclosure"; "You shall cease use of and destroy ... any old versions of the Services within thirty (30) days following the release"; "You must provide attribution of your use to MaxMind". Free account and license key required; "limited to 30 database downloads per day" | `exclude` by Plan 4 policy (registration plus EULA). It cannot be redistributed in an Apache-2.0 repository and its 30-day destruction clause conflicts with a long-lived committed file. |
| DB-IP Lite (ASN Lite) | https://db-ip.com/db/lite.php | CSV and MMDB | Monthly | Raw: "Creative Commons Attribution 4.0 International License" (http://creativecommons.org/licenses/by/4.0/); "provided you give attribution to DB-IP.com for the data"; web pages should link back (suggested snippet `IP Geolocation by DB-IP`) | CC BY 4.0 allows redistribution with attribution, which is compatible with Apache-2.0 if the attribution is carried (NOTICE). The attribution would have to show in a user-facing place, which a privacy-focused tool may not want. Monthly cadence and Lite accuracy ("reduced coverage and accuracy", raw). Not needed given `cf.asn`. |
| IPinfo Lite | https://ipinfo.io/lite | JSON, MMDB, CSV or Parquet; country and ASN fields | "Data updates every 24 hours" | Per fetch: "Released under Creative Commons Attribution-ShareAlike 4.0 International License", attribution to IPinfo required | `exclude`: share-alike (BY-SA) conflicts with the policy for datasets that cannot combine with Apache-2.0 (Plan 4 step 2). Whether downloads need a token is not stated (UNVERIFIED). |
| sapics/ip-location-db | https://github.com/sapics/ip-location-db | Aggregator of the above in CSV/MMDB | Daily or per source | Per fetch: "The required licence depends upon the database which you use"; iptoasn-asn and origin-asn PDDL, DB-IP datasets CC BY 4.0, GeoLite2 under "GeoLite2 License by MaxMind"; "You can use PDDL ... licensed databases without attribution for any purpose." The repo has no license of its own (API `null`) | Convenience mirror only; check each dataset. Not required. |
| ipverse/asn-ip | https://github.com/ipverse/asn-ip | Per-AS prefix files (`as/<n>/ipv4-aggregated.txt`) for ASes announcing in the last 90 days | Daily | "This data is released under CC0 1.0 Universal." (per fetch; LICENSE reports `CC0-1.0`) | CC0, so redistribution is clearly permitted. Handy to resolve "the prefixes of these hosting ASNs" at build time. Source of the data not named on the page (UNVERIFIED). Prefixes are merged, so they may not match exact announcements. |

IPv6: iptoasn, DB-IP (not stated), IPinfo, GeoLite2 and ipverse all include IPv6; the RIR files include IPv6 allocations.

## Comparison

Verdicts follow Plan 4 step 2: `vendor` needs a quoted license line permitting it, which only X4BNet (MIT), ipverse (CC0), iptoasn (PDDL), Onionoo (CC0) and DB-IP (CC BY 4.0) have; none is recommended for vendoring because each is data that goes stale. Everything else is `fetch-at-refresh` or `exclude`.

| Source | Class signal | Raw size | v6 | License stated | Verdict | In minimal set |
|---|---|---|---|---|---|---|
| Hand-authored ASN lists | hosting, mobile | a few KB | n/a | ours | in-repo | yes |
| Tor bulk exit list | tor | 17 KB | no | none stated | fetch-at-refresh | yes |
| Google Cloud `cloud.json` | hosting | 116 KB | yes | none for file | fetch-at-refresh | yes |
| Azure Service Tags | hosting | 4.9 MB | yes | none on page | fetch-at-refresh (URL scrape) | yes, region tags only |
| Oracle `public_ip_ranges.json` | hosting | 234 KB | no | none on page | fetch-at-refresh | yes |
| AWS `ip-ranges.json` | hosting | 2.7 MB | yes | none | fetch-at-refresh | optional (ASN covers most) |
| DigitalOcean geofeed | hosting | 53 KB | UNVERIFIED | UNVERIFIED | fetch-at-refresh | optional |
| Linode geofeed | hosting | 193 KB | yes | UNVERIFIED | fetch-at-refresh | optional |
| X4BNet `output/vpn` | vpn-proxy | 188 KB | yes | MIT (README) | fetch-at-refresh (vendor allowed) | yes |
| X4BNet `output/datacenter` | hosting | 857 KB | yes | MIT (README) | fetch-at-refresh | optional |
| Apple Private Relay egress | vpn-proxy | 12 MB | yes | none | fetch-at-refresh, off-Worker only | no (exceeds 8 MiB cap) |
| firehol lists | proxy, tor | varies | varies | mixed, none repo-wide | exclude | no |
| ipcat | hosting | n/a | n/a | MIT, archived | exclude (stale) | no |
| iptoasn, ipverse | ASN lookup | MBs | yes | PDDL, CC0 | offline use only | no |
| GeoLite2 | ASN | n/a | yes | EULA | exclude | no |
| IPinfo Lite | ASN | n/a | yes | CC BY-SA 4.0 | exclude | no |
| DB-IP Lite | ASN | n/a | UNVERIFIED | CC BY 4.0 | not needed | no |
| RIR files, RouteViews, CAIDA | ASN | 57 MB and up | yes | none / AUA / CC BY 4.0 | exclude | no |

## Recommended minimal set

Cost estimate for the minimal set after parsing into the planned `net/v1` blob: about 16,000 to 25,000 IPv4 ranges plus a few thousand IPv6, well under the plan's 0.7 MB estimate (UNVERIFIED, not built). Raw download per refresh about 5.5 MB, dominated by Azure.

1. Hand-authored `hosting_asns.txt` and `mobile_asns.txt` (already in A18). Hosting detection through the visitor's ASN is free per request and costs no blob space.
2. Ranges only where an ASN is shared with non-hosting traffic. This is the author's inference, not a verified fact: the large clouds' customer ranges sit inside ASNs that also carry the provider's consumer or corporate traffic (for example Google and Microsoft), so an ASN rule would over-flag; the range files draw the line. Use Google Cloud `cloud.json`, Azure region tags, Oracle.
3. Tor bulk exit list.
4. X4BNet `output/vpn` for the vpn-proxy class.

Not recommended: any ASN database at runtime, GeoLite2, IPinfo Lite, firehol aggregates, the Private Relay list.

On the Worker Free plan the 10 ms Cron CPU limit rules out parsing in the Worker; run the builder in CI and upload with a narrowly scoped Cloudflare API token (least privilege: KV write for one namespace) or take the Paid plan. Both are the maintainer's call.

## Recommended refresh design

- Schedule: weekly (plan has `41 4 * * 1`). Tor changes faster than weekly; a second, daily Tor-only fetch is cheap (17 KB, ETag supported) if exit churn matters, but each extra write counts against the 1,000 KV writes per day (trivial).
- Fixed URLs only, https, no redirects followed to another host (the DigitalOcean apex 301, the Oracle 302 and the Google crawler 301 seen here all redirect, so store the final URL, not the redirecting one). Azure's weekly URL is the exception: scrape the download page for one `https://download.microsoft.com/download/...ServiceTags_Public_<date>.json` match, require the host and file-name pattern, otherwise skip and keep the last good stage.
- Validate each response before use: status 200, size under the cap (8 MiB, so Azure at 4.9 MB passes and Private Relay at 12 MB fails), content type or first-byte check, parseable, at least the minimum range count. Suggested count windows from this fetch (the window is loose, since sizes drift): AWS 10,000 to 30,000 total prefixes; Google Cloud `cloud.json` 500 to 3,000; Oracle 300 to 3,000 CIDRs (UNVERIFIED count, not measured); Tor bulk 300 to 5,000 lines; X4BNet vpn IPv4 3,000 to 40,000; X4BNet datacenter IPv4 15,000 to 150,000.
- Sanity rules per A18: no prefix shorter than /8 (IPv4) or /19 (IPv6), no private or reserved space, no source above a quarter of IPv4, non-CIDR lines skipped and counted.
- Merge into the single compact structure with class priority tor > crawler > vpn-proxy > hosting; write `net/v1` only after the merge validates; keep each source's previous stage on failure.
- Fail-safe: any error returns `unknown`, never blocks the request, never throws.
- Record the source name, fetch time and counts in `view_meta` (no visitor data).

## Legal and privacy notes for the maintainer (not legal advice)

1. **Visitor IPs never leave the Worker.** All of the above are downloads of public lists; no lookup service is called per visitor. This is the reason local range data was chosen and is what the privacy page can say.
2. **Range data is not personal data about the visitor, but the result is an inference about them.** Storing only a coarse class ("hosting", "vpn-proxy") keeps within the maintainer's decision; do not store the matched range, source or ASN number per view if you want the class to stay coarse.
3. **Licenses on cloud range files are unstated.** A published range file may still carry terms in a site-wide agreement (AWS Customer Agreement and Site Terms, Microsoft and Oracle terms of use, none read). Fetch-at-refresh by the owner for the owner's own deployment is the lowest-risk reading, not a legal conclusion.
4. **MIT (X4BNet)** requires the notice in all copies. If vendored, or if a built blob is redistributed, carry "Copyright (c) 2024 X4B (Mathew Heard)" and the permission notice. A blob that lives only in the owner's KV is not redistributed.
5. **Attribution licenses.** DB-IP (CC BY 4.0) and RouteViews (CC BY 4.0) require visible credit; IPinfo (CC BY-SA 4.0) adds share-alike, which would pull a combined derived dataset toward the same license. Avoid unless the maintainer accepts that.
6. **GeoLite2** terms bar disclosure to third parties without MaxMind's notice or consent and require deleting old versions within 30 days; do not commit it. Section 5 (quoted in the table) also bars relying on it to identify a specific household or individual.
7. **Not a security control.** VPN and proxy lists are incomplete by their own description. Mobile carriers and corporate gateways put many real people behind shared addresses (carrier-grade NAT), and corporate or cloud-desktop egress can look like hosting. Label results "likely" in the UI and docs, and never use the class to block anyone.
8. **Tor is not abuse.** Firehol's README says "The TOR network is not necessarily bad and you should not block it if you want to allow your users be anonymous" (per fetch). The Tor Project's own "Banning Tor" support page was linked but not read (UNVERIFIED). Here the class is only recorded, never used to block.
9. **GitHub raw is a third-party host.** Fetching lists from `raw.githubusercontent.com` trusts the maintainer of that repo; a compromised list can mislabel traffic (T-24). The sanity rules and count windows are the control.
10. **Do not scrape provider pages without reading their robots rules and terms**; the Azure download-page scrape in particular is a convenience, not a published API.

## Accuracy and false-positive caveats

- VPN lists lag new providers and rotate addresses; X4BNet itself says it lists "the vast majority of common ones", not all. Expect misses, and expect residential-proxy networks (which use ordinary home addresses) to be undetectable by any list here.
- Cloud ranges include more than customer VMs: AWS `AMAZON` includes CloudFront, S3 and service endpoints (use `EC2`); Google `goog.json` includes consumer services; Azure tags include platform endpoints. A person on a corporate VPN or cloud desktop, or using a cloud-based browser or preview service, will look like hosting.
- Mobile carriers share addresses (CGNAT), so a mobile class is about the network, not the person.
- Apple Private Relay and similar services are proxies by design; the same address is used by many people.
- Staleness: Azure weekly, Oracle file dated 2026-08-25 (two months old at fetch time, so the file is not refreshed on every poll), X4BNet automated, AWS several times a day, Tor hourly or better (UNVERIFIED).
- Geofeeds (DigitalOcean, Linode) describe geolocation, not usage.

## Unverified items

- Cloudflare `request.cf.asn` availability on the maintainer's plan (carried from Plan 4).
- Whether a 0.7 MB blob decode and lookup fits 10 ms request CPU on Free.
- Terms of use pages for AWS, Microsoft, Oracle, DigitalOcean, Linode, Tor check service, Apple's range file, and the CAIDA AUA were not read.
- Update cadence for Tor bulk list, Google, DigitalOcean, Linode, X4BNet, GeoLite2.
- IPv6 content in the DigitalOcean geofeed.
- Provenance and accuracy of X4BNet's netblocks.
- Existence, license and freshness of ejrv/VPNs, tn3w/IPSet, josephrocca/is-vpn.
- RIPE RIS terms (page not reached).
- Whether Onionoo `flag=Exit&fields=exit_addresses` is small enough; not run.
- Whether Azure's Service Tag Discovery API is usable without Azure credentials.
- Provider page quotes marked "per fetch" were produced by a text-extraction tool and could be paraphrased; the raw-quoted ones (iptoasn, DB-IP, Onionoo CC0, X4BNet README) are exact.

## Sources

- https://ip-ranges.amazonaws.com/ip-ranges.json (2026-10-24)
- https://docs.aws.amazon.com/vpc/latest/userguide/aws-ip-ranges.html (2026-10-24)
- https://www.gstatic.com/ipranges/cloud.json and goog.json (2026-10-24)
- https://docs.cloud.google.com/compute/docs/faq (2026-10-24)
- https://www.microsoft.com/en-us/download/details.aspx?id=56519 (2026-10-24)
- https://docs.oracle.com/iaas/tools/public_ip_ranges.json and https://docs.oracle.com/en-us/iaas/Content/General/Concepts/addressranges.htm (2026-10-24)
- https://www.cloudflare.com/ips/ , /ips-v4 , /ips-v6 (2026-10-24)
- https://www.digitalocean.com/geo/google.csv , https://geoip.linode.com/ , https://api.fastly.com/public-ip-list (2026-10-24)
- https://check.torproject.org/torbulkexitlist , https://blog.torproject.org/changes-tor-exit-list-service/ , https://metrics.torproject.org/onionoo.html (2026-10-24)
- https://github.com/X4BNet/lists_vpn (README and API metadata, 2026-10-24)
- https://github.com/firehol/blocklist-ipsets , https://github.com/client9/ipcat , https://github.com/sapics/ip-location-db , https://github.com/ipverse/asn-ip (2026-10-24)
- https://mask-api.icloud.com/egress-ip-ranges.csv , https://developer.apple.com/support/prepare-your-network-for-icloud-private-relay/ (2026-10-24)
- https://iptoasn.com/ , https://db-ip.com/db/lite.php , https://ipinfo.io/lite (2026-10-24)
- https://www.maxmind.com/en/geolite2/eula , https://dev.maxmind.com/geoip/geolite2-free-geolocation-data/ (2026-10-24)
- https://www.nro.net/about/rirs/statistics/ , https://www.arin.net/reference/research/statistics/ , https://www.caida.org/catalog/datasets/routeviews-prefix2as/ , https://www.routeviews.org/routeviews/ (2026-10-24)
- https://developers.cloudflare.com/workers/platform/limits/ , /pricing/ , https://developers.cloudflare.com/kv/platform/limits/ (2026-10-24)
- Crawler range files (not evaluated in depth; recorded for P-9): Google https://developers.google.com/static/crawling/ipranges/common-crawlers.json (21,768 bytes; the old `search/apis/ipranges/googlebot.json` 301-redirects here), `special-crawlers.json` (19,109), `user-triggered-fetchers.json` (72,503); Bing https://www.bing.com/toolbox/bingbot.json (1,580); Apple https://search.developer.apple.com/applebot.json (1,640). DuckDuckGo's file was not located. Licenses not read.

## Open questions for the maintainer

1. Which Cloudflare plan runs the Worker? On Free, the 10 ms Cron CPU limit forces the off-Worker builder (GitHub Actions plus a scoped API token) as the default. Is a CI-held Cloudflare token acceptable, or should the plan be Paid?
2. Enable X4BNet `output/vpn` (MIT per its README, incomplete, third-party-hosted)? Enable `output/datacenter` as well, or rely on the ASN list for hosting?
3. Which cloud sources are in: Google Cloud, Azure, Oracle, AWS, DigitalOcean, Linode? Is a scrape of the Azure download page acceptable, given A18's fixed-URL rule?
4. Do you want a daily Tor-only fetch, or weekly with everything?
5. Do you want the crawler-range verification at all, and if so, are the Google, Bing and Apple files enough?
6. Is the Private Relay list (12 MB) worth a raised cap, or is the VPN class allowed to ignore it?
7. Is the DB-IP CC BY 4.0 attribution acceptable if it were ever used, or does the privacy-first stance rule out any attribution-required source?
8. Will you read the unread terms (AWS, Microsoft, Oracle, Tor check service, CAIDA AUA) before anything is enabled?
9. Rename this note to `network-datasets.md` to match the plan, or update the plan?
