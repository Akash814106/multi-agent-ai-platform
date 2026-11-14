import unittest
from unittest.mock import patch

from backend.evaluation.run import evaluate_citations, facts_have_support, retrieval_metrics, run_evaluation


class EvaluationTests(unittest.TestCase):
    def test_metrics_use_exact_passages(self):
        self.assertEqual(retrieval_metrics(["pdf:2", "policy:1"], ["pdf:1", "pdf:2"]),
                         {"recall": 0.5, "precision": 0.5, "hit": 1})
        self.assertEqual(retrieval_metrics(["pdf:2"], ["pdf:1"]),
                         {"recall": 0.0, "precision": 0.0, "hit": 0})
        self.assertEqual(retrieval_metrics(["pdf:2"], []),
                         {"recall": 0.0, "precision": 0.0, "hit": 0})
        self.assertEqual(retrieval_metrics([], []),
                         {"recall": None, "precision": 0.0, "hit": None})

    def test_citation_integrity_and_passage_support_are_distinct(self):
        source = {"id": 1, "chunk_id": "doc:0", "document_id": "doc", "page": 1,
                  "filename": "manual.pdf", "chunk_index": 1, "excerpt": "A real excerpt."}
        truth = {"doc:0": {**source, "owner": "alpha", "label": "manual:1", "text": "A real excerpt."}}
        self.assertEqual(evaluate_citations("Answer [1].", [source], [source], truth, ["manual:2"], "alpha"),
                         {"structurally_valid": True, "passage_support": False})
        for mutation in [{"page": 2}, {"document_id": "foreign"}, {"excerpt": "Invented"}]:
            forged = {**source, **mutation}
            self.assertFalse(evaluate_citations("Answer [1].", [forged], [forged], truth,
                                               ["manual:1"], "alpha")["structurally_valid"])
        self.assertFalse(evaluate_citations("Answer [9].", [source], [source], truth, ["manual:1"],
                                           "alpha")["structurally_valid"])
        self.assertFalse(evaluate_citations("Answer [1].", [source], [source], truth, ["manual:1"],
                                           "beta")["structurally_valid"])
        self.assertTrue(facts_have_support("A real excerpt [1].", [source], truth,
                                          {"real excerpt": ["manual:1"]}))
        self.assertFalse(facts_have_support("A real excerpt [1].", [source], truth,
                                           {"real excerpt": ["manual:2"]}))

    def test_real_embedding_benchmark_is_repeatable_without_production_storage(self):
        with (
            patch("backend.documents.store.document_records", side_effect=AssertionError("production Mongo")),
            patch("backend.documents.store.vectors", side_effect=AssertionError("production Chroma")),
        ):
            first = run_evaluation()
            second = run_evaluation()
        self.assertEqual(first, second)
        self.assertEqual(first["metrics"]["total_cases"], 8)
        cases = {case["id"]: case for case in first["cases"]}
        self.assertTrue(cases["foreign_mixed_selection"]["denied"])
        self.assertEqual(cases["foreign_mixed_selection"]["retrieved_passages"], [])
        self.assertTrue(cases["insufficient"]["abstained"])
