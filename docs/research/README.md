# Platform research

One note per platform, written under epic #1. These are drafts: first-pass research by AI agents from public documentation fetched on 2026-10-23, pending an independent review. Each note lists its sources with dates, marks what it could not verify, and ends with questions for the maintainer.

| Platform | Issue | Note | Review |
|---|---|---|---|
| Instagram | #6 | [instagram.md](instagram.md) | SOUND |
| Threads | #7 | [threads.md](threads.md) | SOUND |
| TikTok | #8 | [tiktok.md](tiktok.md) | FIX |
| X (Twitter) | #9 | [x.md](x.md) | FIX |
| YouTube | #10 | [youtube.md](youtube.md) | FIX |
| Twitch | #11 | [twitch.md](twitch.md) | FIX |
| Kick | #12 | [kick.md](kick.md) | FIX |
| Social Blade | #13 | [social-blade.md](social-blade.md) | FIX |
| Bluesky | #14 | [bluesky.md](bluesky.md) | SOUND |
| Mastodon | #15 | [mastodon.md](mastodon.md) | SOUND |
| Discord | #16 | [discord.md](discord.md) | SOUND |
| Matrix | #17 | [matrix.md](matrix.md) | SOUND |
| GitHub | #18 | [github.md](github.md) | SOUND |
| LinkedIn | #19 | [linkedin.md](linkedin.md) | SOUND |
| StreamElements | #20 | [streamelements.md](streamelements.md) | UNRELIABLE |
| Patreon | #21 | [patreon.md](patreon.md) | FIX |
| Support embeds (StreamElements, Patreon, Ko-fi, Fourthwall, memberships) | #137 | [support-embeds.md](support-embeds.md) | none yet |
| IP network datasets | #120 | [ip-network-datasets.md](ip-network-datasets.md) | none yet |

Review: FIX and UNRELIABLE notes have been corrected in this branch but still need a human read where marked UNVERIFIED (notably TikTok open-source clause, StreamElements docs, Twitch Get Videos/Get Streams, Social Blade terms).

Cross-cutting findings so far, to be confirmed in review:

- Several platforms keep little or no history (GitHub traffic is 14 days; Discord and Matrix have no join or leave history endpoint, and no per-stream analytics endpoint was found for Twitch, though its VOD metadata may serve as history and is unchecked), so the collector must store its own daily snapshots.
- Several tokens expire (Instagram and Threads 60 days, Bluesky public OAuth clients 2 weeks, YouTube refresh tokens 7 days while the consent screen is in Testing), so the daily job needs refresh and expiry alerts.
- Some platforms restrict storing or showing data, or have no API for what a creator wants (LinkedIn, TikTok demographics, Kick followers), so manual or CSV import is a first-class path.
