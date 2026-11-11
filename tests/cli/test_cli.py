import json

import pytest
from sqlalchemy import func, select

from app import db
from app.cli import main
from tests.conftest import StubValidator
from tests.parse import samples


@pytest.fixture
def invoice_file(tmp_path):
    path = tmp_path / "rechnung.xml"
    path.write_bytes(samples.ubl("01.01a"))
    return path


def run(capsys, *args, **kwargs) -> tuple[int, list[dict]]:
    code = main(["process", *map(str, args)], **kwargs)
    return code, [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_valid_invoice_exits_zero_and_prints_the_invoice(
    capsys, invoice_file, accepting, session_factory
):
    code, (report,) = run(
        capsys, invoice_file, validator=accepting, session_factory=session_factory
    )
    assert (code, report["status"], report["invoice"]["number"]) == (0, "valid", "123456XX")


def test_amounts_are_printed_as_exact_strings(capsys, invoice_file, accepting, session_factory):
    _, (report,) = run(capsys, invoice_file, validator=accepting, session_factory=session_factory)
    assert report["invoice"]["gross_total"] == "336.9"


def test_invalid_invoice_exits_nonzero(capsys, invoice_file, rejecting, session_factory):
    code, (report,) = run(
        capsys, invoice_file, validator=rejecting, session_factory=session_factory
    )
    assert (code, report["validation"]["violations"][0]["rule_id"]) == (1, "BR-DE-15")


def test_unreachable_validator_exits_nonzero(capsys, invoice_file, session_factory):
    code, (report,) = run(
        capsys, invoice_file, validator=StubValidator(error="down"), session_factory=session_factory
    )
    assert (code, report["status"]) == (1, "validation_failed")


def test_processing_the_same_file_twice_stores_one_document(
    capsys, invoice_file, accepting, session_factory
):
    _, (first,) = run(capsys, invoice_file, validator=accepting, session_factory=session_factory)
    _, (second,) = run(capsys, invoice_file, validator=accepting, session_factory=session_factory)
    with session_factory() as session:
        count = session.scalar(select(func.count()).select_from(db.DocumentRow))
    assert (count, second["duplicate"], second["document_id"]) == (1, True, first["document_id"])


def test_no_store_leaves_the_database_untouched(capsys, invoice_file, accepting, session_factory):
    _, (report,) = run(
        capsys, "--no-store", invoice_file, validator=accepting, session_factory=session_factory
    )
    with session_factory() as session:
        count = session.scalar(select(func.count()).select_from(db.DocumentRow))
    assert (count, report["document_id"]) == (0, None)


def test_one_bad_file_among_several_makes_the_exit_code_nonzero(
    capsys, invoice_file, tmp_path, accepting, session_factory
):
    other = tmp_path / "notes.txt"
    other.write_text("keine Rechnung")
    code, reports = run(
        capsys, invoice_file, other, validator=accepting, session_factory=session_factory
    )
    assert (code, [r["status"] for r in reports]) == (1, ["valid", "unsupported"])
