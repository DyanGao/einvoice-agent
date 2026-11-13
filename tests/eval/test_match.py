from app.matching import Match, Payment
from eval.generators.world import PAID_ONCE, build_world
from eval.match import BASELINE, MATCHER, Case, accuracy, report, run


def paid_once():
    return next(entry for entry in build_world().invoices if entry.payment_kind == PAID_ONCE)


def right_match(entry) -> Match:
    return Match(
        po_number=entry.po_number,
        delivery_id=entry.delivery_id,
        payments=tuple(Payment(p.bank_transaction_id, p.amount) for p in entry.payments),
    )


def test_a_match_equal_to_the_ground_truth_is_correct_in_every_part():
    entry = paid_once()
    assert set(Case(entry, right_match(entry)).correct().values()) == {True}


def test_right_transfer_with_a_wrong_amount_is_not_a_correct_payment():
    entry = paid_once()
    wrong = Match(
        po_number=entry.po_number,
        delivery_id=entry.delivery_id,
        payments=(Payment(entry.payments[0].bank_transaction_id, entry.payments[0].amount - 1),),
    )
    assert Case(entry, wrong).correct() == {
        "order": True,
        "delivery": True,
        "payments": False,
        "all": False,
    }


def test_finding_nothing_is_a_miss_but_not_a_wrong_claim():
    case = Case(paid_once(), Match())
    assert (case.correct()["all"], case.wrong_claims()) == (False, [])


def test_naming_the_wrong_order_is_a_wrong_claim():
    entry = paid_once()
    case = Case(entry, Match(po_number="PO-000000"))
    assert case.wrong_claims() == ["order"]


def test_matcher_beats_the_exact_key_baseline_on_the_held_out_split():
    world = build_world()
    matcher = accuracy(run(world, MATCHER, "test"))["all invoices"]["all"]
    baseline = accuracy(run(world, BASELINE, "test"))["all invoices"]["all"]
    assert matcher >= 0.95 > baseline


def test_report_counts_wrong_claims_apart_from_open_cases():
    entry = paid_once()
    result = report([Case(entry, Match()), Case(entry, Match(po_number="PO-000000"))])
    assert (result["wrong_claims"], result["left_open"]) == (1, 1)
