"""Fail-closed, provenance-preserving synthetic reconciliation.

Only independently validated ACCEPT field mappings are ever transformed.
Unmapped required fields or unresolved duration meaning hold rows for review.
Unknown values, missing IDs, malformed durations and duplicates are audited.
No cross-system incident/entity joining occurs.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from decimal import Decimal, DecimalException
from pathlib import Path
from typing import Any, Mapping

from .ingest import duplicate_identifiers, read_source
from .matchers import read_demo_contract
from .profile import profile_source
from .validators import validate_suggestions


OUTPUT_FIELDS = (
    "incident_id", "region", "incident_status", "resolution_minutes",
    "source_system", "source_record_id",
)
ISSUE_FIELDS = ("source_system", "source_record_id", "disposition", "reason_code",
                "source_column", "original_value")
COUNT_TYPES = ("ACCEPTED", "ACCEPTED_OPTIONAL_NULL", "HELD_FOR_REVIEW", "QUARANTINED")


def _decimal_minutes(raw: str, factor: Any) -> str:
    try:
        number = Decimal(raw.strip())
        multiplier = Decimal(str(factor))
        if not number.is_finite() or number < 0 or not multiplier.is_finite() or multiplier <= 0:
            raise ValueError("Duration must be finite and nonnegative with a safe factor")
        converted = number * multiplier
        if not converted.is_finite():
            raise ValueError("Converted duration is non-finite")
        return format(converted.normalize(), "f")
    except (DecimalException, ValueError, OverflowError) as exc:
        raise ValueError("Invalid incident duration") from exc


def _resolve_mapping(decisions: list[dict], system: str, fields: Mapping) -> tuple[dict, list[str]]:
    accepted: dict[str, dict] = {}
    for decision in decisions:
        if decision["source_system"] != system or decision["decision"] != "ACCEPT":
            continue
        target = decision["proposed_target"]
        if target not in fields or target in accepted:
            raise ValueError(f"Unsafe duplicate or unknown accepted mapping: {system}/{target}")
        accepted[target] = decision

    # Required target cannot be populated by invented, inferred or rejected fields.
    unresolved = [f"REQUIRED_MAPPING_UNRESOLVED:{field}"
                  for field, cfg in fields.items() if cfg.get("required") and field not in accepted]
    return accepted, unresolved


def _process_system(
    system: str, rows: list[dict], decisions: list[dict],
    source_config: Mapping, schema: Mapping, rules: Mapping,
) -> tuple[list[dict], list[dict], dict]:
    fields = schema["business_fields"]
    mapping, unresolved = _resolve_mapping(decisions, system, fields)
    source_columns = source_config["columns"]
    # A real duration column exists but cannot safely map: do not silently
    # erase it by making all resolution times NULL.
    duration_exists = any(c.get("entity_kind") == "incident_duration"
                          for c in source_columns.values())
    if duration_exists and "resolution_minutes" not in mapping:
        unresolved.append("DURATION_MAPPING_UNRESOLVED")

    id_map = mapping.get("incident_id")
    duplicated = (duplicate_identifiers(rows, id_map["source_column"]) if id_map else set())
    standardised: list[dict] = []
    issues: list[dict] = []
    counts = Counter({key: 0 for key in COUNT_TYPES})
    reasons = Counter()

    for row in rows:
        raw = row["raw"]
        converted: dict[str, str] = {}
        problems: list[tuple[str, str, str, str]] = []

        def problem(severity: str, code: str, column: str = "", value: str = "") -> None:
            problems.append((severity, code, column, value))

        for message in unresolved:
            problem("REVIEW", message)

        for field, decision in mapping.items():
            source_column = decision["source_column"]
            value = raw[source_column]
            cleaned = value.strip()
            if field == "incident_id":
                if not cleaned:
                    problem("QUARANTINE", "REQUIRED_ID_MISSING", source_column, value)
                else:
                    # Maintain text representation and leading zeros.
                    converted[field] = cleaned
                    if cleaned in duplicated:
                        problem("REVIEW", "DUPLICATE_SOURCE_IDENTIFIER", source_column, value)
            elif field in {"region", "incident_status"}:
                if not cleaned:
                    problem("QUARANTINE", "REQUIRED_CATEGORY_MISSING", source_column, value)
                else:
                    vocabulary = "regions" if field == "region" else "statuses"
                    mapped = rules.get(vocabulary, {}).get(system, {}).get(cleaned)
                    if mapped is None or mapped not in fields[field]["allowed_values"]:
                        problem("REVIEW", "CATEGORY_VALUE_UNAPPROVED", source_column, value)
                    else:
                        converted[field] = mapped
            elif field == "resolution_minutes":
                if not cleaned:
                    converted[field] = ""
                else:
                    conversion = decision.get("conversion")
                    if not isinstance(conversion, dict) or conversion.get("to_unit") != "minute":
                        problem("REVIEW", "DURATION_CONVERSION_NOT_APPROVED", source_column, value)
                    else:
                        source_unit = source_columns[source_column].get("unit")
                        approved_factor = rules.get("duration_units", {}).get(source_unit, {}).get("multiplier_to_minutes")
                        if (source_unit != conversion.get("from_unit") or
                                approved_factor is None or approved_factor != conversion.get("factor")):
                            problem("REVIEW", "DURATION_CONVERSION_UNTRUSTED", source_column, value)
                        else:
                            try:
                                converted[field] = _decimal_minutes(cleaned, approved_factor)
                            except ValueError:
                                problem("QUARANTINE", "DURATION_INVALID", source_column, value)

        # Optional blank resolution is acceptable for unresolved incidents.
        # Missing duration on a CLOSED incident is reviewed, not silently
        # counted as a legitimate measured resolution time.
        if ("resolution_minutes" in mapping and converted.get("resolution_minutes") == ""
                and converted.get("incident_status") == "Closed"):
            decision = mapping["resolution_minutes"]
            problem("REVIEW", "CLOSED_DURATION_MISSING", decision["source_column"], "")

        if any(s == "QUARANTINE" for s, _, _, _ in problems):
            disposition = "QUARANTINED"
        elif problems:
            disposition = "HELD_FOR_REVIEW"
        else:
            disposition = ("ACCEPTED_OPTIONAL_NULL" if converted.get("resolution_minutes", "") == ""
                           else "ACCEPTED")
        counts[disposition] += 1
        if disposition in {"ACCEPTED", "ACCEPTED_OPTIONAL_NULL"}:
            standardised.append({
                "incident_id": converted["incident_id"],
                "region": converted["region"],
                "incident_status": converted["incident_status"],
                "resolution_minutes": converted.get("resolution_minutes", ""),
                "source_system": system,
                "source_record_id": row["source_record_id"],
            })
        else:
            for _, code, column, value in problems:
                issues.append({
                    "source_system": system, "source_record_id": row["source_record_id"],
                    "disposition": disposition, "reason_code": code,
                    "source_column": column, "original_value": value,
                })
                reasons[code] += 1

    assert sum(counts.values()) == len(rows)
    return standardised, issues, {
        "rows_read": len(rows),
        "counts": dict(counts),
        "reason_counts": dict(sorted(reasons.items())),
        "accepted_mappings": {field: item["source_column"] for field, item in sorted(mapping.items())},
        "unresolved_fields": sorted(unresolved),
        "duplicates_flagged": len(duplicated),
    }


def reconcile_demo(root: Path, candidates: Mapping[str, Any]) -> dict[str, Any]:
    """Pure computation before any output writing, with all inputs synthetic."""
    root = root.resolve()
    config, schema, metadata = read_demo_contract(root / "config" / "demo.json")
    # Runtime reconciliation must never open holdout labels or leave the
    # project root, even when invoked directly without the GUI adapter.
    rules_relative = config.get("value_rules")
    if not isinstance(rules_relative, str):
        raise ValueError("Invalid runtime value-rules path")
    rules_path = (root / rules_relative).resolve()
    if (not rules_path.is_relative_to(root)
            or "evaluation" in rules_path.relative_to(root).parts):
        raise ValueError("Unsafe runtime value-rules path")
    with rules_path.open(encoding="utf-8") as f:
        rules = json.load(f)
    if not config.get("synthetic_only") or not schema.get("synthetic_only"):
        raise ValueError("Refusing non-synthetic processing")
    field_gate = validate_suggestions(candidates, schema, metadata, rules)
    source_paths = set(config["sources"])
    accepted: list[dict] = []
    rejected: list[dict] = []
    systems = {}
    for system, source_config in sorted(metadata["systems"].items()):
        rows, info = read_source(root, system, source_config, source_paths)
        clean, issues, outcome = _process_system(
            system, rows, field_gate["decisions"], source_config, schema, rules
        )
        accepted.extend(clean)
        rejected.extend(issues)
        systems[system] = {**info, **outcome,
                           "column_profiles": profile_source(rows, source_config["columns"])}

    totals = {k: sum(v["counts"][k] for v in systems.values()) for k in COUNT_TYPES}
    rows_read = sum(v["rows_read"] for v in systems.values())
    if sum(totals.values()) != rows_read:
        raise ValueError("Unaccounted source rows")
    if totals["ACCEPTED"] + totals["ACCEPTED_OPTIONAL_NULL"] != len(accepted):
        raise ValueError("Accepted row count and output do not match")
    review_keys = {(r["source_system"], r["source_record_id"]) for r in rejected}
    if len(review_keys) != totals["HELD_FOR_REVIEW"] + totals["QUARANTINED"]:
        raise ValueError("Unaccounted held/quarantined rows")
    if len({(r["source_system"], r["source_record_id"]) for r in accepted}) != len(accepted):
        raise ValueError("Duplicate row provenance in output")
    if len({(r["source_system"], r["source_record_id"]) for r in accepted} & review_keys):
        raise ValueError("Row appears in both approved and exception outputs")

    return {
        "rows": accepted, "issues": rejected,
        "mapping_report": {
            "stage": "P3_SYNTHETIC_RECONCILIATION",
            "row_count": rows_read, "counts": totals, "systems": systems,
            "field_validation": field_gate,
            "cross_system_joins": 0,
            "gold_labels_accessed": False,
            "field_acceptance_does_not_prove_correctness": True,
            "limitations": ("Synthetic demo only; no independent accuracy evaluation. "
                            "Data not mapped safely is held or quarantined, not guessed."),
        },
        "evaluation": {
            "stage": "P5_NOT_YET_EVALUATED", "metrics_computed": False,
            "precision": None, "recall": None, "holdout_metrics": None,
            "note": "Placeholder only; run independent evaluation in Phase 5.",
        },
    }


def _write_csv(path: Path, header: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="raise",
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_demo_output(root: Path, output_dir: Path, result: Mapping[str, Any]) -> None:
    """Refuse to overwrite source or evaluation files with demonstration outputs."""
    project_root = root.resolve()
    parent = (project_root / "output").resolve()
    if not parent.is_relative_to(project_root) or parent == project_root:
        raise ValueError("Unsafe project output root")
    output_dir = output_dir.resolve()
    if output_dir == parent or not output_dir.is_relative_to(parent):
        raise ValueError("Output must be a named folder under project output/")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"Output directory not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "standardised.csv", OUTPUT_FIELDS, result["rows"])
    _write_csv(output_dir / "rejected_rows.csv", ISSUE_FIELDS, result["issues"])
    for filename, value in (("mapping_report.json", result["mapping_report"]),
                            ("evaluation.json", result["evaluation"])):
        with (output_dir / filename).open("w", encoding="utf-8") as fh:
            json.dump(value, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
