"""The README demo: five invoices in, four booked without review, one
flagged for its price, approved, and all five in the ERP."""

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from eval.demo import DemoResult, problems, run_demo
from eval.generators.demo import build_samples
from eval.generators.world import build_world
from mock_erp import seed
from mock_erp.main import create_app as create_erp
from tests.conftest import ACCEPT, StubValidator

SAMPLES = Path(__file__).parents[2] / "sample_invoices"


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture
def erp(world):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    seed.load(world, engine)
    return TestClient(create_erp(engine))


def test_committed_samples_are_what_the_generator_writes(world):
    """sample_invoices/ is generated; a hand edit would make the demo lie."""
    for sample in build_samples(world):
        assert (SAMPLES / sample.filename).read_bytes() == sample.render(), sample.filename


def test_demo_tells_the_readme_story(session_factory, erp):
    result = run_demo(session_factory, erp, StubValidator(ACCEPT), SAMPLES, say=lambda _: None)
    assert problems(result) == []
    assert (len(result.booked_without_review), result.flagged_findings, len(result.bookings)) == (
        4,
        ["price_deviation"],
        5,
    )


def test_flagged_invoice_is_booked_only_after_the_reviewer_approved_it(session_factory, erp):
    result = run_demo(session_factory, erp, StubValidator(ACCEPT), SAMPLES, say=lambda _: None)
    assert (
        len(result.booked_before_approval),
        result.flagged in result.booked_before_approval,
    ) == (
        4,
        False,
    )


def test_a_story_that_went_otherwise_is_reported():
    told = DemoResult(
        processed=["a", "b", "c", "d", "e"],
        booked_without_review=["a", "b", "c"],
        flagged="d",
        flagged_findings=["iban_changed"],
        booked_before_approval=["a", "b", "c"],
        bookings=[{}] * 4,
    )
    assert len(problems(told)) == 3
    assert problems(replace(told, flagged=None)) != []
