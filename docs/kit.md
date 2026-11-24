<!-- SPDX-License-Identifier: Apache-2.0 -->
# Media kit

The media kit is two pages for two audiences. The public page shows your name,
a short description, headline numbers and recent posts to anyone. The gated page holds
the detail you only want to show people who pass a short check: past partners,
case studies, packages, rates, testimonials, audience details and a disclosure statement.

Everything here is synthetic: a fake creator, `example.com` and made-up brands.
Replace them with your own.

## What you write

Add a `kit` section to `site.yaml`. The full field list is in the
[config reference](config.md#kit); the short version:

```yaml
features:
  kit_gate: true
kit:
  title: Example Creator media kit
  about: A fake creator who streams games and talks about gear.
  photo: media/kit-photo.jpg
  contact: Brand deals - partners at example.com
  tags: [Gaming, Hardware]
  turnstile_site_key: 0x4AAAAAAAexampleKey
  metrics:
    - {platform: twitch, label: Followers, value: 1200, as_of: 2027-03-01}
    - {platform: youtube, label: Subscribers, source: counts}
  analytics:
    - platform: instagram
      synced_with: Example Social API
      as_of: 2027-03-08
      followers: 12460
      followers_30_day_percent: 4.26
      stats:
        - {label: Example views, value: 1260000}
  recent_posts:
    - platform: tiktok
      thumbnail: media/recent-post.png
      views: 1200
      likes: 80
      comments: 7
    - platform: threads
      thumbnail: media/recent-thread.png
      views: 900
      likes: 60
      comments: 5
      reposts: 4
      quotes: 2
  partners:
    - {name: Acme Fake Co, logo: media/acme-fake.png, note: 2026 sponsor, url: "https://example.com/acme"}
  case_studies:
    - thumbnails: [media/fake-post-one.png, media/fake-post-two.jpg]
      views: 12345
      likes: 678
      comments: 90
      period_start: 2027-02-01
      period_end: 2027-02-28
  services:
    - title: Example package
      included: [One example item, Another example item]
      price_text: Ask me!
  rates:
    - {item: Sponsored video, price_text: Ask me!}
  testimonials:
    - {quote: An example quote., name: Example Person, brand: Example Brand}
  demographics:
    - platform: instagram
      as_of: 2027-03-01
      gender: {male: 40, female: 60}
      ages:
        - {range: 18-24, all: 35, male: 30, female: 38}
        - {range: 25-34, all: 65, male: 70, female: 62}
      countries:
        - {name: Exampleland, percentage: 70}
        - {name: Sample Republic, percentage: 30}
      cities:
        - {name: Example City, percentage: 55}
        - {name: Sample Junction, percentage: 45}
  disclosure: An example statement about how paid work is marked.
```

**Counts that stay off the link hub.** A `source: counts` metric reads the
numbers file under its `platform`. The source that fills that entry may set
`show_on_hub: false` in [`numbers.sources`](config.md#numbers): the daily
`counts refresh` still fetches it, the kit prints it with the same dates,
old-number warnings and era marks, and the link hub prints no chip and no count
for it (owner decision: follower counts live in the kit, where the trust labels
apply). Such a source does not need a platforms item, but it must have a
`counts` metric here for its platform; without one the config is refused,
because the number would be shown nowhere.

## What the build writes

| File | Where | Who sees it |
| --- | --- | --- |
| `kit/index.html` | `--out` (published) | everyone |
| `kit/retry/index.html` | `--out` (published) | everyone; only when the kit has gated content |
| `kit.html` | `--gated-out` (never published) | a visitor who passed the gate, served by the Worker |
| `kit.pdf` | `kit pdf --out-dir` (private, never published) | you, the owner (see [The private PDF](#the-private-pdf)) |
| `kit-binding.json` | `--gated-out` (never published) | the tools; digests that tie a measures file to this build (see [The measures file](#the-measures-file)) |
| `kit-measures.json` | `kit private-measures --out-dir` (private, never published, never in `--out` or `--gated-out`) | you, and the sponsor page work (see [The measures file](#the-measures-file)) |
| `kit-public-YYYY-MM-DD.pdf` | `kit pdf --public --out-dir` (the public half; you decide where it goes) | anyone you give it to (see [The public PDF](#the-public-pdf)) |

```sh
reach-around build --config site.yaml --out site-out --gated-out gated-out
```

`--gated-out` is required when the kit has partners, case studies, packages,
rates, testimonials, demographics or a disclosure statement. It may not be the output directory, inside it, or a parent of it.
The build writes only `kit.html` there, with its two records `kit-policy.txt` and
`kit-binding.json` (and removes all three if the kit no longer has gated content); anything else in that directory is left alone. Upload the
gated file with the Worker deploy, never with the Pages deploy: the deploy
publishes only `--out`.

### The first screen

The top of the public page is built to be read in 30 seconds: the title, the
`about` line (your niche in a sentence), the `tags` chips, one range line, and
two buttons. The numbers table sits below it at the full content width.

- **Range line.** "Unique audience is between X and Y, depending on platform overlap." X is the
  largest single number the total adds up and Y is the total. It needs two or
  more counted numbers, and it is hidden whenever the total is hidden (a counted
  number under the privacy floor). It is never one deduplicated figure.
- **Request Full Kit.** Jumps to the gate form. It appears only when the kit has
  a gate form.
- **Download Public PDF.** Appears only when `kit.public_pdf` is set to the https
  URL where you published the public PDF.

The gated page shows the range line too. Both PDFs print the range line and drop
the buttons.

### The audience summary

One block, always the same shape, always stating its assumption (#627):

```
Audience across platforms           3,400 to 4,600   Figures dated 2027-03-01 UTC
2 platforms, 4,600 follows. Added up from 2 numbers; the oldest is dated 2027-03-01 (UTC).
Largest single audience: 3,400 (YouTube).
People who follow on more than one platform are counted more than once.
Unique people are somewhere between 3,400 and 4,600.
```

- **Lower bound** is the largest single follows number (a set cannot be smaller
  than its largest member). **Upper bound** is the sum of the follows numbers,
  which assumes no overlap. Both are plain arithmetic on your own numbers; the
  kit never prints one deduplicated "unique people" figure.
- The overlap sentence appears whenever more than one platform contributes. The
  block also prints the oldest date, the number of inputs, the weakest source
  label among them (a hand-kept or estimated number shows as such), an
  "(out of date)" mark when a counted number is old, the before-and-after
  Reach Around note when the dates span the cutover, and a plain caveat that
  follower counts include inactive and automated accounts.
- **Community members** (Discord and Matrix) are never added into either bound.
  They print on their own line, "Community members: Discord 800, Matrix 130.",
  with a note that they are chat members and not followers. The line shows even
  when no follows block does.
- A number marked `in_total: false` is left out of both bounds.
- A counted number under the privacy floor (Under 5) hides the whole block, as
  it hides the range line.
- **Optional estimate.** Off by default. With a hand-kept poll and
  `show_estimate: true` the block adds one labeled line beside the range:
  "Estimate from a poll of N people on DATE: roughly X unique people."
  X is the sum of follows divided by one plus the average number of other
  platforms each respondent follows on, rounded to two significant figures. The
  method is printed under it. It never replaces the range, and it is left out
  when it falls outside the range (a poll that contradicts the arithmetic is not
  printed).

```yaml
kit:
  audience_summary:
    show_estimate: true
    poll:
      respondents: 200          # at least 5
      as_of: 2027-03-01
      avg_other_platforms: 0.5  # 0 to 100, typed by you
```

### Platform cards and the measure registry

Every number the kit prints is a **measure**: one value, one unit, one date and one
trust label (kit v2, #628). The headline numbers (`metrics`), the analytics tiles
(`analytics`) and the engagement inputs (`engagement`) keep working exactly as
before; the build compiles them into measures once, and the pages read the
measures only. A config that built before builds the same pages.

A measure is never visible to a wider audience than the field it came from: the
registry can only narrow, so an engagement figure stays on the gated page and in
the owner PDF whatever a card asks for.

**The registry.** A config picks keys; it does not rewrite the definition of a
known one. Each key has a unit, a definition, and a default audience.

| Key | Unit | Definition | Shown to |
| --- | --- | --- | --- |
| `followers` | follows | The number of accounts that follow the platform account, as the platform reports it. | everyone |
| `members` | follows | The number of members of a chat room or server, as the platform reports it. A member is not a follower, so it never enters the audience range. | everyone |
| `followers_change_30d` | percent | The change in followers over the last 30 days, in percent, as the owner typed it from the platform's own analytics. It can be negative. | everyone |
| `analytics_stat` | count | A count the owner typed from a platform's analytics, named by its own label; the label says what is counted. | everyone |
| `views` | views | The number of times the platform counted a video as viewed in the period, as the platform's own analytics export shows it. Added up by the importer from the daily rows of the export. | everyone |
| `watch_hours` | hours | The total time viewers spent watching in the period, in whole hours, as the platform's own analytics export shows it. Added up by the importer from the daily rows and rounded half up. | everyone |
| `avg_ccv` | viewers | The average number of people watching at the same moment while the channel was live in the period, as the platform's own dashboard shows it. Typed by the owner. | everyone |
| `peak_ccv` | viewers | The highest number of people watching at the same moment while the channel was live in the period, as the platform's own dashboard shows it. Typed by the owner. | everyone |
| `hours_watched` | hours | The total time viewers spent watching the channel in the period, in whole hours, as the platform's own dashboard shows it. Typed by the owner. | everyone |
| `unique_chatters` | viewers | The number of different accounts that sent at least one chat message in the period, as the platform's own dashboard shows it. Typed by the owner. | everyone |
| `followers_gained` | follows | The number of accounts that began following the channel in the period, as the platform's own dashboard shows it. Typed by the owner. | everyone |
| `hours_streamed_per_week` | hours | The hours the channel was live in an average week of the period, in whole hours, as the platform's own dashboard shows it. Typed by the owner. | everyone |
| `posts_total` | count | The number of public posts the account has made, as the server reports it on the public profile and the owner typed it. | everyone |
| `median_boosts_30d` | interactions | The middle count of boosts across the public posts of the last 30 days, worked out by the build from per-post counts the owner typed from the public profile. Bluesky reads reposts, likes and replies; Pixelfed shares, likes and comments. | everyone |
| `median_favorites_30d` | interactions | The middle count of favorites across the public posts of the last 30 days, worked out by the build from per-post counts the owner typed from the public profile. Bluesky reads reposts, likes and replies; Pixelfed shares, likes and comments. | everyone |
| `median_replies_30d` | interactions | The middle count of replies across the public posts of the last 30 days, worked out by the build from per-post counts the owner typed from the public profile. Bluesky reads reposts, likes and replies; Pixelfed shares, likes and comments. | everyone |
| `mean_boosts_30d` | interactions | The average count of boosts across the public posts of the last 30 days, shown only beside the median and only when the owner turns it on; one viral post can pull it far above the median. | everyone |
| `mean_favorites_30d` | interactions | The average count of favorites across the public posts of the last 30 days, shown only beside the median and only when the owner turns it on; one viral post can pull it far above the median. | everyone |
| `mean_replies_30d` | interactions | The average count of replies across the public posts of the last 30 days, shown only beside the median and only when the owner turns it on; one viral post can pull it far above the median. | everyone |
| `subscribers` | follows | The number of accounts subscribed to a PeerTube channel, a Lemmy community or a newsletter, as the instance or the sending service reports it and the owner typed it. A subscriber is not a follower, so it never enters the audience range. | everyone |
| `lemmy_active_users_month` | count | The number of active users the Lemmy community reports for the last month, as the instance reports it and the owner typed it. | everyone |
| `peertube_watch_time_30d` | hours | The hours watched across the channel's videos in the last 30 days, in whole hours, as the instance's video statistics report it and the owner typed it. | everyone |
| `peertube_avg_watch_time` | seconds | The average time one view of the channel's videos lasted, in whole seconds, as the instance's video statistics report it and the owner typed it. | everyone |
| `peertube_unique_viewers_30d` | viewers | The number of different viewers of the channel's videos in the last 30 days, as the instance's video statistics report it and the owner typed it. | everyone |
| `downloads_30d` | downloads | The number of downloads of the podcast's episodes in the last 30 days, as the podcast host counts them and the owner typed it. | everyone |
| `newsletter_delivered` | emails | The number of newsletter emails delivered in the last 30 days, as the sending service reports it and the owner typed it. | everyone |
| `newsletter_clicks` | clicks | The number of clicks on links in the newsletter in the last 30 days, as the sending service reports it and the owner typed it. Clicks are shown instead of opens, which are unreliable. | everyone |
| `blog_views_30d` | views | The number of page views of the blog in the last 30 days, as the site's own analytics report them and the owner typed it. | everyone |
| `avg_views_per_item` | views | Views divided by posts in the period, worked out by the build from figures the owner typed. | after the gate |
| `engagement_rate` | percent | Interactions divided by views in the period, as a percent, worked out by the build from figures the owner typed. | after the gate |

A percent value is kept in tenths of a percent. The unit `count` is for the
owner-named analytics tiles, whose unit the old config never recorded; the label
says what is counted. A typed 30-day change that rounds to zero prints `+0.0%`.

**Cards.** `kit.sections` lists the platforms that get a card, in the order you
write them. Nothing about a card is typed except its presentation:

```yaml
kit:
  sections:
    - {platform: twitch, account: example_twitch}
    - {platform: kick, account: example_kick}
    - {platform: instagram, show: [followers, followers_change_30d], headline: followers}
    - {platform: mastodon, hide_if_empty: true}
```

- `show: default` prints the default set of the platform's group, in this order.
  Twitch and Kick are one group, so they always show the same fields in the same
  order.
- A key the platform has no number for is left out of the card. Nothing is zeroed,
  guessed or filled in.
- When one key has two numbers with the same label for one platform (the same
  follower count typed in two places), the newest date wins, then the stronger
  source, then the first listed.
- The card leads with `headline`, else the platform's own count (followers or
  members), else its first number. Every printed number carries its source label,
  its date in UTC and the out-of-date mark; a computed number prints its formula.
- A configured platform with no number to show prints "No numbers yet", the same
  way for every platform. With `hide_if_empty: true` it prints nothing. "No number
  to show" is judged for the page being built: a platform whose only numbers are
  gated prints "No numbers yet" on the public page and its numbers on the gated page.
- Cards print on the public page, the gated page and both PDFs (the PDF text check
  expects each card's name, its line and its numbers).

| Group | Platforms |
| --- | --- |
| live | twitch, kick |
| video | youtube, tiktok, instagram, threads |
| open | mastodon, bluesky, pixelfed, peertube, lemmy |
| audio | podcast, newsletter, blog |
| community | discord, matrix |
| other | every other platform |

| Group | Default set (`show: default`) |
| --- | --- |
| live | `followers`, `followers_change_30d`, `analytics_stat`, `avg_views_per_item`, `engagement_rate` |
| video | `followers`, `followers_change_30d`, `analytics_stat`, `avg_views_per_item`, `engagement_rate` |
| open | `followers`, `subscribers`, `posts_total`, `followers_change_30d`, `median_boosts_30d`, `median_favorites_30d`, `median_replies_30d`, `mean_boosts_30d`, `mean_favorites_30d`, `mean_replies_30d`, `peertube_watch_time_30d`, `peertube_avg_watch_time`, `peertube_unique_viewers_30d`, `lemmy_active_users_month`, `analytics_stat`, `avg_views_per_item`, `engagement_rate` |
| audio | `subscribers`, `downloads_30d`, `newsletter_delivered`, `newsletter_clicks`, `blog_views_30d` |
| community | `members` |
| other | `followers`, `followers_change_30d`, `analytics_stat`, `avg_views_per_item`, `engagement_rate` |

**Policy record.** Every build writes a hash of the display policy beside the
kit pages: `kit/policy.txt` in the public output and `kit-policy.txt` in the gated
directory. `kit pdf` and `kit pdf --public` refuse a page whose record is missing
or differs from the current policy ("rebuild the site first"), so a page built
before a field moved to a narrower audience can never be printed. The hash holds
no figure and no setting of yours.

### The live block

`kit.live` prints Twitch and Kick in one block of stacked, equal cards in config
order (kit v2, #629). It
holds the seven live measures: `followers` (the platform's headline number) and
the six typed from the platform's own dashboard, `avg_ccv`, `peak_ccv`,
`hours_watched`, `unique_chatters`, `followers_gained` and
`hours_streamed_per_week`.

```yaml
kit:
  live:
    - platform: twitch
      period_start: 2027-02-01
      period_end: 2027-02-28
      as_of: 2027-03-01
      avg_ccv: 40
      peak_ccv: 120
      hours_watched: 900
      unique_chatters: 75
      followers_gained: 310
      hours_streamed_per_week: 18
    - {platform: kick}
```

- Both platforms print the same seven fields in the same order with the same
  markup. The block follows the order you write the rows in; it is never sorted by
  size, and reversing the rows reverses the block.
- Each figure prints its UTC period and date and the label Hand-kept. A live
  figure beyond the follower count is never read from an API, so there is no
  `source` setting and no way to label one API. Anything else waits for the
  platforms' developer terms.
- A figure you leave out prints "Not entered", never zero. A platform with no
  figure at all prints "No live numbers yet". Figures need a period, an `as_of`
  on or after the period's end, and `peak_ccv` may not be below `avg_ccv`.
- The rows are public like the other platform cards, and each measure is never
  wider than that field. The block prints on the public page, the gated page and
  both PDFs.

### The fediverse block

Mastodon, Bluesky and Pixelfed publish their follower and post counts and the
interaction counts of each public post. `kit.fediverse` holds what the public
profile shows, typed by you on a date, one entry per platform (#630). It is hand-kept
until a collector for public post data has its own design; nothing is fetched.

```yaml
kit:
  fediverse:
    - platform: mastodon
      profile_url: https://social.example/@example
      as_of: 2027-03-08
      followers: 1900
      posts: 4321
      show_mean: false
      interactions:        # one row per public post in the 30 days ending on as_of
        - {boosts: 2, favorites: 10, replies: 0}
        - {boosts: 9, favorites: 14, replies: 3}
```

- **Re-check link.** `profile_url` (https) is printed beside every count and median
  in the card, as a plain URL so it reads on paper, so a sponsor can look for
  themselves.
- **As reported by the server.** A card with an entry says "Counts are as reported by
  the server." A server can hide or filter a count, and a Mastodon-compatible server
  can report any number.
- **Median first.** The build works out the median of each per-post count (boosts,
  favorites, replies; Bluesky reposts, likes, replies; Pixelfed shares, likes,
  comments). With an even number of posts the two middle values are averaged and
  rounded half up. The mean prints only when `show_mean` is true, after the medians.
  Both are whole numbers and carry the formula.
- **Not provided, never zero.** A value you did not type prints in one "Not provided"
  line. A post with no replies is a real 0 and you type it. Each platform's known
  absences print too: impressions, reach and demographics on Mastodon and Bluesky;
  those and any insights on Pixelfed.
- A card shows these only when its platform has an entry. All of it is public
  (spec decision 6) and prints on the public page, the gated page and both PDFs.

### The channel block

PeerTube, Lemmy, a podcast, a newsletter and a blog each publish figures somewhere
other than a platform API this kit reads. `kit.channels` holds what that place
shows, typed by you on a date, one entry per platform (#633). It is hand-kept:
nothing is fetched, and a collector for any of them has its own design issue (and a
recorded terms read where a platform's terms apply) before any collector code.

```yaml
kit:
  channels:
    - platform: peertube
      url: https://video.example/c/example
      as_of: 2027-03-08
      subscribers: 340
      watch_time_hours: 1250
      avg_watch_seconds: 95
      unique_viewers: 8200
    - platform: lemmy
      url: https://lemmy.example/c/example
      as_of: 2027-03-08
      subscribers: 77
      active_users_month: 21
    - {platform: podcast, as_of: 2027-03-08, downloads: 5400}
    - {platform: newsletter, as_of: 2027-03-08, subscribers: 2400, delivered: 2300, clicks: 190}
    - {platform: blog, url: "https://blog.example/", as_of: 2027-03-08, views: 12345}
```

- **Each platform takes its own fields.** PeerTube: `subscribers`, `watch_time_hours`,
  `avg_watch_seconds`, `unique_viewers`. Lemmy: `subscribers`, `active_users_month`
  (community fields only; post-level fields are not read). Podcast: `downloads`.
  Newsletter: `subscribers`, `delivered`, `clicks`. Blog: `views`. A field a
  platform does not take is refused, and an entry needs at least one figure.
- **Who reported it.** A card with an entry says so: "as reported by the instance"
  (PeerTube, Lemmy), "as reported by the podcast host", "as reported by the sending
  service" or "as reported by the site's own analytics". PeerTube's federated-view
  completeness is not verified, so the card never claims it.
- **Clicks, not opens.** The newsletter shows delivered emails and clicks. Opens
  are unreliable since Apple Mail Privacy Protection, so the card says it does not
  provide them.
- **Re-check link.** `url` (https) is required for PeerTube and Lemmy, which have a
  public page, and optional for the rest. It prints beside every count as a plain
  URL. A podcast or newsletter with no public page has no link.
- **Not provided, never zero.** A figure you did not type prints in one "Not
  provided" line. A real 0 is typed. The 30-day figures cover the 30 days ending on
  `as_of` and print that period.
- A count of people (`subscribers`, Lemmy active users, PeerTube unique viewers)
  keeps the privacy floor. `subscribers` is not a follower count and never enters
  the audience range.
- A card shows these only when its platform has an entry and a `sections` item. All
  of it is public and prints on the public page, the gated page and both PDFs.

### Structured demographics

Each demographics entry is for one configured platform and one UTC `as_of`
day. `gender.male` and `gender.female` are whole percentages and must total
100. Every age row supplies the percentage for the All, Male and Female views;
each of those three columns must total 100 across the rows. Country and city
percentages are whole numbers from 0 through 100 and may be a top list rather
than a complete distribution. Threads entries do not accept `cities` because
Threads does not provide that chart. YouTube demographics are refused because
YouTube Analytics figures stay in the internal dashboard only.

The gated page renders a gender pie, CSS-only All, Male and Female age chips,
and country and city bars. Every chart prints its labels and percentages, so
color is never the only way to read it. The page uses inline SVG and native
progress bars and loads no chart script. Demographics remain gated and are
included in the private PDF; they never enter the public page or public PDF.

### The privacy floor in the kit

A count of 1 to 4 is never printed in the kit. These print "Under 5" on the page
and in both PDFs: a headline number, the followers and stat tiles of a platform
analytics tab, the views, likes, comments, reposts and quotes of a recent post and
of a case study, the views and clicks of the "Before Reach Around" block, an
average views per post, and the short count beside a platform on the link page.
Zero prints as zero, and 5 prints as 5.

Three derived figures follow from that:

- **Total.** A printed total minus the printed members would isolate a small
  member, so the combined total is not printed while any number that counts toward
  it is 1 to 4.
- **30-day change.** A tab's change is not printed when its followers are under the
  floor (zero included: "-100.0%" fits every earlier count, so nothing is lost by
  holding it back), or when any whole earlier count under the floor would print exactly this
  change (the check uses the text a reader sees, one decimal, not the typed value).
- **Demographic shares.** A whole-number share printed beside a count of the same
  platform tells a reader how many people the cell holds. The counts a share is
  read against are every count of that platform printed at 5 or more on any page
  or PDF of the site: the headline numbers of the kit (typed, or from the numbers
  file), the followers of the platform's analytics tab, and the count beside the
  platform on the link page (typed on the platforms item, or from the numbers
  file, as the page resolves it), in its short and its exact form. A count under 5
  prints as "Under 5" and is no base. A rounded form such as 1.2k is read at the
  true count: the rule only ever hides more than the rounded form would allow a
  reader to call small. When the most a cell can hold against any such count,
  given the half-point rounding of the share, is 1 to 4, the whole group (gender,
  age, countries or cities) is replaced by a "not shown" line, because its shares
  total 100 and one hidden cell would follow from the rest. Only when no count of
  the platform is printed anywhere do the shares print. A count the owner keeps
  off every page (a figure in the history file, say) is not a base.

The PDF text check expects these forms, including the tile of each analytics tab
(its sync line, followers, change and stats), so a page that printed a number
fails the build. Collected YouTube Analytics and Instagram insights figures are
never in the kit; they are in the owner report only
([report.md](report.md#private-platform-analytics---history-397)).

### Trust labels

Every number the kit prints carries one of exactly three labels, a short line
that says where it came from, and its date. The label is decided in code when the
page is built, from what is recorded about the number (below); a template never
picks it, and a fourth label cannot be made (a test fails if one appears in code
or output).

| Label | Printed as | Set when |
| --- | --- | --- |
| API | "API", "Read from the platform's API on 2026-11-19" | a `source: counts` metric whose numbers-file entry carries `"source": "api"` (written by `counts refresh`) and whose platform still has a collector in `numbers.sources`. A stale API entry keeps the label, with the date in its line and the out-of-date mark |
| Hand-kept | "Hand-kept", with a line that says who typed it | a typed `value`, a `counts` entry with no `source` or `source: hand` (including every entry in a file written before this key existed), an `api` entry whose collector is no longer configured, analytics tiles, engagement inputs, case studies, recent posts and demographics (all typed by the owner), and imported data |
| Estimate | "Estimate", then your own line | a metric with `estimate` set; there is no other way to get this label |

Imported link-page history prints as Hand-kept, with the export named in the line
("Imported from the Beacons export, last period ending 2026-02-28"); there is no
fourth label for it. A derived figure takes the weakest label of what it adds: the
combined total prints the weakest label of its members, and an engagement figure
(worked out from typed inputs) is Hand-kept. Twitch and Kick use the same label
logic and print the same markup; the label depends only on the number's source,
never on the platform. The site build does not read the social history file. It reads provenance from
three places only: the kit config, each numbers-file entry's `source` (with the
configured collectors, `numbers.sources`) and the imported-history file. The
numbers file is your own file, so the label is only as honest as the file: a
hand-edited `source: api` can say something untrue. The daily refresh overwrites
a hand-edited `source` only when its fetch succeeds: a failed or skipped collector
keeps the previous entry, a forged `api` included. Instagram, Threads and TikTok
collectors count only while their token is present, so a build without the token
prints those numbers as Hand-kept. Until the next refresh writes `source`, an older numbers file shows
collected counts as Hand-kept, which is correct.

A number older than its age limit (30 days, or the platform's `numbers.stale_days`
entry) prints "(out of date)" beside its label or date, the same rule as the
build warning; the age rule applies to all three labels. The PDF text check expects
the mark wherever the number is old on the day the PDF is made and on the day the
check runs, and compares label words with their case. The closed past periods of the imported block are history, so they
carry no such mark.

A legend, "How to read these numbers", prints once on the public page, the gated
page and both PDFs (what API, Hand-kept and Estimate mean, and which numbers carry
the out-of-date mark). The gated page and the owner PDF name every number that
carries it: headline numbers, the total, analytics, engagement and demographics;
case studies and imported history print their own period, and recent posts
carry a label and method line but no date (the kit has no date for them). The public page and public PDF use shorter wording that names no
gated section.
Under 40rem (a phone) the headline and engagement tables stack: each row becomes a
block, the headers stay for assistive technology, and the label, its line and the
date are readable without scrolling sideways. The wording is a neutral draft until the owner approves it. The PDF text
check expects every label, its line and the legend, so a PDF that lost one fails
the build. The labels print no count, so they cannot reopen the privacy floor, and
they sit on the public page only beside numbers the display policy already makes
public.

### Public page

The page uses a dark press-deck layout built from the shared brand components.
Its introduction and audience panel stack on a phone and sit side by side from
820 px upward. The combined audience is the one large headline number. Its
visible "As of" date is the oldest date among the metrics included in that
total, so the number and date come from the same resolved inputs as the detail
table. The optional wide photo, kit title, content tags and description remain
in the introduction.

The total adds up the numbers that are not
marked `in_total: false` and are not Discord or Matrix members (see
[the audience summary](#the-audience-summary)); it says how many numbers it adds
up and the date of the oldest one, and notes that people who follow on several
platforms are counted more than once. A number with no value is left out. With nothing to add
there is no headline total.

Optional `analytics` rows render a platform tab row and one stat-tile grid for
each configured platform. The allowed platforms are `instagram`, `tiktok`,
`threads` and `twitch`; YouTube is not accepted here. Every row requires the
literal `synced_with` source name, its `as_of` sync date, the follower count and
the 30-day follower percentage change. `stats` is an optional extensible list of
additional labeled count tiles. Counts use one decimal with `k` or `M` at one
thousand and one million; percentages use one decimal and always show a sign.
The sync pill prints the configured source and sync date. A separate "Last
updated" line prints the build date. These rows are public on both kit pages and
in the public PDF. They do not change the owner-report-only rule for engagement
figures, and they cannot contain YouTube Analytics figures.
`recent_posts` adds a "Most recent posts" grid grouped by platform. Every post
needs `views`, `likes`, `comments` and a PNG or JPEG `thumbnail` path relative
to the instance directory. Threads posts may also carry `reposts` and `quotes`.
The build removes PNG text chunks and JPEG metadata, caps the source at 256
KiB, fingerprints the scrubbed image and loads it from the same site. Remote
image addresses, absolute paths and paths outside the instance are refused.
Supply or generate these image files inside the instance before the build.
Recent-post pictures are left out of PDFs; the post counts remain. YouTube
recent-post figures are refused and stay out of the public kit; keep collected
YouTube Analytics figures in the owner-only dashboard.

When the config sets [`cutover_date`](config.md#cutover_date) and that UTC day
has arrived, a small line "Live since 2026-11-08 (UTC)" sits above the numbers
(on the gated page and in the private PDF too). Without the date, or while it is
still ahead, there is no line.

**Before and after Reach Around.** Once the cutover day has arrived, every dated
number on the public kit page, the gated page, the PDF and the link page's
platform numbers says which side of it the date is on: "before Reach Around" for
a date earlier than the cutover day, "after" for the cutover day itself and any
later day. When the combined audience adds numbers from both sides, the note
under it says so. Unset or still ahead: no extra text and the output is
unchanged. If the history store (`numbers.history_file`) carries an `era` or
`note` for a record, that text is the only thing shown for the record, escaped,
and only where a history value is shown.

**Display zone.** Everything is stored and shown in UTC, and every date on the
page is labeled UTC. Dates are UTC days: a day cannot be re-bucketed into
another zone. The public kit page offers an optional "Also show when each UTC
day begins in" menu with a fixed list of common zones (UTC and 17 others, listed
in `reach_around.config.zones.ZONES`). Picking one fills a short note beside each
date ("that UTC day begins Nov 7, 2026, 4:00 PM PST") and puts `#tz=Zone` in the
address, so a link can carry the choice. It uses no cookie and no browser
storage, makes no request and adds no third-party script. It is one small
same-origin file, `static/zone.js`, loaded with `defer` and covered by the
existing CSP (`script-src 'self'`; no change). The menu is hidden until the
script runs, so with JavaScript off the page is plain UTC. The gated page, the
retry page and the PDF carry no display-zone script and stay UTC (the kit and
retry pages do load the Turnstile script, see below).
There is no setting to turn it on: it is part of the public kit page.

If the kit has gated content, the page explains the gate and shows the form.

### The gate form

The form is plain HTML that works with no script on your side. It has:

- `email`: a required email field with a visible label;
- `website`: a honeypot, kept off screen and out of the tab order. A real visitor
  never fills it; the Worker should treat any value as a bot;
- an empty `div` with class `cf-turnstile` and `data-action="gate"`, the slot
  for the Turnstile widget. It carries `data-sitekey` when
  `kit.turnstile_site_key` is set. Turnstile's own script, loaded on the kit and
  retry pages only (below), renders the widget there and adds the response to the
  form as `cf-turnstile-response`.

There are no hidden fields. The form posts to the Worker route `/k/gate`
(`GATE_ACTION` in `reach_around/site/kit.py`); every refusal there sends the
visitor to the retry page, which carries the same form.

The page says plainly that the address is not checked and nothing is sent to it,
and links to the privacy page, which lists what the gate collects and for how
long.

**Turnstile and the Content-Security-Policy.** The build adds one narrow
exception for the widget, on the pages that render it and nowhere else: the
kit page (`/kit/`) and the retry page (`/kit/retry/`), and only when the kit
has a gate form and `kit.turnstile_site_key` is set. On those two paths
`script-src` and `frame-src` gain `https://challenges.cloudflare.com`, which is
what Cloudflare's Turnstile CSP reference lists (page last updated May 5, 2026,
read 2026-11-07; verified). `connect-src` is not widened: that page does not list
it for the normal widget (it needs `'self'` only for pre-clearance, which this
site does not use). If a browser run ever names a `connect-src` violation, add
that one origin in `reach_around/site/kit.py::gate_path_extras` with a test.
No other path gets the Turnstile origin, and no `unsafe-*`, wildcard or `data:`
source is added. The existing per-path exception for live-tab pages (their own
`frame-src` for the player origins) is unchanged. The gated
page (`kit.html`, served by the Worker) is not part of `_headers`; the Worker
sets its headers.

**The Turnstile script.** The two gate pages carry one tag,
`<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer>`,
only when `kit.turnstile_site_key` is set. It uses Turnstile's implicit rendering:
the script finds the `cf-turnstile` container (site key and action) and adds the
response field to the form, so the site needs no script of its own for it and no
inline code, and the policy above is all it needs. No other page, and not the
gated page, loads it. A test pins that the page names exactly that origin, that
the same origin is in the page's policy, and that no other page changes.
Turnstile is third-party code outside this repository, so the measured scores in
[performance.md](performance.md) do not include it.

### Retry page

`/kit/retry/` is fixed text and the same form. It never says why a try failed,
so it reveals nothing about the check. It is marked `noindex`. There is no
`/kit/sent/` page: it belongs to the emailed link, which is not part of v1
(`kit.unlock_link: true` is refused with an error until it exists).

### Gated page

A complete page (header, navigation, the same numbers, the Engagement table when
the config has `engagement` rows (average views per post and engagement rate per
platform, each with its formula, UTC period and as-of date, or `not available`;
see the [config reference](config.md#kit)), then partners, case
studies, packages, a rates table, testimonials, audience details, the disclosure statement and, when `kit.inquiry` is set, the
[business inquiry form](#the-business-inquiry-form)), marked `noindex`, with
root-based asset links (`/static/...`) because the Worker serves it from any
path. Partner links open with `rel="noopener noreferrer"`. It contains no
script, and no form unless `kit.inquiry` is set.

Partners with a `logo` appear in a responsive logo grid. The first six partners
are visible immediately; any remaining partners are under a native **Show more**
disclosure that needs no script. A partner without a logo keeps the existing
text presentation. The optional header `photo` and partner `logo` values are
local PNG or JPEG paths under the instance directory. The public photo is
fingerprinted with the site's public assets. Partner logos are embedded only in
the gated page, so their files and bytes are not published before the gate.

Each case-study card is one owner-entered aggregate for a group of posts. It
shows every local PNG or JPEG path in `thumbnails`, the required `views`,
`likes` and `comments` totals, and the required UTC range from `period_start`
through `period_end`. All six fields are required together. There is no
platform field. Thumbnail files are embedded in the gated page and owner PDF
and are never copied into the public site.

YouTube Analytics values collected with private OAuth scopes are never accepted
as kit input. A YouTube engagement figure may appear here only when the owner
types it into the config and sets `owner_supplied: true`; the page labels every
such figure `Owner-supplied`. The public pages never receive those rows.

### The business inquiry form

Set `kit.inquiry` (see the [config reference](config.md#kit)) and the gated page
gains a "Business Inquiries" block: the owner's email and PGP fingerprint, taken
from the config, and a plain HTML form beside them. It is on the gated page only,
so it is as private as the partners and the rates; the public page, the retry
page and every other published file hold none of it (a test searches the whole
public output for the address, the fingerprint and the route).

The form has `name` (required, 80 characters), `email` (optional, strict address
grammar, 254), `brand` (80), `budget` (40) and `details` (800, line breaks
allowed), plus the `website` honeypot. It has no script and no Turnstile widget:
the visitor already passed the gate, and the Worker re-checks the unlock cookie
on every post. It posts to `POST /k/inquiry` on the Worker
(`worker/src/kit/inquiry.ts`). What the Worker does with it, in order: kill switch,
method, origin (the site's own), content type, configuration, the unlock cookie
(no valid cookie goes back to the gate page), a per-client limiter, a 16 KiB body
cap, strict field rules (no control, hidden or bidirectional characters in the
one-line fields; unknown or repeated fields refused), the honeypot (a bot is told
it worked and nothing is sent), the daily budget, then delivery. Nothing is
stored. The answer is a redirect to one of two fixed pages, `/k/inquiry/sent` and
`/k/inquiry/error`, that say the same thing every time and echo nothing.

**Delivery.** The inquiry is sent once as the owner alert event
`inquiry_received` over the channels the owner enabled (Discord or Matrix; the
email channel has no provider yet), so the owner must add `inquiry_received` to
`alerts.events` and enable a channel ([alerts](alerts.md)). With no alert
configured the visitor sees the "not sent" page and nothing is lost silently.
The text is neutralized like every alert (no markup, mention or link survives,
line breaks become spaces), so a visitor cannot make the alert do anything. The
daily alert budget (`DAILY_ALERT_BUDGET`, default 20) is also the daily cap on
inquiries.

The private PDF prints the owner details and drops the form (the form's
`<aside>` is chrome, like the footer). The privacy page gains one entry for the
form when `kit.inquiry` is set.

## Link-page history from before Reach Around

`kit.imported_history` names a small JSON file beside the config: views and
clicks per month that the previous link-page provider reported, imported once.
The kit shows them as their own block, **Before Reach Around**, with one
sentence saying they come from the previous provider, are not comparable to the
numbers Reach Around collects and are not added to any total. The block is never
part of the headline numbers or the combined audience: the numbers section is
byte for byte what it is without the file (a test pins it).

Where it shows follows the display policy table below. By default the block is
**gated**: its numbers are on the gated page and the private PDF and nowhere
public. The public gate teaser still names the category ("Link-page history from
the previous provider"), as it does for every other gated category. Set
`kit.imported_history_public: true` to show it on the public page and in the
public PDF too. Era marks apply: with `cutover_date` in force a month that is
not wholly before the cutover is left out (the collected numbers cover it), and
each row carries the "before Reach Around" mark; a month with fewer days than
the calendar month says how many days it holds.

The build never reads the social history file (`numbers.history_file`): that
file holds private analytics and tombstones, and the config is refused if
`imported_history` names it. The kit reads only the aggregate file, made from
the importer's history file by one extra step:

```text
reach-around counts history-export-imported \
  --file beacons-pre-cutover-history.jsonl \
  --out imported-history.json
```

The export checks the hash chain, accepts only the importer's link-page
records, and writes views and clicks per calendar month with the provenance
words (`source: imported`, `note: Beacons`, neither printed) and the days each
month holds. It never overwrites a file. Nothing per day, per link, per
referrer or per visitor is in it. Keep it in the private instance repository
like the history file; both `reach-around build` and `reach-around kit pdf`
read it from the path in the config, and a missing file stops the build.

## The wall between the two

The boundary is structural. The rule for what is public is the display policy
table in `reach_around/config/kit.py`; the public page is rendered from
`public_projection` and the gated page from `gated_projection`, so a template
cannot reach a gated field, and the two pages go to two separate directories.

| Field | Visibility | Owner-report marker |
| --- | --- | --- |
| title, about, photo, contact, tags, metrics, analytics, unified_total, turnstile_site_key | public | no |
| partners, case_studies, services, rates, testimonials, demographics, inquiry, disclosure | gated | no |
| engagement | gated | `owner_report_only`; YouTube figures require `owner_supplied: true` |
| imported_history | gated by default; public when `imported_history_public: true` | no |
| imported_history_public, unlock_link | private | no |

What the build enforces:

1. **Separate roots, checked by filesystem identity.** The gated directory may
   not be the public output, inside it or around it, however it is spelled:
   links and other aliases of one directory are compared by device and inode,
   not by name. The build also refuses a public output that already holds an
   entry named `kit.html`.
2. **Safe writes.** The gated directory is created owner-only. The page is
   written to a new temporary file there and renamed into place after the public
   tree is written, so a failure to write it leaves the public tree as it was. A
   symbolic link at `kit.html` in the gated directory is refused, never written
   through.
3. **A search as defense in depth.** Before anything is written, each gated
   value of at least 6 characters (partner text, case study text, rate text,
   demographic text and dates, and every imported-history number with its
   label unless the owner made the block public) is searched for in every public file: as written,
   HTML-escaped, and in a canonical form: entities, percent-encoding and JSON,
   JavaScript (`\x`, `\u`) and CSS escapes decoded until the text stops changing
   (at most 10 rounds), tags removed, Unicode NFKC, case and whitespace ignored.
   Known text types (html, css, js, json, xml, svg, txt, webmanifest, xhtml, map,
   `_headers`, `_redirects`) are always canonicalized; one that holds a NUL byte
   or invalid UTF-8 fails the build, naming the file and the reason. Any other
   file is sniffed by content: valid UTF-8 with no NUL byte near the start is
   canonicalized; otherwise (an image, a font) it is searched for the raw bytes of
   each value in UTF-8 and UTF-16, which finds a value stored in metadata or a
   name table. The search has
   work limits (text size per file and in total, and number of gated values);
   over a limit the build stops with an error that names the limit. A
   hit stops the build and names the field, not the text. A value that is also
   public by policy (the kit title and description, a metric label or date) is
   not a leak and is skipped.
4. **No remote requests from the gated page.** The page and each local
   stylesheet it links are checked: a build fails, naming the file or element
   and never the address, if the page or a stylesheet imports anything or names
   an `http(s)` or protocol-relative target (a remote theme background image
   counts), or if the page has a request-bearing element pointing at another
   host. Use local files.

What is **not** guaranteed: the search cannot catch every transformation a
custom template could apply (base64 and other arbitrary transforms are out of
scope), and values under 6 characters are not searched (one exception: a
`kit.disclosure` that short fails the build if it is the whole content of an
element in a public file, such as the same text in the site footer). Those rest on the
structure in points 1 and 2. If you use an override template for the kit pages,
review it as code that handles gated-adjacent text. If the same wording must
appear on a public page, reword one of them.

Two tradeoffs are deliberate and not changed:

- **Encoding depth is bounded.** Decoding stops after 10 rounds, so a value
  wrapped in more than 10 layers of encoding is not recognized. The structure,
  not the search, is what keeps such text out.
- **Whitespace is ignored, so the search can over-match.** Comparing with all
  whitespace removed means two different phrases can collide (for example
  "the rapist" and "therapist", or "no where" and "nowhere"). A false positive
  fails the build, which is the safe direction; the fix is to reword one side.
  Invisible format characters (zero-width, joiners, bidi controls, soft hyphen)
  are dropped in the same comparison.

### Trust assumptions

- The gated directory must be owner-only. An existing directory that group or
  others can enter is refused; the build removes only its own leftover staging
  files (`.kit.html.<16 hex>.tmp`) from it.
- The directory that holds `<out>.gated.json` (the manifest's directory) is a trusted state directory. Someone
  who can remove the record can defeat the stale-page check and leave an old
  gated page in place.
- Replacing a parent directory of either root while a build runs is out of scope:
  this is a local build tool run by the owner, not a service.
- The public tree is rewritten in place, not atomically. If a build fails part
  way, the public tree may be partial: rebuild before publishing. If the final
  rename of the gated page fails, or recording the destination fails, the new
  public output can sit beside the previous gated page: rebuild.

### Removing a gated page

`reach-around build` records where it wrote the gated page, in
`<out>.gated.json` beside the manifest (beside `--out` unless you pass `--manifest`; the manifest may not be inside `--out`, so the record is never published). While
that record exists, every build must pass `--gated-out` with the same
directory; without it the build stops with an error and changes nothing. A build
whose kit no longer has gated content then removes the old `kit.html` and
deletes the record, after which plain builds work again. If you moved the gated
directory on purpose, delete the old page and the record first. Code that calls
`build_site` directly has no record and must pass the old directory itself.

### The gated page and requests

The gated page makes no third-party request. A remote (`https://`) creator
avatar is left out of it; the public pages keep the avatar, like every other
page. Use a local avatar file if you want it on the gated page too.

## The private PDF

`reach-around kit pdf` prints the whole gated kit (the numbers, partners, case
studies, packages, rates, testimonials, audience details and the disclosure statement) to a PDF for you, the owner. It is never
published and never linked from a page.

```sh
reach-around build --config site.yaml --out site-out --gated-out gated-out
reach-around kit pdf --config site.yaml --site-out site-out \
  --gated-out gated-out --out-dir ~/private/kit
```

It prints the path of the file and nothing else on standard output. It needs a
Chromium or Chrome binary (the one the visual tests already use): pass
`--chromium PATH`, set `RA_CHROMIUM`, or have `chromium` on `PATH`. It adds no
Python dependency.

| Option | Meaning |
| --- | --- |
| `--config` | the site config (for the kit text the PDF is checked against) |
| `--gated-out` | the gated directory of the same build (holds `kit.html`) |
| `--site-out` | the public output of the same build (the stylesheets and local images the gated page uses) |
| `--out-dir` | where the PDF goes; created owner-only if missing |
| `--name` | file name, default `kit.pdf`; one plain name ending `.pdf` |
| `--date` | the creation date written into the PDF, `YYYY-MM-DD`; default `SOURCE_DATE_EPOCH`, else the day `kit.html` was built |
| `--overwrite` | replace an existing file (never a link or a directory) |
| `--isolation netns` | also run the browser in its own network namespace (see below) |

What it guarantees, and how each part is checked:

- **Offline.** The browser gets a sealed copy of the page, built from an
  allowlist: no script, form, frame, remote stylesheet, remote image, `@import`,
  `@font-face` or `url(...)` survives, and a page that has one of the dangerous
  tags stops the build instead of being cleaned. The copy carries a
  Content-Security-Policy that allows no load at all except embedded images. The
  browser also runs with DNS mapped to nothing, a dead proxy that covers
  loopback, no JavaScript, a fresh empty profile and a scrubbed environment.
  `--isolation netns` adds a network namespace of its own (it needs unprivileged
  user namespaces, and the browser's own sandbox is off in that mode because it
  cannot nest here; the default keeps the sandbox). The tests prove the
  Python side makes no socket call and that the browser never calls a local
  server the page points at.
- **Only your files.** Stylesheets come from `<site-out>/static/*.css` and images
  from `<site-out>/static/*.png|jpg|jpeg`, never through a symbolic link (the site output directory and its
  `static` directory included, which are not canonicalized away), with
  size limits. PNG text chunks are dropped, and a JPEG is rebuilt segment by segment (EXIF, XMP, ICC profiles and comments are dropped wherever they sit, including between the scans of a progressive file; an unknown marker is refused). Fonts are the
  system fonts: the site's web fonts are not embedded, so the PDF will not use
  the site's typeface.
- **Scrubbed and reproducible.** The Info dictionary is rewritten to a title, the
  producer `Reach Around` and the creation date. No author, creator, browser
  version, modification time, XMP block or document id is kept, and the build
  fails if any such trace, your user name, your home directory or the work
  directory is in the file. The same gated page and the same `--date` give the
  same bytes, on the same machine and browser build (a different browser or
  system font set draws differently).
- **Leak check.** The text is extracted from the finished PDF and searched for
  every string value of a field the config marks private or not-for-PDF (the
  Turnstile site key today, listed in `PDF_EXCLUDED` in
  `reach_around/config/kit.py`; `unlock_link` is a switch and has no text) and
  for gate-form words. A hit fails the build and names the field, never the
  value; nothing is written. The same pass checks that every gated text in the
  config is in the PDF, including each headline-number row (platform, measure,
  value and date together) and the combined total, computed from the same resolved
  values the page was built from (`--numbers`, else `numbers.json` beside the
  config, as `build` does). This catches a stale `--gated-out` (rebuild first) and
  text the renderer lost. Values under 6 characters are not searched.
- **Limits.** The file is at most 20 MiB and 60 pages, the gated page at most
  2 MiB, and the browser is stopped after 120 seconds.
- **Where it may go.** The output directory may not be, hold or sit inside the
  public output, the gated directory or the directory of the config, may not
  be inside a git work tree (a `.git` in it or any parent) or hold one below it, and may not go
  through a symbolic link. An existing directory must be yours and closed to
  others. The file is written owner-only to a temporary name and renamed into
  place. The work directory (the page, the browser profile, the unscrubbed
  PDF) is owner-only, under `XDG_RUNTIME_DIR` when that is private, and is
  removed on every exit, including `SIGTERM`. If the removal fails the build
  fails (or, when it was already failing, the original error carries a note), so a
  leftover unscrubbed PDF is never silent.

Defaults the owner may change (set by delegated decision, 2026-11-07):

- Isolation stays `off` with the browser sandbox on. `--isolation netns` is the
  stronger option for the network (its own namespace) but turns the sandbox off.
- Local images must be PNG or JPEG; GIF, WebP and SVG are refused, and the error
  names the accepted formats.
- The creation date defaults to the day the gated page was built.
- The site's web fonts are not embedded (follow-up, not built).

Not covered: text drawn as an image, which no text search can see; the content of
a local image beyond dropping its metadata; a browser bug (the sealed page, the
policy and the optional namespace are the defenses); and what you do with the
file afterwards. Exit code 0 on success, 1 for any refusal (one line on standard
error), 2 for a usage error.

## The measures file

`reach-around kit private-measures` writes the OWNER-ONLY private file
`kit-measures.json`: the measures the gated kit shows, each with its trust label
and date. (`kit measure` is a different command: it writes hand-kept public
entries.) The sponsor page and the quote
read this file, and nothing else, for a measure value, so a quoted number is the
number the kit shows, with the same label and date. The kit computes no price.

```sh
reach-around build --config site.yaml --out site-out --gated-out gated-out
reach-around kit private-measures --config site.yaml --site-out site-out \
  --gated-out gated-out --out-dir ~/private/kit-measures
```

It prints the path and nothing else on standard output, and a reminder that the
file is private on standard error. The options are `--config`, `--site-out`,
`--gated-out`, `--out-dir` (all required), `--numbers` (as for the PDF),
`--date` (the `generated` day; default `SOURCE_DATE_EPOCH`, else the day the
gated page was built) and `--overwrite`. It needs no browser.

Where it may go, and what it holds:

- **Owner-only, never published.** The directory follows the private PDF's rules:
  it may not be, hold or sit inside the public output, the gated directory, the
  config directory or a git work tree (nor hold one below it), may not go through a link, and is created
  owner-only (the file is mode 0600). The file name is in no page, sitemap or
  published list; a test fails if it is.
- **Measures only.** It holds the public and gated measures and nothing else of
  the kit: no partner, rate, case study, demographic, inquiry detail or site
  key. A measure of a field marked private is never in it, and the text is
  searched for every private figure and every not-for-PDF value before anything
  is written; a hit names the field, never the value, and writes nothing.
- **Tied to one build.** `build --gated-out` writes `kit-binding.json` beside
  `kit.html` (never in the public output): digests of the display policy, of the
  resolved measures, of the gated page and of the public kit page. `kit private-measures`
  recomputes each one from the config and numbers you pass and from the two
  directories, and writes nothing when any differs: a changed config or numbers
  file, a `--site-out` and `--gated-out` from different builds, an edited page or
  a changed display policy. Rebuild, then run it again with the same inputs. The
  file records the same digests (`binding`), and the reader can compare them with
  an expected binding. A kit with no measures writes no file.
- **Compared as values.** Before the text is made, every string and number of the
  document is compared, decoded and normalized (case, width and spacing folded),
  with an inventory of the private-source values: each private field, each
  number and free text of each private measure, the loaded imported history when
  its block is private, and the not-for-PDF values. Numbers are compared as
  numbers, by magnitude: a private 12.3 or -12.3 matches 12.30, "-12.30%" and
  "12.3%", and a private 1234567 matches "1,234,567", "1 234 567", "1'234'567"
  and "1234 567" written in a text. Before numbers are read, the text is folded
  (Unicode NFKC, so fullwidth digits count; invisible characters such as zero
  width joiners are dropped). Thousands may be separated by a comma, any Unicode
  space, or one of these apostrophe-like marks: `'` (U+0027), U+2018, U+2019,
  U+201B, U+02BB, U+02BC, U+2032 and the grave accent `` ` ``. Digit groups
  joined by exactly one separator that is not a line break are also read as one
  number ("1234 567"). **Fail closed (6 or more digits):** a private whole
  number of 6 or more digits is also looked for with every separator between
  two digits removed: any run of commas, periods used as group marks, spaces,
  line breaks, apostrophe-like marks and invisible characters. So "9,162, 534",
  "9162  534", "9162" newline "534" and "9.162.534" all match a private 9162534.
  Letters are not separators ("123 apples, 456 pears" is two numbers). **The
  price:** two unrelated numbers that only touch through punctuation ("results
  were 123, 456 respectively" against a private 123456) are a false stop. The
  build error says to reword the text that shows the number, in any spacing; do
  that, since the rule is meant to err on the side of keeping a private figure
  out. Unrelated numbers split by words are left alone.
  **Short figures:** a private whole number of fewer than 6 digits (3, 40) is in
  the inventory, but is matched in a text only when it is written with a unit: a
  percent sign, a currency sign or code before or after it (`$3`, `$-3`, `3
  USD`), or a unit word (the registry units such as follows, views, viewers,
  hours, seconds, clicks, downloads, emails and interactions, and words such as
  followers, subscribers, minutes, posts or episodes). "3%" and "40 subscribers"
  are refused; "3 platforms", "40 days" and a bare "40" in a sentence are not,
  because those say nothing alone. The numbers are read by the same rules as the
  long ones, so "1,234 followers" is 1234 (and the 234 inside a true thousands
  group is not a number of its own). The parts of a joined span do count beside
  a unit: "20 40 subscribers" matches a private 40, as well as the joined 2040.
  **Rows:** a measure row's numeric `value` always has a unit, so a
  row whose `unit` is a registry unit is compared with the short private figures
  as it is (a `percent` row is stored in tenths: 400 is 40). The encoding of the
  file is never part of the comparison.
- **The directory stays put.** The output directory is held open from the walk
  to the write, and the temporary file is made and installed only through that
  descriptor (`openat` and `renameat`, never by path). Just before the install,
  and twice in a row after it, the path is resolved again and must be the same
  directory (same device and inode, still linked) and still outside the
  forbidden directories; otherwise the installed file and the temporary file are
  removed through the held descriptor and nothing is kept. **Residual:** each
  check is a sample of where the folder is at one moment. A process that can
  rename the folder (or an ancestor) can move it into a published tree and back
  between any two samples, or after the last one, and the file is then visible
  there for that time; every sample afterwards sees the folder where it belongs.
  More samples only narrow the gaps; none closes them. Closing it needs the
  kernel to refuse the move, which a user-space tool cannot ask for. So the rule
  is on the owner's side: **write private output to a folder only you can write
  to, and whose parent directory (and every directory above it) no other user can
  write**, so that nobody else can rename it. The tool warns (`OutputWarning`,
  printed on standard error) when the parent of the output folder is writable by
  the group or by others. Mount tricks (bind mounts, mount namespaces) are out of
  scope as well. This is an owner-local tool with a same-user threat model: a
  process running as you can already read the file and the private data it was
  made from.
- **Reproducible.** The same inputs and `--date` give the same bytes.

### Schema

The file is JSON. `schema_version` is 1; a change that renames, removes or
retypes a key raises it, and a reader refuses a version it does not know.
`load_measures_file(path, expected_binding=None)` in
`reach_around.kit.measures_file` is the one reader. It checks the type and format
of every field (whole numbers only, not booleans; negative only for a `percent`
change; real `YYYY-MM-DD` days and ordered periods; a known unit and trust label;
slugs; text limits), and refuses a link, a file over 4 MiB, nesting over 8
levels, more than 5000 rows or none, a duplicate or unknown key, `NaN` or
`Infinity`, another version and a `private` row.

| Key | Meaning |
| --- | --- |
| `schema_version` | 1 |
| `kind` | `reach-around-kit-measures` |
| `notice` | the private reminder, printed in the file |
| `generated` | the day the file was made, `YYYY-MM-DD` |
| `policy` | the display-policy fingerprint the file was made under |
| `binding` | `{policy, measures, gated_page, public_page}`: the SHA-256 digests of the build the file matches |
| `measures` | a list, in page order, of the rows below |

Each row of `measures` has these keys, all always present:

| Key | Meaning |
| --- | --- |
| `platform`, `key`, `label` | the platform slug, the registry key and the label the kit prints |
| `unit` | the registry unit (`follows`, `percent`, `seconds`, and so on) |
| `value` | a whole number, or `null` for not available (never zero for missing); a `percent` is an integer in tenths |
| `as_of` | the day the figure is for, `YYYY-MM-DD`, or `null` |
| `period` | `[start, end]` days, or `null` |
| `visibility` | `public` or `gated` (never `private`) |
| `trust` | `{label, method}` (the label is `api`, `hand-kept` or `estimate`), or `null` |
| `in_summary` | whether the figure counts toward the audience summary |
| `formula`, `note`, `provenance`, `source_note` | the text the kit prints beside the figure, empty when none |

## The public PDF

`reach-around kit pdf --public` prints the public half of the kit (the page the
site publishes at `/kit/`: the title, the introduction, the contact line, the
tags, the audience numbers with their dates and the combined audience) to a
dated PDF, so any brand can save a copy. It is the answer to the owner's
decision of 2026-11-08 (issue #425): the light public kit also gets a
downloadable PDF. It carries a "Valid as of YYYY-MM-DD (UTC)" line (the
creation date) and the "Live since" line when `cutover_date` has arrived.

```sh
reach-around build --config site.yaml --out site-out
reach-around kit pdf --public --config site.yaml --site-out site-out \
  --out-dir ~/kit-pdfs
```

It uses the same code as the private PDF, with the same options (`--config`,
`--site-out`, `--out-dir`, `--name`, `--date`, `--numbers`, `--chromium`,
`--isolation`, `--overwrite`), and the same sealing: an allowlisted rebuild of
the page with nothing that can load, a Content-Security-Policy that forbids every
load, headless Chromium with the network cut off, a fixed Info dictionary, the
same size and page limits, the same confined owner-only write (never inside the
public output, the config directory or a git work tree, never through a link) and
the same removal of the work directory. Differences:

- **No `--gated-out`.** It reads `kit/index.html` from `--site-out`; giving
  `--gated-out` with `--public` is an error. The gated page is never opened.
- **File name.** The default is `kit-public-YYYY-MM-DD.pdf` (the creation date,
  see `--date`); `--name` overrides it.
- **The gate box is dropped.** The email form, its text and the zone menu mean
  nothing on paper, so the stager removes those two boxes whole. Every other form,
  input, select or script, and any inline script, still stops the build.
- **The same display policy.** The build refuses what the private PDF refuses
  (collected YouTube Analytics figures), and a field marked owner-report-only
  never reaches the public projection, so engagement figures are not in it.
- **A wider leak check.** The text of the finished PDF is searched for every
  gated value (partners, case studies, packages, rates, engagement notes,
  testimonials, audience details, the disclosure statement and the inquiry
  details), for the Turnstile site key and for gate-form words. A hit fails the
  build, names the field and never the value, and writes nothing. A gated value
  that equals a public value (for example the same text in the contact line) is
  not a leak. The same pass checks that every public text, the "Valid as of"
  line and the "Live since" line are in the PDF, which catches a stale
  `--site-out` (rebuild first) and text the renderer lost.
- **No warning banner.** The file holds public figures only, so the "private: do
  not publish" notice is not printed. It is still written owner-only; you copy it
  where you want it.

Defaults the owner may change: the "Valid as of" day is the creation date, not
the day the numbers were read (each row keeps its own "as of" date). The dated
"valid as of" line for the deeper kit request flow is separate work (#426).

## Contact line

`kit.contact` is one optional plain-text line (at most 200 characters) printed
under the introduction on the public page, the gated page and the owner PDF. It
is escaped text, never a `mailto:` or `tel:` link. The owner decides what goes
there; an email address as plain text is acceptable, and no postal address or
phone number is required or checked (the Beacons kit publishes a postal address;
Reach Around does not need one). Left out, nothing is printed.

## Hand-kept entries and imports

A number with no collector is kept by hand. Each one is an entry in
`hand-kept.json`, beside your config: a registry key (see
[the registry](#platform-cards-and-the-measure-registry)), a whole-number value, a
UTC day (`as_of`), optionally the period it covers and a short note. The unit, the
definition and the default audience come from the registry; you never restate them.
An entry is labeled hand-kept. One made by an importer says which export in its
method line ("Imported from the YouTube analytics export"); there is no fourth
label.

```
reach-around kit measure add --file hand-kept.json --platform bluesky \
  --key followers --value 120 --as-of 2027-02-20
reach-around kit measure import-youtube --file hand-kept.json \
  --export "Chart data.csv"
```

`measure add` refuses an unknown key, a unit that is not the key's unit (`--unit`,
optional), a measure the build works out itself, a negative value for a key that
cannot fall, a day in the future and a repeat of the same platform, key, day and
period. Its output is a count, never a value. A percent is typed in tenths.

**The wall.** A measure is never visible more widely than the display policy of
the field it came from. An entry names that field (`analytics` unless you say
otherwise) and takes the narrower of it and the registry default. An import adds a
floor that the file cannot change: the figures of a YouTube analytics export stay
private (owner decision of 2026-11-19, see [YouTube analytics](youtube-analytics.md)),
so no page or PDF shows them.

**The YouTube analytics export.** The CSV from YouTube Studio's analytics ("Chart
data"): a header naming exactly `Date`, `Views` and `Watch time (hours)`, then one
row per UTC day. A `Total` row is skipped. The importer adds the days up (`views`
and `watch_hours`, hours rounded half up), sets the period to the first and last
day, and writes nothing if any row is bad. It reads one regular file of at most
256 KiB and 1,100 rows, opens no network connection, and never prints a cell. The
column names are assumed from a sample export, not read from a vendor page.

**Stale reminders.** The instance repository example has a scheduled workflow,
`Check hand-kept freshness`, that opens one issue when a hand-kept measure is older
than its limit (30 days) and closes it when everything is fresh again. It reads
only `hand-kept.json`, uses no secret and sends nothing anywhere else. See
[the instance repository README](../examples/instance-repo/README.md).

## Old numbers

A number older than its stale limit (30 days by default; see
[Hand-kept numbers](config.md#hand-kept-numbers)), a date in the future, and a `counts` metric the
numbers file does not hold each produce a build warning. `reach-around build`
prints them as `warning: ...` on standard error and the build still succeeds.
A kit with `features.kit_gate: true` but nothing gated warns that no form is
built, and a gated kit with no `turnstile_site_key` warns about the empty slot.
The kit table also marks an old number `(out of date)` beside its date.

## Lead retention and deletion

The Worker deletes every lead (the address, its country code and its kit
events) once it is older than `LEAD_RETENTION_DAYS`: 30 days by default, 365 at
most (set it with `privacy.lead_retention_days` in `site.yaml`; the deploy passes the
same number to the Worker and the privacy page prints it), run by the hourly job in bounded batches. To honor a person's request to
delete their address sooner, use `reach-around leads delete` (dry run by
default, SQL only with `--yes`, applied by you with `wrangler`). The numbers,
the steps and what is not covered are in [privacy.md](privacy.md).

## The gate routes (Worker)

`POST /k/gate` and `GET /k/view` live in `worker/src/kit/gate.ts`; the gate
form posts to `/k/gate`. They ship off at the cutover through the Worker `KIT_KILL` switch (`worker/src/kit/env.ts`); the build keeps `features.kit_gate` true.

- **Every refusal of a real request is the same answer** (a 303 to
  `/kit/retry/`, no body, no cookie): wrong origin, content type, size, user
  agent, address, limiter, budget, Turnstile, a bad binding. A refused view goes
  to `/kit/`, including when the page itself is missing or the store fails (a
  database read error on the lead or the floor also refuses, never opens): only
  the closed-vocabulary log says which. **Exceptions, stated plainly:** the kill
  switch answers the fixed 503 page for every method; `GET /k/gate` and any
  method but GET or HEAD on `/k/view` get an empty 404; any other path under
  `/k/` never reaches the gate (see below). Nothing before a passed Turnstile
  check writes to the database.
- **At the lead cap the answer does not change.** Once `MAX_LEADS` leads are
  stored, or the per-day new-lead cap (100 a day, a constant in
  `worker/src/kit/gate.ts`) is spent, a new address is unlocked exactly like a
  known one but is not stored, so the visitor cannot tell the two apart and
  invented addresses cannot lock real visitors out. The cookie carries an id
  that looks like a lead id and is marked by a keyed tag only the Worker can
  compute (`worker/src/kit/unstored.ts`). **What you lose at the cap:** those
  addresses are not stored (and so not counted, not purged, not in any export)
  until old leads age out; the unlock alert still reaches your alert sink, and
  the closed health alert `lead_cap_reached` (docs/alerts.md) tells you once a
  day while it is happening. Existing leads behave as before.
- **Single use.** Turnstile alone is not replay protection (a replay can briefly
  succeed), so a passed token is also claimed once in the `counters` table, in
  the bucket of the UTC day of the vendor's `challenge_ts`, which is the same
  whenever the token is replayed, so the claim holds across midnight and
  concurrent requests race on one row. A pass with no usable `challenge_ts`, or
  one more than 10 minutes from the Worker's clock, is refused. These rows and
  the hourly route counters are purged after two days by the hourly job (one
  query, last in the run).
- **Limits, in the order a request meets them.** (1) `GATE_LIMITER`: each client
  has its own gate allowance, 1 per 60 seconds documented, well under the hourly
  budget. The view has its own binding, `KIT_VIEW_LIMITER` (not `VIEW_LIMITER`,
  which is the page-view beacon's), 4 per 60 seconds per client
  documented, so a visitor can reload several times a minute, use the back
  button or return from the inquiry form. At 4 a minute one address is at most
  240 an hour, below `ROUTE_BUDGET_VIEW` (300), so one client alone can never
  spend an hour's view budget; a test pins that the allowance times 60 stays
  below the budget. Two or more clients can spend it (a refusal, never a bill);
  the budget and the edge rule (issue #613) cover that. (2) `GATE_CHECK_LIMITER`: an
  allowance per coarse network (a /24 for IPv4, a /48 for IPv6, hashed with a
  key and a label of its own, never stored) on requests about to reach
  Turnstile, 20 per 60 seconds per network documented, a binding with no
  database write; it caps vendor calls, and one network can use up only its own
  share, so no group of hosts can starve visitors on other networks.
  (3) `ROUTE_BUDGET_GATE` (default 150 an hour) is charged only after the body
  parsed and Turnstile passed, so junk posts cannot spend it. A client over its
  allowance spends no budget. The required bindings are
  `GATE_LIMITER` and `GATE_CHECK_LIMITER` for the gate and `KIT_VIEW_LIMITER`
  for the view; if one is missing that route refuses.
  A rate rule at the edge (issue #613) is another layer in front; the gate does
  not depend on it for correctness.
- **Per-location busy signal (a monitoring hint only).** The optional
  `GATE_WATCH_LIMITER` binding has one shared key and a limit set far above
  expected peak (600 per 60 seconds documented). Cloudflare counts a rate limit
  separately in each of its locations, so this is not an account-wide total:
  traffic spread across locations can exceed the number without raising it, and
  a single regional spike can raise it. When it reports over its limit nothing
  is refused: the request goes on and the owner gets the closed health alert
  `gate_busy`, at most once a day (docs/alerts.md). A missing binding switches
  the signal off.
- **Daily alerts and a down sink.** `lead_cap_reached` and `gate_busy` each
  take the day's claim before sending and give it back when no channel accepted
  the message, so a later request tries again (after the alert dispatcher's own
  10 minute window for identical messages).
- **New-lead admission.** The daily new-lead unit is taken by one conditional
  statement and given back only after a clear result that shows no row was
  created (the cap refused, or a concurrent request for the same address
  created it first). If the database call fails in a way that leaves it unknown
  whether the row was written, the unit is kept. Guaranteed: the day's counter
  is never below the rows created, so the daily cap cannot be exceeded. After
  such a failure the counter can be above the rows (a unit lost for the day),
  never below.
- **Other `/k/` paths** (anything but `/k/gate`, `/k/view` and the inquiry
  routes) never reach the gate: they keep the Worker's every-path-204 answer.
  The plan's table said 404; this is an intentional difference. The empty 404
  above is only for the wrong method on the two gate routes.
- **Session floor.** `leads.session_floor_ts` is in milliseconds (plan 3 Task 15
  says seconds; that is superseded, and migration 0009 is not edited). Only
  `setSessionFloor` in `worker/src/kit/leads.ts` writes it (and
  `setUnstoredSessionFloor` writes the one kit-wide floor for unlocks that stored
  no lead). A cookie issued in a second at or before the floor's second stops
  working; a lead that passes the gate again gets a new cookie. **Nothing in
  this change writes either floor yet**: the revoke task that will call them is
  not built, so until it lands a leaked cookie works for its whole lifetime,
  24 hours by default (`TOKEN_TTL_SECONDS`).
- **Counter growth.** Each solved challenge writes one single-use claim row,
  after Turnstile passes, and the claim and the hourly gate unit are one atomic
  step: a single D1 batch takes the unit (an upsert that only counts below
  `ROUTE_BUDGET_GATE`) and inserts the claim only if the unit was taken. Claim
  rows therefore cannot outnumber the hour's gate budget, however many requests
  arrive at once; at the default 150 an hour that is at most 3,600 claim rows a
  day, for all networks together (so one network can write at most 150 an hour). A token already claimed is found by one read first and spends no unit.
  Two concurrent first uses of one token both take a unit; the second claim
  insert loses the key and its unit is given back at once (if the give-back
  fails the unit stays spent, which only makes the hour stricter). While the
  budget is spent nothing is written. The claim rows are one counters row each
  counted in the write model (`docs/deploy.md`, issue #650: the gate is 5 rows
  for the budgeted operation plus 1 claim row, 3,600 a day at the default), now bounded by the gate budget instead of
  by the allowance in front of Turnstile (`GATE_CHECK_LIMITER`, 20 a minute per
  coarse network), which still limits vendor calls per network. The hourly purge
  deletes at most 500 counter rows a run, and is unchanged.
- **Accepted risk: spending the new-lead cap.** A client that passes Turnstile
  can use plus-tag or invented addresses to spend the per-day new-lead cap (100)
  or `MAX_LEADS`. Visitors are still unlocked; the owner loses storage of later
  addresses until the day or old leads roll over. The `lead_cap_reached` alert
  tells the owner, and the unlock alert still carries each address.
- **Privacy.** The unlock alert carries the visitor's address to the configured
  alert sink. The privacy wording for the gate must be published before the
  gate is turned on.

## Accessibility and privacy

- Landmarks: one `main`, a skip link, a `nav` when there are several tabs, and
  a footer. One `h1` (your name), then `h2` and `h3`; no levels are skipped.
- Every input has a visible label tied to it by `for`/`id`. Tables use
  `th scope`, a hidden caption, and `time` elements for dates.
- Text, borders and buttons read the same `--ra-*` color tokens as the rest of
  the site, so your theme's contrast choices apply. Targets are at least 44 px.
- No inline script, no inline style, no event attributes, no cookie set by the
  static pages, and no third-party request except Turnstile's script on the kit
  and retry pages when a site key is set. The privacy page (generated from
  `features.kit_gate`) covers what the Worker collects once the gate is live.
