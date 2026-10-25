"""Backend acceptance tests using temporary databases and a stub LLM."""

from __future__ import annotations

from decimal import Decimal
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.init_db import initialize_database
from src.config import Settings
from src.documents import DocumentTool
from src.evaluator import AgentResponse, EvaluationAgent, OpenAIJsonLLM
from src.exporter import export_run_json
from src.models import (
    CriterionEvaluation,
    EvaluationCriterion,
    ExtractedDocument,
    DocumentPage,
    SupplierEvaluation,
    SupplierSubmission,
)
from src.orchestrator import RunOrchestrator
from src.ranking import rank_suppliers
from src.repository import EvaluationRepository
from src.scoring import score_suppliers
from src.validation import ScorecardValidationError, ValidationTool


class FixedLLM:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.prompts: list[str] = []

    def complete_json(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0)


class FixedDocumentTool:
    def extract(self, filename: str, pdf_bytes: bytes) -> ExtractedDocument:
        if pdf_bytes != b"valid-test-pdf":
            raise ValueError("invalid test PDF")
        return ExtractedDocument(filename, (DocumentPage(1, "Evidence present in proposal."),))


class BackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "rfp_evaluation.sqlite3"
        initialize_database(self.database_path)
        self.repository = EvaluationRepository(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_initializer_is_idempotent_and_seeds_five_criteria(self) -> None:
        initialize_database(self.database_path)
        criteria = self.repository.load_active_criteria()
        self.assertEqual(len(criteria), 5)
        self.assertEqual(sum((item.weight for item in criteria), Decimal("0")), Decimal("100"))

    def test_validation_normalizes_invalid_results_and_checks_evidence(self) -> None:
        criteria = self.repository.load_active_criteria()
        payload = {
            "supplier_name": "Incorrect Name",
            "criteria": [
                {"criterion_id": 1, "score": "12", "max_score": 20,
                 "justification": "Too high", "evidence": "Page 1: Evidence present in proposal."},
                {"criterion_id": 1, "score": 7, "max_score": 10,
                 "justification": "Duplicate", "evidence": "Evidence present in proposal."},
                {"criterion_id": 999, "score": 8, "max_score": 10,
                 "justification": "Unknown", "evidence": "Nowhere."},
                {"criterion_id": 2, "score": True, "max_score": 10,
                 "justification": 3, "evidence": "not in proposal"},
            ],
            "risks": [],
            "overall_summary": "Summary",
        }

        result = ValidationTool().normalize(payload, "Acme", criteria, "[Page 1]\nEvidence present in proposal.")

        self.assertEqual(result.supplier_name, "Acme")
        self.assertEqual(result.criteria[0].score, Decimal("10"))
        self.assertEqual(result.criteria[1].score, Decimal("0"))
        self.assertTrue(any("duplicate" in warning.lower() for warning in result.warnings))
        self.assertTrue(any("unknown criterion" in warning.lower() for warning in result.warnings))
        self.assertTrue(any("evidence excerpt" in warning.lower() for warning in result.warnings))
        self.assertTrue(any("missing criterion" in warning.lower() for warning in result.warnings))

    def test_malformed_json_is_a_validation_error(self) -> None:
        criteria = self.repository.load_active_criteria()
        with self.assertRaises(ScorecardValidationError):
            ValidationTool().normalize("not json", "Acme", criteria, "text")

    def test_validation_accepts_id_keyed_criteria_and_alternate_evidence_fields(self) -> None:
        criteria = self.repository.load_active_criteria()
        payload = {
            "supplier_name": "Apex",
            "criteria": {
                str(item.criterion_id): {
                    "score": 7 + item.criterion_id % 3,
                    "justification": "The proposal supports this score.",
                    "supporting_excerpt": "Architecture evidence is in this proposal.",
                }
                for item in criteria
            },
            "risks": [],
            "overall_summary": "Evaluated from proposal evidence.",
        }

        result = ValidationTool().normalize(
            payload,
            "Apex",
            criteria,
            "Page 1: Architecture evidence is in this proposal.",
        )

        self.assertEqual(len(result.criteria), len(criteria))
        self.assertEqual([item.criterion_id for item in result.criteria], [item.criterion_id for item in criteria])
        self.assertTrue(all(item.score > 0 for item in result.criteria))
        self.assertTrue(all("Architecture evidence is in this proposal." in item.evidence for item in result.criteria))
        self.assertFalse(any("unknown criterion" in warning.lower() for warning in result.warnings))
        self.assertFalse(any("inserted missing criterion" in warning.lower() for warning in result.warnings))

    def test_evaluation_agent_does_not_repair_id_keyed_criteria_object(self) -> None:
        response = json.dumps(
            {
                "supplier_name": "Apex",
                "criteria": {"1": {"score": 8}},
                "risks": [],
                "overall_summary": "Summary",
            }
        )
        llm = FixedLLM([response])
        document = ExtractedDocument("proposal.pdf", (DocumentPage(1, "Evidence."),))
        criteria = self.repository.load_active_criteria()

        result = EvaluationAgent(llm).evaluate("Apex", criteria, document)

        self.assertEqual(result.json_text, response)
        self.assertEqual(len(llm.prompts), 1)

    def test_openai_compatible_adapter_accepts_custom_endpoint(self) -> None:
        adapter = OpenAIJsonLLM(
            model="custom-model",
            api_key="test-session-key",
            base_url="https://provider.example/v1",
        )

        self.assertEqual(adapter.model, "custom-model")
        self.assertEqual(adapter.base_url, "https://provider.example/v1")
        self.assertEqual(adapter.api_key, "test-session-key")
        self.assertNotIn("api_key", Settings(database_path=self.database_path).safe_run_config())

    def test_ui_renders_results_for_selected_persisted_run(self) -> None:
        criteria = self.repository.load_active_criteria()
        scorecard = {
            "supplier_name": "Selected Run Supplier",
            "criteria": [
                {
                    "criterion_id": item.criterion_id,
                    "score": 8,
                    "max_score": format(item.max_score, "f"),
                    "justification": "Supported by proposal.",
                    "evidence": "Page 1: Evidence present in proposal.",
                }
                for item in criteria
            ],
            "risks": [],
            "overall_summary": "Selected run test.",
        }
        orchestrator = RunOrchestrator(
            self.repository,
            FixedDocumentTool(),
            EvaluationAgent(FixedLLM([json.dumps(scorecard)])),
            ValidationTool(),
            Settings(database_path=self.database_path),
        )
        orchestrator.run(
            [SupplierSubmission(
                "Selected Run Supplier", "2026-10-01", Decimal("7"), b"valid-test-pdf", "demo.pdf"
            )]
        )

        from streamlit.testing.v1 import AppTest

        connection = sqlite3.connect(self.database_path)
        try:
            run_id = connection.execute(
                "SELECT rfp_run_id FROM rfp_runs WHERE status = 'completed' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()[0]
        finally:
            connection.close()
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20)
        app.session_state["current_run_id"] = run_id
        with patch.dict(os.environ, {"RFP_DATABASE_PATH": str(self.database_path)}):
            app.run()

        self.assertFalse(app.exception)
        self.assertGreaterEqual(len(app.dataframe), 3)

    def test_decimal_scoring_zero_benchmark_and_tie_breaks(self) -> None:
        criteria = (
            EvaluationCriterion(1, "Quality", "", Decimal("50"), Decimal("10")),
            EvaluationCriterion(2, "Delivery", "", Decimal("50"), Decimal("10")),
        )
        suppliers = [
            SupplierEvaluation("Beta", "2026-10-02", Decimal("8"), (
                CriterionEvaluation(1, Decimal("8"), Decimal("10"), "", ""),
                CriterionEvaluation(2, Decimal("0"), Decimal("10"), "", ""),
            )),
            SupplierEvaluation("Alpha", "2026-10-01", Decimal("6"), (
                CriterionEvaluation(1, Decimal("8"), Decimal("10"), "", ""),
                CriterionEvaluation(2, Decimal("0"), Decimal("10"), "", ""),
            )),
        ]
        results = rank_suppliers(score_suppliers(criteria, suppliers))
        self.assertEqual([result.supplier_name for result in results], ["Alpha", "Beta"])
        self.assertEqual([result.final_rank for result in results], [1, 2])
        self.assertEqual(results[0].criteria[1].relative_performance_pct, Decimal("0"))
        self.assertEqual(results[0].absolute_score, Decimal("40"))
        self.assertIn("earlier submission", results[0].ranking_explanation.lower())

    def test_ranking_applies_every_tie_break_in_order(self) -> None:
        from src.models import RankedSupplier

        def ranked(name: str, ppi: str, submitted: str, experience: str) -> RankedSupplier:
            return RankedSupplier(
                name,
                submitted,
                Decimal(experience),
                Decimal("50"),
                Decimal(ppi),
                0,
                (),
                (),
                "",
                (),
                "",
                "",
                "",
            )

        result = rank_suppliers(
            [
                ranked("PPI lower", "99", "2026-01-01", "10"),
                ranked("Date later", "100", "2026-01-02", "10"),
                ranked("Experience lower", "100", "2026-01-01", "9"),
                ranked("Beta", "100", "2026-01-01", "10"),
                ranked("Alpha", "100", "2026-01-01", "10"),
            ]
        )

        self.assertEqual(
            [item.supplier_name for item in result],
            ["Alpha", "Beta", "Experience lower", "Date later", "PPI lower"],
        )
        self.assertEqual([item.final_rank for item in result], [1, 2, 3, 4, 5])

    def test_orchestrator_success_persists_complete_run_and_export(self) -> None:
        criteria = self.repository.load_active_criteria()
        scorecard = {
            "supplier_name": "Demo Supplier",
            "criteria": [
                {
                    "criterion_id": item.criterion_id,
                    "score": 7,
                    "max_score": format(item.max_score, "f"),
                    "justification": "Supported by proposal.",
                    "evidence": "Page 1: Evidence present in proposal.",
                }
                for item in criteria
            ],
            "risks": [],
            "overall_summary": "A test supplier.",
        }
        llm = FixedLLM([json.dumps(scorecard)])
        settings = Settings(database_path=self.database_path)
        orchestrator = RunOrchestrator(
            self.repository,
            FixedDocumentTool(),
            EvaluationAgent(llm),
            ValidationTool(),
            settings,
        )
        submission = SupplierSubmission(
            "Demo Supplier", "2026-10-01", Decimal("7"), b"valid-test-pdf", "demo.pdf"
        )

        result = orchestrator.run([submission])
        exported = json.loads(export_run_json(self.repository, result.rfp_run_id))

        self.assertEqual(result.status, "completed")
        self.assertEqual(exported["status"], "completed")
        self.assertEqual(len(exported["suppliers"]), 1)
        self.assertEqual(exported["suppliers"][0]["final_rank"], 1)
        self.assertIn("untrusted quoted content", llm.prompts[0])
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(connection.execute("SELECT status FROM rfp_runs").fetchone()[0], "completed")
        finally:
            connection.close()

    def test_orchestrator_failure_leaves_no_partial_supplier_rows(self) -> None:
        scorecard = "not-json"
        llm = FixedLLM([scorecard, scorecard])
        orchestrator = RunOrchestrator(
            self.repository,
            FixedDocumentTool(),
            EvaluationAgent(llm),
            ValidationTool(),
            Settings(database_path=self.database_path),
        )
        submission = SupplierSubmission(
            "Demo Supplier", "2026-10-01", Decimal("7"), b"valid-test-pdf", "demo.pdf"
        )

        with self.assertRaisesRegex(RuntimeError, "failed"):
            orchestrator.run([submission])
        connection = sqlite3.connect(self.database_path)
        try:
            run = connection.execute("SELECT status, error_message FROM rfp_runs").fetchone()
            row_count = connection.execute("SELECT count(*) FROM supplier_results").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(run[0], "failed")
        self.assertTrue(run[1])
        self.assertEqual(row_count, 0)

    def test_document_tool_rejects_non_pdf_and_reads_real_sample(self) -> None:
        tool = DocumentTool()
        with self.assertRaisesRegex(ValueError, "not a PDF"):
            tool.extract("invalid.pdf", b"not a PDF")
        sample = (
            Path(__file__).resolve().parents[1]
            / "samples"
            / "synthetic_rfp_proposals_all"
            / "apex_systems_proposal.pdf"
        )
        document = tool.extract(sample.name, sample.read_bytes())
        self.assertTrue(document.pages)
        self.assertIn("[Page 1]", document.text)
        self.assertIn("APEX SYSTEMS", document.text)


if __name__ == "__main__":
    unittest.main()