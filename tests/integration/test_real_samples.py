"""Whole structured path against the running validator sidecar (make up).

make test-integration
"""

from pathlib import Path

import httpx
import pytest

from app.pipeline import DocumentStatus, process_structured
from app.settings import Settings
from app.validate import KositValidator

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parents[1] / "fixtures"
XRECHNUNG_SUITE = sorted((FIXTURES / "xrechnung-testsuite").glob("*.xml"))

ZUGFERD_EXPECTED = {
    "EN16931_Einfach.pdf": DocumentStatus.VALID,
    "EN16931_Rabatte.pdf": DocumentStatus.VALID,
    "EN16931_Gutschrift.pdf": DocumentStatus.VALID,
    # XRechnung 2.1, EXTENDED and MINIMUM have no scenario in the XRechnung 3.0
    # validator configuration: neither confirmed nor refuted.
    "XRECHNUNG_Einfach.pdf": DocumentStatus.UNRECOGNIZED,
    "zugferd_2p0_EXTENDED_Warenrechnung.pdf": DocumentStatus.UNRECOGNIZED,
    "zugferd_2p0_MINIMUM.pdf": DocumentStatus.UNRECOGNIZED,
    "ZUGFeRD_1p0_BASIC_Einfach.pdf": DocumentStatus.UNSUPPORTED,
}


@pytest.fixture(scope="module")
def validator():
    settings = Settings.from_env()
    with httpx.Client(base_url=settings.validator_url, timeout=60) as client:
        yield KositValidator(client)


@pytest.mark.parametrize("path", XRECHNUNG_SUITE, ids=lambda p: p.name)
def test_official_xrechnung_file_is_valid(path, validator):
    assert process_structured(path.read_bytes(), validator).status is DocumentStatus.VALID


@pytest.mark.parametrize(("name", "expected"), ZUGFERD_EXPECTED.items())
def test_real_zugferd_file_gets_its_expected_status(name, expected, validator):
    data = (FIXTURES / "zugferd-corpus" / name).read_bytes()
    assert process_structured(data, validator).status is expected


def test_removing_the_buyer_reference_is_rejected_with_br_de_15(validator):
    xml = (FIXTURES / "xrechnung-testsuite" / "01.01a-INVOICE_ubl.xml").read_bytes()
    broken = xml.replace(b"<cbc:BuyerReference>04011000-12345-03</cbc:BuyerReference>", b"")
    outcome = process_structured(broken, validator)
    assert (outcome.status, [v.rule_id for v in outcome.validation.errors]) == (
        DocumentStatus.INVALID,
        ["BR-DE-15"],
    )
