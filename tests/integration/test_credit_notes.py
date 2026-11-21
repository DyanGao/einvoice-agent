"""Generated credit notes are valid XRechnung (v1.0 Stage 5): the KoSIT
validator accepts a 381 in both syntaxes, with the invoice it names (BT-25).

make test-integration
"""

import httpx
import pytest

from app.pipeline import DocumentStatus, process_structured
from app.settings import Settings
from app.validate import KositValidator
from eval.generators.canonical import XRECHNUNG, build_invoice, credit_note_for
from eval.generators.xrechnung import render_cii, render_ubl

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def validator():
    settings = Settings.from_env()
    with httpx.Client(base_url=settings.validator_url, timeout=60) as client:
        yield KositValidator(client)


@pytest.mark.parametrize("render", [render_ubl, render_cii], ids=["ubl", "cii"])
@pytest.mark.parametrize("index", range(5))
def test_a_generated_credit_note_is_valid_xrechnung(render, index, validator):
    credit = credit_note_for(build_invoice(31, index), f"GS-2026-{index:05d}")
    outcome = process_structured(render(credit, XRECHNUNG), validator)
    assert outcome.status is DocumentStatus.VALID, outcome.validation.errors
    assert outcome.invoice.preceding_ref == credit.preceding_ref


def test_every_xml_credit_note_of_the_eval_set_is_valid_xrechnung(validator):
    """Partial credits too, and those naming an invoice in another writing."""
    from eval.generators.credits import CII, UBL, build_credit_set
    from eval.generators.world import build_world

    cases = [c for c in build_credit_set(build_world()).cases if c.format in (UBL, CII)]
    assert len(cases) == 80
    invalid = {}
    for case in cases:
        outcome = process_structured(case.content, validator)
        if outcome.status is not DocumentStatus.VALID:
            invalid[case.id] = [error.rule_id for error in outcome.validation.errors]
    assert invalid == {}
