"""Normalize LLM scorecards and preserve auditable correction warnings."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any, Sequence

from src.models import CriterionEvaluation, EvaluationCriterion, ValidatedEvaluation, decimal_text, decimal_value, validate_criteria


class ScorecardValidationError(ValueError):
    """Raised when the LLM response cannot be interpreted as a scorecard."""


def _normalized_text(text: str) -> str:
    return " ".join(text.split()).casefold()


def _evidence_found(evidence: str, document_text: str) -> bool:
    excerpt = re.sub(r"^\s*page\s+\d+\s*:\s*", "", evidence, flags=re.IGNORECASE)
    return bool(excerpt.strip()) and _normalized_text(excerpt) in _normalized_text(document_text)


class ValidationTool:
    def normalize(
        self,
        raw_response: str | dict[str, Any],
        supplier_name: str,
        criteria: Sequence[EvaluationCriterion],
        document_text: str,
    ) -> ValidatedEvaluation:
        validate_criteria(criteria)
        raw_text = raw_response if isinstance(raw_response, str) else json.dumps(raw_response)
        try:
            payload = json.loads(raw_response) if isinstance(raw_response, str) else raw_response
        except json.JSONDecodeError as error:
            raise ScorecardValidationError("LLM response is not valid JSON after the repair attempt.") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("criteria"), (list, dict)):
            raise ScorecardValidationError("LLM response must be a JSON object containing criterion results.")

        warnings: list[str] = []
        if not isinstance(payload.get("supplier_name"), str) or payload["supplier_name"].strip() != supplier_name.strip():
            warnings.append("Model supplier name differed from submitted name; submitted name was used.")

        active_by_id = {criterion.criterion_id: criterion for criterion in criteria}
        normalized: dict[int, CriterionEvaluation] = {}
        criteria_payload = payload["criteria"]
        if isinstance(criteria_payload, dict):
            entries = list(criteria_payload.items())
        else:
            entries = [(None, item) for item in criteria_payload]

        for index, (map_id, item) in enumerate(entries):
            if not isinstance(item, dict):
                warnings.append(f"Ignored malformed criterion entry at position {index + 1}.")
                continue
            try:
                raw_id = item.get("criterion_id", item.get("id", map_id))
                criterion_id = int(raw_id) if not isinstance(raw_id, bool) else -1
            except (TypeError, ValueError):
                criterion_id = -1
            criterion = active_by_id.get(criterion_id)
            if criterion is None:
                warnings.append(f"Ignored unknown criterion ID {item.get('criterion_id')!r}.")
                continue
            if criterion_id in normalized:
                warnings.append(f"Ignored duplicate result for criterion {criterion_id}; first result kept.")
                continue

            raw_score = item.get("score")
            try:
                score = decimal_value(raw_score, f"criterion {criterion_id} score")
                if isinstance(raw_score, str):
                    warnings.append(f"Converted numeric-string score for criterion {criterion_id}.")
            except ValueError:
                score = Decimal("0")
                warnings.append(f"Replaced invalid score for criterion {criterion_id} with 0.")
            if score < 0 or score > criterion.max_score:
                original = decimal_text(score)
                score = min(max(score, Decimal("0")), criterion.max_score)
                warnings.append(f"Clipped criterion {criterion_id} score from {original} to {decimal_text(score)}.")

            try:
                reported_max = decimal_value(item.get("max_score"), f"criterion {criterion_id} max score")
            except ValueError:
                reported_max = criterion.max_score
                warnings.append(f"Replaced missing/invalid max score for criterion {criterion_id}.")
            if reported_max != criterion.max_score:
                warnings.append(f"Used configured max score for criterion {criterion_id}.")

            justification = item.get("justification")
            if not isinstance(justification, str):
                justification = ""
                warnings.append(f"Normalized missing/invalid justification for criterion {criterion_id}.")
            evidence = item.get("evidence")
            if evidence is None:
                evidence = item.get("supporting_excerpt", item.get("excerpt", ""))
                if evidence:
                    warnings.append(f"Accepted alternate evidence field for criterion {criterion_id}.")
            if not isinstance(evidence, str):
                evidence = ""
                warnings.append(f"Normalized missing/invalid evidence for criterion {criterion_id}.")
            page_number = item.get("page")
            if evidence and page_number is not None and not re.match(r"^\s*page\s+\d+\s*:", evidence, re.IGNORECASE):
                evidence = f"Page {page_number}: {evidence}"
            if not evidence.strip():
                warnings.append(f"No evidence excerpt supplied for criterion {criterion_id}.")
            elif not _evidence_found(evidence, document_text):
                warnings.append(f"Evidence excerpt for criterion {criterion_id} was not found in extracted text.")
            normalized[criterion_id] = CriterionEvaluation(
                criterion_id, score, criterion.max_score, justification, evidence
            )

        for criterion in criteria:
            if criterion.criterion_id not in normalized:
                normalized[criterion.criterion_id] = CriterionEvaluation(
                    criterion.criterion_id,
                    Decimal("0"),
                    criterion.max_score,
                    "Evaluation result was missing; score defaulted to 0.",
                    "",
                )
                warnings.append(f"Inserted missing criterion {criterion.criterion_id} with score 0.")

        risks = payload.get("risks", [])
        if not isinstance(risks, list) or any(not isinstance(risk, str) for risk in risks):
            risks = []
            warnings.append("Normalized missing or invalid risks to an empty list.")
        summary = payload.get("overall_summary", "")
        if not isinstance(summary, str):
            summary = ""
            warnings.append("Normalized missing or invalid overall summary to an empty string.")
        return ValidatedEvaluation(
            supplier_name,
            tuple(normalized[item.criterion_id] for item in criteria),
            tuple(risks),
            summary,
            tuple(warnings),
            raw_text,
        )