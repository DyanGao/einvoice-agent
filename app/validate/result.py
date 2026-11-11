from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFORMATION = "information"


class Violation(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_id: str
    severity: Severity
    message: str
    location: str | None = None


class ValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    valid: bool
    # Name of the validation scenario that applied; None means the validator
    # did not recognise the document type at all.
    scenario: str | None
    violations: tuple[Violation, ...] = ()

    @property
    def errors(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.severity is Severity.ERROR)
