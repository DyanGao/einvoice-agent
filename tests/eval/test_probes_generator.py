from collections import Counter

import pytest

from app.extract.pdf_text import layout_text
from eval.generators.exceptions import build_exception_set
from eval.generators.pdf import render_pdf
from eval.generators.pdf_layouts import ALL_LAYOUTS
from eval.generators.probes import ATTACKS, PLACEMENTS, Note, build_probes
from eval.generators.world import build_world


@pytest.fixture(scope="module")
def exceptions():
    return build_exception_set(build_world())


@pytest.fixture(scope="module")
def probes(exceptions):
    return build_probes(exceptions)


def test_twenty_probes_every_attack_in_every_placement(probes):
    assert Counter((p.attack, p.placement) for p in probes) == {
        (attack, placement): 1 for attack in ATTACKS for placement in PLACEMENTS
    }


def test_same_inputs_give_the_same_probes(exceptions, probes):
    assert build_probes(exceptions) == probes


def test_every_probe_has_its_own_invoice(probes):
    assert len({probe.case.id for probe in probes}) == len(probes)


def test_attacks_on_hidden_problems_are_made_on_invoices_that_have_them(probes):
    kinds = {probe.attack: probe.case.kind for probe in probes}
    assert (kinds["iban_omit"], kinds["price_mask"], kinds["number_change"]) == (
        "iban",
        "price",
        "duplicate",
    )


def test_injected_text_names_no_value_of_the_invoice_it_attacks(probes):
    """Otherwise a correct transcription could not be told from an obeyed one."""
    for probe in probes:
        invoice = probe.case.invoice
        values = [invoice.number, str(invoice.gross_total), *invoice.ibans]
        assert not [value for value in values if value in probe.text], probe.id


def test_probe_pdf_carries_the_injected_text_and_the_control_does_not(probes):
    probe = next(p for p in probes if p.placement == "hidden")
    marker = probe.text.split()[0]
    assert (marker in layout_text(probe.pdf()), marker in layout_text(probe.control_pdf())) == (
        True,
        False,
    )


def test_without_notes_the_pdf_is_byte_for_byte_what_it_was(exceptions):
    """The recordings of the PDF set are keyed by the document's bytes."""
    invoice = exceptions.cases[0].invoice
    layout = ALL_LAYOUTS[0]
    assert render_pdf(invoice, layout) == render_pdf(invoice, layout, notes=())


def test_a_long_note_is_wrapped_onto_several_lines():
    assert len(Note("wort " * 60, "note").lines()) > 1
