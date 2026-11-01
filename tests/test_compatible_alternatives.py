"""Adversarial tests for independently compatible alternative selection.

Use fabricated scores/metadata; NEVER use development or challenge gold labels.
The first incompatible AI guess is vetoed. A unique alternate must still pass
the original score threshold, and within-source collisions remain reviewed.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from schemabridge.validators import validate_suggestions


TARGETS = {
    "incident_id": {"required": True, "type": "string",
                    "entity_kind": "incident_identifier"},
    "incident_status": {"required": True, "type": "enum",
                        "entity_kind": "incident_lifecycle",
                        "allowed_values": ["Open", "Closed"]},
    "region": {"required": True, "type": "string",
               "entity_kind": "service_region",
               "allowed_values": ["Gauteng", "Mpumalanga"]},
    "resolution_minutes": {"required": False, "type": "number",
                           "entity_kind": "incident_duration", "unit": "minute"},
}
RULES = {
    "duration_units": {"hour": {"dimension": "duration", "multiplier_to_minutes": 60}},
    "regions": {"case_demo": {"GP": "Gauteng"}},
    "statuses": {"case_demo": {"Resolved": "Closed"}},
}


def attrs(kind, datatype="string", **additional):
    return {
        "description": "Source-authored meaning, not a target mapping label",
        "declared_type": datatype,
        "entity_kind": kind,
        **additional,
    }


def ranked(column, pairs, *, lexical=None):
    candidates = [{"canonical_field": target, "similarity": score}
                  for target, score in pairs]
    lexical_rows = ([{"canonical_field": target, "similarity": score}
                     for target, score in lexical] if lexical is not None else None)
    return {"source_system": "case_demo", "source_column": column,
            "semantic_candidates": candidates, "lexical_candidates": lexical_rows}


def run(columns, items, *, schema=TARGETS):
    meta = {"systems": {"case_demo": {"columns": columns}}}
    preview = {"stage": "P2_CANDIDATE_RANKING_ONLY", "gold_labels_accessed": False,
               "data_transformed": False, "results": items}
    return validate_suggestions(preview, {"business_fields": schema}, meta, RULES)


class AlternativeSafetyTests(unittest.TestCase):
    def test_unique_compatible_second_rank_above_cutoff_accepts(self):
        result = run({"Unfamiliar_Ref": attrs("incident_identifier")},
                     [ranked("Unfamiliar_Ref", [
                         ("incident_status", .612), ("incident_id", .587),
                         ("region", .300)])])
        decision = result["decisions"][0]
        self.assertEqual(decision["decision"], "ACCEPT")
        self.assertEqual(decision["proposed_target"], "incident_id")
        self.assertEqual(decision["selection_strategy"], "COMPATIBLE_ALTERNATIVE")
        self.assertEqual(decision["selected_candidate_rank"], 2)
        self.assertIn("PRIMARY_TOP1_INCOMPATIBLE", decision["reason_codes"])
        self.assertEqual(decision["original_top_candidate"], "incident_status")
        self.assertEqual(decision["similarity"], .587)

    def test_subthreshold_second_rank_is_review_not_autoaccept(self):
        result = run({"Some_Ticket": attrs("incident_identifier")},
                     [ranked("Some_Ticket", [
                         ("incident_status", .611), ("incident_id", .5391),
                         ("region", .20)])])
        decision = result["decisions"][0]
        self.assertEqual(decision["decision"], "REVIEW")
        self.assertEqual(decision["proposed_target"], "incident_id")
        self.assertIn("LOW_SIMILARITY", decision["reason_codes"])
        self.assertIsNone(decision["conversion"])

    def test_customer_identifier_decoy_remains_rejected(self):
        result = run({"Cust_Ref": attrs("customer_identifier")},
                     [ranked("Cust_Ref", [("incident_id", .99),
                                          ("incident_status", .7)])])
        decision = result["decisions"][0]
        self.assertEqual(decision["decision"], "REJECT")
        self.assertEqual(decision["selection_strategy"], "PRIMARY_TOP1")
        self.assertIn("ENTITY_KIND_MISMATCH", decision["reason_codes"])

    def test_unknown_entity_kind_never_autoaccepts(self):
        result = run({"Weird_Ref": attrs("unknown_scope")},
                     [ranked("Weird_Ref", [("incident_status", .95),
                                            ("incident_id", .90)])])
        self.assertEqual(result["decisions"][0]["decision"], "REVIEW")

    def test_primary_compatible_but_ambiguous_stays_review(self):
        result = run({"Case_No": attrs("incident_identifier")},
                     [ranked("Case_No", [("incident_id", .70),
                                         ("incident_status", .68)])])
        self.assertEqual(result["decisions"][0]["decision"], "REVIEW")

    def test_incompatible_units_cannot_be_approved_by_reranking(self):
        result = run({"Measure": attrs("incident_duration", "number",
                                       unit="square_metre")},
                     [ranked("Measure", [("region", .91),
                                         ("resolution_minutes", .80)])])
        self.assertEqual(result["decisions"][0]["decision"], "REJECT")

    def test_duration_hours_factor_preserved_after_fallback(self):
        result = run({"T": attrs("incident_duration", "number", unit="hour")},
                     [ranked("T", [("region", .90),
                                   ("resolution_minutes", .70)])])
        decision = result["decisions"][0]
        self.assertEqual(decision["decision"], "ACCEPT")
        self.assertEqual(decision["conversion"]["factor"], 60)

    def test_multiple_equally_compatible_alternatives_require_review(self):
        schema = dict(TARGETS)
        schema["external_incident_id"] = {
            "type": "string", "entity_kind": "incident_identifier", "required": False
        }
        result = run({"Ref": attrs("incident_identifier")},
                     [ranked("Ref", [("incident_status", .93),
                                     ("incident_id", .85),
                                     ("external_incident_id", .80)])],
                     schema=schema)
        d = result["decisions"][0]
        self.assertEqual(d["decision"], "REVIEW")
        self.assertIn("MULTIPLE_COMPATIBLE_ALTERNATIVES", d["reason_codes"])

    def test_incomplete_alternative_ranks_cannot_bypass_threshold(self):
        result = run({"Ref": attrs("incident_identifier")},
                     [ranked("Ref", [("incident_status", .90),
                                     ("incident_id", .50)])])
        self.assertNotEqual(result["decisions"][0]["decision"], "ACCEPT")

    def test_collision_after_fallback_downgrades_both(self):
        cols = {"Alt_Ref": attrs("incident_identifier"),
                "Direct_Ref": attrs("incident_identifier")}
        out = run(cols, [
            ranked("Alt_Ref", [("incident_status", .90), ("incident_id", .77)]),
            ranked("Direct_Ref", [("incident_id", .94), ("region", .30)]),
        ])
        self.assertEqual(out["counts"]["ACCEPT"], 0)
        self.assertEqual(out["counts"]["REVIEW"], 2)
        self.assertEqual(len(out["collisions"]), 1)
        self.assertTrue(all("TARGET_COLLISION" in d["reason_codes"]
                            for d in out["decisions"]))

    def test_no_gold_or_row_processing(self):
        out = run({"Ref": attrs("incident_identifier")},
                  [ranked("Ref", [("incident_status", .90), ("incident_id", .72)])])
        self.assertFalse(out["gold_labels_accessed"])
        self.assertEqual(out["rows_processed"], 0)
        self.assertEqual(out["rows_transformed"], 0)


if __name__ == "__main__":
    unittest.main()
