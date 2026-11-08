# SPDX-License-Identifier: Apache-2.0
"""The ``live`` block: two co-equal players, link-outs, a note and VOD links."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from reach_around.config import ConfigError, SiteConfig, load_site_config
from reach_around.config.blocks import Live


def live_block() -> dict[str, Any]:
    return {
        "id": "live",
        "players": [
            {"platform": "twitch", "channel": "example_twitch"},
            {"platform": "kick", "channel": "example_kick"},
        ],
        "also_on": [
            {"platform": "youtube", "url": "https://www.youtube.com/@example"},
            {"platform": "tiktok", "url": "https://www.tiktok.com/@example"},
            {"platform": "instagram", "url": "https://www.instagram.com/example"},
        ],
        "vods": {
            "youtube": "https://www.youtube.com/@example/streams",
            "kick": "https://kick.com/example_kick/videos",
            "twitch": "https://www.twitch.tv/example_twitch/videos",
        },
    }


def config(block: dict[str, Any] | None = None, **top: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example Creator", "socials": []},
        "tabs": [
            {
                "id": "main",
                "title": "Main",
                "blocks": [
                    {"id": "first", "title": "First", "url": "https://example.com/"}
                ],
            },
            {
                "id": "watch",
                "title": "Watch",
                "blocks": [block if block is not None else live_block()],
            },
        ],
    }
    data.update(top)
    return data


def load(tmp_path: Path, data: object) -> SiteConfig:
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return load_site_config(path)


def refused(tmp_path: Path, data: object) -> str:
    with pytest.raises(ConfigError) as info:
        load(tmp_path, data)
    return str(info.value)


def with_block(**changes: Any) -> dict[str, Any]:
    block = live_block()
    block.update(changes)
    return config(block)


def test_good_live_block_loads(tmp_path: Path) -> None:
    loaded = load(tmp_path, config())
    block = loaded.tabs[1].blocks[0]
    assert isinstance(block, Live)
    assert [p.platform for p in block.players] == ["twitch", "kick"]
    assert block.fediverse_note == "Fediverse coming soon"
    assert block.vods is not None


def test_minimal_block_loads(tmp_path: Path) -> None:
    block = {"id": "live", "players": live_block()["players"]}
    loaded = load(tmp_path, config(block))
    live = loaded.tabs[1].blocks[0]
    assert isinstance(live, Live)
    assert live.also_on == []
    assert live.vods is None


def test_player_order_is_kept_as_written(tmp_path: Path) -> None:
    block = live_block()
    block["players"].reverse()
    live = load(tmp_path, config(block)).tabs[1].blocks[0]
    assert isinstance(live, Live)
    assert [p.platform for p in live.players] == ["kick", "twitch"]


def test_note_can_be_changed_or_cleared(tmp_path: Path) -> None:
    live = load(tmp_path, with_block(fediverse_note="Mastodon soon")).tabs[1].blocks[0]
    assert isinstance(live, Live)
    assert live.fediverse_note == "Mastodon soon"
    cleared = load(tmp_path, with_block(fediverse_note=None)).tabs[1].blocks[0]
    assert isinstance(cleared, Live)
    assert cleared.fediverse_note is None


# --- players ------------------------------------------------------------------


def test_one_player_only_is_refused(tmp_path: Path) -> None:
    for keep in (0, 1):
        players = [live_block()["players"][keep]]
        assert "players" in refused(tmp_path, with_block(players=players))


def test_no_players_is_refused(tmp_path: Path) -> None:
    assert refused(tmp_path, with_block(players=[]))


def test_three_players_is_refused(tmp_path: Path) -> None:
    players = live_block()["players"] + [{"platform": "twitch", "channel": "other"}]
    assert refused(tmp_path, with_block(players=players))


def test_duplicate_platform_is_refused(tmp_path: Path) -> None:
    twitch = {"platform": "twitch", "channel": "example_twitch"}
    assert "both" in refused(tmp_path, with_block(players=[twitch, dict(twitch)]))


def test_other_platform_as_player_is_refused(tmp_path: Path) -> None:
    players = [
        {"platform": "twitch", "channel": "example_twitch"},
        {"platform": "youtube", "channel": "example_kick"},
    ]
    assert refused(tmp_path, with_block(players=players))


@pytest.mark.parametrize(
    "channel",
    [
        "",
        "a" * 26,
        "has-dash",
        "has space",
        "has.dot",
        "https://twitch.tv/example",
        "twitch.tv/example",
        "example\n",
        "exämple",
        "example/",
        "<b>x</b>",
        "ex?x=1",
        "ex#frag",
    ],
)
def test_bad_twitch_channel_is_refused(tmp_path: Path, channel: str) -> None:
    players = live_block()["players"]
    players[0]["channel"] = channel
    assert refused(tmp_path, with_block(players=players))


@pytest.mark.parametrize(
    "channel",
    [
        "",
        "a" * 41,
        "has space",
        "has.dot",
        "https://kick.com/example",
        "kick.com/example",
        "example\n",
        "exämple",
        "example/",
        "ex?x=1",
        "ex#frag",
        "a@b",
    ],
)
def test_bad_kick_channel_is_refused(tmp_path: Path, channel: str) -> None:
    players = live_block()["players"]
    players[1]["channel"] = channel
    assert refused(tmp_path, with_block(players=players))


def test_handle_limits_are_exact(tmp_path: Path) -> None:
    players = live_block()["players"]
    players[0]["channel"] = "a" * 25
    players[1]["channel"] = "a-b_" + "c" * 36
    load(tmp_path, with_block(players=players))


def test_non_string_channel_is_refused(tmp_path: Path) -> None:
    for value in (123, None, ["x"], True):
        players = live_block()["players"]
        players[0]["channel"] = value
        assert refused(tmp_path, with_block(players=players))


def test_extra_player_key_is_refused(tmp_path: Path) -> None:
    players = live_block()["players"]
    players[0]["url"] = "https://evil.example/"
    assert refused(tmp_path, with_block(players=players))


# --- also_on ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "url"),
    [
        ("youtube", "https://evil.example/watch"),
        ("youtube", "https://youtube.com.evil.example/x"),
        ("youtube", "https://evilyoutube.com/x"),
        ("youtube", "https://www.tiktok.com/@x"),
        ("youtube", "http://www.youtube.com/@x"),
        ("youtube", "//www.youtube.com/@x"),
        ("youtube", "https://user:pw@www.youtube.com/@x"),
        ("youtube", "https://www.youtube.com@evil.example/"),
        ("youtube", "https://www.youtube.com:8443/@x"),
        ("youtube", "https://www.youtube.com\\@evil.example/"),
        ("youtube", "https://www.youtube.com./x"),
        ("youtube", "javascript:alert(1)"),
        ("youtube", "tab:main"),
        ("youtube", "https://www.youtube.com/a b"),
        ("tiktok", "https://www.instagram.com/x"),
        ("instagram", "https://www.tiktok.com/@x"),
        ("instagram", "https://instagram.com.evil.example/x"),
    ],
)
def test_also_on_wrong_host_is_refused(tmp_path: Path, platform: str, url: str) -> None:
    assert refused(tmp_path, with_block(also_on=[{"platform": platform, "url": url}]))


@pytest.mark.parametrize(
    ("platform", "url"),
    [
        ("youtube", "https://youtube.com/@x"),
        ("youtube", "https://m.youtube.com/@x"),
        ("youtube", "https://youtu.be/abc"),
        ("youtube", "https://WWW.YouTube.com/@x"),
        ("tiktok", "https://tiktok.com/@x"),
        ("instagram", "https://instagram.com/x"),
    ],
)
def test_also_on_own_hosts_are_accepted(
    tmp_path: Path, platform: str, url: str
) -> None:
    load(tmp_path, with_block(also_on=[{"platform": platform, "url": url}]))


def test_also_on_other_platform_is_refused(tmp_path: Path) -> None:
    for platform in ("twitch", "kick", "mastodon", "other"):
        block = {"platform": platform, "url": "https://www.youtube.com/@x"}
        assert refused(tmp_path, with_block(also_on=[block]))


def test_also_on_duplicate_platform_is_refused(tmp_path: Path) -> None:
    item = {"platform": "youtube", "url": "https://www.youtube.com/@x"}
    assert refused(tmp_path, with_block(also_on=[item, dict(item)]))


def test_also_on_extra_key_is_refused(tmp_path: Path) -> None:
    item = {"platform": "youtube", "url": "https://www.youtube.com/@x", "embed": True}
    assert refused(tmp_path, with_block(also_on=[item]))


def test_also_on_too_many_is_refused(tmp_path: Path) -> None:
    items = [{"platform": "youtube", "url": "https://www.youtube.com/@x"}] * 4
    assert refused(tmp_path, with_block(also_on=items))


# --- fediverse note -----------------------------------------------------------


@pytest.mark.parametrize(
    "note",
    [
        "",
        "x" * 81,
        "see https://example.com",
        "see example.com",
        "mail me@example.com",
        "<b>soon</b>",
        "a\nb",
        "bidi‮gone",
        "zero​width",
        "www.example.org",
        "javascript:alert(1)",
        "//evil.example",
        "back\\slash",
        123,
        ["x"],
    ],
)
def test_bad_note_is_refused(tmp_path: Path, note: object) -> None:
    assert refused(tmp_path, with_block(fediverse_note=note))


# --- vods ---------------------------------------------------------------------


def test_vods_need_all_three(tmp_path: Path) -> None:
    for drop in ("youtube", "kick", "twitch"):
        vods = dict(live_block()["vods"])
        del vods[drop]
        assert drop in refused(tmp_path, with_block(vods=vods))


def test_empty_vods_is_refused(tmp_path: Path) -> None:
    assert refused(tmp_path, with_block(vods={}))


@pytest.mark.parametrize(
    ("which", "url"),
    [
        ("youtube", "https://kick.com/example_kick/videos"),
        ("kick", "https://www.twitch.tv/example_twitch/videos"),
        ("twitch", "https://www.youtube.com/@example/streams"),
        ("twitch", "https://twitch.tv.evil.example/videos"),
        ("kick", "https://evil.example/kick.com"),
        ("twitch", "http://www.twitch.tv/x"),
        ("kick", "https://kick.com:444/x"),
        ("youtube", "https://user@www.youtube.com/x"),
    ],
)
def test_vod_wrong_host_is_refused(tmp_path: Path, which: str, url: str) -> None:
    vods = dict(live_block()["vods"])
    vods[which] = url
    assert refused(tmp_path, with_block(vods=vods))


def test_vods_extra_key_is_refused(tmp_path: Path) -> None:
    vods = dict(live_block()["vods"])
    vods["tiktok"] = "https://www.tiktok.com/@x"
    assert refused(tmp_path, with_block(vods=vods))


# --- the block as a whole -----------------------------------------------------


def test_extra_block_key_is_refused(tmp_path: Path) -> None:
    for key in ("embed_url", "autoplay", "title", "show_from"):
        assert refused(tmp_path, with_block(**{key: "x"}))


def test_duplicate_block_id_is_refused(tmp_path: Path) -> None:
    data = config()
    data["tabs"][0]["blocks"][0]["id"] = "live"
    assert "duplicate" in refused(tmp_path, data)


def test_two_live_blocks_are_refused(tmp_path: Path) -> None:
    data = config()
    second = copy.deepcopy(live_block())
    second["id"] = "live-two"
    data["tabs"][1]["blocks"].append(second)
    assert "one live block" in refused(tmp_path, data)


def test_sections_layout_refuses_live(tmp_path: Path) -> None:
    message = refused(tmp_path, config(page_layout="sections"))
    assert "page_layout" in message
    assert "live" in message


def test_tabs_layout_accepts_live(tmp_path: Path) -> None:
    load(tmp_path, config(page_layout="tabs"))


def test_sections_layout_without_live_still_loads(tmp_path: Path) -> None:
    data = config(page_layout="sections")
    data["tabs"][1]["blocks"] = [{"id": "rule"}]
    load(tmp_path, data)


def test_players_key_is_what_makes_it_a_live_block(tmp_path: Path) -> None:
    block = live_block()
    del block["players"]
    assert refused(tmp_path, config(block))


# --- reach-around check says it in plain words ----------------------------------


def _check_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], data: object
) -> str:
    from reach_around.cli import main

    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    code = main(["check", "--config", str(path)])
    out = capsys.readouterr()
    assert code == 2, out
    return out.out + out.err


@pytest.mark.parametrize(
    ("label", "data", "sentence"),
    [
        (
            "channel",
            with_block(
                players=[
                    {"platform": "twitch", "channel": "a b"},
                    {"platform": "kick", "channel": "example_kick"},
                ]
            ),
            "not a plain channel name",
        ),
        (
            "players",
            with_block(
                players=[live_block()["players"][0]] * 1
                + [{"platform": "twitch", "channel": "other_one"}]
            ),
            "both twitch and kick",
        ),
        (
            "host",
            with_block(
                also_on=[{"platform": "youtube", "url": "https://evil.example/"}]
            ),
            "own site",
        ),
        ("note", with_block(fediverse_note="see a.com"), "fediverse note"),
        ("layout", config(page_layout="sections"), "page_layout: tabs"),
    ],
)
def test_check_names_each_live_problem(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    label: str,
    data: object,
    sentence: str,
) -> None:
    out = _check_text(tmp_path, capsys, data)
    assert sentence in out, (label, out)
    assert "Fix: " in out


# --- the block is rendered now (slice 2) ----------------------------------------


def test_build_renders_a_live_block(tmp_path: Path) -> None:
    from datetime import date

    from reach_around.site.build import build_site

    site = load(tmp_path, config())
    build_site(site, tmp_path / "out", today=date(2027, 1, 1))
    page = (tmp_path / "out" / "watch" / "index.html").read_text(encoding="utf-8")
    assert "block-live" in page


def test_build_cli_builds_a_live_block(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from reach_around.cli import main

    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(config()), encoding="utf-8")
    code = main(["build", "--config", str(path), "--out", str(tmp_path / "out")])
    err = capsys.readouterr()
    assert code == 0, err.err + err.out
    assert (tmp_path / "out" / "watch" / "index.html").is_file()
    assert "configuration only" not in err.err + err.out


def test_check_has_no_live_note_for_a_live_block(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from reach_around.cli import main

    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(config()), encoding="utf-8")
    assert main(["check", "--config", str(path)]) == 0
    out = capsys.readouterr().out
    assert "is valid" in out
    assert "not yet rendered" not in out
    assert "Note:" not in out
