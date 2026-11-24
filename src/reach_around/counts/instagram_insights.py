# SPDX-License-Identifier: Apache-2.0
"""Instagram insights for the account owner (issue 393). PRIVATE DATA.

The figures are the owner's authorized Meta Platform Data. The Meta terms
(docs/research/platform-terms-retention.md) bar selling or licensing it, and the
owner decided on 2026-11-19 that authorized API rows are never shown on a public
page. They go only to the private history file, tagged
``api_provider=instagram_insights``. Nothing in the site build, kit, editor or
preview may import this module (a boundary test enforces it, and a leak test
builds a public site beside a history file full of these figures).

Pattern: the same as youtube_analytics.py (#391): an explicit provider tag set by
the collector, strict per-report figure limits checked before anything is kept,
one report failing alone with a fixed word, no zero for a failure.

Sources read on 2026-10-23 (docs/research/instagram.md, a reviewed research
note; no live call has been made, #552): the insights route is
``GET /<IG_ID>/insights`` with ``metric``, ``period=day`` and ``metric_type``;
account metrics named there are reach, views, total_interactions, accounts_engaged
and profile_links_taps; ``follower_demographics`` needs a ``timeframe`` and
breakdowns age, city, country and gender (top 45), and needs 100 followers; empty
answers are an empty set, not 0; data can lag up to 48 hours. EVERYTHING ELSE
below is marked UNVERIFIED and is tied to #552 (live verification by the owner):

* UNVERIFIED: that ``metric_type=total_value`` with ``since``/``until`` is
  accepted for the five account metrics, that ``reach`` over a window is a unique
  count (it is NOT additive across windows), and the exact answer shape
  (``data[].total_value.value``).
* UNVERIFIED: the answer shape of ``follower_demographics`` (``total_value.
  breakdowns[].dimension_keys`` and ``results[].dimension_values``/``value``),
  the label spellings (age ``13-17`` ... ``65+``, gender ``F``/``M``/``U``,
  ISO country code), and the accepted ``timeframe`` value (the note records that
  some timeframe values are deprecated).
* UNVERIFIED: the Instagram-Login route with a numeric account id (the note
  names ``<IG_ID>``; ``me`` is not used here for insights).
* "Profile visits" is not in the note's metric table, so it is not collected.
* UNVERIFIED: whether these metrics need a business account, a follower minimum
  (100 followers for demographics is in the note; the exact thresholds for other
  metrics are not) or the ``instagram_business_manage_insights`` permission.

The token is the same ``INSTAGRAM_ACCESS_TOKEN`` the follower read uses. The
vendor pages show it only as the ``access_token`` query value, so it is in the
URL (like meta_user.py): nothing the fetch layer raises may travel upward, errors
carry fixed words only, and no redirect is followed. Every call is a GET (read
only, safe to retry within the fetch layer's bound); nothing here mutates.

Floors: a demographic cell (a count of followers) under ``FLOOR`` (5) is summed
into the report's ``other`` figure, as report.md's privacy floor of 5 expects.
Folding reduces noise; it does NOT stop differencing between stored windows. The
data is owner-private, which is the only reason that is acceptable. Account
activity totals are stored exactly; the display work (#397) applies its floor.

Retention: ``counts refresh`` prints a notice with the number of these records
past 30 days. The 30-day rule is the owner's YouTube decision applied here as a
conservative choice; Meta states no day count (the Meta terms say delete when no
longer necessary or on request), so the figure is an owner-confirmable setting.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from reach_around.config.numbers_sources import InstagramSource
from reach_around.counts.history import MAX_VALUE, Record
from reach_around.counts.meta_user import (
    INSTAGRAM_VERSION,
    HealthAlert,
    MetaError,
    NewToken,
    _Session,
    check_source,
    credential_name,
)
from reach_around.net.fetch import FetchError

Fetch = Callable[..., dict[str, object]]

# ---------------------------------------------------------------- API SPECIFICS
INSIGHTS_PATH = "/insights"
# The route is /<version>/<IG_ID>/insights on INSTAGRAM_HOST (UNVERIFIED, #552).
# VERIFIED-NOTE: the account metrics the research note lists. Mapped to the
# stored metric name; each is a total over the window.
ACTIVITY_METRICS = {
    "reach": "reach_7d",
    "views": "views_7d",
    "total_interactions": "interactions_7d",
    "accounts_engaged": "engaged_7d",
    "profile_links_taps": "link_taps_7d",
}
DEMOGRAPHICS_METRIC = "follower_demographics"
# UNVERIFIED: the timeframe value (the note: required, some values deprecated).
TIMEFRAME = "last_30_days"
# UNVERIFIED: label spellings of the breakdown answers.
AGE_LABELS = {
    "13-17": "13_17",
    "18-24": "18_24",
    "25-34": "25_34",
    "35-44": "35_44",
    "45-54": "45_54",
    "55-64": "55_64",
    "65+": "65_up",
}
GENDER_LABELS = {"F": "f", "M": "m", "U": "u"}
# VERIFIED-NOTE: data lags up to 48 hours, so the last day asked for is 3 days
# before today (UTC). ASSUMED: the window length is this project's choice.
DATA_LAG_DAYS = 3
ACTIVITY_WINDOW_DAYS = 7
# ------------------------------------------------------------ end API SPECIFICS

FLOOR = 5  # a follower cell under this is summed into ``other``
COUNTRY_NAMED_MAX = 25  # countries stored by name; the rest are summed
RETAIN_DAYS = 30
API_PROVIDER = "instagram_insights"

MAX_ROWS = 100  # the note says top 45; a longer list is refused whole
MAX_ITEMS = 20  # entries in ``data``
# Output limits, checked before any record is built.
ACTIVITY_MAX_FIGURES = len(ACTIVITY_METRICS)
AGE_MAX_FIGURES = len(AGE_LABELS) + 1  # every age group, plus other
GENDER_MAX_FIGURES = len(GENDER_LABELS) + 1
COUNTRY_MAX_FIGURES = COUNTRY_NAMED_MAX + 1
MAX_RUN_FIGURES = (
    ACTIVITY_MAX_FIGURES + AGE_MAX_FIGURES + GENDER_MAX_FIGURES + COUNTRY_MAX_FIGURES
)
_COUNTRY = re.compile(r"[A-Z]{2}", re.ASCII)  # used with fullmatch
_USER_ID = re.compile(r"[0-9]{1,24}", re.ASCII)  # used with fullmatch
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


class InstagramInsightsError(Exception):
    """Fixed-word failure; same ``kind`` contract as the other collectors."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(kind)


@dataclass(frozen=True)
class Measure:
    """One whole-number figure. ``as_of_utc`` ``None`` means the run time."""

    metric: str
    value: int
    note: str
    as_of_utc: str | None = field(default=None)


class _SilentSink:
    """The follower read already alerts about the token; this one stays quiet."""

    def store(self, token: NewToken, name: str, source: str, expires_on: date) -> bool:
        return False  # nothing was kept

    def alert(self, alert: HealthAlert) -> None:
        return None


def _whole(value: object) -> int:
    if type(value) is not int:  # bool, float and str are all refused
        raise InstagramInsightsError("bad_report")
    if not 0 <= value <= MAX_VALUE:
        raise InstagramInsightsError("bad_aggregate")
    return value


def _data(answer: dict[str, object]) -> list[object]:
    data = answer.get("data")
    if type(data) is not list:
        raise InstagramInsightsError("bad_report")
    if len(data) > MAX_ITEMS:
        raise InstagramInsightsError("too_large")
    return data


class _Reader:
    def __init__(self, source: InstagramSource, env: Mapping[str, str], fetch: Fetch):
        self.path = f"/{INSTAGRAM_VERSION}/{source.user_id}{INSIGHTS_PATH}"
        # The token checks happen inside _Session. The sink and the day are unused:
        # the token is never refreshed here (the follower read owns that).
        self.session = _Session(source, env, fetch, _SilentSink(), date.min)

    def get(self, params: Mapping[str, str]) -> dict[str, object]:
        return self.session.get(self.path, params)


Report = tuple[list[Measure], int]  # figures, and how many rows were skipped


def _activity(reader: _Reader, first: date, last: date) -> Report:
    since = datetime.combine(first, time(0), tzinfo=UTC)
    until = datetime.combine(last + timedelta(days=1), time(0), tzinfo=UTC)
    answer = reader.get(
        {
            "metric": ",".join(ACTIVITY_METRICS),
            "period": "day",
            "metric_type": "total_value",
            "since": str(int(since.timestamp())),
            "until": str(int(until.timestamp())),
        }
    )
    data = _data(answer)
    if not data:
        raise InstagramInsightsError("no_data")  # not a zero: nothing is stored
    window = f"UTC {first.isoformat()} to {last.isoformat()}"
    seen: set[str] = set()
    out: list[Measure] = []
    skipped = 0
    for item in data:
        if type(item) is not dict:
            raise InstagramInsightsError("bad_report")
        name = item.get("name")
        if type(name) is not str:
            raise InstagramInsightsError("bad_report")
        if name in seen:
            raise InstagramInsightsError("bad_report")
        seen.add(name)
        metric = ACTIVITY_METRICS.get(name)
        if metric is None:
            continue  # a metric that was not asked for is ignored
        box = item.get("total_value")
        try:
            if type(box) is not dict:
                raise InstagramInsightsError("bad_report")
            value = _whole(box.get("value"))
        except InstagramInsightsError:
            skipped += 1
            continue
        note = (
            f"{name} total over {window} as the vendor defines it (reach is a "
            "unique count, not a sum of days; UNVERIFIED, #552)"
        )
        out.append(Measure(metric, value, note))
    if not set(ACTIVITY_METRICS) <= seen:
        # A reply that leaves out a requested metric is not a full report.
        raise InstagramInsightsError("missing_field")
    out.sort(key=lambda m: m.metric)
    return out, skipped


def _results(answer: dict[str, object], dim: str) -> list[object]:
    """The ``results`` of the one breakdown named ``dim``; empty when absent."""
    data = _data(answer)
    if not data:
        return []
    items = [
        d for d in data if type(d) is dict and d.get("name") == DEMOGRAPHICS_METRIC
    ]
    if len(items) != 1:
        raise InstagramInsightsError("bad_report")
    box = items[0].get("total_value")
    if type(box) is not dict:
        raise InstagramInsightsError("bad_report")
    breakdowns = box.get("breakdowns")
    if breakdowns is None:
        return []
    if type(breakdowns) is not list or len(breakdowns) > MAX_ITEMS:
        raise InstagramInsightsError("bad_report")
    if not breakdowns:
        return []
    mine = [
        b for b in breakdowns if type(b) is dict and b.get("dimension_keys") == [dim]
    ]
    if len(mine) != 1:
        raise InstagramInsightsError("bad_report")
    results = mine[0].get("results", [])
    if type(results) is not list:
        raise InstagramInsightsError("bad_report")
    if len(results) > MAX_ROWS:
        raise InstagramInsightsError("too_large")
    return results


def _demographic(reader: _Reader, dim: str, first: date, last: date) -> Report:
    answer = reader.get(
        {
            "metric": DEMOGRAPHICS_METRIC,
            "period": "lifetime",
            "metric_type": "total_value",
            "timeframe": TIMEFRAME,
            "breakdown": dim,
        }
    )
    cells: dict[str, int] = {}
    folded = 0
    skipped = 0
    seen: set[object] = set()
    for row in _results(answer, dim):
        if type(row) is not dict:
            raise InstagramInsightsError("bad_report")
        labels = row.get("dimension_values")
        label = labels[0] if type(labels) is list and len(labels) == 1 else None
        if type(label) is str and label in seen:
            raise InstagramInsightsError("bad_report")
        if type(label) is str:
            seen.add(label)
        try:
            count = _whole(row.get("value"))
        except InstagramInsightsError:
            skipped += 1
            continue
        if dim == "country":
            if type(label) is not str:
                skipped += 1
                continue
            if not _COUNTRY.fullmatch(label) or label == "ZZ" or count < FLOOR:
                folded += count
            else:
                cells[label.lower()] = count
            continue
        table = AGE_LABELS if dim == "age" else GENDER_LABELS
        key = table.get(label) if type(label) is str else None
        if key is None:  # only the documented labels count; others are bad rows
            skipped += 1
        elif count < FLOOR:
            folded += count
        else:
            cells[key] = count
    # Do not trust the vendor's order: the largest countries are named.
    ranked = sorted(cells.items(), key=lambda item: (-item[1], item[0]))
    if dim == "country":
        folded += sum(count for _, count in ranked[COUNTRY_NAMED_MAX:])
        ranked = ranked[:COUNTRY_NAMED_MAX]
    if folded > MAX_VALUE:  # refused, never clamped: a clamped total is wrong
        raise InstagramInsightsError("bad_aggregate")
    note = (
        f"followers by {dim} (timeframe {TIMEFRAME}, vendor-defined; UNVERIFIED, "
        f"#552); cells under {FLOOR} are summed into fdemo_{dim}_other"
    )
    out = [Measure(f"fdemo_{dim}_{key}", count, note) for key, count in sorted(ranked)]
    if folded:
        out.append(Measure(f"fdemo_{dim}_other", folded, note))
    return out, skipped


def _word(exc: Exception) -> str:
    kind = getattr(exc, "kind", "")
    if type(kind) is not str:
        return "fetch_failed"
    if isinstance(exc, FetchError):
        if kind == "http_401":
            return "token_rejected"
        if kind == "http_403":
            return "scope_missing"
        if kind in _PASS_KINDS or _HTTP.match(kind):
            return kind
        return "fetch_failed"
    if isinstance(exc, InstagramInsightsError | MetaError):
        return kind
    return "fetch_failed"


def _rotation_possible(source: InstagramSource, today: date) -> bool:
    """True when the follower read may refresh the token in this run (the same
    window ``meta_user`` uses: expiry known and within ``refresh_within_days``)."""
    expires = getattr(source, "token_expires", None)
    within = getattr(source, "refresh_within_days", None)
    if type(expires) is not date or type(within) is not int:
        return False
    return (expires - today).days <= within


def _check(source: InstagramSource, today: date) -> None:
    """Use-time re-check; ``model_copy`` and ``model_construct`` skip validation."""
    # The kind picks the credential (credential_name), so it is checked before any
    # environment read: a forged source of another kind must never send another
    # provider's token to the Instagram host.
    if getattr(source, "kind", None) != "instagram":
        raise InstagramInsightsError("bad_source_setting")
    if getattr(source, "analytics", False) is not True:
        raise InstagramInsightsError("bad_source_setting")
    if getattr(source, "auto_refresh", False) is True and _rotation_possible(
        source, today
    ):
        # The follower read of this run may rotate the token through the Meta
        # sink; this collector reads the original environment and would send a
        # token that was just replaced. Skip this run's insights instead.
        raise InstagramInsightsError("refresh_pending")
    uid = getattr(source, "user_id", None)
    if type(uid) is not str or not _USER_ID.fullmatch(uid):
        raise InstagramInsightsError("bad_source_setting")
    try:
        credential_name(source)
        check_source(source)
    except MetaError as exc:
        raise InstagramInsightsError(exc.kind) from None


def collect_instagram(
    source: InstagramSource,
    env: Mapping[str, str],
    fetch: Fetch,
    today: date,
) -> tuple[list[Measure], list[str]]:
    """Figures plus the fixed failure word of each report that failed.

    Raises ``InstagramInsightsError`` when nothing could be attempted (setting or
    credential problem). One failed report never discards another.
    """
    _check(source, today)
    try:
        reader = _Reader(source, env, fetch)
    except MetaError as exc:
        raise InstagramInsightsError(exc.kind) from None
    last = today - timedelta(days=DATA_LAG_DAYS)
    first = last - timedelta(days=ACTIVITY_WINDOW_DAYS - 1)
    # (report, most figures it may give). Limits are read at call time and
    # checked before anything is kept.
    reports: tuple[tuple[Callable[[], Report], int], ...] = (
        (lambda: _activity(reader, first, last), ACTIVITY_MAX_FIGURES),
        (lambda: _demographic(reader, "age", first, last), AGE_MAX_FIGURES),
        (lambda: _demographic(reader, "gender", first, last), GENDER_MAX_FIGURES),
        (lambda: _demographic(reader, "country", first, last), COUNTRY_MAX_FIGURES),
    )
    measures: list[Measure] = []
    words: list[str] = []
    for run, limit in reports:
        word = ""
        try:
            got, skipped = run()
            if len(got) > limit or len(measures) + len(got) > MAX_RUN_FIGURES:
                raise InstagramInsightsError("too_large")
            measures.extend(got)
            if skipped:
                word = "bad_row"
        except Exception as exc:
            word = _word(exc)
        if word:
            words.append(word)
        if word in ("token_rejected", "scope_missing"):
            break  # a rejected credential: do not send the remaining reports
    return measures, words


def deletion_candidates(
    records: list[Record], today: date, *, revoked: bool = False
) -> list[Record]:
    """The collected Instagram insights records that must be redacted or deleted.

    Only SELECTS; the redaction itself is the tombstone record (#586). Without
    ``revoked`` only records older than ``RETAIN_DAYS`` are chosen (ASSUMED
    policy, see the module note); ``revoked`` (a user request, an app or token
    revocation) chooses every one. A correction is chosen with the record it
    fixes even when written without the tag (a file from older code).
    """
    cutoff = today - timedelta(days=RETAIN_DAYS)
    tagged_keys = {r.key for r in records if r.api_provider == API_PROVIDER}
    out = []
    for record in records:
        if record.api_provider != API_PROVIDER and not (
            record.type == "correction" and record.key in tagged_keys
        ):
            continue
        if revoked or record.as_of_utc[:10] < cutoff.isoformat():
            out.append(record)
    return out
