"""P-030 reconciliation and P-031 no-implicit-export regression tests.

All ranking uses an injected local FakeEncoder. Tests use real fictional
source fixtures and the original validator/reconciler, not scored holdout.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from gui import adapter, matching_state, reconciliation_view

ENTRYPOINT = Path(__file__).resolve().parents[1] / "streamlit_app.py"


class FakeEncoder:
    def encode(self, texts, *, normalize_embeddings, convert_to_numpy, show_progress_bar):
        assert normalize_embeddings and convert_to_numpy and not show_progress_bar
        return np.eye(len(texts), dtype=np.float64)


@pytest.fixture(scope="module")
def bundled():
    ranked = adapter.rank_candidates(model=FakeEncoder())
    validation = adapter.inspect_decisions(ranked)
    result = adapter.reconcile_candidates(ranked)
    return ranked, validation, result


def open_ready(ranked, validation):
    app = AppTest.from_file(str(ENTRYPOINT)).run()
    fingerprint = matching_state.input_fingerprint()
    app.session_state[matching_state.REPORT_KEY] = deepcopy(ranked)
    app.session_state[matching_state.FINGERPRINT_KEY] = fingerprint
    app.session_state["sb_validation_report"] = deepcopy(validation)
    app.session_state["sb_validation_fingerprint"] = fingerprint
    return app.switch_page("pages/results.py").run()


def test_idle_requires_validated_report_and_does_not_reconcile(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Reconciliation without independent validation is forbidden")

    monkeypatch.setattr(adapter, "reconcile_candidates", forbidden)
    app = AppTest.from_file(str(ENTRYPOINT)).run().switch_page("pages/results.py").run()
    assert len(app.exception) == 0
    assert len(app.button) == 0
    assert len(app.file_uploader) == 0
    assert any("No current independently validated candidate report" in x.value for x in app.info)


def test_candidates_without_validation_cannot_reconcile(bundled):
    ranked, _, _ = bundled
    app = AppTest.from_file(str(ENTRYPOINT)).run()
    app.session_state[matching_state.REPORT_KEY] = deepcopy(ranked)
    app.session_state[matching_state.FINGERPRINT_KEY] = matching_state.input_fingerprint()
    app.switch_page("pages/results.py").run()
    assert len(app.exception) == 0
    assert len(app.button) == 0


def test_explicit_reconciliation_button_is_only_action(monkeypatch, bundled):
    ranked, validation, _ = bundled

    def forbidden(*args, **kwargs):
        raise AssertionError("Opening the Results page may not run the engine")

    monkeypatch.setattr(adapter, "reconcile_candidates", forbidden)
    app = open_ready(ranked, validation)
    assert len(app.exception) == 0
    assert len(app.button) == 1
    assert app.button[0].label == "Run reconciliation (read-only)"
    assert any("No reconciled records" in x.value for x in app.info)
    assert len(app.file_uploader) == 0



def test_cross_screen_navigation_with_precomputed_state_never_recomputes_or_writes(
    monkeypatch, bundled
):
    """GUI-AC08: navigation alone cannot infer, validate, reconcile or export.

    Uses complete synthetic session reports from the real backend with a fake
    in-memory encoder. No model downloads, holdout labels or filesystem writes.
    """
    ranked, validation, result = bundled
    app = AppTest.from_file(str(ENTRYPOINT)).run()
    fingerprint = matching_state.input_fingerprint()
    app.session_state[matching_state.REPORT_KEY] = deepcopy(ranked)
    app.session_state[matching_state.FINGERPRINT_KEY] = fingerprint
    app.session_state["sb_validation_report"] = deepcopy(validation)
    app.session_state["sb_validation_fingerprint"] = fingerprint
    app.session_state["sb_reconciliation_report"] = deepcopy(result)
    app.session_state["sb_reconciliation_fingerprint"] = fingerprint

    calls = []

    def forbidden(*args, **kwargs):
        calls.append("unexpected backend action")
        raise AssertionError("Page navigation must never infer, validate, reconcile or write")

    monkeypatch.setattr(adapter, "rank_candidates", forbidden)
    monkeypatch.setattr(adapter, "inspect_decisions", forbidden)
    monkeypatch.setattr(adapter, "reconcile_candidates", forbidden)
    monkeypatch.setattr(adapter, "prepare_audit_export", forbidden)
    monkeypatch.setattr(adapter, "write_demo_output", forbidden)

    for page in (
        "pages/sources.py",
        "pages/matching.py",
        "pages/validation.py",
        "pages/results.py",
        "pages/matching.py",
        "pages/sources.py",
        "pages/results.py",
        "pages/validation.py",
    ):
        app.switch_page(page).run()
        assert len(app.exception) == 0, f"Navigation failed on {page}"
        assert app.session_state[matching_state.FINGERPRINT_KEY] == fingerprint
        assert app.session_state["sb_validation_fingerprint"] == fingerprint
        assert app.session_state["sb_reconciliation_fingerprint"] == fingerprint

    assert calls == []
    assert len(app.download_button) == 0

def test_existing_engine_accounts_for_180_fictional_input_records(bundled):
    _ranked, validation, result = bundled
    summary = reconciliation_view.summarize(result, validation)
    assert summary["input_records"] == 180
    assert len(summary["per_source"]) == 3
    assert all(s["input_records"] == 60 for s in summary["per_source"])
    assert sum(summary["counts"].values()) == 180
    assert summary["standardisable_records"] == len(result["rows"])
    assert summary["exception_records"] == (
        summary["counts"]["HELD_FOR_REVIEW"] + summary["counts"]["QUARANTINED"]
    )
    assert summary["reason_events"] == len(result["issues"])
    assert summary["reason_events"] >= summary["exception_records"]
    assert summary["evaluation_is_placeholder"] is True


def test_reconciliation_requires_same_current_independent_validator(bundled):
    _ranked, validation, result = bundled
    tampered = deepcopy(validation)
    tampered["decisions"][0]["decision"] = "REVIEW"
    with pytest.raises(ValueError, match="current independent validation"):
        reconciliation_view.summarize(result, tampered)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("row_count", 181),
        ("cross_system_joins", 1),
        ("gold_labels_accessed", True),
        ("stage", "UNTRUSTED_STAGE"),
    ],
)
def test_tampered_reconciliation_is_rejected(bundled, field, value):
    _ranked, validation, result = bundled
    changed = deepcopy(result)
    changed["mapping_report"][field] = value
    with pytest.raises(ValueError):
        reconciliation_view.summarize(changed, validation)


def test_tampered_system_accounting_is_rejected(bundled):
    _ranked, validation, result = bundled
    changed = deepcopy(result)
    changed["mapping_report"]["systems"]["system_b"]["rows_read"] = 999
    with pytest.raises(ValueError, match="Per-system row mismatch"):
        reconciliation_view.summarize(changed, validation)


def test_invalid_issue_event_disposition_is_rejected(bundled):
    _ranked, validation, result = bundled
    changed = deepcopy(result)
    assert changed["issues"]
    changed["issues"][0]["disposition"] = "ACCEPTED"
    with pytest.raises(ValueError, match="Invalid exception reason event"):
        reconciliation_view.summarize(changed, validation)


def test_explicit_click_displays_genuine_engine_rows_without_export(bundled, monkeypatch):
    ranked, validation, result = bundled
    calls = []

    def original_result(_candidates):
        calls.append("reconcile")
        return deepcopy(result)

    def forbidden(*args, **kwargs):
        raise AssertionError("P030 must never prepare, write or download an audited report")

    monkeypatch.setattr(adapter, "reconcile_candidates", original_result)
    monkeypatch.setattr(adapter, "prepare_audit_export", forbidden)
    app = open_ready(ranked, validation)
    assert calls == []
    app.button[0].click().run()
    assert len(app.exception) == 0
    assert calls == ["reconcile"]
    assert len(app.metric) == 4
    assert int(app.metric[0].value) == 180
    assert int(app.metric[1].value) == len(result["rows"])
    assert (int(app.metric[1].value) + int(app.metric[2].value)
            + int(app.metric[3].value)) == 180
    assert len(app.button) == 2
    assert app.button[1].label == "Prepare audited reports (write once)"
    assert len(app.download_button) == 0
    assert len(app.file_uploader) == 0
    assert any("No CSV/JSON file has been generated" in x.value for x in app.warning)


def test_source_and_disposition_filters_do_not_rerun_engine(monkeypatch, bundled):
    ranked, validation, result = bundled
    count = []

    def backend(_candidates):
        count.append("once")
        return deepcopy(result)

    monkeypatch.setattr(adapter, "reconcile_candidates", backend)
    app = open_ready(ranked, validation)
    app.button[0].click().run()
    assert len(app.exception) == 0
    assert count == ["once"]
    assert len(app.selectbox) == 2
    app.selectbox[0].set_value("system_b").run()
    app.selectbox[1].set_value("QUARANTINED").run()
    assert len(app.exception) == 0
    assert count == ["once"]
    app.run()
    assert count == ["once"]


def test_failed_reconciliation_discards_previous_report(monkeypatch, bundled):
    ranked, validation, result = bundled

    def fake(_candidate):
        return deepcopy(result)

    monkeypatch.setattr(adapter, "reconcile_candidates", fake)
    app = open_ready(ranked, validation)
    app.button[0].click().run()
    assert len(app.exception) == 0
    assert "sb_reconciliation_report" in app.session_state

    def broken(*args, **kwargs):
        raise RuntimeError("intentional mocked engine failure")

    monkeypatch.setattr(adapter, "reconcile_candidates", broken)
    app.button[0].click().run()
    assert len(app.exception) == 0
    assert "sb_reconciliation_report" not in app.session_state
    assert any("Reconciliation was blocked or failed" in x.value for x in app.error)


def test_changed_fingerprint_clears_cached_validation_and_reconciliation(monkeypatch, bundled):
    ranked, validation, result = bundled
    current = ["before"]
    monkeypatch.setattr(matching_state, "input_fingerprint", lambda: current[0])
    monkeypatch.setattr(adapter, "reconcile_candidates", lambda _: deepcopy(result))
    app = AppTest.from_file(str(ENTRYPOINT)).run()
    app.session_state[matching_state.REPORT_KEY] = deepcopy(ranked)
    app.session_state[matching_state.FINGERPRINT_KEY] = "before"
    app.session_state["sb_validation_report"] = deepcopy(validation)
    app.session_state["sb_validation_fingerprint"] = "before"
    app.switch_page("pages/results.py").run()
    app.button[0].click().run()
    assert len(app.exception) == 0
    assert "sb_reconciliation_report" in app.session_state
    current[0] = "after"
    app.run()
    assert len(app.exception) == 0
    assert "sb_reconciliation_report" not in app.session_state
    assert "sb_validation_report" not in app.session_state
    assert any("No current independently validated candidate report" in x.value for x in app.info)


def test_previous_scored_evaluation_fabrication_is_refused(bundled):
    _ranked, validation, result = bundled
    changed = deepcopy(result)
    changed["evaluation"]["metrics_computed"] = True
    with pytest.raises(ValueError, match="fabricated scored evaluation"):
        reconciliation_view.summarize(changed, validation)
