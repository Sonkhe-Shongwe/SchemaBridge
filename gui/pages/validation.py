"""P-029 independent, read-only candidate validation UI.

Only the original Python validator authorizes ACCEPT/REVIEW/REJECT. This page
never edits candidate fields, approves user overrides, transforms source rows
or writes outputs. Results are session-bound to the approved input fingerprint.
"""

import logging
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gui import adapter, matching_state

LOG = logging.getLogger(__name__)

VALIDATION_KEY = "sb_validation_report"
VALIDATION_FINGERPRINT_KEY = "sb_validation_fingerprint"
DECISION_ORDER = ("ACCEPT", "REVIEW", "REJECT")

st.title("03 · Validation")
st.write("Explain independent field compatibility decisions for the currently ranked synthetic incident columns.")
st.info(
    "SYNTHETIC DEMO · Read-only backend validation. No user approvals, overrides, "
    "record transformation or file exports."
)
st.caption(
    "A high RapidFuzz or MiniLM similarity is not approval. The existing Python "
    "validator independently checks declared entity kind, type, unit conversion, "
    "category vocabulary, runner-up ambiguity and collisions."
)

try:
    fingerprint = matching_state.input_fingerprint()
except (OSError, ValueError, TypeError, KeyError):
    matching_state.revoke_session_artifacts(st.session_state)
    st.error("The approved synthetic inputs could not be verified; validation is blocked.")
    st.stop()

if matching_state.discard_stale(st.session_state, fingerprint):
    st.warning("Inputs changed; stale proposals and validation results were discarded.")

report = st.session_state.get(matching_state.REPORT_KEY)
report_fingerprint = st.session_state.get(matching_state.FINGERPRINT_KEY)

if report is None or report_fingerprint != fingerprint:
    st.info("No current candidate report. First open **02 · AI Mapping** and click **Run matching (offline)**.")
    st.stop()

try:
    matching_state.display_rows(report)  # Verifies candidate-only stage, scope and unchanged provenance.
except (ValueError, TypeError, KeyError, IndexError):
    st.session_state.pop(matching_state.REPORT_KEY, None)
    st.session_state.pop(matching_state.FINGERPRINT_KEY, None)
    matching_state.invalidate_downstream(st.session_state)
    st.error("The candidate report is invalid; rerun matching before validation.")
    st.stop()

if st.session_state.get(VALIDATION_FINGERPRINT_KEY) != fingerprint:
    st.session_state.pop(VALIDATION_KEY, None)
    st.session_state.pop(VALIDATION_FINGERPRINT_KEY, None)

if st.button("Explain validation decisions", type="primary", key="sb_run_validation"):
    st.session_state.pop(VALIDATION_KEY, None)
    st.session_state.pop(VALIDATION_FINGERPRINT_KEY, None)
    # Never allow a newer report to silently inherit an older reconciliation/export.
    for key in ("sb_reconciliation_report", "sb_reconciliation_fingerprint", "sb_export_manifest"):
        st.session_state.pop(key, None)
    try:
        with st.spinner("Applying independent, deterministic field compatibility rules..."):
            decisions = adapter.inspect_decisions(report)
            if matching_state.input_fingerprint() != fingerprint:
                raise ValueError("Synthetic source data changed during validation")
            if (
                decisions.get("stage") != "P3_FIELD_VALIDATION_PREVIEW_ONLY"
                or decisions.get("gold_labels_accessed") is not False
                or decisions.get("rows_processed") != 0
                or decisions.get("rows_transformed") != 0
                or len(decisions.get("decisions", [])) != len(report["results"])
                or sum(decisions.get("counts", {}).values()) != len(report["results"])
                or set(decisions.get("counts", {})) != set(DECISION_ORDER)
            ):
                raise ValueError("Unsafe or incomplete independent validation response")
    except Exception:
        LOG.exception("Local synthetic field validation failed")
        st.error("Validation could not finish safely. Check the local server log; no decisions were published.")
    else:
        st.session_state[VALIDATION_KEY] = decisions
        st.session_state[VALIDATION_FINGERPRINT_KEY] = fingerprint
        # Keep the shared stage label aligned with this completed action.
        st.rerun()

outcome = st.session_state.get(VALIDATION_KEY)
if outcome is None:
    st.info(
        "No validated decisions in this browser session. Click **Explain validation decisions** "
        "to run the original Python compatibility rules on the current proposals."
    )
    st.stop()

try:
    if (
        outcome["stage"] != "P3_FIELD_VALIDATION_PREVIEW_ONLY"
        or outcome["gold_labels_accessed"] is not False
        or outcome["rows_processed"] != 0
        or outcome["rows_transformed"] != 0
        or sum(outcome["counts"].values()) != len(outcome["decisions"])
        or len(outcome["decisions"]) != len(report["results"])
    ):
        raise ValueError("Untrusted validation report")
    _config, _schema, metadata = adapter._contract()
except (ValueError, TypeError, KeyError):
    st.session_state.pop(VALIDATION_KEY, None)
    st.session_state.pop(VALIDATION_FINGERPRINT_KEY, None)
    st.error("The validation report is no longer trustworthy. Run validation again.")
    st.stop()

st.success(f"Independently assessed {len(outcome['decisions'])} source columns.")
st.caption(
    "Independent backend field decisions, not human sign-off. "
    "ACCEPT is provisional compatibility, REVIEW needs further analysis "
    "and REJECT is incompatible under the declared source rules."
)

accepted, reviewed, rejected = st.columns(3)
accepted.metric("ACCEPT by backend rules", outcome["counts"]["ACCEPT"])
reviewed.metric("REVIEW / manual analysis needed", outcome["counts"]["REVIEW"])
rejected.metric("REJECT / incompatible", outcome["counts"]["REJECT"])

# Compact status composition mirrors only the already checked validator
# counts. It is intentionally not a classifier, approval action or chart score.
_status_palette = (
    ("ACCEPT", "#087F82"),
    ("REVIEW", "#C08C38"),
    ("REJECT", "#BB5362"),
)
_status_total = len(outcome["decisions"])
if _status_total:
    _parts = []
    _legend = []
    for _status, _shade in _status_palette:
        _amount = int(outcome["counts"][_status])
        _parts.append(
            f'<span style="width:{_amount * 100 / _status_total:.5f}%;'
            f'background:{_shade}" aria-hidden="true"></span>'
        )
        _legend.append(
            '<span class="sb-outcome-legend-item">'
            f'<span class="sb-outcome-dot" style="background:{_shade}"></span>'
            f'<span>{_status}</span><strong>{_amount}</strong></span>'
        )
    st.html(
        '<div class="sb-outcome-card">'
        '<div class="sb-outcome-heading">FIELD COMPATIBILITY · INDEPENDENT VALIDATOR</div>'
        '<div class="sb-outcome-track" role="img" aria-label="'
        + ", ".join(
            f"{code}: {int(outcome['counts'][code])}"
            for code, _shade in _status_palette
        )
        + '">' + "".join(_parts) + '</div>'
        '<div class="sb-outcome-legend">' + "".join(_legend) + '</div>'
        '</div>'
    )
st.caption(
    "All three totals come directly from the original Python validator. "
    "ACCEPT does not prove semantic mapping correctness; source-row disposition "
    "and audited output have not been calculated here."
)

st.subheader("Source-by-source decision overview")
st.caption(
    "These are original validator field-level decisions, not record outcomes. "
    "Do not confuse the number of decided fields with 180 synthetic input rows."
)
systems = sorted({item["source_system"] for item in outcome["decisions"]})
source_cards = st.columns(len(systems))
for card, source_key in zip(source_cards, systems):
    decisions_for_source = [
        item for item in outcome["decisions"]
        if item["source_system"] == source_key
    ]
    with card:
        with st.container(border=True):
            st.markdown(
                "**" + metadata["systems"][source_key].get(
                    "display_name", source_key
                ) + "**"
            )
            st.caption("Source key: " + source_key)
            st.write(f"{len(decisions_for_source)} source fields assessed")
            st.write(
                "ACCEPT: "
                + str(sum(d["decision"] == "ACCEPT" for d in decisions_for_source))
                + " · REVIEW: "
                + str(sum(d["decision"] == "REVIEW" for d in decisions_for_source))
                + " · REJECT: "
                + str(sum(d["decision"] == "REJECT" for d in decisions_for_source))
            )

st.subheader("Inspect validation decisions")
st.caption(
    "Source and decision filters examine the existing report only. "
    "Changing filters never reruns MiniLM, the independent validator or export."
)
system = st.selectbox(
    "Source system",
    systems,
    format_func=lambda key: metadata["systems"][key].get("display_name", key),
    key="sb_validation_system",
)
filter_decision = st.selectbox(
    "Decision filter",
    ["All", *DECISION_ORDER],
    key="sb_validation_filter",
)
current = [
    item for item in outcome["decisions"]
    if item["source_system"] == system and
    (filter_decision == "All" or item["decision"] == filter_decision)
]

st.subheader("Actual compatibility decisions")
st.caption(
    f"Showing {len(current)} of "
    f"{sum(item['source_system'] == system for item in outcome['decisions'])} "
    "source columns for this system. Filters do not rerun either model or the validator."
)
st.dataframe(
    pd.DataFrame([
        {
            "Source column": item["source_column"],
            "Chosen target": item["proposed_target"] or "(none)",
            "Decision": item["decision"],
            "Method": item["method"],
            "Similarity (not confidence)": item["similarity"],
            "Selected candidate rank": item["selected_candidate_rank"],
            "Selection strategy": item["selection_strategy"],
            "Reason codes": ", ".join(item["reason_codes"]),
        }
        for item in current
    ]),
    hide_index=True,
    width="stretch",
)

columns = [item["source_column"] for item in current]
if columns:
    selected = st.selectbox(
        "Explain one source column",
        columns,
        key="sb_validation_column_" + system + "_" + filter_decision,
    )
    item = next(x for x in current if x["source_column"] == selected)
    definition = metadata["systems"][system]["columns"][selected]

    st.subheader("Independent decision detail")
    with st.container(border=True):
        st.markdown(
            "**" + selected + " → " +
            (item["proposed_target"] or "(no target)") +
            ": " + item["decision"] + "**"
        )
        st.caption(
            "Exact source field and backend-selected target. Neither an "
            "alternative nor a similarity score grants human approval."
        )
        st.write("**Declared source meaning:** " + str(definition.get("description") or "Not declared"))
        st.write(
            "Declared entity: " + str(definition.get("entity_kind") or "not declared")
            + " · Type: " + str(definition.get("declared_type") or "not declared")
            + " · Unit: " + str(definition.get("unit") or "not applicable")
        )
        st.write(
            "**Actual backend selection:** " + item["selection_strategy"]
            + " · Method: " + str(item["method"])
            + " · Candidate rank: "
            + (str(item["selected_candidate_rank"])
               if item["selected_candidate_rank"] is not None else "none")
        )
        st.write(
            "**Backend similarity (not calibrated confidence):** "
            + (str(item["similarity"])
               if item["similarity"] is not None else "not available")
        )
        st.write(
            "**Top-two margin:** "
            + (str(item["top_two_margin"])
               if item["top_two_margin"] is not None else "not available")
        )
        st.write(
            "**Independent validator reason codes:** "
            + ", ".join(item["reason_codes"])
        )
        st.caption(
            "Reason codes are preserved exactly as returned by the validator; "
            "no codes or outcomes are inferred by this interface."
        )
        if item.get("original_top_candidate") != item.get("proposed_target"):
            st.warning(
                "COMPATIBLE ALTERNATIVE: the originally highest-ranked "
                "target was incompatible. A separately examined alternative "
                "is not automatically correct or approved."
            )
            st.write(
                "**Original top-ranked target:** "
                + str(item["original_top_candidate"])
            )
            st.write(
                "**Original incompatibility:** "
                + ", ".join(item["primary_rejection_reasons"])
            )
            st.write(
                "**Original top-two margin:** "
                + (str(item["original_top_two_margin"])
                   if item["original_top_two_margin"] is not None
                   else "not available")
            )
        elif item["decision"] == "REVIEW":
            st.warning(
                "REVIEW required. This field is not accepted, regardless of "
                "its rank or similarity. Continue only under independent backend rules."
            )
        elif item["decision"] == "REJECT":
            st.error(
                "REJECT by independent backend compatibility rules. "
                "No display action can reverse this decision."
            )
        if item.get("conversion") is not None:
            factor = item["conversion"]
            st.caption(
                f"Backend-declared conversion: {factor['from_unit']} to "
                f"{factor['to_unit']}, factor {factor['factor']}. "
                "No source records have been converted on this page."
            )

    if (
        item["decision"] == "REVIEW"
        and definition.get("entity_kind") == "incident_identifier"
    ):
        st.warning(
            "This incident identifier remains REVIEW, including when "
            "a required canonical incident_id is unresolved. "
            "The interface does not guess IDs or bypass the independent validator."
        )
else:
    st.info(
        "There are no source fields for this decision filter. "
        "Change the filter to inspect existing backend decisions."
    )

if outcome.get("collisions"):
    with st.expander(f"Validator target collisions ({len(outcome['collisions'])})"):
        st.caption(
            "These original collisions hold all competing fields for REVIEW; "
            "no last-column-wins behavior or manual override."
        )
        st.dataframe(
            pd.DataFrame(outcome["collisions"]),
            hide_index=True,
            width="stretch",
        )

st.warning(
    "Field decisions only. A compatible alternative is not automatically "
    "a correct or accepted mapping. Even ACCEPT is not guaranteed correctness. "
    "No row transformations, cross-system joins, edits, overrides or exports "
    "occur on this page."
)
