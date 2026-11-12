import io

from PIL import Image

from app.detect import detect
from app.models import DocumentFormat
from eval.generators import pdf as pdf_generator
from eval.generators.scans import PROFILES, generate

COUNT = 6
SCANS = list(generate(COUNT))
SOURCES = list(pdf_generator.generate(COUNT))


def test_one_scan_per_source_pdf():
    assert len(SCANS) == COUNT


def test_scans_are_detected_as_images():
    assert {detect(scan.content) for scan in SCANS} == {DocumentFormat.IMAGE}


def test_scan_keeps_layout_and_split_of_its_source_pdf():
    assert [(s.layout, s.split) for s in SCANS] == [(p.layout, p.split) for p in SOURCES]


def test_gold_is_the_source_invoice_marked_as_image():
    for scan, source in zip(SCANS, SOURCES, strict=True):
        expected = source.invoice.model_copy(update={"source_format": DocumentFormat.IMAGE})
        assert scan.invoice == expected


def test_all_severity_profiles_occur():
    assert {scan.profile for scan in SCANS} == {profile.name for profile in PROFILES}


def test_generation_is_repeatable_on_the_same_machine():
    assert [scan.content for scan in generate(COUNT)] == [scan.content for scan in SCANS]


def test_scan_is_a_greyscale_page_at_roughly_a4_proportions():
    image = Image.open(io.BytesIO(SCANS[0].content))
    assert image.mode == "L"
    assert 1.38 < image.height / image.width < 1.45


def test_gold_record_names_profile_and_has_no_validator_verdict():
    record = SCANS[1].gold_record()
    assert (record["format"], record["profile"], record["expected_valid"]) == (
        "image",
        "medium",
        None,
    )
