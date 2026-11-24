# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: S105
"""YouTube Analytics collector (#391). Recorded fake responses only.

Every fixture under tests/fixtures/youtube_analytics is SYNTHETIC: written by
hand from the response shape the vendor docs describe (researcher read,
2026-11-19). No network, no live API, no real credential. Tokens are sentinels.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

import reach_around.commands.counts as counts_cmd
from fakenet import FakeTransport, reply
from reach_around.cli import main
from reach_around.config.numbers_sources import YoutubeSource
from reach_around.counts.history import (
    MAX_BATCH,
    History,
    Record,
    append_records,
    read_history,
)
from reach_around.counts.insights import collect
from reach_around.counts.youtube_analytics import (
    DEMO_MAX_FIGURES,
    GEO_MAX_PAGES,
    GEO_NAMED_MAX,
    GEO_PAGE_SIZE,
    MAX_ROWS,
    MAX_RUN_FIGURES,
    REPORTS_URL,
    RETAIN_DAYS,
    TOKEN_URL,
    YoutubeAnalyticsError,
    _basis_points,
    collect_youtube,
    deletion_candidates,
)
from reach_around.funnel.social import build_social
from reach_around.net.fetch import fetch_json
from test_counts_insights import violations

FIX = Path(__file__).parent / "fixtures" / "youtube_analytics"
TODAY = date(2026, 11, 19)
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
ENV = {
    "YOUTUBE_OAUTH_CLIENT_ID": "SENTINEL-YT-CLIENT-ID",
    "YOUTUBE_OAUTH_CLIENT_SECRET": "SENTINEL-YT-CLIENT-SECRET",
    "YOUTUBE_OAUTH_REFRESH_TOKEN": "SENTINEL-YT-REFRESH",
}
SECRETS = [*ENV.values(), "SENTINEL-YT-ACCESS-FRESH"]
CHANNEL = "UC" + "a" * 22
SRC = YoutubeSource.model_validate(
    {"platform": "youtube", "kind": "youtube", "channel_id": CHANNEL, "analytics": True}
)


def fx(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIX / name).read_text("utf-8"))
    return data


def router(
    *,
    daily: Any = None,
    demo: Any = None,
    geo: Any = None,
    token: Any = None,
    follow_dates: bool = False,
) -> FakeTransport:
    """Token endpoint plus a reports endpoint that answers by ``dimensions``."""

    def reports(request: Any) -> Any:
        query = parse_qs(urlsplit(request.path).query)
        dims = query.get("dimensions", [""])[0]
        answer = {"day": daily, "ageGroup,gender": demo, "country": geo}[dims]
        if follow_dates and dims == "day" and isinstance(answer, dict):
            # end to end runs use the real clock: lay the rows on the asked days
            first = date.fromisoformat(query["startDate"][0])
            answer = {
                **answer,
                "rows": [
                    [(first + timedelta(days=i)).isoformat(), *row[1:]]
                    for i, row in enumerate(answer["rows"])
                ],
            }
        if answer is None:
            return reply({"kind": "x", "columnHeaders": []}, status=500)
        return answer if not isinstance(answer, dict) else reply(answer)

    return FakeTransport(
        {
            TOKEN_URL: token if token is not None else reply(fx("token.json")),
            REPORTS_URL: reports,
        }
    )


def ok_router(follow_dates: bool = False) -> FakeTransport:
    return router(
        daily=fx("daily.json"),
        demo=fx("demographics.json"),
        geo=fx("geography.json"),
        follow_dates=follow_dates,
    )


def fetch_for(transport: FakeTransport) -> Any:
    return partial(fetch_json, transport=transport, sleep=lambda s: None)


def run(transport: FakeTransport, env: Any = None) -> tuple[Any, list[str]]:
    return collect_youtube(
        SRC, ENV if env is None else env, fetch_for(transport), TODAY
    )


# ---- config ----


def test_analytics_is_off_by_default_and_a_strict_bool() -> None:
    plain = YoutubeSource.model_validate(
        {"platform": "youtube", "kind": "youtube", "channel_id": CHANNEL}
    )
    assert plain.analytics is False
    with pytest.raises(ValueError):
        YoutubeSource.model_validate(
            {**SRC.model_dump(), "analytics": "yes"},
        )


# ---- requests ----


def test_token_exchange_is_a_post_form_and_reports_carry_a_bearer_header() -> None:
    transport = ok_router()
    run(transport)
    token_call, *report_calls = transport.calls
    assert token_call.method == "POST" and token_call.host == "oauth2.googleapis.com"
    form = parse_qs((token_call.body or b"").decode())
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == [ENV["YOUTUBE_OAUTH_REFRESH_TOKEN"]]
    assert len(report_calls) == 3  # one request per report, no retries on success
    for call in report_calls:
        assert call.headers["Authorization"] == "Bearer SENTINEL-YT-ACCESS-FRESH"
        assert call.host == "youtubeanalytics.googleapis.com"
        # no secret in the URL
        assert not any(s in call.path for s in SECRETS)


def test_daily_request_covers_only_processed_days() -> None:
    transport = ok_router()
    run(transport)
    queries = [
        parse_qs(urlsplit(c.path).query) for c in transport.calls if c.method == "GET"
    ]
    daily = next(q for q in queries if q["dimensions"] == ["day"])
    assert daily["ids"] == ["channel==MINE"]
    assert daily["metrics"] == ["views,estimatedMinutesWatched"]
    # data trails by 48 to 72 hours: the end day is 3 days before today
    assert daily["endDate"] == ["2026-11-16"] and daily["startDate"] == ["2026-11-10"]
    demo = next(q for q in queries if q["dimensions"] == ["ageGroup,gender"])
    assert demo["metrics"] == ["viewerPercentage"]
    assert demo["endDate"] == ["2026-11-16"]
    geo = next(q for q in queries if q["dimensions"] == ["country"])
    assert geo["metrics"] == ["views"] and geo["endDate"] == ["2026-11-16"]


# ---- parsing ----


def test_daily_series_gives_one_figure_per_day_and_metric() -> None:
    measures, words = run(ok_router())
    assert words == []
    views = {m.as_of_utc: m.value for m in measures if m.metric == "views_1d"}
    assert views["2026-11-10T00:00:00Z"] == 1101
    assert views["2026-11-16T00:00:00Z"] == 1707 and len(views) == 7
    watch = {m.as_of_utc: m.value for m in measures if m.metric == "watch_minutes_1d"}
    assert watch["2026-11-13T00:00:00Z"] == 7004
    assert all("UTC" in m.note for m in measures)


def test_demographics_fold_small_cells_into_other() -> None:
    measures, _ = run(ok_router())
    demo = {m.metric: m.value for m in measures if m.metric.startswith("demo_")}
    # percent x 100; cells under 2.00 percent are folded into one total (a noise
    # reduction only: it does not stop differencing across stored windows)
    assert demo == {
        "demo_a18_24_f": 1250,
        "demo_a18_24_m": 4025,
        "demo_a25_34_m": 3000,
        "demo_other": 275,
    }


def test_geography_folds_small_countries() -> None:
    measures, _ = run(ok_router())
    geo = {m.metric: m.value for m in measures if m.metric.startswith("geo_")}
    assert geo == {
        "geo_us_views": 4001,
        "geo_ca_views": 900,
        "geo_gb_views": 120,
        "geo_other_views": 100,
    }


def test_empty_demographics_and_geography_are_not_an_error_and_not_zero() -> None:
    transport = router(
        daily=fx("daily.json"), demo=fx("empty.json"), geo={"columnHeaders": []}
    )
    # geography headers are checked even when there are no rows
    measures, words = run(transport)
    assert not [m for m in measures if m.metric.startswith("demo_")]
    assert words == ["bad_report"]  # empty geography answer had no valid headers
    geo_ok = {
        "columnHeaders": [{"name": "country"}, {"name": "views"}],
    }
    measures, words = run(
        router(daily=fx("daily.json"), demo=fx("empty.json"), geo=geo_ok)
    )
    assert words == []
    assert {m.metric for m in measures} == {"views_1d", "watch_minutes_1d"}


def test_empty_daily_is_a_failure_word_not_a_zero() -> None:
    empty_daily = {
        "columnHeaders": [
            {"name": "day"},
            {"name": "views"},
            {"name": "estimatedMinutesWatched"},
        ]
    }
    measures, words = run(
        router(daily=empty_daily, demo=fx("empty.json"), geo=fx("geography.json"))
    )
    assert "no_data" in words
    assert not [m for m in measures if m.metric.endswith("_1d")]


HOSTILE_DAILY = [
    {"columnHeaders": [{"name": "views"}], "rows": [[1]]},
    {
        "columnHeaders": [
            {"name": "day"},
            {"name": "views"},
            {"name": "estimatedMinutesWatched"},
        ],
        "rows": [["2026-11-10", "7", 1]],
    },
    {
        "columnHeaders": [
            {"name": "day"},
            {"name": "views"},
            {"name": "estimatedMinutesWatched"},
        ],
        "rows": [["2026-11-10", True, 1]],
    },
    {
        "columnHeaders": [
            {"name": "day"},
            {"name": "views"},
            {"name": "estimatedMinutesWatched"},
        ],
        "rows": [["2026-11-10", -1, 1]],
    },
    {
        "columnHeaders": [
            {"name": "day"},
            {"name": "views"},
            {"name": "estimatedMinutesWatched"},
        ],
        "rows": [["2026-11-10", 1, 1], ["2026-11-10", 2, 2]],
    },
    {
        "columnHeaders": [
            {"name": "day"},
            {"name": "views"},
            {"name": "estimatedMinutesWatched"},
        ],
        "rows": [["2026-01-01", 1, 1]],
    },
    {
        "columnHeaders": [
            {"name": "day"},
            {"name": "views"},
            {"name": "estimatedMinutesWatched"},
        ],
        "rows": [["2026-11-10", 10**13, 1]],
    },
]


@pytest.mark.parametrize("payload", HOSTILE_DAILY)
def test_hostile_daily_shapes_store_nothing_and_use_fixed_words(
    payload: dict[str, Any],
) -> None:
    measures, words = run(
        router(daily=payload, demo=fx("empty.json"), geo=fx("geography.json"))
    )
    assert not [m for m in measures if m.metric.endswith("_1d")]
    assert words and set(words) <= {"bad_report", "bad_row"}


def test_demographic_percentage_out_of_range_skips_only_that_row() -> None:
    bad = fx("demographics.json")
    bad["rows"][0][2] = 140.0  # the age18-24 female row
    measures, words = run(
        router(daily=fx("daily.json"), demo=bad, geo=fx("geography.json"))
    )
    assert words == ["bad_row"]
    demo = {m.metric for m in measures if m.metric.startswith("demo_")}
    assert "demo_a18_24_f" not in demo and "demo_a18_24_m" in demo


DAILY_HEADERS = [
    {"name": "day"},
    {"name": "views"},
    {"name": "estimatedMinutesWatched"},
]
ARABIC_INDIC_DAY = "٢٠٢٦-١١-١٢"


@pytest.mark.parametrize(
    "bad_day",
    ["2026-11-10\n", ARABIC_INDIC_DAY, "not-a-day", "2026-11-1", " 2026-11-12"],
    ids=["trailing-newline", "arabic-indic", "not-a-date", "short", "leading-space"],
)
def test_day_must_be_strict_ascii_and_a_bad_row_is_skipped_not_fatal(
    bad_day: str,
) -> None:
    payload = {
        "columnHeaders": DAILY_HEADERS,
        "rows": [[bad_day, 5, 6], ["2026-11-11", 7, 8]],
    }
    measures, words = run(
        router(daily=payload, demo=fx("empty.json"), geo=fx("empty_geo.json"))
    )
    got = {(m.metric, m.value, m.as_of_utc) for m in measures}
    assert got == {
        ("views_1d", 7, "2026-11-11T00:00:00Z"),
        ("watch_minutes_1d", 8, "2026-11-11T00:00:00Z"),
    }
    assert words == ["bad_row"]


def test_basis_points_refuses_nan_and_infinity() -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(YoutubeAnalyticsError) as err:
            _basis_points(bad)
        assert err.value.kind == "bad_aggregate"
    assert _basis_points(40.25) == 4025


def test_oversized_report_is_refused_whole() -> None:
    rows = [["2026-11-10", 1, 1]] * (MAX_ROWS + 1)
    measures, words = run(
        router(
            daily={"columnHeaders": DAILY_HEADERS, "rows": rows},
            demo=fx("empty.json"),
            geo=fx("empty_geo.json"),
        )
    )
    assert not measures and words == ["too_large"]


def test_no_request_follows_a_redirect() -> None:
    seen: list[tuple[str, Any]] = []

    def spy(url: str, **kw: Any) -> dict[str, object]:
        seen.append((url, kw.get("follow_redirects")))
        if url == TOKEN_URL:
            return fx("token.json")
        return {"columnHeaders": []}

    collect_youtube(SRC, ENV, spy, TODAY)
    assert len(seen) == 4  # the token POST and three reports
    assert [flag for _, flag in seen] == [False] * 4


# ---- failures ----


def test_rejected_refresh_is_a_fixed_word_and_makes_no_report_request() -> None:
    transport = router(token=reply({"error": "invalid_grant"}, status=400))
    with pytest.raises(YoutubeAnalyticsError) as err:
        run(transport)
    assert err.value.kind == "refresh_failed"
    assert [c.host for c in transport.calls] == ["oauth2.googleapis.com"]
    assert err.value.__cause__ is None
    assert not any(s in repr(err.value) + str(err.value) for s in SECRETS)


def test_token_answer_without_an_access_token_is_refused() -> None:
    with pytest.raises(YoutubeAnalyticsError) as err:
        run(router(token=reply({"expires_in": 3600})))
    assert err.value.kind == "refresh_failed"


@pytest.mark.parametrize(
    ("status", "word"),
    [(401, "token_rejected"), (403, "scope_missing"), (429, "rate_limited")],
)
def test_report_http_errors_use_fixed_words(status: int, word: str) -> None:
    transport = FakeTransport(
        {
            TOKEN_URL: reply(fx("token.json")),
            REPORTS_URL: reply({"error": {"message": "SECRET-BODY"}}, status=status),
        }
    )
    _, words = run(transport)
    assert word in words
    assert "SECRET-BODY" not in " ".join(words)
    # a quota answer is not retried in a storm: at most the fetch layer's bound
    assert len([c for c in transport.calls if c.method == "GET"]) <= 3 * 3


@pytest.mark.parametrize("missing", sorted(ENV))
def test_missing_credential_fails_before_any_request(missing: str) -> None:
    transport = ok_router()
    env = {k: v for k, v in ENV.items() if k != missing}
    with pytest.raises(YoutubeAnalyticsError) as err:
        run(transport, env)
    assert err.value.kind == "missing_credential"
    assert not transport.calls


def test_forged_analytics_flag_is_refused_at_use() -> None:
    transport = ok_router()
    forged = SRC.model_copy(update={"analytics": "yes"})
    with pytest.raises(YoutubeAnalyticsError) as err:
        collect_youtube(forged, ENV, fetch_for(transport), TODAY)
    assert err.value.kind == "bad_source_setting"
    assert not transport.calls


def test_unexpected_exception_has_no_secret_in_it() -> None:
    def boom(url: str, **kw: Any) -> dict[str, object]:
        if url == TOKEN_URL:
            return fx("token.json")
        raise RuntimeError("SENTINEL-YT-REFRESH in the url " + url)

    measures, words = collect_youtube(SRC, ENV, boom, TODAY)
    assert measures == [] and words == ["fetch_failed"] * 3

    # and an exception while exchanging the token is a refresh failure
    def divide(url: str, **kw: Any) -> dict[str, object]:
        raise ZeroDivisionError

    with pytest.raises(YoutubeAnalyticsError) as err:
        collect_youtube(SRC, ENV, divide, TODAY)
    assert err.value.kind == "refresh_failed" and err.value.__context__ is None


# ---- collect() wiring ----


def test_collect_is_opt_in_and_needs_a_good_follower_read() -> None:
    plain = SRC.model_copy(update={"analytics": False})
    transport = ok_router()
    fetch = fetch_for(transport)
    assert collect([plain], frozenset({"youtube"}), ENV, fetch, NOW) == ([], [])
    assert collect([SRC], frozenset(), ENV, fetch, NOW) == ([], [])
    assert not transport.calls


def test_collect_returns_figures_with_their_own_day() -> None:
    figures, failures = collect(
        [SRC], frozenset({"youtube"}), ENV, fetch_for(ok_router()), NOW
    )
    assert failures == []
    assert {f.platform for f in figures} == {"youtube"}
    assert {f.as_of_utc for f in figures if f.metric == "views_1d"} >= {
        "2026-11-16T00:00:00Z"
    }
    assert all(f.note for f in figures)


def test_collect_reports_token_failure_as_a_word() -> None:
    transport = router(token=reply({"error": "invalid_grant"}, status=400))
    figures, failures = collect(
        [SRC], frozenset({"youtube"}), ENV, fetch_for(transport), NOW
    )
    assert figures == []
    assert [(f.platform, f.word) for f in failures] == [("youtube", "refresh_failed")]


# ---- end to end through counts refresh ----

CONFIG = f"""\
version: 1
creator: {{name: Test Creator}}
tabs:
  - id: home
    title: Home
    blocks:
      - id: socials
        items:
          - {{platform: youtube, handle: "@example", url: "https://example.com/yt"}}
numbers:
  history_file: history/social.jsonl
  sources:
    - {{platform: youtube, kind: youtube, channel_id: {CHANNEL}, analytics: true}}
"""
CHANNELS = "https://www.googleapis.com/youtube/v3/channels"


def refresh(
    root: Path, monkeypatch: pytest.MonkeyPatch, transport: FakeTransport
) -> int:
    (root / "history").mkdir(exist_ok=True)
    (root / "site.yaml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.setattr(counts_cmd, "default_sleep", lambda s: None)
    monkeypatch.setattr(counts_cmd, "default_transport", lambda: transport)
    monkeypatch.setenv("YOUTUBE_API_KEY", "SENTINEL-YT-KEY")
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    return main(["counts", "refresh", "--config", str(root / "site.yaml")])


def channel_reply() -> Any:
    return reply({"items": [{"statistics": {"subscriberCount": "321"}}]})


def with_channel(transport: FakeTransport) -> FakeTransport:
    transport.routes[CHANNELS] = channel_reply()
    return transport


def test_refresh_stores_private_figures_tagged_youtube_analytics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert refresh(tmp_path, monkeypatch, with_channel(ok_router(True))) == 0
    records = read_history(tmp_path / "history" / "social.jsonl").records
    analytics = [r for r in records if r.api_provider == "youtube_analytics"]
    assert {r.metric for r in analytics} >= {
        "views_1d",
        "watch_minutes_1d",
        "demo_a18_24_m",
        "geo_us_views",
    }
    assert all(r.source == "api" and r.platform == "youtube" for r in analytics)
    sub = next(r for r in records if r.metric == "subscribers")
    assert sub.api_provider == "youtube_data_api"
    out = capsys.readouterr()
    assert not any(s in out.out + out.err for s in SECRETS)
    # a second run the same day writes nothing new for the same days
    before = (tmp_path / "history" / "social.jsonl").read_bytes()
    assert refresh(tmp_path, monkeypatch, with_channel(ok_router(True))) == 0
    assert (tmp_path / "history" / "social.jsonl").read_bytes() == before


def test_refresh_failure_keeps_subscribers_and_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = with_channel(
        router(token=reply({"error": "invalid_grant"}, status=400))
    )
    assert refresh(tmp_path, monkeypatch, transport) == counts_cmd.EXIT_SOURCE_FAILED
    records = read_history(tmp_path / "history" / "social.jsonl").records
    assert [r.metric for r in records] == ["subscribers"]  # a gap, never a zero
    err = capsys.readouterr().err
    assert "youtube: insights error (refresh_failed)" in err
    assert not any(s in err for s in SECRETS)


# ---- retention and deletion (policy: 30-day re-confirm, delete on revoke) ----


def rec(metric: str, as_of: str, provider: str | None = "youtube_analytics") -> Record:
    return Record("youtube", metric, 5, as_of, "api", "run-1", api_provider=provider)


def test_deletion_candidates_older_than_retention_and_on_revocation() -> None:
    assert RETAIN_DAYS == 30
    old = rec("views_1d", "2026-10-01T00:00:00Z")
    fresh = rec("views_1d", "2026-11-16T00:00:00Z")
    public = rec("subscribers", "2026-09-01T00:00:00Z", "youtube_data_api")
    hand = Record("youtube", "views_1d", 5, "2026-01-01T00:00:00Z", "hand_kept", "r")
    items = [old, fresh, public, hand]
    assert deletion_candidates(items, TODAY) == [old]
    # revocation or a user request: every collected analytics record, any age
    assert deletion_candidates(items, TODAY, revoked=True) == [old, fresh]


def test_a_correction_that_carries_an_analytics_value_is_selected_too() -> None:
    original = rec("views_1d", "2026-10-01T00:00:00Z")
    fix = Record(
        "youtube",
        "views_1d",
        9,
        "2026-10-01T00:00:00Z",
        "api",
        "run-2",
        note="fixed",
        type="correction",
        api_provider="youtube_analytics",
    )
    fresh_fix = Record(
        "youtube",
        "views_1d",
        9,
        "2026-11-16T00:00:00Z",
        "api",
        "run-2",
        note="fixed",
        type="correction",
        api_provider="youtube_analytics",
    )
    assert deletion_candidates([original, fix, fresh_fix], TODAY) == [original, fix]
    assert deletion_candidates([fresh_fix], TODAY, revoked=True) == [fresh_fix]


def old_analytics_history(root: Path, count: int) -> None:
    (root / "history").mkdir(exist_ok=True)
    append_records(
        root / "history" / "social.jsonl",
        [
            Record(
                "youtube",
                f"views_{i}d",
                1_234_567,
                "2026-01-01T00:00:00Z",
                "api",
                "old",
                api_provider="youtube_analytics",
            )
            for i in range(1, count + 1)
        ],
        now=NOW,
    )


def test_refresh_tells_the_owner_how_many_records_are_past_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    old_analytics_history(tmp_path, 3)
    assert refresh(tmp_path, monkeypatch, with_channel(ok_router(True))) == 0
    err = capsys.readouterr().err
    assert (
        "youtube: retention notice: 3 analytics record(s) are past 30 days; "
        "remove them with the history tombstone tool (#586)"
    ) in err
    assert "1234567" not in err  # a count, never a value
    # the notice repeats on every run until the records are removed
    assert refresh(tmp_path, monkeypatch, with_channel(ok_router(True))) == 0
    assert "retention notice: 3 " in capsys.readouterr().err


def test_no_retention_notice_when_nothing_is_past_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert refresh(tmp_path, monkeypatch, with_channel(ok_router(True))) == 0
    assert "retention notice" not in capsys.readouterr().err


# ---- provenance does not depend on the display slug ----

SLUG = "socials-youtube"  # a valid slug that is not the kind "youtube"
RENAMED = CONFIG.replace(
    "platform: youtube, kind: youtube", f"platform: {SLUG}, kind: youtube"
)


def refresh_renamed(
    root: Path, monkeypatch: pytest.MonkeyPatch, transport: FakeTransport
) -> list[Record]:
    (root / "history").mkdir(exist_ok=True)
    (root / "site.yaml").write_text(RENAMED, encoding="utf-8")
    monkeypatch.setattr(counts_cmd, "default_sleep", lambda s: None)
    monkeypatch.setattr(counts_cmd, "default_transport", lambda: transport)
    monkeypatch.setenv("YOUTUBE_API_KEY", "SENTINEL-YT-KEY")
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    code = main(["counts", "refresh", "--config", str(root / "site.yaml")])
    assert code == 0
    return list(read_history(root / "history" / "social.jsonl").records)


def test_a_renamed_platform_slug_still_tags_analytics_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert f"platform: {SLUG}, kind: youtube" in RENAMED
    records = refresh_renamed(tmp_path, monkeypatch, with_channel(ok_router(True)))
    assert {r.platform for r in records} == {SLUG}
    analytics = [r for r in records if r.metric != "subscribers"]
    assert analytics and all(r.api_provider == "youtube_analytics" for r in analytics)
    subs = [r for r in records if r.metric == "subscribers"]
    assert [r.api_provider for r in subs] == ["youtube_data_api"]


def test_a_renamed_slug_is_still_selected_for_deletion_and_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    records = refresh_renamed(tmp_path, monkeypatch, with_channel(ok_router(True)))
    tagged = [r for r in records if r.metric != "subscribers"]
    later = date(2099, 1, 1)
    assert deletion_candidates(records, later) == tagged
    capsys.readouterr()
    counts_cmd._retention_notice(
        tmp_path / "history" / "social.jsonl",
        datetime(2099, 1, 1, tzinfo=UTC),
    )
    err = capsys.readouterr().err
    assert f"retention notice: {len(tagged)} analytics record(s)" in err


def test_the_report_refuses_analytics_whatever_the_platform_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    refresh_renamed(tmp_path, monkeypatch, with_channel(ok_router(True)))
    block = build_social(
        read_history(tmp_path / "history" / "social.jsonl"),
        cutover=None,
        today=date(2026, 11, 20),
    )
    shown = {(s["platform"], s["metric"]) for s in block["platforms"]}
    assert shown == {(SLUG, "subscribers")}
    refused = {(i["platform"], i["metric"]) for i in block["refused"]}
    # private series are named by kind only, never by the cell they hold
    assert refused == {(SLUG, "private analytics")}


def test_records_are_identified_only_by_the_provider_tag() -> None:
    """One rule everywhere (report, deletion, revocation, notice): the tag. An
    untagged API record named like an analytics figure is NOT refused and NOT
    selected for deletion; a tagged one is both."""
    day = "2026-10-01T09:00:00Z"
    untagged = Record("video", "views_1d", 987654, day, "api", "r")
    tagged = Record(
        "video", "views_1d", 123456, day, "api", "r2", api_provider="youtube_analytics"
    )
    today = date(2026, 11, 20)
    block = build_social(History((untagged,)), cutover=None, today=today)
    assert [s["metric"] for s in block["platforms"]] == ["views_1d"]
    assert block["refused"] == []
    assert deletion_candidates([untagged], today) == []
    assert deletion_candidates([untagged], today, revoked=True) == []
    block = build_social(History((tagged,)), cutover=None, today=today)
    assert block["platforms"] == [] and [i["metric"] for i in block["refused"]] == [
        "private analytics"
    ]
    assert deletion_candidates([tagged], today) == [tagged]
    assert deletion_candidates([tagged], today, revoked=True) == [tagged]


# ---- corrections keep the provenance of the record they fix ----

DAY0 = "2026-10-01T00:00:00Z"


def tagged_original(platform: str = SLUG) -> Record:
    return Record(
        platform, "views_1d", 111, DAY0, "api", "r1", api_provider="youtube_analytics"
    )


def correction(provider: str | None, platform: str = SLUG) -> Record:
    return Record(
        platform,
        "views_1d",
        222,
        DAY0,
        "api",
        "r2",
        note="fixed",
        type="correction",
        api_provider=provider,
    )


def test_the_history_validator_refuses_a_correction_with_another_provider(
    tmp_path: Path,
) -> None:
    from reach_around.counts.history import HistoryError

    path = tmp_path / "h.jsonl"
    append_records(path, [tagged_original()], now=NOW)
    for provider in (None, "youtube_data_api"):
        with pytest.raises(HistoryError, match="provider"):
            append_records(path, [correction(provider)], now=NOW)
    append_records(path, [correction("youtube_analytics")], now=NOW)  # matches
    # the other way round: an untagged original and a tagged correction
    other = tmp_path / "o.jsonl"
    untagged = Record(SLUG, "views_1d", 1, DAY0, "api", "r")
    append_records(other, [untagged], now=NOW)
    with pytest.raises(HistoryError, match="provider"):
        append_records(other, [correction("youtube_analytics")], now=NOW)


def test_history_add_copies_the_provider_of_the_original(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    append_records(path, [tagged_original()], now=NOW)
    args = [
        "counts",
        "history-add",
        "--file",
        str(path),
        "--platform",
        SLUG,
        "--metric",
        "views_1d",
        "--value",
        "222",
        "--as-of",
        "2026-10-01",
        "--source",
        "api",
        "--correction",
        "--note",
        "misread",
    ]
    assert main(args) == 0
    records = read_history(path).records
    assert [r.type for r in records] == ["record", "correction"]
    assert records[1].api_provider == "youtube_analytics"
    # a correction of an untagged api record stays untagged
    plain_path = tmp_path / "p.jsonl"
    append_records(plain_path, [Record(SLUG, "views_1d", 1, DAY0, "api", "r")], now=NOW)
    args[3] = str(plain_path)
    assert main(args) == 0
    assert read_history(plain_path).records[1].api_provider is None
    # a correction with no original is refused, not written untagged
    empty = tmp_path / "e.jsonl"
    append_records(empty, [Record("other", "followers", 1, DAY0, "api", "r")], now=NOW)
    args[3] = str(empty)
    assert main(args) != 0


def test_a_legacy_tagged_original_with_untagged_correction_is_still_handled() -> None:
    """Such a file cannot be written any more, but a reader must not trust that:
    selection and the report treat the correction like its original."""
    orig, fix = tagged_original(), correction(None)
    later = date(2026, 12, 31)
    assert deletion_candidates([orig, fix], later) == [orig, fix]
    assert deletion_candidates([orig, fix], later, revoked=True) == [orig, fix]
    block = build_social(History((orig, fix)), cutover=None, today=later)
    assert block["platforms"] == [] and [i["metric"] for i in block["refused"]] == [
        "private analytics"
    ]


def test_the_retention_notice_counts_a_tagged_correction_with_its_original(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # the notice counts what deletion_candidates selects (tested above for a
    # legacy untagged correction); here the real file path, original plus fix
    path = tmp_path / "h.jsonl"
    append_records(path, [tagged_original(), correction("youtube_analytics")], now=NOW)
    counts_cmd._retention_notice(path, datetime(2026, 12, 31, tzinfo=UTC))
    assert "retention notice: 2 analytics record(s)" in capsys.readouterr().err


def test_the_report_never_raises_on_a_correction_without_its_original() -> None:
    orphan = correction(None, platform="video")
    block = build_social(History((orphan,)), cutover=None, today=date(2026, 12, 31))
    assert block["platforms"] == [] and block["refused"] == []
    # a hand-kept series with an orphaned correction beside a good one still shows
    good = Record("video", "followers", 5, DAY0, "hand_kept", "r")
    block = build_social(
        History((orphan, good)), cutover=None, today=date(2026, 12, 31)
    )
    assert [s["metric"] for s in block["platforms"]] == ["followers"]


# ---- demographic shares cannot add up to more than 100 percent ----


def demo_answer(*cells: tuple[str, str, float]) -> dict[str, Any]:
    out = fx("demographics.json")
    out["rows"] = [list(c) for c in cells]
    return out


def demo_run(*cells: tuple[str, str, float]) -> tuple[Any, list[str]]:
    return run(
        router(
            daily=fx("daily.json"),
            demo=demo_answer(*cells),
            geo=fx("empty_geo.json"),
        )
    )


def test_exactly_100_percent_is_accepted() -> None:
    measures, words = demo_run(("age18-24", "male", 50.0), ("age25-34", "female", 50.0))
    total = sum(m.value for m in measures if m.metric.startswith("demo_"))
    assert words == [] and total == 10000


@pytest.mark.parametrize(
    "cells",
    [
        (("age18-24", "male", 100.0), ("age25-34", "male", 100.0)),
        (("age18-24", "male", 99.0), ("age13-17", "female", 1.5)),  # folded counts
        (("age18-24", "male", 50.0), ("age25-34", "female", 50.26)),  # 10026
    ],
    ids=["two-100s", "folded-cells-count", "just-over-tolerance"],
)
def test_a_share_total_over_100_percent_is_bad_aggregate(
    cells: tuple[tuple[str, str, float], ...],
) -> None:
    measures, words = demo_run(*cells)
    assert words == ["bad_aggregate"]
    assert not [m for m in measures if m.metric.startswith("demo_")]


def test_the_rounding_tolerance_boundary_is_accepted() -> None:
    from reach_around.counts.youtube_analytics import DEMO_SUM_TOLERANCE_BP

    assert DEMO_SUM_TOLERANCE_BP == 25
    measures, words = demo_run(
        ("age18-24", "male", 50.0),
        ("age25-34", "female", 50.25),  # 10025, the edge
    )
    assert words == [] and measures
    # folded cells are inside the total: 99.0 + 1.0 folded is exactly 100
    measures, words = demo_run(("age18-24", "male", 99.0), ("age13-17", "female", 1.0))
    assert words == [] and "demo_other" in {m.metric for m in measures}


# ---- limits: the whole append, and one analytics source per config ----


def plain(i: int) -> Record:
    return Record("other", "followers", i, "2026-11-01T00:00:00Z", "api", "r")


def analytics_rec(i: int) -> Record:
    return Record(
        "youtube",
        f"views_{i}d",
        i,
        "2026-11-01T00:00:00Z",
        "api",
        "r",
        api_provider="youtube_analytics",
    )


def test_a_batch_over_the_append_limit_drops_only_the_analytics() -> None:
    others = [plain(i) for i in range(MAX_BATCH - MAX_RUN_FIGURES + 2)]
    figures = [analytics_rec(i) for i in range(MAX_RUN_FIGURES)]
    kept, dropped = counts_cmd.fit_batch(others + figures)
    assert kept == others and dropped == ["youtube"]
    assert len(others) + len(figures) == MAX_BATCH + 2


def test_one_record_over_the_append_limit_drops_only_the_analytics_record() -> None:
    others = [plain(i) for i in range(MAX_BATCH)]
    figure = analytics_rec(1)
    batch = others + [figure]
    assert len(batch) == MAX_BATCH + 1
    kept, dropped = counts_cmd.fit_batch(batch)
    assert kept == others and dropped == ["youtube"]


def test_near_miss_tags_are_not_analytics() -> None:
    def tagged(platform: str, tag: str | None, i: int) -> Record:
        return Record(
            platform,
            f"views_{i}d",
            i,
            "2026-11-01T00:00:00Z",
            "api",
            "r",
            api_provider=tag,
        )

    near = [
        tagged("youtube", "youtube_analytics ", 1),  # trailing space
        tagged("youtube", "YouTube_Analytics", 2),  # case differs
        tagged("youtube_analytics", None, 3),  # right name, but a platform, no tag
    ]
    others = [plain(i) for i in range(MAX_BATCH - len(near) + 1)]
    batch = others + near + [analytics_rec(9)]
    assert len(batch) > MAX_BATCH
    kept, dropped = counts_cmd.fit_batch(batch)
    assert kept == others + near and dropped == ["youtube"]


def test_a_batch_exactly_at_the_limit_is_kept_whole() -> None:
    others = [plain(i) for i in range(MAX_BATCH - 10)]
    figures = [analytics_rec(i) for i in range(10)]
    kept, dropped = counts_cmd.fit_batch(others + figures)
    assert kept == others + figures and dropped == []


def test_refresh_drops_analytics_and_keeps_subscribers_when_over_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(counts_cmd, "MAX_BATCH", 10)  # subscribers + 62 > 10
    code = refresh(tmp_path, monkeypatch, with_channel(ok_router(True)))
    assert code == counts_cmd.EXIT_SOURCE_FAILED
    records = read_history(tmp_path / "history" / "social.jsonl").records
    assert [r.metric for r in records] == ["subscribers"]
    assert "youtube: insights error (too_large)" in capsys.readouterr().err


def test_two_analytics_sources_are_refused_at_load() -> None:
    from pydantic import ValidationError

    from reach_around.config.numbers_sources import NumbersConfig

    def source(slug: str, analytics: bool) -> dict[str, Any]:
        return {
            "platform": slug,
            "kind": "youtube",
            "channel_id": CHANNEL,
            "analytics": analytics,
        }

    with pytest.raises(ValidationError, match="at most one"):
        NumbersConfig.model_validate(
            {"sources": [source("a-yt", True), source("b-yt", True)]}
        )
    ok = NumbersConfig.model_validate(
        {"sources": [source("a-yt", True), source("b-yt", False)]}
    )
    assert [s.platform for s in ok.sources] == ["a-yt", "b-yt"]


def test_geography_sum_over_the_history_maximum_is_refused_not_clamped() -> None:
    rows = [["ZZ", 10**12], ["Zz", 10**12]]  # both fold into geo_other_views
    geo = {"columnHeaders": [{"name": "country"}, {"name": "views"}], "rows": rows}
    measures, words = run(
        router(daily=fx("daily.json"), demo=fx("empty.json"), geo=geo)
    )
    assert words == ["bad_aggregate"]
    assert not [m for m in measures if m.metric.startswith("geo_")]


# ---- limits: output per report and per run ----


def country_codes(count: int) -> list[str]:
    import itertools
    import string

    codes = ("".join(p) for p in itertools.product(string.ascii_uppercase, repeat=2))
    return [c for c in codes if c != "ZZ"][:count]


def geo_pages(total: int, *, extra: int = 0) -> tuple[FakeTransport, list[int]]:
    """A reports endpoint with ``total`` countries, paged by startIndex."""
    rows = [[c, 100_000 - i * 10] for i, c in enumerate(country_codes(total))]
    starts: list[int] = []

    def reports(request: Any) -> Any:
        query = parse_qs(urlsplit(request.path).query)
        dims = query["dimensions"][0]
        if dims == "day":
            return reply(fx("daily.json"))
        if dims == "ageGroup,gender":
            return reply(fx("demographics.json"))
        start = int(query.get("startIndex", ["1"])[0])
        size = int(query["maxResults"][0])
        starts.append(start)
        page = rows[start - 1 : start - 1 + size + extra]
        return reply(
            {
                "columnHeaders": [{"name": "country"}, {"name": "views"}],
                "rows": page,
            }
        )

    transport = FakeTransport(
        {TOKEN_URL: reply(fx("token.json")), REPORTS_URL: reports}
    )
    return transport, starts


def test_201_countries_give_at_most_26_geography_figures() -> None:
    transport, starts = geo_pages(201)
    measures, words = run(transport)
    assert words == []
    geo = [m for m in measures if m.metric.startswith("geo_")]
    assert len(geo) == GEO_NAMED_MAX + 1
    named = [m for m in geo if m.metric != "geo_other_views"]
    assert len(named) == GEO_NAMED_MAX
    # the folded total holds every country outside the named 25 (by views)
    expected = sum(100_000 - i * 10 for i in range(GEO_NAMED_MAX, 201))
    assert next(m for m in geo if m.metric == "geo_other_views").value == expected
    assert starts == [1, 1 + GEO_PAGE_SIZE, 1 + 2 * GEO_PAGE_SIZE]


def test_geography_pagination_has_a_hard_cap() -> None:
    transport, starts = geo_pages(600)
    measures, words = run(transport)
    assert len(starts) == GEO_MAX_PAGES
    # every allowed page was full: the report is not stored as if it were complete
    assert not [m for m in measures if m.metric.startswith("geo_")]
    assert words == ["window_incomplete"]


def test_a_full_last_page_is_incomplete_but_a_short_one_is_stored() -> None:
    full, _ = geo_pages(GEO_MAX_PAGES * GEO_PAGE_SIZE)
    _, words = run(full)
    assert words == ["window_incomplete"]
    short, _ = geo_pages(GEO_MAX_PAGES * GEO_PAGE_SIZE - 1)
    measures, words = run(short)
    assert words == []
    assert [m for m in measures if m.metric.startswith("geo_")]


def test_a_page_larger_than_the_requested_size_fails_only_that_report() -> None:
    transport, _ = geo_pages(201, extra=5)
    measures, words = run(transport)
    assert words == ["too_large"]
    assert not [m for m in measures if m.metric.startswith("geo_")]
    assert {m.metric for m in measures} >= {"views_1d", "demo_a18_24_m"}


def test_the_same_country_on_two_pages_fails_that_report() -> None:
    rows = [[c, 500] for c in country_codes(GEO_PAGE_SIZE)]
    answers = [rows, [rows[0]]]
    seen: list[int] = []

    def reports(request: Any) -> Any:
        query = parse_qs(urlsplit(request.path).query)
        dims = query["dimensions"][0]
        if dims == "day":
            return reply(fx("daily.json"))
        if dims == "ageGroup,gender":
            return reply(fx("demographics.json"))
        seen.append(1)
        return reply(
            {
                "columnHeaders": [{"name": "country"}, {"name": "views"}],
                "rows": answers[len(seen) - 1],
            }
        )

    transport = FakeTransport(
        {TOKEN_URL: reply(fx("token.json")), REPORTS_URL: reports}
    )
    _, words = run(transport)
    assert words == ["bad_report"]


@pytest.mark.parametrize(
    ("age", "gender"),
    [
        ("age99-99", "female"),
        ("age18-24 ", "female"),
        ("AGE18-24", "female"),
        ("age18-24", "other"),
        ("age18-24", "Female"),
        ("age١٨-24", "female"),
        (None, "female"),
    ],
)
def test_labels_outside_the_documented_allowlist_are_bad_rows(
    age: Any, gender: str
) -> None:
    bad = fx("demographics.json")
    bad["rows"] = [[age, gender, 50.0], ["age25-34", "male", 30.0]]
    measures, words = run(
        router(daily=fx("daily.json"), demo=bad, geo=fx("empty_geo.json"))
    )
    demo = {m.metric: m.value for m in measures if m.metric.startswith("demo_")}
    # never folded into demo_other: an odd label is dropped and counted
    assert demo == {"demo_a25_34_m": 3000}
    assert words == ["bad_row"]


def test_a_hostile_demographic_answer_cannot_exceed_the_cell_limit() -> None:
    bad = fx("demographics.json")
    bad["rows"] = [
        [f"age{n:02d}-{n + 1:02d}", "female", 1.0] for n in range(10, 90)
    ] + [["age18-24", "male", 40.0]]
    measures, words = run(
        router(daily=fx("daily.json"), demo=bad, geo=fx("empty_geo.json"))
    )
    demo = [m for m in measures if m.metric.startswith("demo_")]
    assert len(demo) <= DEMO_MAX_FIGURES and words == ["bad_row"]


def test_the_whole_run_fits_one_history_append() -> None:
    transport, _ = geo_pages(250)
    measures, words = run(transport)
    assert words == [] and len(measures) <= MAX_RUN_FIGURES < MAX_BATCH
    # and the writer accepts the batch as one append
    assert measures


def test_an_over_limit_report_fails_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    import reach_around.counts.youtube_analytics as module

    monkeypatch.setattr(module, "GEO_MAX_FIGURES", 3)
    transport, _ = geo_pages(201)
    measures, words = run(transport)
    assert words == ["too_large"]
    assert {m.metric for m in measures} >= {"views_1d", "demo_a18_24_m"}


# ---- boundary and leak ----


def test_module_cannot_be_reached_from_public_side(tmp_path: Path) -> None:
    root = tmp_path
    for rel, text in {
        "__init__.py": "",
        "counts/__init__.py": "",
        "counts/youtube_analytics.py": "X = 1\n",
        "site/__init__.py": "",
        "site/a.py": "from reach_around.counts import youtube_analytics\n",
    }.items():
        target = root / "reach_around" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    assert violations(root), "the guard must bite on a direct import"


PUBLIC_SITE = """\
version: 1
creator: {name: Test Creator}
tabs:
  - id: home
    title: Home
    blocks:
      - id: socials
        items:
          - {platform: youtube, handle: "@example", url: "https://example.com/yt"}
numbers:
  history_file: history/social.jsonl
  sources:
    - platform: youtube
      kind: youtube
      channel_id: UCaaaaaaaaaaaaaaaaaaaaaa
      analytics: true
"""
# Varied on purpose: large, small, and values a renderer could print in a derived
# form (987.7k, 1.2M, a percentage). A differential test catches every form.
ANALYTICS_VALUES = {
    "views_1d": 987_654,
    "watch_minutes_1d": 1_234_567_890,
    "demo_a18_24_f": 4_025,
    "demo_other": 3,
    "geo_us_views": 12,
    "geo_other_views": 765_432,
}


def write_analytics_history(root: Path) -> Path:
    path = root / "history" / "social.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    append_records(
        path,
        [
            Record(
                "youtube",
                metric,
                value,
                "2026-11-16T00:00:00Z",
                "api",
                "r1",
                note="n",
                api_provider="youtube_analytics",
            )
            for metric, value in ANALYTICS_VALUES.items()
        ],
        now=NOW,
    )
    return path


def build_tree(config: Path, out: Path) -> dict[str, bytes]:
    code = main(["build", "--config", str(config), "--out", str(out)])
    assert code == 0 and any(out.rglob("*.html"))
    return {
        str(p.relative_to(out)): p.read_bytes()
        for p in sorted(out.rglob("*"))
        if p.is_file()
    }


def make_instance(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "site.yaml").write_text(PUBLIC_SITE, encoding="utf-8")
    return root


def test_public_output_is_byte_identical_with_and_without_analytics(
    tmp_path: Path,
) -> None:
    """Differential: the same config built with a history file full of analytics
    records and with no history file must give the same public tree, byte for
    byte. Unlike a search for a few values, this also catches a renderer that
    prints a derived form (987.7k, a label, a percentage)."""
    without = build_tree(make_instance(tmp_path / "a") / "site.yaml", tmp_path / "oa")
    with_root = make_instance(tmp_path / "b")
    write_analytics_history(with_root)
    with_history = build_tree(with_root / "site.yaml", tmp_path / "ob")
    assert without.keys() == with_history.keys() and without
    assert [k for k in without if without[k] != with_history[k]] == []


def test_differential_guard_bites_on_a_derived_form(tmp_path: Path) -> None:
    """Negative case: a tree that differs only by a derived '987.7k' is caught."""
    base = build_tree(make_instance(tmp_path / "a") / "site.yaml", tmp_path / "oa")
    altered = dict(base)
    index = next(k for k in altered if k.endswith(".html"))
    altered[index] = altered[index] + b"<p>Views 1d 987.7k</p>"
    assert [k for k in base if base[k] != altered[k]] == [index]


def test_the_build_never_opens_the_history_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import builtins
    import io
    import os

    root = make_instance(tmp_path / "inst")
    history = write_analytics_history(root)
    touched: list[str] = []

    def watch(real: Any) -> Any:
        def spy(path: Any, *args: Any, **kw: Any) -> Any:
            if isinstance(path, str | os.PathLike) and str(path).endswith(history.name):
                touched.append(str(path))
            return real(path, *args, **kw)

        return spy

    monkeypatch.setattr(builtins, "open", watch(builtins.open))
    monkeypatch.setattr(io, "open", watch(io.open))
    monkeypatch.setattr(os, "open", watch(os.open))
    build_tree(root / "site.yaml", tmp_path / "out")
    assert touched == []
    # negative case: the spy does see a real read of that file
    read_history(history)
    assert touched
