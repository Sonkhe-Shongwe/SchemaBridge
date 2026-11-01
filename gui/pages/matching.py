"""P-028 offline candidate-only matching UI, with explicit inference action.

No automatic model load on navigation or filtering, no runtime evaluation
labels, no validation bypass, no arbitrary upload and no file export.
"""

from pathlib import Path
import logging
import sys

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gui import adapter, matching_state

LOG = logging.getLogger(__name__)

st.title("02 · AI Mapping")
st.write("Explore candidate matches between fictional incident-export columns and the canonical schema.")
st.info(
    "SYNTHETIC DEMO · This page ranks suggestions ONLY. "
    "It does not accept mappings, reconcile records or create exports."
)
st.caption(
    "RapidFuzz uses name similarity; MiniLM uses semantic similarity. "
    "Scores are NOT probabilities or calibrated confidence levels. "
    "The independent compatibility validator runs in the next stage."
)

try:
    fingerprint = matching_state.input_fingerprint()
except (OSError, ValueError, TypeError, KeyError):
    matching_state.revoke_session_artifacts(st.session_state)
    st.error("The approved synthetic matching inputs could not be verified. Check local demo configuration.")
    st.stop()

if matching_state.discard_stale(st.session_state, fingerprint):
    st.warning("Demo inputs changed. Previous candidate suggestions were discarded. Run matching again.")

if st.button("Run matching (offline)", type="primary", key="sb_run_matching"):
    st.session_state.pop(matching_state.REPORT_KEY, None)
    st.session_state.pop(matching_state.FINGERPRINT_KEY, None)
    matching_state.invalidate_downstream(st.session_state)
    try:
        with st.spinner("Loading cached MiniLM locally and ranking the declared source columns..."):
            report = adapter.rank_candidates()
            # Avoid publishing a report computed across an input change.
            if matching_state.input_fingerprint() != fingerprint:
                raise ValueError("Synthetic input files changed during matching")
            matching_state.display_rows(report)
    except Exception:
        # Keep exception details in local server logs, not in public UI.
        LOG.exception("Local offline SchemaBridge matching failed")
        st.error(
            "Offline matching could not complete. Verify the locally cached MiniLM model "
            "and unchanged synthetic demo files, then try again. No mappings were approved."
        )
    else:
        st.session_state[matching_state.REPORT_KEY] = report
        st.session_state[matching_state.FINGERPRINT_KEY] = fingerprint
        # The shared shell rendered before this explicit action. Refresh it
        # from the newly stored report without running matching a second time.
        st.rerun()

report = st.session_state.get(matching_state.REPORT_KEY)
if report is None:
    st.info(
        "No candidate report in this browser session. Click **Run matching (offline)** "
        "to invoke the real RapidFuzz and locally cached MiniLM engine."
    )
    st.stop()

try:
    all_rows = matching_state.display_rows(report)
    _demo, _schema, metadata = adapter._contract()
except (ValueError, TypeError, KeyError, IndexError):
    st.session_state.pop(matching_state.REPORT_KEY, None)
    st.session_state.pop(matching_state.FINGERPRINT_KEY, None)
    matching_state.invalidate_downstream(st.session_state)
    st.error("The candidate report is no longer valid; run matching again.")
    st.stop()

st.success(f"Generated {len(all_rows)} source-column proposals using both ranking methods.")
st.caption(
    "PROPOSED ONLY · NOT_EVALUATED. These are ranking suggestions, not accepted "
    "canonical mappings. The independent Python validator decides compatibility "
    "in the Validation stage."
)

# Source and method counts are based on the actual validated candidate report,
# never hardcoded from storyboard illustration or inferred from user selection.
systems = sorted({row["source_system"] for row in all_rows})
top_a, top_b, top_c = st.columns(3)
top_a.metric("Source columns ranked", len(all_rows))
top_b.metric("Fictional sources compared", len(systems))
top_c.metric("Ranking methods", len(("RapidFuzz", "MiniLM")))

st.subheader("Candidate workbench")
st.caption(
    "Choose one source and field for read-only inspection. Filtering a stored "
    "report never loads MiniLM again, runs the validator or changes any mapping."
)
source_columns = st.columns(len(systems))
for column, system in zip(source_columns, systems):
    members = [row for row in all_rows if row["source_system"] == system]
    with column:
        with st.container(border=True):
            st.markdown("**" + metadata["systems"][system].get("display_name", system) + "**")
            st.caption("Source key: " + system)
            st.write(str(len(members)) + " fields with proposals")
            st.caption("Decision: NOT_EVALUATED")

chosen_system = st.selectbox(
    "Source system",
    systems,
    format_func=lambda x: metadata["systems"][x].get("display_name", x),
    key="sb_matching_system",
)
shown = [row for row in all_rows if row["source_system"] == chosen_system]

st.subheader("Top candidate comparison")
st.caption(
    "Actual highest-ranked alternatives from the stored RapidFuzz and MiniLM "
    "reports, shown for each declared field in this source. Similarity values "
    "are raw backend numbers, not percentages, probabilities or validation."
)
table_rows = [
    {
        "Source column": row["source_column"],
        "Declared entity": row["entity_kind"],
        "Type": row["declared_type"],
        "Unit": row["unit"],
        "RapidFuzz candidate": row["lexical_top"],
        "Name similarity": row["lexical_similarity"],
        "MiniLM candidate": row["semantic_top"],
        "Semantic similarity": row["semantic_similarity"],
        "Decision": "NOT_EVALUATED",
    }
    for row in shown
]
st.dataframe(pd.DataFrame(table_rows), hide_index=True, width="stretch")

# A key scoped to the selected source prevents cross-source field leakage.
chosen_column = st.selectbox(
    "Inspect all ranked alternatives for a column",
    [row["source_column"] for row in shown],
    key="sb_matching_column_" + chosen_system,
)
source_row = next(row for row in shown if row["source_column"] == chosen_column)
raw_item = next(
    item for item in report["results"]
    if item["source_system"] == chosen_system and item["source_column"] == chosen_column
)

st.subheader("Selected source field")
with st.container(border=True):
    st.markdown("**" + source_row["source_name"] + " · " + chosen_column + "**")
    st.caption("Declared business meaning, not a validated canonical match")
    st.write(source_row["description"] or "No declared description")
    st.write(
        "Declared entity: " + (source_row["entity_kind"] or "unspecified")
        + " · Type: " + (source_row["declared_type"] or "unspecified")
        + " · Unit: " + (source_row["unit"] or "not applicable")
    )
    st.caption(
        "Ranking state: NOT_EVALUATED · Original source field and provenance "
        "are preserved. No value has been transformed."
    )

st.subheader("Ranked alternatives by method")
st.caption(
    "Both panels display the original candidate order, target field and raw "
    "similarity from the same cached session report. Rank 1 means first "
    "suggestion, NOT automatic approval."
)
lexical_col, semantic_col = st.columns(2)
for target, title, method, candidates in (
    (lexical_col, "RapidFuzz · lexical name similarity",
     "RapidFuzz", raw_item["lexical_candidates"]),
    (semantic_col, "MiniLM · offline semantic similarity",
     "MiniLM", raw_item["semantic_candidates"]),
):
    with target:
        with st.container(border=True):
            st.markdown("**" + title + "**")
            st.caption(
                f"{len(candidates)} stored {method} alternatives · "
                "Similarity only · NOT_EVALUATED"
            )
            st.dataframe(
                pd.DataFrame([
                    {
                        "Rank": rank,
                        "Canonical field": candidate["canonical_field"],
                        "Similarity": candidate["similarity"],
                    }
                    for rank, candidate in enumerate(candidates, 1)
                ]),
                hide_index=True,
                width="stretch",
            )

if source_row["lexical_top"] != source_row["semantic_top"]:
    st.info(
        "The two methods rank different canonical fields first for this "
        "source column. This is a comparison of proposals, not an "
        "approval/rejection or proof that either candidate is correct."
    )
else:
    st.info(
        "Both methods rank the same field first. Agreement between rankings "
        "is NOT an independent compatibility decision."
    )

st.warning(
    "PROPOSED ONLY · NOT_EVALUATED. Similarity is not calibrated confidence. "
    "No candidate is accepted here. Required incident IDs, business meaning, "
    "scope, types, units, status vocabulary and target collisions must pass "
    "the original independent validator. No manual approval or export."
)
