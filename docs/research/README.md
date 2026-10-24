# Platform research

One note per platform, written under epic #1. These are drafts: first-pass research by AI agents from public documentation fetched on 2026-10-23, pending an independent review. Each note lists its sources with dates, marks what it could not verify, and ends with questions for the maintainer.

| Platform | Issue | Note |
|---|---|---|
| Instagram | #6 | [instagram.md](instagram.md) |
| Threads | #7 | [threads.md](threads.md) |
| TikTok | #8 | [tiktok.md](tiktok.md) |
| X (Twitter) | #9 | [x.md](x.md) |
| YouTube | #10 | [youtube.md](youtube.md) |
| Twitch | #11 | [twitch.md](twitch.md) |
| Kick | #12 | [kick.md](kick.md) |
| Social Blade | #13 | [social-blade.md](social-blade.md) |
| Bluesky | #14 | [bluesky.md](bluesky.md) |
| Mastodon | #15 | [mastodon.md](mastodon.md) |
| Discord | #16 | [discord.md](discord.md) |
| Matrix | #17 | [matrix.md](matrix.md) |
| GitHub | #18 | [github.md](github.md) |
| LinkedIn | #19 | [linkedin.md](linkedin.md) |
| StreamElements | #20 | [streamelements.md](streamelements.md) |
| Patreon | #21 | [patreon.md](patreon.md) |

Cross-cutting findings so far, to be confirmed in review:

- Several platforms keep little or no history (GitHub traffic is 14 days; Discord, Matrix and Twitch have no join, leave or stream history endpoint), so the collector must store its own daily snapshots.
- Several tokens expire (Instagram and Threads 60 days, Bluesky public OAuth clients 2 weeks, YouTube refresh tokens 7 days while the consent screen is in Testing), so the daily job needs refresh and expiry alerts.
- Some platforms restrict storing or showing data, or have no API for what a creator wants (LinkedIn, TikTok demographics, Kick followers), so manual or CSV import is a first-class path.
