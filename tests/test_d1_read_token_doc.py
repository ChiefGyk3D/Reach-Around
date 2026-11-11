# SPDX-License-Identifier: Apache-2.0
"""The D1 token research must distinguish evidence from inference."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESEARCH = ROOT / "docs" / "research" / "d1-read-token.md"
BACKUP = ROOT / "docs" / "backup-restore.md"


def _summary() -> str:
    text = RESEARCH.read_text(encoding="utf-8")
    return text[text.index("## Summary") : text.index("## 1.")]


def test_summary_marks_live_table_exposure_as_conditional_and_unverified() -> None:
    summary = _summary()
    assert "If the deployed account contains" in summary
    assert "live account and database contents are unverified" in summary


def test_summary_labels_scope_and_aggregate_boundary_as_inferred() -> None:
    summary = _summary()
    assert "INFERRED from the documented absence" in summary
    assert "INFERRED: Cloudflare documents no table or column scope" in summary


def test_export_permission_contradiction_is_explicitly_unverified() -> None:
    backup = " ".join(BACKUP.read_text(encoding="utf-8").split())
    research = RESEARCH.read_text(encoding="utf-8")
    assert "Unverified: D1 Read is sufficient for exports" in backup
    assert "backup runbook previously stated D1 Read was sufficient" in research
