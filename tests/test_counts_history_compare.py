# SPDX-License-Identifier: Apache-2.0
"""The history compare (issue #619): append-only, or a reviewed tombstone-only
rewrite. Every hostile fixture below must be refused; the one valid rewrite is
made by the real writer so the compare and the writer cannot drift apart."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from reach_around.cli import main
from reach_around.counts.history import (
    GENESIS,
    HistoryError,
    Record,
    append_records,
    plan_redaction,
    read_history,
    redact_records,
    rewrite_metadata,
)
from reach_around.counts.history_compare import (
    CompareResult,
    compare_history,
    read_regular,
)

NOW = datetime(2026, 12, 31, 12, 0, 0, tzinfo=UTC)
OLD_TOMB_AT = datetime(2026, 11, 19, 12, 0, 0, tzinfo=UTC)
REDACT_AT = datetime(2026, 11, 25, 12, 0, 0, tzinfo=UTC)
PR_DAY = "2026-11-25"
SECRET = 424242424  # nine digits: must never reach a tombstone or the manifest


def rec(**over: Any) -> Record:
    fields: dict[str, Any] = {
        "platform": "youtube",
        "metric": "subscribers",
        "value": 10,
        "as_of_utc": "2026-10-01T09:00:00Z",
        "source": "api",
        "run_id": "run-1",
    }
    fields.update(over)
    return Record(**fields)


def day(n: int) -> str:
    return f"2026-10-{n:02d}T09:00:00Z"


def build(path: Path) -> None:
    """30 live records, one correction far from its record, one old tombstone."""

    def add(*records: Record) -> None:
        append_records(path, list(records), now=NOW)

    add(rec(as_of_utc=day(1), value=SECRET))
    for n in range(2, 6):
        add(rec(as_of_utc=day(n), value=50000 + n))
    add(
        rec(
            as_of_utc=day(1),
            value=SECRET + 1,
            type="correction",
            note="fixed",
        )
    )
    for n in range(6, 21):
        add(rec(as_of_utc=day(n), value=50000 + n))
    add(rec(as_of_utc=day(10), value=51010, type="correction", note="later fix"))
    add(rec(as_of_utc=day(3), source="hand_kept", value=77))
    for n in range(1, 11):
        add(rec(platform="discord", metric="members", as_of_utc=day(n), value=900 + n))
    redact_records(
        path,
        plan_redaction(
            read_history(path), platform="discord", source="api", before="2026-10-02"
        ),
        reason="platform_request",
        trigger_date="2026-10-20",
        now=OLD_TOMB_AT,
        confirm=True,
    )


def objs(data: bytes) -> list[dict[str, Any]]:
    return [json.loads(x) for x in data.splitlines()]


def chain(items: list[dict[str, Any]], sep: tuple[str, str] = (",", ":")) -> bytes:
    """A correctly chained file of ``items`` (any fields, any separator)."""
    prev = GENESIS
    out = []
    for item in items:
        line = json.dumps(
            {**item, "prev": prev}, sort_keys=True, separators=sep
        ).encode()
        out.append(line)
        prev = hashlib.sha256(line).hexdigest()
    return b"\n".join(out) + b"\n"


@dataclass
class Scenario:
    old: bytes
    new: bytes
    manifest: bytes
    name: str
    numbers_old: bytes
    numbers_new: bytes

    def check(self, **over: Any) -> CompareResult:
        args: dict[str, Any] = {
            "allow_tombstones": True,
            "manifest": self.manifest,
            "manifest_name": self.name,
            "pr_day": PR_DAY,
            "numbers_old": self.numbers_old,
            "numbers_new": self.numbers_new,
        }
        args.update(over)
        old = args.pop("old", self.old)
        new = args.pop("new", self.new)
        return compare_history(old, new, **args)

    def refused(self, **over: Any) -> None:
        with pytest.raises(HistoryError):
            self.check(**over)


NUMBERS_OLD = {
    "version": 1,
    "platforms": {
        "youtube": {"count": SECRET, "as_of": "2026-10-01T09:00:00Z", "source": "api"},
        "discord": {"count": 905, "as_of": "2026-10-05T09:00:00Z", "source": "api"},
    },
}
NUMBERS_NEW = {
    "version": 1,
    "platforms": {
        "discord": {"count": 905, "as_of": "2026-10-05T09:00:00Z", "source": "api"},
    },
}


def numbers(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, indent=2, sort_keys=True).encode() + b"\n"


def scenario(tmp_path: Path, **redact_over: Any) -> Scenario:
    path = tmp_path / "h.jsonl"
    build(path)
    old = path.read_bytes()
    plan = plan_redaction(
        read_history(path), platform="youtube", source="api", before="2026-10-03"
    )
    redact_args: dict[str, Any] = {
        "reason": "platform_expiry",
        "trigger_date": "2026-10-20",
        "now": REDACT_AT,
        "confirm": True,
        "derived": True,
    }
    redact_args.update(redact_over)
    redact_records(path, plan, **redact_args)
    run_id, _, text = rewrite_metadata(
        [r.key for r in plan],
        redact_args["reason"],
        redact_args["trigger_date"],
        "2026-11-25",
    )
    return Scenario(
        old,
        path.read_bytes(),
        text.encode("ascii"),
        f"{run_id}.manifest",
        numbers(NUMBERS_OLD),
        numbers(NUMBERS_NEW),
    )


@pytest.fixture
def s(tmp_path: Path) -> Scenario:
    return scenario(tmp_path)


def rewrite_with(s: Scenario, edit: Any) -> bytes:
    """The new file after ``edit(list_of_objects)``, correctly re-chained."""
    items = objs(s.new)
    edit(items)
    return chain(items)


def first_kept(items: list[dict[str, Any]]) -> int:
    return next(i for i, o in enumerate(items) if o.get("type") != "tombstone")


# --- the valid paths -------------------------------------------------------


def test_the_real_writers_rewrite_is_accepted_and_the_file_verifies(
    s: Scenario, tmp_path: Path
) -> None:
    result = s.check()
    assert result == CompareResult("rewrite", 2, 1, s.name)
    path = tmp_path / "new.jsonl"
    path.write_bytes(s.new)
    read_history(path)  # the compare and the engine agree


def test_the_tombstones_hold_no_value_in_any_field(s: Scenario) -> None:
    for line in s.new.splitlines()[:2]:
        assert str(SECRET).encode() not in line
    assert str(SECRET).encode() not in s.manifest
    assert str(SECRET).encode() not in s.name.encode()


def test_identical_files_and_a_pure_append_are_append_only(s: Scenario) -> None:
    assert s.check(old=s.old, new=s.old).mode == "append"
    items = objs(s.old)
    items.append(
        {
            "platform": "youtube",
            "metric": "subscribers",
            "value": 60000,
            "as_of_utc": "2026-10-25T09:00:00Z",
            "source": "api",
            "run_id": "run-2",
        }
    )
    assert s.check(old=s.old, new=chain(items), allow_tombstones=False).mode == "append"


def test_a_legacy_noncanonical_prefix_survives_only_in_append_mode(s: Scenario) -> None:
    legacy = chain(objs(s.old), sep=(", ", ": "))
    appended = chain(
        [
            *objs(legacy),
            {
                "platform": "youtube",
                "metric": "subscribers",
                "value": 60000,
                "as_of_utc": "2026-10-25T09:00:00Z",
                "source": "api",
                "run_id": "run-2",
            },
        ],
        sep=(", ", ": "),
    )
    # The appended line itself is noncanonical here, so it is refused ...
    s.refused(old=legacy, new=appended, allow_tombstones=False)
    # ... while a canonical append after the legacy prefix is accepted.
    canonical_tail = json.dumps(
        {
            "platform": "youtube",
            "metric": "subscribers",
            "value": 60000,
            "as_of_utc": "2026-10-25T09:00:00Z",
            "source": "api",
            "run_id": "run-2",
            "prev": hashlib.sha256(legacy.splitlines()[-1]).hexdigest(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert (
        s.check(
            old=legacy, new=legacy + canonical_tail + b"\n", allow_tombstones=False
        ).mode
        == "append"
    )


def test_a_rewrite_that_also_clears_the_published_number_is_accepted(
    s: Scenario,
) -> None:
    # The latest row of a platform and its numbers.json entry go together.
    assert s.check().mode == "rewrite"


def test_a_same_count_entry_with_another_day_stays_and_passes(s: Scenario) -> None:
    # The old row was 2026-10-01; the published entry is the fact for 2026-10-09.
    kept = json.loads(s.numbers_old)
    kept["platforms"]["youtube"]["as_of"] = "2026-10-09T09:00:00Z"
    assert (
        s.check(numbers_old=numbers(kept), numbers_new=numbers(kept)).mode == "rewrite"
    )
    # And it cannot be removed: it does not match a replaced record.
    s.refused(numbers_old=numbers(kept), numbers_new=s.numbers_new)


# --- the label gate: no permission, no rewrite -----------------------------


def test_a_rewrite_without_the_permission_is_refused(s: Scenario) -> None:
    s.refused(allow_tombstones=False)


# --- hostile rewrites ------------------------------------------------------


def test_a_kept_value_changed_with_the_chain_relinked_is_refused(s: Scenario) -> None:
    def edit(items: list[dict[str, Any]]) -> None:
        items[first_kept(items)]["value"] += 1

    s.refused(new=rewrite_with(s, edit))


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("as_of_utc", "2026-09-30T09:00:00Z"),
        ("source", "hand_kept"),
        ("api_provider", "youtube_analytics"),
        ("run_id", "run-changed"),
        ("note", "an added note"),
    ],
)
def test_a_kept_line_with_another_key_or_field_is_refused(
    s: Scenario, field_name: str, value: str
) -> None:
    def edit(items: list[dict[str, Any]]) -> None:
        items[first_kept(items)][field_name] = value

    s.refused(new=rewrite_with(s, edit))


def test_two_kept_lines_swapped_are_refused(s: Scenario) -> None:
    def edit(items: list[dict[str, Any]]) -> None:
        i = first_kept(items)
        items[i], items[i + 1] = items[i + 1], items[i]

    s.refused(new=rewrite_with(s, edit))


def test_a_deleted_kept_line_is_refused(s: Scenario) -> None:
    def edit(items: list[dict[str, Any]]) -> None:
        del items[first_kept(items)]

    s.refused(new=rewrite_with(s, edit))


def test_a_replaced_record_deleted_without_a_tombstone_is_refused(
    s: Scenario,
) -> None:
    def edit(items: list[dict[str, Any]]) -> None:
        del items[0]

    s.refused(new=rewrite_with(s, edit))


def test_an_inserted_line_is_refused(s: Scenario) -> None:
    def edit(items: list[dict[str, Any]]) -> None:
        items.insert(
            first_kept(items) + 3,
            {
                "platform": "youtube",
                "metric": "subscribers",
                "value": 1,
                "as_of_utc": "2026-09-01T09:00:00Z",
                "source": "api",
                "run_id": "run-x",
            },
        )

    s.refused(new=rewrite_with(s, edit))


def test_a_tombstone_for_a_record_that_never_existed_is_refused(s: Scenario) -> None:
    def edit(items: list[dict[str, Any]]) -> None:
        tomb = dict(items[0])
        tomb["as_of_utc"] = "2026-09-15T09:00:00Z"
        items.append(tomb)

    s.refused(new=rewrite_with(s, edit))


def test_a_tombstone_with_another_provider_tag_is_refused(s: Scenario) -> None:
    def edit(items: list[dict[str, Any]]) -> None:
        items[0]["api_provider"] = "youtube_analytics"

    s.refused(new=rewrite_with(s, edit))


@pytest.mark.parametrize("extra", ["value", "note", "era", "replaces"])
def test_a_tombstone_with_an_extra_field_is_refused(s: Scenario, extra: str) -> None:
    def edit(items: list[dict[str, Any]]) -> None:
        items[0][extra] = SECRET

    s.refused(new=rewrite_with(s, edit))


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("reason", "because"),
        ("trigger_date", "2026-10-21"),
        ("trigger_date", "2025-01-01"),
        ("redacted_at_utc", "2026-11-25T12:00:00Z"),
        ("redacted_at_utc", "2026-11-20T00:00:00Z"),
        ("run_id", "redact-20261125-000000"),
        ("run_id", "redact-20261125-424242"),
        ("run_id", "refresh-20261125-abcdef"),
    ],
)
def test_tombstone_metadata_outside_the_derived_form_is_refused(
    s: Scenario, field_name: str, value: str
) -> None:
    def edit(items: list[dict[str, Any]]) -> None:
        for item in items:
            if item.get("type") == "tombstone" and item["platform"] == "youtube":
                item[field_name] = value

    s.refused(new=rewrite_with(s, edit))


def test_tombstones_that_disagree_with_each_other_are_refused(s: Scenario) -> None:
    def edit(items: list[dict[str, Any]]) -> None:
        items[1]["trigger_date"] = "2026-10-19"

    s.refused(new=rewrite_with(s, edit))


def test_an_old_tombstone_that_changed_or_was_reverted_into_data_is_refused(
    s: Scenario,
) -> None:
    items = objs(s.new)
    old_tomb = next(
        i
        for i, o in enumerate(items)
        if o.get("type") == "tombstone" and o["platform"] == "discord"
    )

    def retimed(rows: list[dict[str, Any]]) -> None:
        rows[old_tomb]["reason"] = "user_request"

    def reverted(rows: list[dict[str, Any]]) -> None:
        rows[old_tomb] = {
            "platform": "discord",
            "metric": "members",
            "value": 12345,
            "as_of_utc": rows[old_tomb]["as_of_utc"],
            "source": "api",
            "run_id": "run-9",
        }

    s.refused(new=rewrite_with(s, retimed))
    s.refused(new=rewrite_with(s, reverted))


def test_a_tombstone_turned_back_into_the_old_data_line_is_refused(s: Scenario) -> None:
    # The old file is the "new" one and the rewrite is the "old": data comes back.
    s.refused(old=s.new, new=s.old)


def test_a_correction_kept_after_its_record_became_a_tombstone_is_refused(
    s: Scenario,
) -> None:
    def edit(items: list[dict[str, Any]]) -> None:
        items.append(
            {
                "platform": "youtube",
                "metric": "subscribers",
                "value": SECRET,
                "as_of_utc": day(1),
                "source": "api",
                "run_id": "run-1",
                "type": "correction",
                "note": "kept",
            }
        )

    s.refused(new=rewrite_with(s, edit))


def test_a_record_appended_in_the_same_rewrite_is_refused(s: Scenario) -> None:
    def edit(items: list[dict[str, Any]]) -> None:
        items.append(
            {
                "platform": "youtube",
                "metric": "subscribers",
                "value": 60000,
                "as_of_utc": "2026-10-25T09:00:00Z",
                "source": "api",
                "run_id": "run-2",
            }
        )

    s.refused(new=rewrite_with(s, edit))


def test_noncanonical_bytes_in_a_rewrite_are_refused(s: Scenario) -> None:
    s.refused(new=chain(objs(s.new), sep=(", ", ": ")))


def test_a_broken_chain_blank_line_and_missing_newline_are_refused(s: Scenario) -> None:
    s.refused(new=s.new.replace(b'"value":50006', b'"value":50007', 1))
    s.refused(new=s.new + b"\n")
    s.refused(new=s.new[:-1])


def test_a_rewrite_that_replaces_nothing_is_refused(s: Scenario) -> None:
    s.refused(new=chain(objs(s.old), sep=(", ", ": ")))


# --- the manifest ----------------------------------------------------------


@pytest.mark.parametrize(
    "mangle",
    [
        lambda t: t + b"# a comment\n",
        lambda t: t.replace(b"\n", b" \n", 1),
        lambda t: t + b"\n",
        lambda t: t.replace(b"count=2", b"count=3"),
        lambda t: t.replace(b"platform_expiry", b"owner_decision"),
        lambda t: t.replace(b"\n", b"\r\n"),
        lambda t: t.rstrip(b"\n"),
        lambda t: b"",
    ],
)
def test_a_manifest_that_is_not_byte_equal_to_the_rebuilt_one_is_refused(
    s: Scenario, mangle: Any
) -> None:
    s.refused(manifest=mangle(s.manifest))


def test_a_missing_manifest_or_a_wrong_name_is_refused(s: Scenario) -> None:
    s.refused(manifest=None)
    s.refused(manifest_name=None)
    s.refused(manifest_name="redact-20261125-000000.manifest")
    s.refused(manifest_name=s.name + ".bak")
    s.refused(manifest_name="../" + s.name)


def test_the_pull_request_day_must_be_the_day_or_the_day_before(s: Scenario) -> None:
    assert s.check(pr_day="2026-11-26").mode == "rewrite"
    s.refused(pr_day="2026-11-27")
    s.refused(pr_day="2026-11-24")
    s.refused(pr_day=None)
    s.refused(pr_day="2026-11-25\n")


def test_a_digit_scan_hit_is_refused_even_with_derived_metadata(
    tmp_path: Path,
) -> None:
    # A value that spells the redaction day shows up in the run id.
    path = tmp_path / "h.jsonl"
    build(path)
    append_records(
        path,
        [
            rec(
                platform="mastodon",
                metric="followers",
                as_of_utc=day(2),
                value=20261125,
            )
        ],
        now=NOW,
    )
    old = path.read_bytes()
    plan = plan_redaction(read_history(path), platform="mastodon", source="api")
    redact_records(
        path,
        plan,
        reason="platform_expiry",
        trigger_date="2026-10-20",
        now=REDACT_AT,
        confirm=True,
        derived=True,
    )
    run_id, _, text = rewrite_metadata(
        [r.key for r in plan], "platform_expiry", "2026-10-20", "2026-11-25"
    )
    with pytest.raises(HistoryError, match="appears in the added text"):
        compare_history(
            old,
            path.read_bytes(),
            allow_tombstones=True,
            manifest=text.encode(),
            manifest_name=f"{run_id}.manifest",
            pr_day=PR_DAY,
            numbers_old=numbers({"version": 1, "platforms": {}}),
            numbers_new=numbers({"version": 1, "platforms": {}}),
        )


# --- caps and the source policy -------------------------------------------


def test_the_record_cap_and_the_percent_cap_are_enforced(s: Scenario) -> None:
    assert s.check(max_replaced=2).mode == "rewrite"
    s.refused(max_replaced=1)


def test_more_than_ten_percent_of_the_records_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    for n in range(1, 6):
        append_records(path, [rec(as_of_utc=day(n), value=100 + n)], now=NOW)
    old = path.read_bytes()
    plan = plan_redaction(read_history(path), platform="youtube", before="2026-10-02")
    redact_records(
        path,
        plan,
        reason="platform_expiry",
        trigger_date="2026-10-20",
        now=REDACT_AT,
        confirm=True,
        derived=True,
    )
    run_id, _, text = rewrite_metadata(
        [r.key for r in plan], "platform_expiry", "2026-10-20", "2026-11-25"
    )
    with pytest.raises(HistoryError, match="percent"):
        compare_history(
            old,
            path.read_bytes(),
            allow_tombstones=True,
            manifest=text.encode(),
            manifest_name=f"{run_id}.manifest",
            pr_day=PR_DAY,
        )


@pytest.mark.parametrize("source", ["hand_kept", "imported"])
def test_a_tombstone_over_a_hand_kept_or_imported_row_is_refused(
    tmp_path: Path, source: str
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    append_records(
        path,
        [rec(platform="threads", metric="followers", as_of_utc=day(2), source=source)],
        now=NOW,
    )
    old = path.read_bytes()
    history = read_history(path)
    plan = plan_redaction(
        history, platform="threads", source=source, include_hand_kept=True
    )
    redact_records(
        path,
        plan,
        reason="platform_expiry",
        trigger_date="2026-10-20",
        now=REDACT_AT,
        confirm=True,
        include_hand_kept=True,
        derived=True,
    )
    run_id, _, text = rewrite_metadata(
        [r.key for r in plan], "platform_expiry", "2026-10-20", "2026-11-25"
    )
    with pytest.raises(HistoryError, match="only api"):
        compare_history(
            old,
            path.read_bytes(),
            allow_tombstones=True,
            manifest=text.encode(),
            manifest_name=f"{run_id}.manifest",
            pr_day=PR_DAY,
        )


# --- numbers.json ----------------------------------------------------------


def test_the_published_copy_of_a_replaced_value_must_go(s: Scenario) -> None:
    s.refused(numbers_new=s.numbers_old)  # matching entry survives
    s.refused(numbers_new=None)  # deleting the file also drops the unrelated entry


def test_an_entry_that_does_not_match_must_stay_unchanged(s: Scenario) -> None:
    changed = json.loads(s.numbers_new)
    changed["platforms"]["discord"]["count"] = 1
    s.refused(numbers_new=numbers(changed))
    dropped = {"version": 1, "platforms": {}}
    s.refused(numbers_new=numbers(dropped))


def test_an_entry_added_or_another_key_in_numbers_is_refused(s: Scenario) -> None:
    added = json.loads(s.numbers_new)
    added["platforms"]["twitch"] = {"count": 5, "as_of": "2026-11-01T00:00:00Z"}
    s.refused(numbers_new=numbers(added))
    other = json.loads(s.numbers_new)
    other["note"] = "x"
    s.refused(numbers_new=numbers(other))
    changed_count = json.loads(s.numbers_new)
    changed_count["platforms"]["youtube"] = {
        "count": SECRET + 5,
        "as_of": "2026-10-01T09:00:00Z",
    }
    s.refused(numbers_new=numbers(changed_count))


def test_hostile_numbers_json_is_refused(s: Scenario) -> None:
    s.refused(numbers_new=b'{"version":1,"platforms":{},"platforms":{}}')
    s.refused(numbers_new=b"[]")
    s.refused(numbers_new=b'{"version":1,"platforms":{"a":{"count":1.5}}}')
    s.refused(numbers_new=b"x" * 70000)


# --- file handling ---------------------------------------------------------


def test_the_reader_refuses_a_link_a_fifo_a_folder_and_an_oversize_file(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    real.write_bytes(b"x")
    link = tmp_path / "link"
    link.symlink_to(real)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    for bad in (link, fifo, tmp_path, tmp_path / "missing"):
        with pytest.raises(HistoryError):
            read_regular(str(bad), 10)
    with pytest.raises(HistoryError):
        read_regular(str(real), 0)
    assert read_regular(str(real), 1) == b"x"


# --- the command -----------------------------------------------------------


def run(s: Scenario, tmp_path: Path, *extra: str, **files: bytes) -> list[str]:
    folder = tmp_path / "cli"
    folder.mkdir(exist_ok=True)
    (folder / "old").write_bytes(s.old)
    (folder / "new").write_bytes(files.get("new", s.new))
    (folder / "man").write_bytes(s.manifest)
    (folder / "n_old").write_bytes(s.numbers_old)
    (folder / "n_new").write_bytes(s.numbers_new)
    return [
        "counts",
        "history-compare",
        "--old",
        str(folder / "old"),
        "--new",
        str(folder / "new"),
        *extra,
    ]


def full(tmp_path: Path, s: Scenario) -> list[str]:
    folder = tmp_path / "cli"
    return [
        "--allow-tombstones",
        "--manifest",
        str(folder / "man"),
        "--manifest-name",
        s.name,
        "--pr-day",
        PR_DAY,
        "--numbers-old",
        str(folder / "n_old"),
        "--numbers-new",
        str(folder / "n_new"),
    ]


def test_the_command_exits_10_only_for_a_valid_rewrite_and_prints_counts_only(
    s: Scenario, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = run(s, tmp_path)
    assert main([*argv, *full(tmp_path, s)]) == 10
    out = capsys.readouterr()
    assert "2 records replaced, 1 corrections dropped" in out.out
    assert str(SECRET) not in out.out + out.err
    assert main(argv) == 1  # no permission: a rewrite is refused
    assert "refused" in capsys.readouterr().err


def test_the_command_exits_0_for_append_only_and_1_for_a_hostile_file(
    s: Scenario, tmp_path: Path
) -> None:
    same = run(s, tmp_path, new=s.old)
    assert main(same) == 0
    hostile = run(s, tmp_path, new=s.new.replace(b'"value":50006', b'"value":1'))
    assert main([*hostile, *full(tmp_path, s)]) == 1
