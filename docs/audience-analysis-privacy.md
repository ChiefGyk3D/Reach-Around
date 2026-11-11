# Audience analysis: what Reach Around does not collect

Reach Around analyzes aggregate audiences and the destinations visitors choose
without building visitor profiles. These limits apply to every analytics table,
log, cache, queue and export:

- No raw or partial IP address.
- No user-agent string, TLS or HTTP fingerprint, or other fingerprinting signal.
- No cross-site or third-party identifier. A hop carries only a closed source label.
- No cookie, `localStorage`, `sessionStorage` or `IndexedDB` value is used to recognize a visitor.
- No analytics row is joined to an individual, including an email, account, handle, lead or donation.
- No precise location. Clicks use country only, never coordinates, postal code, ASN or organization.
- No full referrer URL or query string.
- No per-visitor network-classification detail.

Reports apply the existing privacy floor and complementary suppression to every
matrix. Printed cells, row totals and column totals are calculated from the
same safe-to-release cells, so their margins do not reveal a suppressed value.
Source by destination, country by destination, device by destination and hour
by weekday all share this rule.

The Worker may read request fields transiently to enforce limits and derive
closed aggregate categories. It drops the original values before storage and
does not write them to logs.
