# SPDX-License-Identifier: Apache-2.0
"""``reach-around parity``: built HTML against an owner-supplied link export.

Every fixture here is synthetic. Output must show counts and ids only, never a
URL or a label, so each test plants a sentinel host and checks it stays out.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml

from reach_around.cli import main

SENTINEL = "sentinel-7c1e.example"

Link = tuple[str, str]  # (url, label)


def anchor_social(url: str, label: str) -> str:
    return (
        f'<li><a class="social" href="{url}" rel="noopener noreferrer">'
        f'<span class="icon icon-x" aria-hidden="true"></span>'
        f'<span class="sr-only">{label}</span></a></li>'
    )


def anchor_link(url: str, label: str, ident: str | None = None) -> str:
    track = f' ping="/e/c/{ident}" data-link-id="{ident}"' if ident else ""
    return (
        f'<li class="block block-link"><a class="link" href="{url}"{track} '
        f'rel="noopener noreferrer"><span class="link-text">'
        f'<span class="link-title">{label}</span>'
        f'<span class="link-subtitle">ignored subtitle</span></span></a></li>'
    )


def page(
    *,
    socials: Sequence[Link] = (),
    links: Sequence[Link] = (),
    profile: Link | None = None,
    ids: bool = False,
) -> str:
    soc = "".join(anchor_social(u, t) for u, t in socials)
    lnk = "".join(
        anchor_link(u, t, f"id{i}" if ids else None) for i, (u, t) in enumerate(links)
    )
    prof = (
        f'<a class="profile-button" id="kit" href="{profile[0]}" '
        f'rel="noopener noreferrer">{profile[1]}</a>'
        if profile
        else ""
    )
    return (
        '<!DOCTYPE html><html><body><a class="skip-link" href="#content">Skip</a>'
        '<main id="content"><nav class="tabs"><ul>'
        '<li><a href="./" aria-current="page">Start</a></li>'
        '<li><a href="./more/">More</a></li></ul></nav>'
        f'<header><ul class="socials">{soc}</ul></header>'
        f'<ul class="blocks">{lnk}</ul>{prof}'
        '<footer><a href="privacy/">Privacy</a>'
        '<a href="https://credit.example/">Built with</a></footer>'
        "</main></body></html>"
    )


def write_site(
    root: Path,
    pages: dict[str, str] | None = None,
    *,
    socials: Sequence[Link] = (),
    links: Sequence[Link] = (),
    profile: Link | None = None,
    ids: bool = False,
) -> Path:
    site = root / "site"
    if pages is None:
        pages = {"": page(socials=socials, links=links, profile=profile, ids=ids)}
    for rel, html in pages.items():
        target = site / rel / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")
    return site


def write_json(root: Path, rows: list[Link], name: str = "export.json") -> Path:
    path = root / name
    data = [{"url": u, "label": t} for u, t in rows]
    path.write_text(json.dumps({"links": data}), encoding="utf-8")
    return path


def write_csv(root: Path, rows: list[Link], name: str = "export.csv") -> Path:
    path = root / name
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["url", "label"])
        w.writerows(rows)
    return path


def run(
    capsys: pytest.CaptureFixture[str], site: Path, export: Path, *extra: str
) -> tuple[int, str, str]:
    code = main(["parity", "--site", str(site), "--expected", str(export), *extra])
    out = capsys.readouterr()
    return code, out.out, out.err


A: Link = (f"https://{SENTINEL}/a", "Alpha")
B: Link = (f"https://{SENTINEL}/b", "Beta")
C: Link = (f"https://{SENTINEL}/c", "Gamma")
S: Link = (f"https://social.{SENTINEL}/me", "Social One")


def no_leak(*texts: str) -> None:
    for text in texts:
        assert SENTINEL not in text
        assert "Alpha" not in text and "Beta" not in text and "Gamma" not in text


def test_parity_exit_0(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    site = write_site(tmp_path, socials=[S], links=[A, B], profile=C)
    code, out, err = run(capsys, site, write_json(tmp_path, [S, A, B, C]))
    assert code == 0, out + err
    assert "parity" in out.lower()
    assert "matched: 4" in out
    no_leak(out, err)


def test_csv_input_works(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    site = write_site(tmp_path, links=[A, B])
    code, out, _ = run(capsys, site, write_csv(tmp_path, [A, B]))
    assert code == 0, out


def test_missing_link(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    site = write_site(tmp_path, links=[A])
    code, out, err = run(capsys, site, write_json(tmp_path, [A, B]))
    assert code == 1
    assert "missing: 1" in out
    assert "export#2" in out
    no_leak(out, err)


def test_extra_link(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    site = write_site(tmp_path, links=[A, B], ids=True)
    code, out, err = run(capsys, site, write_json(tmp_path, [A]))
    assert code == 1
    assert "extra: 1" in out
    assert "id1" in out  # the link id, not the URL
    no_leak(out, err)


def test_changed_label(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    site = write_site(tmp_path, links=[(A[0], "Alpha Renamed")])
    code, out, err = run(capsys, site, write_json(tmp_path, [A]))
    assert code == 1
    assert "changed label: 1" in out
    assert "missing" not in out.replace("missing: 0", "")
    no_leak(out, err)


def test_changed_target(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    site = write_site(tmp_path, links=[(f"https://{SENTINEL}/new", "Alpha")])
    code, out, err = run(capsys, site, write_json(tmp_path, [A]))
    assert code == 1
    assert "changed target: 1" in out
    no_leak(out, err)


def test_redirect_only_match(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    new = (f"https://{SENTINEL}/new", "Alpha")
    site = write_site(tmp_path, links=[new])
    export = write_json(tmp_path, [A])
    redirects = tmp_path / "redirects.csv"
    redirects.write_text(f"from,to\n{A[0]},{new[0]}\n", encoding="utf-8")
    code, out, err = run(capsys, site, export, "--redirects", str(redirects))
    assert code == 1
    assert "redirect only: 1" in out
    assert "changed target" not in out.replace("changed target: 0", "")
    no_leak(out, err)
    code, out, _ = run(
        capsys, site, export, "--redirects", str(redirects), "--accept-redirects"
    )
    assert code == 0, out
    assert "redirect only: 1" in out


def test_redirect_map_works_in_either_direction(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    new = (f"https://{SENTINEL}/new", "Alpha")
    site = write_site(tmp_path, links=[A])
    redirects = tmp_path / "redirects.json"
    redirects.write_text(json.dumps([{"from": new[0], "to": A[0]}]), encoding="utf-8")
    code, out, _ = run(
        capsys,
        site,
        write_json(tmp_path, [new]),
        "--redirects",
        str(redirects),
        "--accept-redirects",
    )
    assert code == 0, out


def test_duplicate_in_built(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    site = write_site(tmp_path, links=[A, A])
    code, out, err = run(capsys, site, write_json(tmp_path, [A]))
    assert code == 1
    assert "duplicate in site: 1" in out
    assert "extra" not in out.replace("extra: 0", "")
    no_leak(out, err)


def test_duplicate_in_export(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = write_site(tmp_path, links=[A])
    code, out, err = run(capsys, site, write_json(tmp_path, [A, A]))
    assert code == 1
    assert "duplicate in export: 1" in out
    no_leak(out, err)


def test_matching_duplicates_are_parity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = write_site(tmp_path, links=[A, A])
    code, out, _ = run(capsys, site, write_json(tmp_path, [A, A]))
    assert code == 0, out


def test_reordered(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    site = write_site(tmp_path, links=[B, A])
    export = write_json(tmp_path, [A, B])
    code, out, _ = run(capsys, site, export)
    assert code == 1
    assert "reordered: 1" in out
    code, out, _ = run(capsys, site, export, "--ignore-order")
    assert code == 0, out


def test_normalization_ignores_tracking_case_fragment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    built = (f"https://{SENTINEL.upper()}:443/a?utm_source=x&keep=1#top", "Alpha")
    expected = (f"HTTPS://{SENTINEL}/a?keep=1&fbclid=zz", "  alpha ")
    site = write_site(tmp_path, links=[built])
    code, out, _ = run(capsys, site, write_json(tmp_path, [expected]))
    assert code == 0, out


def test_root_path_equivalence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = write_site(tmp_path, links=[(f"https://{SENTINEL}", "Home")])
    code, out, _ = run(
        capsys, site, write_json(tmp_path, [(f"https://{SENTINEL}/", "Home")])
    )
    assert code == 0, out


def test_out_of_scope_anchors_are_ignored(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # skip link, tab bar, privacy link and credit link are in page() already
    site = write_site(tmp_path, links=[A])
    code, out, _ = run(capsys, site, write_json(tmp_path, [A]))
    assert code == 0, out
    assert "extra: 0" in out


def test_internal_tab_targets_are_counted_not_compared(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    html = page(links=[A]).replace(
        '<ul class="blocks">',
        '<ul class="blocks"><li><a class="link" href="./more/"><span '
        'class="link-title">Contact</span></a></li>',
    )
    site = write_site(tmp_path, {"": html})
    code, out, _ = run(capsys, site, write_json(tmp_path, [A]))
    assert code == 0, out
    assert "internal, not compared: 1" in out


def test_repeated_header_and_card_counted_once_across_tabs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pages = {
        "": page(socials=[S], links=[A], profile=C),
        "more": page(socials=[S], links=[B], profile=C),
    }
    site = write_site(tmp_path, pages)
    code, out, err = run(capsys, site, write_json(tmp_path, [S, A, C, B]))
    assert code == 0, out + err


def test_icon_row_missing_on_a_later_tab_is_a_difference(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pages = {
        "": page(socials=[S], links=[A]),
        "more": page(socials=[], links=[B]),
    }
    site = write_site(tmp_path, pages)
    code, out, _ = run(capsys, site, write_json(tmp_path, [S, A, B]))
    assert code == 1
    assert "icon row differs on 1 page" in out


# ---- exit 2: cannot tell -------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[]",
        '{"links": []}',
        '{"links": {"a": 1}}',
        '[{"url": "https://x.example/"}]',
        '[{"label": "x"}]',
        '[{"url": "ftp://x.example/", "label": "x"}]',
        '[{"url": "javascript:alert(1)", "label": "x"}]',
        '[{"url": "https://x.example/", "label": ""}]',
        '[{"url": 5, "label": "x"}]',
        '["https://x.example/"]',
        '[{"url": "https://a.example/", "url": "https://b.example/", "label": "x"}]',
        "[" * 5000,
    ],
)
def test_malformed_json_is_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    site = write_site(tmp_path, links=[A])
    export = tmp_path / "bad.json"
    export.write_text(text, encoding="utf-8")
    code, out, err = run(capsys, site, export)
    assert code == 2
    assert "error" in err.lower()
    assert "Traceback" not in err
    no_leak(out, err)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "url\nhttps://x.example/\n",
        "url,label\n",
        "url,label\nhttps://x.example/\n",
        "url,label\nnot a url,x\n",
        "url,label\n,x\n",
        "url,label\nhttps://x.example/,x,extra\n",
    ],
)
def test_malformed_csv_is_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    site = write_site(tmp_path, links=[A])
    export = tmp_path / "bad.csv"
    export.write_text(text, encoding="utf-8")
    code, _, err = run(capsys, site, export)
    assert code == 2
    assert "error" in err.lower()


def test_malformed_error_does_not_echo_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = write_site(tmp_path, links=[A])
    export = tmp_path / "bad.csv"
    export.write_text(f"url,label\nftp://{SENTINEL}/x,Alpha\n", encoding="utf-8")
    code, out, err = run(capsys, site, export)
    assert code == 2
    assert "row 2" in err
    no_leak(out, err)


def test_unknown_extension_and_missing_file_are_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = write_site(tmp_path, links=[A])
    odd = tmp_path / "export.txt"
    odd.write_text("x", encoding="utf-8")
    assert run(capsys, site, odd)[0] == 2
    assert run(capsys, site, tmp_path / "nope.json")[0] == 2


def test_oversized_export_is_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = write_site(tmp_path, links=[A])
    export = tmp_path / "big.json"
    export.write_text(" " * (6 * 1024 * 1024), encoding="utf-8")
    assert run(capsys, site, export)[0] == 2


def test_missing_site_dir_is_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(capsys, tmp_path / "nope", write_json(tmp_path, [A]))
    assert code == 2
    assert "error" in err.lower()


def test_site_without_pages_or_anchors_is_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run(capsys, empty, write_json(tmp_path, [A]))[0] == 2
    site = write_site(tmp_path, {"": "<html><body><p>nothing</p></body></html>"})
    code, _, err = run(capsys, site, write_json(tmp_path, [A]))
    assert code == 2  # zero anchors found: the parser is probably stale
    assert "error" in err.lower()


def test_symlinked_page_is_not_followed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = write_site(tmp_path, links=[A])
    secret = tmp_path / "outside.html"
    secret.write_text(page(links=[B]), encoding="utf-8")
    (site / "linked").mkdir()
    (site / "linked" / "index.html").symlink_to(secret)
    code, out, _ = run(capsys, site, write_json(tmp_path, [A]))
    assert code == 0, out


def test_bad_redirect_file_is_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = write_site(tmp_path, links=[A])
    redirects = tmp_path / "r.csv"
    redirects.write_text("from,to\nhttps://a.example/\n", encoding="utf-8")
    code, _, _ = run(
        capsys, site, write_json(tmp_path, [A]), "--redirects", str(redirects)
    )
    assert code == 2


def test_parity_against_a_real_build(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The parser matches what the real templates emit."""
    config = {
        "version": 1,
        "creator": {
            "name": "Example Creator",
            "socials": [
                {
                    "name": "Example Video",
                    "url": "https://video.example.com/@x",
                    "icon": "video",
                }
            ],
        },
        "tabs": [
            {
                "id": "links",
                "title": "Links",
                "blocks": [
                    {"id": "home", "title": "Home", "url": "https://example.com/"},
                    {
                        "id": "gear",
                        "title": "Gear",
                        "links": [
                            {
                                "id": "mic",
                                "title": "Mic",
                                "url": "https://shop.example.com/mic?ref=x",
                                "affiliate": True,
                            }
                        ],
                    },
                ],
            },
            {
                "id": "more",
                "title": "More",
                "blocks": [
                    {"id": "extra", "title": "Extra", "url": "https://example.org/"}
                ],
            },
        ],
    }
    cfg = tmp_path / "site.yaml"
    cfg.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    out_dir = tmp_path / "out"
    assert main(["build", "--config", str(cfg), "--out", str(out_dir)]) == 0
    capsys.readouterr()
    rows: list[Link] = [
        ("https://video.example.com/@x", "Example Video"),
        ("https://example.com/", "Home"),
        ("https://shop.example.com/mic?ref=x", "Mic"),
        ("https://example.org/", "Extra"),
    ]
    code, out, err = run(capsys, out_dir, write_json(tmp_path, rows))
    assert code == 0, out + err
    assert "matched: 4" in out
    code, out, _ = run(capsys, out_dir, write_json(tmp_path, rows[:3]))
    assert code == 1
    assert "extra: 1" in out


# ---- review fixes (PR #353) -------------------------------------------------


def test_userinfo_in_export_is_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = write_site(tmp_path, links=[A])
    export = write_json(tmp_path, [(f"https://user:pw@{SENTINEL}/a", "Alpha")])
    code, out, err = run(capsys, site, export)
    assert code == 2
    no_leak(out, err)


def test_userinfo_on_site_is_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = write_site(tmp_path, links=[(f"https://u:pw@{SENTINEL}/a", "Alpha")])
    assert run(capsys, site, write_json(tmp_path, [A]))[0] == 2


def test_unreadable_nested_directory_is_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import os

    pages = {"": page(links=[A]), "more": page(links=[B])}
    site = write_site(tmp_path, pages)
    (site / "more" / "deeper").mkdir()
    (site / "more" / "deeper").chmod(0)
    try:
        if os.access(site / "more" / "deeper", os.R_OK):
            pytest.skip("directory permissions are not enforced here")
        code, _, err = run(capsys, site, write_json(tmp_path, [A, B]))
    finally:
        (site / "more" / "deeper").chmod(0o755)
    assert code == 2
    assert "error" in err.lower()


def test_non_tab_pages_do_not_trigger_icon_row_drift(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    kit = (
        '<html><body><main id="content"><header><ul class="socials">'
        + anchor_social(*S)
        + '</ul></header><section class="kit"><h2>Kit</h2></section>'
        "</main></body></html>"
    )
    pages = {"": page(socials=[S], links=[A], profile=C), "kit": kit}
    site = write_site(tmp_path, pages)
    code, out, err = run(capsys, site, write_json(tmp_path, [S, A, C]))
    assert code == 0, out + err


def test_internal_social_target_is_page_independent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def internal(href: str) -> str:
        return page(links=[A]).replace(
            '<ul class="socials">',
            f'<ul class="socials"><li><a class="social" href="{href}">'
            '<span class="sr-only">Contact</span></a></li>',
        )

    pages = {"": internal("contact/"), "more": internal("../contact/")}
    site = write_site(tmp_path, pages)
    code, out, err = run(capsys, site, write_json(tmp_path, [A, A]))
    assert code == 0, out + err
    assert "icon row differs" not in out


def test_internal_social_pointing_elsewhere_still_drifts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def internal(href: str) -> str:
        return page(links=[A]).replace(
            '<ul class="socials">',
            f'<ul class="socials"><li><a class="social" href="{href}">'
            '<span class="sr-only">Contact</span></a></li>',
        )

    pages = {"": internal("contact/"), "more": internal("../other/")}
    site = write_site(tmp_path, pages)
    code, out, _ = run(capsys, site, write_json(tmp_path, [A, A]))
    assert code == 1
    assert "icon row differs on 1 page" in out
