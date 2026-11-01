"""P-028 GUI ranking session safety and display-only adapters.

No Streamlit imports, no model initialization, no output writes, no evaluation
labels, and no independent matching/validation business rules.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Mapping

from gui import adapter

REPORT_KEY = "sb_matching_report"
FINGERPRINT_KEY = "sb_matching_fingerprint"
DOWNSTREAM_KEYS = (
    "sb_validation_report",
    "sb_validation_fingerprint",
    "sb_reconciliation_report",
    "sb_reconciliation_fingerprint",
    "sb_export_manifest",
)


def input_fingerprint() -> str:
    """Fingerprint only the approved runtime inputs, never holdout/gold files.

    The model ID is present in demo.json; this is NOT a hash of model weights
    or a promise of a pinned Hugging Face snapshot.
    """
    root = Path(adapter.PROJECT_ROOT).resolve()
    demo, _schema, metadata = adapter.read_demo_contract(root / "config" / "demo.json")
    sources = demo.get("sources")
    if not isinstance(sources, list) or any(not isinstance(s, str) for s in sources):
        raise ValueError("Invalid synthetic source paths")
    declared_csvs = {v.get("csv") for v in metadata["systems"].values()}
    if set(sources) != declared_csvs:
        raise ValueError("Declared synthetic source list does not match source metadata")
    paths = ["config/demo.json", demo["canonical_schema"], demo["source_metadata"],
             demo["value_rules"], *sources]
    digest = hashlib.sha256()
    for relative in sorted(set(paths)):
        if not isinstance(relative, str):
            raise ValueError("Invalid runtime fingerprint input")
        location = (root / relative).resolve()
        if not location.is_relative_to(root) or "evaluation" in location.relative_to(root).parts:
            raise ValueError("Unsafe runtime fingerprint path")
        data = location.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def invalidate_downstream(state: Any) -> None:
    """Discard display-only artifacts after a new or invalid candidate run."""
    for key in DOWNSTREAM_KEYS:
        state.pop(key, None)


def revoke_session_artifacts(state: Any) -> None:
    """Fail closed when the approved demo inputs cannot be verified.

    Reset derived state, never unrelated Streamlit filter/view preferences.
    This does not remove any output files on disk.
    """
    state.pop(REPORT_KEY, None)
    state.pop(FINGERPRINT_KEY, None)
    invalidate_downstream(state)


def discard_stale(state: Any, current_fingerprint: str) -> bool:
    """Discard mismatched reports and orphaned downstream session artifacts.

    A validation, reconciliation or export manifest is never independent of
    its candidate report. Missing/None candidates must revoke those artifacts
    even if the leftover fingerprint happens to match current demo inputs.
    """
    report_valid = state.get(REPORT_KEY) is not None
    orphaned = not report_valid and (
        REPORT_KEY in state
        or FINGERPRINT_KEY in state
        or any(key in state for key in DOWNSTREAM_KEYS)
    )
    mismatched = report_valid and state.get(FINGERPRINT_KEY) != current_fingerprint
    if orphaned or mismatched:
        revoke_session_artifacts(state)
        return True
    return False


def display_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Flatten the existing ranking output for visual comparison only."""
    adapter._require_ranked(report)
    if report.get("similarity_is_probability") is not False:
        raise ValueError("Source scores must be labelled as similarity, not confidence")
    _config, schema, metadata = adapter._contract()
    systems = metadata["systems"]
    rows = []
    for result in report.get("results", []):
        system = result["source_system"]
        column = result["source_column"]
        definition = systems[system]["columns"][column]
        lexical = result["lexical_candidates"]
        semantic = result["semantic_candidates"]
        if not lexical or not semantic or result["mapping_decision"] != "NOT_EVALUATED":
            raise ValueError("Unsafe or incomplete source candidate ranking")
        for candidates in (lexical, semantic):
            if any(
                entry["canonical_field"] not in schema["business_fields"]
                or not math.isfinite(entry["similarity"])
                for entry in candidates
            ):
                raise ValueError("Invalid candidate field or similarity score")
        rows.append({
            "source_system": system,
            "source_name": systems[system].get("display_name", system),
            "source_column": column,
            "entity_kind": definition.get("entity_kind", ""),
            "declared_type": definition.get("declared_type", ""),
            "unit": definition.get("unit", ""),
            "description": definition.get("description", ""),
            "lexical_top": lexical[0]["canonical_field"],
            "lexical_similarity": lexical[0]["similarity"],
            "semantic_top": semantic[0]["canonical_field"],
            "semantic_similarity": semantic[0]["similarity"],
            "stage": "PROPOSED_ONLY",
        })
    if len(rows) != report.get("source_column_count") or len(set(
        (row["source_system"], row["source_column"]) for row in rows
    )) != len(rows):
        raise ValueError("Candidate fields are missing or duplicated")
    return rows
