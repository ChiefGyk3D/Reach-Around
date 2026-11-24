# SPDX-License-Identifier: Apache-2.0
"""The owner-only measures file (kit v2 slice 7, #632). Synthetic data only."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import reach_around.commands.kit as kit_command
from kitpdfharness import SITE_KEY, TODAY, build_instance, kit_doc, make_config
from reach_around.cli import main
from reach_around.config import SiteConfig
from reach_around.config import kit as kit_module
from reach_around.config.imported_history import ImportedHistory
from reach_around.config.kit import KitConfig
from reach_around.config.numbers import Numbers, NumbersEntry
from reach_around.counts.beacons import IMPORT_NOTE
from reach_around.kit.binding import BINDING_FILE
from reach_around.kit.measures_file import (
    FILE_NAME,
    SCHEMA_VERSION,
    MeasuresFileError,
    build_measures_file,
    check_no_private_values,
    load_measures_file,
    private_scalars,
)
from reach_around.kit.output import OutputError
from test_kit_private_measures import KIT as PRIVATE_KIT
from test_kit_private_measures import SECRET_FOLLOWERS, make_private

CREATED = date(2027, 3, 10)


def write(
    tmp_path: Path, config: SiteConfig, site: Path, gated: Path, **extra: Any
) -> Path:
    return build_measures_file(
        config,
        site_dir=site,
        gated_dir=gated,
        out_dir=tmp_path / "private",
        generated=CREATED,
        **extra,
    )


def instance(
    tmp_path: Path, doc: dict[str, Any] | None = None
) -> tuple[SiteConfig, Path, Path]:
    return build_instance(tmp_path, make_config(doc or kit_doc()))


def published_hits(*roots: Path) -> list[Path]:
    """Every file under the roots whose name is the measures file, or whose text
    mentions its name. Collected loosely, asserted strictly by the callers."""
    found: list[Path] = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.name == FILE_NAME or FILE_NAME.encode() in path.read_bytes():
                found.append(path)
    return found


# --- what the file holds -------------------------------------------------------


def test_the_file_holds_the_resolved_measures_with_trust_and_date(
    tmp_path: Path,
) -> None:
    config, site, gated = instance(tmp_path)
    path = write(tmp_path, config, site, gated)
    assert path == tmp_path / "private" / FILE_NAME
    doc = load_measures_file(path)
    assert doc["schema_version"] == SCHEMA_VERSION == 1
    assert doc["generated"] == "2027-03-10"
    rows = {(m["platform"], m["key"]): m for m in doc["measures"]}
    twitch = rows[("twitch", "followers")]
    assert twitch["value"] == 1200
    assert twitch["unit"] == "follows"
    assert twitch["as_of"] == "2027-03-01"
    assert twitch["trust"]["label"] == "hand-kept"
    assert twitch["trust"]["method"]
    assert rows[("youtube", "followers")]["value"] == 3400


def test_the_file_is_deterministic_and_owner_only(tmp_path: Path) -> None:
    config, site, gated = instance(tmp_path)
    path = write(tmp_path, config, site, gated)
    first = path.read_bytes()
    write(tmp_path, config, site, gated, overwrite=True)
    assert path.read_bytes() == first
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert first.endswith(b"\n")


def test_nothing_but_measures_is_in_the_file(tmp_path: Path) -> None:
    config, site, gated = instance(tmp_path)
    text = write(tmp_path, config, site, gated).read_text(encoding="utf-8")
    assert "Acme Fake Co" not in text  # a partner
    assert "Sponsored video fake" not in text  # a rate
    assert SITE_KEY not in text
    assert "Exampleland" not in text  # demographics


# --- the wall -------------------------------------------------------------------


def test_the_file_never_lands_in_a_published_directory(tmp_path: Path) -> None:
    config, site, gated = instance(tmp_path)
    for inside in (site, site / "kit", gated, gated / "sub"):
        with pytest.raises(OutputError):
            build_measures_file(
                config,
                site_dir=site,
                gated_dir=gated,
                out_dir=inside,
                generated=CREATED,
            )
    # the config's directory is protected too
    with pytest.raises(OutputError):
        write(tmp_path, config, site, gated, protect=[tmp_path / "private"])
    assert not (tmp_path / "private").exists()


def test_the_name_is_in_no_published_artifact(tmp_path: Path) -> None:
    config, site, gated = instance(tmp_path)
    write(tmp_path, config, site, gated)
    assert list(site.rglob("*")) and list(gated.rglob("*"))  # something collected
    assert published_hits(site, gated) == []


def test_the_name_guard_bites(tmp_path: Path) -> None:
    """Negative case: the scan above finds the file by name and by mention."""
    config, site, gated = instance(tmp_path)
    (site / FILE_NAME).write_text("{}", encoding="utf-8")
    (gated / "other.txt").write_text(f"see {FILE_NAME}", encoding="utf-8")
    assert {p.name for p in published_hits(site, gated)} == {FILE_NAME, "other.txt"}


def test_a_binding_with_another_policy_is_refused(tmp_path: Path) -> None:
    config, site, gated = instance(tmp_path)
    record = json.loads((gated / BINDING_FILE).read_text("utf-8"))
    record["policy"] = "0" * 64
    (gated / BINDING_FILE).write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(MeasuresFileError, match="policy"):
        write(tmp_path, config, site, gated)
    assert not (tmp_path / "private" / FILE_NAME).exists()


def test_a_missing_binding_record_is_refused(tmp_path: Path) -> None:
    config, site, gated = instance(tmp_path)
    (gated / BINDING_FILE).unlink()
    with pytest.raises(MeasuresFileError, match="binding record"):
        write(tmp_path, config, site, gated)


def test_no_measures_means_no_file(tmp_path: Path) -> None:
    doc = {"title": "Example Creator media kit", "about": "A fake creator."}
    config, site, gated = instance(tmp_path, doc)
    with pytest.raises(MeasuresFileError, match="no measures"):
        write(tmp_path, config, site, gated)
    assert not (tmp_path / "private").exists()


def test_an_existing_file_is_kept_without_overwrite(tmp_path: Path) -> None:
    config, site, gated = instance(tmp_path)
    path = write(tmp_path, config, site, gated)
    path.write_text("keep", encoding="utf-8")
    with pytest.raises(OutputError, match="overwrite"):
        write(tmp_path, config, site, gated)
    assert path.read_text(encoding="utf-8") == "keep"


# --- private figures ------------------------------------------------------------


def private_doc() -> dict[str, Any]:
    doc = kit_doc()
    doc["engagement"] = PRIVATE_KIT["engagement"]
    doc["analytics"] = PRIVATE_KIT["analytics"]
    return doc


def test_a_private_field_is_not_in_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = private_doc()
    config, site, gated = instance(tmp_path, doc)
    control = write(tmp_path, config, site, gated).read_text(encoding="utf-8")
    assert "5555555" in control and "765432" in control  # shown while not private
    make_private(monkeypatch, "analytics")
    make_private(monkeypatch, "engagement")
    # the pages are rebuilt under the new policy, as a real run would
    config, site, gated = build_instance(tmp_path / "again", make_config(doc))
    out = tmp_path / "again" / "private"
    path = build_measures_file(
        config,
        site_dir=site,
        gated_dir=gated,
        out_dir=out,
        generated=CREATED,
    )
    text = path.read_text(encoding="utf-8")
    for needle in ("5555555", "9162534", "Canary tile label", "765432"):
        assert needle not in text, needle
    assert "1200" in text  # a gated headline number still is


def test_a_stale_page_with_now_private_figures_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, site, gated = instance(tmp_path, private_doc())
    make_private(monkeypatch, "engagement")
    with pytest.raises(MeasuresFileError, match="policy"):
        write(tmp_path, config, site, gated)


def test_a_private_small_figure_is_needled_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last check, through the real inventory: a small private figure (40)
    that reaches a public label with a unit stops the write, naming the field and
    never the value; the same figure without a unit does not."""
    doc = kit_doc()
    doc["analytics"] = [
        {
            "platform": "tiktok",
            "synced_with": "Example Social API",
            "as_of": "2027-03-02",
            "followers": 5555555,
            "followers_30_day_percent": 12.3,
            "stats": [{"label": "Canary tile label", "value": 40}],
        }
    ]
    make_private(monkeypatch, "analytics")
    doc["metrics"][0]["label"] = "Gained 40 subscribers"
    config, site, gated = build_instance(tmp_path / "unit", make_config(doc))
    with pytest.raises(MeasuresFileError, match="kit.analytics") as caught:
        build_measures_file(
            config,
            site_dir=site,
            gated_dir=gated,
            out_dir=tmp_path / "unit" / "private",
            generated=CREATED,
        )
    assert "40" not in str(caught.value)
    assert not (tmp_path / "unit" / "private" / FILE_NAME).exists()
    doc["metrics"][0]["label"] = "Gained 40 platforms"
    config, site, gated = build_instance(tmp_path / "bare", make_config(doc))
    path = build_measures_file(
        config,
        site_dir=site,
        gated_dir=gated,
        out_dir=tmp_path / "bare" / "private",
        generated=CREATED,
    )
    assert path.is_file()


def test_the_turnstile_key_is_a_needle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reach_around.kit import measures_file

    config, site, gated = instance(tmp_path)
    monkeypatch.setattr(
        measures_file,
        "private_scalars",
        lambda kit, numbers, api=None, imported=None: [
            ("turnstile_site_key", "Followers")
        ],
    )
    with pytest.raises(MeasuresFileError, match="turnstile_site_key"):
        write(tmp_path, config, site, gated)


# --- the reader -----------------------------------------------------------------


def good(tmp_path: Path) -> dict[str, Any]:
    config, site, gated = instance(tmp_path)
    doc: dict[str, Any] = json.loads(
        write(tmp_path, config, site, gated).read_text("utf-8")
    )
    return doc


def put(tmp_path: Path, doc: Any, name: str = "x.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_the_reader_refuses_another_version(tmp_path: Path) -> None:
    doc = good(tmp_path)
    doc["schema_version"] = 2
    with pytest.raises(MeasuresFileError, match="schema_version"):
        load_measures_file(put(tmp_path, doc))


def test_the_reader_refuses_an_unknown_key(tmp_path: Path) -> None:
    doc = good(tmp_path)
    doc["measures"][0]["extra"] = 1
    with pytest.raises(MeasuresFileError, match="measures"):
        load_measures_file(put(tmp_path, doc))
    doc = good(tmp_path / "b")
    doc["partners"] = ["x"]
    with pytest.raises(MeasuresFileError, match="partners"):
        load_measures_file(put(tmp_path, doc, "y.json"))


def test_the_reader_refuses_a_private_row(tmp_path: Path) -> None:
    doc = good(tmp_path)
    doc["measures"][0]["visibility"] = "private"
    with pytest.raises(MeasuresFileError, match="visibility"):
        load_measures_file(put(tmp_path, doc))


def test_the_reader_refuses_a_link_and_bad_json(tmp_path: Path) -> None:
    doc = good(tmp_path)
    real = put(tmp_path, doc, "real.json")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(MeasuresFileError, match="link"):
        load_measures_file(link)
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(MeasuresFileError, match="JSON"):
        load_measures_file(bad)
    big = tmp_path / "big.json"
    big.write_bytes(b" " * (5 * 1024 * 1024))
    with pytest.raises(MeasuresFileError, match="large"):
        load_measures_file(big)


def test_the_reader_refuses_a_duplicate_key(tmp_path: Path) -> None:
    path = tmp_path / "dup.json"
    path.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")
    with pytest.raises(MeasuresFileError, match="duplicate"):
        load_measures_file(path)


# --- the command ----------------------------------------------------------------


def run_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config: SiteConfig,
    site: Path,
    gated: Path,
    out_dir: Path,
) -> int:
    cfg = tmp_path / "cfg" / "site.yaml"
    cfg.parent.mkdir()
    cfg.write_text("x", encoding="utf-8")
    monkeypatch.setattr(kit_command, "load_site_config", lambda path: config)
    return main(
        [
            "kit",
            "measures",
            "--config",
            str(cfg),
            "--site-out",
            str(site),
            "--gated-out",
            str(gated),
            "--out-dir",
            str(out_dir),
            "--date",
            "2027-03-10",
        ]
    )


def test_the_command_prints_only_the_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, site, gated = instance(tmp_path)
    out_dir = tmp_path / "private"
    code = run_command(monkeypatch, tmp_path, config, site, gated, out_dir)
    out = capsys.readouterr()
    assert code == 0
    assert out.out.strip() == str(out_dir / FILE_NAME)
    assert "private" in out.err
    for text in (out.out, out.err):
        assert "1200" not in text and "Followers" not in text
    assert os.path.isfile(out.out.strip())


def test_the_command_refuses_a_published_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, site, gated = instance(tmp_path)
    code = run_command(monkeypatch, tmp_path, config, site, gated, site / "kit")
    assert code == 1
    assert "error:" in capsys.readouterr().err
    assert published_hits(site, gated) == []


# --- the build binding ---------------------------------------------------------


def test_the_binding_is_in_the_gated_directory_only(tmp_path: Path) -> None:
    config, site, gated = instance(tmp_path)
    assert (gated / BINDING_FILE).is_file()
    assert [p for p in site.rglob(BINDING_FILE.name)] == []
    assert published_hits(site) == []
    assert BINDING_FILE.name.encode() not in b"".join(
        p.read_bytes() for p in site.rglob("*") if p.is_file()
    )


def test_changed_inputs_with_the_old_pages_are_refused(tmp_path: Path) -> None:
    """The pages show 1200; a config that now says 999999 must not produce a file
    that disagrees with them."""
    config, site, gated = instance(tmp_path)
    doc = kit_doc()
    doc["metrics"][0]["value"] = 999999
    with pytest.raises(MeasuresFileError, match="measures"):
        write(tmp_path, make_config(doc), site, gated)
    assert not (tmp_path / "private").exists()


def test_a_site_and_a_gated_page_from_two_builds_are_refused(tmp_path: Path) -> None:
    config, site, _ = instance(tmp_path / "a")
    other = kit_doc()
    other["title"] = "Another media kit"
    _, _, gated = build_instance(tmp_path / "b", make_config(other))
    with pytest.raises(MeasuresFileError, match="public page|gated page|measures"):
        write(tmp_path, config, site, gated)


def test_a_changed_page_is_refused(tmp_path: Path) -> None:
    config, site, gated = instance(tmp_path)
    page = site / "kit" / "index.html"
    page.write_bytes(page.read_bytes() + b"<!-- x -->")
    with pytest.raises(MeasuresFileError, match="public page"):
        write(tmp_path, config, site, gated)
    page = gated / "kit.html"
    config, site, gated = instance(tmp_path / "again")
    (gated / "kit.html").write_bytes((gated / "kit.html").read_bytes() + b" ")
    with pytest.raises(MeasuresFileError, match="gated page"):
        write(tmp_path / "again", config, site, gated)


def test_the_file_records_the_binding_and_the_reader_checks_it(
    tmp_path: Path,
) -> None:
    config, site, gated = instance(tmp_path)
    path = write(tmp_path, config, site, gated)
    recorded = json.loads((gated / BINDING_FILE).read_text("utf-8"))
    doc = load_measures_file(path, expected_binding=recorded)
    assert doc["binding"] == recorded
    other = {**recorded, "measures": "1" * 64}
    with pytest.raises(MeasuresFileError, match="expected"):
        load_measures_file(path, expected_binding=other)


# --- values compared decoded, not as JSON text --------------------------------


def test_a_private_label_with_odd_characters_is_found_in_a_public_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The comparison is on decoded values: a quote and a non-ASCII letter are
    escaped in the JSON text, so a text search would miss them."""
    odd = 'Tïle "x" label'
    doc = kit_doc()
    doc["analytics"] = [
        {
            "platform": "tiktok",
            "synced_with": "Example Social API",
            "as_of": "2027-03-02",
            "followers": 5555555,
            "followers_30_day_percent": 12.3,
            "stats": [{"label": odd, "value": 4242}],
        }
    ]
    doc["metrics"][0]["label"] = f"About {odd} today"
    make_private(monkeypatch, "analytics")
    config, site, gated = build_instance(tmp_path, make_config(doc))
    with pytest.raises(MeasuresFileError, match="kit.analytics") as caught:
        write(tmp_path, config, site, gated)
    assert "Tïle" not in str(caught.value)
    assert not (tmp_path / "private" / FILE_NAME).exists()


def test_the_compare_ignores_json_encoding() -> None:
    document = {"measures": [{"note": 'Sëcret "rate"\ttab', "value": 1}]}
    for private in ('Sëcret "rate"\ttab', "SËCRET   “rate”"[:0] + 'sëcret "rate" tab'):
        with pytest.raises(MeasuresFileError, match="kit.x.note"):
            check_no_private_values(document, [("kit.x.note", private)])
    check_no_private_values(document, [("kit.x.note", "another text")])


def test_a_private_number_is_found_as_a_value_and_inside_a_text() -> None:
    inventory = [("kit.x.value", 9162534)]
    for document in (
        {"value": 9162534},
        {"note": "reached 9162534 once"},
    ):
        with pytest.raises(MeasuresFileError, match="kit.x.value"):
            check_no_private_values(document, inventory)
    check_no_private_values({"note": "91625345 and 59162534"}, inventory)


def test_the_inventory_holds_each_free_text_and_number_of_a_private_measure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kit = KitConfig.model_validate(PRIVATE_KIT)
    assert private_scalars(kit, None) == []
    make_private(monkeypatch, "analytics")
    inventory = dict(private_scalars(kit, None))
    assert 9162534 in inventory.values()
    assert 5555555 in inventory.values()
    assert "Canary tile label" in inventory.values()
    # names, dates and registry labels say nothing and are left out
    assert not {"Followers", "tiktok", "2027-03-02"} & set(inventory.values())


def test_a_private_free_text_is_found_in_a_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kit = KitConfig.model_validate(PRIVATE_KIT)
    make_private(monkeypatch, "analytics")
    document = {"measures": [{"note": "see the Canary tile label here"}]}
    with pytest.raises(MeasuresFileError, match="kit.analytics"):
        check_no_private_values(document, private_scalars(kit, None))


# --- the reader checks every field ----------------------------------------------


def mutate(tmp_path: Path, change: Any) -> Path:
    doc = good(tmp_path)
    change(doc)
    return put(tmp_path, doc, "m.json")


BAD = {
    "version is a bool": lambda d: d.update(schema_version=True),
    "version is a float": lambda d: d.update(schema_version=1.0),
    "notice is a list": lambda d: d.update(notice=[]),
    "generated is an object": lambda d: d.update(generated={"not": "date"}),
    "generated is not a day": lambda d: d.update(generated="2027-13-40"),
    "generated has a time": lambda d: d.update(generated="2027-03-10T00:00:00"),
    "policy is null": lambda d: d.update(policy=None),
    "policy is not a digest": lambda d: d.update(policy="abc"),
    "binding is null": lambda d: d.update(binding=None),
    "binding differs from policy": lambda d: d["binding"].update(policy="1" * 64),
    "measures is empty": lambda d: d.update(measures=[]),
    "measures is an object": lambda d: d.update(measures={}),
    "too many measures": lambda d: d.update(measures=d["measures"][:1] * 5001),
    "value is negative": lambda d: d["measures"][0].update(value=-1),
    "value is a float": lambda d: d["measures"][0].update(value=1.5),
    "value is a bool": lambda d: d["measures"][0].update(value=True),
    "value is huge": lambda d: d["measures"][0].update(value=10**16),
    "value is a string": lambda d: d["measures"][0].update(value="12"),
    "platform is a list": lambda d: d["measures"][0].update(platform=["x"]),
    "key is not a slug": lambda d: d["measures"][0].update(key="A b"),
    "label is empty": lambda d: d["measures"][0].update(label=""),
    "label is an object": lambda d: d["measures"][0].update(label={}),
    "unit is unknown": lambda d: d["measures"][0].update(unit="parsecs"),
    "unit is a list": lambda d: d["measures"][0].update(unit=[]),
    "as_of is invalid": lambda d: d["measures"][0].update(as_of="2027-02-30"),
    "as_of is a number": lambda d: d["measures"][0].update(as_of=20270301),
    "period is a day": lambda d: d["measures"][0].update(period="2027-03-01"),
    "period has three": lambda d: d["measures"][0].update(period=["2027-03-01"] * 3),
    "period is reversed": lambda d: d["measures"][0].update(
        period=["2027-03-05", "2027-03-01"]
    ),
    "trust label is unknown": lambda d: d["measures"][0]["trust"].update(
        label="verified"
    ),
    "trust method is an object": lambda d: d["measures"][0]["trust"].update(
        method={"a": 1}
    ),
    "in_summary is an int": lambda d: d["measures"][0].update(in_summary=1),
    "note is a list": lambda d: d["measures"][0].update(note=[]),
    "note is huge": lambda d: d["measures"][0].update(note="x" * 4001),
    "visibility is private": lambda d: d["measures"][0].update(visibility="private"),
}


@pytest.mark.parametrize("name", sorted(BAD))
def test_the_reader_refuses_a_bad_field(tmp_path: Path, name: str) -> None:
    path = mutate(tmp_path, BAD[name])
    with pytest.raises(MeasuresFileError):
        load_measures_file(path)


def test_the_reader_refuses_nan_and_infinity(tmp_path: Path) -> None:
    for number, constant in enumerate(("NaN", "Infinity", "-Infinity")):
        path = tmp_path / "c.json"
        text = json.dumps(good(tmp_path / str(number)))
        path.write_text(text.replace('"value": 1200', f'"value": {constant}'))
        assert constant in path.read_text(encoding="utf-8")
        with pytest.raises(MeasuresFileError, match="not a number"):
            load_measures_file(path)


def test_the_reader_refuses_deep_nesting(tmp_path: Path) -> None:
    doc = good(tmp_path)
    nest: Any = "x"
    for _ in range(100000):
        nest = [nest]
    path = tmp_path / "deep.json"
    path.write_text(
        json.dumps(doc)[:-1] + ', "x": ' + "[" * 100000 + "]" * 100000 + "}"
    )
    with pytest.raises(MeasuresFileError, match="deeply"):
        load_measures_file(path)


def test_the_reader_accepts_what_the_writer_wrote(tmp_path: Path) -> None:
    assert load_measures_file(put(tmp_path, good(tmp_path)))["measures"]


# --- the inventory covers decimals, formats and every source ----------------------

FORMS = {
    "plain": "reached {v}",
    "thousands comma": "reached {c}",
    "thousands space": "reached {s}",
    "with a percent sign": "reached {v}%",
}


def written(value: int | str) -> dict[str, str]:
    text = str(value)
    whole, _, frac = text.partition(".")
    grouped = f"{int(whole):,}" + (f".{frac}" if frac else "")
    return {"v": text, "c": grouped, "s": grouped.replace(",", " ")}


def a_note(text: str) -> dict[str, Any]:
    return {"measures": [{"note": text}]}


@pytest.mark.parametrize("form", sorted(FORMS))
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("metrics", SECRET_FOLLOWERS),
        ("analytics", 5555555),
        ("analytics", 9162534),
        ("engagement", 7654321),
    ],
)
def test_a_private_whole_number_is_found_in_every_written_form(
    field: str, value: int, form: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit = KitConfig.model_validate(PRIVATE_KIT)
    text = FORMS[form].format(**written(value))
    assert private_scalars(kit, None) == []  # control: nothing private yet
    make_private(monkeypatch, field)
    with pytest.raises(MeasuresFileError, match="private field kit"):
        check_no_private_values(a_note(text), private_scalars(kit, None))


@pytest.mark.parametrize("form", sorted(FORMS))
@pytest.mark.parametrize("written_as", ["12.3", "12.30"])
def test_a_private_decimal_is_found_however_it_is_written(
    form: str, written_as: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit = KitConfig.model_validate(PRIVATE_KIT)
    make_private(monkeypatch, "analytics")
    inventory = private_scalars(kit, None)
    assert ("kit.analytics.followers_30_day_percent", Decimal("12.3")) in inventory
    text = FORMS[form].format(v=written_as, c=written_as, s=written_as)
    with pytest.raises(MeasuresFileError, match="followers_30_day_percent"):
        check_no_private_values(a_note(text), inventory)


@pytest.mark.parametrize("form", sorted(FORMS))
@pytest.mark.parametrize("sign", ["-", "−"])
@pytest.mark.parametrize(
    ("private", "written_as"),
    [
        (Decimal("-12.3"), "12.30"),
        (Decimal("-12.3"), "12.3"),
        (-1234567, "1234567"),
    ],
)
def test_a_private_negative_number_is_found_when_reformatted(
    private: Any, written_as: str, sign: str, form: str
) -> None:
    forms = written(written_as)
    text = FORMS[form].format(**{k: f"{sign}{v}" for k, v in forms.items()})
    with pytest.raises(MeasuresFileError, match="kit.x.n"):
        check_no_private_values(a_note(text), [("kit.x.n", private)])
    # the same text without a sign is the same magnitude: found as well
    unsigned = FORMS[form].format(**forms)
    with pytest.raises(MeasuresFileError, match="kit.x.n"):
        check_no_private_values(a_note(unsigned), [("kit.x.n", private)])


def short_kit(value: int) -> KitConfig:
    return KitConfig.model_validate(
        {
            "title": "Example Creator media kit",
            "about": "A fake creator used in tests.",
            "analytics": [
                {
                    "platform": "tiktok",
                    "synced_with": "Example Social API",
                    "as_of": "2027-03-02",
                    "followers": 5555555,
                    "followers_30_day_percent": 12.3,
                    "stats": [{"label": "Canary tile label", "value": value}],
                }
            ],
        }
    )


@pytest.mark.parametrize("value", [3, 40])
def test_a_short_private_whole_number_is_in_the_real_inventory(
    value: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit = short_kit(value)
    assert value not in [v for _p, v in private_scalars(kit, None)]  # not private yet
    make_private(monkeypatch, "analytics")
    assert value in [v for _p, v in private_scalars(kit, None)]


@pytest.mark.parametrize(
    ("value", "text", "found"),
    [
        (3, "up 3% on last month", True),
        (3, "3 % more", True),
        (3, "paid $3 each", True),
        (3, "about 3 USD", True),
        (3, "3 followers", True),
        (3, "3 platforms", False),
        (3, "3 streams a week", False),
        (3, "version 3", False),
        (3, "a 3-part series", False),
        (3, "13% and 30%", False),
        (40, "40 subscribers", True),
        (40, "40 Subscribers gained", True),
        (40, "40 views", True),
        (40, "４０ hours", True),
        (40, "€40", True),
        (40, "40 platforms", False),
        (40, "40 days", False),
        (40, "40", False),
        (40, "140 subscribers", False),
        (40, "40.5 subscribers", False),
    ],
)
def test_a_short_private_number_is_found_only_with_a_unit(
    value: int,
    text: str,
    found: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Driven through the real inventory, not a stand-in."""
    kit = short_kit(value)
    make_private(monkeypatch, "analytics")
    inventory = private_scalars(kit, None)
    if found:
        with pytest.raises(MeasuresFileError, match="kit.analytics"):
            check_no_private_values(a_note(text), inventory)
    else:
        check_no_private_values(a_note(text), inventory)


def test_a_short_private_number_as_a_bare_output_value_is_not_a_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kit = short_kit(40)
    make_private(monkeypatch, "analytics")
    check_no_private_values({"measures": [{"value": 40}]}, private_scalars(kit, None))


WRITTEN_1234567 = {
    "zero-width characters": "1​234‍567",
    "zero-width inside a decimal": "1234⁠567",
    "no-break space grouping": "1 234 567",
    "thin space grouping": "1 234 567",
    "narrow no-break space grouping": "1 234 567",
    "figure space grouping": "1 234 567",
    "ideographic space grouping": "1　234　567",
    "newline grouping": "1\n234\n567",
    "apostrophe grouping": "1'234'567",
    "typographic apostrophe grouping": "1’234’567",
    "fullwidth digits": "１２３４５６７",
    "fullwidth digits and commas": "１，２３４，５６７",
    "a space inside the number": "1234 567",
    "a comma inside the number": "1234,567",
}


@pytest.mark.parametrize("name", sorted(WRITTEN_1234567))
def test_a_private_number_is_found_in_unusual_written_forms(name: str) -> None:
    text = f"we reached {WRITTEN_1234567[name]} in total"
    with pytest.raises(MeasuresFileError, match="kit.x.n"):
        check_no_private_values(a_note(text), [("kit.x.n", 1234567)])


@pytest.mark.parametrize(
    "text",
    [
        "12​.30",
        "12.3​0",
        "１２．３０",
        "12.30％",
    ],
)
def test_a_private_decimal_is_found_in_unusual_written_forms(text: str) -> None:
    with pytest.raises(MeasuresFileError, match="kit.x.n"):
        check_no_private_values(a_note(text), [("kit.x.n", Decimal("12.3"))])


def test_unrelated_numbers_are_not_a_hit() -> None:
    inventory: list[tuple[str, Any]] = [
        ("kit.x.whole", 9162534),
        ("kit.x.decimal", Decimal("12.3")),
    ]
    for text in (
        "ids 91625345 and 59162534",
        "split 9,162,53 and 1,916,253,4",
        "12.35 and 112.3 and 12.4%",
        "founded in 2027, 3 streams, 40 days",
    ):
        check_no_private_values(a_note(text), inventory)
    check_no_private_values({"measures": [{"value": 9162535}]}, inventory)
    with pytest.raises(MeasuresFileError, match="kit.x.whole"):
        check_no_private_values({"measures": [{"value": 9162534}]}, inventory)


def test_a_private_number_from_the_numbers_file_is_in_the_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stamp = datetime(2027, 3, 9, 12, 0, tzinfo=UTC)
    numbers = Numbers(entries={"twitch": NumbersEntry(count=9876543, as_of=stamp)})
    kit = KitConfig.model_validate(
        {
            "title": "Example Creator media kit",
            "about": "A fake creator used in tests.",
            "metrics": [{"platform": "twitch", "label": "Reach", "source": "counts"}],
        }
    )
    assert private_scalars(kit, numbers) == []
    make_private(monkeypatch, "metrics")
    inventory = private_scalars(kit, numbers)
    assert 9876543 in [value for _path, value in inventory]
    with pytest.raises(MeasuresFileError):
        check_no_private_values(a_note("about 9,876,543 of them"), inventory)


def imported_history() -> ImportedHistory:
    return ImportedHistory.model_validate(
        {
            "version": 1,
            "source": "imported",
            "note": IMPORT_NOTE,
            "platform": "link-page",
            "periods": [
                {
                    "start": "2026-01-01",
                    "end": "2026-01-31",
                    "days": 31,
                    "views": 1234567,
                    "clicks": 7654321,
                }
            ],
        }
    )


def test_imported_values_enter_the_inventory_when_the_block_is_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kit = KitConfig.model_validate(PRIVATE_KIT)
    history = imported_history()
    shown = [v for _path, v in private_scalars(kit, None, None, history)]
    assert 1234567 not in shown  # gated, not private: the page may show it
    monkeypatch.setattr(kit_module, "imported_level", lambda kit: "private")
    assert 1234567 not in [v for _p, v in private_scalars(kit, None)]  # none given
    inventory = private_scalars(kit, None, None, history)
    assert {1234567, 7654321} <= {v for _path, v in inventory}
    assert any(path.startswith("kit.imported_history") for path, _v in inventory)
    with pytest.raises(MeasuresFileError, match="kit.imported_history"):
        check_no_private_values(a_note("1,234,567 views"), inventory)


# --- the docs -------------------------------------------------------------------


def test_the_schema_is_documented() -> None:
    docs = (Path(__file__).parent.parent / "docs" / "kit.md").read_text("utf-8")
    assert FILE_NAME in docs
    assert f"schema_version` is {SCHEMA_VERSION}" in docs
    for field in ("generated", "policy", "measures", "trust", "as_of", "period"):
        assert f"`{field}`" in docs, field


_ = (TODAY, SECRET_FOLLOWERS)
