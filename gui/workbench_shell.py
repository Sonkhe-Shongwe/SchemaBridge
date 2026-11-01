"""UX2-04 presentation-only shared shell, truthful session-state labels.

No Streamlit dependency in state derivation. No model, validator, reconciliation,
writer, network, or output-file access. Presence of a report is never treated
as independent proof of ACCEPT, audit integrity, or final stage completion.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from gui import matching_state


@dataclass(frozen=True)
class StageView:
    label: str
    explanation: str


IDLE = StageView(
    "IDLE",
    "No candidate report in this browser session. Navigation does not run AI.",
)
STALE = StageView(
    "STALE / UNVERIFIED",
    "Saved stage information is missing, invalid or belongs to changed inputs. "
    "Open the relevant stage to recheck it; old reports are not accepted here.",
)
RANKED = StageView(
    "PROPOSALS PRESENT",
    "Candidate report stored for the current inputs. Similarities are not "
    "probabilities or independently accepted mappings.",
)
VALIDATION = StageView(
    "VALIDATION REPORT PRESENT",
    "Independent decision report stored for current inputs; open Validation "
    "to check its contents. REVIEW never becomes ACCEPT by navigation.",
)
RECONCILED = StageView(
    "RECONCILIATION REPORT PRESENT",
    "Read-only reconciliation is stored. This is not evidence that audited "
    "files have been prepared.",
)
MANIFEST = StageView(
    "AUDIT MANIFEST PRESENT",
    "A manifest record exists. Open Results & Export to recheck exact files "
    "and hashes; this indicator never claims an export is VERIFIED.",
)
UNAVAILABLE = StageView(
    "INPUTS UNAVAILABLE",
    "Current approved synthetic inputs could not be fingerprinted. "
    "No stored candidate or output state is considered current.",
)


def derive_stage(
    state: Mapping[str, object],
    current_fingerprint: str | None = None,
    *,
    inputs_available: bool = True,
) -> StageView:
    """Conservative read-only label based on actual session fields.

    This does not validate report contents or audited files. We deliberately
    avoid returning a completed/approved/exported state from mere key presence.
    """
    keys = (
        matching_state.REPORT_KEY,
        matching_state.FINGERPRINT_KEY,
        "sb_validation_report",
        "sb_validation_fingerprint",
        "sb_reconciliation_report",
        "sb_reconciliation_fingerprint",
        "sb_export_manifest",
    )
    if not any(key in state for key in keys):
        return IDLE
    if not inputs_available:
        return UNAVAILABLE
    report = state.get(matching_state.REPORT_KEY)
    if (
        not isinstance(current_fingerprint, str)
        or len(current_fingerprint) != 64
        or not isinstance(report, Mapping)
        or state.get(matching_state.FINGERPRINT_KEY) != current_fingerprint
        or report.get("stage") != "P2_CANDIDATE_RANKING_ONLY"
        or report.get("methods") != "both"
        or report.get("mapping_validation_performed") is not False
        or report.get("data_transformed") is not False
        or report.get("gold_labels_accessed") is not False
    ):
        return STALE

    validation = state.get("sb_validation_report")
    if validation is None:
        if any(k in state for k in (
            "sb_validation_fingerprint",
            "sb_reconciliation_report", "sb_reconciliation_fingerprint",
            "sb_export_manifest",
        )):
            return STALE
        return RANKED
    if (
        not isinstance(validation, Mapping)
        or state.get("sb_validation_fingerprint") != current_fingerprint
        or validation.get("stage") != "P3_FIELD_VALIDATION_PREVIEW_ONLY"
        or validation.get("gold_labels_accessed") is not False
        or validation.get("rows_transformed") != 0
        or validation.get("rows_processed") != 0
    ):
        return STALE
    reconciliation = state.get("sb_reconciliation_report")
    if reconciliation is None:
        if any(k in state for k in (
            "sb_reconciliation_fingerprint", "sb_export_manifest",
        )):
            return STALE
        return VALIDATION
    if (
        not isinstance(reconciliation, Mapping)
        or state.get("sb_reconciliation_fingerprint") != current_fingerprint
        or not isinstance(reconciliation.get("mapping_report"), Mapping)
        or reconciliation["mapping_report"].get("stage")
        != "P3_SYNTHETIC_RECONCILIATION"
    ):
        return STALE

    if "sb_export_manifest" in state:
        if not isinstance(state["sb_export_manifest"], Mapping):
            return STALE
        return MANIFEST
    return RECONCILED


def session_stage(state: Mapping[str, object]) -> StageView:
    """Read only approved-input fingerprint when saved workflow state exists."""
    if not any(key in state for key in (
        matching_state.REPORT_KEY,
        matching_state.FINGERPRINT_KEY,
        *matching_state.DOWNSTREAM_KEYS,
    )):
        return IDLE
    try:
        fingerprint = matching_state.input_fingerprint()
    except (OSError, ValueError, TypeError, KeyError):
        return derive_stage(state, inputs_available=False)
    return derive_stage(state, current_fingerprint=fingerprint)


def render_shell(state: Mapping[str, object]) -> None:
    """Branded native shell. No page changes and no expensive side effects."""
    stylesheet = Path(__file__).resolve().parents[1] / (
        "design/probes/ux2_light_focus_candidate.css"
    )
    # Supported static CSS path, no DOM replacement or browser scripting.
    st.html("<style>\n" + stylesheet.read_text(encoding="utf-8") + "\n</style>")

    logo = Path(__file__).resolve().parents[1] / "design/brand/schemabridge-mark.svg"
    st.sidebar.image(str(logo), width=48)
    st.sidebar.markdown("### SchemaBridge")
    st.sidebar.caption("LOCAL · OFFLINE · SYNTHETIC ONLY")
    stage = session_stage(state)
    st.sidebar.info("Session stage: **" + stage.label + "**")
    st.sidebar.caption(stage.explanation)
    st.sidebar.caption("Four stages · review each report in its page")

    st.caption(
        "SCHEMABRIDGE  /  LOCAL SYNTHETIC DEMO  /  "
        "INDEPENDENT VALIDATION  /  GUARDED EXPORT"
    )
