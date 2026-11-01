"""Phase 2 candidate-ranking tests; no real model download or gold labels used."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemabridge.matchers import (
    _take_top, lexical_candidates, normalize_label, preview_candidates,
    read_demo_contract, semantic_candidates,
)


class FakeModel:
    def encode(self, texts, **kwargs):
        # Tests operate only on vectors, independent of any MiniLM install.
        self.texts = texts
        self.kwargs = kwargs
        return [[1, 0], [1, 0], [0, 1]]


class ZeroModel:
    def encode(self, texts, **kwargs):
        return [[0, 0], [1, 0], [0, 1]]


class Phase2RankingTests(unittest.TestCase):
    def test_normalization_preserves_word_boundaries(self):
        self.assertEqual(normalize_label("Resolution_Minutes"), "resolution minutes")
        self.assertEqual(normalize_label("incidentID"), "incident id")
        self.assertEqual(normalize_label("GeoArea-code"), "geo area code")

    def test_lexical_exact_header_has_perfect_similarity(self):
        canonical = {"incident_id": {}, "region": {}, "resolution_minutes": {}}
        ranking = lexical_candidates("Incident_ID", canonical, top_k=3)
        self.assertEqual(ranking[0]["canonical_field"], "incident_id")
        self.assertEqual(ranking[0]["similarity"], 1.0)

    def test_lexical_does_not_pretend_to_approve(self):
        _, schema, _ = read_demo_contract(ROOT / "config/demo.json")
        ranks = lexical_candidates("Customer_ID", schema["business_fields"])
        self.assertEqual(len(ranks), 4)
        self.assertNotIn("decision", ranks[0])
        self.assertNotIn("confidence", ranks[0])

    def test_ties_are_deterministic(self):
        ranks = _take_top({"region": 0.5, "incident_id": 0.5}, 2)
        self.assertEqual([r["canonical_field"] for r in ranks], ["incident_id", "region"])

    def test_semantic_fake_encoder_ranks_cosine(self):
        source = {"Ticket_No": {"description": "Ticket number for incidents"}}
        canonical = {
            "incident_id": {"description": "Incident number"},
            "region": {"description": "Geographic province"},
        }
        model = FakeModel()
        ranks = semantic_candidates(source, canonical, model, top_k=2)
        self.assertEqual(ranks["Ticket_No"][0]["canonical_field"], "incident_id")
        self.assertAlmostEqual(ranks["Ticket_No"][0]["similarity"], 1)
        self.assertEqual(len(model.texts), 3)

    def test_zero_vector_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "zero-length"):
            semantic_candidates(
                {"Ticket_No": {"description": "Reference"}},
                {"incident_id": {"description": "Identifier"},
                 "region": {"description": "Province"}},
                ZeroModel(), top_k=2,
            )

    def test_top_k_rejects_invalid_range(self):
        with self.assertRaises(ValueError):
            _take_top({"region": 0.1}, 2)

    def test_preview_reads_four_business_fields_not_generated_ones(self):
        report = preview_candidates(ROOT / "config/demo.json", method="lexical")
        self.assertEqual(report["stage"], "P2_CANDIDATE_RANKING_ONLY")
        self.assertEqual(report["source_column_count"], 22)
        self.assertEqual(report["business_target_count"], 4)
        self.assertFalse(report["mapping_validation_performed"])
        self.assertFalse(report["gold_labels_accessed"])
        self.assertFalse(report["data_transformed"])
        self.assertTrue(all(x["mapping_decision"] == "NOT_EVALUATED"
                            and len(x["lexical_candidates"]) == 4
                            and x["semantic_candidates"] is None
                            for x in report["results"]))

    def test_semantic_preview_with_fake_encoder_no_network(self):
        class AllSame:
            def encode(self, texts, **kwargs):
                return [[1.0, 0.0] for _ in texts]
        report = preview_candidates(ROOT / "config/demo.json", method="semantic",
                                    model=AllSame(), offline=True)
        self.assertTrue(all(row["lexical_candidates"] is None and
                            len(row["semantic_candidates"]) == 4
                            for row in report["results"]))
        self.assertTrue(report["semantic_model_local_files_only"])


if __name__ == "__main__":
    unittest.main()
