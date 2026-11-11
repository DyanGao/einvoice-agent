from enum import StrEnum


class DocumentFormat(StrEnum):
    UBL = "ubl"
    CII = "cii"
    ZUGFERD_PDF = "zugferd_pdf"
    PDF_TEXT = "pdf_text"
    IMAGE = "image"
    UNKNOWN = "unknown"

    @property
    def is_structured(self) -> bool:
        """Structured formats are parsed and validated without any LLM."""
        return self in {DocumentFormat.UBL, DocumentFormat.CII, DocumentFormat.ZUGFERD_PDF}
