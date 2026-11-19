"""Failed password sign-ins are limited per email and per address."""

from app.auth.limits import SignInLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def limiter(clock: Clock) -> SignInLimiter:
    return SignInLimiter(window_seconds=900, max_per_email=3, max_per_address=5, clock=clock)


def test_a_few_failures_are_allowed():
    limits = limiter(Clock())
    for _ in range(2):
        limits.failed("admin@example.com", "10.0.0.1")
    assert limits.retry_after("admin@example.com", "10.0.0.1") is None


def test_too_many_failures_for_one_email_block_it_from_anywhere():
    clock = Clock()
    limits = limiter(clock)
    for address in ("10.0.0.1", "10.0.0.2", "10.0.0.3"):
        limits.failed("Admin@Example.com", address)
    assert limits.retry_after("admin@example.com", "10.0.0.9") == 900


def test_the_block_ends_when_the_oldest_failure_leaves_the_window():
    clock = Clock()
    limits = limiter(clock)
    for _ in range(3):
        limits.failed("admin@example.com", "10.0.0.1")
        clock.now += 100
    assert limits.retry_after("admin@example.com", "10.0.0.1") == 600
    clock.now += 600
    assert limits.retry_after("admin@example.com", "10.0.0.1") is None


def test_trying_many_emails_from_one_address_runs_into_its_limit():
    limits = limiter(Clock())
    for number in range(5):
        limits.failed(f"user{number}@example.com", "10.0.0.1")
    assert limits.retry_after("someone@example.com", "10.0.0.1") is not None
    assert limits.retry_after("someone@example.com", "10.0.0.2") is None


def test_success_clears_the_email_but_not_the_address():
    limits = limiter(Clock())
    for _ in range(3):
        limits.failed("admin@example.com", "10.0.0.1")
    limits.succeeded("admin@example.com")
    assert limits.retry_after("admin@example.com", "10.0.0.2") is None
    limits.failed("x@example.com", "10.0.0.1")
    limits.failed("y@example.com", "10.0.0.1")
    assert limits.retry_after("z@example.com", "10.0.0.1") is not None


def test_old_failures_are_forgotten():
    clock = Clock()
    limits = limiter(clock)
    limits.failed("old@example.com", "10.0.0.1")
    clock.now += 901
    limits.failed("new@example.com", "10.0.0.2")
    assert set(limits._failures) == {"email:new@example.com", "address:10.0.0.2"}
