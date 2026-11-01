"""P-029 GUI independent validator test cases using fake/local ranker only.

These tests never use the frozen holdout or initialize actual MiniLM.
The original backend validator owns decisions, reason codes and fallbacks.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

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


def _open_with_report(report):
    app = AppTest.from_file(str(ENTRYPOINT)).run()
    app.session_state[matching_state.REPORT_KEY] = deepcopy(report)
    app.session_state[matching_state.FINGERPRINT_KEY] = matching_state.input_fingerprint()
    return app.switch_page("pages/validation.py").run()


def test_without_matching_report_no_validation_or_auto_approval(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Validator must not run without explicit action")

    monkeypatch.setattr(adapter, "inspect_decisions", blocked)
    app = AppTest.from_file(str(ENTRYPOINT)).run().switch_page("pages/validation.py").run()
    assert len(app.exception) == 0
    assert len(app.button) == 0
    assert len(app.file_uploader) == 0
    assert any("No current candidate report" in item.value for item in app.info)


def test_with_current_candidates_requires_explicit_explain_action(monkeypatch, report):
    def blocked(*args, **kwargs):
        raise AssertionError("Validator must not run merely by opening the page")

    monkeypatch.setattr(adapter, "inspect_decisions", blocked)
    app = _open_with_report(report)
    assert len(app.exception) == 0
    assert len(app.button) == 1
    assert app.button[0].label == "Explain validation decisions"
    assert any("No validated decisions" in item.value for item in app.info)
    assert len(app.file_uploader) == 0


def test_validator_integration_shows_all_22_real_field_decisions(report):
    app = _open_with_report(report)
    app.button[0].click().run()
    assert len(app.exception) == 0
    assert any("Independently assessed 22 source columns" in x.value for x in app.success)
    assert len(app.metric) == 3
    assert sum(int(x.value) for x in app.metric) == 22
    assert len(app.selectbox) == 3
    assert len(app.button) == 1
    assert len(app.file_uploader) == 0


def test_filtering_does_not_rerun_validator(monkeypatch, report):
    calls = []
    original = adapter.inspect_decisions

    def counted(value):
        calls.append("validate")
        return original(value)

    monkeypatch.setattr(adapter, "inspect_decisions", counted)
    app = _open_with_report(report)
    assert calls == []
    app.button[0].click().run()
    assert len(app.exception) == 0
    assert calls == ["validate"]
    app.selectbox[0].set_value("system_b").run()
    app.selectbox[1].set_value("REVIEW").run()
    assert len(app.exception) == 0
    assert calls == ["validate"]
    assert len(app.button) == 1
    assert len(app.file_uploader) == 0


def test_high_similarity_customer_id_decoy_is_rejected_by_backend(report):
    crafted = deepcopy(report)
    item = next(
        x for x in crafted["results"]
        if x["source_system"] == "system_c" and x["source_column"] == "Customer_ID"
    )
    item["semantic_candidates"] = [
        {"canonical_field": "incident_id", "similarity": 0.99},
        {"canonical_field": "incident_status", "similarity": 0.70},
        {"canonical_field": "resolution_minutes", "similarity": 0.62},
        {"canonical_field": "region", "similarity": 0.43},
    ]
    result = adapter.inspect_decisions(crafted)
    decoy = next(
        x for x in result["decisions"]
        if x["source_system"] == "system_c" and x["source_column"] == "Customer_ID"
    )
    assert decoy["decision"] == "REJECT"
    assert "ENTITY_KIND_MISMATCH" in decoy["reason_codes"]
    assert decoy["similarity"] == pytest.approx(0.99)


def test_low_similarity_required_ticket_identifier_not_auto_accepted(report):
    crafted = deepcopy(report)
    item = next(
        x for x in crafted["results"]
        if x["source_system"] == "system_b" and x["source_column"] == "Ticket_No"
    )
    item["semantic_candidates"] = [
        {"canonical_field": "incident_id", "similarity": 0.5391},
        {"canonical_field": "incident_status", "similarity": 0.42},
        {"canonical_field": "region", "similarity": 0.30},
        {"canonical_field": "resolution_minutes", "similarity": 0.21},
    ]
    result = adapter.inspect_decisions(crafted)
    field = next(
        x for x in result["decisions"]
        if x["source_system"] == "system_b" and x["source_column"] == "Ticket_No"
    )
    assert field["decision"] == "REVIEW"
    assert "LOW_SIMILARITY" in field["reason_codes"]


def test_validation_failure_does_not_publish_stale_result(monkeypatch, report):
    def broken(*args, **kwargs):
        raise RuntimeError("Intentional validator failure")

    monkeypatch.setattr(adapter, "inspect_decisions", broken)
    app = _open_with_report(report)
    app.button[0].click().run()
    assert len(app.exception) == 0
    assert any("Validation could not finish safely" in x.value for x in app.error)
    assert any("No validated decisions" in x.value for x in app.info)


def test_changed_fingerprint_clears_previous_candidate_and_decisions(monkeypatch, report):
    current = ["A"]
    monkeypatch.setattr(matching_state, "input_fingerprint", lambda: current[0])
    app = AppTest.from_file(str(ENTRYPOINT)).run()
    app.session_state[matching_state.REPORT_KEY] = deepcopy(report)
    app.session_state[matching_state.FINGERPRINT_KEY] = "A"
    app = app.switch_page("pages/validation.py").run()
    app.button[0].click().run()
    assert len(app.exception) == 0
    assert len(app.metric) == 3
    current[0] = "B"
    app.run()
    assert len(app.exception) == 0
    assert any("stale proposals" in x.value for x in app.warning)
    assert any("No current candidate report" in x.value for x in app.info)
    assert matching_state.REPORT_KEY not in app.session_state
    assert "sb_validation_report" not in app.session_state


def test_validation_never_writes_reports_or_calls_reconciliation(monkeypatch, report):
    def forbidden(*args, **kwargs):
        raise AssertionError("Validation page attempted to reconcile or export")

    monkeypatch.setattr(adapter, "reconcile_candidates", forbidden)
    monkeypatch.setattr(adapter, "prepare_audit_export", forbidden)
    app = _open_with_report(report)
    app.button[0].click().run()
    assert len(app.exception) == 0
    assert len(app.file_uploader) == 0
