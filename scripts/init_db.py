"""Initialize the dedicated RFP application database and seed default criteria."""

from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "data" / "rfp_evaluation.sqlite3"

DEFAULT_CRITERIA = (
    (1, "Technical Capability", "Architecture, integrations, scalability, and technical fit.", "30", "10"),
    (2, "Implementation Plan", "Timeline, milestones, staffing, and risk plan.", "20", "10"),
    (3, "Commercial Value", "Pricing clarity, total cost, and assumptions.", "20", "10"),
    (4, "Security & Compliance", "Controls, certifications, privacy, and auditability.", "20", "10"),
    (5, "Support & Experience", "Support model, similar projects, and references.", "10", "10"),
)


def initialize_database(database_path: str | Path = DATABASE_PATH) -> None:
    """Create schema and seed default criteria without overwriting user edits."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS evaluation_criteria (
                    criterion_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    weight TEXT NOT NULL,
                    max_score TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
                );

                CREATE TABLE IF NOT EXISTS rfp_runs (
                    rfp_run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
                    criteria_snapshot_json TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    error_message TEXT
                );

                CREATE TABLE IF NOT EXISTS supplier_results (
                    rfp_run_id TEXT NOT NULL,
                    supplier_name TEXT NOT NULL,
                    submission_date TEXT NOT NULL,
                    experience_rating TEXT NOT NULL,
                    absolute_score TEXT NOT NULL,
                    ppi TEXT NOT NULL,
                    final_rank INTEGER NOT NULL,
                    result_json TEXT NOT NULL,
                    PRIMARY KEY (rfp_run_id, supplier_name),
                    FOREIGN KEY (rfp_run_id) REFERENCES rfp_runs (rfp_run_id)
                );
                """
            )
            connection.executemany(
                """INSERT OR IGNORE INTO evaluation_criteria
                   (criterion_id, name, description, weight, max_score, is_active)
                   VALUES (?, ?, ?, ?, ?, 1)""",
                DEFAULT_CRITERIA,
            )
    finally:
        connection.close()


if __name__ == "__main__":
    initialize_database()
    print(f"RFP database initialized at {DATABASE_PATH}")