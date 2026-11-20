# SPDX-License-Identifier: Apache-2.0
"""The private analytics block of the owner report (#397): views, watch time and
demographics read from the private-provider records of the history file, with
the privacy floor of 5 on every count. Synthetic data only."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from reach_around.counts.history import History, Record, append_records, read_history
from reach_around.funnel.private_analytics import (
    CSV_HEADER,
    build_private,
    private_csv_rows,
    private_lines,
)
from test_report_devices_csv import GOOD, export, run

TODAY = date(2026, 11, 19)
IG = "instagram_insights"
YT = "youtube_analytics"


def rec(
    metric: str,
    value: int,
    *,
    platform: str = "instagram",
    provider: str | None = IG,
    day: str = "2026-11-16",
    source: str = "api",
    era: str | None = None,
) -> Record:
    return Record(
        platform=platform,
        metric=metric,
        value=value,
        as_of_utc=f"{day}T09:00:00Z",
        source=source,
        run_id="refresh-test",
        era=era,
        api_provider=provider,
    )


def block(records: list[Record], **kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = dict(cutover=None, today=TODAY, zone=None)
    args.update(kw)
    result: dict[str, Any] = build_private(History(tuple(records)), **args)
    return result


def group(b: dict[str, Any], name: str) -> dict[str, Any]:
    found = [g for s in b["sections"] for g in s["groups"] if g["name"] == name]
    assert len(found) == 1, name
    result: dict[str, Any] = found[0]
    return result


def rows_of(g: dict[str, Any]) -> dict[str, int]:
    return {r["label"]: r["value"] for r in g["rows"]}


AGE = "Followers by age"


def ages(*cells: tuple[str, int]) -> list[Record]:
    return [rec(f"fdemo_age_{k}", v) for k, v in cells]


# --- floors -----------------------------------------------------------------


def test_a_cell_of_four_is_never_printed_and_five_is() -> None:
    g = group(block(ages(("18_24", 40), ("25_34", 4), ("35_44", 5))), AGE)
    assert rows_of(g) == {"35-44": 5}  # 4 hidden; the largest (40) joined it
    assert g["other"] == 44
    g = group(block(ages(("18_24", 40), ("25_34", 6), ("35_44", 5))), AGE)
    assert rows_of(g) == {"18-24": 40, "25-34": 6, "35-44": 5}
    assert g["other"] is None


def test_text_json_and_csv_never_hold_a_hidden_cell() -> None:
    b = block(ages(("18_24", 400), ("25_34", 4), ("35_44", 50)))
    text = "\n".join(private_lines(b))
    assert "25-34" not in text
    assert "25-34" not in json.dumps(b)
    assert "25-34" not in repr(private_csv_rows(b))
    # the hidden cell and the largest shown cell sit in one remainder line
    assert g_other(b) == 404


def g_other(b: dict[str, Any]) -> int:
    other = group(b, AGE)["other"]
    assert isinstance(other, int)
    return other


def test_a_stored_other_under_the_floor_is_not_printed_alone() -> None:
    recs = ages(("18_24", 40), ("25_34", 30)) + [rec("fdemo_age_other", 3)]
    g = group(block(recs), AGE)
    # 3 is a lone small cell; the largest shown cell joins so the line bounds
    # nothing, and the 3 is not printed on its own
    assert 3 not in rows_of(g).values()
    assert g["other"] == 3 + 40
    assert rows_of(g) == {"25-34": 30}


def test_a_stored_other_at_the_floor_is_printed_as_the_other_line() -> None:
    recs = ages(("18_24", 40), ("25_34", 30)) + [rec("fdemo_age_other", 5)]
    g = group(block(recs), AGE)
    assert rows_of(g) == {"18-24": 40, "25-34": 30}
    assert g["other"] == 5


def test_hidden_cells_fold_into_the_stored_other() -> None:
    recs = ages(("18_24", 40), ("25_34", 2)) + [rec("fdemo_age_other", 9)]
    g = group(block(recs), AGE)
    assert rows_of(g) == {}
    assert g["other"] == 40 + 2 + 9


def test_only_small_cells_with_nothing_to_join_withhold_the_group() -> None:
    g = group(block(ages(("18_24", 2), ("25_34", 3))), AGE)
    assert g["state"] == "withheld"
    assert g["rows"] == [] and g["other"] is None
    lines = "\n".join(private_lines(block(ages(("18_24", 2), ("25_34", 3)))))
    assert "withheld" in lines


def test_every_count_group_uses_the_floor() -> None:
    recs = [
        rec("fdemo_gender_f", 30),
        rec("fdemo_gender_m", 3),
        rec("fdemo_country_us", 50),
        rec("fdemo_country_de", 4),
        rec("geo_us_views", 500, platform="youtube", provider=YT),
        rec("geo_nz_views", 4, platform="youtube", provider=YT),
    ]
    b = block(recs)
    for name, hidden in (
        ("Followers by gender", "M"),
        ("Followers by country", "DE"),
        ("Views by country", "NZ"),
    ):
        g = group(b, name)
        assert hidden not in rows_of(g)
        assert all(v >= 5 for v in rows_of(g).values())


def test_activity_under_the_floor_is_withheld_and_zero_is_not() -> None:
    recs = [
        rec("views_7d", 4, day="2026-11-14"),
        rec("views_7d", 5, day="2026-11-15"),
        rec("views_7d", 0, day="2026-11-16"),
    ]
    b = block(recs)
    points = b["sections"][0]["activity"][0]["points"]
    by_day = {p["as_of_utc"][:10]: p for p in points}
    assert by_day["2026-11-14"]["value"] is None
    assert by_day["2026-11-14"]["status"] == "below_floor"
    assert by_day["2026-11-15"]["value"] == 5
    assert by_day["2026-11-16"]["value"] == 0
    text = "\n".join(private_lines(b))
    assert "below the floor" in text
    csv_rows = private_csv_rows(b)
    hidden = [r for r in csv_rows if r[CSV_HEADER.index("status")] == "below_floor"]
    assert [r[CSV_HEADER.index("value")] for r in hidden] == [""]


def test_a_raised_floor_applies() -> None:
    g = group(block(ages(("18_24", 40), ("25_34", 8), ("35_44", 30)), floor=10), AGE)
    assert 8 not in rows_of(g).values()


def test_shares_are_percents_and_are_not_counts() -> None:
    recs = [
        rec("demo_a18_24_f", 4025, platform="youtube", provider=YT),
        rec("demo_a25_34_m", 2500, platform="youtube", provider=YT),
        rec("demo_other", 3475, platform="youtube", provider=YT),
    ]
    g = group(block(recs), "Viewer shares by age and gender (percent)")
    assert rows_of(g)["18-24 F"] == 4025
    text = "\n".join(private_lines(block(recs)))
    assert "40.25%" in text and "34.75%" in text


# --- provenance, dates, eras -------------------------------------------------


def test_each_figure_carries_platform_provider_date_and_label() -> None:
    b = block([rec("views_7d", 80)])
    text = "\n".join(private_lines(b))
    assert "instagram" in text and IG in text
    assert "2026-11-16T09:00:00Z" in text
    assert "collected" in text and "not hand-kept" in text
    assert "UTC" in text
    assert "owner" in text.lower()


def test_era_marks_follow_the_cutover() -> None:
    recs = [
        rec("views_7d", 80, day="2026-10-01"),
        rec("views_7d", 90, day="2026-11-16"),
    ]
    b = block(recs, cutover=date(2026, 11, 1))
    points = b["sections"][0]["activity"][0]["points"]
    assert [p["era"] for p in points] == ["before", "after"]
    assert "before Reach Around" in "\n".join(private_lines(b))
    none = block(recs)
    assert all("era" not in p for p in none["sections"][0]["activity"][0]["points"])


def test_a_definition_tag_is_shown() -> None:
    b = block([rec("views_7d", 80, era="views-v2")])
    assert "views-v2" in "\n".join(private_lines(b))


def test_never_summed_across_platforms() -> None:
    recs = [
        rec("views_7d", 80),
        rec("views_1d", 90, platform="youtube", provider=YT),
    ]
    b = block(recs)
    assert [s["platform"] for s in b["sections"]] == ["instagram", "youtube"]
    flat = json.dumps(b)
    assert "total" not in flat.lower().replace("total_interactions", "")
    assert "never added across platforms" in "\n".join(private_lines(b))


def test_the_display_zone_adds_a_labeled_instant() -> None:
    b = block([rec("views_7d", 80)], zone="America/New_York")
    assert "2026-11-16T09:00:00Z" in "\n".join(private_lines(b))


# --- what is refused ----------------------------------------------------------


def test_untagged_and_hand_kept_records_are_not_in_the_block() -> None:
    recs = [
        rec("views_7d", 777, provider=None),
        rec("views_28d", 888, platform="youtube", provider=None, source="hand_kept"),
        rec("followers", 999, provider="youtube_data_api", platform="youtube"),
    ]
    b = block(recs)
    assert b["sections"] == []
    assert "777" not in json.dumps(b) and "888" not in json.dumps(b)


def test_an_unrecognized_private_metric_is_counted_not_shown() -> None:
    b = block([rec("mystery_metric", 123456), rec("views_7d", 80)])
    assert b["unrecognized"] == 1
    assert "123456" not in json.dumps(b)


def test_a_correction_goes_with_its_private_original() -> None:
    original = rec("views_7d", 80)
    fix = Record(
        platform="instagram",
        metric="views_7d",
        value=90,
        as_of_utc=original.as_of_utc,
        source="api",
        run_id="fix",
        type="correction",
        api_provider=IG,
    )
    points = block([original, fix])["sections"][0]["activity"][0]["points"]
    assert [p["value"] for p in points] == [90]


# --- the command ---------------------------------------------------------------


def history_file(tmp_path: Path, records: list[Record]) -> Path:
    path = tmp_path / "social.jsonl"
    append_records(path, records, now=datetime(2026, 11, 19, 12, 0, tzinfo=UTC))
    return path


SENTINEL = 7_654_321


def private_records() -> list[Record]:
    return [
        rec("views_7d", SENTINEL),
        rec("fdemo_age_18_24", 60),
        rec("fdemo_age_25_34", 3),
        rec("fdemo_age_35_44", 20),
    ]


def test_cli_text_json_and_csv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hist = history_file(tmp_path, private_records())
    inp = str(export(tmp_path, GOOD))
    code, out, err = run(capsys, "--input", inp, "--history", str(hist))
    assert code == 0, err
    assert "Private platform analytics" in out
    assert str(SENTINEL) in out or f"{SENTINEL:,}" in out
    assert "25-34" not in out
    code, out, _ = run(capsys, "--input", inp, "--history", str(hist), "--json")
    data = json.loads(out)
    assert "private_analytics" in data
    assert "25-34" not in out
    out_dir = tmp_path / "csv"
    code, _, err = run(
        capsys, "--input", inp, "--history", str(hist), "--csv", str(out_dir)
    )
    assert code == 0, err
    rows = list(
        csv.DictReader(io.StringIO((out_dir / "private_analytics.csv").read_text()))
    )
    assert rows and "25-34" not in json.dumps(rows)
    assert [r for r in rows if r["status"] == "below_floor"] == []


def test_cli_without_history_has_no_block(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inp = str(export(tmp_path, GOOD))
    _, out, _ = run(capsys, "--input", inp, "--json")
    assert "private_analytics" not in json.loads(out)
    out_dir = tmp_path / "csv"
    run(capsys, "--input", inp, "--csv", str(out_dir))
    assert not (out_dir / "private_analytics.csv").exists()


def test_the_html_owner_report_does_not_carry_the_block(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hist = history_file(tmp_path, private_records())
    page = tmp_path / "owner.html"
    code, _, err = run(
        capsys,
        "--input",
        str(export(tmp_path, GOOD)),
        "--history",
        str(hist),
        "--html",
        str(page),
    )
    assert code == 0, err
    text = page.read_text(encoding="utf-8")
    assert str(SENTINEL) not in text and f"{SENTINEL:,}" not in text


def test_the_social_block_still_refuses_the_private_tags() -> None:
    from reach_around.funnel.social import build_social

    social = build_social(
        History(tuple(private_records())), cutover=None, today=TODAY, zone=None
    )
    flat = json.dumps(social)
    assert str(SENTINEL) not in flat
    assert re.search(r"refused", flat)


def test_the_floor_cannot_be_lowered() -> None:
    with pytest.raises(ValueError, match="floor cannot be below 5"):
        block(private_records(), floor=4)


def leaks(texts: list[str], forms: list[str]) -> list[str]:
    return [form for form in forms if any(form in text for text in texts)]


def sentinel_config(root: Path) -> Path:
    """A real instance: a kit with a gate and a ``numbers.history_file`` that
    points at a history file full of private-provider sentinels."""
    from kitpdfharness import kit_doc

    kit = kit_doc()
    kit.pop("case_studies")  # needs a picture file; not what this test is about
    doc = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": [
            {
                "id": "start",
                "title": "Start",
                "blocks": [
                    {
                        "id": "socials",
                        "items": [
                            {
                                "platform": "youtube",
                                "handle": "@example",
                                "url": "https://example.com/yt",
                            }
                        ],
                    }
                ],
            }
        ],
        "features": {"kit_gate": True},
        "numbers": {
            "history_file": "history/social.jsonl",
            "sources": [
                {
                    "platform": "youtube",
                    "kind": "youtube",
                    "channel_id": "UCaaaaaaaaaaaaaaaaaaaaaa",
                    "analytics": True,
                }
            ],
        },
        "kit": kit,
    }
    (root / "history").mkdir(parents=True)
    history_file(
        root / "history",
        [
            *private_records(),
            rec("views_1d", SENTINEL, platform="youtube", provider=YT),
            rec("geo_us_views", SENTINEL + 1, platform="youtube", provider=YT),
        ],
    )
    path = root / "site.yaml"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_private_sentinels_never_reach_the_public_site_or_either_pdf(
    tmp_path: Path,
) -> None:
    """The build is handed the history file through ``numbers.history_file`` and
    the file holds sentinels; nothing public or gated may carry them."""
    from kitpdfharness import fake_renderer
    from reach_around.cli import main
    from reach_around.config import load_site_config
    from reach_around.kit.pdf import build_kit_pdf, build_public_kit_pdf
    from reach_around.kit.pdftext import extract_pages

    root = tmp_path / "inst"
    config_path = sentinel_config(root)
    config = load_site_config(config_path)
    assert config.numbers is not None and config.numbers.history_file is not None
    history_path = root / config.numbers.history_file
    assert history_path.is_file() and history_path.stat().st_size > 0
    site, gated = tmp_path / "site", tmp_path / "gated"
    code = main(
        [
            "build",
            "--config",
            str(config_path),
            "--out",
            str(site),
            "--gated-out",
            str(gated),
        ]
    )
    assert code == 0
    forms = [str(n) for n in (SENTINEL, SENTINEL + 1)]
    forms += [f"{n:,}" for n in (SENTINEL, SENTINEL + 1)] + ["7.7M", "7654.3k"]
    texts = [
        p.read_text(errors="replace")
        for tree in (site, gated)
        for p in sorted(tree.rglob("*"))
        if p.is_file()
    ]
    common: dict[str, Any] = dict(
        site_dir=site, name="kit.pdf", created=date(2027, 3, 10), renderer=fake_renderer
    )
    public = build_public_kit_pdf(config, out_dir=tmp_path / "out-a", **common)
    private = build_kit_pdf(
        config, gated_dir=gated, out_dir=tmp_path / "out-b", **common
    )
    for result in (public, private):
        texts.append(" ".join(extract_pages(result.path.read_bytes())))
    assert len(texts) > 3
    assert leaks(texts, forms) == []
    # the guard bites: one page carrying a derived form is caught
    assert leaks([*texts, "<p>Views 7.7M</p>"], forms) == ["7.7M"]


def test_the_sentinel_history_really_holds_the_sentinels(tmp_path: Path) -> None:
    """Control: the report command does read the same file and prints them."""
    root = tmp_path / "inst"
    sentinel_config(root)
    history = read_history(root / "history" / "social.jsonl")
    values = {r.value for r in history.records}
    assert {SENTINEL, SENTINEL + 1} <= values


def test_no_printed_remainder_or_row_pins_a_cell_under_the_floor() -> None:
    """Every small dataset: a printed row is at least the floor and a printed
    remainder is at least the floor, so subtracting the printed rows from the
    followers count (printed elsewhere) cannot isolate a cell under it."""
    import itertools

    values = (0, 1, 3, 4, 5, 9)
    checked = 0
    for a, b, c, other in itertools.product(values, values, values, (0, 2, 5, 6)):
        recs = ages(("18_24", a), ("25_34", b), ("35_44", c))
        if other:
            recs.append(rec("fdemo_age_other", other))
        g = group(block(recs), AGE)
        checked += 1
        assert all(r["value"] >= 5 for r in g["rows"])
        assert g["other"] is None or g["other"] >= 5


# --- shares beside printed counts ---------------------------------------------


def yt_shares(
    bp: int, geo_us: int, geo_other: int, other_bp: int = 9800
) -> list[Record]:
    return [
        rec("demo_a18_24_f", bp, platform="youtube", provider=YT),
        rec("demo_other", other_bp, platform="youtube", provider=YT),
        rec("geo_us_views", geo_us, platform="youtube", provider=YT),
        rec("geo_other_views", geo_other, platform="youtube", provider=YT),
    ]


SHARE = "Viewer shares by age and gender (percent)"


def test_a_share_beside_a_small_printed_total_is_withheld() -> None:
    # 2.00 percent of the 150 printed views would be 3 people or views
    g = group(block(yt_shares(200, 120, 30)), SHARE)
    assert g["state"] == "withheld" and g["rows"] == []
    text = "\n".join(private_lines(block(yt_shares(200, 120, 30))))
    assert "2.00%" not in text and "withheld" in text


def test_shares_beside_a_large_printed_total_are_printed() -> None:
    g = group(block(yt_shares(200, 120_000, 30_000, 9800)), SHARE)
    assert g["state"] == "ok"
    assert rows_of(g)["18-24 F"] == 200


@pytest.mark.parametrize(("bp", "hidden"), [(300, True), (400, False), (500, False)])
def test_the_report_share_boundaries(bp: int, hidden: bool) -> None:
    g = group(block(yt_shares(bp, 120, 30, other_bp=10000 - bp)), SHARE)
    assert (g["state"] == "withheld") is hidden


def test_a_printed_activity_count_is_also_a_base() -> None:
    recs = [
        rec("demo_a18_24_f", 200, platform="youtube", provider=YT),
        rec("demo_other", 9800, platform="youtube", provider=YT),
        rec("views_1d", 100, platform="youtube", provider=YT),
    ]
    assert group(block(recs), SHARE)["state"] == "withheld"
    recs[2] = rec("views_1d", 100_000, platform="youtube", provider=YT)
    assert group(block(recs), SHARE)["state"] == "ok"


# --- a stored remainder alone ---------------------------------------------------


@pytest.mark.parametrize("value", [1, 4, 5, 20])
def test_a_group_whose_only_positive_cell_is_the_stored_other_is_withheld(
    value: int,
) -> None:
    """A remainder made of one cell is that cell, and the stored value cannot
    tell which; with no named cell beside it the group prints nothing."""
    b = block([rec("fdemo_age_other", value)])
    g = group(b, AGE)
    assert g["state"] == "withheld" and g["rows"] == [] and g["other"] is None
    assert "other" not in "\n".join(private_lines(b)).replace("(other", "")
    assert [
        r for r in private_csv_rows(b) if r[CSV_HEADER.index("status")] == "ok"
    ] == []


def test_zero_named_cells_beside_the_stored_other_are_still_alone() -> None:
    recs = ages(("18_24", 0)) + [rec("fdemo_age_other", 20)]
    assert group(block(recs), AGE)["state"] == "withheld"


@pytest.mark.parametrize("value", [1, 4, 5, 20])
def test_the_stored_other_beside_a_named_cell_follows_the_floor_rule(
    value: int,
) -> None:
    recs = ages(("18_24", 40)) + [rec("fdemo_age_other", value)]
    g = group(block(recs), AGE)
    if value < 5:
        # the small remainder and the named cell are merged into one line
        assert g["rows"] == [] and g["other"] == 40 + value
    else:
        assert rows_of(g) == {"18-24": 40} and g["other"] == value
