"""Decimal-based deterministic score calculation and peer benchmarking."""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Sequence

from src.models import EvaluationCriterion, RankedCriterionScore, RankedSupplier, SupplierEvaluation, validate_criteria


def score_suppliers(
    criteria: Sequence[EvaluationCriterion],
    suppliers: Sequence[SupplierEvaluation],
) -> list[RankedSupplier]:
    validate_criteria(criteria)
    if not suppliers:
        raise ValueError("At least one validated supplier scorecard is required.")
    names = [item.supplier_name.strip().casefold() for item in suppliers]
    if len(names) != len(set(names)):
        raise ValueError("Supplier names must be unique after trimming and case-folding.")
    active_ids = {criterion.criterion_id for criterion in criteria}
    for supplier in suppliers:
        score_ids = [score.criterion_id for score in supplier.criteria]
        if len(score_ids) != len(set(score_ids)) or set(score_ids) != active_ids:
            raise ValueError(f"{supplier.supplier_name} must have one score per active criterion.")

    with localcontext() as context:
        context.prec = 28
        score_maps = {
            item.supplier_name: {score.criterion_id: score for score in item.criteria}
            for item in suppliers
        }
        benchmarks = {
            criterion.criterion_id: max(
                score_maps[item.supplier_name][criterion.criterion_id].score for item in suppliers
            )
            for criterion in criteria
        }
        ranked: list[RankedSupplier] = []
        for supplier in suppliers:
            absolute = Decimal("0")
            weighted_relative = Decimal("0")
            criteria_output: list[RankedCriterionScore] = []
            for criterion in criteria:
                score = score_maps[supplier.supplier_name][criterion.criterion_id]
                if not score.score.is_finite() or not Decimal("0") <= score.score <= criterion.max_score:
                    raise ValueError(f"{supplier.supplier_name} has an invalid normalized score.")
                benchmark = benchmarks[criterion.criterion_id]
                relative = Decimal("0") if benchmark == 0 else Decimal("100") * score.score / benchmark
                absolute += score.score / criterion.max_score * criterion.weight
                weighted_relative += relative * criterion.weight
                criteria_output.append(
                    RankedCriterionScore(
                        criterion.criterion_id,
                        criterion.name,
                        score.score,
                        criterion.max_score,
                        criterion.weight,
                        score.justification,
                        score.evidence,
                        benchmark,
                        score.score - benchmark,
                        relative,
                    )
                )
            ranked.append(
                RankedSupplier(
                    supplier.supplier_name.strip(),
                    supplier.submission_date,
                    supplier.experience_rating,
                    absolute,
                    weighted_relative / Decimal("100"),
                    0,
                    tuple(criteria_output),
                    supplier.risks,
                    supplier.overall_summary,
                    supplier.warnings,
                    supplier.source_filename,
                    supplier.raw_response,
                    "",
                )
            )
        return ranked