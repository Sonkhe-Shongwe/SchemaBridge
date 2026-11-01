"""Safety tests for the independent field gate. No embedding model, CSV or gold labels."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from schemabridge.validators import validate_suggestions


def source_field(kind="incident_duration", declared_type="number", unit="hour", **extra):
    return {
        "description": "Elapsed resolution time in an incident report",
        "entity_kind": kind,
        "declared_type": declared_type,
        "unit": unit,
        **extra,
    }


TARGETS = {
    "incident_id": {"entity_kind": "incident_identifier", "type": "string"},
    "region": {"entity_kind": "service_region", "type": "string",
               "allowed_values": ["Gauteng", "Mpumalanga"]},
    "incident_status": {"entity_kind": "incident_lifecycle", "type": "enum",
                        "allowed_values": ["Open", "Closed"]},
    "resolution_minutes": {"entity_kind": "incident_duration", "type": "number",
                           "unit": "minute"},
}

RULES = {
    "duration_units": {"hour": {"dimension": "duration", "multiplier_to_minutes": 60},
                       "minute": {"dimension": "duration", "multiplier_to_minutes": 1}},
    "regions": {"system_a": {"GP": "Gauteng", "MP": "Mpumalanga"}},
    "statuses": {"system_a": {"New": "Open", "Resolved": "Closed"}},
}


def suggestion(col, target="resolution_minutes", score=0.93, runner_up=0.15, *, lexical=False):
    other = "incident_id" if target != "incident_id" else "region"
    ranking = [
        {"canonical_field": target, "similarity": score},
        {"canonical_field": other, "similarity": runner_up},
    ]
    return {
        "source_system": "system_a",
        "source_column": col,
        "semantic_candidates": None if lexical else ranking,
        "lexical_candidates": ranking if lexical else None,
    }


def run_gate(columns, candidates, *, rules=RULES):
    schema = {"business_fields": TARGETS}
    metadata = {"systems": {"system_a": {"columns": columns}}}
    preview = {
        "stage": "P2_CANDIDATE_RANKING_ONLY",
        "gold_labels_accessed": False,
        "data_transformed": False,
        "results": candidates,
    }
    return validate_suggestions(preview, schema, metadata, rules)


class Phase3GateTests(unittest.TestCase):
    def test_compatible_hour_to_minute_only_as_field_rule(self):
        report = run_gate({"Fix_Hours": source_field()}, [suggestion("Fix_Hours")])
        self.assertEqual(report["counts"], {"ACCEPT": 1, "REVIEW": 0, "REJECT": 0})
        self.assertEqual(report["decisions"][0]["conversion"]["factor"], 60)
        self.assertEqual(report["rows_transformed"], 0)

    def test_incompatible_entity_customer_id_rejected(self):
        report = run_gate(
            {"Customer_ID": source_field(kind="customer_identifier", declared_type="string",
                                          unit=None)},
            [suggestion("Customer_ID", target="incident_id")],
        )
        self.assertEqual(report["decisions"][0]["decision"], "REJECT")
        self.assertIn("ENTITY_KIND_MISMATCH", report["decisions"][0]["reason_codes"])

    def test_kilometres_not_minutes(self):
        report = run_gate({"Duration_Km": source_field(unit="kilometre")},
                          [suggestion("Duration_Km")])
        self.assertEqual(report["decisions"][0]["decision"], "REJECT")
        self.assertIsNone(report["decisions"][0]["conversion"])

    def test_unknown_area_goes_to_review(self):
        report = run_gate(
            {"Area": source_field(kind="unknown_scope", declared_type="string", unit=None)},
            [suggestion("Area", target="region")],
        )
        self.assertEqual(report["decisions"][0]["decision"], "REVIEW")

    def test_unknown_entity_kind_goes_to_review(self):
        report = run_gate(
            {"Area": source_field(kind=None, declared_type="string", unit=None)},
            [suggestion("Area", target="region")],
        )
        self.assertEqual(report["decisions"][0]["decision"], "REVIEW")

    def test_missing_unit_goes_to_review(self):
        report = run_gate({"Elapsed": source_field(unit=None)}, [suggestion("Elapsed")])
        self.assertEqual(report["decisions"][0]["decision"], "REVIEW")

    def test_low_similarity_does_not_auto_accept(self):
        report = run_gate({"Elapsed": source_field()}, [suggestion("Elapsed", score=0.51)])
        self.assertEqual(report["decisions"][0]["decision"], "REVIEW")

    def test_close_runner_up_requires_review(self):
        report = run_gate({"Elapsed": source_field()},
                          [suggestion("Elapsed", score=0.92, runner_up=0.90)])
        self.assertEqual(report["decisions"][0]["decision"], "REVIEW")

    def test_two_competing_source_fields_both_review(self):
        columns = {"Fix_Hours": source_field(), "Elapsed_Hrs": source_field()}
        report = run_gate(columns, [suggestion("Fix_Hours"), suggestion("Elapsed_Hrs")])
        self.assertEqual(report["counts"]["ACCEPT"], 0)
        self.assertEqual(report["counts"]["REVIEW"], 2)
        self.assertEqual(len(report["collisions"]), 1)
        self.assertTrue(all("TARGET_COLLISION" in x["reason_codes"] for x in report["decisions"]))

    def test_unrecognized_state_needs_explicit_source_vocabulary(self):
        report = run_gate(
            {"Ticket_State": source_field(kind="incident_lifecycle", declared_type="enum",
                                          unit=None, declared_values=["New", "Bogus"])},
            [suggestion("Ticket_State", target="incident_status")],
        )
        self.assertEqual(report["decisions"][0]["decision"], "REVIEW")

    def test_known_enum_can_pass_with_approved_dictionary(self):
        report = run_gate(
            {"Ticket_State": source_field(kind="incident_lifecycle", declared_type="enum",
                                          unit=None, declared_values=["New", "Resolved"])},
            [suggestion("Ticket_State", target="incident_status")],
        )
        self.assertEqual(report["decisions"][0]["decision"], "ACCEPT")

    def test_type_conflict_rejected(self):
        report = run_gate(
            {"Duration": source_field(declared_type="datetime")},
            [suggestion("Duration")],
        )
        self.assertEqual(report["decisions"][0]["decision"], "REJECT")

    def test_missing_column_in_preview_is_error(self):
        with self.assertRaisesRegex(ValueError, "missing source columns"):
            run_gate({"Fix_Hours": source_field()}, [])

    def test_lexical_only_requires_stronger_similarity(self):
        report = run_gate({"Duration": source_field()},
                          [suggestion("Duration", score=0.70, lexical=True)])
        self.assertEqual(report["decisions"][0]["decision"], "REVIEW")

    def test_reject_injected_gold_tainted_preview(self):
        columns = {"Fix_Hours": source_field()}
        with self.assertRaisesRegex(ValueError, "tainted"):
            validate_suggestions(
                {"stage": "P2_CANDIDATE_RANKING_ONLY", "gold_labels_accessed": True,
                 "data_transformed": False, "results": [suggestion("Fix_Hours")]},
                {"business_fields": TARGETS}, {"systems": {"system_a": {"columns": columns}}},
                RULES,
            )

    def test_no_row_transform_or_gold_claims(self):
        report = run_gate({"Fix_Hours": source_field()}, [suggestion("Fix_Hours")])
        self.assertFalse(report["gold_labels_accessed"])
        self.assertEqual(report["rows_processed"], 0)
        self.assertEqual(report["rows_transformed"], 0)


if __name__ == "__main__":
    unittest.main()
