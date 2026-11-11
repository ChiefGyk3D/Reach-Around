# SPDX-License-Identifier: Apache-2.0
"""Public media-kit recent posts use only local synthetic thumbnails."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from assetpaths import asset_file
from reach_around.config import SiteConfig
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
IEND = b"\x00\x00\x00\x00IEND\xaeB`\x82"
PNG = b"\x89PNG\r\n\x1a\nsynthetic recent post" + IEND


def config(recent_posts: list[dict[str, Any]]) -> SiteConfig:
    return SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "Example Creator"},
            "tabs": [
                {
                    "id": "start",
                    "title": "Start",
                    "blocks": [
                        {
                            "id": "one",
                            "title": "One",
                            "url": "https://example.com/one",
                        }
                    ],
                }
            ],
            "kit": {
                "title": "Example Creator media kit",
                "about": "A synthetic creator profile.",
                "recent_posts": recent_posts,
            },
        }
    )


def post(platform: str, thumbnail: str, **extra: int) -> dict[str, Any]:
    return {
        "platform": platform,
        "thumbnail": thumbnail,
        "views": 1234,
        "likes": 56,
        "comments": 7,
        **extra,
    }


def test_public_kit_groups_recent_posts_with_local_thumbnails_and_counts(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    (instance / "media").mkdir(parents=True)
    (instance / "media" / "clip.png").write_bytes(PNG)
    (instance / "media" / "thread.png").write_bytes(PNG)

    out = tmp_path / "public"
    build_site(
        config(
            [
                post("tiktok", "media/clip.png"),
                post(
                    "threads",
                    "media/thread.png",
                    reposts=4,
                    quotes=2,
                ),
            ]
        ),
        out,
        today=TODAY,
        assets_dir=instance,
    )

    page = (out / "kit" / "index.html").read_text(encoding="utf-8")
    assert '<section class="kit-recent" aria-labelledby="kit-recent-title">' in page
    assert page.count('class="kit-posts"') == 2
    assert "TikTok" in page and "Threads" in page
    assert 'alt="TikTok recent post thumbnail"' in page
    assert "1,234 views" in page
    assert "56 likes" in page
    assert "7 comments" in page
    assert "4 reposts" in page
    assert "2 quotes" in page
    assert (
        "https://"
        not in page.split('class="kit-recent"', 1)[1].split("</section>", 1)[0]
    )
    assert asset_file(out, "media/clip.png").read_bytes() == PNG


def test_recent_post_cards_are_mobile_first_and_images_do_not_hotlink(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    (instance / "media").mkdir(parents=True)
    (instance / "media" / "post.png").write_bytes(PNG)
    out = tmp_path / "public"
    build_site(
        config([post("instagram", "media/post.png")]),
        out,
        today=TODAY,
        assets_dir=instance,
    )

    page = (out / "kit" / "index.html").read_text(encoding="utf-8")
    assert '<img class="kit-post-thumb"' in page
    assert 'loading="lazy"' in page
    assert 'width="320" height="180"' in page
    css = asset_file(out, "static/kit.css").read_text(encoding="utf-8")
    assert ".kit-posts { display: grid; grid-template-columns: 1fr;" in css
    assert "@media (min-width: 40rem)" in css
