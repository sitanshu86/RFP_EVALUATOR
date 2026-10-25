"""Serialize a completed, persisted run as a reproducible JSON document."""

from __future__ import annotations

import json

from src.repository import EvaluationRepository


def export_run_json(repository: EvaluationRepository, run_id: str) -> str:
    run = repository.load_run(run_id)
    return json.dumps(run, ensure_ascii=True, indent=2, sort_keys=True)