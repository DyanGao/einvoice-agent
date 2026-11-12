"""These tests run the real Tesseract program with the pinned language data
(`make ocr-data`); CI installs both."""

import pytest

from app.extract import BaselineExtractor
from app.extract.ocr import OcrError, OcrUnavailable, ocr_text
from app.models import DocumentFormat
from eval.generators.scans import generate

LIGHT_SCAN = next(generate(1))


def test_lightly_degraded_scan_is_read_including_umlauts():
    text = ocr_text(LIGHT_SCAN.content)
    assert LIGHT_SCAN.invoice.number in text
    assert "Äpfelmann" in text or "Mühlbach" in text or "straße" in text


def test_table_columns_stay_separated_by_gaps():
    text = ocr_text(LIGHT_SCAN.content)
    header = next(line for line in text.splitlines() if "Bezeichnung" in line)
    assert "  " in header


def test_missing_language_data_is_reported_with_the_fix(tmp_path):
    with pytest.raises(OcrUnavailable, match="make ocr-data"):
        ocr_text(LIGHT_SCAN.content, tessdata_dir=tmp_path)


@pytest.mark.parametrize("data", [b"", b"not an image", b"\xff\xd8\xff broken jpeg"])
def test_unreadable_image_is_an_ocr_error(data):
    with pytest.raises(OcrError):
        ocr_text(data)


def test_baseline_reads_a_scan_end_to_end():
    draft = BaselineExtractor().extract(LIGHT_SCAN.content)
    assert (draft.number, draft.source_format) == (
        LIGHT_SCAN.invoice.number,
        DocumentFormat.IMAGE,
    )
