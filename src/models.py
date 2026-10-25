"""Typed domain models for inputs, validated scorecards, and run output."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence


def decimal_value(value: Any, field_name: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field_name} must be a finite number.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field_name} must be a finite number.") from error
    if not result.is_finite():
        raise ValueError(f"{field_name} must be a finite number.")
    return result


def decimal_text(value: Decimal) -> str:
    return format(value, "f")


@dataclass(frozen=True)
class EvaluationCriterion:
    criterion_id: int
    name: str
    description: str
    weight: Decimal
    max_score: Decimal


@dataclass(frozen=True)
class SupplierSubmission:
    supplier_name: str
    submission_date: str
    experience_rating: Decimal
    pdf_bytes: bytes
    source_filename: str


@dataclass(frozen=True)
class DocumentPage:
    page_number: int
    text: str


@dataclass(frozen=True)
class ExtractedDocument:
    source_filename: str
    pages: tuple[DocumentPage, ...]

    @property
    def text(self) -> str:
        return "\n\n".join(f"[Page {page.page_number}]\n{page.text}" for page in self.pages)


@dataclass(frozen=True)
class CriterionEvaluation:
    criterion_id: int
    score: Decimal
    max_score: Decimal
    justification: str
    evidence: str


@dataclass(frozen=True)
class ValidatedEvaluation:
    supplier_name: str
    criteria: tuple[CriterionEvaluation, ...]
    risks: tuple[str, ...]
    overall_summary: str
    warnings: tuple[str, ...]
    raw_response: str


@dataclass(frozen=True)
class SupplierEvaluation:
    supplier_name: str
    submission_date: str
    experience_rating: Decimal
    criteria: tuple[CriterionEvaluation, ...]
    risks: tuple[str, ...] = ()
    overall_summary: str = ""
    warnings: tuple[str, ...] = ()
    source_filename: str = ""
    raw_response: str = ""


@dataclass(frozen=True)
class RankedCriterionScore:
    criterion_id: int
    criterion_name: str
    score: Decimal
    max_score: Decimal
    weight: Decimal
    justification: str
    evidence: str
    benchmark: Decimal
    gap: Decimal
    relative_performance_pct: Decimal


@dataclass(frozen=True)
class RankedSupplier:
    supplier_name: str
    submission_date: str
    experience_rating: Decimal
    absolute_score: Decimal
    ppi: Decimal
    final_rank: int
    criteria: tuple[RankedCriterionScore, ...]
    risks: tuple[str, ...]
    overall_summary: str
    warnings: tuple[str, ...]
    source_filename: str
    raw_response: str
    ranking_explanation: str


@dataclass(frozen=True)
class EvaluationRun:
    rfp_run_id: str
    created_at: str
    status: str
    criteria: tuple[EvaluationCriterion, ...]
    suppliers: tuple[RankedSupplier, ...]
    warnings: tuple[str, ...]
    config: dict[str, str]


def validate_criteria(criteria: Sequence[EvaluationCriterion]) -> None:
    if not criteria:
        raise ValueError("At least one active evaluation criterion is required.")
    ids = [criterion.criterion_id for criterion in criteria]
    if len(ids) != len(set(ids)):
        raise ValueError("Active criterion IDs must be unique.")
    for criterion in criteria:
        if criterion.criterion_id <= 0:
            raise ValueError("Criterion IDs must be positive integers.")
        if not criterion.name.strip():
            raise ValueError("Criterion names cannot be empty.")
        if not criterion.weight.is_finite() or criterion.weight < 0:
            raise ValueError(f"Criterion {criterion.criterion_id} has an invalid weight.")
        if not criterion.max_score.is_finite() or criterion.max_score <= 0:
            raise ValueError(f"Criterion {criterion.criterion_id} needs a positive finite max score.")
    total = sum((criterion.weight for criterion in criteria), Decimal("0"))
    if total != Decimal("100"):
        raise ValueError(f"Active criterion weights must total 100%; found {total}.")


def validate_submission(
    submission: SupplierSubmission,
    experience_min: Decimal,
    experience_max: Decimal,
) -> None:
    if not submission.supplier_name.strip():
        raise ValueError("Supplier name cannot be empty.")
    try:
        date.fromisoformat(submission.submission_date)
    except ValueError as error:
        raise ValueError("Submission date must use ISO format YYYY-MM-DD.") from error
    if not submission.experience_rating.is_finite():
        raise ValueError("Experience rating must be finite.")
    if not experience_min <= submission.experience_rating <= experience_max:
        raise ValueError(f"Experience rating must be between {experience_min} and {experience_max}.")
    if not submission.pdf_bytes:
        raise ValueError(f"{submission.source_filename or submission.supplier_name}: PDF is empty.")


def criterion_from_row(row: Any) -> EvaluationCriterion:
    return EvaluationCriterion(
        criterion_id=int(row["criterion_id"]),
        name=str(row["name"]),
        description=str(row["description"]),
        weight=decimal_value(row["weight"], "criterion weight"),
        max_score=decimal_value(row["max_score"], "criterion max score"),
    )


def to_json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return decimal_text(value)
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_json_value(item) for key, item in value.__dict__.items()}
    if isinstance(value, dict):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_value(item) for item in value]
    return value