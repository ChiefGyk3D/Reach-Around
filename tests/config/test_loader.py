# SPDX-License-Identifier: Apache-2.0
"""Hostile-input tests for the YAML loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from reach_around.config.loader import ConfigError, load_yaml


def write(tmp_path: Path, text: str, name: str = "site.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def refused(path: Path, **kwargs: int) -> ConfigError:
    with pytest.raises(ConfigError) as info:
        load_yaml(path, **kwargs)
    return info.value


def test_valid_file_loads(tmp_path: Path) -> None:
    path = write(tmp_path, "title: Hello\nlinks:\n  - id: a\n")
    assert load_yaml(path) == {"title": "Hello", "links": [{"id": "a"}]}


def test_non_mapping_root_is_allowed_to_load_but_models_reject_it(
    tmp_path: Path,
) -> None:
    assert load_yaml(write(tmp_path, "- a\n- b\n")) == ["a", "b"]


def test_duplicate_key_refused(tmp_path: Path) -> None:
    error = refused(write(tmp_path, "a: 1\nb:\n  x: 1\n  x: 2\n"))
    assert "duplicate key" in error.message
    assert "x" in error.message
    assert error.line == 4
    assert "site.yaml" in str(error)
    assert error.path.endswith("site.yaml")


def test_anchor_refused(tmp_path: Path) -> None:
    error = refused(write(tmp_path, "a: &p {x: 1}\n"))
    assert "anchor" in error.message
    assert error.line == 1


def test_alias_refused(tmp_path: Path) -> None:
    error = refused(write(tmp_path, "- *p\n"))
    assert "alias" in error.message
    assert error.line == 1


def test_anchor_and_alias_pair_refused(tmp_path: Path) -> None:
    error = refused(write(tmp_path, "a: &p 1\nb: *p\n"))
    assert "anchor" in error.message or "alias" in error.message


def test_merge_key_refused(tmp_path: Path) -> None:
    error = refused(write(tmp_path, "a: {x: 1}\nb:\n  <<: {x: 2}\n"))
    assert "merge key" in error.message
    assert error.line == 3


def test_oversized_file_refused(tmp_path: Path) -> None:
    path = tmp_path / "big.yaml"
    path.write_bytes(b"#" * 101)
    error = refused(path, max_bytes=100)
    assert "too large" in error.message
    assert "big.yaml" in str(error)


def test_file_at_size_limit_loads(tmp_path: Path) -> None:
    path = tmp_path / "ok.yaml"
    path.write_bytes(b"a: 1\n" + b"#" * 95)
    assert load_yaml(path, max_bytes=100) == {"a": 1}


def test_deep_nesting_refused(tmp_path: Path) -> None:
    error = refused(write(tmp_path, "[" * 65 + "]" * 65))
    assert "nesting" in error.message


def test_nesting_at_limit_loads(tmp_path: Path) -> None:
    assert load_yaml(write(tmp_path, "[" * 64 + "]" * 64), max_depth=64)


def test_very_deep_nesting_is_a_config_error_not_recursion(tmp_path: Path) -> None:
    error = refused(write(tmp_path, "[" * 20000 + "]" * 20000))
    assert "nesting" in error.message


def test_invalid_utf8_refused(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_bytes(b"a: \xff\xfe\n")
    error = refused(path)
    assert "UTF-8" in error.message
    assert "bad.yaml" in str(error)


def test_empty_file_refused(tmp_path: Path) -> None:
    error = refused(write(tmp_path, ""))
    assert "empty" in error.message


def test_comment_only_file_refused(tmp_path: Path) -> None:
    assert "empty" in refused(write(tmp_path, "# nothing\n")).message


def test_missing_file_refused(tmp_path: Path) -> None:
    error = refused(tmp_path / "nope.yaml")
    assert "cannot read" in error.message


def test_directory_refused(tmp_path: Path) -> None:
    assert "regular file" in refused(tmp_path).message


def test_symlink_refused(tmp_path: Path) -> None:
    target = write(tmp_path, "a: 1\n", "real.yaml")
    link = tmp_path / "link.yaml"
    link.symlink_to(target)
    error = refused(link)
    assert "symlink" in error.message


def test_python_tag_refused(tmp_path: Path) -> None:
    error = refused(write(tmp_path, "a: !!python/object:os.system {}\n"))
    assert "tag" in error.message
    assert error.line == 1


def test_unknown_tag_refused(tmp_path: Path) -> None:
    assert "tag" in refused(write(tmp_path, "a: !custom 1\n")).message


def test_alias_bomb_refused_before_expansion(tmp_path: Path) -> None:
    lines = ['a0: &a0 ["x", "x", "x", "x", "x", "x", "x", "x", "x"]']
    for i in range(1, 12):
        refs = ", ".join([f"*a{i - 1}"] * 9)
        lines.append(f"a{i}: &a{i} [{refs}]")
    error = refused(write(tmp_path, "\n".join(lines) + "\n"))
    # The first anchor is on line 1, so the refusal happens before any expansion.
    assert error.line == 1
    assert "anchor" in error.message


def test_alias_bomb_without_anchors_is_refused_as_alias(tmp_path: Path) -> None:
    error = refused(write(tmp_path, "- [*a, *a, *a, *a]\n"))
    assert "alias" in error.message


def test_syntax_error_names_file_and_line(tmp_path: Path) -> None:
    error = refused(write(tmp_path, "a: 1\nb: [1, 2\nc: 3\n"))
    assert error.line is not None
    assert "site.yaml" in str(error)


def test_multiple_documents_refused(tmp_path: Path) -> None:
    refused(write(tmp_path, "a: 1\n---\nb: 2\n"))


def test_error_attributes() -> None:
    error = ConfigError("f.yaml", 3, "boom")
    assert (error.path, error.line, error.message) == ("f.yaml", 3, "boom")
    assert str(error) == "f.yaml:3: boom"
    assert str(ConfigError("f.yaml", None, "boom")) == "f.yaml: boom"
