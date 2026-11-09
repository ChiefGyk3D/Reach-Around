# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: S105
"""TikTok video stats (#395) and Threads insights (#394). Mocked transport only.

Every fixture is SYNTHETIC: built by hand from the response shapes the vendor
docs describe (read 2026-11-09). Nothing is recorded from a live call. Values
and tokens are sentinels.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path
from typing import Any

import pytest

import reach_around.commands.counts as counts_cmd
from fakenet import FakeTransport, reply
from reach_around.cli import main
from reach_around.config.numbers_sources import ThreadsSource, TiktokSource
from reach_around.counts.history import read_history
from reach_around.counts.insights import OWNER_REPORT_ONLY, collect
from reach_around.counts.meta_user import MetaError, threads_insights
from reach_around.counts.tiktok_user import TiktokError, video_stats
from reach_around.net.fetch import FetchError, fetch_json

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
NOW_S = int(NOW.timestamp())
DAY = 86_400
TT_ENV = {"TIKTOK_ACCESS_TOKEN": "SENTINEL-TT-ACCESS"}
TH_ENV = {"THREADS_ACCESS_TOKEN": "SENTINEL-TH-TOKEN"}
SECRETS = [*TT_ENV.values(), *TH_ENV.values()]
TT = TiktokSource.model_validate(
    {"platform": "tiktok", "kind": "tiktok", "insights": True}
)
TH = ThreadsSource.model_validate(
    {"platform": "threads", "kind": "threads", "user_id": "42", "insights": True}
)
VIDEO = "https://open.tiktokapis.com/v2/video/list/"
INSIGHTS = "https://graph.threads.com/v1.0/42/threads_insights"


def video(age_days: float, views: Any = 100, likes: Any = 10, **kw: Any) -> dict:
    out = {
        "id": "7000000000000000001",
        "create_time": NOW_S - int(age_days * DAY),
        "view_count": views,
        "like_count": likes,
        "comment_count": 2,
        "share_count": 3,
    }
    out.update(kw)
    return {k: v for k, v in out.items() if v is not ...}


def page(videos: list, more: bool = False, cursor: int = 1_700_000_000_000) -> Any:
    return reply(
        {
            "data": {"videos": videos, "cursor": cursor, "has_more": more},
            "error": {"code": "ok", "message": "", "log_id": "x"},
        }
    )


def tt_fetch(*pages: Any) -> tuple[Any, FakeTransport]:
    queue = list(pages)
    transport = FakeTransport({VIDEO: lambda request: queue.pop(0)})
    return partial(fetch_json, transport=transport, sleep=lambda s: None), transport


def series(name: str, values: list[int]) -> dict:
    return {
        "name": name,
        "period": "day",
        "values": [
            {"value": v, "end_time": f"2026-11-0{i + 3}T08:00:00+0000"}
            for i, v in enumerate(values)
        ],
    }


def total(name: str, value: Any) -> dict:
    return {"name": name, "total_value": {"value": value}}


def th_ok() -> Any:
    return reply(
        {
            "data": [
                series("views", [10, 20, 30]),
                total("likes", 5),
                total("replies", 4),
                total("reposts", 3),
                total("quotes", 2),
            ]
        }
    )


def th_fetch(answer: Any) -> tuple[Any, FakeTransport]:
    transport = FakeTransport({INSIGHTS: answer})
    return partial(fetch_json, transport=transport, sleep=lambda s: None), transport


# ---- TikTok parsing, windows ----


def test_tiktok_sums_only_videos_inside_the_28_day_window() -> None:
    fetch, transport = tt_fetch(
        page([video(1, 100), video(27, 50), video(29, 9999, 9999)])
    )
    out = video_stats(TT, TT_ENV, fetch, NOW_S)
    assert out == {
        "videos_28d": 2,
        "video_views_28d": 150,
        "video_engagement_28d": (10 + 2 + 3) * 2,
    }
    call = transport.calls[0]
    assert call.method == "POST"
    assert json.loads(call.body) == {"max_count": 20}
    assert call.headers["Authorization"] == "Bearer SENTINEL-TT-ACCESS"
    assert call.headers["Content-Type"] == "application/json"
    assert "SENTINEL" not in call.path


def test_tiktok_pages_with_cursor_until_the_window_ends() -> None:
    fetch, transport = tt_fetch(
        page([video(1, 1)], more=True, cursor=2_000), page([video(40, 7)], cursor=1_000)
    )
    assert video_stats(TT, TT_ENV, fetch, NOW_S)["video_views_28d"] == 1
    assert json.loads(transport.calls[1].body) == {"max_count": 20, "cursor": 2_000}


def test_tiktok_no_videos_is_a_real_zero() -> None:
    fetch, _ = tt_fetch(page([]))
    out = video_stats(TT, TT_ENV, fetch, NOW_S)
    assert out["video_views_28d"] == 0 and out["videos_28d"] == 0


def test_tiktok_page_limit_before_window_end_is_incomplete_not_a_number() -> None:
    pages = [page([video(1, 1)], more=True, cursor=10_000 - i) for i in range(5)]
    fetch, _ = tt_fetch(*pages)
    with pytest.raises(TiktokError) as err:
        video_stats(TT, TT_ENV, fetch, NOW_S)
    assert err.value.kind == "window_incomplete"


def test_tiktok_missing_engagement_fields_drop_only_that_metric() -> None:
    fetch, _ = tt_fetch(page([video(1, 5), video(2, 5, likes=...)]))
    out = video_stats(TT, TT_ENV, fetch, NOW_S)
    assert out["video_views_28d"] == 10
    assert "video_engagement_28d" not in out


def test_tiktok_missing_views_drop_views_but_keep_engagement() -> None:
    fetch, _ = tt_fetch(page([video(1, views=...)]))
    out = video_stats(TT, TT_ENV, fetch, NOW_S)
    assert "video_views_28d" not in out and out["video_engagement_28d"] == 15


def test_tiktok_missing_create_time_is_refused() -> None:
    fetch, _ = tt_fetch(page([video(1, create_time=...)]))
    with pytest.raises(TiktokError) as err:
        video_stats(TT, TT_ENV, fetch, NOW_S)
    assert err.value.kind == "missing_field"


@pytest.mark.parametrize("bad", ["100", 1.5, True, -1, [1], None, 10**13], ids=repr)
def test_tiktok_wrong_typed_view_count_is_never_summed(bad: Any) -> None:
    fetch, _ = tt_fetch(page([video(1, views=bad), video(2, 5)]))
    out = video_stats(TT, TT_ENV, fetch, NOW_S)
    assert "video_views_28d" not in out


@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"videos": "x", "has_more": False}},
        {"data": {"videos": [1, 2], "has_more": False}},
        {"data": {"videos": [video(1)] * 81, "has_more": False}},
        {"data": [], "error": {"code": "ok"}},
        {"data": {"videos": [], "has_more": True, "cursor": "x"}},
        {"data": {"videos": [], "has_more": True}},
        {"data": {"videos": [video(1)]}, "error": {"code": "internal_error"}},
    ],
)
def test_tiktok_hostile_shapes_fail_with_a_fixed_word(payload: dict) -> None:
    fetch, _ = tt_fetch(reply(payload))
    with pytest.raises(TiktokError) as err:
        video_stats(TT, TT_ENV, fetch, NOW_S)
    assert err.value.kind in ("missing_field", "api_error")


def test_tiktok_cursor_that_does_not_go_back_is_refused() -> None:
    fetch, _ = tt_fetch(
        page([video(1)], more=True, cursor=5), page([video(2)], more=True, cursor=5)
    )
    with pytest.raises(TiktokError) as err:
        video_stats(TT, TT_ENV, fetch, NOW_S)
    assert err.value.kind == "missing_field"


def test_tiktok_huge_body_redirect_and_401_use_fixed_words() -> None:
    for answer, word in (
        (reply(body=b"{" + b" " * 70_000 + b"}"), "too_large"),
        (
            reply({}, status=302, headers={"location": "https://evil.example/x"}),
            "redirect",
        ),
        (
            reply({"error": {"code": "access_token_invalid"}}, status=401),
            "token_expired",
        ),
        (
            reply({"error": {"code": "scope_not_authorized"}}, status=401),
            "scope_missing",
        ),
    ):
        fetch, _ = tt_fetch(answer)
        with pytest.raises((FetchError, TiktokError)) as err:
            video_stats(TT, TT_ENV, fetch, NOW_S)
        assert err.value.kind == word  # type: ignore[attr-defined]


def test_tiktok_never_refreshes_in_the_insights_path() -> None:
    src = TT.model_copy(update={"auto_refresh": True})
    env = {
        **TT_ENV,
        "TIKTOK_REFRESH_TOKEN": "SENTINEL-R",
        "TIKTOK_CLIENT_KEY": "k",
        "TIKTOK_CLIENT_SECRET": "s",
    }
    fetch, transport = tt_fetch(
        reply({"error": {"code": "access_token_invalid"}}, status=401)
    )
    with pytest.raises(TiktokError) as err:
        video_stats(src, env, fetch, NOW_S)
    assert err.value.kind == "token_expired"
    assert len(transport.calls) == 1


def test_tiktok_names_and_missing_token_checked_at_use() -> None:
    fetch, transport = tt_fetch(page([]))
    bad = TT.model_copy(update={"access_token_env": "OTHER_TOKEN"})
    with pytest.raises(TiktokError) as err:
        video_stats(bad, {"OTHER_TOKEN": "x"}, fetch, NOW_S)
    assert err.value.kind == "bad_credential_name"
    with pytest.raises(TiktokError) as err2:
        video_stats(TT, {}, fetch, NOW_S)
    assert err2.value.kind == "missing_credential"
    assert not transport.calls


def test_tiktok_unexpected_fetch_exception_becomes_a_fixed_word() -> None:
    def boom(url: str, **kw: Any) -> dict:
        raise RuntimeError("SENTINEL-TT-ACCESS leaked in text")

    with pytest.raises(TiktokError) as err:
        video_stats(TT, TT_ENV, boom, NOW_S)
    assert err.value.kind == "fetch_failed"
    assert "SENTINEL" not in repr(err.value) and err.value.__cause__ is None
    assert err.value.__context__ is None


# ---- Threads ----


def test_threads_window_formula_and_request() -> None:
    fetch, transport = th_fetch(th_ok())
    out = threads_insights(TH, TH_ENV, fetch, date(2026, 11, 9))
    assert out == {"views_7d": 60, "engagement_7d": 14}
    path = transport.calls[0].path
    assert "metric=views%2Clikes%2Creplies%2Creposts%2Cquotes" in path
    until = int(datetime(2026, 11, 9, tzinfo=UTC).timestamp())
    assert f"until={until}" in path and f"since={until - 7 * DAY}" in path
    assert "clicks" not in path


def test_threads_views_as_total_value_shape_is_accepted() -> None:
    fetch, _ = th_fetch(reply({"data": [total("views", 77)]}))
    assert threads_insights(TH, TH_ENV, fetch, date(2026, 11, 9)) == {
        "views_vendor_window": 77
    }


def test_threads_missing_one_engagement_part_drops_engagement() -> None:
    answer = reply(
        {"data": [series("views", [1]), total("likes", 1), total("replies", 1)]}
    )
    fetch, _ = th_fetch(answer)
    assert threads_insights(TH, TH_ENV, fetch, date(2026, 11, 9)) == {"views_7d": 1}


@pytest.mark.parametrize(
    "data",
    [
        [],
        "x",
        [total("views", "5")],
        [total("views", True)],
        [total("views", -1)],
        [{"name": "views", "values": []}],
        [{"name": "views", "values": [{"value": 1.5}]}],
        [{"name": "views", "values": [{"value": 1}] * 40}],
        [{"name": "views", "total_value": []}],
        [1, None, "views"],
    ],
    ids=repr,
)
def test_threads_hostile_shapes_store_nothing(data: Any) -> None:
    fetch, _ = th_fetch(reply({"data": data}))
    with pytest.raises(MetaError) as err:
        threads_insights(TH, TH_ENV, fetch, date(2026, 11, 9))
    assert err.value.kind in ("missing_field", "window_mismatch")


def test_threads_failures_are_fixed_words() -> None:
    for answer, word in (
        (
            reply({}, status=302, headers={"location": "https://evil.example/"}),
            "redirect",
        ),
        (reply(body=b"[" + b"1," * 40_000 + b"1]"), "too_large"),
        (reply({}, status=401), "token_rejected"),
        (reply({}, status=500), "server_error"),
    ):
        fetch, _ = th_fetch(answer)
        with pytest.raises((FetchError, MetaError)) as err:
            threads_insights(TH, TH_ENV, fetch, date(2026, 11, 9))
        assert err.value.kind == word  # type: ignore[attr-defined]


def test_threads_expired_token_day_makes_no_request() -> None:
    src = TH.model_copy(update={"token_expires": date(2026, 11, 1)})
    fetch, transport = th_fetch(th_ok())
    with pytest.raises(MetaError) as err:
        threads_insights(src, TH_ENV, fetch, date(2026, 11, 9))
    assert err.value.kind == "token_expired"
    assert not transport.calls


def test_threads_credential_name_and_setting_type_checked_at_use() -> None:
    fetch, transport = th_fetch(th_ok())
    forged = TH.model_copy(update={"user_id": "../x"})
    with pytest.raises(MetaError):
        threads_insights(forged, TH_ENV, fetch, date(2026, 11, 9))
    forged = TH.model_copy(update={"insights": "yes"})
    with pytest.raises(MetaError):
        threads_insights(forged, TH_ENV, fetch, date(2026, 11, 9))
    assert not transport.calls


def test_threads_unexpected_exception_has_no_url_in_it() -> None:
    def boom(url: str, **kw: Any) -> dict:
        raise RuntimeError(url)

    with pytest.raises(MetaError) as err:
        threads_insights(TH, TH_ENV, boom, date(2026, 11, 9))
    assert err.value.kind == "fetch_failed"
    assert err.value.__cause__ is None and err.value.__context__ is None


# ---- collect: opt-in, gating, no leak ----


def test_collect_is_off_by_default_and_needs_a_good_follower_read() -> None:
    plain = TiktokSource.model_validate({"platform": "tiktok", "kind": "tiktok"})
    assert plain.insights is False
    fetch, transport = tt_fetch(page([video(1)]))
    assert collect([plain], frozenset({"tiktok"}), TT_ENV, fetch, NOW) == ([], [])
    assert collect([TT], frozenset(), TT_ENV, fetch, NOW) == ([], [])
    assert not transport.calls


def test_collect_reports_a_failure_word_and_keeps_other_sources_going() -> None:
    fetch_t, _ = tt_fetch(
        reply({}, status=302, headers={"location": "https://x.example/"})
    )
    figures, failures = collect([TT], frozenset({"tiktok"}), TT_ENV, fetch_t, NOW)
    assert figures == [] and [(f.platform, f.word) for f in failures] == [
        ("tiktok", "redirect")
    ]


def test_collect_figures_carry_a_note_naming_window_and_formula() -> None:
    fetch, _ = th_fetch(th_ok())
    figures, failures = collect([TH], frozenset({"threads"}), TH_ENV, fetch, NOW)
    assert not failures
    assert {f.metric for f in figures} == {"views_7d", "engagement_7d"}
    assert all("UTC" in f.note or "vendor-defined" in f.note for f in figures)
    assert {f.metric for f in figures} <= OWNER_REPORT_ONLY


def test_owner_report_only_metrics_never_reach_the_build_or_kit() -> None:
    """Boundary check: public-side code cannot read the history store at all.

    The history file is the only place the figures live, so site, kit, editor and
    preview code must not import the history or insights modules (an import
    check, not a search for metric names, so aliases and prefixes cannot slip by).
    """
    import ast

    root = Path(__file__).resolve().parents[1] / "src" / "reach_around"
    banned = ("reach_around.counts.history", "reach_around.counts.insights")
    for sub in ("site", "kit", "editor", "preview.py", "config", "editing.py"):
        target = root / sub
        files = [target] if target.is_file() else sorted(target.rglob("*.py"))
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    names = [
                        node.module,
                        *(f"{node.module}.{a.name}" for a in node.names),
                    ]
                elif isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                for name in names:
                    assert not name.startswith(banned), (path.name, name)


# ---- review findings (Daybreak, PR 478) ----


@pytest.mark.parametrize("flag", ["true", 1, None, "false", 0, [True]], ids=repr)
def test_tiktok_has_more_must_be_a_real_boolean(flag: Any) -> None:
    payload = {
        "data": {"videos": [video(1)], "has_more": flag, "cursor": 5},
        "error": {"code": "ok"},
    }
    fetch, _ = tt_fetch(reply(payload))
    with pytest.raises(TiktokError) as err:
        video_stats(TT, TT_ENV, fetch, NOW_S)
    assert err.value.kind == "missing_field"


def test_tiktok_has_more_absent_is_refused() -> None:
    fetch, _ = tt_fetch(
        reply({"data": {"videos": [video(1)]}, "error": {"code": "ok"}})
    )
    with pytest.raises(TiktokError) as err:
        video_stats(TT, TT_ENV, fetch, NOW_S)
    assert err.value.kind == "missing_field"


def test_tiktok_page_over_the_documented_size_is_refused() -> None:
    fetch, _ = tt_fetch(page([video(1, 1)] * 21))
    with pytest.raises(TiktokError) as err:
        video_stats(TT, TT_ENV, fetch, NOW_S)
    assert err.value.kind == "missing_field"
    fetch, _ = tt_fetch(page([video(1, 1)] * 20))
    assert video_stats(TT, TT_ENV, fetch, NOW_S)["videos_28d"] == 20


def test_tiktok_final_aggregate_over_the_history_maximum_is_refused() -> None:
    big = 10**12  # each value is individually allowed
    fetch, _ = tt_fetch(page([video(1, big), video(2, big)]))
    with pytest.raises(TiktokError) as err:
        video_stats(TT, TT_ENV, fetch, NOW_S)
    assert err.value.kind == "bad_aggregate"


def test_tiktok_engagement_aggregate_over_the_maximum_is_refused() -> None:
    big = 10**12
    fetch, _ = tt_fetch(page([video(1, 1, likes=big), video(2, 1, likes=big)]))
    with pytest.raises(TiktokError) as err:
        video_stats(TT, TT_ENV, fetch, NOW_S)
    assert err.value.kind == "bad_aggregate"


def test_threads_views_sum_over_the_maximum_is_refused() -> None:
    values = [10**12, 10**12]
    fetch, _ = th_fetch(reply({"data": [series("views", values)]}))
    with pytest.raises(MetaError) as err:
        threads_insights(TH, TH_ENV, fetch, date(2026, 11, 9))
    assert err.value.kind == "bad_aggregate"


def test_threads_engagement_sum_over_the_maximum_is_refused() -> None:
    data = [total(n, 10**12) for n in ("likes", "replies", "reposts", "quotes")]
    fetch, _ = th_fetch(reply({"data": data}))
    with pytest.raises(MetaError) as err:
        threads_insights(TH, TH_ENV, fetch, date(2026, 11, 9))
    assert err.value.kind == "bad_aggregate"


def _views(period: str, points: list[tuple[int, str]]) -> Any:
    return reply(
        {
            "data": [
                {
                    "name": "views",
                    "period": period,
                    "values": [{"value": v, "end_time": t} for v, t in points],
                }
            ]
        }
    )


@pytest.mark.parametrize(
    "answer",
    [
        _views("lifetime", [(100, "2026-11-03T08:00:00+0000")]),
        _views(
            "day",
            [(100, "2020-01-01T00:00:00+0000"), (200, "2035-01-01T00:00:00+0000")],
        ),
        _views("day", [(1, "2026-11-03T08:00:00+0000")] * 2),
        _views("day", [(1, "not a time")]),
        _views("day", [(1, "2026-11-01T08:00:00+0000")]),
        _views("day", [(1, "2026-11-12T08:00:00+0000")]),
        _views("day", [(1, f"2026-11-{d:02d}T08:00:00+0000") for d in range(2, 12)]),
        _views("day", [(1, 5)]),
    ],
    ids=range(8),
)
def test_threads_views_series_must_match_the_requested_window(answer: Any) -> None:
    fetch, _ = th_fetch(answer)
    with pytest.raises(MetaError) as err:
        threads_insights(TH, TH_ENV, fetch, date(2026, 11, 9))
    assert err.value.kind in ("missing_field", "window_mismatch")
    assert err.value.kind == "window_mismatch"


def test_threads_views_series_inside_the_window_is_accepted() -> None:
    points = [(1, f"2026-11-{d:02d}T08:00:00+0000") for d in range(3, 10)]
    fetch, _ = th_fetch(_views("day", points))
    assert threads_insights(TH, TH_ENV, fetch, date(2026, 11, 9)) == {"views_7d": 7}


def test_threads_total_value_views_is_stored_as_vendor_defined_window() -> None:
    fetch, _ = th_fetch(reply({"data": [total("views", 77)]}))
    out = threads_insights(TH, TH_ENV, fetch, date(2026, 11, 9))
    assert out == {"views_vendor_window": 77}
    figures, _ = collect(
        [TH],
        frozenset({"threads"}),
        TH_ENV,
        lambda u, **k: {"data": [total("views", 77)]},
        NOW,
    )
    note = figures[0].note
    assert "vendor-defined" in note and figures[0].metric in OWNER_REPORT_ONLY


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
          - {platform: tiktok, handle: "@example", url: "https://example.com/t"}
numbers:
  history_file: history/social.jsonl
  sources:
    - {platform: tiktok, kind: tiktok, insights: true}
"""


def run(root: Path, monkeypatch: pytest.MonkeyPatch, routes: dict) -> int:
    (root / "history").mkdir(exist_ok=True)
    (root / "site.yaml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.setattr(counts_cmd, "default_sleep", lambda s: None)
    transport = FakeTransport(routes)
    monkeypatch.setattr(counts_cmd, "default_transport", lambda: transport)
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", TT_ENV["TIKTOK_ACCESS_TOKEN"])
    return main(["counts", "refresh", "--config", str(root / "site.yaml")])


INFO = "https://open.tiktokapis.com/v2/user/info/"


def info_ok() -> Any:
    return reply({"data": {"user": {"follower_count": 9}}, "error": {"code": "ok"}})


def test_refresh_stores_insight_numbers_with_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    now = int(time.time())
    vids = page([video(1, 10), video(3, 20)])
    vids2 = reply(
        {
            "data": {
                "videos": [
                    {**v, "create_time": now - DAY * (i + 1)}
                    for i, v in enumerate(json.loads(vids.body)["data"]["videos"])
                ],
                "has_more": False,
                "cursor": 1,
            },
            "error": {"code": "ok"},
        }
    )
    assert run(tmp_path, monkeypatch, {INFO: info_ok(), VIDEO: vids2}) == 0
    records = read_history(tmp_path / "history" / "social.jsonl").records
    got = {r.metric: (r.value, r.source, r.note) for r in records}
    assert got["followers"][0] == 9
    assert got["video_views_28d"][0] == 30
    assert got["video_views_28d"][2] and "28 UTC days" in got["video_views_28d"][2]
    out = capsys.readouterr()
    assert not any(s in out.out + out.err for s in SECRETS)


def test_insight_failure_keeps_followers_and_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    routes = {
        INFO: info_ok(),
        VIDEO: reply({}, status=302, headers={"location": "https://x.example/"}),
    }
    assert run(tmp_path, monkeypatch, routes) == counts_cmd.EXIT_SOURCE_FAILED
    records = read_history(tmp_path / "history" / "social.jsonl").records
    assert [r.metric for r in records] == ["followers"]
    err = capsys.readouterr().err
    assert "tiktok: insights error (redirect)" in err
    assert "SENTINEL" not in err and "x.example" not in err
