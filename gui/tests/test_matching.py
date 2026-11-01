"""P-028 candidate-only Streamlit UX and fingerprint safeguards.

All model calls in these tests use a trivial local in-memory encoder. No real
MiniLM loading, HF network, challenge labels or audited output writes.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from gui import adapter, matching_state


ENTRYPOINT = Path(__file__).resolve().parents[1] / "streamlit_app.py"


class FakeEncoder:
    def encode(self, texts, *, normalize_embeddings, convert_to_numpy, show_progress_bar):
        assert normalize_embeddings and convert_to_numpy and not show_progress_bar
        return np.eye(len(texts), dtype=np.float64)


@pytest.fixture
def report():
    return adapter.rank_candidates(model=FakeEncoder())


def _matching_page():
    app = AppTest.from_file(str(ENTRYPOINT)).run()
    return app.switch_page("pages/matching.py").run()


def test_fingerprint_is_stable_and_excludes_unapproved_files():
    first = matching_state.input_fingerprint()
    assert len(first) == 64
    assert first == matching_state.input_fingerprint()


def test_fingerprint_detects_synthetic_csv_edit_without_touching_real_files(tmp_path, monkeypatch):
    source_root = adapter.PROJECT_ROOT
    demo = json.loads((source_root / "config" / "demo.json").read_text(encoding="utf-8"))
    paths = ["config/demo.json", demo["canonical_schema"], demo["source_metadata"],
             demo["value_rules"], *demo["sources"]]
    for path in paths:
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / path, destination)
    monkeypatch.setattr(adapter, "PROJECT_ROOT", tmp_path)
    old = matching_state.input_fingerprint()
    with (tmp_path / demo["sources"][0]).open("ab") as handle:
        handle.write(b"\n")
    assert matching_state.input_fingerprint() != old


def test_stale_report_invalidation_removes_downstream_artifacts():
    state = {
        matching_state.REPORT_KEY: {"stage": "old"},
        matching_state.FINGERPRINT_KEY: "old-fingerprint",
        "sb_validation_report": {"stale": True},
        "sb_export_manifest": {"stale": True},
    }
    assert matching_state.discard_stale(state, "new-fingerprint") is True
    assert matching_state.REPORT_KEY not in state
    assert matching_state.FINGERPRINT_KEY not in state
    assert "sb_validation_report" not in state
    assert "sb_export_manifest" not in state
    assert matching_state.discard_stale(state, "new-fingerprint") is False



def test_orphaned_validation_and_export_state_is_revoked_without_candidate_report():
    # A missing or evicted matching report must never leave its old derived
    # validation, reconciliation or prepared-download manifest accessible.
    state = {
        matching_state.FINGERPRINT_KEY: "same-fingerprint",
        "sb_validation_report": {"stale": True},
        "sb_validation_fingerprint": "same-fingerprint",
        "sb_reconciliation_report": {"stale": True},
        "sb_reconciliation_fingerprint": "same-fingerprint",
        "sb_export_manifest": {"run_id": "gui_previously_prepared"},
        "sb_matching_system": "system_b",  # unrelated UI selection
    }
    assert matching_state.REPORT_KEY not in state
    assert matching_state.discard_stale(state, "same-fingerprint") is True
    assert state == {"sb_matching_system": "system_b"}
    assert matching_state.discard_stale(state, "same-fingerprint") is False


def test_none_candidate_with_leftover_manifest_fails_closed_without_touching_valid_state():
    orphan = {
        matching_state.REPORT_KEY: None,
        matching_state.FINGERPRINT_KEY: "current",
        "sb_export_manifest": {"run_id": "gui_previously_prepared"},
    }
    assert matching_state.discard_stale(orphan, "current") is True
    assert orphan == {}

    valid = {
        matching_state.REPORT_KEY: {"stage": "candidate-only"},
        matching_state.FINGERPRINT_KEY: "current",
        "sb_validation_report": {"still": "current"},
    }
    assert matching_state.discard_stale(valid, "current") is False
    assert valid["sb_validation_report"] == {"still": "current"}



@pytest.mark.parametrize(
    ("page", "expected_error"),
    [
        ("pages/matching.py", "synthetic matching inputs could not be verified"),
        ("pages/validation.py", "synthetic inputs could not be verified"),
        ("pages/results.py", "synthetic sources; reconciliation is blocked"),
    ],
)
def test_input_verification_failure_revokes_all_existing_session_artifacts(
    monkeypatch, page, expected_error
):
    """Transient missing/invalid sources must not resurrect old downloads.

    This uses AppTest and never loads MiniLM or writes an audited report.
    """
    app = AppTest.from_file(str(ENTRYPOINT)).run()
    app.session_state[matching_state.REPORT_KEY] = {"stage": "old"}
    app.session_state[matching_state.FINGERPRINT_KEY] = "old-fingerprint"
    for key in matching_state.DOWNSTREAM_KEYS:
        app.session_state[key] = {"old": True}
    app.session_state["sb_matching_system"] = "system_b"

    def unreadable_inputs():
        raise OSError("simulated unavailable synthetic fixture")

    monkeypatch.setattr(matching_state, "input_fingerprint", unreadable_inputs)
    app.switch_page(page).run()

    assert len(app.exception) == 0
    assert any(expected_error in item.value for item in app.error)
    assert matching_state.REPORT_KEY not in app.session_state
    assert matching_state.FINGERPRINT_KEY not in app.session_state
    for key in matching_state.DOWNSTREAM_KEYS:
        assert key not in app.session_state
    assert app.session_state["sb_matching_system"] == "system_b"
    assert len(app.download_button) == 0


def test_display_rows_stays_candidate_only_for_22_original_fields(report):
    rows = matching_state.display_rows(report)
    assert len(rows) == 22
    assert len({(row["source_system"], row["source_column"]) for row in rows}) == 22
    assert {row["stage"] for row in rows} == {"PROPOSED_ONLY"}
    assert {row["source_system"] for row in rows} == {"system_a", "system_b", "system_c"}
    assert all(isinstance(row["lexical_similarity"], (float, int)) for row in rows)
    assert all(isinstance(row["semantic_similarity"], (float, int)) for row in rows)


def test_display_rows_rejects_tainted_confidence_or_approved_status(report):
    tainted = deepcopy(report)
    tainted["similarity_is_probability"] = True
    with pytest.raises(ValueError, match="similarity"):
        matching_state.display_rows(tainted)
    changed = deepcopy(report)
    changed["results"][0]["mapping_decision"] = "ACCEPT"
    with pytest.raises(ValueError, match="Unsafe"):
        matching_state.display_rows(changed)




@pytest.mark.parametrize("unsafe_variant", ["probability_claim", "silent_approval"])
def test_malformed_session_candidate_revokes_previous_validation_and_exports(
    monkeypatch, report, unsafe_variant
):
    """A tainted stored candidate cannot expose pre-existing downstream links.

    Tests the GUI boundary, not only the underlying display_rows validator.
    No real encoder load, audited output or holdout access.
    """
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Opening malformed state must never infer or export")

    monkeypatch.setattr(adapter, "rank_candidates", forbidden)
    monkeypatch.setattr(adapter, "prepare_audit_export", forbidden)

    app = AppTest.from_file(str(ENTRYPOINT)).run()
    fingerprint = matching_state.input_fingerprint()
    candidate = deepcopy(report)
    if unsafe_variant == "probability_claim":
        candidate["similarity_is_probability"] = True
    else:
        candidate["results"][0]["mapping_decision"] = "ACCEPT"

    app.session_state[matching_state.REPORT_KEY] = candidate
    app.session_state[matching_state.FINGERPRINT_KEY] = fingerprint
    for key in matching_state.DOWNSTREAM_KEYS:
        app.session_state[key] = {"previous": True}

    app.switch_page("pages/matching.py").run()

    assert len(app.exception) == 0
    assert any("candidate report is no longer valid" in item.value for item in app.error)
    assert matching_state.REPORT_KEY not in app.session_state
    assert matching_state.FINGERPRINT_KEY not in app.session_state
    for key in matching_state.DOWNSTREAM_KEYS:
        assert key not in app.session_state
    assert len(app.download_button) == 0

def test_idle_page_never_loads_model_or_exports(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No ranking, reconciliation or exports on navigation")

    monkeypatch.setattr(adapter, "rank_candidates", forbidden)
    monkeypatch.setattr(adapter, "reconcile_candidates", forbidden)
    monkeypatch.setattr(adapter, "prepare_audit_export", forbidden)
    app = _matching_page()
    assert len(app.exception) == 0
    assert len(app.button) == 1
    assert any("No candidate report" in x.value for x in app.info)
    assert len(app.file_uploader) == 0


def test_click_runs_once_then_source_filter_and_reruns_do_not_reload(monkeypatch, report):
    calls = []

    def rank_once(*args, **kwargs):
        calls.append("rank")
        return deepcopy(report)

    monkeypatch.setattr(adapter, "rank_candidates", rank_once)
    app = _matching_page()
    assert len(calls) == 0
    app.button[0].click().run()
    assert len(app.exception) == 0
    assert calls == ["rank"]
    assert any("Generated 22 source-column proposals" in x.value for x in app.success)
    assert str(app.metric[0].value) == "22"
    assert len(app.selectbox) == 2
    app.selectbox[0].set_value("system_b").run()
    assert len(app.exception) == 0
    assert calls == ["rank"]
    app.run()
    assert calls == ["rank"]
    assert len(app.file_uploader) == 0


def test_run_failure_is_explained_and_does_not_leave_stale_result(monkeypatch):
    def failure(*args, **kwargs):
        raise RuntimeError("deliberate offline model-missing test")

    monkeypatch.setattr(adapter, "rank_candidates", failure)
    app = _matching_page()
    app.button[0].click().run()
    assert len(app.exception) == 0
    assert any("Offline matching could not complete" in x.value for x in app.error)
    assert any("No candidate report" in x.value for x in app.info)
    assert len(app.file_uploader) == 0



def test_failed_offline_retry_revokes_previously_prepared_session(monkeypatch):
    """Missing local model on an explicit retry cannot revive old downloads.

    No real MiniLM or network, no gold labels and no audit-file writes.
    The old report has a CURRENT fingerprint, so failure handling (rather than
    ordinary stale-input invalidation) must clear all downstream artifacts.
    """
    calls = []

    def missing_offline_model(*args, **kwargs):
        calls.append("explicit matching")
        raise OSError("simulated locally cached MiniLM unavailable")

    def forbidden_export(*args, **kwargs):
        raise AssertionError("A failed matching retry must not export")

    monkeypatch.setattr(adapter, "rank_candidates", missing_offline_model)
    monkeypatch.setattr(adapter, "prepare_audit_export", forbidden_export)
    app = _matching_page()
    assert len(app.exception) == 0
    assert calls == []

    fingerprint = matching_state.input_fingerprint()
    app.session_state[matching_state.REPORT_KEY] = {"stage": "old"}
    app.session_state[matching_state.FINGERPRINT_KEY] = fingerprint
    for key in matching_state.DOWNSTREAM_KEYS:
        app.session_state[key] = {"old": True}
    app.session_state["sb_matching_system"] = "system_b"

    app.button[0].click().run()

    assert len(app.exception) == 0
    assert calls == ["explicit matching"]
    assert any("Offline matching could not complete" in x.value for x in app.error)
    assert any("No candidate report" in x.value for x in app.info)
    assert matching_state.REPORT_KEY not in app.session_state
    assert matching_state.FINGERPRINT_KEY not in app.session_state
    for key in matching_state.DOWNSTREAM_KEYS:
        assert key not in app.session_state
    assert app.session_state["sb_matching_system"] == "system_b"
    assert len(app.download_button) == 0


def test_changed_input_fingerprint_invalidates_active_session(monkeypatch, report):
    current = ["demo-A"]
    calls = []

    def fake_fingerprint():
        return current[0]

    def fake_rank(*args, **kwargs):
        calls.append(1)
        return deepcopy(report)

    monkeypatch.setattr(matching_state, "input_fingerprint", fake_fingerprint)
    monkeypatch.setattr(adapter, "rank_candidates", fake_rank)
    app = _matching_page()
    app.button[0].click().run()
    assert len(app.exception) == 0
    assert len(calls) == 1
    current[0] = "demo-B"
    app.run()
    assert len(app.exception) == 0
    assert len(calls) == 1
    assert any("inputs changed" in x.value for x in app.warning)
    assert any("No candidate report" in x.value for x in app.info)
