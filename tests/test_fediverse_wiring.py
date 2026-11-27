# SPDX-License-Identifier: Apache-2.0
"""Slice 4 of the fediverse collector (#940): the config model, the refresh
wiring, the history rows, the kit feed and the egress check.

Hermetic: a fake transport or fake collectors stand in for the network, the clock
and the sleep are injected, and every handle, DID and host is a synthetic example.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import reach_around.commands.counts as counts_cmd
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
from reach_around.counts.history import read_history

NOW = datetime(2026, 11, 26, 12, 0, 0, tzinfo=UTC)
TODAY = date(2026, 11, 26)
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
