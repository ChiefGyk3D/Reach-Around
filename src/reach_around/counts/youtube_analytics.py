# SPDX-License-Identifier: Apache-2.0
"""YouTube Analytics for the channel owner (issue 391). PRIVATE DATA.

The figures are the owner's authorized data. YouTube's developer policy (read
2026-11-19, docs/research/platform-terms-retention.md, III.E.3.b) bars showing
them to anyone but the authorizing user, and the owner decided on 2026-11-19
that they stay private. They go only to the private history file, tagged
``api_provider=youtube_analytics``. Nothing in the site build, kit, editor or
preview may import this module (a boundary test enforces it, and a leak test
builds a public site beside a history file full of these figures).

Every request and response detail lives in the block marked API SPECIFICS, so
it can be corrected in one place. Marks, as read by a researcher on 2026-11-19
from developers.google.com (tool-extracted summaries, not yet reviewed by a
human): VERIFIED-SUMMARY = stated on the vendor page; ASSUMED = not stated on
any page this code could read; UNRESOLVED = the vendor page conflicts itself.

Folding small cells reduces noise; it does NOT stop someone from differencing
consecutive stored 28-day windows to recover a folded cell. That is acceptable
here because the data is owner-private and never published.

The history store keeps one record per metric and UTC day (``skip_days``), so a
later revision of a past day's views is never stored. Data trails by 48 to 72
hours and the window ends 3 days back, so a day is read once it has settled.

Retention: ``counts refresh`` prints a notice with the number of analytics
records past 30 days. Actual removal is the history tombstone tool (#586); until
it is merged the owner must run it by hand once the collector is enabled.

Rules: a failed or empty report stores nothing (a gap, never a zero); a bad row
is skipped and counted (word ``bad_row``) while the other rows count; the
access token is fetched from a refresh token on every run and kept only in
memory; errors carry fixed words and never a token, URL or response text.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import urlencode

from reach_around.config.numbers_sources import (
    YOUTUBE_OAUTH_CLIENT_ID_ENV,
    YOUTUBE_OAUTH_CLIENT_SECRET_ENV,
    YOUTUBE_OAUTH_REFRESH_TOKEN_ENV,
    YoutubeSource,
)
from reach_around.counts.history import MAX_VALUE, Record
from reach_around.net.fetch import FetchError

Fetch = Callable[..., dict[str, object]]

# ---------------------------------------------------------------- API SPECIFICS
# VERIFIED-SUMMARY: GET reports.query on this host and path; query parameters
# ids=channel==MINE, startDate, endDate (YYYY-MM-DD), metrics, dimensions,
# sort (a leading minus sorts descending), maxResults.
REPORTS_HOST = "youtubeanalytics.googleapis.com"
REPORTS_URL = f"https://{REPORTS_HOST}/v2/reports"
IDS = "channel==MINE"
# ASSUMED: the Google OAuth 2.0 token endpoint and its refresh grant (standard
# OAuth 2.0 refresh_token grant; not re-read on a vendor page for this task).
TOKEN_HOST = "oauth2.googleapis.com"  # noqa: S105 (a host name)
TOKEN_URL = f"https://{TOKEN_HOST}/token"
# UNRESOLVED: yt-analytics.readonly is the least-privilege scope for
# non-monetary metrics, but the same vendor page also says the method needs
# youtube.readonly. The owner grants the scopes at consent time; the refresh
# grant does not send them, so this list is documentation and a doc-test target.
SCOPES = ("https://www.googleapis.com/auth/yt-analytics.readonly",)
SCOPES_ALSO_MAYBE_NEEDED = ("https://www.googleapis.com/auth/youtube.readonly",)
# VERIFIED-SUMMARY: daily series is dimensions=day with these metrics.
DAILY_DIMENSIONS = ("day",)
DAILY_METRICS = ("views", "estimatedMinutesWatched")
# VERIFIED-SUMMARY: demographics are dimensions ageGroup,gender with metric
# viewerPercentage. Country is a filter there, not a dimension.
DEMO_DIMENSIONS = ("ageGroup", "gender")
DEMO_METRICS = ("viewerPercentage",)
# VERIFIED-SUMMARY: per-country numbers are a separate report, dimensions=country.
GEO_DIMENSIONS = ("country",)
GEO_METRICS = ("views",)
GEO_SORT = "-views"
# ASSUMED: the reference lists maxResults and startIndex (VERIFIED-SUMMARY); the
# largest page the API accepts for this report is NOT known, and startIndex being
# 1-based is ASSUMED. A refused page size shows as an http_4xx word.
GEO_PAGE_SIZE = 100
GEO_MAX_PAGES = 3  # a hard cap: at most 300 countries are read per run
GEO_NAMED_MAX = 25  # countries stored by name; everything else is summed
# ASSUMED: the documented ageGroup values; any other label is a bad row.
AGE_LABELS = {
    "age13-17": "a13_17",
    "age18-24": "a18_24",
    "age25-34": "a25_34",
    "age35-44": "a35_44",
    "age45-54": "a45_54",
    "age55-64": "a55_64",
    "age65-": "a65_up",
}
# VERIFIED-SUMMARY: data trails by 48 to 72 hours, so the last day asked for is
# 3 days before today (UTC).
DATA_LAG_DAYS = 3
# ASSUMED: window lengths are this project's choice.
DAILY_WINDOW_DAYS = 7
DEMO_WINDOW_DAYS = 28
# ------------------------------------------------------------ end API SPECIFICS

# Noise floors, NOT a privacy guarantee. ASSUMED values (the vendor's own
# thresholds are unpublished). A demographic cell under this share is summed
# into ``demo_other``; a country under this many views is summed into
# ``geo_other_views``. The demographic floor is on a share, not on a viewer
# count, and the daily series has no floor. Because consecutive stored windows
# overlap, the change in an "other" total across days can still reveal a folded
# cell. That is acceptable only because the data is private and never published.
DEMO_FLOOR_BP = 200  # 2.00 percent, in hundredths of a percent
GEO_FLOOR_VIEWS = 100

# Policy (docs/research/platform-terms-retention.md, owner decision 2026-11-19):
# stored authorized data is refreshed or deleted after 30 days.
RETAIN_DAYS = 30
API_PROVIDER = "youtube_analytics"

MAX_ROWS = 1000
MAX_TOKEN_LEN = 2048
# Output limits, checked before any record is built. One history append takes at
# most MAX_BATCH (200) records; a report over its limit fails alone (too_large).
DAILY_MAX_FIGURES = 2 * DAILY_WINDOW_DAYS
DEMO_MAX_FIGURES = len(AGE_LABELS) * 3 + 1  # every age x gender, plus demo_other
GEO_MAX_FIGURES = GEO_NAMED_MAX + 1  # named countries plus geo_other_views
MAX_RUN_FIGURES = DAILY_MAX_FIGURES + DEMO_MAX_FIGURES + GEO_MAX_FIGURES
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")  # used with fullmatch
_COUNTRY = re.compile(r"[A-Z]{2}", re.ASCII)  # used with fullmatch
_GENDER = {"female": "f", "male": "m", "user_specified": "u"}
_HTTP = re.compile(r"^http_[45][0-9]{2}$")
_PASS_KINDS = frozenset(
    {
        "rate_limited",
        "server_error",
        "redirect",
        "too_large",
        "bad_json",
        "content_type",
    }
)


class YoutubeAnalyticsError(Exception):
    """Fixed-word failure; same ``kind`` contract as the other collectors."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(kind)


@dataclass(frozen=True)
class Measure:
    """One whole-number figure. ``as_of_utc`` is set for a figure about a past
    day; ``None`` means the run time."""

    metric: str
    value: int
    note: str
    as_of_utc: str | None = field(default=None)


def _secret(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value:
        raise YoutubeAnalyticsError("missing_credential")
    if len(value) > MAX_TOKEN_LEN or any(
        not ch.isprintable() or ch.isspace() for ch in value
    ):
        raise YoutubeAnalyticsError("invalid_credential")
    return value


def _whole(value: object) -> int:
    if isinstance(value, bool):
        raise YoutubeAnalyticsError("bad_report")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int):
        raise YoutubeAnalyticsError("bad_report")
    if not 0 <= value <= MAX_VALUE:
        raise YoutubeAnalyticsError("bad_aggregate")
    return value


def _basis_points(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise YoutubeAnalyticsError("bad_report")
    if not 0 <= value <= 100:  # also false for NaN
        raise YoutubeAnalyticsError("bad_aggregate")
    return round(value * 100)


def _table(answer: dict[str, object], names: tuple[str, ...]) -> list[list[object]]:
    """Rows of a report whose column headers are exactly ``names``.

    No ``rows`` key (or an empty list) is an empty table, never an error here.
    """
    headers = answer.get("columnHeaders")
    got = (
        [h.get("name") for h in headers if isinstance(h, dict)]
        if isinstance(headers, list)
        else None
    )
    if got is None or got != list(names):
        raise YoutubeAnalyticsError("bad_report")
    rows = answer.get("rows", [])
    if not isinstance(rows, list):
        raise YoutubeAnalyticsError("bad_report")
    if len(rows) > MAX_ROWS:
        raise YoutubeAnalyticsError("too_large")
    out: list[list[object]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) != len(names):
            raise YoutubeAnalyticsError("bad_report")
        out.append(row)
    return out


class _Session:
    def __init__(self, env: Mapping[str, str], fetch: Fetch) -> None:
        self.fetch = fetch
        client_id = _secret(env, YOUTUBE_OAUTH_CLIENT_ID_ENV)
        client_secret = _secret(env, YOUTUBE_OAUTH_CLIENT_SECRET_ENV)
        refresh = _secret(env, YOUTUBE_OAUTH_REFRESH_TOKEN_ENV)
        self.access = self._exchange(client_id, client_secret, refresh)

    def _exchange(self, client_id: str, client_secret: str, refresh: str) -> str:
        form = urlencode(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            }
        ).encode("ascii")
        token: str | None = None
        try:
            answer = self.fetch(
                TOKEN_URL,
                method="POST",
                body=form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                follow_redirects=False,
            )
            value = answer.get("access_token")
            if (
                isinstance(value, str)
                and value
                and len(value) <= MAX_TOKEN_LEN
                and all(ch.isprintable() and not ch.isspace() for ch in value)
            ):
                token = value
        except Exception:  # noqa: S110 (the failure word is raised below, unchained)
            token = None
        # A rejected refresh (revoked, expired, Testing-status 7-day token) needs
        # the owner to authorize again; the word is all that is reported.
        if token is None:
            raise YoutubeAnalyticsError("refresh_failed")
        return token

    def report(
        self,
        start: date,
        end: date,
        dimensions: tuple[str, ...],
        metrics: tuple[str, ...],
        *,
        sort: str | None = None,
        max_results: int | None = None,
        start_index: int | None = None,
    ) -> dict[str, object]:
        params = {
            "ids": IDS,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": ",".join(dimensions),
            "metrics": ",".join(metrics),
        }
        if sort is not None:
            params["sort"] = sort
        if max_results is not None:
            params["maxResults"] = str(max_results)
        if start_index is not None:
            params["startIndex"] = str(start_index)
        return self.fetch(
            REPORTS_URL + "?" + urlencode(params),
            headers={"Authorization": f"Bearer {self.access}"},
            follow_redirects=False,
        )


Report = tuple[list[Measure], int]  # figures, and how many rows were skipped


def _daily(session: _Session, first: date, last: date) -> Report:
    names = (*DAILY_DIMENSIONS, *DAILY_METRICS)
    rows = _table(session.report(first, last, DAILY_DIMENSIONS, DAILY_METRICS), names)
    if not rows:
        raise YoutubeAnalyticsError("no_data")  # not a zero: nothing is stored
    seen: set[str] = set()
    out: list[Measure] = []
    skipped = 0
    for day, views, minutes in rows:
        # A row that is not a plain ASCII day inside the window, or whose numbers
        # are bad, is skipped and counted; the other rows still count.
        if not isinstance(day, str) or not _DAY.fullmatch(day):
            skipped += 1
            continue
        if not first.isoformat() <= day <= last.isoformat():
            skipped += 1
            continue
        if day in seen:  # two answers for one day: trust neither
            raise YoutubeAnalyticsError("bad_report")
        seen.add(day)
        try:
            pair = (_whole(views), _whole(minutes))
        except YoutubeAnalyticsError:
            skipped += 1
            continue
        stamp = f"{day}T00:00:00Z"
        out.append(Measure("views_1d", pair[0], f"views on UTC day {day}", stamp))
        out.append(
            Measure(
                "watch_minutes_1d",
                pair[1],
                f"estimated minutes watched on UTC day {day}",
                stamp,
            )
        )
    return out, skipped


def _demographics(session: _Session, first: date, last: date) -> Report:
    names = (*DEMO_DIMENSIONS, *DEMO_METRICS)
    rows = _table(session.report(first, last, DEMO_DIMENSIONS, DEMO_METRICS), names)
    cells: dict[str, int] = {}
    folded = 0
    skipped = 0
    seen: set[tuple[object, object]] = set()
    for age, gender, percent in rows:
        key = (age, gender)
        if key in seen:
            raise YoutubeAnalyticsError("bad_report")
        seen.add(key)
        # Only the documented labels count; any other label is a bad row (it is
        # not folded into demo_other, so an odd answer cannot grow the output).
        label_age = AGE_LABELS.get(age) if isinstance(age, str) else None
        label_sex = _GENDER.get(gender) if isinstance(gender, str) else None
        if label_age is None or label_sex is None:
            skipped += 1
            continue
        try:
            bp = _basis_points(percent)
        except YoutubeAnalyticsError:
            skipped += 1
            continue
        if bp < DEMO_FLOOR_BP:
            folded += bp
        else:
            cells[f"demo_{label_age}_{label_sex}"] = bp
    window = f"{first.isoformat()} to {last.isoformat()}"
    note = (
        f"viewer share x100 (4025 = 40.25 percent), UTC {window}; cells under "
        f"{DEMO_FLOOR_BP / 100:.2f} percent are summed into demo_other"
    )
    out = [Measure(metric, value, note) for metric, value in sorted(cells.items())]
    if folded:
        out.append(Measure("demo_other", folded, note))
    return out, skipped


def _geography(session: _Session, first: date, last: date) -> Report:
    names = (*GEO_DIMENSIONS, *GEO_METRICS)
    # Bounded pagination: at most GEO_MAX_PAGES requests of GEO_PAGE_SIZE rows. A
    # page longer than asked for is refused (the answer cannot be trusted).
    rows: list[list[object]] = []
    start = 1
    for _ in range(GEO_MAX_PAGES):
        page = _table(
            session.report(
                first,
                last,
                GEO_DIMENSIONS,
                GEO_METRICS,
                sort=GEO_SORT,
                max_results=GEO_PAGE_SIZE,
                start_index=start,
            ),
            names,
        )
        if len(page) > GEO_PAGE_SIZE:
            raise YoutubeAnalyticsError("too_large")
        rows.extend(page)
        if len(page) < GEO_PAGE_SIZE:
            break
        start += GEO_PAGE_SIZE
    counts: dict[str, int] = {}
    folded = 0
    seen: set[object] = set()
    skipped = 0
    for country, views in rows:
        if country in seen:
            raise YoutubeAnalyticsError("bad_report")
        seen.add(country)
        try:
            count = _whole(views)
        except YoutubeAnalyticsError:
            skipped += 1
            continue
        if (
            not isinstance(country, str)
            or not _COUNTRY.fullmatch(country)
            or country == "ZZ"  # unknown territory
            or count < GEO_FLOOR_VIEWS
        ):
            folded += count
        else:
            counts[country] = count
    # Do not trust the vendor's order: the largest GEO_NAMED_MAX countries are
    # named, every other country is summed.
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    cells = {
        f"geo_{country.lower()}_views": count
        for country, count in ranked[:GEO_NAMED_MAX]
    }
    folded += sum(count for _, count in ranked[GEO_NAMED_MAX:])
    window = f"{first.isoformat()} to {last.isoformat()}"
    note = (
        f"views by country, UTC {window}; the {GEO_NAMED_MAX} largest countries "
        f"with at least {GEO_FLOOR_VIEWS} views are named, every other country read "
        f"(up to {GEO_MAX_PAGES * GEO_PAGE_SIZE}) is summed into geo_other_views"
    )
    if folded > MAX_VALUE:  # refused, never clamped: a clamped total is wrong
        raise YoutubeAnalyticsError("bad_aggregate")
    out = [Measure(metric, value, note) for metric, value in sorted(cells.items())]
    if folded:
        out.append(Measure("geo_other_views", folded, note))
    return out, skipped


def _word(exc: Exception) -> str:
    kind = getattr(exc, "kind", "")
    if not isinstance(kind, str):
        return "fetch_failed"
    if isinstance(exc, FetchError):
        if kind == "http_401":
            return "token_rejected"
        if kind == "http_403":
            return "scope_missing"
        if kind in _PASS_KINDS or _HTTP.match(kind):
            return kind
        return "fetch_failed"
    if isinstance(exc, YoutubeAnalyticsError):
        return kind
    return "fetch_failed"


def collect_youtube(
    source: YoutubeSource,
    env: Mapping[str, str],
    fetch: Fetch,
    today: date,
) -> tuple[list[Measure], list[str]]:
    """Figures plus the fixed failure word of each report that failed.

    Raises ``YoutubeAnalyticsError`` when nothing could be attempted (setting,
    credential or refresh problem). One failed report never discards another.
    """
    if getattr(source, "analytics", False) is not True:
        raise YoutubeAnalyticsError("bad_source_setting")
    # Setup raises only YoutubeAnalyticsError: a missing or bad credential, or a
    # refresh that failed (every other exception is turned into that word).
    session = _Session(env, fetch)
    last = today - timedelta(days=DATA_LAG_DAYS)
    # (report, window in days, most figures it may give). The limits are read
    # here, at call time, and checked before anything is kept.
    reports: tuple[tuple[Callable[[_Session, date, date], Report], int, int], ...] = (
        (_daily, DAILY_WINDOW_DAYS, DAILY_MAX_FIGURES),
        (_demographics, DEMO_WINDOW_DAYS, DEMO_MAX_FIGURES),
        (_geography, DEMO_WINDOW_DAYS, GEO_MAX_FIGURES),
    )
    measures: list[Measure] = []
    words: list[str] = []
    for run, days, limit in reports:
        word = ""
        try:
            got, skipped = run(session, last - timedelta(days=days - 1), last)
            if len(got) > limit or len(measures) + len(got) > MAX_RUN_FIGURES:
                raise YoutubeAnalyticsError("too_large")
            measures.extend(got)
            if skipped:
                word = "bad_row"
        except Exception as exc:
            word = _word(exc)
        if word:
            words.append(word)
    return measures, words


def deletion_candidates(
    records: list[Record], today: date, *, revoked: bool = False
) -> list[Record]:
    """The collected analytics records that must be redacted or deleted.

    A STUB for the deletion path: it only SELECTS records; it changes nothing.
    The redaction itself is the tombstone record of #586 (not built here), and
    because git history keeps old values, a true deletion needs the owner-
    approved rewrite described in docs/research/platform-terms-retention.md.

    Policy, as docs/research/platform-terms-retention.md records it (YouTube
    Developer Policies, read 2026-11-19): refresh or delete API data after 30
    calendar days (III.E.4, III.E.4.c); delete within 7 days of a trigger
    (III.E.4.g); delete within 30 days of user revocation (III.D.2).
    UNVERIFIED beyond that note: any finer reading of what counts as a trigger;
    a human must check the vendor page. ``revoked`` selects every analytics
    record. Without it, only records older than ``RETAIN_DAYS`` are selected (the
    owner's 2026-11-19 refresh-or-delete rule). A correction that carries an
    analytics value is selected like the record it fixes. Public-data records
    (the Data API subscriber count) are never selected here.
    """
    cutoff = today - timedelta(days=RETAIN_DAYS)
    out = []
    for record in records:
        if record.api_provider != API_PROVIDER:
            continue
        if revoked or record.as_of_utc[:10] < cutoff.isoformat():
            out.append(record)
    return out
