"""Apply the required deterministic ordering and explain each rank."""

from __future__ import annotations

from datetime import date
from typing import Sequence

from src.models import RankedSupplier


def rank_suppliers(suppliers: Sequence[RankedSupplier]) -> list[RankedSupplier]:
    ordered = sorted(
        suppliers,
        key=lambda item: (
            -item.ppi,
            date.fromisoformat(item.submission_date),
            -item.experience_rating,
            item.supplier_name.strip().casefold(),
        ),
    )
    output: list[RankedSupplier] = []
    for index, item in enumerate(ordered):
        if index == 0:
            reason = "Highest PPI" if len(ordered) == 1 else _explain_precedence(item, ordered[index + 1])
        else:
            reason = _explain_precedence(ordered[index - 1], item)
        output.append(
            RankedSupplier(**{**item.__dict__, "final_rank": index + 1, "ranking_explanation": reason})
        )
    return output


def _explain_precedence(previous: RankedSupplier, current: RankedSupplier) -> str:
    if previous.ppi != current.ppi:
        return f"Higher PPI than {previous.supplier_name}."
    if previous.submission_date != current.submission_date:
        return f"Equal PPI; earlier submission date than {previous.supplier_name}."
    if previous.experience_rating != current.experience_rating:
        return f"Equal PPI and date; higher experience rating than {previous.supplier_name}."
    return f"Equal PPI, date, and experience; supplier name sorts after {previous.supplier_name}."