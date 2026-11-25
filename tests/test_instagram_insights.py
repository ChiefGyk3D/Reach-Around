# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: S105
"""Instagram insights collector (#393). Recorded fake responses only.

Every fixture under tests/fixtures/instagram_insights is SYNTHETIC: written by
hand from the response shape docs/research/instagram.md describes. The exact
live shape is UNVERIFIED (#552). No network, no live API, no real credential.
The token is a sentinel.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

import reach_around.commands.counts as counts_cmd
import reach_around.counts.instagram_insights as ins
from fakenet import FakeTransport, reply
from reach_around.cli import main
from reach_around.config.numbers_sources import InstagramSource, NumbersConfig
from reach_around.counts.history import (
    API_PROVIDERS,
    MAX_BATCH,
    PRIVATE_PROVIDERS,
    History,
    HistoryError,
    Record,
    append_records,
    read_history,
)
from reach_around.counts.insights import collect
from reach_around.counts.instagram_insights import (
    COUNTRY_NAMED_MAX,
    FLOOR,
    MAX_ROWS,
    MAX_RUN_FIGURES,
    RETAIN_DAYS,
    InstagramInsightsError,
    collect_instagram,
    deletion_candidates,
)
from reach_around.funnel.social import build_social
from reach_around.net.fetch import fetch_json
from test_counts_insights import violations

FIX = Path(__file__).parent / "fixtures" / "instagram_insights"
TODAY = date(2026, 11, 19)
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
TOKEN = "SENTINEL-IG-TOKEN"
ENV = {"INSTAGRAM_ACCESS_TOKEN": TOKEN}
UID = "17841400000000000"
SRC = InstagramSource.model_validate(
    {"platform": "instagram", "kind": "instagram", "analytics": True, "user_id": UID}
)
PROVIDER = "instagram_insights"
INSIGHTS_URL = f"https://graph.instagram.com/v27.0/{UID}/insights"


def fx(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIX / name).read_text("utf-8"))
    return data


def breakdown(dim: str, results: list[Any]) -> dict[str, Any]:
    return {
        "data": [
            {
                "name": "follower_demographics",
                "total_value": {
                    "breakdowns": [{"dimension_keys": [dim], "results": results}]
                },
            }
        ]
    }


def cell(label: object, value: object) -> dict[str, Any]:
    return {"dimension_values": [label], "value": value}


def router(
    *,
    activity: Any = None,
    age: Any = None,
    gender: Any = None,
    country: Any = None,
) -> FakeTransport:
    """An insights endpoint that answers by the ``metric`` and ``breakdown``."""

    def insights(request: Any) -> Any:
        query = parse_qs(urlsplit(request.path).query)
        metric = query["metric"][0]
        if metric == "follower_demographics":
            answer = {"age": age, "gender": gender, "country": country}[
                query["breakdown"][0]
            ]
        else:
            answer = activity
        if answer is None:
            return reply({"error": "x"}, status=500)
        return reply(answer) if isinstance(answer, dict) else answer

    return FakeTransport({INSIGHTS_URL: insights})


def ok_router() -> FakeTransport:
    return router(
        activity=fx("activity.json"),
        age=fx("age.json"),
        gender=fx("gender.json"),
        country=fx("country.json"),
    )


def fetch_for(transport: FakeTransport) -> Any:
    return partial(fetch_json, transport=transport, sleep=lambda s: None)


def run(transport: FakeTransport, env: Any = None) -> tuple[Any, list[str]]:
    return collect_instagram(
        SRC, ENV if env is None else env, fetch_for(transport), TODAY
    )


def figures(measures: Any, prefix: str = "") -> dict[str, int]:
    return {m.metric: m.value for m in measures if m.metric.startswith(prefix)}


# ---- config ----


def test_analytics_is_off_by_default_and_a_strict_bool() -> None:
    plain = InstagramSource.model_validate(
        {"platform": "instagram", "kind": "instagram"}
    )
    assert plain.analytics is False and plain.user_id is None
    with pytest.raises(ValueError):
        InstagramSource.model_validate({**SRC.model_dump(), "analytics": "yes"})


@pytest.mark.parametrize("bad", ["me", "12a", "", "1" * 25, " 1"])
def test_user_id_must_be_numeric_and_is_needed_for_analytics(bad: str) -> None:
    with pytest.raises(ValueError):
        InstagramSource.model_validate({**SRC.model_dump(), "user_id": bad})
    with pytest.raises(ValueError, match="user_id"):
        InstagramSource.model_validate(
            {"platform": "instagram", "kind": "instagram", "analytics": True}
        )


def test_two_instagram_analytics_sources_are_refused_but_one_per_provider_is_ok() -> (
    None
):
    from pydantic import ValidationError

    def ig(slug: str, analytics: bool) -> dict[str, Any]:
        return {
            "platform": slug,
            "kind": "instagram",
            "analytics": analytics,
            "user_id": UID,
        }

    youtube = {
        "platform": "youtube",
        "kind": "youtube",
        "channel_id": "UC" + "a" * 22,
        "analytics": True,
    }
    with pytest.raises(ValidationError, match="at most one"):
        NumbersConfig.model_validate({"sources": [ig("a-ig", True), ig("b-ig", True)]})
    ok = NumbersConfig.model_validate(
        {"sources": [ig("a-ig", True), ig("b-ig", False), youtube]}
    )
    assert len(ok.sources) == 3  # one YouTube and one Instagram source may both opt in


# ---- requests ----


def test_requests_are_gets_to_one_host_with_the_documented_parameters() -> None:
    transport = ok_router()
    run(transport)
    assert len(transport.calls) == 4  # activity plus three breakdowns, no retries
    for call in transport.calls:
        assert call.method == "GET" and call.host == "graph.instagram.com"
        assert call.body is None
        assert "Authorization" not in call.headers
        parts = urlsplit(call.path)
        assert parts.path == f"/v27.0/{UID}/insights"
        # the token is only ever the access_token query value
        assert parse_qs(parts.query)["access_token"] == [TOKEN]
    queries = [parse_qs(urlsplit(c.path).query) for c in transport.calls]
    activity = next(q for q in queries if q["metric"][0] != "follower_demographics")
    assert activity["metric"] == [
        "reach,views,total_interactions,accounts_engaged,profile_links_taps"
    ]
    assert activity["period"] == ["day"] and activity["metric_type"] == ["total_value"]
    # last settled day is 3 days before today (data lags up to 48 hours); 7 UTC days
    since = int(datetime(2026, 11, 10, tzinfo=UTC).timestamp())
    until = int(datetime(2026, 11, 17, tzinfo=UTC).timestamp())
    assert activity["since"] == [str(since)] and activity["until"] == [str(until)]
    demos = [q for q in queries if q["metric"] == ["follower_demographics"]]
    assert sorted(q["breakdown"][0] for q in demos) == ["age", "country", "gender"]
    assert all(q["metric_type"] == ["total_value"] for q in demos)
    # request construction only: the vendor acceptance is not proven here (#552)
    assert all(q["timeframe"] == ["last_30_days"] for q in demos)
    # per-metric table: only demographics carry timeframe; the activity metrics do not
    for q in queries:
        metric = q["metric"][0]
        if metric in ins.TIMEFRAME_ALLOWED:
            tf = q["timeframe"][0]
            assert tf in ins.TIMEFRAME_ALLOWED[metric]
            assert tf not in ins.TIMEFRAME_REMOVED_V20.get(metric, frozenset())
        else:
            assert "timeframe" not in q


def test_no_request_follows_a_redirect_and_none_is_a_mutation() -> None:
    seen: list[tuple[str, Any, Any]] = []

    def spy(url: str, **kw: Any) -> dict[str, object]:
        seen.append((url, kw.get("follow_redirects"), kw.get("method")))
        return {"data": []}

    collect_instagram(SRC, ENV, spy, TODAY)
    assert len(seen) == 4
    assert [flag for _, flag, _ in seen] == [False] * 4
    assert all(method in (None, "GET") for _, _, method in seen)


def test_a_redirect_answer_is_a_fixed_word_and_the_target_is_never_called() -> None:
    other = FakeTransport(
        {"https://evil.example/": reply({"data": []})}
    )  # would see the token if followed
    transport = FakeTransport(
        {
            INSIGHTS_URL: reply(
                {}, status=302, headers={"location": "https://evil.example/x"}
            ),
            "https://evil.example/": other,
        }
    )
    measures, words = run(transport)
    assert measures == [] and words == ["redirect"] * 4
    assert not other.calls and all(
        c.host == "graph.instagram.com" for c in transport.calls
    )


def test_the_host_is_pinned() -> None:
    seen: list[str] = []

    def spy(url: str, **kw: Any) -> dict[str, object]:
        seen.append(url)
        return {"data": []}

    collect_instagram(SRC, ENV, spy, TODAY)
    assert seen and all(
        urlsplit(u).hostname == "graph.instagram.com" and urlsplit(u).scheme == "https"
        for u in seen
    )


# ---- parsing ----


def test_activity_gives_one_window_total_per_metric() -> None:
    measures, words = run(ok_router())
    assert words == []
    assert figures(measures, "reach_7d") == {"reach_7d": 4210}
    got = {m.metric: m.value for m in measures if not m.metric.startswith("fdemo_")}
    assert got == {
        "reach_7d": 4210,
        "views_7d": 15320,
        "interactions_7d": 880,
        "engaged_7d": 412,
        "link_taps_7d": 37,
    }
    note = next(m.note for m in measures if m.metric == "reach_7d")
    assert "UTC" in note and "2026-11-10" in note and "2026-11-16" in note


def test_demographics_fold_cells_under_the_floor_into_other() -> None:
    assert FLOOR == 5
    measures, _ = run(ok_router())
    demo = figures(measures, "fdemo_")
    assert demo == {
        "fdemo_age_18_24": 120,
        "fdemo_age_25_34": 300,
        "fdemo_age_other": 7,
        "fdemo_gender_f": 200,
        "fdemo_gender_m": 190,
        "fdemo_gender_other": 4,
        "fdemo_country_us": 400,
        "fdemo_country_ca": 60,
        "fdemo_country_gb": 5,  # exactly at the floor is kept
        "fdemo_country_other": 6,
    }
    assert not any(v < FLOOR for k, v in demo.items() if not k.endswith("_other"))


def test_only_the_largest_countries_are_named() -> None:
    rows = [
        cell(f"{a}{b}", 1000 - i)
        for i, (a, b) in enumerate(
            (chr(65 + i // 26), chr(65 + i % 26)) for i in range(40)
        )
    ]
    rows = [r for r in rows if r["dimension_values"][0] != "ZZ"]
    measures, words = run(
        router(
            activity=fx("empty.json"),
            age=fx("empty.json"),
            gender=fx("empty.json"),
            country=breakdown("country", rows),
        )
    )
    geo = figures(measures, "fdemo_country_")
    named = [k for k in geo if not k.endswith("_other")]
    assert len(named) == COUNTRY_NAMED_MAX and "fdemo_country_other" in geo
    assert words == ["no_data"]  # activity answered an empty set; nothing else failed
    assert geo["fdemo_country_other"] == sum(r["value"] for r in rows) - sum(
        v for k, v in geo.items() if k != "fdemo_country_other"
    )


def test_unknown_country_code_is_folded_not_named() -> None:
    measures, _ = run(
        router(
            activity=fx("empty.json"),
            age=fx("empty.json"),
            gender=fx("empty.json"),
            country=breakdown(
                "country",
                [cell("ZZ", 900), cell("us", 80), cell("USA", 70), cell("US", 60)],
            ),
        )
    )
    assert figures(measures, "fdemo_country_") == {
        "fdemo_country_us": 60,
        "fdemo_country_other": 1050,
    }


def test_empty_answers_store_nothing_and_are_not_zero() -> None:
    transport = router(
        activity=fx("empty.json"),
        age=fx("empty.json"),
        gender={"data": [{"name": "follower_demographics", "total_value": {}}]},
        country={
            "data": [
                {
                    "name": "follower_demographics",
                    "total_value": {"breakdowns": [{"dimension_keys": ["country"]}]},
                }
            ]
        },
    )
    measures, words = run(transport)
    assert measures == []
    assert words == ["no_data"]  # only the activity report treats empty as a failure


@pytest.mark.parametrize(
    "payload",
    [
        {"data": "x"},
        {"data": [1]},
        {
            "data": [
                {"name": "reach", "total_value": {"value": 1}},
                {"name": "reach", "total_value": {"value": 2}},
            ]
        },
        {"nodata": 1},
    ],
)
def test_hostile_activity_shapes_store_nothing_and_use_fixed_words(
    payload: dict[str, Any],
) -> None:
    measures, words = run(
        router(
            activity=payload,
            age=fx("empty.json"),
            gender=fx("empty.json"),
            country=fx("empty.json"),
        )
    )
    assert not [m for m in measures if m.metric.endswith("_7d")]
    assert words and set(words) <= {"bad_report", "no_data"}


@pytest.mark.parametrize("value", [True, -1, 1.5, "7", None, 10**13, float("nan")])
def test_bad_numbers_skip_only_that_row(value: object) -> None:
    bad = fx("activity.json")
    bad["data"][1]["total_value"]["value"] = value  # views
    measures, words = run(
        router(
            activity=bad,
            age=fx("empty.json"),
            gender=fx("empty.json"),
            country=fx("empty.json"),
        )
    )
    assert words == ["bad_row"]
    got = figures(measures)
    assert "views_7d" not in got and got["reach_7d"] == 4210


@pytest.mark.parametrize("label", ["0-12", "unknown", "", 7, None, "18-24 "])
def test_age_labels_outside_the_allowlist_are_bad_rows_not_folded(
    label: object,
) -> None:
    measures, words = run(
        router(
            activity=fx("activity.json"),
            age=breakdown("age", [cell("25-34", 50), cell(label, 99)]),
            gender=fx("empty.json"),
            country=fx("empty.json"),
        )
    )
    assert words == ["bad_row"]
    assert figures(measures, "fdemo_age_") == {"fdemo_age_25_34": 50}


def test_duplicate_labels_fail_that_report_only() -> None:
    measures, words = run(
        router(
            activity=fx("activity.json"),
            age=breakdown("age", [cell("25-34", 50), cell("25-34", 60)]),
            gender=fx("gender.json"),
            country=fx("country.json"),
        )
    )
    assert words == ["bad_report"]
    assert not figures(measures, "fdemo_age_") and figures(measures, "fdemo_gender_")


def test_wrong_breakdown_key_fails_that_report() -> None:
    measures, words = run(
        router(
            activity=fx("activity.json"),
            age=breakdown("city", [cell("18-24", 50)]),
            gender=fx("gender.json"),
            country=fx("country.json"),
        )
    )
    assert words == ["bad_report"] and not figures(measures, "fdemo_age_")


def test_oversized_answer_is_refused_whole_and_alone() -> None:
    rows = [cell("US", 9)] * (MAX_ROWS + 1)
    measures, words = run(
        router(
            activity=fx("activity.json"),
            age=fx("age.json"),
            gender=fx("gender.json"),
            country=breakdown("country", rows),
        )
    )
    assert words == ["too_large"]
    assert figures(measures, "fdemo_age_") and figures(measures, "reach_7d")


def test_folded_total_over_the_history_maximum_is_refused_not_clamped() -> None:
    big = 10**12  # allowed alone; two unnamed countries sum past the maximum
    many = [cell(f"A{chr(65 + i)}", big) for i in range(26)] + [cell("BB", big)]
    measures, words = run(
        router(
            activity=fx("activity.json"),
            age=fx("empty.json"),
            gender=fx("empty.json"),
            country=breakdown("country", many),
        )
    )
    assert words == ["bad_aggregate"] and not figures(measures, "fdemo_country_")


def test_the_run_fits_one_history_append_and_each_report_has_its_own_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert MAX_RUN_FIGURES <= MAX_BATCH // 2
    import reach_around.counts.instagram_insights as module

    monkeypatch.setattr(module, "AGE_MAX_FIGURES", 1)
    measures, words = run(ok_router())
    assert words == ["too_large"]
    assert figures(measures, "fdemo_gender_") and figures(measures, "reach_7d")
    assert not figures(measures, "fdemo_age_")


def test_the_whole_run_has_its_own_figure_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import reach_around.counts.instagram_insights as module

    # five activity figures fit; the next report would pass the run limit
    monkeypatch.setattr(module, "MAX_RUN_FIGURES", 6)
    measures, words = run(ok_router())
    assert "too_large" in words and len(measures) <= 6
    assert figures(measures, "reach_7d")


def test_the_word_for_any_other_exception_is_fixed_and_carries_no_text() -> None:
    from reach_around.counts.instagram_insights import _word

    class Odd(Exception):
        kind = 7  # not a string

    for exc in (RuntimeError(TOKEN), Odd(TOKEN), ValueError(f"url {TOKEN}")):
        assert _word(exc) == "fetch_failed"


# ---- failures ----


@pytest.mark.parametrize(
    ("status", "word"),
    [
        (401, "token_rejected"),
        (403, "scope_missing"),
        (400, "http_400"),
        (429, "rate_limited"),
        (500, "server_error"),
    ],
)
def test_http_errors_use_fixed_words_and_are_bounded(status: int, word: str) -> None:
    transport = FakeTransport(
        {
            INSIGHTS_URL: reply(
                {"error": {"message": f"SECRET-BODY {TOKEN}"}}, status=status
            )
        }
    )
    _, words = run(transport)
    assert word in words
    assert "SECRET-BODY" not in " ".join(words) and TOKEN not in " ".join(words)
    # bounded: each of the four reports takes at most the fetch layer's 3 attempts
    assert len(transport.calls) <= 4 * 3
    assert all(c.method == "GET" for c in transport.calls)


def test_missing_or_bad_credential_fails_before_any_request() -> None:
    transport = ok_router()
    for env, kind in (
        ({}, "missing_credential"),
        ({"INSTAGRAM_ACCESS_TOKEN": "has space"}, "invalid_credential"),
        ({"INSTAGRAM_ACCESS_TOKEN": "x" * 600}, "invalid_credential"),
    ):
        with pytest.raises(InstagramInsightsError) as err:
            run(transport, env)
        assert err.value.kind == kind
    assert not transport.calls


def test_forged_flags_are_refused_at_use() -> None:
    transport = ok_router()
    for update in (
        {"analytics": "yes"},
        {"user_id": None},
        {"user_id": "../x"},
        {"user_id": "1/../2"},
    ):
        forged = SRC.model_copy(update=update)
        with pytest.raises(InstagramInsightsError) as err:
            collect_instagram(forged, ENV, fetch_for(transport), TODAY)
        assert err.value.kind == "bad_source_setting"
    assert not transport.calls


def test_unexpected_exception_has_no_secret_in_it() -> None:
    def boom(url: str, **kw: Any) -> dict[str, object]:
        raise RuntimeError(f"{TOKEN} in the url {url}")

    measures, words = collect_instagram(SRC, ENV, boom, TODAY)
    assert measures == [] and words == ["fetch_failed"] * 4
    assert TOKEN not in " ".join(words)


def test_the_token_never_reaches_stdout_stderr_or_an_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run(FakeTransport({INSIGHTS_URL: reply({}, status=401)}))
    collect(
        [SRC],
        frozenset({"instagram"}),
        ENV,
        fetch_for(FakeTransport({INSIGHTS_URL: reply({}, status=403)})),
        NOW,
    )
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err


# ---- collect() wiring ----


def test_collect_is_opt_in_and_needs_a_good_follower_read() -> None:
    transport = ok_router()
    off = SRC.model_copy(update={"analytics": False})
    assert collect([off], frozenset({"instagram"}), ENV, fetch_for(transport), NOW) == (
        [],
        [],
    )
    assert collect([SRC], frozenset(), ENV, fetch_for(transport), NOW) == ([], [])
    assert not transport.calls


def test_collect_tags_every_figure_with_the_provider_whatever_the_slug() -> None:
    renamed = SRC.model_copy(update={"platform": "socials-instagram"})
    found, failures = collect(
        [renamed], frozenset({"socials-instagram"}), ENV, fetch_for(ok_router()), NOW
    )
    assert not failures and found
    assert {f.platform for f in found} == {"socials-instagram"}
    assert {f.api_provider for f in found} == {PROVIDER}
    assert all(f.note and f.as_of_utc is None for f in found)


def test_collect_reports_credential_problem_as_a_word() -> None:
    found, failures = collect(
        [SRC], frozenset({"instagram"}), {}, fetch_for(ok_router()), NOW
    )
    assert found == []
    assert [(f.platform, f.word) for f in failures] == [
        ("instagram", "missing_credential")
    ]


# ---- provenance, retention, deletion ----


def test_the_tag_is_in_the_closed_lists() -> None:
    assert PROVIDER in API_PROVIDERS and PROVIDER in PRIVATE_PROVIDERS
    assert "youtube_analytics" in PRIVATE_PROVIDERS
    assert "youtube_data_api" not in PRIVATE_PROVIDERS


def rec(
    metric: str = "views_7d",
    as_of: str = "2026-10-01T00:00:00Z",
    provider: str | None = PROVIDER,
    platform: str = "instagram",
    source: str = "api",
) -> Record:
    return Record(platform, metric, 5, as_of, source, "run-1", api_provider=provider)


def test_deletion_candidates_older_than_retention_and_on_revocation() -> None:
    assert RETAIN_DAYS == 30
    old, fresh = rec(), rec(as_of="2026-11-16T00:00:00Z")
    other_provider = rec(provider="youtube_analytics")
    untagged = rec(provider=None)
    items = [old, fresh, other_provider, untagged]
    assert deletion_candidates(items, TODAY) == [old]
    assert deletion_candidates(items, TODAY, revoked=True) == [old, fresh]


def test_a_correction_is_selected_with_its_original() -> None:
    original = rec()
    fix = Record(
        "instagram",
        "views_7d",
        9,
        "2026-10-01T00:00:00Z",
        "api",
        "r2",
        note="fixed",
        type="correction",
        api_provider=PROVIDER,
    )
    legacy = Record(
        "instagram",
        "views_7d",
        9,
        "2026-10-01T00:00:00Z",
        "api",
        "r3",
        note="fixed",
        type="correction",
    )
    assert deletion_candidates([original, fix, legacy], TODAY) == [
        original,
        fix,
        legacy,
    ]


def test_records_are_identified_only_by_the_provider_tag() -> None:
    day = "2026-10-01T09:00:00Z"
    untagged = Record("instagram", "views_7d", 987654, day, "api", "r")
    tagged = Record(
        "instagram", "views_7d", 123456, day, "api", "r2", api_provider=PROVIDER
    )
    today = date(2026, 11, 20)
    block = build_social(History((untagged,)), cutover=None, today=today)
    assert block["refused"] == [] and deletion_candidates([untagged], today) == []
    block = build_social(History((tagged,)), cutover=None, today=today)
    assert block["platforms"] == [] and [i["metric"] for i in block["refused"]] == [
        "private analytics"
    ]
    assert deletion_candidates([tagged], today, revoked=True) == [tagged]


def test_near_miss_tags_are_not_private() -> None:
    for tag in ("instagram_insights ", "Instagram_Insights", "instagram"):
        with pytest.raises(HistoryError, match="not recognized"):
            append_records(
                Path("/nonexistent-never-written"), [rec(provider=tag)], now=NOW
            )


def test_the_history_validator_refuses_a_correction_with_another_provider(
    tmp_path: Path,
) -> None:
    path = tmp_path / "h.jsonl"
    append_records(path, [rec()], now=NOW)

    def fix(provider: str | None) -> Record:
        return Record(
            "instagram",
            "views_7d",
            7,
            "2026-10-01T00:00:00Z",
            "api",
            "r2",
            note="fixed",
            type="correction",
            api_provider=provider,
        )

    for provider in (None, "youtube_analytics"):
        with pytest.raises(HistoryError, match="provider"):
            append_records(path, [fix(provider)], now=NOW)
    append_records(path, [fix(PROVIDER)], now=NOW)


def test_redaction_selects_by_provider_tag(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    append_records(
        path,
        [
            rec("views_7d", "2026-11-01T00:00:00Z"),
            rec("reach_7d", "2026-11-01T00:00:00Z", provider=None),
            rec("followers", "2026-11-01T00:00:00Z", provider="youtube_analytics"),
        ],
        now=NOW,
    )
    args = [
        "counts",
        "history-redact",
        "--file",
        str(path),
        "--provider",
        PROVIDER,
        "--reason",
        "user_request",
        "--trigger-date",
        "2026-11-19",
    ]
    assert main(args) == 0  # dry run
    assert main([*args, "--confirm"]) == 0
    records = read_history(path).records
    live = [r for r in records if r.type == "record"]
    assert [r.metric for r in live] == ["reach_7d", "followers"]


# ---- end to end through counts refresh ----

CONFIG = """\
version: 1
creator: {name: Test Creator}
tabs:
  - id: home
    title: Home
    blocks:
      - id: socials
        items:
          - {platform: instagram, handle: "@example", url: "https://example.com/ig"}
numbers:
  history_file: history/social.jsonl
  sources:
    - platform: instagram
      kind: instagram
      analytics: true
      user_id: "17841400000000000"
"""
ME = "https://graph.instagram.com/v27.0/me"


def with_followers(transport: FakeTransport) -> FakeTransport:
    transport.routes[ME] = reply({"followers_count": 321})
    return transport


def refresh(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    transport: FakeTransport,
    config: str = CONFIG,
) -> int:
    (root / "history").mkdir(exist_ok=True)
    (root / "site.yaml").write_text(config, encoding="utf-8")
    monkeypatch.setattr(counts_cmd, "default_sleep", lambda s: None)
    monkeypatch.setattr(counts_cmd, "default_transport", lambda: transport)
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", TOKEN)
    return main(["counts", "refresh", "--config", str(root / "site.yaml")])


def test_refresh_stores_private_figures_tagged_instagram_insights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert refresh(tmp_path, monkeypatch, with_followers(ok_router())) == 0
    path = tmp_path / "history" / "social.jsonl"
    records = read_history(path).records
    private = [r for r in records if r.api_provider == PROVIDER]
    assert {r.metric for r in private} >= {
        "reach_7d",
        "fdemo_age_18_24",
        "fdemo_country_us",
    }
    assert all(r.source == "api" and r.platform == "instagram" for r in private)
    follower = next(r for r in records if r.metric == "followers")
    assert follower.api_provider is None  # the public follower count is not private
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err
    # a second run the same day writes nothing new
    before = path.read_bytes()
    assert refresh(tmp_path, monkeypatch, with_followers(ok_router())) == 0
    assert path.read_bytes() == before


def test_a_renamed_slug_is_still_tagged_and_refused_by_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = CONFIG.replace(
        "platform: instagram, kind", "platform: socials-instagram, kind"
    )
    assert refresh(tmp_path, monkeypatch, with_followers(ok_router()), config) == 0
    records = list(read_history(tmp_path / "history" / "social.jsonl").records)
    private = [r for r in records if r.metric not in ("followers",)]
    assert private and all(r.api_provider == PROVIDER for r in private)
    block = build_social(History(tuple(records)), cutover=None, today=TODAY)
    assert {i["metric"] for i in block["refused"]} == {"private analytics"}
    assert all(s["metric"] == "followers" for s in block["platforms"])


def test_refresh_failure_keeps_followers_and_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = with_followers(router())  # every insights answer is a 500
    transport.routes[INSIGHTS_URL] = reply({}, status=403)
    assert refresh(tmp_path, monkeypatch, transport) == counts_cmd.EXIT_SOURCE_FAILED
    records = read_history(tmp_path / "history" / "social.jsonl").records
    assert [r.metric for r in records] == ["followers"]  # a gap, never a zero
    err = capsys.readouterr().err
    assert "instagram: insights error (scope_missing)" in err and TOKEN not in err


def test_refresh_without_the_follower_read_collects_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = ok_router()
    transport.routes[ME] = reply({}, status=401)
    refresh(tmp_path, monkeypatch, transport)
    assert not any(c.path.split("?")[0].endswith("/insights") for c in transport.calls)


def tagged(i: int, platform: str = "instagram", tag: str = PROVIDER) -> Record:
    return Record(
        platform, f"m{i}", i, "2026-11-01T00:00:00Z", "api", "r", api_provider=tag
    )


def plain(i: int) -> Record:
    return Record(f"p{i}", "followers", i, "2026-11-01T00:00:00Z", "api", "r")


def test_fit_batch_drops_only_private_figures_of_this_provider() -> None:
    others = [plain(i) for i in range(MAX_BATCH - 10)]
    batch = others + [tagged(i) for i in range(20)]
    kept, dropped = counts_cmd.fit_batch(batch)
    assert kept == others and dropped == ["instagram"]
    # both private providers go together, and both platforms are named
    mixed = others + [tagged(1), tagged(2, "youtube", "youtube_analytics")] * 6
    kept, dropped = counts_cmd.fit_batch(mixed)
    assert kept == others and dropped == ["instagram", "youtube"]
    # at the limit nothing is dropped
    assert counts_cmd.fit_batch(others + [tagged(i) for i in range(10)])[1] == []
    # a record without the tag is never dropped, whatever its metric name
    plain_named = Record("instagram", "views_7d", 1, "2026-11-01T00:00:00Z", "api", "r")
    kept, dropped = counts_cmd.fit_batch(others + [plain_named] + [tagged(1)] * 20)
    assert plain_named in kept and dropped == ["instagram"]


def test_refresh_drops_instagram_insights_over_the_limit_and_keeps_followers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(counts_cmd, "MAX_BATCH", 3)
    code = refresh(tmp_path, monkeypatch, with_followers(ok_router()))
    assert code == counts_cmd.EXIT_SOURCE_FAILED
    records = read_history(tmp_path / "history" / "social.jsonl").records
    assert [r.metric for r in records] == ["followers"]
    assert "instagram: insights error (too_large)" in capsys.readouterr().err


def test_refresh_tells_the_owner_how_many_records_are_past_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "history").mkdir()
    append_records(
        tmp_path / "history" / "social.jsonl",
        [
            Record(
                "instagram",
                f"views_{i}d",
                1_234_567,
                "2026-01-01T00:00:00Z",
                "api",
                "old",
                api_provider=PROVIDER,
            )
            for i in range(1, 4)
        ],
        now=NOW,
    )
    assert refresh(tmp_path, monkeypatch, with_followers(ok_router())) == 0
    err = capsys.readouterr().err
    assert (
        "instagram: retention notice: 3 insights record(s) are past 30 days; "
        "remove them with the history tombstone tool (#586)"
    ) in err
    assert "1234567" not in err


def test_no_retention_notice_when_nothing_is_past_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert refresh(tmp_path, monkeypatch, with_followers(ok_router())) == 0
    assert "instagram: retention notice" not in capsys.readouterr().err


# ---- boundary and leak ----


def test_module_cannot_be_reached_from_public_side(tmp_path: Path) -> None:
    for rel, text in {
        "__init__.py": "",
        "counts/__init__.py": "",
        "counts/instagram_insights.py": "X = 1\n",
        "site/__init__.py": "",
        "site/a.py": "from reach_around.counts import instagram_insights\n",
    }.items():
        target = tmp_path / "reach_around" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    assert violations(tmp_path), "the guard must bite on a direct import"


PUBLIC_SITE = """\
version: 1
creator: {name: Test Creator}
tabs:
  - id: home
    title: Home
    blocks:
      - id: socials
        items:
          - {platform: instagram, handle: "@example", url: "https://example.com/ig"}
numbers:
  history_file: history/social.jsonl
  sources:
    - platform: instagram
      kind: instagram
      analytics: true
      user_id: "17841400000000000"
"""
VALUES = {
    "reach_7d": 987_654,
    "views_7d": 1_234_567_890,
    "fdemo_age_18_24": 4_025,
    "fdemo_country_other": 3,
}


def write_history(root: Path) -> Path:
    path = root / "history" / "social.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    append_records(
        path,
        [
            Record(
                "instagram",
                m,
                v,
                "2026-11-16T00:00:00Z",
                "api",
                "r1",
                note="n",
                api_provider=PROVIDER,
            )
            for m, v in VALUES.items()
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


def test_public_output_is_byte_identical_with_and_without_insights(
    tmp_path: Path,
) -> None:
    without = build_tree(make_instance(tmp_path / "a") / "site.yaml", tmp_path / "oa")
    with_root = make_instance(tmp_path / "b")
    write_history(with_root)
    with_history = build_tree(with_root / "site.yaml", tmp_path / "ob")
    assert without.keys() == with_history.keys() and without
    assert [k for k in without if without[k] != with_history[k]] == []


def test_the_build_never_opens_the_history_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import builtins
    import io
    import os

    root = make_instance(tmp_path / "inst")
    history = write_history(root)
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


def test_differential_guard_bites_on_a_derived_form(tmp_path: Path) -> None:
    """Negative case: a tree that differs only by a derived '987.7k' is caught."""
    base = build_tree(make_instance(tmp_path / "a") / "site.yaml", tmp_path / "oa")
    altered = dict(base)
    index = next(k for k in altered if k.endswith(".html"))
    altered[index] = altered[index] + b"<p>Reach 987.7k</p>"
    assert [k for k in base if base[k] != altered[k]] == [index]


def test_floors_and_caps_are_pinned_by_literals() -> None:
    from reach_around.counts import instagram_insights as module

    assert module.FLOOR == 5 and module.COUNTRY_NAMED_MAX == 25
    assert module.MAX_RUN_FIGURES == 43  # 5 + 8 + 4 + 26
    assert module.AGE_MAX_FIGURES == 8 and module.COUNTRY_MAX_FIGURES == 26
    answers = dict(
        activity=fx("empty.json"),
        age=breakdown("age", [cell("18-24", 5), cell("25-34", 4)]),
        gender=breakdown("gender", [cell("F", 5), cell("M", 4), cell("U", 4)]),
        country=breakdown("country", [cell("US", 5), cell("CA", 4)]),
    )
    measures, _ = run(router(**answers))
    assert figures(measures, "fdemo_") == {
        "fdemo_age_18_24": 5,  # exactly at the floor is kept
        "fdemo_age_other": 4,  # just under is folded
        "fdemo_gender_f": 5,
        "fdemo_gender_other": 8,
        "fdemo_country_us": 5,
        "fdemo_country_other": 4,
    }


def test_a_full_age_answer_with_an_under_floor_cell_passes() -> None:
    labels = list(["13-17", "18-24", "25-34", "35-44", "45-54", "55-64", "65+"])
    rows = [cell(label, 50) for label in labels[:-1]] + [cell("65+", 2)]
    measures, words = run(
        router(
            activity=fx("empty.json"),
            age=breakdown("age", rows),
            gender=fx("empty.json"),
            country=fx("empty.json"),
        )
    )
    assert words == ["no_data"]  # only the empty activity report; age is not too_large
    age = figures(measures, "fdemo_age_")
    assert len(age) == 7 and age["fdemo_age_other"] == 2


@pytest.mark.parametrize(
    ("status", "word"), [(401, "token_rejected"), (403, "scope_missing")]
)
def test_a_rejected_credential_stops_the_run_after_one_request(
    status: int, word: str
) -> None:
    transport = FakeTransport({INSIGHTS_URL: reply({}, status=status)})
    measures, words = run(transport)
    assert measures == [] and words == [word]
    assert len(transport.calls) == 1
    # a rejection that is not about the credential does not stop the run
    other = FakeTransport({INSIGHTS_URL: reply({}, status=500)})
    run(other)
    assert len(other.calls) > 4


# ---- forged kinds and token rotation (review findings) ----


class SpyEnv(dict[str, str]):
    """An environment that records every name read."""

    def __init__(self, data: dict[str, str]) -> None:
        super().__init__(data)
        self.reads: list[str] = []

    def get(self, key: str, default: Any = None) -> Any:
        self.reads.append(key)
        return super().get(key, default)

    def __getitem__(self, key: str) -> str:
        self.reads.append(key)
        return super().__getitem__(key)


OTHER_TOKENS = {
    "THREADS_ACCESS_TOKEN": "SENTINEL-THREADS-TOKEN",
    "TWITCH_USER_ACCESS_TOKEN": "SENTINEL-TWITCH-TOKEN",
    "YOUTUBE_API_KEY": "SENTINEL-YT-KEY",
}


@pytest.mark.parametrize("kind", ["threads", "twitch", "youtube", "tiktok", "x", ""])
def test_a_forged_kind_never_sends_another_providers_credential(kind: str) -> None:
    transport = ok_router()
    env = SpyEnv({**ENV, **OTHER_TOKENS})
    forged = SRC.model_copy(update={"kind": kind})
    with pytest.raises(InstagramInsightsError) as err:
        collect_instagram(forged, env, fetch_for(transport), TODAY)
    assert err.value.kind == "bad_source_setting"
    assert not transport.calls and env.reads == []  # no request, no env read
    # the same through collect(): a fixed word, nothing sent
    found, failures = collect(
        [forged], frozenset({"instagram"}), env, fetch_for(transport), NOW
    )
    assert not transport.calls and env.reads == []
    assert found == [] and [f.word for f in failures] == ["bad_source_setting"]


def rotating_transport() -> FakeTransport:
    transport = ok_router()
    transport.routes["https://graph.instagram.com/refresh_access_token"] = reply(
        {
            "access_token": "SENTINEL-IG-NEW",
            "token_type": "bearer",
            "expires_in": 5184000,
        }
    )
    transport.routes[ME] = reply({"followers_count": 321})
    return transport


def test_insights_never_send_the_old_token_after_a_rotation() -> None:
    from reach_around.counts.meta_user import follower_total

    stored: list[str] = []

    class Sink:
        def store(self, token: Any, name: str, source: str, expires_on: Any) -> bool:
            stored.append(token.value)
            return True

        def alert(self, alert: Any) -> None:
            pass

    source = SRC.model_copy(
        update={
            "auto_refresh": True,
            "token_expires": date(2026, 11, 25),  # inside the refresh window
            "refresh_within_days": 10,
        }
    )
    transport = rotating_transport()
    fetch = fetch_for(transport)
    # the follower read rotates the token (the old one is sent to refresh it)
    assert follower_total(source, ENV, fetch, Sink(), TODAY) == 321
    assert stored == ["SENTINEL-IG-NEW"]
    rotated_at = len(transport.calls)
    # the insights step of the same run must not use the replaced token
    found, failures = collect([source], frozenset({"instagram"}), ENV, fetch, NOW)
    assert found == [] and [f.word for f in failures] == ["refresh_pending"]
    assert len(transport.calls) == rotated_at  # nothing was sent at all
    assert not any(TOKEN in c.path for c in transport.calls[rotated_at:])


def test_a_source_outside_the_refresh_window_still_collects() -> None:
    far = SRC.model_copy(
        update={
            "auto_refresh": True,
            "token_expires": date(2027, 1, 20),
            "refresh_within_days": 10,
        }
    )
    measures, words = run_source(far)
    assert words == [] and measures
    # no expiry known: the follower read never refreshes, so insights collect
    unknown = SRC.model_copy(update={"auto_refresh": True, "token_expires": None})
    assert run_source(unknown)[1] == []
    # auto_refresh off: a due token is only reported, never rotated
    due = SRC.model_copy(update={"token_expires": date(2026, 11, 25)})
    assert run_source(due)[1] == []


def run_source(source: InstagramSource) -> tuple[Any, list[str]]:
    return collect_instagram(source, ENV, fetch_for(ok_router()), TODAY)


def test_the_refresh_window_edge_matches_the_follower_read() -> None:
    def source(day: date) -> InstagramSource:
        return SRC.model_copy(
            update={
                "auto_refresh": True,
                "token_expires": day,
                "refresh_within_days": 10,
            }
        )

    with pytest.raises(InstagramInsightsError, match="refresh_pending"):
        run_source(source(date(2026, 11, 29)))  # exactly 10 days left
    assert run_source(source(date(2026, 11, 30)))[1] == []  # 11 days left


def test_timeframe_removals_are_scoped_per_metric() -> None:
    # The v20.0 removals name two metrics; follower_demographics is not one of them
    # (vendor changelog read 2026-11-24), so nothing is removed for it.
    assert set(ins.TIMEFRAME_REMOVED_V20) == {
        "reached_audience_demographics",
        "engaged_audience_demographics",
    }
    assert ins.DEMOGRAPHICS_METRIC not in ins.TIMEFRAME_REMOVED_V20


def test_demographics_timeframe_is_pinned_to_last_30_days() -> None:
    # No behavior change to the working request; this_month is not adopted until
    # #552 shows it is accepted and returns the same window.
    assert ins.TIMEFRAME == "last_30_days"
    assert ins.TIMEFRAME in ins.TIMEFRAME_ALLOWED[ins.DEMOGRAPHICS_METRIC]
