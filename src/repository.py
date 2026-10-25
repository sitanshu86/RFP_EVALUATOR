"""SQLite repositories for criteria, run lifecycle, persistence, and export."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from typing import Sequence
from uuid import uuid4

from src.models import EvaluationCriterion, decimal_text, to_json_value, validate_criteria


class EvaluationRepository:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def load_active_criteria(self) -> list[EvaluationCriterion]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT criterion_id, name, description, weight, max_score
                   FROM evaluation_criteria WHERE is_active = 1 ORDER BY criterion_id"""
            ).fetchall()
        criteria = [
            EvaluationCriterion(
                int(row["criterion_id"]),
                row["name"],
                row["description"],
                __import__("decimal").Decimal(str(row["weight"])),
                __import__("decimal").Decimal(str(row["max_score"])),
            )
            for row in rows
        ]
        validate_criteria(criteria)
        return criteria

    def create_run(
        self,
        criteria: Sequence[EvaluationCriterion],
        config: dict[str, str],
        rfp_run_id: str | None = None,
    ) -> tuple[str, str]:
        validate_criteria(criteria)
        run_id = rfp_run_id or str(uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        snapshot = [to_json_value(criterion) for criterion in criteria]
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO rfp_runs (
                    rfp_run_id, created_at, status, criteria_snapshot_json,
                    config_json, warnings_json, error_message
                ) VALUES (?, ?, 'running', ?, ?, '[]', NULL)""",
                (run_id, created_at, json.dumps(snapshot), json.dumps(config, sort_keys=True)),
            )
        return run_id, created_at

    def complete_run(self, run_id: str, suppliers: Sequence, warnings: Sequence[str]) -> None:
        if not suppliers:
            raise ValueError("A completed run must contain at least one ranked supplier.")
        with self._connection() as connection:
            run = connection.execute(
                "SELECT status FROM rfp_runs WHERE rfp_run_id = ?", (run_id,)
            ).fetchone()
            if run is None or run["status"] != "running":
                raise ValueError("Run is missing or no longer in running state.")
            seen: set[str] = set()
            for supplier in suppliers:
                normalized_name = supplier.supplier_name.strip().casefold()
                if normalized_name in seen:
                    raise ValueError("Supplier names must be unique within a run.")
                seen.add(normalized_name)
                connection.execute(
                    """INSERT INTO supplier_results (
                        rfp_run_id, supplier_name, submission_date, experience_rating,
                        absolute_score, ppi, final_rank, result_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        run_id,
                        supplier.supplier_name,
                        supplier.submission_date,
                        decimal_text(supplier.experience_rating),
                        decimal_text(supplier.absolute_score),
                        decimal_text(supplier.ppi),
                        supplier.final_rank,
                        json.dumps(to_json_value(supplier), ensure_ascii=True, sort_keys=True),
                    ),
                )
            connection.execute(
                """UPDATE rfp_runs SET status = 'completed', warnings_json = ?, error_message = NULL
                   WHERE rfp_run_id = ?""",
                (json.dumps(list(warnings), ensure_ascii=True), run_id),
            )

    def fail_run(self, run_id: str, error_message: str, warnings: Sequence[str] = ()) -> None:
        with self._connection() as connection:
            connection.execute(
                """UPDATE rfp_runs SET status = 'failed', error_message = ?, warnings_json = ?
                   WHERE rfp_run_id = ? AND status = 'running'""",
                (error_message, json.dumps(list(warnings), ensure_ascii=True), run_id),
            )

    def load_run(self, run_id: str) -> dict[str, object]:
        with self._connection() as connection:
            run = connection.execute("SELECT * FROM rfp_runs WHERE rfp_run_id = ?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(f"Unknown RFP run: {run_id}")
            rows = connection.execute(
                """SELECT supplier_name, submission_date, experience_rating, absolute_score,
                          ppi, final_rank, result_json
                   FROM supplier_results WHERE rfp_run_id = ? ORDER BY final_rank""",
                (run_id,),
            ).fetchall()
        return {
            "schema_version": "1",
            "rfp_run_id": run["rfp_run_id"],
            "created_at": run["created_at"],
            "status": run["status"],
            "criteria_snapshot": json.loads(run["criteria_snapshot_json"]),
            "config": json.loads(run["config_json"]),
            "warnings": json.loads(run["warnings_json"]),
            "error_message": run["error_message"],
            "suppliers": [
                {
                    "supplier_name": row["supplier_name"],
                    "submission_date": row["submission_date"],
                    "experience_rating": row["experience_rating"],
                    "absolute_score": row["absolute_score"],
                    "ppi": row["ppi"],
                    "final_rank": row["final_rank"],
                    **json.loads(row["result_json"]),
                }
                for row in rows
            ],
        }