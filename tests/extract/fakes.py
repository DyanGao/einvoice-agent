from types import SimpleNamespace

from app.extract import LlmRequest, LlmResponse

WIRE = {
    "number": "RE-2026-00042",
    "issue_date": "2026-03-05",
    "due_date": None,
    "type_code": "380",
    "currency": "eur",
    "buyer_reference": None,
    "po_ref": " PO-123456 ",
    "seller_name": "Muster Lieferant GmbH",
    "seller_vat_id": "DE 123 456 789",
    "buyer_name": "Beispiel Kunde AG",
    "buyer_vat_id": None,
    "payment_ibans": ["DE79 0000 0000 1234 5678 90", "DE79000000001234567890"],
    "lines": [
        {
            "line_id": "1",
            "description": "Beratung vor Ort",
            "quantity": "2.5",
            "unit_price": "100.00",
            "net": "250.00",
            "vat_rate": "19",
        }
    ],
    "vat_breakdown": [
        {"category": "S", "rate": "19", "taxable_amount": "250.00", "tax_amount": "47.50"}
    ],
    "line_net_total": "250.00",
    "net_total": "250.00",
    "vat_total": "47.50",
    "gross_total": "297.50",
    "amount_due": "297.50",
}


class FakeLlm:
    """An LlmClient that returns a fixed output and remembers what it was asked."""

    def __init__(self, output: dict | None = None) -> None:
        self.output = WIRE if output is None else output
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return LlmResponse(
            output=self.output, model=request.model, input_tokens=1200, output_tokens=300
        )


def sdk_message(text: str | None, stop_reason: str = "end_turn") -> SimpleNamespace:
    """Shaped like anthropic.types.Message, as far as AnthropicLlm reads it."""
    content = [SimpleNamespace(type="thinking", thinking="")]
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        model="claude-opus-5-5",
        usage=SimpleNamespace(input_tokens=1200, output_tokens=300),
    )


class StubSdk:
    """Stands in for anthropic.Anthropic: records the call, returns or raises."""

    def __init__(self, result) -> None:
        self.calls: list[dict] = []
        self._result = result
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result
