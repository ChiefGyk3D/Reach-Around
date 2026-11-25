<!-- SPDX-License-Identifier: Apache-2.0 -->
# Security policy

## Supported versions

Reach Around is alpha: every release is `0.x` and anything can change between
releases (see [CHANGELOG.md](CHANGELOG.md)). Security fixes go to the `main` branch
and into the next release. Only the latest release and `main` are supported; older
releases do not get backports.

## Reporting a vulnerability

Please report a vulnerability privately through GitHub:

1. Open the repository's main page on GitHub.
2. Select the **Security and quality** tab (the shield icon under the repository
   name; it may sit in the "..." menu).
3. Click **Report a vulnerability**.
4. Fill in the form (summary, details, proof of concept, impact) and click
   **Submit report**.

GitHub notifies the maintainer and keeps the report private between you and the
maintainer until an advisory is published.

If you do not see **Report a vulnerability**, private reporting is not switched on
for the repository at that moment. Do not post details in public. Open an issue that
says only that you have a security report and want a private channel, and wait for
the maintainer to reply.

Please do not put exploit detail, proof-of-concept code, tokens, personal data or
stolen data in a public issue, pull request, discussion or comment. A public issue
cannot be unwritten: its edit history stays visible.

## What to expect

- The maintainer reads every private report.
- You will get a reply that says whether the report is accepted as a vulnerability,
  needs more information, or is out of scope. [OWNER INPUT NEEDED: response-time
  target, if any. Until the owner sets one, no response time is promised.]
- Fixes are made on a private branch where needed, released, and then described in
  the changelog and, where it fits, a published advisory. You can be credited if you
  want to be.
- Please give the maintainer reasonable time to fix a problem before you disclose it.

## Scope

The engine in this repository, its Worker, its container image and its documentation.
Findings in a deployment's own configuration, or in third-party services it uses,
belong with that deployment's owner or that service.
