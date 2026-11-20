# SPDX-License-Identifier: Apache-2.0
"""The kit block "before Reach Around": link-page history imported from the old
provider (#377). Synthetic data only; the sentinel numbers below are made up.

The block comes from an owner-supplied file, never from the social history
file, is gated unless the owner sets it public, and never touches a total.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from test_kit_build import TODAY, build, gated_doc, kit_doc, make_config

import reach_around.commands.counts as counts_cmd
from kitpdfharness import build_instance, fake_renderer
from kitpdfharness import make_config as pdf_config
from reach_around.cli import main
from reach_around.config import ConfigError, SiteConfig
from reach_around.config.imported_history import (
    IMPORTED_HEADING,
    IMPORTED_NOTE,
    ImportedHistory,
    imported_source,
    load_imported,
    parse_imported,
)
from reach_around.config.kit import DISPLAY_POLICY, gated_projection, public_projection
from reach_around.counts.beacons import parse_beacons_csv
from reach_around.counts.history import (
    Record,
    dump_new_history,
    read_history,
)
from reach_around.counts.imported_export import export_imported
from reach_around.kit.pdf import (
    PdfError,
    PdfLeakError,
    _expected_text,
    build_kit_pdf,
    build_public_kit_pdf,
    check_text,
)
from reach_around.kit.pdftext import extract_pages
from reach_around.site.kit import GATED_FILE, KitLeakError, check_no_gated_text

GOLDEN = Path(__file__).parent / "golden_kit_imported"
CUTOVER = date(2026, 6, 1)
# Sentinels: numbers nothing else in the kit prints.
JAN_VIEWS, JAN_CLICKS = 9171823, 4242017
FEB_VIEWS, FEB_CLICKS = 8100455, 3111099
JUN_VIEWS, JUN_CLICKS = 7777331, 2222113  # on or after the cutover


def imported_doc(*, with_after: bool = False) -> dict[str, Any]:
    periods = [
        {
            "start": "2026-01-01",
            "end": "2026-01-31",
            "days": 31,
            "views": JAN_VIEWS,
            "clicks": JAN_CLICKS,
        },
        {
            "start": "2026-02-01",
            "end": "2026-02-28",
            "days": 20,
            "views": FEB_VIEWS,
            "clicks": FEB_CLICKS,
        },
    ]
    if with_after:
        periods.append(
            {
                "start": "2026-06-01",
                "end": "2026-06-30",
                "days": 30,
                "views": JUN_VIEWS,
                "clicks": JUN_CLICKS,
            }
        )
    return {
        "version": 1,
        "source": "imported",
        "note": "Beacons",
        "platform": "link-page",
        "periods": periods,
    }


def imported(**kw: Any) -> ImportedHistory:
    return parse_imported(json.dumps(imported_doc(**kw)).encode())


def config(
    *, public: bool = False, cutover: date | None = CUTOVER, **kit: Any
) -> SiteConfig:
    doc = gated_doc(imported_history="imported-history.json", **kit)
    if public:
        doc["imported_history_public"] = True
    extra: dict[str, Any] = {}
    if cutover is not None:
        extra["cutover_date"] = cutover.isoformat()
    return make_config(doc, **extra)


def pages(tmp_path: Path, **kw: Any) -> tuple[str, str]:
    imp = kw.pop("imp", imported())
    out, gated = build(tmp_path, kw.pop("cfg", None) or config(**kw), imported=imp)
    return (
        (out / "kit" / "index.html").read_text(encoding="utf-8"),
        (gated / GATED_FILE).read_text(encoding="utf-8"),
    )


def numbers_section(html: str) -> str:
    match = re.search(r'<section class="kit-numbers".*?</section>', html, re.S)
    assert match, "no headline numbers section"
    return match.group(0)


SENTINELS = (
    f"{JAN_VIEWS:,}",
    f"{JAN_CLICKS:,}",
    f"{FEB_VIEWS:,}",
    f"{FEB_CLICKS:,}",
)


# --- the file and its parser ---


def test_the_file_parses_to_aggregate_periods_only() -> None:
    data = imported()
    assert [p.views for p in data.periods] == [JAN_VIEWS, FEB_VIEWS]
    assert data.periods[0].start == date(2026, 1, 1)
    assert data.source == "imported" and data.note == "Beacons"


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(extra="x"),
        lambda d: d["periods"][0].update(referrer="example.com"),
        lambda d: d.update(version=2),
        lambda d: d.update(source="api"),
        lambda d: d.update(platform="twitch"),
        lambda d: d["periods"][0].update(views=-1),
        lambda d: d["periods"][0].update(views=1.5),
        lambda d: d["periods"][0].update(views=True),
        lambda d: d["periods"][0].update(views=10**13),
        lambda d: d["periods"][0].update(end="2025-12-31"),
        lambda d: d["periods"][0].update(days=0),
        lambda d: d["periods"][0].update(days=32),
        lambda d: d["periods"].append(dict(d["periods"][0])),
        lambda d: d.update(periods=[]),
        lambda d: d.update(periods=d["periods"] * 100),
        lambda d: d.update(note="<b>x</b>"),
    ],
)
def test_the_parser_refuses_a_file_that_is_not_the_aggregate_shape(
    change: Any,
) -> None:
    doc = imported_doc()
    change(doc)
    with pytest.raises(ConfigError):
        parse_imported(json.dumps(doc).encode())


def test_the_parser_refuses_a_history_line_a_duplicate_key_and_junk() -> None:
    line = (
        '{"platform":"link-page","metric":"views","value":1,'
        '"as_of_utc":"2026-01-01T00:00:00Z","source":"imported",'
        '"run_id":"beacons-import","prev":"' + "0" * 64 + '"}'
    )
    for raw in (
        line.encode(),
        b'{"version":1,"version":1}',
        b"not json",
        b"\xff",
        b"[]",
    ):
        with pytest.raises(ConfigError):
            parse_imported(raw)


def test_the_loader_refuses_a_symlink_and_a_huge_file(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text(json.dumps(imported_doc()), encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    assert load_imported(real).periods
    with pytest.raises(ConfigError):
        load_imported(link)
    big = tmp_path / "big.json"
    big.write_bytes(b" " * (300 * 1024))
    with pytest.raises(ConfigError):
        load_imported(big)


def test_the_config_key_is_a_relative_path_inside_the_config_folder() -> None:
    for bad in ("/abs.json", "../x.json", "a/../b.json", "a\\b.json", ""):
        with pytest.raises(ValueError):
            make_config(kit_doc(imported_history=bad))
    assert make_config(gated_doc(imported_history="private/x.json")).kit is not None


def test_the_file_may_not_be_the_social_history_file(tmp_path: Path) -> None:
    cfg = make_config(
        kit_doc(imported_history="history.jsonl"),
        numbers={"history_file": "history.jsonl"},
    )
    (tmp_path / "site.yaml").write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match="history_file"):
        imported_source(cfg, tmp_path / "site.yaml")


def test_imported_source_is_none_without_the_key(tmp_path: Path) -> None:
    (tmp_path / "site.yaml").write_text("", encoding="utf-8")
    assert imported_source(make_config(), tmp_path / "site.yaml") is None
    assert (
        imported_source(config(), tmp_path / "site.yaml")
        == tmp_path / "imported-history.json"
    )


# --- the policy table ---


def test_the_policy_gates_the_block_and_keeps_its_switch_private() -> None:
    assert DISPLAY_POLICY["imported_history"] == "gated"
    assert DISPLAY_POLICY["imported_history_public"] == "private"


def test_projections_follow_the_switch() -> None:
    data = imported()
    gated_cfg, public_cfg = config().kit, config(public=True).kit
    assert gated_cfg is not None and public_cfg is not None
    assert "imported_history" not in public_projection(gated_cfg, None, data)
    assert gated_projection(gated_cfg, None, data)["imported_history"] is data
    assert public_projection(public_cfg, None, data)["imported_history"] is data
    assert "imported_history_public" not in gated_projection(public_cfg, None, data)


# --- the page ---


def test_default_is_gated_only_the_gated_page_has_the_block(tmp_path: Path) -> None:
    public, gated = pages(tmp_path)
    assert IMPORTED_HEADING in gated and IMPORTED_NOTE in gated
    for number in SENTINELS:
        assert number in gated and number not in public
    assert IMPORTED_HEADING not in public and IMPORTED_NOTE not in public
    assert 'class="kit-imported"' in gated


def test_the_public_page_is_byte_identical_with_and_without_the_file(
    tmp_path: Path,
) -> None:
    with_file, _ = pages(tmp_path / "a")
    out, _ = build(tmp_path / "b", config(), imported=None)
    assert (out / "kit" / "index.html").read_text(encoding="utf-8") == with_file


def test_owner_may_set_it_public(tmp_path: Path) -> None:
    public, gated = pages(tmp_path, public=True)
    for page in (public, gated):
        assert IMPORTED_HEADING in page and IMPORTED_NOTE in page
        for number in SENTINELS:
            assert number in page


@pytest.mark.parametrize("public", [False, True])
def test_headline_numbers_and_total_are_unchanged_by_the_file(
    tmp_path: Path, public: bool
) -> None:
    with_file = pages(tmp_path / "a", public=public)
    out, gated_dir = build(
        tmp_path / "b", config(public=public), imported=None, gated=True
    )
    without = (
        (out / "kit" / "index.html").read_text(encoding="utf-8"),
        (gated_dir / GATED_FILE).read_text(encoding="utf-8"),
    )
    for a, b in zip(with_file, without, strict=True):
        assert numbers_section(a) == numbers_section(b)
        assert numbers_section(a).encode() == numbers_section(b).encode()
    # the sum is of the collected metrics only: 1200 + 3400 (the third is out)
    assert "4,600" in numbers_section(with_file[0])
    for number in SENTINELS:
        assert number not in numbers_section(with_file[0])
        assert number not in numbers_section(with_file[1])


def test_era_marks_apply_and_months_from_the_cutover_on_are_left_out(
    tmp_path: Path,
) -> None:
    _, gated = pages(tmp_path, imp=imported(with_after=True))
    assert f"{JUN_VIEWS:,}" not in gated and f"{JUN_CLICKS:,}" not in gated
    block = re.search(r'<section class="kit-imported".*?</section>', gated, re.S)
    assert block
    assert block.group(0).count('class="kit-era"') == 2
    assert "before Reach Around" in block.group(0)
    assert "20 of 28 days" in block.group(0)  # a partial month is said to be one


def test_without_a_cutover_every_month_shows_and_no_era_mark(tmp_path: Path) -> None:
    _, gated = pages(tmp_path, imp=imported(with_after=True), cutover=None)
    block = re.search(r'<section class="kit-imported".*?</section>', gated, re.S)
    assert block
    assert f"{JUN_VIEWS:,}" in block.group(0)
    assert 'class="kit-era"' not in block.group(0)


def test_no_block_when_the_file_is_not_loaded(tmp_path: Path) -> None:
    _, gated = pages(tmp_path, imp=None)
    assert IMPORTED_HEADING not in gated


def test_the_block_alone_makes_the_kit_gated() -> None:
    kit = make_config(kit_doc(imported_history="x.json")).kit
    assert kit is not None and kit.has_gated_content
    plain = make_config(kit_doc(), features={"kit_gate": False}).kit
    assert plain is not None and not plain.has_gated_content
    pub = make_config(
        kit_doc(imported_history="x.json", imported_history_public=True)
    ).kit
    assert pub is not None and not pub.has_gated_content


# --- the leak search ---


def leaky_files(number: str) -> dict[Path, bytes]:
    return {Path("kit/index.html"): f"<p>{number} views</p>".encode()}


def test_the_leak_search_finds_an_imported_value_in_a_public_file() -> None:
    kit = config().kit
    assert kit is not None
    for number in (f"{JAN_VIEWS:,}", str(JAN_VIEWS)):
        with pytest.raises(KitLeakError, match="imported_history"):
            check_no_gated_text(leaky_files(number), kit, imported())
    clicks = {Path("kit/index.html"): f"<p>{JAN_CLICKS:,} clicks</p>".encode()}
    with pytest.raises(KitLeakError):
        check_no_gated_text(clicks, kit, imported())


def test_the_leak_search_negative_cases() -> None:
    kit, public_kit = config().kit, config(public=True).kit
    assert kit is not None and public_kit is not None
    # nothing to search for without the file: the guard bites only when told
    check_no_gated_text(leaky_files(f"{JAN_VIEWS:,}"), kit, None)
    # a public block is public by the owner's choice
    check_no_gated_text(leaky_files(f"{JAN_VIEWS:,}"), public_kit, imported())
    # an unrelated public file passes
    check_no_gated_text({Path("a.html"): b"<p>hello</p>"}, kit, imported())


def test_a_build_refuses_to_publish_a_sentinel_in_a_public_file(
    tmp_path: Path,
) -> None:
    cfg = config(about=f"Seen {JAN_VIEWS:,} views before.")
    with pytest.raises(KitLeakError):
        build(tmp_path, cfg, imported=imported())


# --- the PDF ---


def pdf_config_with_imported(public: bool = False) -> SiteConfig:
    from kitpdfharness import kit_doc as pdf_kit_doc

    doc = pdf_kit_doc()
    doc["imported_history"] = "imported-history.json"
    if public:
        doc["imported_history_public"] = True
    return pdf_config(doc)


def flat(path: Path) -> str:
    return "".join(" ".join(extract_pages(path.read_bytes())).split())


def test_private_pdf_prints_the_block_and_public_pdf_does_not(tmp_path: Path) -> None:
    cfg = pdf_config_with_imported()
    data = imported()
    _, site, gated = build_instance(tmp_path, cfg, imported=data)
    private = build_kit_pdf(
        cfg,
        gated_dir=gated,
        site_dir=site,
        out_dir=tmp_path / "private",
        name="kit.pdf",
        created=date(2027, 3, 10),
        renderer=fake_renderer,
        imported=data,
    ).path
    public = build_public_kit_pdf(
        cfg,
        site_dir=site,
        out_dir=tmp_path / "private",
        name="kit-public.pdf",
        created=date(2027, 3, 10),
        renderer=fake_renderer,
        imported=data,
    ).path
    private_text, public_text = flat(private), flat(public)
    for number in SENTINELS:
        assert "".join(number.split()) in private_text
        assert number not in public_text
    assert "".join(IMPORTED_HEADING.split()) not in public_text


def test_the_public_pdf_text_check_finds_a_sentinel() -> None:
    cfg = pdf_config_with_imported()
    kit = cfg.kit
    assert kit is not None
    text = f"Example Creator media kit {JAN_VIEWS:,} views"
    with pytest.raises(PdfLeakError, match="imported_history"):
        check_text(kit, text, None, public=True, imported=imported())
    # negative: told nothing about the file, the guard has nothing to find
    with pytest.raises(PdfError) as err:
        check_text(kit, text, None, public=True, imported=None)
    assert not isinstance(err.value, PdfLeakError)
    # the owner made it public: the same text is not a leak
    pub_kit = pdf_config_with_imported(public=True).kit
    assert pub_kit is not None
    with pytest.raises(PdfError) as err2:
        check_text(pub_kit, text, None, public=True, imported=imported())
    assert not isinstance(err2.value, PdfLeakError)


def test_the_private_pdf_check_fails_when_the_block_is_missing() -> None:
    kit = pdf_config_with_imported().kit
    assert kit is not None
    everything = [v for _, v in _expected_text(kit, None, imported=imported())]
    ok = " ".join(everything)
    check_text(kit, ok, None, imported=imported())
    without = " ".join(v for v in everything if IMPORTED_HEADING not in v)
    with pytest.raises(PdfError, match="imported history heading"):
        check_text(kit, without, None, imported=imported())


# --- the export step ---


def beacons_history(tmp_path: Path) -> Path:
    rows = ["Section,Item,Detail,Views,Clicks,CTR (%)", "Total,Link in Bio,,40,8,20"]
    days = [("2026-01-30", 10, 2), ("2026-01-31", 20, 4), ("2026-02-01", 10, 2)]
    rows += [f"Daily,{d},,{v},{c}," for d, v, c in days]
    export = parse_beacons_csv(("\n".join(rows) + "\n").encode())
    records = [
        Record(
            "link-page",
            m,
            v,
            f"{d.day.isoformat()}T00:00:00Z",
            "imported",
            "beacons-import",
            note="Beacons",
        )
        for d in export.days
        for m, v in (("views", d.views), ("clicks", d.clicks))
    ]
    path = tmp_path / "beacons-history.jsonl"
    path.write_text(dump_new_history(records), encoding="utf-8")
    return path


def test_export_aggregates_by_month_with_no_daily_values(tmp_path: Path) -> None:
    text = export_imported(read_history(beacons_history(tmp_path)))
    data = parse_imported(text.encode())
    assert [(p.start, p.end, p.days, p.views, p.clicks) for p in data.periods] == [
        (date(2026, 1, 1), date(2026, 1, 31), 2, 30, 6),
        (date(2026, 2, 1), date(2026, 2, 28), 1, 10, 2),
    ]
    assert data.note == "Beacons" and data.source == "imported"
    for forbidden in ("2026-01-30", "run_id", "prev", "as_of_utc", "beacons-import"):
        assert forbidden not in text


def test_export_refuses_a_history_with_collected_records(tmp_path: Path) -> None:
    path = beacons_history(tmp_path)
    history = read_history(path)
    foreign = Record("twitch", "followers", 5, "2026-01-01T00:00:00Z", "api", "r")
    records = [*history.records, foreign]
    mixed = tmp_path / "mixed.jsonl"
    mixed.write_text(dump_new_history(records), encoding="utf-8")
    with pytest.raises(ConfigError):
        export_imported(read_history(mixed))
    # an imported record of another platform is refused too (platform rule alone)
    others = [
        Record("twitch", m, 5, "2025-12-01T00:00:00Z", "imported", "r", note="Beacons")
        for m in ("views", "clicks")
    ]
    only_other = tmp_path / "other.jsonl"
    only_other.write_text(
        dump_new_history([*history.records, *others]), encoding="utf-8"
    )
    with pytest.raises(ConfigError):
        export_imported(read_history(only_other))


def test_export_command_writes_a_file_the_kit_reads(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    history = beacons_history(tmp_path)
    out = tmp_path / "imported-history.json"
    assert (
        main(
            [
                "counts",
                "history-export-imported",
                "--file",
                str(history),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert load_imported(out).periods
    assert oct(out.stat().st_mode & 0o777) == "0o600"
    # never over an existing file, and never the history file itself
    assert (
        main(
            [
                "counts",
                "history-export-imported",
                "--file",
                str(history),
                "--out",
                str(out),
            ]
        )
        == 2
    )
    assert (
        main(
            [
                "counts",
                "history-export-imported",
                "--file",
                str(history),
                "--out",
                str(history),
            ]
        )
        == 2
    )
    assert counts_cmd is not None
    capsys.readouterr()


# --- the build command ---


def write_instance(root: Path, kit: dict[str, Any], **extra: Any) -> Path:
    import yaml

    data: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": [
            {
                "id": "start",
                "title": "Start",
                "blocks": [
                    {"id": "one", "title": "One", "url": "https://example.com/1"}
                ],
            }
        ],
        "features": {"kit_gate": True},
        "kit": kit,
        "cutover_date": CUTOVER.isoformat(),
    }
    data.update(extra)
    path = root / "site.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_build_command_reads_the_file_beside_the_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = write_instance(tmp_path, gated_doc(imported_history="imported-history.json"))
    (tmp_path / "imported-history.json").write_text(
        json.dumps(imported_doc()), encoding="utf-8"
    )
    out, gated = tmp_path / "out", tmp_path / "gated"
    args = ["build", "--config", str(cfg), "--out", str(out), "--gated-out", str(gated)]
    assert main(args) == 0
    assert f"{JAN_VIEWS:,}" in (gated / GATED_FILE).read_text(encoding="utf-8")
    assert f"{JAN_VIEWS:,}" not in (out / "kit" / "index.html").read_text("utf-8")
    capsys.readouterr()


def test_build_command_fails_when_the_named_file_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = write_instance(tmp_path, gated_doc(imported_history="imported-history.json"))
    args = [
        "build",
        "--config",
        str(cfg),
        "--out",
        str(tmp_path / "o"),
        "--gated-out",
        str(tmp_path / "g"),
    ]
    assert main(args) == 1
    assert "imported" in capsys.readouterr().err


# --- golden pages ---


def test_golden_pages_with_the_block(tmp_path: Path, update_golden: bool) -> None:
    _, gated = pages(tmp_path / "gated-default")
    public_page, public_gated = pages(tmp_path / "public-set", public=True)
    built = {
        "gated-default/kit.html": gated,
        "public-set/kit/index.html": public_page,
        "public-set/kit.html": public_gated,
    }
    if update_golden:
        shutil.rmtree(GOLDEN, ignore_errors=True)
        for rel, text in built.items():
            (GOLDEN / rel).parent.mkdir(parents=True, exist_ok=True)
            (GOLDEN / rel).write_text(text, encoding="utf-8")
    for rel, text in built.items():
        assert (GOLDEN / rel).is_file(), "no golden file; run pytest --update-golden"
        assert text == (GOLDEN / rel).read_text(encoding="utf-8"), rel


def test_the_absent_case_is_the_existing_kit_golden(tmp_path: Path) -> None:
    out, gated = build(tmp_path)
    golden = Path(__file__).parent / "golden_kit"
    assert (out / "kit" / "index.html").read_bytes() == (
        golden / "public/kit/index.html"
    ).read_bytes()
    assert (gated / GATED_FILE).read_bytes() == (golden / "gated/kit.html").read_bytes()
    assert TODAY is not None
