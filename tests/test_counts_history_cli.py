# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest

import reach_around.commands.counts as counts_cmd
from fakenet import FakeTransport, reply
from reach_around.cli import main
from reach_around.counts.history import read_history
from reach_around.net.egress import RawResponse

CONFIG = """\
version: 1
creator: {name: Test Creator}
tabs:
  - id: home
    title: Home
    blocks:
      - id: socials
        items:
          - {platform: bluesky, handle: "@example", url: "https://example.com/b"}
          - {platform: github, handle: "@example", url: "https://example.com/g"}
numbers:
  history_file: history/social.jsonl
  sources:
    - {platform: bluesky, kind: bluesky, actor: example.bsky.social}
    - {platform: github, kind: github, user: some-user}
"""
BSKY = "https://public.api.bsky.app/"
GH = "https://api.github.com/users/some-user"


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "history").mkdir()
    (tmp_path / "site.yaml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.setattr(counts_cmd, "default_sleep", lambda seconds: None)
    return tmp_path


def use(monkeypatch: pytest.MonkeyPatch, routes: dict[str, RawResponse]) -> None:
    transport = FakeTransport(routes)
    monkeypatch.setattr(counts_cmd, "default_transport", lambda: transport)


def refresh(root: Path, *extra: str) -> int:
    return main(["counts", "refresh", "--config", str(root / "site.yaml"), *extra])


def test_refresh_appends_fetched_values_only(
    setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, {BSKY: reply({"followersCount": 111}), GH: reply({}, status=500)})
    assert refresh(setup) == 3
    history = read_history(setup / "history" / "social.jsonl")
    assert [(r.platform, r.metric, r.value, r.source) for r in history.records] == [
        ("bluesky", "followers", 111, "api")
    ]  # github failed: a gap, not a zero
    assert history.records[0].run_id.startswith("refresh-")


def test_refresh_without_history_file_writes_none(
    setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (setup / "site.yaml").write_text(
        CONFIG.replace("  history_file: history/social.jsonl\n", ""), encoding="utf-8"
    )
    use(monkeypatch, {BSKY: reply({"followersCount": 1}), GH: reply({"followers": 2})})
    assert refresh(setup) == 0
    assert not (setup / "history" / "social.jsonl").exists()


def test_dry_run_and_total_failure_write_no_history(
    setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, {BSKY: reply({}, status=500), GH: reply({}, status=500)})
    assert refresh(setup) == 3
    assert refresh(setup, "--dry-run") == 0
    assert not (setup / "history" / "social.jsonl").exists()


def test_damaged_history_is_reported_and_left_alone(
    setup: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = setup / "history" / "social.jsonl"
    target.write_bytes(b'{"half')
    use(monkeypatch, {BSKY: reply({"followersCount": 5}), GH: reply({"followers": 6})})
    assert refresh(setup) == 1
    assert target.read_bytes() == b'{"half'
    assert "history not written" in capsys.readouterr().err


def test_history_add_and_verify(
    setup: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = str(setup / "site.yaml")
    base = ["counts", "history-add", "--config", config, "--platform", "linkedin"]
    assert (
        main([*base, "--value", "321", "--as-of", "2026-11-01", "--note", "typed"]) == 0
    )
    assert main([*base, "--value", "322", "--as-of", "2026-11-01"]) == 1  # duplicate
    fix = [*base, "--value", "320", "--as-of", "2026-11-01", "--correction"]
    assert main(fix) == 1  # needs a note
    assert main([*fix, "--note", "misread"]) == 0
    assert main(["counts", "history-verify", "--config", config]) == 0
    assert "2 records" in capsys.readouterr().out
    history = read_history(setup / "history" / "social.jsonl")
    point = history.series("linkedin", "followers").points()[0]
    assert (point.value, point.source, point.corrected) == (320, "hand_kept", True)


def test_history_add_rejects_bad_input_and_disabled_store(
    setup: Path, tmp_path: Path
) -> None:
    config = str(setup / "site.yaml")
    base = ["counts", "history-add", "--config", config, "--platform", "linkedin"]
    assert main([*base, "--value", "-5", "--as-of", "2026-11-01"]) == 1
    assert main([*base, "--value", "5", "--as-of", "2999-01-01"]) == 1
    (setup / "site.yaml").write_text(
        CONFIG.replace("  history_file: history/social.jsonl\n", ""), encoding="utf-8"
    )
    assert main([*base, "--value", "5", "--as-of", "2026-11-01"]) == 2
    assert main(["counts", "history-verify", "--config", config]) == 2


def test_verify_refuses_a_truncated_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "h.jsonl"
    assert (
        main(
            [
                "counts",
                "history-add",
                "--file",
                str(path),
                "--platform",
                "kick",
                "--value",
                "9",
                "--as-of",
                "2026-11-01",
            ]
        )
        == 0
    )
    path.write_bytes(path.read_bytes()[:-5])
    assert main(["counts", "history-verify", "--file", str(path)]) == 1
    assert "truncated" in capsys.readouterr().err


def test_history_file_config_rejects_junk(setup: Path) -> None:
    (setup / "site.yaml").write_text(
        CONFIG.replace("history/social.jsonl", "history/"), encoding="utf-8"
    )
    assert refresh(setup) == 2


def test_history_path_may_not_collide_with_other_outputs(setup: Path) -> None:
    for name in ("numbers.json", "numbers.status.json", "site.yaml"):
        (setup / "site.yaml").write_text(
            CONFIG.replace("history/social.jsonl", name), encoding="utf-8"
        )
        assert refresh(setup) == 2
    (setup / "site.yaml").write_text(
        CONFIG.replace("history/social.jsonl", "numbers.json.lock"), encoding="utf-8"
    )
    assert refresh(setup) == 2


def test_history_path_must_stay_inside_the_config_folder(
    setup: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    for bad in ("/var/x.jsonl", "../x.jsonl", "history/../../x.jsonl", "a\\b"):
        (setup / "site.yaml").write_text(
            CONFIG.replace("history/social.jsonl", bad), encoding="utf-8"
        )
        assert refresh(setup) == 2
    outside = tmp_path_factory.mktemp("outside")
    (setup / "escape").symlink_to(outside)
    (setup / "site.yaml").write_text(
        CONFIG.replace("history/social.jsonl", "escape/x.jsonl"), encoding="utf-8"
    )
    assert refresh(setup) == 2
    assert list(outside.iterdir()) == []


def test_history_failure_leaves_numbers_and_status_untouched(
    setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (setup / "history" / "social.jsonl").write_bytes(b'{"half')
    use(monkeypatch, {BSKY: reply({"followersCount": 5}), GH: reply({"followers": 6})})
    assert refresh(setup) == 1
    assert not (setup / "numbers.json").exists()
    assert not (setup / "numbers.status.json").exists()


def test_a_second_refresh_the_same_day_keeps_the_first_value(
    setup: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use(monkeypatch, {BSKY: reply({"followersCount": 5}), GH: reply({"followers": 6})})
    assert refresh(setup) == 0
    use(monkeypatch, {BSKY: reply({"followersCount": 7}), GH: reply({"followers": 8})})
    assert refresh(setup) == 0
    values = [r.value for r in read_history(setup / "history" / "social.jsonl").records]
    assert values == [5, 6]
    assert "already recorded" in capsys.readouterr().out


def test_import_appends_hand_kept_rows_to_the_history(setup: Path) -> None:
    from datetime import UTC, datetime

    cfg = setup / "kick.yaml"
    cfg.write_text(
        CONFIG.replace(
            "          - {platform: github",
            '          - {platform: kick, label: subscribers, handle: "@e",'
            ' url: "https://example.com/k"}\n          - {platform: github',
        ),
        encoding="utf-8",
    )
    day = datetime.now(UTC).date().isoformat()
    data = setup / "in.csv"
    data.write_text(
        f"platform,metric,value,as_of\nkick,subscribers,41,{day}\n", encoding="utf-8"
    )
    argv = ["counts", "import", "--config", str(cfg), "--csv", str(data)]
    assert main(argv) == 0
    records = read_history(setup / "history" / "social.jsonl").records
    assert [(r.platform, r.metric, r.value, r.source) for r in records] == [
        ("kick", "subscribers", 41, "hand_kept")
    ]
    assert main(argv) == 0  # the same day again keeps the first, no error
    assert len(read_history(setup / "history" / "social.jsonl").records) == 1
