# SPDX-License-Identifier: Apache-2.0
"""``reach-around report``: synthetic exports only, hostile ones included."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from reach_around.cli import main
from reach_around.funnel.export import ExportError, load_exports
from reach_around.funnel.report import floor_cells

DAY1 = "2026-11-01"
DAY2 = "2026-11-02"
TS = 1_793_491_200_000  # an hour boundary, 2026-11-01 00:00 UTC


def vd(
    page: Any = "home",
    src_kind: str = "d",
    src: str = "",
    country: str = "US",
    bot_class: str = "h",
    views: Any = 10,
    day: str = DAY1,
    site: str = "links",
    day_tz: str = "UTC",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "site": site,
        "day": day,
        "day_tz": day_tz,
        "page": page,
        "src_kind": src_kind,
        "src": src,
        "country": country,
        "bot_class": bot_class,
        "views": views,
        "uniques": 1,
        **extra,
    }


def geo(
    country: str = "US",
    region: str = "",
    city: str = "",
    bot_class: str = "h",
    views: int = 10,
    day: str = DAY1,
) -> dict[str, Any]:
    return {
        "site": "links",
        "day": day,
        "day_tz": "UTC",
        "country": country,
        "region": region,
        "city": city,
        "bot_class": bot_class,
        "views": views,
        "uniques": 1,
    }


def dim(value: str, views: int, bot_class: str = "a") -> dict[str, Any]:
    return {
        "site": "links",
        "day": DAY1,
        "day_tz": "UTC",
        "dim": "reason",
        "value": value,
        "bot_class": bot_class,
        "views": views,
        "uniques": 0,
    }


def sample() -> dict[str, Any]:
    return {
        "version": 1,
        "tables": {
            "view_daily": [
                vd(page="home", views=120, src_kind="r", src="news.example"),
                vd(page="home", views=40, src_kind="u", src="newsletter", day=DAY2),
                vd(page="kit", views=60, country="DE", day=DAY2),
                vd(page="home", views=3, country="FR"),
                vd(page="home", views=30, bot_class="a"),
                vd(page="home", views=8, bot_class="s"),
            ],
            "view_geo_daily": [
                geo("US", "NY", "Springfield", views=40),
                geo("US", "NY", "", views=30),
                geo("US", "", "", views=60),
                geo("DE", "BE", "Berlin", views=60),
                geo("FR", "", "", views=3),
            ],
            "view_dim_daily": [dim("ua-script", 25), dim("net-dc", 6, "s")],
            "click_events": [
                {"ts": TS, "link_id": "shop", "country": "US"} for _ in range(50)
            ]
            + [{"ts": TS, "link_id": "rare"} for _ in range(2)],
            "kit_events": [{"ts": TS, "kind": "view"} for _ in range(12)]
            + [{"ts": TS, "kind": "unlock"} for _ in range(9)],
        },
    }


def write(tmp_path: Path, data: Any, name: str = "export.json") -> Path:
    path = tmp_path / name
    path.write_text(
        data if isinstance(data, str) else json.dumps(data), encoding="utf-8"
    )
    return path


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(["report", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


# ---- happy path -----------------------------------------------------------


def test_text_report_shows_funnel_geo_and_bots(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, sample())
    code, out, err = run(capsys, "--input", str(path))
    assert code == 0, err
    assert "Funnel" in out
    assert "Page views (likely human)" in out
    assert "Link clicks" in out
    assert "Kit views" in out
    assert "Geography" in out
    assert "US" in out and "DE" in out
    assert "Bots" in out
    assert "ua-script" in out
    assert "shop" in out


def test_json_report_matches_the_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, sample())
    code, out, _ = run(capsys, "--input", str(path), "--json")
    assert code == 0
    report = json.loads(out)
    assert report["floor"] == 5
    funnel = report["funnel"]
    assert funnel["page_views"] == 223  # 120+40+60+3, class h only
    assert (
        funnel["clicks"] == 52
    )  # the total counts every click; the link table hides "rare"
    assert funnel["kit_views"] == 12
    assert funnel["clicks_per_100_views"] == pytest.approx(23.3, abs=0.05)
    shares = {row["class"]: row["views"] for row in report["bots"]["classes"]}
    assert shares["h"] == 223 and shares["a"] == 30 and shares["s"] == 8


def test_window_filters_by_day(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, sample())
    _, out, _ = run(capsys, "--input", str(path), "--json", "--since", DAY2)
    assert json.loads(out)["funnel"]["page_views"] == 100
    _, out, _ = run(capsys, "--input", str(path), "--json", "--days", "1")
    assert json.loads(out)["funnel"]["page_views"] == 100


def test_geo_levels(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write(tmp_path, sample())
    _, out, _ = run(capsys, "--input", str(path), "--json", "--geo", "city")
    labels = {row["label"] for row in json.loads(out)["geography"]["rows"]}
    assert "US / NY / Springfield" in labels
    assert "DE / BE / Berlin" in labels
    _, out, _ = run(capsys, "--input", str(path), "--json", "--geo", "country")
    rows = {r["label"]: r["views"] for r in json.loads(out)["geography"]["rows"]}
    assert rows == {"US": 130, "DE": 60}


def test_csv_input_and_two_inputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = sample()
    views = write(tmp_path, {"tables": {"view_daily": data["tables"]["view_daily"]}})
    csv_path = tmp_path / "clicks.csv"
    csv_path.write_text(
        "ts,link_id\n" + "".join(f"{TS},shop\n" for _ in range(7)),
        encoding="utf-8",
    )
    code, out, err = run(
        capsys, "--input", str(views), "--input", str(csv_path), "--json"
    )
    assert code == 0, err
    assert json.loads(out)["funnel"]["clicks"] == 7


def test_wrangler_style_list_infers_the_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = sample()["tables"]["view_daily"]
    path = write(tmp_path, [{"results": rows, "success": True}])
    code, out, err = run(capsys, "--input", str(path), "--json")
    assert code == 0, err
    assert json.loads(out)["funnel"]["page_views"] == 223


# ---- the privacy floor ----------------------------------------------------


def test_floor_cells_hides_small_and_merges() -> None:
    result = floor_cells({"a": 100, "b": 3, "c": 2}, 5)
    assert [label for label, _ in result.shown] == ["a"]
    assert result.other == 5  # b and c merged, never shown apart
    result = floor_cells({"a": 100, "b": 6, "c": 2}, 5)
    assert [label for label, _ in result.shown] == ["a", "b"]
    assert result.other == 0  # a merged cell under the floor is dropped, not shown
    assert floor_cells({"a": 2, "b": 1}, 5).withheld


def test_floor_cells_boundary() -> None:
    assert floor_cells({"a": 5}, 5).shown == [("a", 5)]
    assert floor_cells({"a": 4}, 5).withheld


def test_nothing_below_the_floor_is_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, sample())
    for flags in ([], ["--json"], ["--json", "--geo", "city"], ["--include-bots"]):
        _, out, _ = run(capsys, "--input", str(path), *flags)
        assert "rare" not in out
        assert "FR" not in out  # 3 views


def test_everything_below_floor_is_withheld(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tiny = {
        "tables": {
            "view_daily": [vd(views=2), vd(page="kit", views=2, country="DE")],
            "click_events": [{"ts": TS, "link_id": "xyzzy"}] * 4,
        }
    }
    path = write(tmp_path, tiny)
    code, out, _ = run(capsys, "--input", str(path), "--json")
    assert code == 0
    report = json.loads(out)
    assert report["funnel"]["page_views"] is None
    assert report["funnel"]["clicks"] is None
    assert report["funnel"]["clicks_per_100_views"] is None
    assert report["funnel"]["withheld"]
    text = run(capsys, "--input", str(path))[1]
    assert "below the privacy floor" in text
    assert "xyzzy" not in text


def test_floor_cannot_be_lowered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, sample())
    code, _, err = run(capsys, "--input", str(path), "--floor", "1")
    assert code == 2
    assert "floor" in err


def test_higher_floor_hides_more(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, sample())
    _, out, _ = run(capsys, "--input", str(path), "--json", "--floor", "45")
    assert json.loads(out)["funnel"]["kit_views"] is None  # 12 < 45


def test_clicks_over_100_per_100_views_carry_a_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = {
        "tables": {
            "view_daily": [vd(views=10)],
            "click_events": [{"ts": TS, "link_id": "a"}] * 30,
        }
    }
    path = write(tmp_path, data)
    _, out, _ = run(capsys, "--input", str(path))
    assert "not a conversion rate" in out
    assert "more than 100" in out


# ---- hostile exports ------------------------------------------------------


def hostile_cases() -> list[tuple[str, Any]]:
    base = sample()["tables"]
    esc = chr(27)
    return [
        ("ip column", {"tables": {"view_daily": [vd(ip="203.0.113.7")]}}),
        ("addr column", {"tables": {"view_daily": [vd(client_addr="x")]}}),
        ("ua column", {"tables": {"view_daily": [vd(user_agent="curl/8")]}}),
        (
            "ip in value",
            {"tables": {"view_daily": [vd(src_kind="r", src="203.0.113.7")]}},
        ),
        (
            "ipv6 in value",
            {"tables": {"view_daily": [vd(src_kind="r", src="2001:db8::1")]}},
        ),
        (
            "ua in value",
            {"tables": {"view_daily": [vd(src_kind="r", src="Mozilla/5.0 (X11)")]}},
        ),
        ("control chars", {"tables": {"view_daily": [vd(page=f"home{esc}[31m")]}}),
        ("bidi override", {"tables": {"view_daily": [vd(page="a‮b")]}}),
        (
            "lead_id in kit",
            {
                "tables": {
                    "kit_events": [{"ts": 3600000, "kind": "view", "lead_id": "a" * 32}]
                }
            },
        ),
        (
            "email in clicks",
            {
                "tables": {
                    "click_events": [
                        {"ts": 3600000, "link_id": "a", "email": "x@example.com"}
                    ]
                }
            },
        ),
        ("unknown table", {"tables": {"leads": [{"email": "x@example.com"}]}}),
        ("bad version", {"version": 2, "tables": base}),
        ("bad class", {"tables": {"view_daily": [vd(bot_class="x")]}}),
        ("bad src_kind", {"tables": {"view_daily": [vd(src_kind="z")]}}),
        ("float views", {"tables": {"view_daily": [vd(views=1.5)]}}),
        ("bool views", {"tables": {"view_daily": [vd(views=True)]}}),
        ("string views", {"tables": {"view_daily": [vd(views="10")]}}),
        ("zero views", {"tables": {"view_daily": [vd(views=0)]}}),
        ("negative views", {"tables": {"view_daily": [vd(views=-5)]}}),
        ("huge views", {"tables": {"view_daily": [vd(views=10**15)]}}),
        ("bad day", {"tables": {"view_daily": [vd(day="2026-13-45")]}}),
        ("non-UTC day", {"tables": {"view_daily": [vd(day_tz="America/New_York")]}}),
        ("bad site", {"tables": {"view_daily": [vd(site="Bad Site")]}}),
        ("bad country", {"tables": {"view_daily": [vd(country="usa")]}}),
        ("long label", {"tables": {"view_daily": [vd(page="p" * 500)]}}),
        ("null label", {"tables": {"view_daily": [vd(page=None)]}}),
        ("missing field", {"tables": {"view_daily": [{"day": DAY1}]}}),
        ("row not object", {"tables": {"view_daily": [[1, 2]]}}),
        ("rows not list", {"tables": {"view_daily": {"a": 1}}}),
        ("duplicate key row", {"tables": {"view_daily": [vd(), vd()]}}),
        ("bad kit kind", {"tables": {"kit_events": [{"ts": 3600000, "kind": "x"}]}}),
        ("top level scalar", 5),
        ("empty object", {}),
    ]


@pytest.mark.parametrize(
    ("name", "data"), hostile_cases(), ids=[c[0] for c in hostile_cases()]
)
def test_hostile_exports_fail_closed(
    name: str,
    data: Any,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = write(tmp_path, data)
    code, out, err = run(capsys, "--input", str(path))
    assert code == 2, (name, out)
    assert out == ""
    assert err.startswith("error:")
    for secret in ("203.0.113.7", "2001:db8", "Mozilla", "x@example.com", "curl/8"):
        assert secret not in err  # the message never echoes the offending value


def test_duplicate_json_keys_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = '{"tables": {"view_daily": []}, "tables": {"view_daily": []}}'
    code, _, err = run(capsys, "--input", str(write(tmp_path, text)))
    assert code == 2 and "duplicate" in err
    row = json.dumps(vd())[:-1] + ', "views": 99}'
    text = '{"tables": {"view_daily": [' + row + "]}}"
    code, _, err = run(capsys, "--input", str(write(tmp_path, text)))
    assert code == 2 and "duplicate" in err


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_numbers_refused(
    token: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    row = json.dumps(vd()).replace('"views": 10', f'"views": {token}')
    path = write(tmp_path, '{"tables": {"view_daily": [' + row + "]}}")
    assert run(capsys, "--input", str(path))[0] == 2


def test_invalid_json_and_utf8(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(capsys, "--input", str(write(tmp_path, "{not json")))[0] == 2
    bad = tmp_path / "bad.json"
    bad.write_bytes(b'{"tables": {}}\xff\xfe')
    assert run(capsys, "--input", str(bad))[0] == 2


def test_deeply_nested_json_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, "[" * 200_000 + "]" * 200_000)
    code, _, err = run(capsys, "--input", str(path))
    assert code == 2 and err.startswith("error:")


def test_huge_file_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import reach_around.funnel.export as export

    monkeypatch.setattr(export, "MAX_EXPORT_BYTES", 1000)
    path = write(tmp_path, sample())
    code, _, err = run(capsys, "--input", str(path))
    assert code == 2 and "too large" in err


def test_too_many_rows_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import reach_around.funnel.export as export

    monkeypatch.setattr(export, "MAX_ROWS", 3)
    rows = [{"ts": 3600000, "link_id": "a"}] * 4
    path = write(tmp_path, {"tables": {"click_events": rows}})
    code, _, err = run(capsys, "--input", str(path))
    assert code == 2 and "rows" in err


def test_symlink_and_fifo_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    real = write(tmp_path, sample())
    link = tmp_path / "link.json"
    link.symlink_to(real)
    assert run(capsys, "--input", str(link))[0] == 2
    fifo = tmp_path / "fifo.json"
    os.mkfifo(fifo)
    assert run(capsys, "--input", str(fifo))[0] == 2
    assert run(capsys, "--input", str(tmp_path / "missing.json"))[0] == 2
    assert run(capsys, "--input", str(tmp_path))[0] == 2


def test_csv_hostile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cases = {
        "ip header": "ts,link_id,ip\n1,a,203.0.113.7\n",
        "duplicate header": "ts,link_id,link_id\n1,a,b\n",
        "ragged": "ts,link_id\n1,a,extra\n",
        "short": "ts,link_id\n1\n",
        "no marker": "ts,foo\n1,2\n",
        "empty": "",
        "bad int": "ts,link_id\nabc,a\n",
        "huge int": "ts,link_id\n" + "9" * 40 + ",a\n",
        "ip value": "ts,link_id\n3600000,203.0.113.7\n",
        "nul byte": "ts,link_id\n3600000,a" + chr(0) + "b\n",
    }
    for name, text in cases.items():
        path = tmp_path / "x.csv"
        path.write_bytes(text.encode())
        code, out, err = run(capsys, "--input", str(path))
        assert code == 2, name
        assert out == "" and "203.0.113.7" not in err, name


def test_same_table_in_two_inputs_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    one = write(tmp_path, sample(), "a.json")
    two = write(tmp_path, sample(), "b.json")
    code, _, err = run(capsys, "--input", str(one), "--input", str(two))
    assert code == 2 and "twice" in err


def test_hostile_labels_never_break_the_terminal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = {"tables": {"view_daily": [vd(page="<script>alert(1)</script>", views=50)]}}
    path = write(tmp_path, data)
    code, out, _ = run(capsys, "--input", str(path))
    assert code == 0
    assert all(ch.isprintable() or ch == "\n" for ch in out)


def test_load_exports_needs_an_input(tmp_path: Path) -> None:
    path = write(tmp_path, sample())
    assert load_exports([path]).view_daily
    with pytest.raises(ExportError):
        load_exports([])


def test_other_site_rows_are_not_mixed_in(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = {
        "tables": {
            "view_daily": [vd(views=50), vd(views=70, site="blog")],
        }
    }
    path = write(tmp_path, data)
    _, out, _ = run(capsys, "--input", str(path), "--json")
    assert json.loads(out)["funnel"]["page_views"] == 50
    _, out, _ = run(capsys, "--input", str(path), "--json", "--site", "blog")
    assert json.loads(out)["funnel"]["page_views"] == 70


def test_usage_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write(tmp_path, sample())
    for argv in (
        ["--input", str(path), "--since", "yesterday"],
        ["--input", str(path), "--until", "2026-02-30"],
        ["--input", str(path), "--since", DAY2, "--until", DAY1],
        ["--input", str(path), "--days", "0"],
        ["--input", str(path), "--days", "1", "--since", DAY1],
        ["--input", str(path), "--top", "0"],
        ["--input", str(path), "--site", "Bad Site"],
    ):
        code, out, err = run(capsys, *argv)
        assert code == 2 and out == "" and err.startswith("error:"), argv


def test_help_mentions_privacy_and_input(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        main(["report", "--help"])
    text = capsys.readouterr().out
    assert "--input" in text and "floor" in text and "--json" in text


def test_docs_example_is_synthetic_and_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = (Path(__file__).resolve().parent.parent / "docs" / "report.md").read_text(
        encoding="utf-8"
    )
    match = re.search(r"```json\n(.*?)```", doc, re.DOTALL)
    assert match is not None
    path = write(tmp_path, match.group(1))
    code, out, err = run(capsys, "--input", str(path))
    assert code == 0, err
    assert "Funnel" in out


def test_address_and_agent_columns_get_the_forbidden_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for column in ("ip", "client_ip", "user_agent", "ua", "lead_id", "email"):
        row = {"ts": 3600000, "link_id": "a", column: "x"}
        path = write(tmp_path, {"tables": {"click_events": [row]}})
        code, _, err = run(capsys, "--input", str(path))
        assert code == 2 and "forbidden column" in err, column


# ---- review round: kit scope, window, zero ---------------------------------


def kit_rows_at(ts: int, view: int, unlock: int, **extra: Any) -> list[dict[str, Any]]:
    return [{"ts": ts, "kind": "view", **extra} for _ in range(view)] + [
        {"ts": ts, "kind": "unlock", **extra} for _ in range(unlock)
    ]


def test_unscoped_kit_is_not_reported_for_another_site(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = {
        "tables": {
            "view_daily": [vd(views=50), vd(views=70, site="blog")],
            "kit_events": kit_rows_at(TS, 12, 9),
        }
    }
    path = write(tmp_path, data)
    _, out, _ = run(capsys, "--input", str(path), "--json", "--site", "blog")
    funnel = json.loads(out)["funnel"]
    assert funnel["page_views"] == 70
    assert funnel["kit_views"] is None and funnel["kit_unlocks"] is None
    assert funnel["not_scoped"] == ["kit_views", "kit_unlocks"]
    assert "kit_views" not in funnel["withheld"]
    _, text, _ = run(capsys, "--input", str(path), "--site", "blog")
    assert "cannot be scoped by site" in text
    assert "below the privacy floor" not in text
    _, out, _ = run(capsys, "--input", str(path), "--json")  # default site links
    assert json.loads(out)["funnel"]["kit_views"] == 12


def test_kit_site_column_scopes_the_kit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = kit_rows_at(TS, 12, 0, site="links") + kit_rows_at(TS, 7, 0, site="blog")
    path = write(tmp_path, {"tables": {"kit_events": rows}})
    for site, want in (("links", 12), ("blog", 7)):
        _, out, _ = run(capsys, "--input", str(path), "--json", "--site", site)
        funnel = json.loads(out)["funnel"]
        assert funnel["kit_views"] == want
        assert funnel["not_scoped"] == []


def test_kit_bad_site_value_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [{"ts": TS, "kind": "view", "site": "Bad Site"}]
    path = write(tmp_path, {"tables": {"kit_events": rows}})
    assert run(capsys, "--input", str(path))[0] == 2


def test_days_window_uses_kit_days(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    day_ms = 86_400_000
    # kit-only export: --days 1 keeps only the newest day
    rows = kit_rows_at(TS, 6, 0) + kit_rows_at(TS + day_ms, 8, 0)
    path = write(tmp_path, {"tables": {"kit_events": rows}})
    _, out, _ = run(capsys, "--input", str(path), "--json", "--days", "1")
    report = json.loads(out)
    assert report["funnel"]["kit_views"] == 8
    assert report["period"]["since"] == DAY2 == report["period"]["until"]
    # a newer kit day moves the window end past the latest view day
    data = {
        "tables": {
            "view_daily": [vd(views=50, day=DAY1)],
            "kit_events": kit_rows_at(TS + day_ms, 9, 0),
        }
    }
    path = write(tmp_path, data, "mixed.json")
    _, out, _ = run(capsys, "--input", str(path), "--json", "--days", "1")
    funnel = json.loads(out)["funnel"]
    assert funnel["kit_views"] == 9
    assert funnel["page_views"] == 0  # DAY1 views are outside the window


def test_zero_is_printed_and_a_suppressed_count_is_withheld(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    only_bots = {"tables": {"view_daily": [vd(views=40, bot_class="a")]}}
    path = write(tmp_path, only_bots)
    _, out, _ = run(capsys, "--input", str(path), "--json")
    funnel = json.loads(out)["funnel"]
    assert funnel["page_views"] == 0
    assert "page_views" not in funnel["withheld"]
    _, text, _ = run(capsys, "--input", str(path))
    assert "Page views (likely human)" in text
    line = next(x for x in text.splitlines() if "Page views" in x)
    assert line.split()[-1] == "0"
    assert "below the privacy floor" not in text
    four = write(tmp_path, {"tables": {"view_daily": [vd(views=4)]}}, "four.json")
    _, out, _ = run(capsys, "--input", str(four), "--json")
    funnel = json.loads(out)["funnel"]
    assert funnel["page_views"] is None and "page_views" in funnel["withheld"]
    five = write(tmp_path, {"tables": {"view_daily": [vd(views=5)]}}, "five.json")
    _, out, _ = run(capsys, "--input", str(five), "--json")
    assert json.loads(out)["funnel"]["page_views"] == 5
