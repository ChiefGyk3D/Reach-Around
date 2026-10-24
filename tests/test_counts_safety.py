# SPDX-License-Identifier: Apache-2.0
"""Safety rules for ``counts refresh``: suspect values, path collisions,
concurrent runs, directory-anchored writes and bounded DNS lookups."""

import json
import os
import socket
import stat
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

import reach_around.net.egress as egress
from fakenet import FakeTransport, reply
from reach_around.commands.counts import run_refresh
from reach_around.counts.files import FileLock, LockBusy, plan_paths, write_atomic
from reach_around.counts.refresh import DROP_MIN_PREVIOUS, DROP_PERCENT
from reach_around.net.egress import (
    EgressBlocked,
    RawResponse,
    TransportRequest,
    system_resolver,
)

CONFIG = """\
version: 1
creator: {name: Test Creator}
tabs:
  - id: home
    title: Home
    blocks:
      - id: socials
        items:
          - {platform: bluesky, handle: "@example", url: "https://example.com/bluesky"}
          - {platform: github, handle: "@example", url: "https://example.com/github"}
numbers:
  sources:
    - {platform: github, kind: github, user: some-user}
    - {platform: bluesky, kind: bluesky, actor: example.bsky.social}
"""
GH = "https://api.github.com/users/some-user"
BSKY = "https://public.api.bsky.app/"


def entry(count: int, as_of: str = "2026-10-01T00:00:00Z") -> dict[str, object]:
    return {"count": count, "as_of": as_of}


def numbers_text(**platforms: dict[str, object]) -> str:
    return json.dumps({"version": 1, "platforms": platforms}, indent=2) + "\n"


@pytest.fixture
def site(tmp_path: Path) -> Path:
    (tmp_path / "site.yaml").write_text(CONFIG, encoding="utf-8")
    return tmp_path


def refresh(
    site: Path,
    followers: int,
    *,
    accept: list[str] | None = None,
    only: list[str] | None = None,
) -> int:
    transport = FakeTransport({GH: reply({"followers": followers})})
    return run_refresh(
        site / "site.yaml",
        None,
        only or ["github"],
        False,
        accept_drop=accept,
        transport=transport,
        env={},
    )


def status_detail(site: Path, platform: str = "github") -> str | None:
    data = json.loads((site / "numbers.status.json").read_text())
    detail = data["sources"][platform].get("detail")
    assert detail is None or isinstance(detail, str)
    return detail


# ---- A: a suspicious value never replaces a good one -------------------------------


def test_thresholds_are_documented_constants() -> None:
    assert DROP_MIN_PREVIOUS == 1000
    assert DROP_PERCENT == 90


@pytest.mark.parametrize("previous", [1, 100, 999, 1000, 10**9])
def test_zero_over_a_positive_previous_value_is_kept_and_fails(
    site: Path, previous: int
) -> None:
    target = site / "numbers.json"
    target.write_text(numbers_text(github=entry(previous)))
    before = target.read_bytes()
    assert refresh(site, 0) == 3
    assert target.read_bytes() == before
    assert status_detail(site) == "zero_suspect"


@pytest.mark.parametrize("has_file", [False, True])
def test_zero_is_accepted_when_there_is_no_previous_value(
    site: Path, has_file: bool
) -> None:
    if has_file:  # a file that does not mention the platform
        (site / "numbers.json").write_text(numbers_text(tiktok=entry(5)))
    assert refresh(site, 0) == 0
    data = json.loads((site / "numbers.json").read_text())["platforms"]
    assert data["github"]["count"] == 0


def test_zero_over_zero_and_growth_from_zero_are_accepted(site: Path) -> None:
    (site / "numbers.json").write_text(numbers_text(github=entry(0)))
    assert refresh(site, 0) == 0
    assert refresh(site, 7) == 0
    data = json.loads((site / "numbers.json").read_text())["platforms"]
    assert data["github"]["count"] == 7


@pytest.mark.parametrize(
    ("previous", "new", "ok"),
    [
        (1000, 99, False),  # more than 90 percent down
        (1000, 100, True),  # exactly 90 percent down is allowed
        (1000, 101, True),
        (999, 5, True),  # below the 1000 floor the drop rule does not apply
        (5000, 499, False),
        (5000, 500, True),
        (10**9, 10**8 - 1, False),
    ],
)
def test_large_drops_are_suspect_only_from_a_previous_1000_or_more(
    site: Path, previous: int, new: int, ok: bool
) -> None:
    target = site / "numbers.json"
    target.write_text(numbers_text(github=entry(previous)))
    before = target.read_bytes()
    assert refresh(site, new) == (0 if ok else 3)
    if ok:
        data = json.loads(target.read_text())["platforms"]
        assert data["github"]["count"] == new
    else:
        assert target.read_bytes() == before
        assert status_detail(site) == "drop_suspect"


def test_zero_is_reported_as_zero_suspect_not_drop_suspect(site: Path) -> None:
    (site / "numbers.json").write_text(numbers_text(github=entry(5000)))
    assert refresh(site, 0) == 3
    assert status_detail(site) == "zero_suspect"


@pytest.mark.parametrize(("previous", "new"), [(300, 0), (5000, 0), (5000, 10)])
def test_accept_drop_lets_a_real_drop_through(
    site: Path, previous: int, new: int
) -> None:
    (site / "numbers.json").write_text(numbers_text(github=entry(previous)))
    assert refresh(site, new, accept=["github"]) == 0
    data = json.loads((site / "numbers.json").read_text())["platforms"]
    assert data["github"]["count"] == new
    assert status_detail(site) is None


def test_accept_drop_for_another_platform_does_not_help(site: Path) -> None:
    (site / "numbers.json").write_text(numbers_text(github=entry(5000)))
    assert refresh(site, 0, accept=["bluesky"]) == 3


def test_accept_drop_with_an_unknown_platform_is_a_usage_error(
    site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert refresh(site, 5, accept=["nope"]) == 2
    assert "nope" in capsys.readouterr().err
    assert not (site / "numbers.json").exists()


def test_suspect_failure_prints_the_remedy(
    site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (site / "numbers.json").write_text(numbers_text(github=entry(50)))
    assert refresh(site, 0) == 3
    assert "--accept-drop github" in capsys.readouterr().err


# ---- B: output paths can never be the config or each other -------------------------


def paths_error(config: Path, out: Path | None) -> str:
    with pytest.raises(ValueError) as caught:
        plan_paths(config, out)
    return str(caught.value)


def test_plan_paths_default_and_status_and_lock_names(site: Path) -> None:
    planned = plan_paths(site / "site.yaml", None)
    assert planned.target == site.resolve() / "numbers.json"
    assert planned.status == site.resolve() / "numbers.status.json"
    assert planned.lock == site.resolve() / "numbers.json.lock"


def test_status_path_that_resolves_to_the_config_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "site.status.json"
    config.write_text(CONFIG, encoding="utf-8")
    code = run_refresh(config, tmp_path / "site.json", None, False, env={})
    assert code == 2
    assert config.read_text(encoding="utf-8") == CONFIG
    assert "same" in capsys.readouterr().err


@pytest.mark.parametrize(
    "out_name",
    ["site.yaml", "numbers.yml", "NUMBERS.YAML", "site.status.json"],
)
def test_collisions_and_config_like_names_are_refused(
    site: Path, out_name: str
) -> None:
    config = site / "site.yaml"
    if out_name == "site.status.json":
        config = site / "site.status.json"
        config.write_text(CONFIG, encoding="utf-8")
    code = run_refresh(config, site / out_name, None, False, env={})
    assert code == 2
    assert config.read_text(encoding="utf-8") == CONFIG


def test_the_lock_file_may_not_be_the_config(site: Path) -> None:
    config = site / "numbers.json.lock"
    config.write_text(CONFIG, encoding="utf-8")
    assert "same path" in paths_error(config, site / "numbers.json")


def test_symlinked_outputs_are_refused(site: Path) -> None:
    real = site / "real.json"
    real.write_text(numbers_text())
    link = site / "link.json"
    link.symlink_to(real)
    assert "symlink" in paths_error(site / "site.yaml", link)
    dangling = site / "dangling.json"
    dangling.symlink_to(site / "missing.json")
    assert "symlink" in paths_error(site / "site.yaml", dangling)
    # a symlinked status file next to a plain numbers file
    (site / "numbers.status.json").symlink_to(real)
    assert "symlink" in paths_error(site / "site.yaml", None)


def test_a_symlink_pointing_at_the_config_is_refused_before_anything_is_written(
    site: Path,
) -> None:
    link = site / "numbers.json"
    link.symlink_to(site / "site.yaml")
    code = run_refresh(site / "site.yaml", None, None, False, env={})
    assert code == 2
    assert (site / "site.yaml").read_text(encoding="utf-8") == CONFIG


def test_a_hardlink_to_the_config_is_refused(site: Path) -> None:
    config = site / "site.yaml"
    hard = site / "numbers.json"
    os.link(config, hard)
    assert "same file" in paths_error(config, hard)
    code = run_refresh(config, hard, None, False, env={})
    assert code == 2
    assert config.read_text(encoding="utf-8") == CONFIG


def test_a_hardlinked_status_file_is_refused(site: Path) -> None:
    os.link(site / "site.yaml", site / "numbers.status.json")
    assert "same file" in paths_error(site / "site.yaml", None)


def test_hardlinks_between_outputs_are_refused(site: Path) -> None:
    (site / "numbers.json").write_text(numbers_text())
    os.link(site / "numbers.json", site / "numbers.status.json")
    assert "same file" in paths_error(site / "site.yaml", None)


def test_missing_parent_directory_and_directory_targets_are_refused(
    site: Path,
) -> None:
    assert "does not exist" in paths_error(site / "site.yaml", site / "no" / "n.json")
    assert paths_error(site / "site.yaml", site)
    assert paths_error(site / "site.yaml", site / "..")


def test_a_symlinked_parent_is_resolved_before_the_checks(
    site: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    (elsewhere / "cfg.txt").write_text(CONFIG, encoding="utf-8")
    alias = site / "alias"
    alias.symlink_to(elsewhere, target_is_directory=True)
    # alias/site.yaml is the same file as the config through a directory link
    assert "same" in paths_error(elsewhere / "cfg.txt", alias / "cfg.txt")


# ---- C: concurrent refreshes cannot roll back a newer value ------------------------


def test_overlapping_runs_do_not_roll_back_a_newer_value(site: Path) -> None:
    target = site / "numbers.json"
    target.write_text(numbers_text(bluesky=entry(100), github=entry(100)))
    a_inside = threading.Event()
    release_a = threading.Event()
    b_inside = threading.Event()
    release_b = threading.Event()

    def slow_bsky(request: TransportRequest) -> RawResponse:
        a_inside.set()
        assert release_a.wait(10)
        return reply({"followersCount": 200})

    def slow_gh(request: TransportRequest) -> RawResponse:
        b_inside.set()
        assert release_b.wait(10)
        return reply({"followers": 150})

    codes: dict[str, int] = {}

    def run(name: str, only: str, transport: FakeTransport) -> None:
        codes[name] = run_refresh(
            site / "site.yaml", None, [only], False, transport=transport, env={}
        )

    first = threading.Thread(
        target=run, args=("a", "bluesky", FakeTransport({BSKY: slow_bsky}))
    )
    second = threading.Thread(
        target=run, args=("b", "github", FakeTransport({GH: slow_gh}))
    )
    first.start()
    assert a_inside.wait(10)
    second.start()
    # B must be waiting on the lock, not already fetching from a stale file
    assert not b_inside.wait(0.5)
    release_a.set()
    first.join(10)
    assert b_inside.wait(10)
    release_b.set()
    second.join(10)
    assert codes == {"a": 0, "b": 0}
    data = json.loads(target.read_text())["platforms"]
    assert data["bluesky"]["count"] == 200  # A's newer value survived B's write
    assert data["github"]["count"] == 150


def test_lock_wait_is_bounded_and_reported(
    site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lock_path = site / "numbers.json.lock"
    with FileLock(lock_path, wait=1.0):
        code = run_refresh(
            site / "site.yaml",
            None,
            ["github"],
            False,
            transport=FakeTransport({GH: reply({"followers": 1})}),
            env={},
            lock_wait=0.2,
        )
    assert code == 1
    assert "another" in capsys.readouterr().err
    assert not (site / "numbers.json").exists()


def test_file_lock_is_exclusive_released_and_private(site: Path) -> None:
    lock_path = site / "x.lock"
    with FileLock(lock_path, wait=1.0):
        with pytest.raises(LockBusy):
            with FileLock(lock_path, wait=0.1):
                pass
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    with FileLock(lock_path, wait=0.1):  # released on exit
        pass


def test_file_lock_refuses_a_symlinked_lock_file(site: Path) -> None:
    real = site / "real.lock"
    real.write_text("")
    link = site / "x.lock"
    link.symlink_to(real)
    with pytest.raises(OSError):
        with FileLock(link, wait=0.1):
            pass


def test_dry_run_takes_no_lock_and_writes_nothing(site: Path) -> None:
    with FileLock(site / "numbers.json.lock", wait=1.0):
        assert run_refresh(site / "site.yaml", None, None, True, env={}) == 0
    assert sorted(p.name for p in site.iterdir()) == [
        "numbers.json.lock",
        "site.yaml",
    ]


# ---- D: the write is anchored to a verified directory ------------------------------


def test_a_parent_swapped_for_a_symlink_mid_write_cannot_redirect_the_file(
    tmp_path: Path,
) -> None:
    good = tmp_path / "out"
    good.mkdir()
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    moved = tmp_path / "out-moved"

    def swap() -> None:
        good.rename(moved)
        good.symlink_to(attacker, target_is_directory=True)

    write_atomic(good / "numbers.json", "data\n", before_replace=swap)
    assert (moved / "numbers.json").read_text() == "data\n"
    assert list(attacker.iterdir()) == []
    assert [p.name for p in moved.iterdir()] == ["numbers.json"]


def test_a_symlink_as_the_output_directory_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(OSError):
        write_atomic(link / "numbers.json", "x")
    assert list(real.iterdir()) == []


def test_a_planted_symlink_at_the_final_name_is_replaced_not_followed(
    tmp_path: Path,
) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    target = tmp_path / "numbers.json"
    target.symlink_to(victim)
    write_atomic(target, "new\n")
    assert victim.read_text() == "keep"
    assert not target.is_symlink() and target.read_text() == "new\n"


def test_write_atomic_still_uses_mode_0600_and_cleans_up_on_failure(
    tmp_path: Path,
) -> None:
    target = tmp_path / "n.json"
    write_atomic(target, "a")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    def boom() -> None:
        raise OSError(28, "No space left on device")

    with pytest.raises(OSError):
        write_atomic(target, "b", before_replace=boom)
    assert target.read_text() == "a"
    assert [p.name for p in tmp_path.iterdir()] == ["n.json"]


# ---- E: abandoned DNS lookups are bounded ------------------------------------------


@pytest.fixture
def blocked_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[threading.Event, list[str]]]:
    release = threading.Event()
    calls: list[str] = []

    def slow(host: str, *args: object, **kwargs: object) -> list[object]:
        calls.append(host)
        release.wait(10)
        return [(0, 0, 0, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", slow)
    monkeypatch.setattr(egress, "MAX_RESOLVER_THREADS", 2)
    yield release, calls
    release.set()
    egress.wait_for_idle_resolvers(5.0)


def test_lookups_beyond_the_cap_are_refused_with_a_typed_error(
    blocked_dns: tuple[threading.Event, list[str]],
) -> None:
    _, calls = blocked_dns
    for host in ("a.example.org", "b.example.org"):
        with pytest.raises(EgressBlocked) as caught:
            system_resolver(host, 443, timeout=0.05)
        assert caught.value.reason == "resolution_timeout"
    with pytest.raises(EgressBlocked) as third:
        system_resolver("c.example.org", 443, timeout=0.05)
    assert third.value.reason == "resolution_busy"
    assert sorted(calls) == ["a.example.org", "b.example.org"]  # no third thread


def test_the_same_host_shares_one_lookup_thread(
    blocked_dns: tuple[threading.Event, list[str]],
) -> None:
    _, calls = blocked_dns
    for _ in range(5):
        with pytest.raises(EgressBlocked) as caught:
            system_resolver("a.example.org", 443, timeout=0.05)
        assert caught.value.reason == "resolution_timeout"
    assert calls == ["a.example.org"]


def test_a_shared_lookup_answers_a_later_caller_once_it_finishes(
    blocked_dns: tuple[threading.Event, list[str]],
) -> None:
    release, calls = blocked_dns
    with pytest.raises(EgressBlocked):
        system_resolver("a.example.org", 443, timeout=0.05)
    release.set()
    assert system_resolver("a.example.org", 443, timeout=5.0) == ["93.184.216.34"]
    assert calls == ["a.example.org"]


def test_capacity_returns_when_lookups_finish(
    blocked_dns: tuple[threading.Event, list[str]],
) -> None:
    release, _ = blocked_dns
    for host in ("a.example.org", "b.example.org"):
        with pytest.raises(EgressBlocked):
            system_resolver(host, 443, timeout=0.05)
    release.set()
    assert egress.wait_for_idle_resolvers(5.0)
    assert system_resolver("c.example.org", 443, timeout=5.0) == ["93.184.216.34"]
