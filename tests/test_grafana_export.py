# SPDX-License-Identifier: Apache-2.0
"""``reach-around grafana-export``: floored aggregates only, deterministic."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from reach_around.cli import main
from reach_around.funnel import grafana
from reach_around.funnel.export import Export

DAY1 = "2026-11-01"
DAY2 = "2026-11-02"
TS = 1_793_491_200_000  # 2026-11-01 00:00 UTC
EXPECTED_TABLES = {
    "grafana_annotations",
    "grafana_meta",
    "grafana_views_daily",
    "grafana_pages_daily",
    "grafana_sources_daily",
    "grafana_countries_daily",
    "grafana_clicks_daily",
    "grafana_gaps_daily",
    "grafana_clicks_monthly",
    "grafana_sources_monthly",
    "grafana_countries_monthly",
}


def vd(
    page: str = "home",
    src_kind: str = "d",
    src: str = "",
    country: str = "US",
    bot_class: str = "h",
    views: int = 10,
    day: str = DAY1,
    site: str = "links",
) -> dict[str, Any]:
    return {
        "site": site,
        "day": day,
        "day_tz": "UTC",
        "page": page,
        "src_kind": src_kind,
        "src": src,
        "country": country,
        "bot_class": bot_class,
        "views": views,
        "uniques": 1,
    }


def sample() -> dict[str, Any]:
    return {
        "version": 1,
        "tables": {
            "view_daily": [
                vd(page="home", views=120, src_kind="r", src="news.example"),
                vd(page="home", views=40, src_kind="u", src="newsletter", day=DAY2),
                vd(page="kit", views=60, country="DE", day=DAY2),
                vd(page="rare", views=3, country="FR"),
                vd(page="home", views=30, bot_class="a"),
                vd(page="home", views=8, bot_class="s"),
                vd(page="blog", views=99, site="blog"),
            ],
            "view_geo_daily": [
                {
                    "site": "links",
                    "day": DAY1,
                    "day_tz": "UTC",
                    "country": "US",
                    "region": "NY",
                    "city": "Springfield",
                    "bot_class": "h",
                    "views": 40,
                    "uniques": 1,
                }
            ],
            "view_dim_daily": [
                {
                    "site": "links",
                    "day": DAY1,
                    "day_tz": "UTC",
                    "dim": "browser",
                    "value": "firefox",
                    "bot_class": "h",
                    "views": 50,
                    "uniques": 1,
                }
            ],
            "click_events": [
                {"ts": TS, "link_id": "shop", "country": "US"} for _ in range(50)
            ]
            + [{"ts": TS, "link_id": "rare"} for _ in range(2)],
            "kit_events": [{"ts": TS, "kind": "view"} for _ in range(12)],
        },
    }


def write(tmp_path: Path, data: Any, name: str = "export.json") -> Path:
    path = tmp_path / name
    path.write_text(
        data if isinstance(data, str) else json.dumps(data), encoding="utf-8"
    )
    return path


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(["grafana-export", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def rows(db: Path, table: str) -> list[tuple[Any, ...]]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()  # noqa: S608 - fixed test names
    finally:
        con.close()


def tables(db: Path) -> set[str]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
    finally:
        con.close()


def export_ok(tmp_path: Path, data: Any = None) -> Path:
    src = write(tmp_path, sample() if data is None else data)
    out = tmp_path / "grafana.sqlite3"
    from reach_around.funnel.export import load_exports

    grafana.write_store(out, grafana.build_subset(load_exports([src])))
    return out


# ---- the documented subset -------------------------------------------------


def test_only_the_documented_tables_exist(tmp_path: Path) -> None:
    db = export_ok(tmp_path)
    assert tables(db) == EXPECTED_TABLES


def test_documented_columns(tmp_path: Path) -> None:
    db = export_ok(tmp_path)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        cols = {
            t: [r[1] for r in con.execute(f"PRAGMA table_info({t})")]  # noqa: S608 - fixed names
            for t in EXPECTED_TABLES
        }
    finally:
        con.close()
    assert cols["grafana_views_daily"] == ["day", "bot_class", "views"]
    assert cols["grafana_pages_daily"] == ["day", "page", "views"]
    assert cols["grafana_sources_daily"] == ["day", "source", "views"]
    assert cols["grafana_countries_daily"] == ["day", "country", "views"]
    assert cols["grafana_clicks_daily"] == ["day", "link_id", "clicks"]
    assert cols["grafana_gaps_daily"] == ["day", "status", "reason"]
    assert cols["grafana_annotations"] == ["day", "text"]
    assert cols["grafana_meta"] == ["key", "value"]


def test_values_follow_the_report_rules(tmp_path: Path) -> None:
    db = export_ok(tmp_path)
    # pages: human only, other site excluded, small cell merged with the
    # smallest shown row so the remainder is safe
    assert rows(db, "grafana_pages_daily") == [
        (DAY1, "(below the floor)", 123),
        (DAY2, "home", 40),
        (DAY2, "kit", 60),
    ]
    assert rows(db, "grafana_sources_daily") == [
        (DAY1, "(below the floor)", 123),
        (DAY2, "(direct)", 60),
        (DAY2, "utm:newsletter", 40),
    ]
    assert rows(db, "grafana_countries_daily") == [
        (DAY1, "(below the floor)", 123),
        (DAY2, "DE", 60),
        (DAY2, "US", 40),
    ]
    assert rows(db, "grafana_views_daily") == [
        (DAY1, "a", 30),
        (DAY1, "h", 123),
        (DAY1, "s", 8),
        (DAY2, "h", 100),
    ]
    assert rows(db, "grafana_clicks_daily") == [(DAY1, "(below the floor)", 52)]


def test_nothing_under_the_floor_is_written_alone(tmp_path: Path) -> None:
    db = export_ok(tmp_path)
    for table, col in (
        ("grafana_views_daily", "views"),
        ("grafana_pages_daily", "views"),
        ("grafana_sources_daily", "views"),
        ("grafana_countries_daily", "views"),
        ("grafana_clicks_daily", "clicks"),
    ):
        for row in rows(db, table):
            assert row[-1] >= 5, (table, row)
        assert col


def test_no_raw_or_per_visitor_data_in_the_store(tmp_path: Path) -> None:
    db = export_ok(tmp_path)
    blob = "\n".join(str(r) for t in sorted(EXPECTED_TABLES) for r in rows(db, t))
    # no geography finer than country, no device facts, no kit, no uniques
    for word in ("Springfield", "NY", "firefox", "kit_events", "unique"):
        assert word not in blob
    assert not re.search(r"\d+\.\d+\.\d+\.\d+", blob)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        sql = " ".join(r[0] or "" for r in con.execute("SELECT sql FROM sqlite_master"))
    finally:
        con.close()
    for word in ("uniques", "city", "region", "ip", "agent", "email", "lead"):
        assert word not in sql.lower().replace("grafana", "")


def test_meta_records_site_floor_and_version_only(tmp_path: Path) -> None:
    db = export_ok(tmp_path)
    assert rows(db, "grafana_meta") == [
        ("floor", "5"),
        ("schema_version", "4"),
        ("site", "links"),
    ]


def test_cutover_marks_spanning_window_for_grafana(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = write(tmp_path, sample())
    out = tmp_path / "g.sqlite3"
    code, _, err = run(
        capsys,
        "--input",
        str(src),
        "--output",
        str(out),
        "--cutover",
        DAY2,
    )
    assert code == 0, err
    assert rows(out, "grafana_meta") == [
        ("cutover_utc", DAY2),
        ("floor", "5"),
        ("schema_version", "4"),
        ("site", "links"),
        ("window_era", "spans"),
    ]
    assert rows(out, "grafana_annotations") == [(DAY2, "Reach Around cutover")]


def test_higher_floor_hides_more(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = write(tmp_path, sample())
    out = tmp_path / "g.sqlite3"
    code, _, err = run(
        capsys, "--input", str(src), "--output", str(out), "--floor", "50"
    )
    assert code == 0, err
    for table in EXPECTED_TABLES - {"grafana_meta", "grafana_annotations"}:
        for row in rows(out, table):
            assert row[-1] >= 50, (table, row)


def test_site_selection(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = write(tmp_path, sample())
    out = tmp_path / "g.sqlite3"
    code, _, err = run(
        capsys, "--input", str(src), "--output", str(out), "--site", "blog"
    )
    assert code == 0, err
    assert rows(out, "grafana_pages_daily") == [(DAY1, "blog", 99)]
    assert ("site", "blog") in rows(out, "grafana_meta")


# ---- determinism -----------------------------------------------------------


def test_output_is_deterministic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = sample()
    a_in = write(tmp_path, data, "a.json")
    shuffled = json.loads(json.dumps(data))
    for table in shuffled["tables"].values():
        table.reverse()
    b_in = write(tmp_path, shuffled, "b.json")
    a, b, c = (tmp_path / n for n in ("a.db", "b.db", "c.db"))
    assert run(capsys, "--input", str(a_in), "--output", str(a))[0] == 0
    assert run(capsys, "--input", str(a_in), "--output", str(b))[0] == 0
    assert run(capsys, "--input", str(b_in), "--output", str(c))[0] == 0
    assert a.read_bytes() == b.read_bytes() == c.read_bytes()


def test_rerun_replaces_the_store_atomically(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = write(tmp_path, sample())
    out = tmp_path / "g.sqlite3"
    assert run(capsys, "--input", str(src), "--output", str(out))[0] == 0
    first = out.read_bytes()
    assert run(capsys, "--input", str(src), "--output", str(out))[0] == 0
    assert out.read_bytes() == first
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "export.json",
        "g.sqlite3",
    ]


# ---- empty and degenerate stores ------------------------------------------


def test_empty_export_gives_empty_tables(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = write(tmp_path, {"tables": {"view_daily": [], "click_events": []}})
    out = tmp_path / "g.sqlite3"
    code, stdout, err = run(capsys, "--input", str(src), "--output", str(out))
    assert code == 0, err
    assert tables(out) == EXPECTED_TABLES
    for table in EXPECTED_TABLES - {"grafana_meta"}:
        assert rows(out, table) == []
    assert "0 rows" in stdout or "rows" in stdout


def test_empty_subset_in_memory(tmp_path: Path) -> None:
    out = tmp_path / "g.sqlite3"
    grafana.write_store(out, grafana.build_subset(Export()))
    assert tables(out) == EXPECTED_TABLES
    assert rows(out, "grafana_pages_daily") == []


def test_everything_below_the_floor_writes_nothing(tmp_path: Path) -> None:
    from reach_around.funnel.export import load_exports

    data = {"tables": {"view_daily": [vd(views=2), vd(page="x", views=1)]}}
    subset = grafana.build_subset(load_exports([write(tmp_path, data)]))
    assert subset.pages == []
    assert subset.countries == []


# ---- bad input -------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        {"tables": {"view_daily": [{**vd(), "ip": "203.0.113.7"}]}},
        {"tables": {"view_daily": [vd(src_kind="r", src="203.0.113.7")]}},
        {"tables": {"view_daily": [vd(src_kind="r", src="Mozilla/5.0")]}},
        {"tables": {"leads": [{"email": "x@example.com"}]}},
        {"tables": {"view_daily": [vd(views=-1)]}},
        {"tables": {"view_daily": [vd(day="2026-13-45")]}},
        "not json {",
        5,
    ],
)
def test_bad_input_is_refused_and_no_store_is_written(
    data: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = write(tmp_path, data)
    out = tmp_path / "g.sqlite3"
    code, stdout, err = run(capsys, "--input", str(src), "--output", str(out))
    assert code == 2
    assert stdout == ""
    assert err.startswith("error:")
    assert "203.0.113.7" not in err and "Mozilla" not in err and "x@example" not in err
    assert not out.exists()
    assert [p.name for p in tmp_path.iterdir()] == ["export.json"]


def test_missing_input_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(
        capsys,
        "--input",
        str(tmp_path / "none.json"),
        "--output",
        str(tmp_path / "o.db"),
    )
    assert code == 2
    assert err.startswith("error:")


@pytest.mark.parametrize(
    "args",
    [
        ["--floor", "4"],
        ["--site", "Bad Site"],
        ["--floor", "x"],
        ["--cutover", "11/02/2026"],
    ],
)
def test_bad_options(
    args: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = write(tmp_path, sample())
    out = tmp_path / "g.sqlite3"
    if args == ["--floor", "x"]:
        with pytest.raises(SystemExit) as exc:
            run(capsys, "--input", str(src), "--output", str(out), *args)
        assert exc.value.code == 2
    else:
        code, _, err = run(capsys, "--input", str(src), "--output", str(out), *args)
        assert code == 2
        assert err.startswith("error:")
    assert not out.exists()


def test_build_subset_refuses_floor_below_minimum() -> None:
    with pytest.raises(ValueError, match="floor"):
        grafana.build_subset(Export(), floor=4)


def test_future_cutover_adds_no_metadata_or_annotation(tmp_path: Path) -> None:
    from datetime import date

    from reach_around.funnel.export import load_exports

    subset = grafana.build_subset(
        load_exports([write(tmp_path, sample())]),
        cutover=date(2026, 11, 3),
        today=date(2026, 11, 2),
    )
    assert subset.cutover_utc is None
    assert subset.window_era is None
    assert subset.annotations == []


def test_arrived_cutover_without_retained_days_keeps_marker_and_annotation() -> None:
    from datetime import date

    subset = grafana.build_subset(
        Export(), cutover=date(2026, 11, 2), today=date(2026, 11, 3)
    )
    assert subset.cutover_utc == "2026-11-02"
    assert subset.annotations == [("2026-11-02", "Reach Around cutover")]
    assert subset.window_era is None


def test_output_must_not_clobber_a_foreign_file_or_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = write(tmp_path, sample())
    foreign = tmp_path / "notes.txt"
    foreign.write_text("keep me", encoding="utf-8")
    code, _, err = run(capsys, "--input", str(src), "--output", str(foreign))
    assert code == 2 and err.startswith("error:")
    assert foreign.read_text(encoding="utf-8") == "keep me"
    directory = tmp_path / "d"
    directory.mkdir()
    code, _, err = run(capsys, "--input", str(src), "--output", str(directory))
    assert code == 2 and err.startswith("error:")
    link = tmp_path / "link.db"
    link.symlink_to(foreign)
    code, _, err = run(capsys, "--input", str(src), "--output", str(link))
    assert code == 2 and err.startswith("error:")
    assert foreign.read_text(encoding="utf-8") == "keep me"


def test_output_directory_must_exist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = write(tmp_path, sample())
    code, _, err = run(
        capsys, "--input", str(src), "--output", str(tmp_path / "nope" / "g.db")
    )
    assert code == 2 and err.startswith("error:")


def test_job_never_touches_the_network(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("network used")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    src = write(tmp_path, sample())
    assert run(capsys, "--input", str(src), "--output", str(tmp_path / "g.db"))[0] == 0


def test_example_queries_in_the_doc_execute(tmp_path: Path) -> None:
    doc = Path(__file__).resolve().parents[1] / "docs" / "grafana.md"
    queries = re.findall(r"```sql\n(.*?)\n```", doc.read_text(encoding="utf-8"), re.S)
    assert len(queries) >= 5  # collect loosely, assert that something was found
    db = export_ok(tmp_path)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for query in queries:
            assert con.execute(query).fetchall() is not None
        # the guard bites: a query on a table outside the subset fails
        with pytest.raises(sqlite3.OperationalError):
            con.execute("SELECT * FROM view_geo_daily")
    finally:
        con.close()


def test_rows_older_than_90_days_are_dropped(tmp_path: Path) -> None:
    from reach_around.funnel.export import load_exports

    data = {
        "tables": {
            "view_daily": [
                vd(views=50, day="2026-08-01"),  # 122 days before the newest
                vd(
                    views=50, day="2026-08-04"
                ),  # the 90th day counting the newest: kept
                vd(views=50, day="2026-08-03"),  # the 91st day: dropped
                vd(views=50, day="2026-11-01"),
            ],
            "click_events": [{"ts": 1_785_542_400_000, "link_id": "old"}] * 6,
        }
    }
    subset = grafana.build_subset(load_exports([write(tmp_path, data)]))
    days = sorted({row[0] for row in subset.views})
    assert days == ["2026-08-04", "2026-11-01"]
    assert subset.clicks == []  # 2026-08-01 is outside the window


def test_symlinked_parent_directory_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = write(tmp_path, sample())
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "linkdir").symlink_to(real)
    code, _, err = run(
        capsys, "--input", str(src), "--output", str(tmp_path / "linkdir" / "g.db")
    )
    assert code == 2 and err.startswith("error:")
    assert list(real.iterdir()) == []


def test_not_human_query_ignores_the_suppression_row(tmp_path: Path) -> None:
    from reach_around.funnel.export import load_exports

    # humans are under the floor, so their views sit in the suppression row
    data = {
        "tables": {
            "view_daily": [
                vd(views=2),
                vd(page="x", views=2),
                vd(bot_class="a", views=40),
                vd(bot_class="s", views=20),
            ]
        }
    }
    db = tmp_path / "g.db"
    grafana.write_store(db, grafana.build_subset(load_exports([write(tmp_path, data)])))
    doc = (Path(__file__).resolve().parents[1] / "docs" / "grafana.md").read_text(
        encoding="utf-8"
    )
    query = next(
        q for q in re.findall(r"```sql\n(.*?)\n```", doc, re.S) if "not_human" in q
    )
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        assert con.execute(query).fetchall() == [(DAY1, 100.0)]
    finally:
        con.close()
