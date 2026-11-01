"""UX2-04 pure status derivation and protected Streamlit shell.

Do not run original matching/validation/reconciliation/output in stage tests.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from streamlit.testing.v1 import AppTest

from gui import matching_state, workbench_shell


FP = "a" * 64
ENTRYPOINT = Path(__file__).resolve().parents[1] / "streamlit_app.py"


def ranked_state() -> dict:
    return {
        matching_state.REPORT_KEY: {
            "stage": "P2_CANDIDATE_RANKING_ONLY",
            "methods": "both",
            "mapping_validation_performed": False,
            "data_transformed": False,
            "gold_labels_accessed": False,
        },
        matching_state.FINGERPRINT_KEY: FP,
    }


def test_idle_has_no_false_completion_and_does_not_require_fingerprint(monkeypatch):
    def blocked():
        raise AssertionError("No fingerprint required in an empty session")
    monkeypatch.setattr(matching_state, "input_fingerprint", blocked)
    assert workbench_shell.session_stage({}) == workbench_shell.IDLE
    assert workbench_shell.derive_stage({}) == workbench_shell.IDLE


def test_saved_candidate_is_proposal_only_never_acceptance():
    state = ranked_state()
    assert workbench_shell.derive_stage(state, FP) == workbench_shell.RANKED
    assert workbench_shell.derive_stage(state, "b" * 64) == workbench_shell.STALE
    assert workbench_shell.derive_stage(state, inputs_available=False) == workbench_shell.UNAVAILABLE


def test_invalid_or_orphaned_stage_is_stale():
    state = ranked_state()
    state[matching_state.REPORT_KEY]["mapping_validation_performed"] = True
    assert workbench_shell.derive_stage(state, FP) == workbench_shell.STALE
    orphan = {"sb_validation_report": {"stage": "P3_FIELD_VALIDATION_PREVIEW_ONLY"}}
    assert workbench_shell.derive_stage(orphan, FP) == workbench_shell.STALE


def test_later_stage_names_are_explicitly_unverified():
    state = ranked_state()
    state["sb_validation_report"] = {
        "stage": "P3_FIELD_VALIDATION_PREVIEW_ONLY",
        "gold_labels_accessed": False,
        "rows_transformed": 0,
        "rows_processed": 0,
    }
    state["sb_validation_fingerprint"] = FP
    assert workbench_shell.derive_stage(state, FP) == workbench_shell.VALIDATION
    state["sb_reconciliation_report"] = {
        "mapping_report": {"stage": "P3_SYNTHETIC_RECONCILIATION"},
    }
    state["sb_reconciliation_fingerprint"] = FP
    assert workbench_shell.derive_stage(state, FP) == workbench_shell.RECONCILED
    state["sb_export_manifest"] = {"run_id": "unverified"}
    assert workbench_shell.derive_stage(state, FP) == workbench_shell.MANIFEST
    assert "VERIFIED" not in workbench_shell.MANIFEST.label
    state["sb_reconciliation_fingerprint"] = "b" * 64
    assert workbench_shell.derive_stage(state, FP) == workbench_shell.STALE


def test_stage_observation_does_not_mutate_session():
    state = ranked_state()
    state["sb_validation_report"] = {"stage": "invalid"}
    before = deepcopy(state)
    workbench_shell.derive_stage(state, FP)
    assert state == before


def test_router_navigation_is_idle_no_backend_implicit_calls(monkeypatch):
    from gui import adapter

    def forbidden(*args, **kwargs):
        raise AssertionError("Navigation must not run processing")

    for name in (
        "rank_candidates", "inspect_decisions",
        "reconcile_candidates", "prepare_audit_export",
    ):
        monkeypatch.setattr(adapter, name, forbidden)
    app = AppTest.from_file(str(ENTRYPOINT)).run()
    assert len(app.exception) == 0
    assert any("Session stage: **IDLE**" in x.value for x in app.info)
    for page in ("pages/matching.py", "pages/validation.py", "pages/results.py"):
        app.switch_page(page).run()
        assert len(app.exception) == 0
        # AppTest.switch_page executes the page module without the router shell.
        # Navigation safety is checked here; the banner is asserted on entry.
        assert len(app.file_uploader) == 0
