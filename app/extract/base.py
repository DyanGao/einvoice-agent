from typing import Protocol

from app.extract.draft import InvoiceDraft


class Extractor(Protocol):
    """Reads an unstructured document (text PDF or image) into a draft."""

    def extract(self, data: bytes) -> InvoiceDraft: ...
