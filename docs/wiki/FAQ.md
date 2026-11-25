<!-- SPDX-License-Identifier: Apache-2.0 -->
# FAQ

Short, honest answers. Where the repository cannot back an answer, the page says so with the marker [OWNER INPUT NEEDED] and does not guess. Nothing here is legal advice.

## What is Reach Around?

An open-source (Apache-2.0) tool that builds a creator's link page, media kit and platform numbers from one config file, meant to be self-hosted with your own API keys (README). The visitor gets a static page; one small Cloudflare Worker handles the media kit gate, event collection, retention and optional features (README, "What it is meant to do").

It is alpha. Every release is `0.x`, there is no stable config format and no upgrade path between releases (changelog header). See the [Roadmap](Roadmap.md) for what exists and what does not.

## How is it different from a link-in-bio service?

In general terms, a hosted link-in-bio service runs the page, the editor, the analytics and the billing for you. Reach Around is the other end of that trade:

- Your page is built from a config file that lives in your own repository ([instance-repos.md](../instance-repos.md)), and you deploy it to a host you control.
- The page is static. Click and view counting go to a Worker you deploy to your own Cloudflare account, and the privacy notes list cookies as not collected (README; `docs/privacy.md`).
- The numbers come from your own API keys and are kept in a history file in your repository (#383).
- There is no account to sign up for, no dashboard to log in to, and no vendor to ask for an export. You are the operator.

Neither model is better for everyone. If you want a page in ten minutes with no setup, a hosted service fits that and this does not. See "What do I give up?" below.

## Who is it for?

Reach Around was built mainly for the maintainer's own cutover away from a link-in-bio service. It is open sourced as a springboard, so other creators can use it as a starting point to build their own tools.

## Is there a hosted version I can pay for?

[OWNER INPUT NEEDED] Nothing in the repository offers or promises hosting. Whether the project will ever offer it is the maintainer's decision.

## What does it cost?

The software is free. Running it costs whatever your hosting plan and domain cost. The costs page has the worksheet and the published prices with their dates: [Costs.md](Costs.md).

The one design fact that drives cost is write volume to the Worker's database, which is why plan limits matter. The docs model the daily write quota explicitly (`docs/deploy.md`, `docs/failure-modes.md`, "Quota and budgets"), and a limit fails closed and visibly rather than silently.

## How hard is self-hosting?

You need, at minimum: a Cloudflare account and a domain for the production page, a private instance repository that pins an engine release, a way to hold secrets, and GitHub Actions for deploys ([instance-repos.md](../instance-repos.md), [deploy.md](../deploy.md)). Collectors need API keys from each platform you use, and some platforms need a token you must refresh by hand (`docs/token-health.md`).

You can build the example page locally with a container and no Cloudflare account to see what you would get (README, "Run it with Docker or Podman").

An hours estimate for a first deploy is not something the repository can support. [OWNER INPUT NEEDED] The maintainer's own experience of the setup time goes here.

## Who owns my data?

You do. The config and the stored numbers history are files in your repository. Analytics rows live in a database in your own Cloudflare account. Exports leave your data portable: a local export for Grafana (#335), an analytics archive command (#399) and the history store (#383).

The project sets no third-party analytics and no per-visit identifiers beyond a daily hash, and the do-not-collect list is public ([audience-analysis-privacy.md](../audience-analysis-privacy.md)). If you run the media kit gate you hold visitor email addresses, which makes you responsible for them: retention and deletion are covered in [privacy.md](../privacy.md). Whether a law such as GDPR or CCPA applies to you is a question for you or a lawyer. [OWNER INPUT NEEDED] The maintainer's stated stance on those laws goes here.

## What do I give up?

- **Convenience.** You set up, deploy and update it yourself, and you watch it. Routine work is in [Operations.md](Operations.md).
- **Stability.** Alpha means breaking changes between releases (changelog header).
- **Features a hosted service may have.** These are recorded decisions, not gaps by accident: no website builder, no automated replies, no digital product sales, no email marketing, no audience groups, no sign-up forms beyond the media kit gate and the business inquiry form (owner rulings 2026-11-06, `docs/research/beacons-parity.md`). See [Roadmap.md](Roadmap.md), "What is not planned".
- **Real-time everything.** Some platforms have no API for the numbers you want, so you type those by hand with a date and the report warns when they are old (`docs/operations.md`).
- **A support desk.** [OWNER INPUT NEEDED] Whether and how the maintainer answers questions or fixes issues, and any response commitment, is his to state.

## Can I move my existing page over?

The repository has two tools for it: `reach-around parity` compares the links on your built site with a list exported from your old page (#340, [parity.md](../parity.md)), and a strict importer reads a Link in Bio history export so your old numbers can sit beside new ones (#376, [beacons-history-import.md](../beacons-history-import.md)). The cutover runbook is [cutover-runbook.md](../cutover-runbook.md).

## Which platforms can it count?

Per the changelog: Bluesky, Mastodon-compatible servers, GitHub, Discord invites, Matrix and YouTube (`0.2.0`), plus Twitch followers (#385), TikTok (#388), and Instagram and Threads (#387). Others are typed by hand with an `as_of` date. Facebook (#711) and a fediverse post-count collector (#743) are planned. Each platform's rules for what you may store are your responsibility; the research notes are in [docs/research](../research/README.md).

## Is it ready for production?

It is not stable. The newest build is a `0.3.0` release candidate for previews and staging, not the frozen soak candidate (changelog, `0.3.0-rc.6`, #595), and the storage design for the cloud side is still in draft (#753). Before any production claim, a whole-system review is planned (#754). Check the [Roadmap](Roadmap.md) before you depend on it.

## Is it safe?

It is built to fail closed and to keep the visitor's data small. Hardening is tracked in the issue list; the wiki does not itemize open findings. To report a problem privately, use the repository's private vulnerability reporting if it is enabled. [OWNER INPUT NEEDED] The project has no security policy file today; the maintainer should confirm the reporting route.

## How do I contribute?

Start with [CONTRIBUTING.md](../../CONTRIBUTING.md), which covers the local gate and CI. The project tracks every task as an issue on its board (README, "Status").

## Something is broken

See [Troubleshooting.md](Troubleshooting.md).
