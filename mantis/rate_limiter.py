from time import time
from threading import Lock

class RateLimiter:
    """
    Rate limiter using the token bucket algorithm.

    Attributes:
        max_tokens (int): The maximum number of tokens in the bucket.
        refill_rate (float): The rate at which tokens are added (tokens per second).
        tokens (float): The current number of tokens in the bucket.
        last_refill (float): The last time the tokens were refilled.
        lock (Lock): A threading lock to manage concurrent access.
    """

    def __init__(self, max_tokens: int, refill_rate: float) -> None:
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate
        self.tokens = max_tokens
        self.last_refill = time()
        self.lock = Lock()

    def _refill(self) -> None:
        """
        Refills the token bucket based on the elapsed time since the last refill.
        """
        now = time()
        elapsed = now - self.last_refill
        added_tokens = elapsed * self.refill_rate
        self.tokens = min(self.max_tokens, self.tokens + added_tokens)
        self.last_refill = now

    def acquire(self, tokens_required: int) -> bool:
        """
        Attempts to acquire the specified number of tokens from the bucket.

        Args:
            tokens_required (int): The number of tokens to acquire.

        Returns:
            bool: True if the tokens were successfully acquired, False otherwise.
        """
        with self.lock:
            self._refill()
            if tokens_required <= self.tokens:
                self.tokens -= tokens_required
                return True
            return False

    def allow(self, tokens: int = 1) -> bool:
        """
        Alias for acquire(tokens_required=tokens) for convenience.

        Args:
            tokens (int): The number of tokens to acquire (default: 1).

        Returns:
            bool: True if the tokens were successfully acquired, False otherwise.
        """
        return self.acquire(tokens_required=tokens)

    def available(self) -> float:
        """
        Returns the current number of available tokens in the bucket.

        Returns:
            float: The current number of tokens available.
        """
        with self.lock:
            self._refill()
            return self.tokens
