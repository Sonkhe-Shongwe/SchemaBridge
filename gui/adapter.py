"""Backend adapter for the SchemaBridge Streamlit workbench.

Provides source inspection, candidate ranking, independent validation,
reconciliation and guarded audited export without duplicating core rules.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CORE = PROJECT_ROOT / "src"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from schemabridge.ingest import read_source
from schemabridge.matchers import preview_candidates, read_demo_contract
from schemabridge.profile import profile_source
from schemabridge.reconcile import COUNT_TYPES, reconcile_demo, write_demo_output
from schemabridge.validators import validate_suggestions


AUDIT_FILENAMES = (
    "standardised.csv",
    "rejected_rows.csv",
    "mapping_report.json",
    "evaluation.json",
)
_RUN_ID = re.compile(r"[a-zA-Z0-9_-]{8,80}\Z")


def _contract() -> tuple[dict, dict, dict]:
    """Only the project's approved synthetic runtime configuration is read."""
    return read_demo_contract(PROJECT_ROOT / "config" / "demo.json")


def load_sources(*, preview_limit: int = 5) -> dict[str, Any]:
    """Read actual declared synthetic sources without altering cells or files."""
    if type(preview_limit) is not int or not 0 <= preview_limit <= 25:
        raise ValueError("preview_limit must be an integer from 0 to 25")
    config, schema, metadata = _contract()
    if config.get("synthetic_only") is not True or schema.get("synthetic_only") is not True:
        raise ValueError("GUI supports only the approved synthetic demo")
    allowed = set(config["sources"])
    systems = []
    for system, spec in sorted(metadata["systems"].items()):
        rows, info = read_source(PROJECT_ROOT, system, spec, allowed)
        systems.append({
            "source_system": system,
            "display_name": spec.get("display_name", system),
            "rows_read": info["rows_read"],
            "source_file": info["file"],
            "columns": list(info["fields"]),
            "column_metadata": spec["columns"],
            "profiles": profile_source(rows, spec["columns"]),
            "preview_rows": [
                {"source_record_id": row["source_record_id"], **dict(row["raw"])}
                for row in rows[:preview_limit]
            ],
            "preview_count": min(preview_limit, len(rows)),
        })
    return {
        "synthetic_only": True,
        "source_count": len(systems),
        "source_column_count": sum(len(system["columns"]) for system in systems),
        "row_count": sum(system["rows_read"] for system in systems),
        "systems": systems,
    }


def rank_candidates(*, model: Any = None) -> dict[str, Any]:
    """Real dual-method ranking, always offline; never an ACCEPT decision.

    Tests inject a fake encoder. A real Run matching click can pass no model,
    which delegates lazy cached MiniLM loading to the existing core matcher.
    """
    report = preview_candidates(
        PROJECT_ROOT / "config" / "demo.json",
        method="both",
        offline=True,
        model=model,
    )
    if (
        report.get("stage") != "P2_CANDIDATE_RANKING_ONLY"
        or report.get("methods") != "both"
        or report.get("mapping_validation_performed") is not False
        or report.get("data_transformed") is not False
        or report.get("gold_labels_accessed") is not False
    ):
        raise ValueError("Unsafe or unexpected candidate-ranking response")
    return report


def _require_ranked(report: Mapping[str, Any]) -> None:
    if not isinstance(report, Mapping):
        raise TypeError("Candidate report must be a mapping")
    if (
        report.get("stage") != "P2_CANDIDATE_RANKING_ONLY"
        or report.get("methods") != "both"
        or report.get("gold_labels_accessed") is not False
        or report.get("data_transformed") is not False
        or report.get("mapping_validation_performed") is not False
    ):
        raise ValueError("Only an untouched both-method synthetic preview is permitted")


def inspect_decisions(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return actual independent backend gate decisions; never modify them."""
    _require_ranked(report)
    config, schema, metadata = _contract()
    root = PROJECT_ROOT.resolve()
    rules_path = (root / config["value_rules"]).resolve()
    if (not rules_path.is_relative_to(root)
            or "evaluation" in rules_path.relative_to(root).parts):
        raise ValueError("Unsafe rules path in demo configuration")
    with rules_path.open("r", encoding="utf-8") as fh:
        rules = json.load(fh)
    return validate_suggestions(report, schema, metadata, rules)


def reconcile_candidates(report: Mapping[str, Any]) -> dict[str, Any]:
    """Run the existing fail-closed engine, without writing any files."""
    _require_ranked(report)
    return reconcile_demo(PROJECT_ROOT, report)


def _checked_counts(result: Mapping[str, Any]) -> int:
    if not isinstance(result, Mapping):
        raise TypeError("Reconciliation result must be a mapping")
    report = result.get("mapping_report")
    if not isinstance(report, Mapping) or report.get("gold_labels_accessed") is not False:
        raise ValueError("Refusing non-synthetic or tainted reconciliation report")
    counts = report.get("counts")
    if not isinstance(counts, Mapping) or set(counts) != set(COUNT_TYPES):
        raise ValueError("Incomplete disposition counts")
    if any(type(counts[k]) is not int or counts[k] < 0 for k in COUNT_TYPES):
        raise ValueError("Invalid disposition count")
    total = sum(counts.values())
    if total != report.get("row_count"):
        raise ValueError("Unaccounted input rows")
    accepted = counts["ACCEPTED"] + counts["ACCEPTED_OPTIONAL_NULL"]
    rows, issues = result.get("rows"), result.get("issues")
    if not isinstance(rows, list) or not isinstance(issues, list) or len(rows) != accepted:
        raise ValueError("Approved rows disagree with safe engine report")
    rejected_keys = {(x["source_system"], x["source_record_id"]) for x in issues}
    if len(rejected_keys) != counts["HELD_FOR_REVIEW"] + counts["QUARANTINED"]:
        raise ValueError("Held/quarantined distinct record count mismatch")
    if result.get("evaluation", {}).get("metrics_computed") is not False:
        raise ValueError("GUI demo cannot write a fabricated scored evaluation")
    if report.get("cross_system_joins") != 0:
        raise ValueError("Cross-system entity joins are forbidden")
    return accepted


def prepare_audit_export(result: Mapping[str, Any], *, run_id: str | None = None) -> dict[str, Any]:
    """Write-once audited export, under ignored output/gui_runs/<safe ID>.

    Caller must initiate this explicitly after a successful backend run. The
    original backend writer remains the sole authority for the four files.
    """
    expected_rows = _checked_counts(result)
    if run_id is None:
        run_id = "gui_" + uuid4().hex
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise ValueError("Invalid GUI run identifier")
    root = PROJECT_ROOT.resolve()
    output_root = (root / "output").resolve()
    if not output_root.is_relative_to(root) or output_root == root:
        raise ValueError("Unsafe project output root")
    parent = (root / "output" / "gui_runs").resolve()
    destination = (parent / run_id).resolve()
    if (not parent.is_relative_to(output_root)
            or not destination.is_relative_to(parent)
            or destination == parent):
        raise ValueError("Unsafe GUI export destination")
    # Reserve the new run directory exclusively before the original backend
    # writer starts. A pre-existing run is never reused or overwritten.
    parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(exist_ok=False)
    write_demo_output(root, destination, result)
    files = {}
    for name in AUDIT_FILENAMES:
        file = destination / name
        if not file.is_file() or file.stat().st_size == 0:
            raise ValueError("Missing or empty audited output: " + name)
        files[name] = file
    if {file.name for file in destination.iterdir()} != set(AUDIT_FILENAMES):
        raise ValueError("Unexpected files in audited output directory")
    with files["standardised.csv"].open("r", encoding="utf-8-sig", newline="") as fh:
        standard = csv.DictReader(fh)
        if tuple(standard.fieldnames or ()) != (
            "incident_id", "region", "incident_status", "resolution_minutes",
            "source_system", "source_record_id",
        ):
            raise ValueError("Incorrect standardised CSV columns")
        actual = sum(1 for _ in standard)
    with files["rejected_rows.csv"].open("r", encoding="utf-8-sig", newline="") as fh:
        rejected = csv.DictReader(fh)
        if tuple(rejected.fieldnames or ()) != (
            "source_system", "source_record_id", "disposition", "reason_code",
            "source_column", "original_value",
        ) or sum(1 for _ in rejected) != len(result["issues"]):
            raise ValueError("Audited reason-event CSV mismatch")
    for name, key in (("mapping_report.json", "mapping_report"),
                      ("evaluation.json", "evaluation")):
        with files[name].open("r", encoding="utf-8") as fh:
            if json.load(fh) != result[key]:
                raise ValueError("Audited report JSON does not match original backend")
    if actual != expected_rows:
        raise ValueError("Exported data-row count does not match reconciliation")
    return {
        "run_id": run_id,
        "directory": destination,
        "data_rows": actual,
        "files": files,
        "evaluation_is_placeholder": True,
    }
