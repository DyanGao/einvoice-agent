"""A limit on failed password sign-ins (v1.0 Stage 1, increment 4).

Password sign-in is only for the built-in admin account(s), so a few tries
are enough. After too many failures for one email, or from one address, in
the window, the next attempts are refused before the password is checked,
until the oldest failure leaves the window. A success clears the email's
count; the address keeps its count, so trying many emails from one place
still runs into the limit.

The counts are kept in memory, per API process. Compose runs one; an
installation with several processes behind a load balancer should limit
sign-ins at the proxy as well.
"""

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

WINDOW_SECONDS = 15 * 60
MAX_FAILURES_PER_EMAIL = 5
MAX_FAILURES_PER_ADDRESS = 20


@dataclass
class SignInLimiter:
    window_seconds: float = WINDOW_SECONDS
    max_per_email: int = MAX_FAILURES_PER_EMAIL
    max_per_address: int = MAX_FAILURES_PER_ADDRESS
    clock: Callable[[], float] = time.monotonic
    _failures: dict[str, deque[float]] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _recent(self, key: str, now: float) -> deque[float]:
        moments = self._failures.get(key, deque())
        while moments and moments[0] <= now - self.window_seconds:
            moments.popleft()
        if not moments:
            self._failures.pop(key, None)
        return moments

    def _wait(self, key: str, limit: int, now: float) -> float:
        moments = self._recent(key, now)
        if len(moments) < limit:
            return 0.0
        return moments[0] + self.window_seconds - now

    def retry_after(self, email: str, address: str) -> int | None:
        """Seconds until another attempt is allowed, or None if it is now."""
        with self._lock:
            now = self.clock()
            wait = max(
                self._wait(f"email:{email.strip().lower()}", self.max_per_email, now),
                self._wait(f"address:{address}", self.max_per_address, now),
            )
        return None if wait <= 0 else max(1, int(wait + 0.999))

    def failed(self, email: str, address: str) -> None:
        with self._lock:
            now = self.clock()
            # Forget whatever has left the window, so the table does not grow
            # with every email ever tried.
            for key in list(self._failures):
                self._recent(key, now)
            for key in (f"email:{email.strip().lower()}", f"address:{address}"):
                self._failures.setdefault(key, deque()).append(now)

    def succeeded(self, email: str) -> None:
        with self._lock:
            self._failures.pop(f"email:{email.strip().lower()}", None)
