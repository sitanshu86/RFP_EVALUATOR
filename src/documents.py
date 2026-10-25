"""Page-aware PDF extraction with bounded input size."""

from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader

from src.models import DocumentPage, ExtractedDocument


class DocumentExtractionError(ValueError):
    """Raised when a proposal cannot safely be converted to text."""


class DocumentTool:
    def __init__(self, max_pdf_bytes: int = 10 * 1024 * 1024, max_characters: int = 120_000):
        self.max_pdf_bytes = max_pdf_bytes
        self.max_characters = max_characters

    def extract(self, filename: str, pdf_bytes: bytes) -> ExtractedDocument:
        if len(pdf_bytes) > self.max_pdf_bytes:
            raise DocumentExtractionError(f"{filename}: PDF exceeds the {self.max_pdf_bytes}-byte upload limit.")
        if not pdf_bytes.startswith(b"%PDF-"):
            raise DocumentExtractionError(f"{filename}: uploaded file is not a PDF.")
        try:
            reader = PdfReader(BytesIO(pdf_bytes), strict=True)
            if reader.is_encrypted:
                raise DocumentExtractionError(f"{filename}: encrypted PDFs are not supported.")
            pages = tuple(
                DocumentPage(index, (page.extract_text() or "").strip())
                for index, page in enumerate(reader.pages, start=1)
            )
        except DocumentExtractionError:
            raise
        except Exception as error:
            raise DocumentExtractionError(
                f"{filename}: PDF could not be read. Check that the file is valid and not damaged."
            ) from error
        document = ExtractedDocument(filename, pages)
        if not document.text.strip():
            raise DocumentExtractionError(
                f"{filename}: no text could be extracted. Image-only PDFs require OCR, which is not enabled."
            )
        if len(document.text) > self.max_characters:
            raise DocumentExtractionError(f"{filename}: extracted text exceeds the {self.max_characters}-character limit.")
        return document