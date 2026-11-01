"""P-026 adapter contract tests: no network, real engine, fake MiniLM.

Use the existing synthetic fixtures and backend. Every vector is generated
locally; the spent gold holdout and installed model are never accessed.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import numpy as np
import pytest

from gui import adapter


class FakeEncoder:
    """Deterministic local encoder implementing the model's encode protocol."""

    def encode(self, texts, *, normalize_embeddings, convert_to_numpy, show_progress_bar):
        assert normalize_embeddings is True
        assert convert_to_numpy is True
        assert show_progress_bar is False
        return np.asarray(
            [
                np.frombuffer(sha256(text.encode("utf-8")).digest(), dtype=np.uint8)
                .astype(np.float64) - 127.5
                for text in texts
            ],
            dtype=np.float64,
        )


@pytest.fixture
def ranked():
    return adapter.rank_candidates(model=FakeEncoder())


@pytest.fixture
def reconciled(ranked):
    return adapter.reconcile_candidates(ranked)


def test_sources_use_all_three_real_fictional_csvs_and_keep_text():
    summary = adapter.load_sources(preview_limit=5)
    assert summary["synthetic_only"] is True
    assert summary["source_count"] == 3
    assert summary["row_count"] == 180
    assert summary["source_column_count"] == 22
    assert [len(s["columns"]) for s in summary["systems"]] == [7, 7, 8]
    assert all(s["rows_read"] == 60 for s in summary["systems"])
    assert all(len(s["preview_rows"]) == 5 for s in summary["systems"])
    for system in summary["systems"]:
        for row in system["preview_rows"]:
            assert all(isinstance(value, str) for value in row.values())
            assert row["source_record_id"].startswith("source_")


@pytest.mark.parametrize("value", [True, -1, 26, "5"])
def test_preview_limit_is_bounded(value):
    with pytest.raises(ValueError, match="preview_limit"):
        adapter.load_sources(preview_limit=value)


def test_rank_candidates_is_only_a_both_method_preview(ranked):
    assert ranked["stage"] == "P2_CANDIDATE_RANKING_ONLY"
    assert ranked["methods"] == "both"
    assert ranked["semantic_model_local_files_only"] is True
    assert ranked["source_column_count"] == 22
    assert ranked["business_target_count"] == 4
    assert len(ranked["results"]) == 22
    assert ranked["mapping_validation_performed"] is False
    assert ranked["data_transformed"] is False
    assert ranked["gold_labels_accessed"] is False
    assert all(item["mapping_decision"] == "NOT_EVALUATED" for item in ranked["results"])


def test_independent_validator_does_not_transform_rows(ranked):
    outcome = adapter.inspect_decisions(ranked)
    assert outcome["stage"] == "P3_FIELD_VALIDATION_PREVIEW_ONLY"
    assert len(outcome["decisions"]) == 22
    assert sum(outcome["counts"].values()) == 22
    assert outcome["rows_processed"] == 0
    assert outcome["rows_transformed"] == 0
    assert outcome["gold_labels_accessed"] is False
    assert {x["decision"] for x in outcome["decisions"]} <= {"ACCEPT", "REVIEW", "REJECT"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("gold_labels_accessed", True),
        ("data_transformed", True),
        ("methods", "lexical"),
        ("mapping_validation_performed", True),
        ("stage", "P3_FIELD_VALIDATION_PREVIEW_ONLY"),
    ],
)
def test_adapter_rejects_tainted_or_wrong_stage_candidates(ranked, field, value):
    bad = deepcopy(ranked)
    bad[field] = value
    with pytest.raises((TypeError, ValueError)):
        adapter.inspect_decisions(bad)
    with pytest.raises((TypeError, ValueError)):
        adapter.reconcile_candidates(bad)


def test_adapter_reconciliation_calls_existing_backend_without_writing(reconciled):
    report = reconciled["mapping_report"]
    counts = report["counts"]
    assert report["row_count"] == 180
    assert sum(counts.values()) == 180
    assert len(reconciled["rows"]) == (
        counts["ACCEPTED"] + counts["ACCEPTED_OPTIONAL_NULL"]
    )
    assert report["gold_labels_accessed"] is False
    assert report["cross_system_joins"] == 0
    assert reconciled["evaluation"]["metrics_computed"] is False


def test_export_writes_four_files_once_into_isolated_folder(reconciled, tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "PROJECT_ROOT", tmp_path)
    manifest = adapter.prepare_audit_export(reconciled, run_id="gui_test_safe_0001")
    assert manifest["directory"].is_relative_to(tmp_path / "output" / "gui_runs")
    assert set(manifest["files"]) == set(adapter.AUDIT_FILENAMES)
    assert manifest["data_rows"] == len(reconciled["rows"])
    assert manifest["evaluation_is_placeholder"] is True
    assert all(path.is_file() and path.stat().st_size > 0 for path in manifest["files"].values())
    with pytest.raises(FileExistsError):
        adapter.prepare_audit_export(reconciled, run_id="gui_test_safe_0001")


@pytest.mark.parametrize("run_id", ["..", "../outside", "/absolute/path", "a\\b", "", "x" * 81])
def test_export_rejects_unsafe_folder_ids(reconciled, tmp_path, monkeypatch, run_id):
    monkeypatch.setattr(adapter, "PROJECT_ROOT", tmp_path)
    with pytest.raises(ValueError, match="Invalid GUI run identifier"):
        adapter.prepare_audit_export(reconciled, run_id=run_id)
    assert not (tmp_path / "output").exists()


def test_export_rejects_fabricated_accuracy_or_bad_row_accounting(reconciled, tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "PROJECT_ROOT", tmp_path)
    fraudulent = deepcopy(reconciled)
    fraudulent["evaluation"]["metrics_computed"] = True
    with pytest.raises(ValueError, match="fabricated scored evaluation"):
        adapter.prepare_audit_export(fraudulent, run_id="gui_fake_score_0001")
    mismatched = deepcopy(reconciled)
    mismatched["mapping_report"]["row_count"] += 1
    with pytest.raises(ValueError, match="Unaccounted input rows"):
        adapter.prepare_audit_export(mismatched, run_id="gui_bad_counts_0001")
    assert not (tmp_path / "output").exists()


def test_export_prevents_symlink_escape(reconciled, tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "PROJECT_ROOT", tmp_path)
    (tmp_path / "output").mkdir()
    target = tmp_path / "outside_output"
    target.mkdir(exist_ok=True)
    try:
        (tmp_path / "output" / "gui_runs").symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Windows symlinks unavailable without Developer Mode")
    with pytest.raises(ValueError, match="Unsafe GUI export destination"):
        adapter.prepare_audit_export(reconciled, run_id="gui_symlink_0001")
    assert list(target.iterdir()) == []
