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
from reach_around.counts.history import Record, append_records, read_history
from reach_around.counts.insights import collect
from reach_around.counts.youtube_analytics import (
    MAX_ROWS,
    REPORTS_URL,
    RETAIN_DAYS,
    TOKEN_URL,
    YoutubeAnalyticsError,
    _basis_points,
    collect_youtube,
    deletion_candidates,
)
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
LEAK_VALUES = ("987654", "876543", "765432")
LEAK_NAMES = ("views_1d", "watch_minutes_1d", "demo_a18_24_f", "geo_us_views")


def leaks(out: Path) -> list[str]:
    found = []
    for path in sorted(out.rglob("*")):
        if path.is_file():
            text = path.read_bytes().decode("utf-8", "replace")
            found += [
                f"{path.name}:{needle}"
                for needle in (*LEAK_VALUES, *LEAK_NAMES, "youtube_analytics")
                if needle in text
            ]
    return found


def test_public_build_never_shows_private_analytics(tmp_path: Path) -> None:
    root = tmp_path / "inst"
    (root / "history").mkdir(parents=True)
    (root / "site.yaml").write_text(PUBLIC_SITE, encoding="utf-8")
    append_records(
        root / "history" / "social.jsonl",
        [
            Record(
                "youtube",
                metric,
                int(value),
                "2026-11-16T00:00:00Z",
                "api",
                "r1",
                note="n",
                api_provider="youtube_analytics",
            )
            for metric, value in zip(LEAK_NAMES, (*LEAK_VALUES, "765432"), strict=True)
        ],
        now=NOW,
    )
    out = tmp_path / "out"
    code = main(["build", "--config", str(root / "site.yaml"), "--out", str(out)])
    assert code == 0 and any(out.rglob("*.html"))
    assert leaks(out) == []


def test_leak_guard_bites_when_a_value_is_in_the_output(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<p>987654</p>", encoding="utf-8")
    assert leaks(tmp_path) == ["index.html:987654"]
    (tmp_path / "index.html").write_text("<p>geo_us_views</p>", encoding="utf-8")
    assert leaks(tmp_path) == ["index.html:geo_us_views"]
