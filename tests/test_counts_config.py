# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest

from reach_around.config import ConfigError, SiteConfig, load_site_config

BASE = """\
version: 1
creator: {name: Test Creator}
tabs:
  - id: home
    title: Home
    blocks: []
"""


def load(tmp_path: Path, numbers: str) -> SiteConfig:
    path = tmp_path / "site.yaml"
    path.write_text(BASE + numbers, encoding="utf-8")
    return load_site_config(path)


def fails(tmp_path: Path, numbers: str) -> str:
    with pytest.raises(ConfigError) as caught:
        load(tmp_path, numbers)
    return str(caught.value)


def source(body: str) -> str:
    return "numbers:\n  sources:\n" + "".join(
        f"    {line}\n" for line in body.splitlines()
    )


def test_numbers_is_optional(tmp_path: Path) -> None:
    config = load(tmp_path, "")
    assert config.numbers is None


GOOD = [
    "- {platform: bluesky, kind: bluesky, actor: example.bsky.social}",
    "- {platform: bluesky, kind: bluesky, actor: 'did:plc:abcdefghijklmnopqrstuvwx'}",
    "- {platform: mastodon, kind: mastodon, host: example.social, acct: someone}",
    "- {platform: pixelfed, kind: mastodon, host: pixel.example.org, acct: a.b_c-d}",
    "- {platform: github, kind: github, user: some-user}",
    "- {platform: discord, kind: discord, invite: abc-DEF123}",
    "- {platform: matrix, kind: matrix, room: '#room:example.org'}",
    (
        "- {platform: youtube, kind: youtube, channel_id: UCabcdefghijklmnopqrstuv, "
        "api_key_env: YOUTUBE_API_KEY}"
    ),
    (
        "- {platform: twitch, kind: twitch, login: some_user, "
        "client_id_env: TWITCH_CLIENT_ID, client_secret_env: TWITCH_CLIENT_SECRET}"
    ),
]


@pytest.mark.parametrize("line", GOOD)
def test_valid_sources(tmp_path: Path, line: str) -> None:
    load(tmp_path, source(line))


BAD = [
    ("- {platform: x, kind: tiktok, user: a}", "kind"),
    ("- {platform: x, kind: bluesky, actor: a, extra: 1}", "extra"),
    ("- {platform: x, kind: bluesky}", "actor"),
    ("- {platform: X, kind: github, user: a}", "platform"),
    ("- {platform: x, kind: bluesky, actor: 'not a handle'}", "actor"),
    ("- {platform: x, kind: bluesky, actor: 'a/b.example'}", "actor"),
    ("- {platform: x, kind: mastodon, host: 127.0.0.1, acct: a}", "host"),
    ("- {platform: x, kind: mastodon, host: localhost, acct: a}", "host"),
    ("- {platform: x, kind: mastodon, host: '169.254.169.254', acct: a}", "host"),
    ("- {platform: x, kind: mastodon, host: 'example.social:8443', acct: a}", "host"),
    ("- {platform: x, kind: mastodon, host: 'example.social/x', acct: a}", "host"),
    ("- {platform: x, kind: mastodon, host: example.social, acct: '@a'}", "acct"),
    ("- {platform: x, kind: mastodon, host: example.social, acct: 'a b'}", "acct"),
    ("- {platform: x, kind: github, user: '-bad'}", "user"),
    ("- {platform: x, kind: github, user: 'a/../b'}", "user"),
    ("- {platform: x, kind: discord, invite: 'https://discord.gg/abc'}", "invite"),
    ("- {platform: x, kind: matrix, room: 'room:example.org'}", "room"),
    ("- {platform: x, kind: matrix, room: '#room:127.0.0.1'}", "room"),
    ("- {platform: x, kind: matrix, room: '#room:example.org:8448'}", "room"),
    ("- {platform: x, kind: matrix, room: '#room:localhost'}", "room"),
    (
        (
            "- {platform: x, kind: youtube, channel_id: short, "
            "api_key_env: YOUTUBE_API_KEY}"
        ),
        "channel_id",
    ),
    (
        (
            "- {platform: x, kind: youtube, channel_id: UCabcdefghijklmnopqrstuv, "
            "api_key_env: lower}"
        ),
        "api_key_env",
    ),
    (
        "- {platform: x, kind: youtube, channel_id: UCabcdefghijklmnopqrstuv}",
        "api_key_env",
    ),
    (
        (
            "- {platform: x, kind: youtube, channel_id: UCabcdefghijklmnopqrstuv, "
            "api_key: AIzaSECRET}"
        ),
        "api_key",
    ),
    (
        (
            "- {platform: x, kind: twitch, login: a, client_id_env: A_B, "
            "client_secret_env: C_D}"
        ),
        "login",
    ),
    (
        (
            "- {platform: x, kind: twitch, login: some_user, client_id_env: SAME, "
            "client_secret_env: SAME}"
        ),
        "differ",
    ),
    (
        (
            "- {platform: x, kind: twitch, login: some_user, client_id_env: A_B, "
            "client_secret: shh}"
        ),
        "client_secret",
    ),
]


@pytest.mark.parametrize(("line", "needle"), BAD)
def test_invalid_sources_are_refused(tmp_path: Path, line: str, needle: str) -> None:
    assert needle in fails(tmp_path, source(line))


def test_empty_sources_and_duplicate_platforms_refused(tmp_path: Path) -> None:
    assert fails(tmp_path, "numbers:\n  sources: []\n")
    two = source(
        "- {platform: a, kind: github, user: u1}\n- {platform: a, kind: github, "
        "user: u2}"
    )
    assert "two sources" in fails(tmp_path, two)


def test_too_many_sources_refused(tmp_path: Path) -> None:
    many = source(
        "\n".join(f"- {{platform: p{i}, kind: github, user: u}}" for i in range(51))
    )
    assert fails(tmp_path, many)


def test_unknown_numbers_key_refused(tmp_path: Path) -> None:
    assert fails(
        tmp_path,
        "numbers:\n  sources:\n    - {platform: a, kind: github, user: u}\n  x: 1\n",
    )
