# SPDX-License-Identifier: Apache-2.0
"""``open_store``, ``StoreConfig`` and the ``store`` CLI command."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from reach_around.cli import main
from reach_around.config import ConfigError, StoreConfig, load_site_config
from reach_around.store import StoreError, open_store

EXAMPLE = Path(__file__).parents[2] / "examples" / "instance" / "site.yaml"


def test_open_store_migrates_by_default(tmp_path: Path) -> None:
    config = StoreConfig(path=str(tmp_path / "d" / "s.db"))
    store = open_store(config, {})
    assert store.migration_status().pending == ()
    assert store.migration_status().applied == (1,)


def test_open_store_can_skip_migrating(tmp_path: Path) -> None:
    store = open_store(StoreConfig(path=str(tmp_path / "s.db")), {}, migrate=False)
    assert store.migration_status().applied == ()
    assert store.migration_status().pending == (1,)


def test_postgres_is_a_seam_not_yet_built() -> None:
    config = StoreConfig(kind="postgres", dsn_env="REACH_AROUND_DSN")
    with pytest.raises(StoreError):
        open_store(config, {"REACH_AROUND_DSN": "postgresql://u:pw@h/db"})


def test_postgres_requires_dsn_env() -> None:
    with pytest.raises(ValueError):
        StoreConfig(kind="postgres")


def test_no_dsn_or_password_field_exists() -> None:
    assert "dsn" not in StoreConfig.model_fields
    assert not [n for n in StoreConfig.model_fields if "password" in n]


def test_dsn_env_must_be_a_variable_name() -> None:
    with pytest.raises(ValueError):
        StoreConfig(kind="postgres", dsn_env="postgresql://u:pw@h/db")


def test_defaults_keep_plan_1_example_loading() -> None:
    assert load_site_config(EXAMPLE).store == StoreConfig()


def _config_with_store(tmp_path: Path, db: Path) -> Path:
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    data["store"] = {"path": str(db)}
    target = tmp_path / "site.yaml"
    target.write_text(yaml.safe_dump(data), encoding="utf-8")
    return target


def test_cli_store_status_then_migrate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config_with_store(tmp_path, tmp_path / "data" / "s.db")
    assert main(["store", "status", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "applied: none" in out and "pending: 0001" in out
    assert not (tmp_path / "data").exists()  # status never creates the database
    assert main(["store", "migrate", "--config", str(config)]) == 0
    assert "schema at 0001" in capsys.readouterr().out
    assert main(["store", "status", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "applied: 0001" in out and "pending: none" in out


def test_cli_store_bad_config_is_exit_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "site.yaml"
    bad.write_text("version: 1\n", encoding="utf-8")
    assert main(["store", "status", "--config", str(bad)]) == 1
    assert "error:" in capsys.readouterr().err
    with pytest.raises(ConfigError):
        load_site_config(bad)
