- Cutover runbook: new section 3.5 on consolidating the old support host into
  the Support page (redirect plan, staging checks, rollback, owner approves
  each step), a "Payments and support rails" checklist entry, and matching
  parity checklist wording. The verifier uses direct GET requests without
  following redirects, starts curl with only a fixed safe path, C locale and
  empty private home, and excludes inherited proxy, curl, certificate and TLS
  key logging settings. It validates DNS label and total lengths, ASCII label
  syntax, IPv4 dotted quads, and normalized host authorities including default
  and invalid ports, parses only the final response header block, and rejects
  obsolete folded headers. It
  still checks exact `Location` values, two probe queries, an unaffected path,
  and a 200 response from `/support/`. Docs and tests, refs #888.
  Curl is selected by an absolute path independent of the caller's `PATH` and
  must resolve to a regular executable owned by root that is not group or world
  writable; every parent directory must meet the same ownership and write
  restrictions. The verifier uses `/usr/bin/bash`, resets `PATH` before other
  executable statements, and invokes security-sensitive utilities by absolute
  path. Header capture is limited to 64 KiB, with boundary tests below and
  above the limit. Host validation also rejects empty normalized hosts, control
  characters, legacy numeric and hexadecimal IPv4 spellings, and noncanonical
  dotted quads. The remaining theoretical replacement race requiring root
  access is accepted.
