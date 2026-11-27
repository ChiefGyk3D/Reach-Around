# SPDX-License-Identifier: Apache-2.0
"""Slice 4 of the fediverse collector (#940): the config model, the refresh
wiring, the history rows, the kit feed and the egress check.

Hermetic: a fake transport or fake collectors stand in for the network, the clock
and the sleep are injected, and every handle, DID and host is a synthetic example.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, Self

import pytest
from pydantic import ValidationError

import reach_around.commands.counts as counts_cmd
import reach_around.counts.history as history_mod
from fakenet import FakeTransport, reply
from kitpdfharness import kit_doc
from reach_around.cli import main
from reach_around.commands.counts import run_egress_check, run_refresh
from reach_around.config import ConfigError, SiteConfig, load_site_config
from reach_around.config.fediverse import fediverse_measures
from reach_around.config.fediverse_sources import (
    BlueskyFediverseSource,
    MastodonFediverseSource,
)
from reach_around.config.import_guard import ImportGuardError
from reach_around.config.kit import KitConfig
from reach_around.config.numbers_sources import NumbersConfig
from reach_around.counts import bluesky_feed, mastodon_feed
from reach_around.counts import fediverse_refresh as fed
from reach_around.counts.fediverse_rows import InteractionRow, NotProvided
from reach_around.counts.fediverse_store import (
    apply_collected,
    dump_snapshots,
    feed_kit,
    load_snapshots,
    missing_endpoints,
    required_endpoints,
    snapshot_path,
)
from reach_around.counts.files import write_atomic
from reach_around.counts.history import HistoryError, append_records, read_history

NOW = datetime(2026, 11, 26, 12, 0, 0, tzinfo=UTC)
TODAY = date(2026, 11, 26)
# History validates a row against its own clock ("not in the future"). Every test
# here freezes that clock at a fixed day after all scenarios, so no test depends on
# the real date (Daybreak round 3: it used to be the real clock plus a day).
FROZEN = NOW + timedelta(days=30)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> Self:
        moment = FROZEN if tz is None else FROZEN.astimezone(tz)
        return cls.fromtimestamp(moment.timestamp(), tz)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The history module reads its clock from here, never from the machine."""
    monkeypatch.setattr(history_mod, "datetime", _FrozenDatetime)


DID = "did:plc:ewvi7nxzyoun6zhxrhs64oiz"
BSKY = {"kind": "bluesky", "actor": "alice.example.com"}
MASTO = {"kind": "mastodon", "host": "social.example", "acct": "alice"}


def numbers(*items: dict[str, Any]) -> dict[str, Any]:
    return {"fediverse": list(items)}


# --- (a) the config model --------------------------------------------------------


def test_valid_sources_load_and_are_typed() -> None:
    config = NumbersConfig.model_validate(numbers(BSKY, {**MASTO, "account_id": "109"}))
    assert isinstance(config.fediverse[0], BlueskyFediverseSource)
    second = config.fediverse[1]
    assert isinstance(second, MastodonFediverseSource)
    assert second.account_id == "109"
    assert NumbersConfig.model_validate(numbers(BSKY, {**MASTO, "account_id": 109}))
    assert NumbersConfig.model_validate({}).fediverse == []


@pytest.mark.parametrize(
    "item",
    [
        {"kind": "bluesky", "actor": "@alice.example.com"},
        {"kind": "bluesky", "actor": "https://bsky.app/profile/alice.example.com"},
        {"kind": "bluesky", "actor": "did:plc:short"},
        {"kind": "bluesky", "actor": "nodots"},
        {"kind": "bluesky", "actor": "alice.example.com", "token_env": "TOKEN"},
        {"kind": "mastodon", "host": "127.0.0.1", "acct": "alice"},
        {"kind": "mastodon", "host": "0x7f.1", "acct": "alice"},
        {"kind": "mastodon", "host": "localhost", "acct": "alice"},
        {"kind": "mastodon", "host": "social.example:8443", "acct": "alice"},
        {"kind": "mastodon", "host": "Social.Example", "acct": "alice"},
        {"kind": "mastodon", "host": "user@social.example", "acct": "alice"},
        {"kind": "mastodon", "host": "social.example", "acct": "@alice"},
        {"kind": "mastodon", "host": "social.example", "acct": "alice@other.example"},
        {"kind": "mastodon", "host": "social.example", "acct": "a b"},
        {**MASTO, "account_id": "12ab"},
        {**MASTO, "account_id": "-5"},
        {**MASTO, "account_id": True},
        {**MASTO, "access_token": "secret"},
        {"kind": "pixelfed", "host": "social.example", "acct": "alice"},
        {"kind": "mastodon", "host": "social.example"},
    ],
)
def test_hostile_sources_are_refused(item: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        NumbersConfig.model_validate(numbers(item))


def test_duplicates_too_many_and_an_empty_list_are_refused() -> None:
    with pytest.raises(ValidationError, match="more than once"):
        NumbersConfig.model_validate(
            numbers(BSKY, {**BSKY, "actor": "bob.example.com"})
        )
    with pytest.raises(ValidationError, match="at most 2"):
        NumbersConfig.model_validate(numbers(BSKY, MASTO, MASTO))
    with pytest.raises(ValidationError, match="at least one"):
        NumbersConfig.model_validate({"fediverse": []})


def test_the_config_loader_reads_the_block_from_yaml(tmp_path: Path) -> None:
    config = load_site_config(write_config(tmp_path))
    assert config.numbers is not None
    assert [s.kind for s in config.numbers.fediverse] == ["bluesky", "mastodon"]
    path = tmp_path / "bad.yaml"
    path.write_text(
        CONFIG.replace("host: social.example", "host: 10.0.0.1"), encoding="utf-8"
    )
    with pytest.raises(ConfigError):
        load_site_config(path)


# --- (b) refresh wiring with fake providers --------------------------------------


def complete_read(*rows: tuple[int, int, int]) -> bluesky_feed.BlueskyRead:
    return bluesky_feed.BlueskyRead(
        321, 77, tuple(InteractionRow(*r) for r in rows), calls=3
    )


def fake_bluesky(read: bluesky_feed.BlueskyRead) -> Any:
    def collect(actor: str, **kw: Any) -> bluesky_feed.BlueskyRead:
        assert actor == "alice.example.com" and kw["now"] == NOW
        return read

    return collect


def fake_mastodon(read: mastodon_feed.MastodonRead) -> Any:
    def collect(host: str, acct: str, **kw: Any) -> mastodon_feed.MastodonRead:
        assert (host, acct) == ("social.example", "alice")
        return read

    return collect


SOURCES = list(NumbersConfig.model_validate(numbers(BSKY, MASTO)).fediverse)


def test_a_complete_read_gives_a_snapshot_and_aggregates() -> None:
    result = fed.run(
        SOURCES,
        now=NOW,
        collect_bluesky=fake_bluesky(complete_read((1, 5, 0), (2, 3, 4))),
        collect_mastodon=fake_mastodon(mastodon_feed.MastodonRead(10, 20, (), calls=2)),
    )
    snap = result.snapshots["bluesky"]
    assert snap["complete"] is True and snap["as_of"] == "2026-11-26"
    assert result.aggregates["bluesky"] == {
        "posts_30d": 2,
        "boosts_30d": 3,
        "favorites_30d": 8,
        "replies_30d": 4,
    }
    # a finished window with no post is the one true zero
    assert result.aggregates["mastodon"]["posts_30d"] == 0
    assert [(o.platform, o.status) for o in result.outcomes] == [
        ("fediverse-bluesky", "ok"),
        ("fediverse-mastodon", "ok"),
    ]


def test_an_incomplete_read_writes_nothing_and_names_a_fixed_reason() -> None:
    result = fed.run(
        SOURCES[:1],
        now=NOW,
        collect_bluesky=fake_bluesky(
            bluesky_feed.BlueskyRead(321, 77, NotProvided("partial_pages"), calls=2)
        ),
    )
    assert result.snapshots == {} and result.aggregates == {}
    (outcome,) = result.outcomes
    assert (outcome.status, outcome.detail, outcome.count) == (
        "error",
        "partial_pages",
        None,
    )


def test_a_collector_that_raises_is_reported_by_class_only() -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("alice.example.com leaked text")

    result = fed.run(SOURCES[:1], now=NOW, collect_bluesky=boom)
    assert result.snapshots == {}
    assert result.outcomes[0].detail == "RuntimeError"


def test_a_guard_failure_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_snapshot: Any) -> None:
        raise ImportGuardError("no")

    monkeypatch.setattr(bluesky_feed, "check_snapshot", refuse)
    result = fed.run(
        SOURCES[:1], now=NOW, collect_bluesky=fake_bluesky(complete_read((1, 1, 1)))
    )
    assert result.snapshots == {} and result.aggregates == {}
    assert result.outcomes[0].detail == "guard"


# The full command, with a fake HTTP transport and the file layout of an instance.

CONFIG = """\
version: 1
creator: {name: Test Creator}
tabs:
  - id: home
    title: Home
    blocks:
      - id: socials
        items:
          - {platform: github, handle: "@example", url: "https://example.com/github"}
numbers:
  history_file: history/social.jsonl
  fediverse:
    - {kind: bluesky, actor: alice.example.com}
    - {kind: mastodon, host: social.example, acct: alice}
"""
PROFILE = "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile"
FEED = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
DATE_OK = {"date": "Thu, 26 Nov 2026 12:00:00 GMT"}


def write_config(root: Path, text: str = CONFIG) -> Path:
    (root / "history").mkdir(exist_ok=True)
    path = root / "site.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def bsky_post(n: int, when: datetime, likes: int) -> dict[str, Any]:
    key = "3jt4w2fyy4s" + "22" if n == 0 else "3jt4w2fyy4s" + "23"
    return {
        "post": {
            "uri": f"at://{DID}/app.bsky.feed.post/{key}",
            "author": {"did": DID, "handle": "alice.example.com"},
            "record": {"text": "hello @friend", "createdAt": stamp(when)},
            "likeCount": likes,
            "repostCount": 1,
            "replyCount": 2,
            "indexedAt": stamp(when),
        }
    }


def good_transport() -> FakeTransport:
    page = {"feed": [bsky_post(0, NOW - timedelta(days=1), 9)]}
    return FakeTransport(
        {
            PROFILE: reply(
                {"did": DID, "followersCount": 321, "postsCount": 77}, headers=DATE_OK
            ),
            FEED: reply(page, headers=DATE_OK),
            "https://social.example/": reply({"error": "down"}, status=503),
        }
    )


@pytest.fixture
def instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    write_config(tmp_path)
    monkeypatch.setattr(counts_cmd, "default_sleep", lambda seconds: None)
    return tmp_path


def refresh(root: Path, transport: FakeTransport) -> int:
    return run_refresh(
        root / "site.yaml",
        None,
        None,
        False,
        env={},
        transport=transport,
        now=lambda: NOW,
    )


def test_refresh_writes_snapshot_history_and_status(
    instance: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = refresh(instance, good_transport())
    assert code == 3  # the Mastodon source failed (503): reported, never zero
    out = capsys.readouterr()
    assert "fediverse-bluesky: complete 1 posts" in out.out
    assert "fediverse-mastodon: incomplete (" in out.err
    stored = load_snapshots(snapshot_path(instance / "numbers.json"))
    assert set(stored) == {"bluesky"}
    assert stored["bluesky"]["interactions"] == [
        {"boosts": 1, "favorites": 9, "replies": 2}
    ]
    records = read_history(instance / "history" / "social.jsonl").records
    rows = {(r.platform, r.metric): r.value for r in records}
    assert rows == {
        ("bluesky", "posts_30d"): 1,
        ("bluesky", "boosts_30d"): 1,
        ("bluesky", "favorites_30d"): 9,
        ("bluesky", "replies_30d"): 2,
    }
    assert all(
        r.source == "api" and r.as_of_utc.startswith("2026-11-26") for r in records
    )
    status = json.loads((instance / "numbers.status.json").read_text())["sources"]
    assert status["fediverse-bluesky"]["status"] == "ok"
    assert status["fediverse-mastodon"]["status"] == "error"
    assert "numbers.json" in {p.name for p in instance.iterdir()}
    # nothing identifying reaches a stored file
    for name in ("numbers.fediverse.json", "numbers.json", "numbers.status.json"):
        text = (instance / name).read_text()
        assert "alice" not in text and DID not in text and "social.example" not in text
    assert "alice" not in (instance / "history" / "social.jsonl").read_text()


def test_an_incomplete_read_keeps_the_prior_snapshot_and_writes_no_zero(
    instance: Path,
) -> None:
    assert refresh(instance, good_transport()) == 3
    target = snapshot_path(instance / "numbers.json")
    before = target.read_bytes()
    history_before = (instance / "history" / "social.jsonl").read_bytes()
    later = FakeTransport({PROFILE: reply({"error": "x"}, status=500)})
    code = run_refresh(
        instance / "site.yaml",
        None,
        None,
        False,
        env={},
        transport=later,
        now=lambda: NOW + timedelta(days=1),
    )
    assert code == 3
    assert target.read_bytes() == before  # the prior snapshot, untouched
    assert (instance / "history" / "social.jsonl").read_bytes() == history_before
    kept = load_snapshots(target)["bluesky"]
    assert kept["as_of"] == "2026-11-26" and kept["interactions"]


def test_a_guard_failure_in_the_command_writes_no_file(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(_snapshot: Any) -> None:
        raise ImportGuardError("no")

    monkeypatch.setattr(bluesky_feed, "check_snapshot", refuse)
    assert refresh(instance, good_transport()) == 3
    assert not snapshot_path(instance / "numbers.json").exists()
    assert not (instance / "history" / "social.jsonl").exists()


def test_only_never_starts_the_fediverse_collector(instance: Path) -> None:
    transport = good_transport()
    code = run_refresh(
        instance / "site.yaml",
        None,
        ["bluesky"],
        False,
        env={},
        transport=transport,
        now=lambda: NOW,
    )
    assert code == 2  # no numbers source named bluesky: refused, nothing fetched
    assert transport.calls == []


def test_dry_run_names_the_hosts_and_fetches_nothing(
    instance: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = good_transport()
    code = run_refresh(
        instance / "site.yaml", None, None, True, env={}, transport=transport
    )
    out = capsys.readouterr().out
    assert code == 0 and transport.calls == []
    assert "public.api.bsky.app:443" in out and "social.example:443" in out
    assert not snapshot_path(instance / "numbers.json").exists()


# --- (c) the stored file is guarded both ways ------------------------------------


def stored_snapshot(**over: Any) -> dict[str, object]:
    base: dict[str, object] = {
        "platform": "bluesky",
        "as_of": "2026-11-26",
        "complete": True,
        "followers": 5,
        "posts": 9,
        "interactions": [{"boosts": 1, "favorites": 2, "replies": 3}],
    }
    base.update(over)
    return base


def test_the_store_refuses_to_write_or_read_an_identifying_snapshot(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConfigError):
        dump_snapshots({"bluesky": stored_snapshot(handle="alice.example.com")})
    with pytest.raises(ConfigError):
        dump_snapshots({"bluesky": stored_snapshot(complete=False, reason="timeout")})
    path = tmp_path / "numbers.fediverse.json"
    good = dump_snapshots({"bluesky": stored_snapshot()})
    path.write_text(good, encoding="utf-8")
    assert set(load_snapshots(path)) == {"bluesky"}
    dirty = json.loads(good)
    dirty["platforms"]["bluesky"]["interactions"][0]["author"] = "x"
    path.write_text(json.dumps(dirty), encoding="utf-8")
    assert load_snapshots(path) == {}
    path.write_text("{not json", encoding="utf-8")
    assert load_snapshots(path) == {}
    assert load_snapshots(tmp_path / "missing.json") == {}


# --- (d) the kit block feed ------------------------------------------------------


def kit_config(*, with_typed: bool = True, show_mean: bool = False) -> SiteConfig:
    kit = kit_doc()
    kit["sections"] = [{"platform": "bluesky"}]
    kit["fediverse"] = (
        [
            {
                "platform": "bluesky",
                "profile_url": "https://bsky.example/profile/typed",
                "as_of": "2026-10-01",
                "followers": 11,
                "interactions": [{"boosts": 100, "favorites": 100, "replies": 100}],
                "show_mean": show_mean,
            }
        ]
        if with_typed
        else []
    )
    return SiteConfig.model_validate(
        {
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
            "privacy": {
                "controller_name": "Example Creator",
                "contact": "example.com/contact",
            },
            "numbers": numbers(BSKY),
            "kit": kit,
        }
    )


def write_snapshot(root: Path, snapshot: dict[str, object] | None = None) -> Path:
    path = snapshot_path(root / "numbers.json")
    path.write_text(
        dump_snapshots({"bluesky": snapshot or stored_snapshot()}), encoding="utf-8"
    )
    return path


def test_a_fresh_snapshot_feeds_the_kit_block(tmp_path: Path) -> None:
    path = write_snapshot(tmp_path)
    config = apply_collected(kit_config(show_mean=True), path, TODAY)
    assert config.kit is not None
    (entry,) = config.kit.fediverse
    assert entry.platform == "bluesky" and entry.collected
    assert entry.profile_url == "https://bsky.app/profile/alice.example.com"
    assert (entry.followers, entry.posts, entry.as_of) == (5, 9, date(2026, 11, 26))
    assert [r.favorites for r in entry.interactions] == [2]
    assert entry.show_mean is True  # the owner's choice is kept
    measures = {m.key: m for m in fediverse_measures(config.kit.fediverse)}
    assert measures["median_favorites_30d"].value == 2  # computed by the build
    assert {m.trust.label for m in measures.values() if m.trust} == {"api"}


def test_the_hand_kept_path_is_unchanged_without_collected_data(
    tmp_path: Path,
) -> None:
    base = kit_config()
    assert base.kit is not None
    typed = base.kit.fediverse
    measures = fediverse_measures(typed)
    assert {m.trust.label for m in measures if m.trust} == {"hand-kept"}
    assert not typed[0].collected
    assert (
        apply_collected(base, snapshot_path(tmp_path / "numbers.json"), TODAY) is base
    )
    path = write_snapshot(tmp_path)
    for snapshot, today in (
        (stored_snapshot(as_of="2026-10-01"), TODAY),  # older than the window
        (stored_snapshot(), date(2026, 11, 20)),  # dated in the future
    ):
        path = write_snapshot(tmp_path, snapshot)
        kept = apply_collected(base, path, today)
        assert kept.kit is not None and kept.kit.fediverse == typed
    path.write_text("{}", encoding="utf-8")
    assert apply_collected(base, path, TODAY).kit == base.kit
    # a config with no fediverse source never reads the file
    plain = base.model_copy(update={"numbers": None})
    assert apply_collected(plain, write_snapshot(tmp_path), TODAY) is plain


def test_a_snapshot_can_add_a_platform_with_no_typed_entry(tmp_path: Path) -> None:
    config = apply_collected(
        kit_config(with_typed=False), write_snapshot(tmp_path), TODAY
    )
    assert config.kit is not None
    assert [e.platform for e in config.kit.fediverse] == ["bluesky"]


def test_a_typed_entry_cannot_claim_to_be_collected() -> None:
    kit = kit_doc()
    kit["fediverse"] = [
        {
            "platform": "bluesky",
            "profile_url": "https://bsky.example/profile/typed",
            "as_of": "2026-10-01",
            "collected": True,
        }
    ]
    with pytest.raises(ValidationError):
        KitConfig.model_validate(kit)


def test_feed_kit_reads_the_file_beside_the_numbers_file(tmp_path: Path) -> None:
    write_snapshot(tmp_path)
    config_path = tmp_path / "site.yaml"
    fed_config = feed_kit(kit_config(), config_path, None, TODAY)
    assert fed_config.kit is not None and fed_config.kit.fediverse[0].collected
    elsewhere = tmp_path / "other"
    elsewhere.mkdir()
    same = feed_kit(kit_config(), config_path, elsewhere / "n.json", TODAY)
    assert same.kit is not None and not same.kit.fediverse[0].collected
    inside = feed_kit(kit_config(), config_path, None, TODAY, published=tmp_path)
    assert inside.kit is not None and not inside.kit.fediverse[0].collected


# --- the egress allow-list check -------------------------------------------------


def test_required_and_missing_endpoints() -> None:
    assert required_endpoints(SOURCES) == (
        "public.api.bsky.app:443",
        "social.example:443",
    )
    allowed = "public.api.bsky.app:443,\n  github.com:443 social.example:443"
    assert missing_endpoints(SOURCES, allowed) == ()
    assert missing_endpoints(SOURCES, "public.api.bsky.app:443") == (
        "social.example:443",
    )
    # no wildcard or suffix match
    assert missing_endpoints(SOURCES, "*.example:443 social.example:80 x") == (
        "public.api.bsky.app:443",
        "social.example:443",
    )
    assert missing_endpoints([], "") == ()


def test_the_egress_check_command_exit_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_config(tmp_path)
    allowed = tmp_path / "allowed.txt"
    allowed.write_text("public.api.bsky.app:443\nsocial.example:443\n")
    assert run_egress_check(config, allowed) == 0
    allowed.write_text("public.api.bsky.app:443\n")
    assert run_egress_check(config, allowed) == 3
    assert "missing from the allow-list: social.example:443" in capsys.readouterr().err
    assert (
        main(
            [
                "counts",
                "egress-check",
                "--config",
                str(config),
                "--allowed",
                str(allowed),
            ]
        )
        == 3
    )
    assert run_egress_check(config, tmp_path / "nope.txt") == 2


# --- one unit: the snapshot, history, numbers and status (Daybreak F1, F2) -------


class Writes:
    """A fake for ``write_atomic`` that fails chosen writes and counts the rest.

    Writes are named by file: ``snapshot`` (the staged copy of the snapshot file),
    ``numbers`` and ``status``; ``fail`` gets that name and how many writes of it
    came before.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, fail: Any = None) -> None:
        self.real = write_atomic
        self.fail = fail
        self.seen: dict[str, int] = {}
        monkeypatch.setattr(counts_cmd, "write_atomic", self)

    def __call__(self, path: Path, text: str, **kwargs: Any) -> None:
        name = (
            "snapshot"
            if path.name.endswith(".staged") or path.name.endswith("fediverse.json")
            else path.name.removesuffix(".json").removeprefix("numbers.")
        )
        before = self.seen.get(name, 0)
        self.seen[name] = before + 1
        if self.fail is not None and self.fail(name, before):
            raise OSError(28, "No space left on device")
        self.real(path, text, **kwargs)


def tree(root: Path) -> dict[str, bytes]:
    """Every file of the instance folder by relative name (stray temp files too)."""
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name not in ("site.yaml", ".lock")
    }


def second_day(root: Path, likes: int, days: int = 1) -> int:
    """A later run with ``likes`` as the post's count: the next UTC day by
    default; ``days=0`` is the same UTC day (the same-day rule applies)."""
    moment = NOW + timedelta(days=days)
    page = {"feed": [bsky_post(0, NOW - timedelta(days=1), likes)]}
    date_next = {"date": moment.strftime("%a, %d %b %Y %H:%M:%S GMT")}
    transport = FakeTransport(
        {
            PROFILE: reply(
                {"did": DID, "followersCount": 400, "postsCount": 90},
                headers=date_next,
            ),
            FEED: reply(page, headers=date_next),
            "https://social.example/": reply({"error": "down"}, status=503),
        }
    )
    return run_refresh(
        root / "site.yaml",
        None,
        None,
        False,
        env={},
        transport=transport,
        now=lambda: moment,
    )


def committed(root: Path) -> object:
    return json.loads((root / "numbers.status.json").read_text())["committed"]


def test_a_failed_numbers_write_after_the_commit_is_repaired_by_the_next_run(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert refresh(instance, good_transport()) == 3
    snap = instance / "numbers.fediverse.json"
    numbers_before = (instance / "numbers.json").read_bytes()
    Writes(monkeypatch, lambda name, n: name == "numbers")
    assert second_day(instance, likes=77) == 1  # the write failed
    # the commit point (the snapshot rename) was passed: the snapshot is the new
    # one, its history row is there, the numbers file is the old one, no commit
    assert load_snapshots(snap)["bluesky"]["interactions"] == [
        {"boosts": 1, "favorites": 77, "replies": 2}
    ]
    assert (instance / "numbers.json").read_bytes() == numbers_before
    assert not [p for p in tree(instance) if p.endswith((".staged", ".tmp"))]
    assert committed(instance) is False  # never claims the run
    # a later run settles everything: history has one row per metric and day,
    # each day with the value of the snapshot committed that day
    monkeypatch.undo()
    monkeypatch.setattr(history_mod, "datetime", _FrozenDatetime)  # undo took it
    monkeypatch.setattr(counts_cmd, "default_sleep", lambda seconds: None)
    assert second_day(instance, likes=77) == 3
    assert load_snapshots(snap)["bluesky"]["interactions"] == [
        {"boosts": 1, "favorites": 77, "replies": 2}
    ]
    records = read_history(instance / "history" / "social.jsonl").records
    keys = [(r.platform, r.metric, r.as_of_utc[:10]) for r in records]
    assert len(keys) == len(set(keys))
    assert {r.value for r in records if r.metric == "favorites_30d"} == {9, 77}
    assert committed(instance) is True


def test_a_failed_snapshot_stage_changes_nothing(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert refresh(instance, good_transport()) == 3
    before = tree(instance)
    Writes(monkeypatch, lambda name, n: name == "snapshot")
    assert second_day(instance, likes=77) == 1
    assert tree(instance) == before  # snapshot, history, numbers and status alike


def test_a_failed_history_append_after_the_commit_is_repaired_by_the_next_run(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert refresh(instance, good_transport()) == 3
    history = instance / "history" / "social.jsonl"
    before = history.read_bytes()
    real = append_records

    def refuse(path: Path, rows: Any, **kwargs: Any) -> int:
        if rows[0].as_of_utc.startswith("2026-11-27"):
            raise HistoryError("disk")
        return real(path, rows, **kwargs)

    monkeypatch.setattr(counts_cmd, "append_records", refuse)
    assert second_day(instance, likes=77) == 1
    # committed, but no row for the new snapshot yet and the status says so
    assert history.read_bytes() == before
    assert committed(instance) is False
    assert not [p for p in tree(instance) if p.endswith((".staged", ".tmp"))]
    monkeypatch.undo()
    monkeypatch.setattr(history_mod, "datetime", _FrozenDatetime)  # undo took it
    monkeypatch.setattr(counts_cmd, "default_sleep", lambda seconds: None)
    assert second_day(instance, likes=77) == 3
    rows = read_history(history).records
    assert sorted(r.value for r in rows if r.metric == "favorites_30d") == [9, 77]
    assert committed(instance) is True


def test_a_failed_snapshot_rename_keeps_the_old_snapshot_and_no_commit_claim(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert refresh(instance, good_transport()) == 3
    snap = instance / "numbers.fediverse.json"
    before = snap.read_bytes()
    real_replace = os.replace

    def replace(src: str, dst: str, **kwargs: Any) -> None:
        if dst == snap.name and src.endswith(".staged"):
            raise OSError(5, "I/O error")
        real_replace(src, dst, **kwargs)

    monkeypatch.setattr("reach_around.counts.files.os.replace", replace)
    assert second_day(instance, likes=77) == 1
    assert snap.read_bytes() == before
    assert not [p for p in tree(instance) if p.endswith((".staged", ".tmp"))]
    assert committed(instance) is False
    # nothing else moved on: no row for the snapshot that did not land
    rows = read_history(instance / "history" / "social.jsonl").records
    assert {r.value for r in rows if r.metric == "favorites_30d"} == {9}


def test_the_status_says_committed_only_after_every_write_landed(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writes = Writes(monkeypatch)
    assert refresh(instance, good_transport()) == 3
    assert writes.seen == {"snapshot": 1, "status": 2, "numbers": 1}
    assert committed(instance) is True
    # the closing status write fails: the data landed, the status stays honest
    monkeypatch.undo()
    monkeypatch.setattr(history_mod, "datetime", _FrozenDatetime)  # undo took it
    monkeypatch.setattr(counts_cmd, "default_sleep", lambda seconds: None)
    Writes(monkeypatch, lambda name, n: name == "status" and n == 1)
    assert second_day(instance, likes=77) == 1
    assert committed(instance) is False
    assert load_snapshots(instance / "numbers.fediverse.json")["bluesky"][
        "interactions"
    ] == [{"boosts": 1, "favorites": 77, "replies": 2}]


def test_no_status_claims_a_commit_when_every_later_status_write_fails(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert refresh(instance, good_transport()) == 3
    Writes(
        monkeypatch,
        lambda name, n: name == "numbers" or (name == "status" and n >= 1),
    )
    assert second_day(instance, likes=77) == 1
    assert committed(instance) is False  # written false before the data, never true


# --- the schema says what it can (Daybreak F3) -------------------------------------


def test_the_schema_carries_the_constraints_it_can_express() -> None:
    import re

    from reach_around.schema import build_schema

    defs = build_schema()["$defs"]
    listing = defs["NumbersConfig"]["properties"]["fediverse"]
    assert listing["minItems"] == 1 and listing["maxItems"] == 2
    assert listing["uniqueItems"] is True
    actor = defs["BlueskyFediverseSource"]["properties"]["actor"]
    host = defs["MastodonFediverseSource"]["properties"]["host"]
    acct = defs["MastodonFediverseSource"]["properties"]["acct"]
    ident = defs["MastodonFediverseSource"]["properties"]["account_id"]
    for prop in (actor, host, acct, ident):
        assert prop["maxLength"] and prop["pattern"]
    # whatever the run-time checks accept, the schema accepts too
    accepted = {
        "actor": ["alice.example.com", DID, "did:web:example.com%3A8080:u:alice"],
        "host": ["social.example", "xn--mnchen-3ya.example", "a-b.c-d.example"],
        "acct": ["a", "alice", "a.b-c_d", "_x_", "a" * 30],
        "id": ["0", "109", "9" * 20],
    }
    for value in accepted["actor"]:
        BlueskyFediverseSource(kind="bluesky", actor=value)
        assert re.fullmatch(actor["pattern"], value) and len(value) <= 512
    for value in accepted["host"]:
        assert re.fullmatch(host["pattern"], value) and len(value) <= 253
    for value in accepted["acct"]:
        assert re.fullmatch(acct["pattern"], value) and len(value) <= 30
    for value in accepted["id"]:
        assert re.fullmatch(ident["pattern"], value)
    # and it refuses what is plainly wrong, as the run-time checks do
    for value in ("@alice", "a b", "https://x", "a/b", ""):
        assert not re.fullmatch(actor["pattern"], value)
    for value in ("Social.Example", "a b", "x:443", "x/y", "a@b"):
        assert not re.fullmatch(host["pattern"], value)
    for value in ("@a", "a b", ".a", "a.", "a" * 31, ""):
        assert not (re.fullmatch(acct["pattern"], value) and len(value) <= 30)
    for value in ("01", "-1", "1" * 21, "1.5", ""):
        assert not re.fullmatch(ident["pattern"], value)
    # the integer form is bounded the same way (the string keywords do not apply
    # to a number): a whole number from 0 to the largest 20-digit id
    assert {"type": "integer"} in ident["anyOf"]
    assert ident["minimum"] == 0 and ident["maximum"] == 10**20 - 1
    low, high = ident["minimum"], ident["maximum"]
    for number in (0, 109, 10**20 - 1):
        assert low <= number <= high
        assert MastodonFediverseSource(
            kind="mastodon", host="social.example", acct="a", account_id=number
        )
    for number in (-1, 10**20, 10**42):
        assert not low <= number <= high
        with pytest.raises(ValidationError):
            MastodonFediverseSource(
                kind="mastodon", host="social.example", acct="a", account_id=number
            )


# --- the full claims behind the security tests (Daybreak F4) -----------------------


IDENTIFYING = (
    "alice",
    DID,
    "social.example",
    "example.com",
    "at://",
    "http",
    "hello",
    "friend",
    "3jt4w2fyy4s",
    "createdAt",
    "indexedAt",
    "2026-11-25",  # a post's own date
    ".000Z",  # a post's timestamp
    "author",
    "handle",
)


def test_no_identifying_string_reaches_any_file_the_run_writes(
    instance: Path,
) -> None:
    assert refresh(instance, good_transport()) == 3
    files = tree(instance)
    assert "numbers.fediverse.json" in files and "history/social.jsonl" in files
    for name, data in files.items():
        if name == "site.yaml":
            continue
        text = data.decode("utf-8")
        for token in IDENTIFYING:
            assert token not in text, (name, token)
    body = json.loads(files["numbers.fediverse.json"])
    assert set(body) == {"version", "platforms"}
    snapshot = body["platforms"]["bluesky"]
    assert set(snapshot) == {
        "platform",
        "as_of",
        "complete",
        "followers",
        "posts",
        "interactions",
    }
    assert all(
        set(row) == {"boosts", "favorites", "replies"}
        for row in snapshot["interactions"]
    )


HOSTILE_ROW = {"boosts": 1, "favorites": 2, "replies": 3}


DEEP = 900  # above any sane depth, below the recursion limit of Python 3.11 (1000)


def deep_text(good: dict[str, object]) -> str:
    """A snapshot file whose ``interactions`` nest ``DEEP`` levels. The nesting is
    built as text by repetition: nothing recursive ever meets the JSON encoder, so
    it builds the same on every supported Python at collection time."""
    marker = "@@DEEP@@"
    text = json.dumps(
        {"version": 1, "platforms": {"bluesky": {**good, "interactions": marker}}}
    )
    return text.replace(f'"{marker}"', "[" * DEEP + "1" + "]" * DEEP)


def test_the_deep_hostile_fixture_builds_without_recursion() -> None:
    text = deep_text(stored_snapshot())
    assert text.count("[") >= DEEP and DEEP < 1000
    # it is a JSON document (the decoder may refuse its depth, never the builder)
    try:
        json.loads(text)
    except RecursionError:
        pass


def hostile_files() -> dict[str, str]:
    good = stored_snapshot()

    def doc(platforms: object, **root: object) -> str:
        return json.dumps({"version": 1, "platforms": platforms, **root})

    out = {
        "snapshot_identity": doc({"bluesky": {**good, "uri": "at://x/y/z"}}),
        "row_text": doc(
            {"bluesky": {**good, "interactions": [{**HOSTILE_ROW, "text": "hi"}]}}
        ),
        "row_time": doc(
            {
                "bluesky": {
                    **good,
                    "interactions": [{**HOSTILE_ROW, "createdAt": "2026-11-25"}],
                }
            }
        ),
        "future_date": doc({"bluesky": {**good, "as_of": "2999-01-01"}}),
        "old_date": doc({"bluesky": {**good, "as_of": "1900-01-01"}}),
        "deep_nesting": deep_text(good),
        "not_a_mapping": doc(["bluesky"]),
        "wrong_version": json.dumps({"version": 2, "platforms": {"bluesky": good}}),
        "padding": json.dumps({"version": 1, "platforms": {}, "pad": "x" * 600_000}),
        "unknown_platform": doc(
            {"pixelfed": good, "bluesky": {**good, "platform": "x"}}
        ),
        "incomplete": doc({"bluesky": {**good, "complete": False}}),
        "damaged": '{"version": 1, "platforms": {"bluesky": ',
    }
    return out


@pytest.mark.parametrize("name", sorted(hostile_files()))
def test_a_hostile_snapshot_file_never_reaches_the_kit_page(
    tmp_path: Path, name: str
) -> None:
    path = snapshot_path(tmp_path / "numbers.json")
    path.write_text(hostile_files()[name], encoding="utf-8")
    base = kit_config()
    assert base.kit is not None
    kept = feed_kit(base, tmp_path / "site.yaml", None, TODAY)
    assert kept.kit is not None
    # the hand-kept entry is exactly what it was, and nothing is labeled collected
    assert kept.kit.fediverse == base.kit.fediverse
    assert not any(e.collected for e in kept.kit.fediverse)
    labels = {m.trust.label for m in fediverse_measures(kept.kit.fediverse) if m.trust}
    assert labels == {"hand-kept"}
    # and nothing it carried is in the rendered model of the kit block
    assert "alice" not in kept.kit.model_dump_json()
    assert "x" * 100 not in kept.kit.model_dump_json()


def test_a_valid_entry_survives_next_to_an_unexpected_platform(tmp_path: Path) -> None:
    good = stored_snapshot()
    path = snapshot_path(tmp_path / "numbers.json")
    path.write_text(
        json.dumps({"version": 1, "platforms": {"bluesky": good, "pixelfed": good}}),
        encoding="utf-8",
    )
    assert set(load_snapshots(path)) == {"bluesky"}
    # a stray key at the root is never read: nothing of it reaches the result
    path.write_text(
        json.dumps(
            {"version": 1, "platforms": {"bluesky": good}, "handle": "alice.example"}
        ),
        encoding="utf-8",
    )
    assert "alice" not in json.dumps(load_snapshots(path))


def test_the_collected_flag_cannot_be_set_from_config_by_any_path(
    tmp_path: Path,
) -> None:
    from reach_around.config.fediverse import FediverseEntry

    entry: dict[str, Any] = {
        "platform": "bluesky",
        "profile_url": "https://bsky.example/profile/typed",
        "as_of": "2026-10-01",
    }
    for flag in ("collected", "_collected"):
        hostile = {**entry, flag: True}
        with pytest.raises(ValidationError):
            FediverseEntry.model_validate(hostile)
        with pytest.raises(ValidationError):
            FediverseEntry.model_validate_json(json.dumps(hostile))
        with pytest.raises(ValidationError):
            FediverseEntry(**hostile)
        kit = kit_doc()
        kit["fediverse"] = [hostile]
        with pytest.raises(ValidationError):
            KitConfig.model_validate(kit)
    plain = FediverseEntry.model_validate(entry)
    assert plain.collected is False
    # a copy, a dump and a re-read never turn the flag on either
    assert plain.model_copy(update={"show_mean": True}).collected is False
    assert "collected" not in plain.model_dump()
    assert "_collected" not in plain.model_dump_json()
    collected = apply_collected(kit_config(), write_snapshot(tmp_path), TODAY)
    assert collected.kit is not None and collected.kit.fediverse[0].collected
    dumped = collected.kit.fediverse[0].model_dump(mode="json")
    assert "collected" not in dumped and "_collected" not in dumped
    assert FediverseEntry.model_validate(dumped).collected is False
    assert (
        FediverseEntry.model_validate_json(
            collected.kit.fediverse[0].model_dump_json()
        ).collected
        is False
    )


def test_dry_run_and_egress_check_never_print_the_account(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_acct, secret_actor = "zorblat", "quuxer.example.com"
    config = write_config(
        tmp_path,
        "version: 1\n"
        "creator: {name: Example Creator}\n"
        "tabs:\n  - id: start\n    title: Start\n    blocks:\n"
        "      - {id: one, title: One, url: 'https://example.com/1'}\n"
        "numbers:\n  fediverse:\n"
        f"    - {{kind: bluesky, actor: {secret_actor}}}\n"
        f"    - {{kind: mastodon, host: social.example, acct: {secret_acct}, "
        "account_id: '4242'}\n",
    )
    transport = good_transport()
    assert run_refresh(config, None, None, True, env={}, transport=transport) == 0
    assert transport.calls == []
    allowed = tmp_path / "allowed.txt"
    for text in ("public.api.bsky.app:443\nsocial.example:443\n", "x:443\n", ""):
        allowed.write_text(text)
        run_egress_check(config, allowed)
        main(
            [
                "counts",
                "egress-check",
                "--config",
                str(config),
                "--allowed",
                str(allowed),
            ]
        )
    run_egress_check(config, tmp_path / "missing.txt")
    seen = capsys.readouterr()
    shown = seen.out + seen.err
    assert "social.example:443" in shown and "public.api.bsky.app:443" in shown
    for name in (secret_acct, secret_actor, "zorblat", "quuxer", "4242"):
        assert name not in shown
