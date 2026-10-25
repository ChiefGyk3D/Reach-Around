# SPDX-License-Identifier: Apache-2.0
"""render_headers enforces what it documents about the cache rules."""

from __future__ import annotations

import pytest

from reach_around.site.overrides import OverridesError, render_headers


def test_disjoint_lists_render() -> None:
    text = render_headers(
        immutable_paths=["/static/*", "/a.1234.png"], revalidate_paths=["/", "/x/"]
    )
    assert "/static/*\n  Cache-Control: public, max-age=31536000, immutable" in text
    assert "/x/\n  Cache-Control: public, max-age=0, must-revalidate" in text


@pytest.mark.parametrize(
    ("immutable", "revalidate"),
    [
        (["/a"], ["/a"]),  # the same exact path in both classes
        (["/static/*"], ["/static/*"]),
        (["/static/*"], ["/static/x.css"]),  # splat covers an exact rule
        (["/static/x.css"], ["/static/*"]),  # and the other way round
        (["/*"], ["/index.html"]),
        (["/static/*"], ["/static/sub/*"]),  # nested splats
        (["/static/sub/*"], ["/static/*"]),
        (["/a", "/a"], []),  # a repeat inside one class
        ([], ["/b/", "/b/"]),
    ],
)
def test_overlapping_rules_are_refused(
    immutable: list[str], revalidate: list[str]
) -> None:
    with pytest.raises(OverridesError, match="overlap|duplicate"):
        render_headers(immutable_paths=immutable, revalidate_paths=revalidate)


def test_a_splat_that_covers_nothing_of_the_other_class_is_fine() -> None:
    render_headers(immutable_paths=["/static/*"], revalidate_paths=["/staticx", "/"])
    render_headers(immutable_paths=["/media/*", "/static/*"], revalidate_paths=["/m"])


def test_a_line_longer_than_2000_characters_is_refused() -> None:
    long_path = "/" + "a" * 2000
    with pytest.raises(OverridesError, match="2000"):
        render_headers(revalidate_paths=[long_path])
    with pytest.raises(OverridesError, match="2000"):
        render_headers(immutable_paths=[long_path])


def test_a_line_of_exactly_2000_characters_is_accepted() -> None:
    path = "/" + "a" * 1997  # "/aaa..." is the whole line: 1998 chars
    render_headers(revalidate_paths=[path])
    text = render_headers(revalidate_paths=["/" + "a" * 1999])
    assert max(len(line) for line in text.splitlines()) == 2000


def test_the_long_site_wide_csp_line_stays_under_the_limit() -> None:
    assert max(len(line) for line in render_headers().splitlines()) < 2000


def test_a_noindex_path_line_is_also_checked() -> None:
    with pytest.raises(OverridesError, match="noindex path"):
        render_headers(noindex_paths=["/" + "a" * 2001])
