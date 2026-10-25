"""Environment-backed settings for the RFP evaluation backend."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = ROOT / "data" / "rfp_evaluation.sqlite3"


@dataclass(frozen=True)
class Settings:
    database_path: Path = DEFAULT_DATABASE_PATH
    llm_provider: str = "OpenAI"
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = ""
    max_pdf_bytes: int = 10 * 1024 * 1024
    max_document_characters: int = 120_000
    experience_min: str = "0"
    experience_max: str = "10"
    llm_max_attempts: int = 2
    prompt_version: str = "1"
    formula_version: str = "1"

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            database_path=Path(os.getenv("RFP_DATABASE_PATH", str(DEFAULT_DATABASE_PATH))),
            llm_provider=os.getenv("RFP_LLM_PROVIDER", "OpenAI"),
            llm_model=os.getenv("RFP_LLM_MODEL", "gpt-4o-mini"),
            llm_base_url=os.getenv("RFP_LLM_BASE_URL", ""),
            max_pdf_bytes=int(os.getenv("RFP_MAX_PDF_BYTES", str(10 * 1024 * 1024))),
            max_document_characters=int(os.getenv("RFP_MAX_DOCUMENT_CHARACTERS", "120000")),
            experience_min=os.getenv("RFP_EXPERIENCE_MIN", "0"),
            experience_max=os.getenv("RFP_EXPERIENCE_MAX", "10"),
            llm_max_attempts=int(os.getenv("RFP_LLM_MAX_ATTEMPTS", "2")),
            prompt_version=os.getenv("RFP_PROMPT_VERSION", "1"),
            formula_version=os.getenv("RFP_FORMULA_VERSION", "1"),
        )

    def safe_run_config(self) -> dict[str, str]:
        return {
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "llm_base_url": self.llm_base_url,
            "prompt_version": self.prompt_version,
            "normalization_policy": "architecture.md:v1",
            "formula_version": self.formula_version,
        }