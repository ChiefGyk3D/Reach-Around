# SPDX-License-Identifier: Apache-2.0
"""The shared import guard (#784): person-level fields are refused, never echoed."""

from datetime import date
from pathlib import Path

import pytest

from reach_around.config.handkept import HandKeptError
from reach_around.config.import_guard import (
    NOT_ALLOWED,
    PERSON_FIELD,
    ImportGuardError,
    check_cohort,
    check_names,
    check_value,
    is_person_name,
)
from reach_around.config.youtube_export import import_youtube
from reach_around.counts.beacons import BeaconsImportError, parse_beacons_csv

TODAY = date(2027, 3, 1)
YT_HEAD = "Date,Views,Watch time (hours)\n"
BEACONS_HEAD = "Section,Item,Detail,Views,Clicks,CTR (%)\n"
CANARY = "canary9876"
ALLOWED = frozenset({"Date", "Views"})


@pytest.mark.parametrize(
    "name",
    [
        "Username",
        "Display name",
        "Email",
        "Commenter",
        "Comment author",
        "User ID",
        "Channel ID",
        "IP address",
        "ip",
        "Latitude",
        "City",
        "Postal code",
        "Name",
    ],
)
def test_person_names_are_recognized(name: str) -> None:
    assert is_person_name(name)
    with pytest.raises(ImportGuardError, match=PERSON_FIELD):
        check_names(["Date", name], ALLOWED | {name})


@pytest.mark.parametrize("name", ["Date", "Views", "Watch time (hours)", "Item"])
def test_aggregate_names_are_not_person_names(name: str) -> None:
    assert not is_person_name(name)


def test_a_name_outside_the_allowlist_is_refused() -> None:
    with pytest.raises(ImportGuardError, match=NOT_ALLOWED):
        check_names(["Date", "Subscribers"], ALLOWED)
    check_names(["Date", "Views"], ALLOWED)


@pytest.mark.parametrize(
    "value",
    [
        "a.b@example.com",
        "@somehandle",
        "hi @somehandle",
        "203.0.113.7",
        "2001:db8:0:0:0:0:0:1",
        "UC" + "a" * 22,
    ],
)
def test_person_shaped_values_are_refused(value: str) -> None:
    with pytest.raises(ImportGuardError):
        check_value(value)


@pytest.mark.parametrize("value", ["", "2027-02-01", "12", "1.5", "zz", "mobile"])
def test_plain_values_pass(value: str) -> None:
    check_value(value)


def test_cohort_floor() -> None:
    check_cohort(0)
    check_cohort(5)
    for small in (1, 4):
        with pytest.raises(ImportGuardError):
            check_cohort(small)


def _yt(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "export.csv"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "header",
    [
        f"Date,Views,Watch time (hours),Username{CANARY}\n",
        "Date,Views,Watch time (hours),Email\n",
        "Date,Views,Watch time (hours),Subscribers\n",
        "Date,Views,Watch time (hours),Commenter\n",
        "Date,Views,Watch time (hours),IP address\n",
    ],
)
def test_youtube_refuses_extra_columns(tmp_path: Path, header: str) -> None:
    cells = header.count(",") + 1
    row = "2027-02-01,5,1" + ",x" * (cells - 3) + "\n"
    with pytest.raises(HandKeptError) as err:
        import_youtube(_yt(tmp_path, header + row), today=TODAY)
    assert CANARY not in str(err.value)


def test_youtube_person_column_message_is_fixed(tmp_path: Path) -> None:
    text = f"Date,Views,Watch time (hours),Username{CANARY}\n2027-02-01,5,1,{CANARY}\n"
    with pytest.raises(HandKeptError) as err:
        import_youtube(_yt(tmp_path, text), today=TODAY)
    assert str(err.value) == PERSON_FIELD


@pytest.mark.parametrize("cell", [f"{CANARY}@example.com", "203.0.113.7", f"@{CANARY}"])
def test_youtube_refuses_a_person_shaped_cell_without_echo(
    tmp_path: Path, cell: str
) -> None:
    text = YT_HEAD + f"2027-02-01,5,1\n{cell},5,1\n"
    with pytest.raises(HandKeptError) as err:
        import_youtube(_yt(tmp_path, text), today=TODAY)
    assert CANARY not in str(err.value)
    assert "203.0" not in str(err.value)


def test_youtube_still_accepts_the_allowed_shape(tmp_path: Path) -> None:
    got = import_youtube(
        _yt(tmp_path, YT_HEAD + "Total,9,2\n2027-02-01,5,1\n"), today=TODAY
    )
    assert {e.key: e.value for e in got} == {"views": 5, "watch_hours": 1}


@pytest.mark.parametrize(
    "header",
    [
        f"Section,Item,Detail,Views,Clicks,CTR (%),Username{CANARY}\n",
        "Section,Item,Detail,Views,Clicks,CTR (%),Email\n",
        "Section,Item,Detail,Views,Clicks,CTR (%),Followers\n",
    ],
)
def test_beacons_refuses_extra_columns(header: str) -> None:
    body = "Total,Link in Bio,,1,1,1.0,x\nDaily,2026-10-01,,1,1,,x\n"
    with pytest.raises(BeaconsImportError) as err:
        parse_beacons_csv((header + body).encode())
    assert CANARY not in str(err.value)


def _beacons(*rows: str) -> bytes:
    base = ["Total,Link in Bio,,40,9,24.3", "Daily,2026-10-01,,12,3,"]
    return (BEACONS_HEAD + "\n".join([*base, *rows]) + "\n").encode()


def test_beacons_refuses_a_small_country_row() -> None:
    with pytest.raises(BeaconsImportError, match="privacy floor"):
        parse_beacons_csv(_beacons("Countries,zz,,3,0,1.0"))
    parse_beacons_csv(_beacons("Countries,zz,,17,5,1.0"))
    parse_beacons_csv(_beacons("Countries,zz,,0,0,0"))
    parse_beacons_csv(_beacons("Devices,mobile,,3,1,1.0"))


@pytest.mark.parametrize("item", [f"{CANARY}@example.com", "203.0.113.7"])
def test_beacons_refuses_a_person_shaped_cell_without_echo(item: str) -> None:
    with pytest.raises(BeaconsImportError) as err:
        parse_beacons_csv(_beacons(f"Sources,{item},,6,6,1.0"))
    assert CANARY not in str(err.value)
    assert "203.0" not in str(err.value)
