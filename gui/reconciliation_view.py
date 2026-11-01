"""P-030 read-only reconciliation report checks and presentation.

The original Python engine makes every mapping and row-disposition decision.
This module checks accounting, provenance and the current independent
validation gate; it neither changes data nor writes reports.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from gui import adapter

COUNTS = ("ACCEPTED", "ACCEPTED_OPTIONAL_NULL", "HELD_FOR_REVIEW", "QUARANTINED")
REVIEW_TYPES = ("HELD_FOR_REVIEW", "QUARANTINED")


def summarize(result: Mapping[str, Any], validation: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed if any row, issue event or backend decision is unaccounted."""
    accepted = adapter._checked_counts(result)
    report = result["mapping_report"]
    if report.get("stage") != "P3_SYNTHETIC_RECONCILIATION":
        raise ValueError("Invalid reconciliation stage")
    if not isinstance(validation, Mapping) or validation.get("stage") != "P3_FIELD_VALIDATION_PREVIEW_ONLY":
        raise ValueError("Independent field validation required")
    if (
        validation.get("gold_labels_accessed") is not False
        or validation.get("rows_processed") != 0
        or validation.get("rows_transformed") != 0
        or report.get("field_validation") != validation
    ):
        raise ValueError("Reconciliation does not match current independent validation")
    systems = report.get("systems")
    if not isinstance(systems, Mapping) or set(systems) != {"system_a", "system_b", "system_c"}:
        raise ValueError("Unexpected synthetic source systems")

    counts = report["counts"]
    rows, issues = result["rows"], result["issues"]
    approved_keys = []
    for row in rows:
        if (
            row.get("source_system") not in systems
            or not isinstance(row.get("source_record_id"), str)
            or not isinstance(row.get("incident_id"), str)
            or not isinstance(row.get("region"), str)
            or not isinstance(row.get("incident_status"), str)
            or not isinstance(row.get("resolution_minutes"), str)
        ):
            raise ValueError("Invalid approved record or provenance")
        approved_keys.append((row["source_system"], row["source_record_id"]))
    if len(set(approved_keys)) != len(approved_keys):
        raise ValueError("Duplicate approved row provenance")

    issue_records = {}
    reason_counts = Counter()
    system_events = Counter()
    for event in issues:
        system = event.get("source_system")
        provenance = event.get("source_record_id")
        disposition = event.get("disposition")
        reason = event.get("reason_code")
        if (
            system not in systems
            or not isinstance(provenance, str)
            or disposition not in REVIEW_TYPES
            or not isinstance(reason, str)
            or not reason
        ):
            raise ValueError("Invalid exception reason event")
        key = (system, provenance)
        if key in approved_keys:
            raise ValueError("Record both approved and held")
        old = issue_records.setdefault(key, {
            "source_system": system,
            "source_record_id": provenance,
            "disposition": disposition,
            "reason_codes": set(),
            "reason_events": 0,
        })
        if old["disposition"] != disposition:
            raise ValueError("Mixed disposition for same record")
        old["reason_codes"].add(reason)
        old["reason_events"] += 1
        reason_counts[reason] += 1
        system_events[system] += 1

    if len(issue_records) != counts["HELD_FOR_REVIEW"] + counts["QUARANTINED"]:
        raise ValueError("Exception record count mismatch")
    event_dispositions = Counter(v["disposition"] for v in issue_records.values())
    if any(event_dispositions[d] != counts[d] for d in REVIEW_TYPES):
        raise ValueError("Exception disposition tally mismatch")
    if accepted + len(issue_records) != report["row_count"]:
        raise ValueError("Source records not fully accounted")

    per_source = []
    for system, info in sorted(systems.items()):
        local = info.get("counts", {})
        if set(local) != set(COUNTS):
            raise ValueError("Incomplete per-system status counts")
        if any(type(local[d]) is not int or local[d] < 0 for d in COUNTS):
            raise ValueError("Invalid per-system status counts")
        if sum(local.values()) != info.get("rows_read"):
            raise ValueError("Per-system row mismatch")
        approved_here = sum(row["source_system"] == system for row in rows)
        if approved_here != local["ACCEPTED"] + local["ACCEPTED_OPTIONAL_NULL"]:
            raise ValueError("Per-system approved record mismatch")
        held_here = sum(
            rec["source_system"] == system and rec["disposition"] == "HELD_FOR_REVIEW"
            for rec in issue_records.values()
        )
        quarantined_here = sum(
            rec["source_system"] == system and rec["disposition"] == "QUARANTINED"
            for rec in issue_records.values()
        )
        if held_here != local["HELD_FOR_REVIEW"] or quarantined_here != local["QUARANTINED"]:
            raise ValueError("Per-system exception mismatch")
        if system_events[system] != sum(info["reason_counts"].values()):
            raise ValueError("Per-system reason-event total mismatch")
        if dict(Counter(e["reason_code"] for e in issues if e["source_system"] == system)) != info["reason_counts"]:
            raise ValueError("Per-system reason-event codes mismatch")
        per_source.append({
            "source_system": system,
            "input_records": info["rows_read"],
            **{d: local[d] for d in COUNTS},
            "standardisable_records": approved_here,
            "reason_events": system_events[system],
            "unresolved_fields": list(info["unresolved_fields"]),
        })

    if any(sum(s[d] for s in per_source) != counts[d] for d in COUNTS):
        raise ValueError("Source totals disagree with global totals")
    if len(issues) != sum(reason_counts.values()):
        raise ValueError("Reason event tally mismatch")

    record_view = [
        {
            "source_system": v["source_system"],
            "source_record_id": v["source_record_id"],
            "disposition": v["disposition"],
            "reason_codes": ", ".join(sorted(v["reason_codes"])),
            "reason_event_count": v["reason_events"],
        }
        for _key, v in sorted(issue_records.items())
    ]
    return {
        "input_records": report["row_count"],
        "counts": {d: counts[d] for d in COUNTS},
        "standardisable_records": accepted,
        "exception_records": len(issue_records),
        "reason_events": len(issues),
        "per_source": per_source,
        "exception_records_view": record_view,
        "reason_counts": dict(sorted(reason_counts.items())),
        "evaluation_is_placeholder": True,
    }
