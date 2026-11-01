"""Independent Phase 3 field compatibility gate.

A ranking is a *suggestion*, not a probability. This module never joins rows,
reads evaluation labels, or transforms values. Only documented type, entity kind,
units and categorical dictionaries can authorize an ACCEPT candidate.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Mapping


# These are conservative, provisional development heuristics. They were NOT
# selected on holdout data and are NOT statistically calibrated probabilities.
DEFAULT_POLICY = {
    "min_semantic_similarity": 0.55,
    "min_lexical_similarity": 0.85,
    "min_runner_up_margin": 0.05,
}


def _ranked_proposal(entry: Mapping[str, Any], policy: Mapping[str, float]) -> dict:
    """Prefer semantic rankings if present; otherwise use the lexical baseline."""
    candidates = entry.get("semantic_candidates")
    kind = "semantic"
    if candidates is None:
        candidates = entry.get("lexical_candidates")
        kind = "lexical"
    if not isinstance(candidates, list) or not candidates:
        return {"kind": kind, "target": None, "score": None, "margin": None, "reason": "NO_CANDIDATE"}
    seen = set()
    for row in candidates:
        if not isinstance(row, dict) or not isinstance(row.get("canonical_field"), str):
            raise ValueError("Invalid candidate record")
        name = row["canonical_field"]
        score = row.get("similarity")
        if name in seen or not isinstance(score, (float, int)) or isinstance(score, bool):
            raise ValueError("Duplicate candidate or missing numeric similarity")
        if not math.isfinite(score):
            raise ValueError("Non-finite candidate similarity")
        seen.add(name)
    ordered = sorted(candidates, key=lambda r: (-r["similarity"], r["canonical_field"]))
    top = ordered[0]
    margin = (float(top["similarity"]) - float(ordered[1]["similarity"])
              if len(ordered) > 1 else None)
    bound = ("min_semantic_similarity" if kind == "semantic"
             else "min_lexical_similarity")
    if margin is None:
        reason = "ALTERNATIVE_NOT_AVAILABLE"
    elif top["similarity"] < policy[bound]:
        reason = "LOW_SIMILARITY"
    elif margin < policy["min_runner_up_margin"]:
        reason = "AMBIGUOUS_MARGIN"
    else:
        reason = "SCORE_GATE_PASSED"
    return {
        "kind": kind,
        "target": top["canonical_field"],
        "score": top["similarity"],
        "margin": margin,
        "reason": reason,
    }


def _compatibility(
    source: Mapping[str, Any],
    canonical: Mapping[str, Any],
    target: str,
    system: str,
    value_rules: Mapping[str, Any],
) -> tuple[str, list[str], dict | None]:
    """Independent hard veto with explicit unit/status dictionaries."""
    reasons = []
    source_type, target_type = source.get("declared_type"), canonical.get("type")
    source_kind, target_kind = source.get("entity_kind"), canonical.get("entity_kind")
    if not source_kind or not target_kind or str(source_kind).startswith("unknown_"):
        return "REVIEW", ["ENTITY_KIND_UNKNOWN"], None
    if source_kind != target_kind:
        return "REJECT", ["ENTITY_KIND_MISMATCH"], None
    if not source_type or not target_type:
        return "REVIEW", ["TYPE_UNKNOWN"], None
    if source_type != target_type:
        return "REJECT", ["TYPE_MISMATCH"], None
    desc = source.get("description")
    if not isinstance(desc, str) or not desc.strip():
        return "REVIEW", ["DESCRIPTION_MISSING"], None

    conversion = None
    if target == "resolution_minutes":
        unit = source.get("unit")
        if not unit:
            return "REVIEW", ["UNIT_UNKNOWN"], None
        permitted = value_rules.get("duration_units", {}).get(unit)
        if not isinstance(permitted, dict) or permitted.get("dimension") != "duration":
            return "REJECT", ["UNIT_NOT_COMPATIBLE"], None
        factor = permitted.get("multiplier_to_minutes")
        if not isinstance(factor, (float, int)) or isinstance(factor, bool) or not math.isfinite(factor) or factor <= 0:
            return "REJECT", ["UNIT_CONVERSION_UNSAFE"], None
        conversion = {"from_unit": unit, "to_unit": "minute", "factor": factor}
        reasons.append("DECLARED_DURATION_UNIT_CONVERSION")
    elif source.get("unit") is not None:
        return "REJECT", ["UNEXPECTED_UNIT_FOR_TARGET"], None

    if target in ("region", "incident_status"):
        policy_name = "regions" if target == "region" else "statuses"
        mapping = value_rules.get(policy_name, {}).get(system)
        if not isinstance(mapping, dict) or not mapping:
            return "REVIEW", ["CATEGORY_DICTIONARY_MISSING"], None
        known_values = source.get("declared_values")
        # If source system never documented a controlled domain, hold for review.
        if not isinstance(known_values, list) or not known_values:
            return "REVIEW", ["SOURCE_CATEGORY_DOMAIN_UNKNOWN"], None
        if not all(isinstance(item, str) and item in mapping for item in known_values):
            return "REVIEW", ["SOURCE_CATEGORY_UNAPPROVED"], None
        allowed = set(canonical.get("allowed_values", []))
        if not allowed or not set(mapping.values()).issubset(allowed):
            return "REJECT", ["CATEGORY_MAPPING_INVALID"], None
        reasons.append("EXPLICIT_CATEGORY_DICTIONARY")

    reasons.append("ENTITY_TYPE_AND_MEANING_METADATA_COMPATIBLE")
    return "COMPATIBLE", reasons, conversion



def _compatible_alternative(
    entry: Mapping[str, Any],
    source: Mapping[str, Any],
    targets: Mapping[str, Mapping[str, Any]],
    system: str,
    value_rules: Mapping[str, Any],
    policy: Mapping[str, float],
) -> dict[str, Any] | None:
    """Investigate non-top candidates only after a known-incompatible top-1.

    The independent business type, entity kind, category dictionary and unit
    rules must positively return COMPATIBLE. One uniquely compatible alternate
    can pass the EXISTING similarity threshold. An ambiguous or weak alternate
    remains REVIEW. No source-column aliases or development gold are consulted.
    """
    ranks = entry.get("semantic_candidates")
    method = "semantic"
    if ranks is None:
        ranks = entry.get("lexical_candidates")
        method = "lexical"
    if not isinstance(ranks, list) or len(ranks) <= 1:
        return None

    viable = []
    for position, ranked in enumerate(ranks[1:], start=2):
        target = ranked["canonical_field"]
        if target not in targets:
            raise ValueError("Unexpected canonical target in ranking")
        compatibility, why, conversion = _compatibility(
            source, targets[target], target, system, value_rules,
        )
        if compatibility == "COMPATIBLE":
            viable.append((position, ranked, why, conversion))
    if not viable:
        return None
    if len(viable) > 1:
        # An alternative can be investigated, never auto-accepted when
        # more than one target independently matches source declarations.
        position, candidate, why, _ = viable[0]
        return {
            "target": candidate["canonical_field"], "score": candidate["similarity"],
            "rank": position, "method": method, "decision": "REVIEW",
            "reason_codes": why + ["MULTIPLE_COMPATIBLE_ALTERNATIVES"],
            "conversion": None,
        }

    position, candidate, why, conversion = viable[0]
    bound = ("min_semantic_similarity" if method == "semantic"
             else "min_lexical_similarity")
    enough = candidate["similarity"] >= policy[bound]
    return {
        "target": candidate["canonical_field"], "score": candidate["similarity"],
        "rank": position, "method": method,
        "decision": "ACCEPT" if enough else "REVIEW",
        "reason_codes": why + [
            "UNIQUE_COMPATIBLE_ALTERNATIVE",
            "SCORE_GATE_PASSED" if enough else "LOW_SIMILARITY",
        ],
        "conversion": conversion if enough else None,
    }


def validate_suggestions(
    preview: Mapping[str, Any],
    schema: Mapping[str, Any],
    metadata: Mapping[str, Any],
    value_rules: Mapping[str, Any],
    *,
    policy: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Validate column proposals conservatively; no transformation performed.

    Candidate rankings are consumed only after independent metadata rules.
    Unknown/ambiguous proposals require review. Hard incompatibility rejects.
    All collision contenders are reviewed instead of last-column-wins.
    """
    if preview.get("stage") != "P2_CANDIDATE_RANKING_ONLY":
        raise ValueError("Expected Phase 2 candidate-ranking report")
    if preview.get("gold_labels_accessed") is not False or preview.get("data_transformed") is not False:
        raise ValueError("Refusing non-preview or tainted candidate report")
    if not isinstance(preview.get("results"), list):
        raise ValueError("Candidate report missing results")
    final_policy = dict(DEFAULT_POLICY)
    if policy:
        for name, value in policy.items():
            if name not in final_policy or not isinstance(value, (float, int)) or isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 2:
                raise ValueError(f"Invalid validation policy parameter: {name}")
            final_policy[name] = float(value)
    canonical_fields = schema["business_fields"]
    source_systems = metadata["systems"]
    decisions: list[dict[str, Any]] = []
    observed = set()
    for item in preview["results"]:
        system, column = item.get("source_system"), item.get("source_column")
        key = (system, column)
        if not isinstance(system, str) or system not in source_systems:
            raise ValueError("Unknown source system in preview")
        if not isinstance(column, str) or column not in source_systems[system]["columns"]:
            raise ValueError("Unknown source column in preview")
        if key in observed:
            raise ValueError("Duplicate source column in candidate report")
        observed.add(key)
        proposed = _ranked_proposal(item, final_policy)
        target = proposed["target"]
        original_top = target
        original_margin = proposed["margin"]
        original_hard_reasons: list[str] = []
        selected_rank = 1 if target is not None else None
        selection_strategy = "PRIMARY_TOP1"
        decision = "REVIEW"
        reasons = [proposed["reason"]]
        conversion = None
        if target is not None:
            if target not in canonical_fields:
                raise ValueError("Unknown or technical provenance target proposed")
            source = source_systems[system]["columns"][column]
            hard_decision, hard_reasons, conversion = _compatibility(
                source, canonical_fields[target], target, system, value_rules,
            )
            reasons.extend(hard_reasons)
            if hard_decision == "REJECT":
                decision = "REJECT"
                original_hard_reasons = list(hard_reasons)
                conversion = None
                alternative = _compatible_alternative(
                    item, source, canonical_fields, system, value_rules, final_policy
                )
                if alternative is not None:
                    selection_strategy = "COMPATIBLE_ALTERNATIVE"
                    target = alternative["target"]
                    proposed = {**proposed,
                                "target": target,
                                "score": alternative["score"],
                                "kind": alternative["method"],
                                "margin": None}
                    selected_rank = alternative["rank"]
                    decision = alternative["decision"]
                    reasons = ["PRIMARY_TOP1_INCOMPATIBLE"] + alternative["reason_codes"]
                    conversion = alternative["conversion"]
            elif hard_decision == "REVIEW":
                decision = "REVIEW"
                conversion = None
            elif proposed["reason"] == "SCORE_GATE_PASSED":
                decision = "ACCEPT"
        decisions.append({
            "source_system": system,
            "source_column": column,
            "proposed_target": target,
            "method": proposed["kind"],
            "similarity": proposed["score"],
            "top_two_margin": proposed["margin"],
            "original_top_two_margin": original_margin,
            "original_top_candidate": original_top,
            "primary_rejection_reasons": original_hard_reasons,
            "selection_strategy": selection_strategy,
            "selected_candidate_rank": selected_rank,
            "decision": decision,
            "reason_codes": sorted(set(reasons)),
            "conversion": conversion if decision == "ACCEPT" else None,
        })

    expected = {
        (system, col)
        for system, details in source_systems.items()
        for col in details["columns"]
    }
    if observed != expected:
        raise ValueError("Candidate report missing source columns")

    contenders: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in decisions:
        if item["decision"] == "ACCEPT":
            contenders[(item["source_system"], item["proposed_target"])].append(item)
    collisions = []
    for (system, target), group in contenders.items():
        if len(group) > 1:
            collisions.append({"source_system": system, "target": target,
                               "columns": sorted(x["source_column"] for x in group)})
            for item in group:
                item["decision"] = "REVIEW"
                item["conversion"] = None
                item["reason_codes"] = sorted(set(item["reason_codes"] + ["TARGET_COLLISION"]))
    counts = {s: sum(x["decision"] == s for x in decisions) for s in ("ACCEPT", "REVIEW", "REJECT")}
    return {
        "stage": "P3_FIELD_VALIDATION_PREVIEW_ONLY",
        "policy": final_policy,
        "decisions": sorted(decisions, key=lambda x: (x["source_system"], x["source_column"])),
        "counts": counts,
        "collisions": sorted(collisions, key=lambda x: (x["source_system"], x["target"])),
        "rows_processed": 0,
        "rows_transformed": 0,
        "gold_labels_accessed": False,
        "note": "Field-level decisions only. ACCEPT is not proof of correct mapping. Row conversion/reconciliation not yet implemented or tested.",
    }
