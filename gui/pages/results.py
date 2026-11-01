"""P-030 read-only reconciliation and P-031 guarded audited export.

The original engine decides mapping and disposition; only a separate explicit
button writes reports. Filters and navigation never infer or write files.
"""

import logging
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gui import adapter, export_view, matching_state, reconciliation_view

LOG = logging.getLogger(__name__)
VALIDATION_KEY = "sb_validation_report"
VALIDATION_FINGERPRINT_KEY = "sb_validation_fingerprint"
RESULT_KEY = "sb_reconciliation_report"
RESULT_FINGERPRINT_KEY = "sb_reconciliation_fingerprint"

st.title("04 · Results & Export")
st.write("Inspect standardised records, exceptions and guarded audited reports from the fictional incident demo.")
st.info(
    "SYNTHETIC DEMO · Reconciliation is read-only. Creating the four audited "
    "files requires a separate explicit Prepare action and a unique output folder."
)
st.caption(
    "This is deterministic row processing under the original Python engine. "
    "It does not merge incidents across source systems or silently approve REVIEW fields."
)

try:
    fingerprint = matching_state.input_fingerprint()
except (OSError, TypeError, KeyError, ValueError):
    matching_state.revoke_session_artifacts(st.session_state)
    st.error("Unable to verify the approved synthetic sources; reconciliation is blocked.")
    st.stop()

if matching_state.discard_stale(st.session_state, fingerprint):
    st.warning("Demo inputs changed, so stale matching and downstream results were discarded.")

candidates = st.session_state.get(matching_state.REPORT_KEY)
validation = st.session_state.get(VALIDATION_KEY)
if (
    candidates is None
    or st.session_state.get(matching_state.FINGERPRINT_KEY) != fingerprint
    or validation is None
    or st.session_state.get(VALIDATION_FINGERPRINT_KEY) != fingerprint
):
    st.session_state.pop(RESULT_KEY, None)
    st.session_state.pop(RESULT_FINGERPRINT_KEY, None)
    st.session_state.pop(export_view.MANIFEST_KEY, None)
    st.info(
        "No current independently validated candidate report. In this browser session, "
        "run **02 AI Mapping**, then click **Explain validation decisions** on **03 Validation**."
    )
    st.stop()

try:
    matching_state.display_rows(candidates)
    if (
        validation["stage"] != "P3_FIELD_VALIDATION_PREVIEW_ONLY"
        or validation["gold_labels_accessed"] is not False
        or validation["rows_processed"] != 0
        or validation["rows_transformed"] != 0
        or len(validation["decisions"]) != len(candidates["results"])
        or sum(validation["counts"].values()) != len(candidates["results"])
    ):
        raise ValueError("Invalid independent validation")
except (KeyError, TypeError, ValueError, IndexError):
    st.session_state.pop(RESULT_KEY, None)
    st.session_state.pop(RESULT_FINGERPRINT_KEY, None)
    st.session_state.pop(export_view.MANIFEST_KEY, None)
    st.error("Previous matching or validation is not trustworthy; rerun these stages before reconciling.")
    st.stop()

if st.session_state.get(RESULT_FINGERPRINT_KEY) != fingerprint:
    st.session_state.pop(RESULT_KEY, None)
    st.session_state.pop(RESULT_FINGERPRINT_KEY, None)
    st.session_state.pop(export_view.MANIFEST_KEY, None)

if st.button("Run reconciliation (read-only)", type="primary", key="sb_run_reconciliation"):
    st.session_state.pop(RESULT_KEY, None)
    st.session_state.pop(RESULT_FINGERPRINT_KEY, None)
    st.session_state.pop(export_view.MANIFEST_KEY, None)
    try:
        with st.spinner("Applying the existing synthetic-only reconciliation and row audit rules..."):
            result = adapter.reconcile_candidates(candidates)
            summary = reconciliation_view.summarize(result, validation)
            if matching_state.input_fingerprint() != fingerprint:
                raise ValueError("Approved synthetic inputs changed during reconciliation")
            if st.session_state.get(VALIDATION_FINGERPRINT_KEY) != fingerprint:
                raise ValueError("Independent validation changed during reconciliation")
    except Exception:
        LOG.exception("Local read-only SchemaBridge reconciliation failed")
        st.error(
            "Reconciliation was blocked or failed. Check local server logs; "
            "no reports were written and no previous output was reused."
        )
    else:
        st.session_state[RESULT_KEY] = result
        st.session_state[RESULT_FINGERPRINT_KEY] = fingerprint
        # Refresh the shell after storing the read-only result. This rerender
        # does not invoke reconciliation or audited preparation again.
        st.rerun()

result = st.session_state.get(RESULT_KEY)
if result is None:
    st.info(
        "No reconciled records in this browser session. Click **Run reconciliation (read-only)** "
        "to process the approved fictional datasets without writing any files."
    )
    st.stop()

try:
    summary = reconciliation_view.summarize(result, validation)
    _config, _schema, metadata = adapter._contract()
except (KeyError, TypeError, ValueError, IndexError):
    st.session_state.pop(RESULT_KEY, None)
    st.session_state.pop(RESULT_FINGERPRINT_KEY, None)
    st.session_state.pop(export_view.MANIFEST_KEY, None)
    st.error("The stored reconciliation report failed accounting checks; run reconciliation again.")
    st.stop()

st.success(f"Accounted for all {summary['input_records']} source records using the existing Python engine.")
st.caption(
    "A source record has exactly one final disposition. "
    "One held or quarantined record may generate several distinct audit reason events."
)
input_metric, standard_metric, held_metric, quarantined_metric = st.columns(4)
input_metric.metric("Input records", summary["input_records"])
standard_metric.metric("Standardisable records", summary["standardisable_records"])
held_metric.metric("Held for review", summary["counts"]["HELD_FOR_REVIEW"])
quarantined_metric.metric("Quarantined", summary["counts"]["QUARANTINED"])
st.caption(
    f"Standardisable = {summary['counts']['ACCEPTED']} ACCEPTED with duration + "
    f"{summary['counts']['ACCEPTED_OPTIONAL_NULL']} ACCEPTED_OPTIONAL_NULL. "
    f"Distinct exception records: {summary['exception_records']}. "
    f"Reason-event entries: {summary['reason_events']} (not distinct records)."
)

st.subheader("Four-way record disposition")
st.caption(
    "Each source record appears in exactly one of these four categories. "
    "These are original backend record outcomes, NOT field-validation counts "
    "or estimates from a chart."
)
for disposition, meaning in (
    ("ACCEPTED", "Accepted with a declared duration"),
    ("ACCEPTED_OPTIONAL_NULL", "Accepted with an allowed missing duration"),
    ("HELD_FOR_REVIEW", "Held because one or more decisions remain unresolved"),
    ("QUARANTINED", "Quarantined under the original safety rules"),
):
    count = summary["counts"][disposition]
    st.write(
        f"**{disposition}**: {count} source records "
        f"({count / summary['input_records']:.1%}) · {meaning}"
    )
    st.progress(count / summary["input_records"])
st.caption(
    f"ACCOUNTING CHECK · {summary['input_records']} input records = "
    + " + ".join(
        str(summary["counts"][key])
        for key in reconciliation_view.COUNTS
    )
    + ". All counts were checked against the original engine."
)

st.subheader("Per-source disposition accounting")
st.dataframe(
    pd.DataFrame([
        {
            "Source": metadata["systems"][s["source_system"]].get("display_name", s["source_system"]),
            "Input": s["input_records"],
            "Standardisable": s["standardisable_records"],
            "Held": s["HELD_FOR_REVIEW"],
            "Quarantined": s["QUARANTINED"],
            "Reason events": s["reason_events"],
            "Unresolved mappings": ", ".join(s["unresolved_fields"]),
        }
        for s in summary["per_source"]
    ]),
    hide_index=True,
    width="stretch",
)

st.subheader("Source outcome cards")
st.caption(
    "Each card is derived from one source's verified original reconciliation "
    "report. Beta may have zero standardisable records when required mappings "
    "remain unresolved; zero is not a missing value."
)
cards = st.columns(len(summary["per_source"]))
for card, item in zip(cards, summary["per_source"]):
    with card:
        with st.container(border=True):
            st.markdown(
                "**" + metadata["systems"][item["source_system"]].get(
                    "display_name", item["source_system"]
                ) + "**"
            )
            st.caption("Source key: " + item["source_system"])
            st.write(
                f'{item["input_records"]} input records · '
                f'{item["standardisable_records"]} standardisable'
            )
            st.write(
                f'{item["HELD_FOR_REVIEW"]} held · '
                f'{item["QUARANTINED"]} quarantined'
            )
            st.caption(
                f'{item["reason_events"]} reason events; '
                "not a count of distinct source records"
            )
            if item["unresolved_fields"]:
                st.caption(
                    "Unresolved mappings: "
                    + ", ".join(item["unresolved_fields"])
                )
            if item["standardisable_records"] == 0:
                st.warning(
                    "No source records standardisable under the original "
                    "required-field safety rules. No identifiers are guessed."
                )

systems = ["All sources", *sorted(metadata["systems"])]
chosen_system = st.selectbox(
    "Filter source system",
    systems,
    format_func=lambda system: system if system == "All sources" else metadata["systems"][system].get("display_name", system),
    key="sb_results_source",
)
matching_system = lambda item: chosen_system == "All sources" or item["source_system"] == chosen_system

st.subheader("Standardised records (read-only)")
approved_rows = [dict(row) for row in result["rows"] if matching_system(row)]
limit = 25
st.caption(
    f"Showing {min(limit, len(approved_rows))} of {len(approved_rows)} standardisable records "
    "for this filter. Source-system and source-record provenance remain explicit; "
    "identifiers are kept as text."
)
if approved_rows:
    st.dataframe(pd.DataFrame(approved_rows[:limit]), hide_index=True, width="stretch")
else:
    st.info("No standardisable records for this source. Unsafe or unresolved required mappings are not guessed.")

st.subheader("Held and quarantined records (distinct source rows)")
disposition_filter = st.selectbox(
    "Exception disposition",
    ["All exceptions", "HELD_FOR_REVIEW", "QUARANTINED"],
    key="sb_results_disposition",
)
exception_records = [
    row for row in summary["exception_records_view"]
    if matching_system(row)
    and (disposition_filter == "All exceptions" or row["disposition"] == disposition_filter)
]
st.caption(
    f"Showing {min(limit, len(exception_records))} of {len(exception_records)} distinct exception records "
    "for this filter. Multiple reason codes on one row do NOT mean multiple rejected records."
)
if exception_records:
    st.dataframe(pd.DataFrame(exception_records[:limit]), hide_index=True, width="stretch")
else:
    st.info("No exception records match these filters.")

with st.expander("Individual audit reason events (not unique records)", expanded=False):
    events = [
        dict(event) for event in result["issues"]
        if matching_system(event)
        and (disposition_filter == "All exceptions" or event["disposition"] == disposition_filter)
    ]
    st.caption(
        f"Showing {min(limit, len(events))} of {len(events)} audit reason events. "
        "The same source record may appear more than once with different reason codes."
    )
    if events:
        st.dataframe(pd.DataFrame(events[:limit]), hide_index=True, width="stretch")

st.subheader("Audit reason-event frequency")
st.caption(
    "Counts below are individual reason-code occurrences from the original "
    "backend, not distinct rejected records. One held or quarantined record "
    "may contribute more than one event."
)
filtered_events = [
    event for event in result["issues"]
    if matching_system(event)
    and (
        disposition_filter == "All exceptions"
        or event["disposition"] == disposition_filter
    )
]
reason_event_counts = {}
for event in filtered_events:
    code = event["reason_code"]
    reason_event_counts[code] = reason_event_counts.get(code, 0) + 1
st.caption(
    f"{len(filtered_events)} reason events in this filter; "
    f"{len(exception_records)} distinct exceptional records."
)
if reason_event_counts:
    st.dataframe(
        pd.DataFrame([
            {"Reason code": code, "Event count": count}
            for code, count in sorted(reason_event_counts.items())
        ]),
        hide_index=True,
        width="stretch",
    )
else:
    st.info("No audit reason events match these read-only filters.")

with st.expander("What this result does not prove", expanded=False):
    st.write(
        "No independent accuracy score was calculated here. evaluation.json will be an "
        "unscored placeholder, not a fresh holdout evaluation. Previously observed one-shot "
        "synthetic challenge had only 3/9 auto-accepted true matches; this GUI does not "
        "re-score or optimise against that challenge."
    )

st.subheader("Prepare audited reports (four files)")
st.caption(
    "Only the original four-file writer may produce downloadable reports. "
    "A prepared package is never created by page visits, source filters, "
    "table previews or the read-only reconciliation action."
)
with st.expander("What the four audited files contain", expanded=False):
    descriptions = {
        "standardised.csv": "All backend-standardisable source records with text identifiers and provenance",
        "rejected_rows.csv": "Individual held and quarantined reason events, not unique exception rows",
        "mapping_report.json": "Original mapping, validation and reconciliation decisions",
        "evaluation.json": "UNSCORED PLACEHOLDER, never fresh accuracy, precision or recall",
    }
    for name in adapter.AUDIT_FILENAMES:
        st.markdown("**" + name + "**")
        st.caption(descriptions[name])
st.caption(
    "Exports use the FULL current reconciliation, not the 25-row table previews "
    "or source/disposition filters. Files include all standardisable rows, "
    "individual held/quarantined reason events, mapping decisions and an "
    "unscored evaluation placeholder. Existing folders are never overwritten."
)

# This local label is derived from this render's actual export outcome;
# no separate UI cache may independently authorise downloads.
export_ui_state = "NOT PREPARED"
if st.button(
    "Prepare audited reports (write once)",
    key="sb_prepare_audited",
    disabled=st.session_state.get(export_view.MANIFEST_KEY) is not None,
):
    # A click is accepted only while no export is already prepared.
    # The rendered disabled flag alone is insufficient: it was evaluated
    # before the successful Prepare run updated session state.
    if st.session_state.get(export_view.MANIFEST_KEY) is not None:
        st.rerun()
    try:
        with st.spinner("PREPARING · Writing and checking four backend-generated audit files..."):
            if matching_state.input_fingerprint() != fingerprint:
                raise ValueError("Demo inputs changed before export")
            prepared = export_view.prepare(result, validation, fingerprint)
            if (matching_state.input_fingerprint() != fingerprint
                    or st.session_state.get(RESULT_KEY) is not result
                    or st.session_state.get(RESULT_FINGERPRINT_KEY) != fingerprint
                    or st.session_state.get(VALIDATION_KEY) is not validation):
                raise ValueError("Session changed during export")
            export_view.download_payloads(prepared, result, fingerprint)
    except Exception:
        export_ui_state = "FAILED"
        LOG.exception("Guarded SchemaBridge audited export failed")
        st.error(
            "Audited export was blocked or incomplete. No download links were "
            "published. See the local server log; previous export folders "
            "were not overwritten."
        )
    else:
        st.session_state[export_view.MANIFEST_KEY] = prepared
        # Render a fresh, server-side disabled Prepare button immediately.
        # Without this rerun, the first successful response leaves the button
        # enabled even though download links and a manifest already exist.
        st.rerun()

manifest = st.session_state.get(export_view.MANIFEST_KEY)
if manifest is None:
    if export_ui_state == "FAILED":
        st.error(
            "FAILED · The explicit write-once preparation was not verified. "
            "No audited download links are available. An incomplete reserved "
            "folder, if any, is never reused."
        )
    else:
        st.warning(
            "NOT PREPARED · No audited export has been requested or verified "
            "for the current reconciliation."
        )
    st.warning(
        "No CSV/JSON file has been generated on this page unless you explicitly "
        "prepare audited reports. The table previews are read-only and "
        "their native CSV toolbar download remains disabled."
    )
else:
    try:
        downloads = export_view.download_payloads(manifest, result, fingerprint)
        if matching_state.input_fingerprint() != fingerprint:
            raise ValueError("Synthetic inputs changed while preparing downloads")
    except Exception:
        export_ui_state = "STALE"
        LOG.exception("Audited export is stale or its files changed")
        st.session_state.pop(export_view.MANIFEST_KEY, None)
        st.error(
            "STALE · The previous manifest or one of its four files no "
            "longer passes the original session and SHA-256 integrity checks. "
            "No downloads are available."
        )
        st.error(
            "Prepared export is stale or failed integrity checks. Download links "
            "were withdrawn. Reconcile and prepare a new audited run if needed."
        )
    else:
        export_ui_state = "VERIFIED"
        st.success(
            "VERIFIED · All four original audited files have just passed "
            "session, result and SHA-256 integrity checks. This does not "
            "certify model accuracy or approve held records."
        )
        st.success(
            f"Audited run {manifest['run_id']} prepared: "
            f"{manifest['data_rows']} standardised records and "
            f"{manifest['reason_events']} reason events."
        )
        st.caption(
            "Files are in a new ignored output/gui_runs folder. Downloads contain "
            "complete reports, not table previews. Files are hash-checked when "
            "links are displayed; clicking Download never reruns the engine."
        )
        st.subheader("Audited artifact integrity")
        st.caption(
            "Byte lengths and SHA-256 digests below belong to the exact "
            "current-session payloads that passed the original export "
            "verification immediately before the download controls."
        )
        for name in adapter.AUDIT_FILENAMES:
            st.markdown("**" + name + "**")
            st.caption(str(len(downloads[name])) + " verified bytes")
            sha = manifest.get("sha256", {}).get(name)
            if isinstance(sha, str):
                st.code(sha, language=None)
            else:
                # Legacy test mocks do not contain a real SHA manifest.
                # The original export backend always provides all four.
                st.caption("Digest not included in this manifest.")
            mimetype = "text/csv" if name.endswith(".csv") else "application/json"
            st.download_button(
                f"Download {name}",
                data=downloads[name],
                file_name=name,
                mime=mimetype,
                key="sb_download_" + name.replace(".", "_"),
                on_click="ignore",
            )
        st.caption(
            "evaluation.json is an unscored runtime placeholder, not precision, "
            "recall or a new holdout evaluation. When opening CSV in a spreadsheet, "
            "import incident identifiers as Text to preserve leading zeros."
        )
