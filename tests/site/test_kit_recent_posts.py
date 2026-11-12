# SPDX-License-Identifier: Apache-2.0
"""Public media-kit recent posts use only local synthetic thumbnails."""

from __future__ import annotations

import struct
import zlib
from datetime import date
from pathlib import Path
from typing import Any

from assetpaths import asset_file
from reach_around.config import SiteConfig
from reach_around.kit.stage import stage_page
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)


def png(extra_chunk: bytes = b"") -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + extra_chunk
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )


PNG = png()


def jpeg_with_metadata() -> bytes:
    exif = b"Exif\x00\x00SECRET-JPEG-OWNER-GPS"
    return (
        b"\xff\xd8"
        + b"\xff\xe1"
        + struct.pack(">H", len(exif) + 2)
        + exif
        + b"\xff\xd9"
    )


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


def test_recent_post_thumbnail_metadata_is_removed_before_publication(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    (instance / "media").mkdir(parents=True)
    text = b"Author\x00SECRET-OWNER-GPS"
    body = b"tEXt" + text
    metadata = struct.pack(">I", len(text)) + body + struct.pack(">I", zlib.crc32(body))
    (instance / "media" / "post.png").write_bytes(png(metadata))
    (instance / "media" / "post.jpg").write_bytes(jpeg_with_metadata())

    out = tmp_path / "public"
    build_site(
        config(
            [
                post("instagram", "media/post.png"),
                post("tiktok", "media/post.jpg"),
            ]
        ),
        out,
        today=TODAY,
        assets_dir=instance,
    )

    published = asset_file(out, "media/post.png").read_bytes()
    assert b"SECRET-OWNER-GPS" not in published
    assert b"tEXt" not in published
    published_jpeg = asset_file(out, "media/post.jpg").read_bytes()
    assert b"SECRET-JPEG-OWNER-GPS" not in published_jpeg
    assert b"Exif" not in published_jpeg


def test_recent_post_images_are_excluded_from_public_pdf_staging(
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

    staged = stage_page(out, out, public=True).html
    assert "1,234 views" in staged
    assert '<img class="kit-post-thumb"' not in staged
    assert "media/post" not in staged


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
