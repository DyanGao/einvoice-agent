import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.validate import Severity, ValidationResult, ValidatorError, Violation


class StubValidator:
    """Stands in for the sidecar: returns a fixed result or raises."""

    def __init__(self, result: ValidationResult | None = None, error: str | None = None):
        self.result, self.error, self.seen = result, error, []

    def validate(self, xml: bytes) -> ValidationResult:
        self.seen.append(xml)
        if self.error is not None:
            raise ValidatorError(self.error)
        return self.result


ACCEPT = ValidationResult(valid=True, scenario="EN16931 XRechnung (UBL Invoice)")
REJECT = ValidationResult(
    valid=False,
    scenario="EN16931 XRechnung (UBL Invoice)",
    violations=(Violation(rule_id="BR-DE-15", severity=Severity.ERROR, message="BT-10 fehlt"),),
)
NO_SCENARIO = ValidationResult(valid=False, scenario=None)


@pytest.fixture
def accepting():
    return StubValidator(ACCEPT)


@pytest.fixture
def rejecting():
    return StubValidator(REJECT)


@pytest.fixture
def session_factory():
    """SQLite in memory by default; set TEST_DATABASE_URL to run on Postgres
    (an empty database that these tests may create and drop tables in)."""
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        engine = create_engine(url)
    else:
        # The API tests reach the database from the test client's thread.
        engine = create_engine(
            "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
    Base.metadata.create_all(engine)
    yield sessionmaker(engine)
    Base.metadata.drop_all(engine)
    engine.dispose()
