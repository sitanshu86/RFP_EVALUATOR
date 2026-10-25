"""Ordered run orchestration for the agent/tool backend workflow."""

from __future__ import annotations

from decimal import Decimal
import json
from typing import Sequence

from src.config import Settings
from src.documents import DocumentTool
from src.evaluator import EvaluationAgent, EvaluationError
from src.models import EvaluationRun, SupplierEvaluation, SupplierSubmission, decimal_value, validate_submission
from src.ranking import rank_suppliers
from src.repository import EvaluationRepository
from src.scoring import score_suppliers
from src.validation import ScorecardValidationError, ValidationTool


class RunOrchestrator:
    def __init__(
        self,
        repository: EvaluationRepository,
        document_tool: DocumentTool,
        evaluation_agent: EvaluationAgent,
        validation_tool: ValidationTool,
        settings: Settings,
    ):
        self.repository = repository
        self.document_tool = document_tool
        self.evaluation_agent = evaluation_agent
        self.validation_tool = validation_tool
        self.settings = settings

    def run(self, submissions: Sequence[SupplierSubmission]) -> EvaluationRun:
        self._validate_submissions(submissions)
        criteria = tuple(self.repository.load_active_criteria())
        run_config = self.settings.safe_run_config()
        run_id, created_at = self.repository.create_run(criteria, run_config)
        try:
            evaluations: list[SupplierEvaluation] = []
            for submission in submissions:
                document = self.document_tool.extract(submission.source_filename, submission.pdf_bytes)
                response = self.evaluation_agent.evaluate(submission.supplier_name, criteria, document)
                validated = self.validation_tool.normalize(
                    response.json_text,
                    submission.supplier_name,
                    criteria,
                    document.text,
                )
                evaluations.append(
                    SupplierEvaluation(
                        supplier_name=submission.supplier_name.strip(),
                        submission_date=submission.submission_date,
                        experience_rating=submission.experience_rating,
                        criteria=validated.criteria,
                        risks=validated.risks,
                        overall_summary=validated.overall_summary,
                        warnings=validated.warnings,
                        source_filename=submission.source_filename,
                        raw_response=json.dumps({"attempts": list(response.diagnostics)}),
                    )
                )
            ranked = tuple(rank_suppliers(score_suppliers(criteria, evaluations)))
            warnings = tuple(
                f"{supplier.supplier_name}: {warning}"
                for supplier in ranked
                for warning in supplier.warnings
            )
            self.repository.complete_run(run_id, ranked, warnings)
            return EvaluationRun(run_id, created_at, "completed", criteria, ranked, warnings, run_config)
        except Exception as error:
            message = self._safe_failure_message(error)
            self.repository.fail_run(run_id, message)
            raise RuntimeError(f"RFP run {run_id} failed: {message}") from error

    def _validate_submissions(self, submissions: Sequence[SupplierSubmission]) -> None:
        if not submissions:
            raise ValueError("Upload at least one supplier proposal.")
        minimum = decimal_value(self.settings.experience_min, "experience minimum")
        maximum = decimal_value(self.settings.experience_max, "experience maximum")
        names: set[str] = set()
        for submission in submissions:
            validate_submission(submission, minimum, maximum)
            name = submission.supplier_name.strip().casefold()
            if name in names:
                raise ValueError("Supplier names must be unique within a run.")
            names.add(name)

    @staticmethod
    def _safe_failure_message(error: Exception) -> str:
        if isinstance(error, (ScorecardValidationError, EvaluationError, ValueError)):
            return str(error)
        return "An unexpected backend error occurred. Check server logs without exposing credentials."