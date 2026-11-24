# SPDX-License-Identifier: Apache-2.0
"""scripts/wiki-flatten.py: the flat wiki layout and the link rewrite, offline."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "wiki-flatten.py"
_spec = importlib.util.spec_from_file_location("wiki_flatten", SCRIPT)
assert _spec is not None and _spec.loader is not None
wf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wf)


def tree(**pages: str) -> dict[str, bytes]:
    """Source tree; ``__`` stands for ``/``, ``_md``/``_png`` for the suffix."""
    return {
        k.replace("__", "/").replace("_md", ".md").replace("_png", ".png"): v.encode()
        for k, v in pages.items()
    }


def out(files: dict[str, bytes], **kw: str) -> dict[str, str]:
    return {k: v.decode() for k, v in wf.flatten(files, **kw).items()}


def test_flat_names() -> None:
    assert wf.flat_name("Home.md") == "Home.md"
    assert wf.flat_name("a/b.md") == "a-b.md"
    assert wf.flat_name("a/b/c.md") == "a-b-c.md"
    assert wf.flat_name("a/index.md") == "a.md"
    assert wf.flat_name("a/b/README.md") == "a-b.md"
    assert wf.flat_name("img/x.png") == "img-x.png"


def test_home_required() -> None:
    with pytest.raises(wf.FlattenError) as exc:
        wf.flatten({"a.md": b"x"})
    assert ("Home.md", "required home page is missing") in exc.value.problems


def test_pages_flattened_and_links_rewritten() -> None:
    files = tree(
        Home_md="See [s](quick/start.md#top) and [u](quick/start.md).",
        quick__start_md='Back to [home](../Home.md) and [sib](deploy.md "t").',
        quick__deploy_md="![a](../img/s.png)",
        img__s_png="PNG",
    )
    res = out(files)
    assert res["Home.md"] == "See [s](quick-start#top) and [u](quick-start)."
    assert res["quick-start.md"] == 'Back to [home](Home) and [sib](quick-deploy "t").'
    assert res["quick-deploy.md"] == "![a](img-s.png)"
    assert res["img-s.png"] == "PNG"


def test_folder_index_page_and_link_to_it() -> None:
    files = tree(
        Home_md="[g](guides/)",
        guides__index_md="[x](x.md)",
        guides__x_md="x",
    )
    res = out(files)
    assert res["Home.md"] == "[g](guides)"
    assert res["guides.md"] == "[x](guides-x)"


def test_asset_base() -> None:
    files = tree(Home_md="![a](img/s.png)", img__s_png="P")
    res = out(files, asset_base="https://example.com/wiki/")
    assert res["Home.md"] == "![a](https://example.com/wiki/img-s.png)"


def test_reference_definition_and_angle_target() -> None:
    files = tree(Home_md="[a][r] [b](<x/y.md>)\n\n[r]: x/y.md\n", x__y_md="y")
    res = out(files)
    assert res["Home.md"] == "[a][r] [b](<x-y>)\n\n[r]: x-y\n"


def test_untouched_links_and_code() -> None:
    body = (
        "[ext](https://example.com/a.md) [mail](mailto:a@example.com) [here](#sec)\n"
        "`[c](x/y.md)` text\n"
        "```\n[f](nope.md)\n```\n"
        "~~~\n[g](nope.md)\n~~~\n"
    )
    res = out(tree(Home_md=body))
    assert res["Home.md"] == body


def test_longer_fence_not_closed_by_shorter() -> None:
    body = "````\n```\n[f](nope.md)\n````\n"
    assert out(tree(Home_md=body))["Home.md"] == body


@pytest.mark.parametrize(
    "link",
    ["missing.md", "../outside.md", "/abs.md", "sub/", "Home.md/x"],
)
def test_bad_links_are_errors(link: str) -> None:
    with pytest.raises(wf.FlattenError):
        wf.flatten({"Home.md": f"[x]({link})".encode()})


def test_errors_never_carry_page_content() -> None:
    with pytest.raises(wf.FlattenError) as exc:
        wf.flatten({"Home.md": b"SECRET-BODY [x](missing.md)"})
    assert "SECRET-BODY" not in str(exc.value)


def test_collision_is_error() -> None:
    files = tree(Home_md="h", a__b_md="1", **{"a-b_md": "2"})
    with pytest.raises(wf.FlattenError) as exc:
        wf.flatten(files)
    assert any("collides" in m for _, m in exc.value.problems)


def test_collision_is_case_insensitive() -> None:
    files = tree(Home_md="h", a_md="1", A_md="2")
    with pytest.raises(wf.FlattenError):
        wf.flatten(files)


@pytest.mark.parametrize(
    "name",
    ["a/script.sh", "a/data.json", "bad name.md", ".hidden.md", "a/.x/b.md", "a/é.md"],
)
def test_disallowed_sources(name: str) -> None:
    with pytest.raises(wf.FlattenError):
        wf.flatten({"Home.md": b"h", name: b"x"})


def test_sidebar_and_footer_root_only() -> None:
    wf.flatten({"Home.md": b"h", "_Sidebar.md": b"s", "_Footer.md": b"f"})
    with pytest.raises(wf.FlattenError):
        wf.flatten({"Home.md": b"h", "a/_Sidebar.md": b"s"})


def test_authored_sidebar_kept_and_rewritten() -> None:
    files = {"Home.md": b"h", "_Sidebar.md": b"[s](a/b.md)", "a/b.md": b"b"}
    assert out(files)["_Sidebar.md"] == "[s](a-b)"


def test_generated_sidebar() -> None:
    res = out(tree(Home_md="h", about_md="a", quick__start_md="s", quick__index_md="i"))
    side = res["_Sidebar.md"]
    assert side.startswith("**[Home](Home)**")
    assert "- [about](about)" in side
    assert "- [quick start](quick-start)" in side
    assert "- [quick](quick)" in side
    assert "**quick**" in side


def test_oversize_file_is_error() -> None:
    with pytest.raises(wf.FlattenError):
        wf.flatten({"Home.md": b"h", "a.md": b"x" * (wf.MAX_FILE_BYTES + 1)})


def test_cli_roundtrip_and_refusals(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "src"
    (src / "q").mkdir(parents=True)
    (src / "Home.md").write_text("[a](q/a.md)")
    (src / "q" / "a.md").write_text("a")
    dest = tmp_path / "out"
    assert wf.main(["--src", str(src), "--out", str(dest)]) == 0
    assert sorted(p.name for p in dest.iterdir()) == [
        "Home.md",
        "_Sidebar.md",
        "q-a.md",
    ]
    assert (dest / "Home.md").read_text() == "[a](q-a)"
    # A non-empty output directory and an output inside the source are refused.
    assert wf.main(["--src", str(src), "--out", str(dest)]) == 1
    assert wf.main(["--src", str(src), "--out", str(src / "o")]) == 1
    capsys.readouterr()


def test_cli_refuses_symlink(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "Home.md").write_text("h")
    target = tmp_path / "private.md"
    target.write_text("PRIVATE")
    os.symlink(target, src / "leak.md")
    assert wf.main(["--src", str(src), "--out", str(tmp_path / "out")]) == 1
    err = capsys.readouterr().err
    assert "leak.md: symlinks are not allowed" in err
    assert "PRIVATE" not in err
    assert not (tmp_path / "out").exists()


def test_cli_missing_source(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert wf.main(["--src", str(tmp_path / "none"), "--out", str(tmp_path / "o")]) == 1
    capsys.readouterr()
