"""Non-destructive synthetic column profiling. Source ID strings remain strings."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Mapping


def _bad_numeric(text: str) -> tuple[bool, bool]:
    """Return invalid and negative flags without coercing missing to zero."""
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        return True, False
    if not value.is_finite():
        return True, False
    return False, value < 0


def profile_source(rows: list[dict], source_columns: Mapping[str, Mapping]) -> dict:
    """Summarize value issues without filtering, converting or joining records."""
    result = {}
    for name, attributes in source_columns.items():
        strings = [record["raw"][name] for record in rows]
        present = [value.strip() for value in strings if value.strip()]
        field_type = attributes.get("declared_type")
        invalid = negative = 0
        if field_type == "number":
            for value in present:
                bad, less_than_zero = _bad_numeric(value)
                invalid += int(bad)
                negative += int(less_than_zero)
        declared = attributes.get("declared_values")
        unknown_categories = 0
        if isinstance(declared, list):
            allowed = set(declared)
            unknown_categories = sum(value not in allowed for value in present)
        result[name] = {
            "declared_type": field_type,
            "rows": len(rows),
            "nonempty": len(present),
            "empty": len(rows) - len(present),
            "distinct_nonempty": len(set(present)),
            "nonfinite_or_non_numeric": invalid if field_type == "number" else None,
            "negative_numeric": negative if field_type == "number" else None,
            "outside_declared_categories": unknown_categories if isinstance(declared, list) else None,
            "leading_zero_identifiers": (sum(len(v) > 1 and v[0] == "0" and v.isdigit()
                                             for v in present)
                                         if attributes.get("entity_kind") == "incident_identifier"
                                         else None),
        }
    return result
