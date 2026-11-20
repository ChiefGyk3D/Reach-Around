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
    blocks:
      - id: socials
        items:
          - {platform: bluesky, handle: "@example", url: "https://example.com/bluesky"}
          - {platform: mastodon, handle: "@example", url: "https://example.com/mastodon"}
          - {platform: pixelfed, handle: "@example", url: "https://example.com/pixelfed"}
          - {platform: github, handle: "@example", url: "https://example.com/github"}
          - {platform: discord, handle: "@example", url: "https://example.com/discord"}
          - {platform: matrix, handle: "@example", url: "https://example.com/matrix"}
          - {platform: youtube, handle: "@example", url: "https://example.com/youtube"}
          - {platform: twitch, handle: "@example", url: "https://example.com/twitch"}
          - {platform: x, handle: "@example", url: "https://example.com/x"}
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
    ("- {platform: x, kind: kick, user: a}", "kind"),
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
        (
            "- {platform: x, kind: youtube, channel_id: UCabcdefghijklmnopqrstuv, "
            "api_key_env: AWS_SECRET_ACCESS_KEY}"
        ),
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
            "- {platform: x, kind: twitch, login: a, client_id_env: TWITCH_A, "
            "client_secret_env: TWITCH_C}"
        ),
        "login",
    ),
    (
        (
            "- {platform: x, kind: twitch, login: some_user, "
            "client_id_env: TWITCH_SAME, client_secret_env: TWITCH_SAME}"
        ),
        "Input should be",
    ),
    (
        (
            "- {platform: x, kind: twitch, login: some_user, client_id_env: TWITCH_A, "
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


# ---- source keys must match the platforms block -------------------------------------

ONE_PLATFORM = """\
version: 1
creator: {name: Test Creator}
tabs:
  - id: home
    title: Home
    blocks:
      - id: socials
        items:
          - {platform: github, handle: "@x", url: "https://example.com/g"}
          - {platform: github, handle: "@y", url: "https://example.com/g2"}
"""
NO_PLATFORMS_BLOCK = """\
version: 1
creator: {name: Test Creator}
tabs:
  - id: home
    title: Home
    blocks: []
"""


def config_with(tmp_path: Path, head: str, key: str) -> SiteConfig:
    path = tmp_path / "site.yaml"
    entry = f"    - {{platform: {key}, kind: github, user: some-user}}\n"
    path.write_text(head + "numbers:\n  sources:\n" + entry, encoding="utf-8")
    return load_site_config(path)


@pytest.mark.parametrize("key", ["github", "socials-github", "socials-github-2"])
def test_a_source_may_name_a_platform_or_an_item_id(tmp_path: Path, key: str) -> None:
    assert config_with(tmp_path, ONE_PLATFORM, key).numbers is not None


@pytest.mark.parametrize("key", ["twitch", "socials-twitch", "socials", "other"])
def test_a_source_that_matches_no_platforms_item_is_refused(
    tmp_path: Path, key: str
) -> None:
    with pytest.raises(ConfigError) as caught:
        config_with(tmp_path, ONE_PLATFORM, key)
    message = str(caught.value)
    assert "numbers.sources[0].platform" in message and repr(key) in message
    assert "matches no platforms item" in message


def test_a_source_with_no_platforms_block_at_all_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="matches no platforms item"):
        config_with(tmp_path, NO_PLATFORMS_BLOCK, "github")


# --- kit-only sources (show_on_hub: false, #689) -------------------------------

KIT_ONLY_HEAD = """\
version: 1
creator: {name: Test Creator}
features: {kit_gate: false}
tabs:
  - id: home
    title: Home
    blocks:
      - {id: one, title: One, url: "https://example.com/1"}
kit:
  title: Test kit
  about: A synthetic test kit.
  metrics:
    - {platform: twitch, label: Followers, source: counts}
"""
KIT_ONLY_SOURCE = (
    "numbers:\n  sources:\n"
    "    - {platform: twitch, kind: twitch, login: example, show_on_hub: false}\n"
)


def kit_only(tmp_path: Path, head: str = KIT_ONLY_HEAD, tail: str = "") -> SiteConfig:
    path = tmp_path / "site.yaml"
    path.write_text(head + (tail or KIT_ONLY_SOURCE), encoding="utf-8")
    return load_site_config(path)


def test_a_kit_only_source_needs_no_platforms_item(tmp_path: Path) -> None:
    config = kit_only(tmp_path)
    assert config.numbers is not None
    assert config.numbers.sources[0].show_on_hub is False


def test_show_on_hub_defaults_to_true(tmp_path: Path) -> None:
    config = config_with(tmp_path, ONE_PLATFORM, "github")
    assert config.numbers is not None
    assert config.numbers.sources[0].show_on_hub is True


def test_a_kit_only_source_with_no_kit_counts_metric_is_refused(
    tmp_path: Path,
) -> None:
    head = KIT_ONLY_HEAD.replace(
        "source: counts", "source: manual, value: 5, as_of: 2027-03-01"
    )
    head = head.replace("value: 5, as_of: 2027-03-01", "value: 5, as_of: 2027-03-01")
    with pytest.raises(ConfigError) as caught:
        kit_only(tmp_path, head)
    message = str(caught.value)
    assert "numbers.sources[0].platform" in message
    assert "show_on_hub: false needs a kit metric with source: counts" in message
    assert "twitch" not in message and "example" not in message


def test_a_kit_only_source_with_no_kit_at_all_is_refused(tmp_path: Path) -> None:
    head = KIT_ONLY_HEAD.split("kit:\n")[0]
    with pytest.raises(ConfigError, match="show_on_hub: false needs a kit metric"):
        kit_only(tmp_path, head)


@pytest.mark.parametrize("value", ['"false"', "0", "no_such"])
def test_show_on_hub_must_be_a_real_boolean(tmp_path: Path, value: str) -> None:
    tail = KIT_ONLY_SOURCE.replace("show_on_hub: false", f"show_on_hub: {value}")
    with pytest.raises(ConfigError):
        kit_only(tmp_path, tail=tail)


def test_a_hidden_source_may_still_match_a_platforms_item(tmp_path: Path) -> None:
    head = KIT_ONLY_HEAD.replace(
        "blocks:\n",
        "blocks:\n"
        "      - id: socials\n        items:\n"
        '          - {platform: twitch, handle: "@x", url: "https://example.com/t"}\n',
    )
    assert kit_only(tmp_path, head).numbers is not None


# --- kit-only sources are keyed by platform, never by item id (#689) -----------

ITEM_ID_HEAD = KIT_ONLY_HEAD.replace(
    "blocks:\n",
    "blocks:\n"
    "      - id: socials\n        items:\n"
    '          - {platform: twitch, handle: "@x", url: "https://example.com/t"}\n'
    '          - {platform: bluesky, handle: "@x", url: "https://example.com/b"}\n',
)


def test_a_kit_only_source_keyed_by_an_item_id_is_refused_with_fixed_words(
    tmp_path: Path,
) -> None:
    tail = KIT_ONLY_SOURCE.replace("platform: twitch", "platform: socials-twitch")
    with pytest.raises(ConfigError) as caught:
        kit_only(tmp_path, ITEM_ID_HEAD, tail)
    message = str(caught.value)
    assert "numbers.sources[0].platform" in message
    assert "show_on_hub: false must be keyed by a platform, not an item id" in message
    assert "socials-twitch" not in message and "example" not in message


def test_a_visible_item_id_source_beside_a_kit_only_platform_source_loads(
    tmp_path: Path,
) -> None:
    tail = (
        "numbers:\n  sources:\n"
        "    - {platform: twitch, kind: twitch, login: example, show_on_hub: false}\n"
        "    - {platform: socials-bluesky, kind: bluesky, actor: a.example.com}\n"
    )
    config = kit_only(tmp_path, ITEM_ID_HEAD, tail)
    assert config.numbers is not None and len(config.numbers.sources) == 2
