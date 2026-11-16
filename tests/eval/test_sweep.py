import pytest

from eval.sweep import operating_point, upper_bound


def at(threshold, auto, errors):
    return {
        "threshold": threshold,
        "auto": auto,
        "errors": errors,
        "error_rate": errors / auto if auto else 0.0,
    }


def test_threshold_is_loosened_while_it_only_lets_right_invoices_through():
    points = [at(0.99, 50, 0), at(0.97, 80, 0), at(0.9, 90, 0)]
    assert operating_point(points)["threshold"] == 0.9


def test_step_that_only_lets_a_wrong_invoice_through_is_not_taken():
    """1 wrong in 81 is within 1 % overall, but that step added only the wrong one."""
    points = [at(0.97, 80, 0), at(0.33, 81, 1)]
    assert operating_point(points)["threshold"] == 0.97


def test_step_with_few_enough_errors_among_many_invoices_is_taken():
    points = [at(0.97, 100, 0), at(0.9, 300, 1)]
    assert operating_point(points)["threshold"] == 0.9


def test_of_thresholds_that_let_the_same_invoices_through_the_strictest_is_kept():
    points = [at(0.99, 80, 0), at(0.5, 80, 0)]
    assert operating_point(points)["threshold"] == 0.99


def test_no_operating_point_when_even_the_strictest_threshold_has_too_many_errors():
    with pytest.raises(ValueError, match="strictest"):
        operating_point([at(1.0, 10, 1)])


@pytest.mark.parametrize(
    ("errors", "n", "expected"),
    [(0, 100, 0.0295), (0, 300, 0.0099), (1, 157, 0.0299), (5, 5, 1.0)],
)
def test_upper_bound_of_the_error_rate_is_the_one_sided_clopper_pearson_bound(errors, n, expected):
    assert upper_bound(errors, n) == pytest.approx(expected, abs=0.0002)


def test_no_upper_bound_without_invoices():
    assert upper_bound(0, 0) is None


# --- gate: a replayed run must not book a misread invoice ----------------------


def test_wrong_automatic_booking_on_the_held_out_split_fails_the_gate():
    from eval.sweep import wrong_bookings

    result = {
        "system": "llm-rules-run2",
        "test": {
            "wrong_but_auto": [{"set": "pdf", "id": "pdf-0142", "wrong": ["buyer_reference"]}]
        },
    }
    (failure,) = wrong_bookings(result)
    assert "pdf-0142" in failure
    assert "buyer_reference" in failure


def test_no_wrong_automatic_booking_passes_the_gate():
    from eval.sweep import wrong_bookings

    assert wrong_bookings({"system": "llm-rules", "test": {"wrong_but_auto": []}}) == []


def test_wrong_bookings_on_the_dev_split_are_not_the_gate():
    """Dev is where the threshold is fitted; the gate guards the reported split."""
    from eval.sweep import wrong_bookings

    result = {
        "system": "llm-rules",
        "dev": {"wrong_but_auto": [{"set": "scans", "id": "scan-0000", "wrong": ["iban"]}]},
        "test": {"wrong_but_auto": []},
    }
    assert wrong_bookings(result) == []
