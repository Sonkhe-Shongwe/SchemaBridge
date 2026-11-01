"""UX2-05 read-only approved fictional source overview and field inspector.

All counts, source identity, previews, descriptions and quality statistics are
obtained from the original synthetic-only adapter. Nothing infers, mutates,
transforms, validates, reconciles or writes audit files on this page.
"""

from pathlib import Path
import sys

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gui.adapter import load_sources


def quality_text(value):
    """Show not-applicable separately from a measured zero."""
    return "Not applicable" if value is None else str(value)


st.title("01 · Data Sources")
st.write("Inspect the original fictional incident exports before any mapping or transformation.")
st.info(
    "SYNTHETIC DEMO · Read-only local source inspection. "
    "Changing a source or field never runs AI matching, validation, "
    "reconciliation or export."
)

try:
    summary = load_sources(preview_limit=5)
except (OSError, ValueError, TypeError, KeyError):
    # Machine paths or unapproved content must never be shown in an error.
    st.error(
        "The approved synthetic sources could not be loaded. "
        "Check the local fictional CSV files and declared metadata."
    )
    st.stop()

left, middle, right = st.columns(3)
left.metric("Source systems", summary["source_count"])
middle.metric("Input records", summary["row_count"])
right.metric("Declared source columns", summary["source_column_count"])

st.subheader("Approved fictional source inventory")
st.caption(
    "Counts come from the original CSVs and declared metadata, not "
    "a hardcoded dashboard. All source systems remain separate."
)
cards = st.columns(3)
for card, item in zip(cards, summary["systems"]):
    with card:
        with st.container(border=True):
            st.markdown("**" + item["display_name"] + "**")
            st.caption("Source key: " + item["source_system"])
            st.write(str(item["rows_read"]) + " source records")
            st.write(str(len(item["columns"])) + " declared columns")
            st.caption("Approved fixture: " + item["source_file"])

systems = {source["source_system"]: source for source in summary["systems"]}
if not systems:
    st.warning("No declared fictional sources are available for inspection.")
    st.stop()

st.subheader("Inspect a source")
selected_system = st.selectbox(
    "Source to inspect",
    options=list(systems),
    format_func=lambda key: f'{systems[key]["display_name"]} ({key})',
    key="sources_selected_system",
)
source = systems[selected_system]
st.subheader(source["display_name"])
st.caption(
    f'Source: {source["source_file"]} · {source["rows_read"]} records · '
    f'{len(source["columns"])} declared columns'
)
st.caption(
    "Source namespaces remain independent. Original identifiers, "
    "including any leading zeros, are displayed as text."
)

st.subheader("Selected source field inspector")
chosen_field = st.selectbox(
    "Field to inspect",
    options=source["columns"],
    # Per-source key avoids carrying a field choice into another source.
    key="sources_field_" + selected_system,
)
definition = source["column_metadata"][chosen_field]
profile = source["profiles"][chosen_field]

with st.container(border=True):
    st.markdown("**Source field: " + chosen_field + "**")
    st.caption("Declared meaning, not an inferred canonical mapping")
    st.write(definition.get("description") or "No declared description")
    st.write(
        "Declared type: " + str(definition.get("declared_type") or "Not declared")
        + " · Entity: " + str(definition.get("entity_kind") or "Not declared")
    )
    st.write(
        "Unit: " + str(definition.get("unit") or "Not applicable")
        + " · Nullable: "
        + (
            "Yes" if definition.get("nullable") is True
            else "No" if definition.get("nullable") is False
            else "Not declared"
        )
    )
    if "timezone" in definition:
        st.caption("Declared time zone: " + str(definition["timezone"]))
    if "declared_values" in definition:
        st.caption(
            "Declared categories: "
            + ", ".join(str(v) for v in definition["declared_values"])
        )
    st.markdown("**Full-source quality for this field**")
    st.caption(
        f'Calculated from all {profile["rows"]} source records, '
        "not only the five previewed below."
    )
    st.write(
        f'Present: {profile["nonempty"]} · Blank: {profile["empty"]} · '
        f'Distinct present values: {profile["distinct_nonempty"]}'
    )
    st.write(
        "Invalid numeric: " + quality_text(profile["nonfinite_or_non_numeric"])
        + " · Negative numeric: " + quality_text(profile["negative_numeric"])
    )
    st.write(
        "Unapproved categories: " + quality_text(profile["outside_declared_categories"])
        + " · Leading-zero incident IDs: "
        + quality_text(profile["leading_zero_identifiers"])
    )

st.subheader("Original data preview")
st.caption(
    f'Displaying {source["preview_count"]} of {source["rows_read"]} source records. '
    "The original five-row preview is not transformed, cleaned or inferred."
)
preview_frame = pd.DataFrame(
    source["preview_rows"],
    columns=["source_record_id", *source["columns"]],
)
# The approved reader exposes source cells as literal strings, including
# numeric-looking identifiers. Never coerce these cells to numbers.
st.dataframe(preview_frame, hide_index=True, width="stretch")

st.subheader("Source field quality profile")
st.caption(
    f'Quality counts below cover all {source["rows_read"]} records in this '
    "source, not only the preview."
)
profile_rows = []
for name in source["columns"]:
    values = source["profiles"][name]
    profile_rows.append({
        "Source column": name,
        "Declared type": values.get("declared_type", "Unknown"),
        "Present": values["nonempty"],
        "Blank": values["empty"],
        "Distinct": values["distinct_nonempty"],
        "Invalid numeric": values.get("nonfinite_or_non_numeric"),
        "Negative numeric": values.get("negative_numeric"),
        "Unapproved categories": values.get("outside_declared_categories"),
        "Leading-zero IDs": values.get("leading_zero_identifiers"),
    })
st.dataframe(pd.DataFrame(profile_rows), hide_index=True, width="stretch")
st.caption(
    "An empty quality-table cell means a check does not apply to that field; "
    "a numeric 0 means the check was applicable and found zero issues."
)

with st.expander("Declared source field definitions", expanded=False):
    definitions = [
        {
            "Source column": name,
            "Business meaning": source["column_metadata"][name].get("description", ""),
            "Declared type": source["column_metadata"][name].get("declared_type", ""),
            "Entity kind": source["column_metadata"][name].get("entity_kind", ""),
            "Unit": source["column_metadata"][name].get("unit") or "Not applicable",
            "Nullable": (
                "Yes" if source["column_metadata"][name].get("nullable") is True
                else "No" if source["column_metadata"][name].get("nullable") is False
                else "Not declared"
            ),
        }
        for name in source["columns"]
    ]
    st.dataframe(pd.DataFrame(definitions), hide_index=True, width="stretch")

st.caption(
    "This page only inspects approved fictional sources. "
    "Matching proposals, independent validation decisions, "
    "read-only reconciliation and guarded four-file export "
    "require their own explicit actions on later pages."
)
